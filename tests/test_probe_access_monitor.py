# SPDX-License-Identifier: Apache-2.0
"""Access-denial propagation through real workers with offline model responses."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from flora.general.agent import GeneralAgent, PauseRequested
from flora.integrations.providers import ModelResponse, TransportError
from tests.helpers import bundle, pure
from tests.live_reliability_probe import AccessFailureMonitor


class ProbeAccessMonitorTests(unittest.TestCase):
    def test_worker_denial_stops_pending_calls_but_allows_inflight_return(self):
        for status in (401, 402, 403, 404):
            for parallel in (1, 2):
                with self.subTest(status=status, parallel=parallel):
                    self.check_worker_denial(status, parallel)

    def check_worker_denial(self, status, parallel):
        rejecting_started = threading.Event()
        inflight_started = threading.Event()
        release_rejection = threading.Event()
        release_inflight = threading.Event()
        calls, returned, events = [], [], []
        monitor = AccessFailureMonitor(events)

        class Provider:
            def complete(self, messages, *, max_tokens):
                view = json.loads(messages[1]["content"])
                task = view["task"].rsplit("User task:\n", 1)[-1]
                calls.append(task)
                if task == "reject":
                    rejecting_started.set()
                    if not release_rejection.wait(5):
                        raise AssertionError("Rejecting worker was not released")
                    raise TransportError(
                        f"model HTTP {status}: rejected", category="http", status=status
                    )
                if task == "inflight":
                    inflight_started.set()
                    if not release_inflight.wait(5):
                        raise AssertionError("In-flight worker was not released")
                    returned.append(task)
                return ModelResponse(
                    json.dumps(bundle(pure("done"), view["epoch"], view["trace_digest"])),
                    1,
                    1,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            app = GeneralAgent(
                session_dir=root / "session",
                workspace=workspace,
                provider=Provider(),
                profile={
                    "general": {
                        "subagents": {"enabled": True, "max_parallel": parallel, "max_children": 4}
                    }
                },
                on_event=monitor,
            )
            monitor.app = app
            try:
                app.task = {"key": "current", "task": "parent"}
                app.work.begin("current", "parent")
                workers = app.delegation
                rejecting = workers.spawn_agent("reject")["agent_id"]
                self.assertTrue(rejecting_started.wait(5))
                ids = [rejecting]
                if parallel == 2:
                    ids.append(workers.spawn_agent("inflight")["agent_id"])
                    self.assertTrue(inflight_started.wait(5))

                # All pool slots are occupied; these cover both the executor
                # queue and the coordinator's undispatched dependency queue.
                queued = workers.spawn_agent("queued")["agent_id"]
                dependent = workers.spawn_agent("dependent", depends_on=[rejecting])["agent_id"]
                ids.extend([queued, dependent])
                release_rejection.set()
                workers.futures[rejecting].result(timeout=5)
                self.assertEqual(monitor.status, status)
                self.assertTrue(app.pause.is_set())
                self.assertTrue(workers.stop.is_set())
                release_inflight.set()
                for ident in ids:
                    workers.futures[ident].result(timeout=5)

                # Exercise the next parent compiler boundary in the same run.
                # GeneralAgent.run/resume intentionally starts a new run and
                # clears pause; the evaluator must not invoke either on denial.
                with self.assertRaises(PauseRequested):
                    app.agent.run("parent after rejection")
                self.assertEqual(calls, ["reject"] if parallel == 1 else ["reject", "inflight"])
                self.assertEqual(returned, [] if parallel == 1 else ["inflight"])
                self.assertEqual(workers.records[queued]["status"], "paused")
                self.assertEqual(workers.records[dependent]["status"], "paused")
                self.assertTrue(
                    any(
                        e.get("kind") == "transcript"
                        and e.get("channel") == "model_failure"
                        and e.get("actor") == rejecting
                        for e in events
                    )
                )
            finally:
                release_rejection.set()
                release_inflight.set()
                app.close()


if __name__ == "__main__":
    unittest.main()
