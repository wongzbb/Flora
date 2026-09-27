# SPDX-License-Identifier: Apache-2.0
"""Authenticated loopback UI, backed by the persistent GeneralAgent API."""

from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit

from flora.support.errors import FloraError, ValidationError


class AgentServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, agent, port=0):
        self.agent, self.token = agent, secrets.token_urlsafe(32)
        self.worker, self.job_lock, self.error = None, threading.Lock(), None
        super().__init__(("127.0.0.1", port), Handler)
        self.origins = {
            f"http://127.0.0.1:{self.server_port}",
            f"http://localhost:{self.server_port}",
        }

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}/#token={self.token}"

    def start_task(self, task=None, resume=False):
        with self.job_lock:
            if self.worker and self.worker.is_alive():
                raise ValidationError("A task is already running")
            self.error = None

            def work():
                try:
                    if resume:
                        self.agent.resume()
                    else:
                        self.agent.run(task)
                except BaseException as exc:
                    self.error = {
                        "type": type(exc).__name__,
                        "message": str(exc)
                        if isinstance(exc, FloraError)
                        else "Task stopped; inspect local logs and session status",
                    }
                    self.agent.store.event({"kind": "task_error", **self.error})

            self.worker = threading.Thread(target=work, name="flora-task", daemon=False)
            self.worker.start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def _send(self, status, data, media="application/json; charset=utf-8", filename=None):
        raw = (
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            if not isinstance(data, bytes)
            else data
        )
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if filename:
            self.send_header(
                "Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename, safe="")
            )
        self.end_headers()
        self.wfile.write(raw)

    def _authorize(self):
        if "http://" + self.headers.get("Host", "") not in self.server.origins:
            self._send(403, {"error": "Host is not this loopback server"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins:
            self._send(403, {"error": "Origin is not allowed"})
            return False
        if not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.token
        ):
            self._send(401, {"error": "Open the authenticated link printed by flora serve"})
            return False
        return True

    def _body(self, limit):
        if self.headers.get("Transfer-Encoding"):
            raise ValidationError("Chunked request bodies are not accepted")
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1 or not values[0].isdigit():
            raise ValidationError("Exactly one Content-Length is required")
        size = int(values[0])
        if size > limit:
            raise ValidationError("Request body exceeds limit")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValidationError("Incomplete request body")
        return raw

    def do_GET(self):
        try:
            p = urlsplit(self.path)
            if p.path == "/" and not p.query:
                if "http://" + self.headers.get("Host", "") not in self.server.origins:
                    return self._send(403, {"error": "Invalid Host"})
                return self._send(
                    200,
                    files("flora.general").joinpath("static/app.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
            if not self._authorize():
                return
            q, agent = parse_qs(p.query), self.server.agent
            if p.path == "/api/status":
                status = agent.status()
                status["busy"] = (
                    bool(self.server.worker and self.server.worker.is_alive()) or status["busy"]
                )
                return self._send(200, {**status, "error": self.server.error})
            if p.path == "/api/events":
                return self._send(200, agent.store.events(int(q.get("after", ["0"])[0])))
            if p.path == "/api/sources":
                return self._send(
                    200, agent.store.list_sources(offset=int(q.get("offset", ["0"])[0]))
                )
            if p.path == "/api/source":
                return self._send(
                    200,
                    agent.store.read_source(
                        q.get("id", [""])[0], offset=int(q.get("offset", ["0"])[0])
                    ),
                )
            if p.path == "/api/artifacts":
                return self._send(200, agent.artifact_status())
            if p.path == "/api/download":
                path = q.get("path", [""])[0]
                if path not in {x["path"] for x in agent.store.artifacts()}:
                    raise ValidationError("Only registered artifacts can be downloaded")
                return self._send(
                    200,
                    agent.files.read_bytes(path),
                    mimetypes.guess_type(path)[0] or "application/octet-stream",
                    PurePosixPath(path).name,
                )
            self._send(404, {"error": "Unknown endpoint"})
        except (FloraError, ValueError, OSError) as exc:
            self._send(
                400,
                {
                    "error": str(exc)
                    if isinstance(exc, FloraError)
                    else "Invalid or unavailable resource"
                },
            )

    def do_POST(self):
        if not self._authorize():
            self.close_connection = True
            return
        try:
            p = urlsplit(self.path)
            q, agent = parse_qs(p.query), self.server.agent
            if p.path == "/api/upload":
                if self.server.worker and self.server.worker.is_alive():
                    raise ValidationError("Wait for the running task before adding attachments")
                name = q.get("name", [""])[0]
                if (
                    not name
                    or name != PurePosixPath(name).name
                    or len(name) > 150
                    or any(ord(c) < 32 for c in name)
                    or "\\" in name
                ):
                    raise ValidationError("Invalid attachment filename")
                raw = self._body(8 * 1024 * 1024)
                agent.files.make_directory("uploads")
                return self._send(
                    201,
                    agent.files.create_bytes("uploads/" + uuid.uuid4().hex[:12] + "-" + name, raw),
                )
            from flora.integrations.providers import _strict_json_loads

            body = _strict_json_loads(self._body(262144).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValidationError("JSON body must be an object")
            if p.path == "/api/task":
                if (
                    set(body) != {"task"}
                    or not isinstance(body["task"], str)
                    or not body["task"].strip()
                ):
                    raise ValidationError("Task requires nonempty text")
                self.server.start_task(task=body["task"])
                return self._send(202, {"accepted": True})
            if p.path == "/api/resume" and not body:
                self.server.start_task(resume=True)
                return self._send(202, {"accepted": True})
            if p.path == "/api/pause" and not body:
                return self._send(200, agent.request_pause())
            self._send(404, {"error": "Unknown endpoint or fields"})
        except (FloraError, ValueError, OSError) as exc:
            self.close_connection = True
            self._send(
                400, {"error": str(exc) if isinstance(exc, FloraError) else "Invalid request"}
            )


def serve(agent, port=8765):
    with AgentServer(agent, port) as server:
        print("Flora is ready. Open this local link:\n" + server.url, flush=True)
        try:
            server.serve_forever(poll_interval=0.3)
        except KeyboardInterrupt:
            agent.request_pause()
        finally:
            if server.worker and server.worker.is_alive():
                print("Waiting for the active operation to settle before closing…", flush=True)
                server.worker.join()
