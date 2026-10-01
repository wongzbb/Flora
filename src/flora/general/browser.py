# SPDX-License-Identifier: Apache-2.0
"""Optional Playwright browser. Handles are session-local and never replayed on restart."""

from __future__ import annotations

import concurrent.futures
import json
import queue
import threading
import uuid

from flora.support.errors import InterruptedEffect, ValidationError

from .network import NetworkPolicy


class BrowserTools:
    def __init__(self, config, store, files, task_key=None):
        if not isinstance(config, dict) or set(config) - {
            "allowed_hosts",
            "allow_private",
            "allow_actions",
            "timeout",
            "headless",
        }:
            raise ValidationError("Unknown browser configuration field")
        if not config.get("allowed_hosts"):
            raise ValidationError(
                "Browser requires an explicit allowed_hosts list including required asset hosts"
            )
        for name in ("allow_private", "allow_actions", "headless"):
            if name in config and type(config[name]) is not bool:
                raise ValidationError(name + " must be boolean")
        self.policy = NetworkPolicy(
            allowed_hosts=config["allowed_hosts"],
            allow_private=config.get("allow_private", False),
            timeout=config.get("timeout", 25),
        )
        self.config, self.store, self.files = config, store, files
        self.task_key = task_key or (lambda: "")
        self.ready, self.requests = concurrent.futures.Future(), queue.Queue(maxsize=8)
        self.closed, self.poisoned = False, False
        self.thread = threading.Thread(target=self._worker, daemon=True, name="flora-browser")
        self.thread.start()
        try:
            self.ready.result(timeout=self.policy.timeout + 5)
        except BaseException:
            self.close()
            raise

    def _worker(self):
        try:
            from playwright.sync_api import sync_playwright

            rules = []
            for host in self.policy.allowed_hosts:
                _, canonical, _, address = self.policy.resolve("https://" + host)
                rules.append("MAP " + canonical + " " + address)
            # DNS is pinned for this browser process, and every request is checked against the host grant.
            rules.append("MAP * ~NOTFOUND")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    channel="chromium",
                    headless=self.config.get("headless", True),
                    args=[
                        "--host-resolver-rules=" + ", ".join(rules),
                        "--disable-quic",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    ],
                )
                try:
                    context = browser.new_context(accept_downloads=False, service_workers="block")
                    context.set_default_timeout(self.policy.timeout * 1000)
                    context.set_default_navigation_timeout(self.policy.timeout * 1000)

                    def route(request_route):
                        try:
                            self.policy.resolve(request_route.request.url)
                            request_route.continue_()
                        except Exception:
                            request_route.abort("blockedbyclient")

                    context.route("**/*", route)
                    context.route_web_socket("**/*", lambda websocket: websocket.close())
                    self.context, self.pages, self.handles = context, {}, {}
                    self.ready.set_result(True)
                    while True:
                        item = self.requests.get()
                        if item is None:
                            break
                        name, arguments, future = item
                        try:
                            answer = getattr(self, "_" + name)(**arguments)
                            future.set_result(answer)
                        except ValidationError as exc:
                            future.set_exception(exc)
                        except BaseException as exc:
                            # Clicks/fills/navigation may have happened before a transport failure.
                            future.set_exception(
                                InterruptedEffect(
                                    "Browser operation outcome is unknown ("
                                    + type(exc).__name__
                                    + "); no retry sent"
                                )
                            )
                            self.poisoned = True
                            break
                finally:
                    browser.close()
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(
                    ValidationError(
                        "Browser startup failed ("
                        + type(exc).__name__
                        + "). Install flora-lang[browser] and run: python -m playwright install chromium"
                    )
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
                        InterruptedEffect("Browser connection closed; inspect outcome externally")
                    )

    def _call(self, name, **arguments):
        if self.poisoned or self.closed:
            raise ValidationError("Browser unavailable; handles do not survive process restart")
        future = concurrent.futures.Future()
        self.requests.put((name, arguments, future), timeout=1)
        try:
            return future.result(timeout=self.policy.timeout + 8)
        except concurrent.futures.TimeoutError:
            self.poisoned = True
            raise InterruptedEffect("Browser deadline passed; operation outcome unknown") from None

    def _page(self, tab):
        if tab not in self.pages or self.pages[tab].is_closed():
            raise ValidationError(
                "Unknown browser tab; tabs from earlier processes are not restored"
            )
        return self.pages[tab]

    def _invalidate(self, tab):
        for key in [key for key, value in self.handles.items() if value[0] == tab]:
            try:
                self.handles[key][1].dispose()
            except Exception:
                pass
            del self.handles[key]

    def _open(self, url):
        self.policy.resolve(url)
        if len(self.pages) >= 8:
            raise ValidationError("Browser tab limit is 8; close a tab first")
        page = self.context.new_page()
        page.on("popup", lambda popup: popup.close())
        tab = "tab-" + uuid.uuid4().hex[:12]
        self.pages[tab] = page
        try:
            page.goto(url, wait_until="domcontentloaded")
            return self._snapshot(tab)
        except Exception as exc:
            raise InterruptedEffect(
                "Browser navigation was dispatched but its observation failed; do not retry"
            ) from exc

    def _snapshot(self, tab):
        page = self._page(tab)
        self._invalidate(tab)
        text = page.locator("body").inner_text(timeout=self.policy.timeout * 1000)
        if len(text.encode()) > 2 * 1024 * 1024:
            raise ValidationError("Page text exceeds the 2 MiB snapshot bound")
        elements = page.locator(
            "a,button,input,textarea,select,[role=button],[contenteditable=true]"
        )
        count = elements.count()
        controls = []
        for i in range(min(count, 200)):
            handle = elements.nth(i).element_handle()
            if handle is None or not handle.is_visible():
                if handle:
                    handle.dispose()
                continue
            ref = "el-" + uuid.uuid4().hex[:12]
            self.handles[ref] = (tab, handle)
            controls.append(
                {
                    "ref": ref,
                    "label": (
                        handle.get_attribute("aria-label")
                        or handle.get_attribute("placeholder")
                        or handle.inner_text()
                        or handle.get_attribute("name")
                        or ""
                    )[:300],
                    "type": handle.get_attribute("type"),
                    "href": handle.get_attribute("href"),
                }
            )
        source = self.store.record(
            origin=page.url, title=page.title(), text=text + "\n\nCONTROLS\n" + json.dumps(controls)
        )
        return {
            **source,
            "tab": tab,
            "controls": controls,
            "total_controls": count,
            "controls_truncated": count > 200,
            "handles_survive_restart": False,
        }

    def _action(self, ref, value=None):
        if not self.config.get("allow_actions", False):
            raise ValidationError("Browser click/fill actions are disabled")
        if ref not in self.handles:
            raise ValidationError("Unknown or stale browser reference; take a new snapshot")
        tab, handle = self.handles[ref]
        if not handle.evaluate("element => element.isConnected"):
            raise ValidationError("Element was detached; take a new snapshot")
        try:
            if value is None:
                handle.click(no_wait_after=True)
            else:
                handle.fill(value)
            self._invalidate(tab)
        except Exception as exc:
            raise InterruptedEffect(
                "Browser action was dispatched but its outcome could not be confirmed; do not retry"
            ) from exc
        # A receipt acknowledges this action only. A new snapshot observes the resulting page.
        return {
            "tab": tab,
            "action": "click" if value is None else "fill",
            "dispatched": True,
            "next": "browser_snapshot",
        }

    def _screenshot(self, tab, path):
        page = self._page(tab)
        if not path.lower().endswith(".png"):
            raise ValidationError("Screenshot path must end in .png")
        result = self.files.create_bytes(
            path, page.screenshot(full_page=False, timeout=self.policy.timeout * 1000)
        )
        try:
            self.store.record_artifact(
                result["path"], result["sha256"], [], self.task_key(), kind="screenshot"
            )
        except Exception as exc:
            raise InterruptedEffect(
                "Screenshot was published but its artifact receipt could not be persisted"
            ) from exc
        return result

    def _close_tab(self, tab):
        page = self._page(tab)
        page.close()
        self._invalidate(tab)
        del self.pages[tab]
        return {"closed": tab}

    def browser_open(self, url: str) -> dict:
        """Open an allowed HTTP(S) URL and return observed text, controls and an ephemeral tab ID."""
        return self._call("open", url=url)

    def browser_snapshot(self, tab: str) -> dict:
        """Observe current rendered page and issue fresh element references. Earlier references expire."""
        return self._call("snapshot", tab=tab)

    def browser_click(self, ref: str) -> dict:
        """Click an observed element once. Requires allow_actions; no automatic retries or replay."""
        return self._call("action", ref=ref)

    def browser_fill(self, ref: str, value: str) -> dict:
        """Fill an observed field once. Requires allow_actions. Do not supply credentials in model context."""
        if not isinstance(value, str) or len(value) > 65536:
            raise ValidationError("Invalid browser field value")
        return self._call("action", ref=ref, value=value)

    def browser_screenshot(self, tab: str, path: str) -> dict:
        """Save a viewport PNG as a new workspace file; the model receives a file receipt, not image pixels."""
        return self._call("screenshot", tab=tab, path=path)

    def browser_close(self, tab: str) -> dict:
        """Close an owned browser tab and invalidate its references."""
        return self._call("close_tab", tab=tab)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.requests.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(timeout=self.policy.timeout + 8)
