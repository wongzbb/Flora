# SPDX-License-Identifier: Apache-2.0
"""Interactive, ephemeral credentials and bounded model discovery."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from flora.integrations.providers import OpenAICompatibleProvider, _NoRedirect, _strict_json_loads
from flora.support.errors import ValidationError


@dataclass
class Connection:
    base_url: str
    model: str
    key: str = field(repr=False)


@dataclass
class Catalog:
    models: list[str]
    warning: str = ""


def normalize_url(value):
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if not parsed.path:
        value += "/v1"
    OpenAICompatibleProvider(
        base_url=value, model="validation", api_key_env=None, allow_insecure_http=True
    )
    return value


def discover_models(base_url, key, *, timeout=12, max_pages=100, opener=None):
    """Never redirect a credential. Retain all returned IDs; explicitly report any incomplete list."""
    base_url = normalize_url(base_url)
    validator = OpenAICompatibleProvider(
        base_url=base_url, model="validation", api_key_env=None, allow_insecure_http=True
    )
    validator.set_session_key(key)
    opener = opener or urllib.request.build_opener(_NoRedirect())
    found, cursor, seen = [], None, set()
    deadline = time.monotonic() + 30
    for _ in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Catalog(found, "Model discovery time limit reached; the list may be incomplete.")
        query = "?after=" + urllib.parse.quote(cursor, safe="") if cursor else ""
        request = urllib.request.Request(
            base_url + "/models" + query,
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
        )
        try:
            with opener.open(request, timeout=min(timeout, remaining)) as response:
                raw = response.read(4194305)
            if len(raw) > 4194304:
                return Catalog(found, "Model response exceeds 4 MiB; enter a model ID manually.")
            body = _strict_json_loads(raw.decode("utf-8"))
            rows = body.get("data") if isinstance(body, dict) else None
            if not isinstance(rows, list):
                raise ValueError
            for row in rows:
                ident = row.get("id") if isinstance(row, dict) else None
                if (
                    not isinstance(ident, str)
                    or not 1 <= len(ident) <= 512
                    or not ident.isprintable()
                ):
                    raise ValueError
                if ident not in seen:
                    seen.add(ident)
                    found.append(ident)
            if not body.get("has_more"):
                return Catalog(found, "" if found else "The endpoint returned no model IDs.")
            next_cursor = body.get("last_id")
            if not isinstance(next_cursor, str) or next_cursor == cursor or next_cursor not in seen:
                return Catalog(
                    found, "Model pagination is unsupported; the list may be incomplete."
                )
            cursor = next_cursor
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            return Catalog(
                found, f"Model list unavailable (HTTP {code}); enter the model ID manually."
            )
        except (urllib.error.URLError, OSError, TimeoutError):
            return Catalog(found, "Model list connection failed; enter the model ID manually.")
        except (ValueError, UnicodeError, TypeError, RecursionError):
            return Catalog(
                found, "The model list has an unsupported format; enter the model ID manually."
            )
    return Catalog(found, "Model pagination limit reached; the list may be incomplete.")


def configure(ui, profile, *, saved=False):
    """Always ask for all three values, including on resume; never read a stored API key."""
    options = dict(profile.get("provider", {}))
    previous = options.get("base_url", "")
    if saved:
        ui.note(f"Resume connection: {previous} · {options.get('model', '')}")
    elif previous:
        ui.note(f"Profile endpoint: {previous}")
    ui.note("Enter plain values, without quotes. API Key is hidden and is not saved.")
    while True:
        try:
            base_url = normalize_url(ui.ask("Base URL"))
            if saved and base_url != previous.rstrip("/"):
                ui.error(
                    "This session belongs to the displayed endpoint. Use it to resume, or start a new session."
                )
                continue
            break
        except (ValidationError, ValueError):
            ui.error(
                "Enter a valid http:// or https:// API base URL without credentials or query parameters."
            )
    if base_url.startswith("http://"):
        ui.note("This HTTP endpoint sends credentials without transport encryption.")
    while True:
        key = ui.ask("API Key", secret=True).strip()
        try:
            if key.startswith(('"', "'")) or key.endswith(('"', "'")):
                raise ValidationError("Enter the key without quotes")
            validator = OpenAICompatibleProvider(model="validation", api_key_env=None)
            validator.set_session_key(key)
            break
        except ValidationError:
            ui.error("Enter a nonempty API key without spaces or quotation marks.")
    with ui.waiting("Discovering available models"):
        catalog = discover_models(base_url, key)
    if catalog.warning:
        ui.note(catalog.warning)
    ui.models(catalog.models)
    while True:
        model = ui.ask("Model · number or full ID").strip()
        if model.isdecimal() and 1 <= int(model) <= len(catalog.models):
            model = catalog.models[int(model) - 1]
        if not model or len(model) > 512 or not model.isprintable() or model.startswith(('"', "'")):
            ui.error("Enter a listed number or a model ID, without quotes.")
            continue
        if saved and model != options.get("model"):
            ui.error(
                "Resume uses the original model shown above; select it to preserve this session."
            )
            continue
        break
    if not saved:
        options.update(
            model=model,
            base_url=base_url,
            api_key_env=None,
            allow_insecure_http=base_url.startswith("http://"),
        )
        options.setdefault("timeout", 120)
        options.setdefault("max_tokens_parameter", "max_tokens")
        profile["provider"] = options
    return Connection(base_url, model, key)
