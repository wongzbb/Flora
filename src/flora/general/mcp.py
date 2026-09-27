# SPDX-License-Identifier: Apache-2.0
"""MCP SDK adapter with a single lifecycle owner and no effect retries."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import datetime
import json
import os
import queue
import re
import threading
from urllib.parse import urlsplit

from flora.integrations.tools import ToolSpec
from flora.support.errors import InterruptedEffect, ValidationError


def _validate_schema(schema):
    from jsonschema.validators import validator_for

    # Do not let untrusted schema references initiate host network/file I/O.
    def visit(item):
        if isinstance(item, dict):
            if "$ref" in item and not str(item["$ref"]).startswith("#"):
                raise ValidationError("MCP input schema contains a nonlocal reference")
            if "$dynamicRef" in item and not str(item["$dynamicRef"]).startswith("#"):
                raise ValidationError("MCP input schema contains a nonlocal dynamic reference")
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(schema)
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


class MCPConnection:
    """One connection, one owning async task. A timeout permanently poisons the connection."""

    def __init__(self, name, config, store):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name):
            raise ValidationError("MCP server name must be a short identifier")
        allowed = {
            "transport",
            "command",
            "args",
            "env",
            "cwd",
            "url",
            "headers_env",
            "tools",
            "timeout",
            "allow_insecure_http",
        }
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValidationError("Unknown MCP configuration field")
        self.name, self.config, self.store = name, dict(config), store
        self.timeout = config.get("timeout", 30)
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not 1 <= self.timeout <= 120
        ):
            raise ValidationError("MCP timeout must be between 1 and 120 seconds")
        if (
            not isinstance(config.get("tools"), list)
            or not config["tools"]
            or any(not isinstance(x, str) for x in config["tools"])
        ):
            raise ValidationError("MCP requires an explicit nonempty tools allowlist")
        if len(config["tools"]) > 64 or len(set(config["tools"])) != len(config["tools"]):
            raise ValidationError("MCP tool allowlist must be unique and at most 64 entries")
        transport = config.get("transport", "stdio")
        if transport not in {"stdio", "http"}:
            raise ValidationError("MCP transport must be stdio or http")
        if transport == "stdio":
            if not isinstance(config.get("command"), str) or not config["command"]:
                raise ValidationError("MCP stdio requires an executable command")
            if not isinstance(config.get("args", []), list) or any(
                not isinstance(x, str) for x in config.get("args", [])
            ):
                raise ValidationError("MCP args must be an array of strings")
        else:
            p = urlsplit(config.get("url", ""))
            if (
                p.scheme not in {"http", "https"}
                or not p.hostname
                or p.username
                or p.password
                or p.fragment
                or p.query
            ):
                raise ValidationError("MCP requires a credential-free HTTP(S) endpoint")
            if p.scheme != "https" and config.get("allow_insecure_http") is not True:
                raise ValidationError("Plain HTTP MCP requires allow_insecure_http=true")
        for key in ("env", "headers_env"):
            values = config.get(key, {})
            if not isinstance(values, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()
            ):
                raise ValidationError("MCP credential maps must contain environment variable names")
        self.requests = queue.Queue(maxsize=8)
        self.ready = concurrent.futures.Future()
        self.poisoned, self.closed = False, False
        self.thread = threading.Thread(
            target=self._thread_main, name="flora-mcp-" + name, daemon=True
        )
        self.thread.start()
        try:
            self.catalog = self.ready.result(timeout=self.timeout + 5)
        except BaseException:
            self.close()
            raise

    def _environment(self, field):
        values = {}
        for key, env in self.config.get(field, {}).items():
            if env not in os.environ:
                raise ValidationError(f"MCP credential environment variable {env} is not set")
            values[key] = os.environ[env]
        return values

    def _thread_main(self):
        try:
            asyncio.run(self._main())
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(
                    exc
                    if isinstance(exc, ValidationError)
                    else ValidationError("MCP initialization failed: " + type(exc).__name__)
                )
        finally:
            self.poisoned = True
            while True:
                try:
                    item = self.requests.get_nowait()
                except queue.Empty:
                    break
                if item is not None and not item[2].done():
                    item[2].set_exception(
                        InterruptedEffect(
                            "MCP connection closed; outcome requires external verification"
                        )
                    )

    async def _main(self):
        try:
            import httpx
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError:
            raise ValidationError(
                "Install MCP dependencies with pip install 'flora-lang[mcp]'"
            ) from None
        async with contextlib.AsyncExitStack() as stack:
            if self.config.get("transport", "stdio") == "stdio":
                # The SDK supplies a minimal process environment; only explicitly mapped secrets are added.
                stderr = stack.enter_context(open(os.devnull, "w"))
                streams = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=self.config["command"],
                            args=self.config.get("args", []),
                            cwd=self.config.get("cwd"),
                            env=self._environment("env"),
                        ),
                        errlog=stderr,
                    )
                )
            else:
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=self._environment("headers_env"),
                        timeout=httpx.Timeout(self.timeout),
                        follow_redirects=False,
                    )
                )
                streams = await stack.enter_async_context(
                    streamable_http_client(self.config["url"], http_client=client)
                )
            session = await stack.enter_async_context(
                ClientSession(
                    streams[0],
                    streams[1],
                    read_timeout_seconds=datetime.timedelta(seconds=self.timeout),
                )
            )
            async with asyncio.timeout(self.timeout):
                await session.initialize()
                catalog, cursor, seen = [], None, set()
                for _ in range(20):
                    page = await session.list_tools(cursor=cursor)
                    catalog.extend(page.tools)
                    if len(catalog) > 1000:
                        raise ValidationError("MCP catalog exceeds 1000 tools")
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise ValidationError("MCP catalog repeated a cursor")
                    seen.add(cursor)
                else:
                    raise ValidationError("MCP catalog pagination limit exceeded")
                names = [x.name for x in catalog]
                if len(names) != len(set(names)) or set(self.config["tools"]) - set(names):
                    raise ValidationError("MCP catalog has duplicate names or lacks a granted tool")
                selected = [
                    x.model_dump(mode="json", exclude_none=True)
                    for x in catalog
                    if x.name in self.config["tools"]
                ]
                if len(json.dumps(selected).encode()) > 131072:
                    raise ValidationError("Granted MCP schemas exceed the 128 KiB catalog bound")
                for entry in selected:
                    _validate_schema(entry["inputSchema"])
                self.ready.set_result(selected)
            while True:
                item = await asyncio.to_thread(self.requests.get)
                if item is None:
                    break
                name, arguments, future = item
                try:
                    async with asyncio.timeout(self.timeout):
                        result = await session.call_tool(
                            name,
                            arguments,
                            read_timeout_seconds=datetime.timedelta(seconds=self.timeout),
                        )
                    value = result.model_dump(mode="json", exclude_none=True)
                    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if len(encoded.encode()) > self.store.max_item_bytes // 2:
                        raise InterruptedEffect("MCP returned an oversized result after dispatch")
                    source = self.store.record(
                        origin=f"mcp:{self.name}/{name}", title=f"{self.name}: {name}", text=encoded
                    )
                    answer = (
                        {
                            "source_id": source["source_id"],
                            "isError": bool(value.get("isError")),
                            "result": value,
                        }
                        if len(encoded) <= 16000
                        else {
                            **source,
                            "isError": bool(value.get("isError")),
                            "result_in_source": True,
                        }
                    )
                    if not future.done():
                        future.set_result(answer)
                except BaseException as exc:
                    if not future.done():
                        future.set_exception(
                            InterruptedEffect(
                                "MCP call outcome is unknown ("
                                + type(exc).__name__
                                + "); no retry sent"
                            )
                        )
                    break

    def specs(self):
        specs = []
        for index, entry in enumerate(sorted(self.catalog, key=lambda x: x["name"])):
            validator = _validate_schema(entry["inputSchema"])

            def invoke(arguments: dict, _name=entry["name"], _validator=validator):
                if self.poisoned or self.closed:
                    raise ValidationError(
                        "MCP connection is unavailable; reopen the session and resolve any unknown effect"
                    )
                errors = list(_validator.iter_errors(arguments))
                if errors:
                    raise ValidationError("MCP arguments failed the original server JSON Schema")
                future = concurrent.futures.Future()
                self.requests.put((_name, arguments, future), timeout=1)
                try:
                    return future.result(timeout=self.timeout + 3)
                except concurrent.futures.TimeoutError:
                    self.poisoned = True
                    raise InterruptedEffect(
                        "MCP deadline passed; outcome unknown, no retry sent"
                    ) from None

            specs.append(
                ToolSpec(
                    f"mcp_{self.name}_{index}",
                    invoke,
                    f"MCP server {self.name}; tool {entry['name']}. {entry.get('description', '')}\n"
                    "Supply arguments matching this full JSON Schema (validated before dispatch): "
                    + json.dumps(entry["inputSchema"], ensure_ascii=False),
                    {
                        "type": "object",
                        "properties": {"arguments": {"type": "object"}},
                        "required": ["arguments"],
                        "additionalProperties": False,
                    },
                )
            )
        return specs

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.requests.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(timeout=self.timeout + 3)
