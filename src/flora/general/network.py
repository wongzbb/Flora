# SPDX-License-Identifier: Apache-2.0
"""Bounded HTTP transport with checked redirect targets and pinned DNS connections."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from flora.support.errors import InterruptedEffect, ValidationError

_DNS_SLOTS = threading.BoundedSemaphore(8)


def _resolve_bounded(host, port, timeout):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise ValidationError("DNS resolver capacity is exhausted")
    done, result = threading.Event(), []

    def lookup():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as exc:
            result.append(exc)
        finally:
            _DNS_SLOTS.release()
            done.set()

    threading.Thread(target=lookup, name="flora-dns", daemon=True).start()
    if not done.wait(timeout):
        raise ValidationError("DNS lookup deadline exceeded")
    if isinstance(result[0], Exception):
        raise ValidationError("URL cannot be resolved") from result[0]
    return result[0]


@dataclass(frozen=True)
class NetworkPolicy:
    allow_private: bool = False
    allowed_hosts: tuple[str, ...] = ()
    timeout: float = 25
    max_bytes: int = 4194304
    max_redirects: int = 5
    proxy_url: str | None = None
    dns_over_https: str | None = None
    dns_bootstrap: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.allow_private) is not bool or not isinstance(
            self.allowed_hosts, (list, tuple)
        ):
            raise ValidationError("Invalid network policy")
        if (
            type(self.timeout) not in (int, float)
            or type(self.max_bytes) is not int
            or not 1 <= self.timeout <= 120
            or not 1024 <= self.max_bytes <= 16777216
        ):
            raise ValidationError("Invalid HTTP resource limits")
        if type(self.max_redirects) is not int or not 0 <= self.max_redirects <= 10:
            raise ValidationError("Invalid redirect limit")
        if any(
            not isinstance(h, str) or not h or len(h) > 255 or any(c in h for c in "/\\*, \r\n\t")
            for h in self.allowed_hosts
        ):
            raise ValidationError("allowed_hosts contains exact host names only")
        if self.proxy_url is not None:
            p = urlsplit(self.proxy_url)
            if (
                p.scheme != "http"
                or not p.hostname
                or p.username
                or p.password
                or p.path not in {"", "/"}
                or p.query
                or p.fragment
            ):
                raise ValidationError(
                    "proxy_url must be an explicit HTTP CONNECT proxy without embedded credentials"
                )
            try:
                _ = p.port
            except ValueError:
                raise ValidationError("Invalid proxy port") from None
        if self.dns_over_https is not None:
            p = urlsplit(self.dns_over_https)
            if (
                p.scheme != "https"
                or not p.hostname
                or p.username
                or p.password
                or p.query
                or p.fragment
            ):
                raise ValidationError("dns_over_https must be an explicit HTTPS JSON DNS endpoint")
            if (
                not isinstance(self.dns_bootstrap, (list, tuple))
                or not 1 <= len(self.dns_bootstrap) <= 4
            ):
                raise ValidationError("DoH needs 1–4 explicitly configured public bootstrap IPs")
            for value in self.dns_bootstrap:
                try:
                    ip = ipaddress.ip_address(value)
                except ValueError:
                    raise ValidationError(
                        "DNS bootstrap entries must be public IP literals"
                    ) from None
                if not ip.is_global or ip.is_multicast:
                    raise ValidationError("DNS bootstrap entries must be public IP literals")
        elif self.dns_bootstrap:
            raise ValidationError("dns_bootstrap requires dns_over_https")

    def resolve(self, url):
        if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url):
            raise ValidationError("Invalid URL")
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValidationError("Only HTTP(S) URLs without embedded credentials are allowed")
        host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if self.allowed_hosts and host not in [h.lower().rstrip(".") for h in self.allowed_hosts]:
            raise ValidationError("URL host is outside the configured allowlist")
        try:
            port = p.port or (443 if p.scheme == "https" else 80)
            try:
                addresses = [str(ipaddress.ip_address(host))]
            except ValueError:
                addresses = (
                    _resolve_doh(self, host)
                    if self.dns_over_https
                    else list(
                        dict.fromkeys(r[4][0] for r in _resolve_bounded(host, port, self.timeout))
                    )
                )
        except (ValueError, OSError) as exc:
            raise ValidationError("URL cannot be resolved") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%")[0])
            if not self.allow_private and (not ip.is_global or ip.is_multicast):
                effective_ip = getattr(ip, "ipv4_mapped", None) or ip
                if effective_ip in ipaddress.ip_network("198.18.0.0/15"):
                    raise ValidationError(
                        "network_policy_benchmark_address: DNS returned a reserved benchmarking "
                        "address (198.18.0.0/15), possibly synthetic/fake-IP DNS from a proxy. "
                        "No request was dispatched. Check DNS/proxy routing or use an already "
                        "configured authorized search provider; private-network protection stays enabled"
                    )
                raise ValidationError(
                    "Private, reserved, or local network destinations are disabled"
                )
        if not addresses:
            raise ValidationError("DNS returned no address records")
        return p, host, port, addresses[0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class _TunnelHTTP(http.client.HTTPConnection):
    """Connect only to an explicit proxy, then tunnel to the checked, pinned IP."""

    def __init__(self, host, port, address, timeout, proxy, *, tls=False):
        super().__init__(host, port, timeout=timeout)
        self.proxy, self.tls = proxy, tls
        self.set_tunnel(address, port)

    def connect(self):
        self.sock = socket.create_connection(self.proxy, self.timeout)
        self._tunnel()
        if self.tls:
            self.sock = ssl.create_default_context().wrap_socket(
                self.sock, server_hostname=self.host
            )


def _connection(policy, host, port, address, timeout, *, tls):
    if policy.proxy_url:
        p = urlsplit(policy.proxy_url)
        # The user grants this exact transport endpoint, not arbitrary private targets.
        rows = _resolve_bounded(p.hostname, p.port or 80, timeout)
        proxy = (rows[0][4][0], p.port or 80)
        return _TunnelHTTP(host, port, address, timeout, proxy, tls=tls)
    return (_PinnedHTTPS if tls else _PinnedHTTP)(host, port, address, timeout)


def _resolve_doh(policy, host):
    """Explicitly configured JSON DoH, TLS host checked against its public bootstrap.

    Wire shape follows the Google Public DNS JSON API. Resolver answers still pass
    the SAME destination policy. No redirects, proxy-side DNS, or implicit resolver.
    """
    endpoint = urlsplit(policy.dns_over_https)
    started = time.monotonic()
    for query_type in ("A", "AAAA"):
        left = policy.timeout - (time.monotonic() - started)
        if left <= 0:
            raise ValidationError("DNS over HTTPS deadline exceeded")
        conn = _connection(
            policy, endpoint.hostname, endpoint.port or 443, policy.dns_bootstrap[0], left, tls=True
        )
        try:
            path = endpoint.path or "/resolve"
            conn.request(
                "GET",
                path + "?" + urlencode({"name": host, "type": query_type}),
                headers={"Accept": "application/dns-json", "Accept-Encoding": "identity"},
            )
            response = conn.getresponse()
            if response.status != 200 or response.getheader("content-encoding", "identity") not in {
                "",
                "identity",
            }:
                raise ValidationError("DNS over HTTPS resolver is unavailable")
            chunks, size = [], 0
            while True:
                left = policy.timeout - (time.monotonic() - started)
                if left <= 0:
                    raise ValidationError("DNS over HTTPS deadline exceeded")
                if conn.sock:
                    conn.sock.settimeout(left)
                part = response.read1(min(8192, 65537 - size))
                if not part:
                    break
                chunks.append(part)
                size += len(part)
                if size > 65536:
                    raise ValidationError("DNS response exceeds byte limit")
            raw = b"".join(chunks)
            result = json.loads(raw)
            if (
                not isinstance(result, dict)
                or result.get("Status") != 0
                or result.get("TC") is True
            ):
                raise ValidationError("DNS resolver did not provide a complete successful answer")
            expected_type = 1 if query_type == "A" else 28
            addresses = list(
                dict.fromkeys(
                    str(ipaddress.ip_address(r["data"]))
                    for r in result.get("Answer", [])
                    if r.get("type") == expected_type
                )
            )
            if addresses:
                return addresses
        except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError):
            raise ValidationError(
                "DNS over HTTPS lookup failed; no destination request was sent"
            ) from None
        finally:
            conn.close()
    raise ValidationError("DNS resolver returned no address records")


class HttpClient:
    def __init__(self, policy=None):
        self.policy = policy or NetworkPolicy()

    def request(self, url, *, method="GET", headers=None, body=None):
        method = method.upper()
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValidationError("Unsupported HTTP method")
        headers = dict(headers or {})
        if any(
            not isinstance(k, str) or not isinstance(v, str) or "\n" in k + v or "\r" in k + v
            for k, v in headers.items()
        ):
            raise ValidationError("Invalid HTTP headers")
        if any(
            k.lower() in {"host", "content-length", "transfer-encoding", "connection"}
            for k in headers
        ):
            raise ValidationError("Transport-owned HTTP header cannot be overridden")
        headers.setdefault("User-Agent", "Flora/0.1 (+general-agent)")
        headers.setdefault("Accept-Encoding", "identity")
        if body is not None and len(body) > self.policy.max_bytes:
            raise ValidationError("HTTP request body exceeds limit")
        started = time.monotonic()
        redirects = []
        for step in range(self.policy.max_redirects + 1):
            remaining = self.policy.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("HTTP deadline exceeded")
            p, host, port, address = self.policy.resolve(url)
            remaining = self.policy.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ValidationError("HTTP deadline exceeded before dispatch")
            conn = _connection(self.policy, host, port, address, remaining, tls=p.scheme == "https")
            target = urlunsplit(
                (
                    "",
                    "",
                    quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                    quote(p.query, safe="%=&?/:@!$'()*+,;~-._+"),
                    "",
                )
            )
            dispatched = False
            try:
                dispatched = True
                conn.request(method, target, body=body, headers=headers)
                response = conn.getresponse()
                response_headers = {k.lower(): v for k, v in response.getheaders()}
                if response_headers.get("content-encoding", "identity") not in {"", "identity"}:
                    raise ValidationError(
                        "Server ignored identity encoding; compressed response is not accepted"
                    )
                if (
                    method in {"GET", "HEAD"}
                    and response.status in {301, 302, 303, 307, 308}
                    and "location" in response_headers
                ):
                    if step == self.policy.max_redirects:
                        raise ValidationError("HTTP redirect limit exceeded")
                    next_url = urljoin(url, response_headers["location"])
                    np = urlsplit(next_url)
                    if (np.scheme, np.hostname, np.port) != (p.scheme, p.hostname, p.port):
                        headers = {
                            k: v
                            for k, v in headers.items()
                            if k.lower() not in {"authorization", "cookie"}
                        }
                    redirects.append({"url": url, "status": response.status})
                    url = next_url
                    continue
                size = response_headers.get("content-length")
                if size and size.isdigit() and int(size) > self.policy.max_bytes:
                    raise ValidationError("HTTP response exceeds configured byte limit")
                chunks, total = [], 0
                while True:
                    left = self.policy.timeout - (time.monotonic() - started)
                    if left <= 0:
                        raise TimeoutError("HTTP deadline exceeded")
                    if conn.sock:
                        conn.sock.settimeout(left)
                    chunk = response.read1(min(65536, self.policy.max_bytes + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > self.policy.max_bytes:
                        raise ValidationError("HTTP response exceeds configured byte limit")
                return {
                    "url": url,
                    "status": response.status,
                    "headers": response_headers,
                    "body": b"".join(chunks),
                    "redirects": redirects,
                }
            except (OSError, http.client.HTTPException, TimeoutError) as exc:
                if dispatched and method not in {"GET", "HEAD"}:
                    raise InterruptedEffect(
                        "HTTP operation outcome is unknown; no retry was sent"
                    ) from exc
                raise ValidationError("HTTP read failed: " + type(exc).__name__) from exc
            except ValidationError as exc:
                if dispatched and method not in {"GET", "HEAD"}:
                    raise InterruptedEffect(
                        "HTTP operation may have completed but its result was unavailable"
                    ) from exc
                raise
            finally:
                conn.close()
        raise ValidationError("Unreachable HTTP redirect state")

    def json(self, url, *, method="GET", headers=None, data=None):
        merged = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data, allow_nan=False).encode()
            merged["Content-Type"] = "application/json"
        result = self.request(url, method=method, headers=merged, body=body)
        if not 200 <= result["status"] < 300:
            raise ValidationError(f"HTTP service returned status {result['status']}")
        try:
            return json.loads(result["body"]), result
        except (UnicodeError, ValueError):
            raise ValidationError("HTTP service did not return valid JSON") from None
