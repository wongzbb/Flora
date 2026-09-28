# SPDX-License-Identifier: Apache-2.0
"""Bounded, synchronous Chat Completions transport with no hidden retries.

The transport intentionally requires no vendor SDK. Authentication is read from
an environment variable at request time; keys and HTTP error bodies are never
included in exceptions. A provider response is data, not an executed tool call.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from flora.support.errors import CompilerError, ValidationError
from flora.support.values import canonical_json, clone


@dataclass(frozen=True)
class ModelResponse:
    """A text response and reported usage (``None`` means unavailable)."""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValidationError("ModelResponse.text must be a string")
        try:
            self.text.encode("utf-8")
        except UnicodeError:
            raise ValidationError("ModelResponse.text must be valid Unicode") from None
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValidationError(f"ModelResponse.{name} must be a nonnegative integer or None")
        if self.request_id is not None and not isinstance(self.request_id, str):
            raise ValidationError("ModelResponse.request_id must be a string or None")
        if not isinstance(self.raw_metadata, dict):
            raise ValidationError("ModelResponse.raw_metadata must be a dictionary")
        object.__setattr__(self, "raw_metadata", clone(self.raw_metadata))


class Provider(Protocol):
    def complete(self, messages: list[dict], *, max_tokens: int, **kwargs: Any) -> ModelResponse:
        """Make one model request. No implicit network retries."""
        ...


class ProviderError(CompilerError):
    """Sanitized provider failure; no raw remote body or secret is attached."""

    def __init__(
        self, message: str, *, input_tokens: int | None = None, output_tokens: int | None = None
    ) -> None:
        super().__init__(message)
        for value in (input_tokens, output_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ValidationError("provider error usage must be a nonnegative integer or None")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib redirects may forward Authorization. Refuse all redirects.
        return None


class TransportError(ProviderError):
    """Safe, machine-readable transport diagnosis for explicitly budgeted recovery."""

    def __init__(
        self, message, *, category, retryable=False, status=None, unsupported=None, retry_after=0
    ):
        super().__init__(message)
        self.category, self.retryable, self.status = category, retryable, status
        self.unsupported, self.retry_after = unsupported, retry_after


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON constant")


def _strict_json_loads(text: str) -> Any:
    return json.loads(
        text, object_pairs_hook=_object_no_duplicates, parse_constant=_reject_constant
    )


class OpenAICompatibleProvider:
    """POST to ``{base_url}/chat/completions`` exactly once per call.

    ``base_url`` normally includes ``/v1``. HTTPS is required except for
    loopback servers; remote HTTP requires ``allow_insecure_http=True``.
    The default output limit field is ``max_completion_tokens``; older
    compatible servers can select ``max_tokens_parameter='max_tokens'``.
    ``request_options`` holds explicitly selected model options. No model-
    specific temperature, JSON mode, or reasoning setting is imposed.
    """

    _PROTECTED = frozenset(
        {
            "model",
            "messages",
            "stream",
            "n",
            "max_tokens",
            "max_completion_tokens",
            "tools",
            "tool_choice",
            "functions",
            "function_call",
        }
    )

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str,
        api_key_env: str | None = "OPENAI_API_KEY",
        timeout: float = 60.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        max_request_bytes: int = 2 * 1024 * 1024,
        max_tokens_parameter: str = "max_completion_tokens",
        allow_insecure_http: bool = False,
        request_options: dict[str, Any] | None = None,
        stream: bool = False,
        total_timeout: float = 600.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url or any(c.isspace() for c in base_url):
            raise ValidationError("base_url must be a URL without whitespace")
        try:
            parsed = urllib.parse.urlsplit(base_url)
            _ = parsed.port
        except ValueError:
            raise ValidationError("base_url is malformed") from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValidationError("base_url requires an http or https hostname")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValidationError("base_url must not contain credentials, query, or fragment")
        if (
            parsed.scheme == "http"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            and not allow_insecure_http
        ):
            raise ValidationError("remote HTTP requires allow_insecure_http=True")
        if not isinstance(model, str) or not model.strip() or len(model) > 512:
            raise ValidationError("model must be a nonempty string of at most 512 characters")
        if api_key_env is not None and (
            not isinstance(api_key_env, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env)
        ):
            raise ValidationError("api_key_env must be an environment variable name or None")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValidationError("timeout must be a positive finite number")
        for name, size in (
            ("max_response_bytes", max_response_bytes),
            ("max_request_bytes", max_request_bytes),
        ):
            if type(size) is not int or size < 1:
                raise ValidationError(f"{name} must be a positive integer")
        if max_tokens_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValidationError(
                "max_tokens_parameter must be max_tokens or max_completion_tokens"
            )
        if type(allow_insecure_http) is not bool:
            raise ValidationError("allow_insecure_http must be a boolean")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = float(timeout)
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self.max_tokens_parameter = max_tokens_parameter
        self.request_options = self._options({} if request_options is None else request_options)
        if (
            type(stream) is not bool
            or isinstance(total_timeout, bool)
            or not isinstance(total_timeout, (int, float))
            or not math.isfinite(total_timeout)
            or total_timeout <= 0
        ):
            raise ValidationError("stream must be boolean and total_timeout positive and finite")
        self.stream, self.total_timeout = stream, float(total_timeout)
        self.prefer_json = False
        self.on_event = None
        self._disabled_features = set()
        self._session_key: str | None = None
        self._opener = urllib.request.build_opener(_NoRedirect())

    def set_session_key(self, key: str | None) -> None:
        """Use a process-local credential without storing it in configuration or the environment."""
        if key is not None and (
            not isinstance(key, str)
            or not key
            or len(key) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
        ):
            raise ValidationError("API key must be nonempty printable ASCII without spaces")
        self._session_key = key

    def has_credentials(self) -> bool:
        return (
            self._session_key is not None
            or self.api_key_env is None
            or bool(os.environ.get(self.api_key_env))
        )

    @classmethod
    def _options(cls, options: dict) -> dict:
        if not isinstance(options, dict) or cls._PROTECTED.intersection(options):
            raise ValidationError(
                "request options cannot override transport fields or introduce model tools"
            )
        return clone(options)

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(model={self.model!r}, authentication='environment')"

    def _emit(self, event):
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass  # Presentation cannot change execution or accounting.

    def adapt(self, error):
        """Downgrade only automatically added options explicitly rejected by HTTP 400/422."""
        feature = getattr(error, "unsupported", None)
        if feature is None or feature in self._disabled_features:
            return False
        if feature == "response_format" and "response_format" in self.request_options:
            return False  # Never silently override an explicit user's profile.
        if feature == "stream_options" and "stream_options" in self.request_options:
            return False
        self._disabled_features.add(feature)
        return True

    def complete(self, messages: list[dict], *, max_tokens: int, **kwargs: Any) -> ModelResponse:
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValidationError("max_tokens must be a positive integer")
        if not isinstance(messages, list) or not messages:
            raise ValidationError("messages must be a nonempty list")
        for message in messages:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise ValidationError("messages require exactly role and content")
            if (
                not isinstance(message["role"], str)
                or message["role"] not in {"system", "developer", "user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise ValidationError("only plain-text chat messages are supported")
        payload = dict(self.request_options)
        payload.update(self._options(kwargs))
        streaming = self.stream and "stream" not in self._disabled_features
        if self.prefer_json and "response_format" not in self._disabled_features:
            payload.setdefault("response_format", {"type": "json_object"})
        if streaming and "stream_options" not in self._disabled_features:
            payload.setdefault("stream_options", {"include_usage": True})
        if not streaming:
            payload.pop("stream_options", None)
        payload.update(model=self.model, messages=clone(messages), stream=streaming, n=1)
        payload[self.max_tokens_parameter] = max_tokens
        body = canonical_json(payload).encode("utf-8")
        if len(body) > self.max_request_bytes:
            raise ProviderError("model request exceeds configured byte limit")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json" if streaming else "application/json",
            "User-Agent": "Flora/0.1.0",
        }
        if self._session_key is not None or self.api_key_env is not None:
            key = self._session_key or os.environ.get(self.api_key_env)
            if not key:
                raise ProviderError("API key environment variable is unset or empty")
            if any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ProviderError("API key contains invalid header characters")
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=body, headers=headers, method="POST"
        )
        started = time.monotonic()
        self._emit(
            {
                "kind": "model_request",
                "messages": messages,
                "model": self.model,
                "stream": streaming,
                "json_mode": "response_format" in payload,
            }
        )
        data = None
        try:
            with self._opener.open(
                request, timeout=min(self.timeout, self.total_timeout)
            ) as response:
                request_id = response.headers.get("x-request-id")
                if request_id is not None and (
                    len(request_id) > 256 or not request_id.isprintable()
                ):
                    request_id = None
                if "text/event-stream" in response.headers.get("Content-Type", "").lower():
                    from .streaming import read_completion

                    try:
                        data = read_completion(
                            response,
                            limit=self.max_response_bytes,
                            deadline=started + self.total_timeout,
                            loads=_strict_json_loads,
                            emit=self._emit,
                            idle_timeout=self.timeout,
                        )
                    except EOFError:
                        raise TransportError(
                            "model stream ended before completion; partial program discarded; "
                            "usage may be unknown",
                            category="stream_interrupted",
                            retryable=True,
                        ) from None
                    except (ValueError, TypeError, RecursionError):
                        raise TransportError(
                            "model stream is malformed or exceeds its byte limit; "
                            "partial program discarded",
                            category="invalid_stream",
                        ) from None
                else:
                    if hasattr(response, "read1"):
                        from .streaming import chunks

                        try:
                            raw = b"".join(
                                chunks(
                                    response,
                                    self.max_response_bytes,
                                    started + self.total_timeout,
                                    self.timeout,
                                )
                            )
                        except ValueError:
                            raise ProviderError(
                                "model response exceeds configured byte limit"
                            ) from None
                    else:
                        raw = response.read(self.max_response_bytes + 1)
                    if len(raw) > self.max_response_bytes:
                        raise ProviderError("model response exceeds configured byte limit")
                    if time.monotonic() - started > self.total_timeout:
                        raise TimeoutError
        except urllib.error.HTTPError as exc:
            code = exc.code
            # Inspect only a bounded error object to recognize rejected field names.
            # Never retain or print a remote body, URL, headers, or exception string.
            unsupported = None
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            retry_after = (
                min(float(retry_after), 30) if re.fullmatch(r"\d{1,6}", retry_after) else 0
            )
            if code in (400, 422):
                try:
                    error = _strict_json_loads(exc.read(8193).decode("utf-8"))
                    error = error.get("error", {}) if isinstance(error, dict) else {}
                    if isinstance(error, dict):
                        param, message = error.get("param", ""), error.get("message", "")
                        for field in ("stream_options", "response_format", "stream"):
                            if field in payload and (
                                param == field
                                or (
                                    isinstance(message, str)
                                    and re.search(r"\b" + field + r"\b", message)
                                    and re.search(
                                        r"unsupported|not support|unknown|unrecognized|not allowed",
                                        message,
                                        re.I,
                                    )
                                )
                            ):
                                unsupported = field
                                break
                except (ValueError, OSError, TypeError, RecursionError):
                    pass
            exc.close()
            advice = {
                401: "check the API key",
                402: "check the account balance",
                403: "check model access",
                404: "check Base URL and model ID",
                429: "provider rate limit",
                400: "request rejected; inspect the model profile",
                422: "request parameters rejected",
            }
            raise TransportError(
                f"model HTTP {code}: {advice.get(code, 'upstream service failure')}",
                category="http",
                status=code,
                retryable=code in {408, 429, 500, 502, 503, 504},
                unsupported=unsupported,
                retry_after=retry_after,
            ) from None
        except ProviderError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            category = (
                "timeout"
                if isinstance(reason, TimeoutError)
                else "tls"
                if isinstance(reason, ssl.SSLError)
                else "dns"
                if isinstance(reason, socket.gaierror)
                else "connection"
            )
            detail = {
                "timeout": "no response within the connection/read timeout or total request limit",
                "tls": "TLS verification/handshake failed",
                "dns": "hostname resolution failed",
                "connection": "connection failed or was interrupted",
            }[category]
            raise TransportError(
                f"model {category}: {detail} (after {time.monotonic() - started:.1f}s); "
                "completion and usage may be unknown",
                category=category,
                retryable=category in {"timeout", "dns", "connection"},
            ) from None
        try:
            if data is None:
                data = _strict_json_loads(raw.decode("utf-8"))
                # A relay may ignore stream=true and return ordinary JSON.
                message = (
                    data.get("choices", [{}])[0].get("message", {})
                    if isinstance(data, dict)
                    else {}
                )
                for key, channel in (("reasoning_content", "reasoning"), ("content", "program")):
                    if isinstance(message.get(key), str) and message[key]:
                        self._emit(
                            {"kind": "model_delta", "channel": channel, "text": message[key]}
                        )
            result = self._parse_response(data, request_id)
            self._emit(
                {
                    "kind": "model_response",
                    "finish_reason": result.raw_metadata.get("finish_reason"),
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            )
            return result
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            RecursionError,
            ValidationError,
        ):
            # A refusal or unusable message can still have billable, valid usage.
            # Preserve only validated counters, never the rejected payload/body.
            usage = data.get("usage") if isinstance(data, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
            counts = [count if type(count) is int and count >= 0 else None for count in counts]
            raise ProviderError(
                "model response is not a valid text Chat Completion",
                input_tokens=counts[0],
                output_tokens=counts[1],
            ) from None

    @staticmethod
    def _parse_response(data: Any, request_id: str | None) -> ModelResponse:
        if not isinstance(data, dict):
            raise ValueError("response object required")
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("one response choice required")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("text message required")
        content = message.get("content")
        # Some reasoning providers exhaust the output budget before producing
        # final text. Preserve this as truncation, never execute reasoning as IR.
        if content is None and choice.get("finish_reason") == "length":
            content = ""
        if not isinstance(content, str):
            raise ValueError("text message required")
        if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
            raise ValueError("tool calls or refusals are not compiler programs")
        usage = data.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("usage must be an object")
        usage = usage or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        for count in (input_tokens, output_tokens):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("invalid usage counter")
        if choice.get("finish_reason") is not None and not isinstance(choice["finish_reason"], str):
            raise ValueError("invalid finish reason")
        metadata = {
            "finish_reason": choice.get("finish_reason"),
            "text_bytes": len(content.encode("utf-8")),
        }
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str):
            metadata["reasoning_bytes"] = len(reasoning.encode("utf-8"))
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            count = details.get("reasoning_tokens")
            if type(count) is int and count >= 0:
                metadata["reasoning_tokens"] = count
        # Deliberately keep no provider payload/messages/headers in metadata.
        return ModelResponse(content, input_tokens, output_tokens, request_id, metadata)
