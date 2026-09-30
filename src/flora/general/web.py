# SPDX-License-Identifier: Apache-2.0
"""Search, extraction and explicitly configured HTTP services."""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

from flora.support.errors import InterruptedEffect, ValidationError

from .network import HttpClient


class PageText(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url, self.parts, self.links, self.title = url, [], [], []
        self.hidden = 0
        self.in_title = False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "section", "article"}:
            self.parts.append("\n")
        if tag == "a" and values.get("href"):
            self.anchor = [urljoin(self.url, values["href"]), []]

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if tag == "a" and self.anchor:
            url, words = self.anchor
            if urlsplit(url).scheme in {"http", "https"}:
                self.links.append({"url": url, "title": " ".join(words).strip()[:500]})
            self.anchor = None
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.in_title:
                self.title.append(data)
            if self.anchor:
                self.anchor[1].append(data)

    @property
    def text(self):
        return "\n".join(
            line
            for line in (re.sub(r"\s+", " ", x).strip() for x in "".join(self.parts).splitlines())
            if line
        )


def decode_text(raw, content_type):
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    encoding = match.group(1) if match else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        raise ValidationError("Server declared an unsupported character encoding") from None


class WebTools:
    def __init__(self, store, client=None, search=None, services=None):
        self.store, self.client = store, client or HttpClient()
        self.search = dict(search or {"provider": "duckduckgo"})
        self.services = dict(services or {})
        if set(self.search) - {"provider", "base_url", "api_key_env", "fallbacks"}:
            raise ValidationError("Unknown search configuration field")
        fallbacks = self.search.get("fallbacks", [])
        if not isinstance(fallbacks, list) or len(fallbacks) > 3:
            raise ValidationError("search.fallbacks must contain at most three configured providers")
        self.fallbacks = []
        for config in fallbacks:
            if not isinstance(config, dict) or "fallbacks" in config:
                raise ValidationError("Search fallbacks must be flat provider configurations")
            self.fallbacks.append(WebTools(store, self.client, config))
        if self.search.get("provider", "duckduckgo") not in {
            "duckduckgo",
            "brave",
            "tavily",
            "searxng",
            "disabled",
        }:
            raise ValidationError("Unsupported search provider")
        if self.search.get("provider") == "searxng" and not self.search.get("base_url"):
            raise ValidationError("SearXNG requires base_url")
        for name, service in self.services.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or not isinstance(
                service, dict
            ):
                raise ValidationError("Invalid HTTP service entry")
            if set(service) - {"base_url", "methods", "headers_env"} or "base_url" not in service:
                raise ValidationError("HTTP services accept base_url, methods and headers_env")
            p = urlsplit(service["base_url"])
            if (
                p.scheme not in {"http", "https"}
                or not p.hostname
                or p.username
                or p.password
                or p.query
                or p.fragment
            ):
                raise ValidationError("Invalid HTTP service base_url")
            if not isinstance(service.get("methods", ["GET"]), list) or any(
                m not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
                for m in service.get("methods", ["GET"])
            ):
                raise ValidationError("Invalid HTTP service methods")
            if not isinstance(service.get("headers_env", {}), dict):
                raise ValidationError("headers_env maps header names to environment variable names")

    def web_fetch(self, url: str) -> dict:
        """Fetch a public HTTP(S) page once. Save the observed content with a stable source ID."""
        result = self.client.request(url)
        if not 200 <= result["status"] < 300:
            raise ValidationError(f"Page returned HTTP {result['status']}")
        media = result["headers"].get("content-type", "text/plain")
        if result["headers"].get("content-encoding", "identity") not in {"", "identity"}:
            raise ValidationError(
                "Server ignored identity encoding; encoded response was not extracted"
            )
        raw = result["body"]
        if "application/pdf" in media or raw.startswith(b"%PDF-"):
            from .documents import extract_document

            text, details = extract_document(raw, ".pdf")
            title, links = result["url"], []
        elif "html" in media or raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            parser = PageText(result["url"])
            parser.feed(decode_text(raw, media))
            text, title, links = parser.text, "".join(parser.title).strip(), parser.links
            details = {"link_count": len(links)}
        elif media.startswith("text/") or "json" in media or "xml" in media:
            text, title, links, details = decode_text(raw, media), result["url"], [], {}
        else:
            raise ValidationError("Unsupported page media type; use an attachment/document parser")
        # Links are part of the saved observation, so links beyond the initial window remain readable.
        if links:
            text += "\n\nLINKS\n" + "\n".join(json.dumps(x, ensure_ascii=False) for x in links)
        source = self.store.record(
            origin=result["url"],
            title=(title or result["url"])[:2048],
            text=text,
            raw=raw,
            media_type=media,
        )
        return {
            **source,
            "http_status": result["status"],
            "redirects": result["redirects"],
            "links": links[:30],
            "details": details,
            "observed_only": True,
        }

    def web_search(self, query: str, limit: int = 5) -> dict:
        """Search for pages; snippets are search observations, not verification of destination content."""
        from .reliability import network_failure
        failures = []
        routes = [self, *self.fallbacks]
        for index, configured in enumerate(routes):
            try:
                result = configured._search_once(query, limit)
                if failures:
                    result["provider_failures"] = failures
                return result
            except InterruptedEffect:
                # In particular, a POST search may have unknown transport outcome.
                # No replacement request or provider fallback is sent in that case.
                raise
            except ValidationError as exc:
                code, alternate = network_failure(exc)
                failures.append({"provider": configured.search.get("provider", "duckduckgo"), "code": code})
                if not alternate or index == len(routes) - 1:
                    if not self.fallbacks:
                        raise
                    raise ValidationError("search_unavailable: " + json.dumps(failures) +
                                          "; no search result was observed; check configured sources or DNS/proxy") from None
        raise ValidationError("No configured search provider is available")

    def search_capabilities(self):
        """Configured search routes only; this does not assert current availability."""
        return {"providers": [s.search.get("provider", "duckduckgo") for s in [self, *self.fallbacks]],
                "availability": "unprobed", "fallbacks_explicit": True}

    def _search_once(self, query, limit):
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 1000
            or type(limit) is not int
            or not 1 <= limit <= 10
        ):
            raise ValidationError("Search requires a query and limit between 1 and 10")
        provider = self.search.get("provider", "duckduckgo")
        base = self.search.get("base_url")
        if provider == "disabled":
            raise ValidationError("Search is disabled in this configuration")
        key = None
        if provider in {"brave", "tavily"}:
            env = self.search.get(
                "api_key_env", "BRAVE_API_KEY" if provider == "brave" else "TAVILY_API_KEY"
            )
            key = os.environ.get(env)
            if not key:
                raise ValidationError(f"Search credential environment variable {env} is not set")
        if provider == "brave":
            data, response = self.client.json(
                (base or "https://api.search.brave.com/res/v1/web/search")
                + "?"
                + urlencode({"q": query, "count": limit}),
                headers={"X-Subscription-Token": key},
            )
            items = [
                {"url": x["url"], "title": x.get("title", ""), "snippet": x.get("description", "")}
                for x in data.get("web", {}).get("results", [])[:limit]
            ]
        elif provider == "tavily":
            data, response = self.client.json(
                base or "https://api.tavily.com/search",
                method="POST",
                headers={"Authorization": "Bearer " + key},
                data={"query": query, "max_results": limit},
            )
            items = [
                {"url": x["url"], "title": x.get("title", ""), "snippet": x.get("content", "")}
                for x in data.get("results", [])[:limit]
            ]
        elif provider == "searxng":
            data, response = self.client.json(
                base.rstrip("/") + "/search?" + urlencode({"q": query, "format": "json"})
            )
            items = [
                {"url": x["url"], "title": x.get("title", ""), "snippet": x.get("content", "")}
                for x in data.get("results", [])[:limit]
            ]
        else:
            response = self.client.request(
                (base or "https://html.duckduckgo.com/html/") + "?" + urlencode({"q": query})
            )
            if response["status"] != 200:
                raise ValidationError(
                    f"Search returned HTTP {response['status']}; configure a search API if blocked"
                )
            parser = PageText(response["url"])
            parser.feed(
                decode_text(response["body"], response["headers"].get("content-type", "text/html"))
            )
            items, seen = [], set()
            for link in parser.links:
                p = urlsplit(link["url"])
                destination = parse_qs(p.query).get("uddg", [link["url"]])[0]
                if urlsplit(destination).scheme not in {"http", "https"}:
                    continue
                if (
                    "duckduckgo.com" in (urlsplit(destination).hostname or "")
                    or destination in seen
                    or not link["title"]
                ):
                    continue
                seen.add(destination)
                items.append({"url": destination, "title": link["title"], "snippet": ""})
                if len(items) == limit:
                    break
            if not items and any(x in parser.text.lower() for x in ("captcha", "bot", "challenge")):
                raise ValidationError(
                    "Search provider issued a challenge; configure Brave, Tavily or SearXNG"
                )
        text = json.dumps(
            {"query": query, "provider": provider, "results": items}, ensure_ascii=False, indent=2
        )
        source = self.store.record(
            origin="search:" + provider + ":" + query,
            title=query,
            text=text,
            raw=response["body"],
            media_type="application/search-results+json",
        )
        visible = []
        for item in items:
            if not all(isinstance(item.get(key), str) for key in ("url", "title", "snippet")):
                raise ValidationError("Search provider returned malformed result fields")
            if len(item["url"]) > 8192:
                continue
            visible.append(
                {
                    "url": item["url"],
                    "title": item["title"][:500],
                    "snippet": item["snippet"][:2000],
                    "snippet_truncated": len(item["snippet"]) > 2000,
                }
            )
        return {**source, "results": visible, "destination_pages_fetched": False}

    def http_request(
        self, service: str, path: str, method: str = "GET", body: dict | None = None
    ) -> dict:
        """Call an explicitly configured service once. Methods and credential headers are host-controlled."""
        config = self.services.get(service)
        if config is None or method not in config.get("methods", ["GET"]):
            raise ValidationError("Unknown service or HTTP method is not granted")
        if (
            not isinstance(path, str)
            or path.startswith(("/", "\\"))
            or urlsplit(path).scheme
            or ".." in path.split("/")
        ):
            raise ValidationError("Service path must be relative without traversal")
        base = config["base_url"].rstrip("/") + "/"
        decoded = urlsplit(path).path
        for _ in range(5):
            previous, decoded = decoded, unquote(decoded)
            if decoded == previous:
                break
        if (
            decoded.startswith(("/", "\\"))
            or "\\" in decoded
            or ".." in decoded.split("/")
            or "%" in decoded
        ):
            raise ValidationError("Encoded service path must not escape the configured base")
        url = urljoin(base, path)
        if not url.startswith(base):
            raise ValidationError("Service path escaped its configured base")
        headers = {}
        for name, env in config.get("headers_env", {}).items():
            if not isinstance(env, str) or not os.environ.get(env):
                raise ValidationError("HTTP service credential environment variable is not set")
            headers[name] = os.environ[env]
        payload = None if body is None else json.dumps(body, allow_nan=False).encode()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        result = self.client.request(url, method=method, headers=headers, body=payload)
        text = decode_text(result["body"], result["headers"].get("content-type", "text/plain"))
        try:
            source = self.store.record(
                origin=url, title=f"{method} {service}/{path}", text=text, raw=result["body"]
            )
        except Exception as exc:
            if method not in {"GET", "HEAD"}:
                raise InterruptedEffect(
                    "HTTP action returned but its observation could not be persisted; do not retry"
                ) from exc
            raise
        return {
            **source,
            "http_status": result["status"],
            "method": method,
            "redirects": result["redirects"],
        }
