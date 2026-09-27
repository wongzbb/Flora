# SPDX-License-Identifier: Apache-2.0
"""Small nonsecret user configuration for the direct-use interface."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from flora.integrations.providers import OpenAICompatibleProvider, _strict_json_loads
from flora.support.errors import ValidationError

_ENV = {
    "model": "FLORA_MODEL",
    "base_url": "FLORA_BASE_URL",
    "api_key_env": "FLORA_API_KEY_ENV",
}


def settings_path() -> Path:
    """Honor a dedicated override, then XDG_CONFIG_HOME, then ~/.config."""
    override = os.environ.get("FLORA_CONFIG_HOME")
    if override:
        return Path(override).expanduser() / "settings.json"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (
        (Path(base).expanduser() if base else Path.home() / ".config") / "flora" / "settings.json"
    )


def _validate(value: dict, *, allow_insecure_http: bool = False) -> dict:
    if not isinstance(value, dict) or set(value) - set(_ENV):
        raise ValidationError(
            "Settings accept only model, base_url, and api_key_env; never API keys"
        )
    if any(
        not isinstance(item, str) or not item.strip()
        for name, item in value.items()
        if not (name == "api_key_env" and item is None)
    ):
        raise ValidationError("Saved settings must contain nonempty strings")
    # Constructor validation does not contact a provider or read the key value.
    OpenAICompatibleProvider(**{"model": "configuration-check", **value},
                             allow_insecure_http=allow_insecure_http)
    return value.copy()


def load_settings() -> dict:
    path = settings_path()
    try:
        with path.open("rb") as handle:
            raw = handle.read(65_537)
    except FileNotFoundError:
        return {}
    if len(raw) > 65_536:
        raise ValidationError("Saved settings exceed 64 KiB")
    try:
        value = _strict_json_loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ValidationError("Saved settings must be strict UTF-8 JSON") from None
    return _validate(value)


def save_settings(
    *,
    model: str,
    base_url: str | None = None,
    api_key_env: str | None = None,
    no_api_key: bool = False,
) -> Path:
    """Replace only the supplied nonsecret preferences using an atomic file."""
    value = load_settings()
    value["model"] = model
    for name, item in (("base_url", base_url), ("api_key_env", api_key_env)):
        if item is not None:
            value[name] = item
    if no_api_key:
        value["api_key_env"] = None
    value = _validate(value)
    path = settings_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def resolve_settings(
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    no_api_key: bool = False,
    allow_insecure_http: bool = False,
) -> dict:
    """Explicit flags > FLORA_* environment > saved settings > defaults."""
    value = load_settings()
    for key, environment in _ENV.items():
        item = os.environ.get(environment)
        if item:
            value[key] = item
    for key, item in (("model", model), ("base_url", base_url), ("api_key_env", api_key_env)):
        if item is not None:
            value[key] = item
    if no_api_key:
        value["api_key_env"] = None
    if not value.get("model"):
        raise ValidationError(
            "No model configured. Run `flora setup --model MODEL`, set FLORA_MODEL, or pass --model MODEL."
        )
    value.setdefault("base_url", "https://api.openai.com/v1")
    value.setdefault("api_key_env", "OPENAI_API_KEY")
    return _validate(value, allow_insecure_http=allow_insecure_http)
