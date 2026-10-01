# SPDX-License-Identifier: Apache-2.0
"""Recovery admission survives scheduling/restart without taking over task semantics."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.engine.runtime import Runtime, RuntimeConfig
from flora.integrations.binding import make_registry
from flora.language.compiler import ScriptedCompiler
from flora.state.trace import MemoryTrace, SQLiteTrace
from flora.support.errors import ValidationError
from tests.helpers import bundle
from tests.test_long_tasks import sequence


class RecoveryAccountingTests(unittest.TestCase):
    def setup_runtime(self, trace=None):
        calls = []

        def blocked():
            calls.append("blocked")
            raise ValidationError("unchanged failure")

        def available():
            calls.append("available")
            return 7

        compiler = ScriptedCompiler(
            lambda c: bundle(sequence("blocked", 20), c.epoch, c.trace_digest)
        )
        runtime = Runtime(
            make_registry([blocked, available]),
            trace=trace,
            compiler=compiler,
            config=RuntimeConfig(max_steps=None, max_compile_cycles=None),
            memory={"user_owned": {"hypothesis": "source may recover"}},
        )
        return runtime, compiler, calls

    def test_one_step_slices_and_reopen_do_not_refund_recovery(self):
        for durable in (False, True):
            with self.subTest(durable=durable), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "trace.sqlite"
                trace = SQLiteTrace(path) if durable else MemoryTrace()
                runtime, compiler, calls = self.setup_runtime(trace)
                for attempt in range(10):
                    result = runtime.run(
                        "task" if attempt == 0 else None, slice_steps=1, repeated_error_limit=3
                    )
                    self.assertNotIn("__flora_error_recovery__", runtime.memory)
                    if result.status == "stalled":
                        break
                    if durable:
                        trace.close()
                        trace = SQLiteTrace(path)
                    runtime = Runtime.restore(runtime.tools, trace, compiler=compiler)
                self.assertEqual(result.status, "stalled")
                self.assertEqual(calls, ["blocked"] * 4)
                self.assertEqual(len(compiler.requests), 2)
                self.assertEqual(runtime.memory["user_owned"]["hypothesis"], "source may recover")
                # Explicit resume can request a revised strategy, never reset prior failures.
                runtime = Runtime.restore(runtime.tools, trace, compiler=compiler)
                self.assertEqual(
                    runtime.run(slice_steps=1, repeated_error_limit=3).status, "stalled"
                )
                self.assertEqual(len(calls), 4)
                # A different explicit action remains allowed; no hidden success decision.
                repaired = runtime.run(
                    bundle=bundle(sequence("available", 1), trace.epoch, trace.digest),
                    repeated_error_limit=3,
                )
                self.assertEqual(repaired.status, "completed")
                self.assertEqual(calls, ["blocked"] * 4 + ["available"])
                self.assertIsNone(trace.load_checkpoint()["error_recovery"])
                trace.close()

    def test_recovery_state_is_checked_and_legacy_checkpoint_remains_readable(self):
        runtime, compiler, _ = self.setup_runtime()
        runtime.run("task", slice_steps=1, repeated_error_limit=3)
        original = runtime.trace.load_checkpoint()
        invalid = [
            [],
            {"epoch": 0},
            {"signature": "x" * 64, "epoch": 0, "stalled": False},
            {"signature": "a" * 64, "epoch": True, "stalled": False},
            {"signature": "a" * 64, "epoch": 99, "stalled": False},
            {"signature": "a" * 64, "epoch": 0, "stalled": "no"},
        ]
        for value in invalid:
            with self.subTest(value=value):
                runtime.trace.save_checkpoint({**original, "error_recovery": value})
                with self.assertRaisesRegex(ValidationError, "recovery"):
                    Runtime.restore(runtime.tools, runtime.trace, compiler=compiler)
        del original["error_recovery"]
        runtime.trace.save_checkpoint(original)
        restored = Runtime.restore(runtime.tools, runtime.trace, compiler=compiler)
        self.assertEqual(restored.trace.epoch, 1)
        self.assertIsNone(restored._error_recovery)

    def test_unknown_outcome_and_external_resolution_do_not_refund_recovery(self):
        for pending in (False, True):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "trace.sqlite"
                trace = SQLiteTrace(path)
                runtime, compiler, calls = self.setup_runtime(trace)
                settle = trace.settle

                def lose_receipt(event_id, outcome):
                    if event_id == 3:
                        if pending:
                            raise OSError("injected settlement failure")
                        outcome = {
                            "status": "interrupted_unknown",
                            "error": {
                                "type": "ObservationUnavailable",
                                "message": "lost response",
                            },
                        }
                    return settle(event_id, outcome)

                with patch.object(trace, "settle", side_effect=lose_receipt):
                    if pending:
                        with self.assertRaises(OSError):
                            runtime.run("task", repeated_error_limit=3)
                    else:
                        self.assertEqual(
                            runtime.run("task", repeated_error_limit=3).status,
                            "interrupted_unknown",
                        )
                self.assertEqual(len(calls), 4)
                trace.close()
                trace = SQLiteTrace(path)
                try:
                    restored = Runtime.restore(runtime.tools, trace, compiler=compiler)
                    self.assertEqual(
                        restored.run(repeated_error_limit=3).status, "interrupted_unknown"
                    )
                    self.assertIsNotNone(trace.load_checkpoint()["error_recovery"])
                    trace.resolve(
                        3,
                        {
                            "status": "raised",
                            "error": {
                                "type": "ValidationError",
                                "message": "unchanged failure",
                            },
                        },
                        reason="Fixture operator verified the same failure",
                    )
                    restored = Runtime.restore(runtime.tools, trace, compiler=compiler)
                    self.assertEqual(restored.run(repeated_error_limit=3).status, "stalled")
                    self.assertEqual(len(calls), 4)
                    self.assertEqual(len(compiler.requests), 2)
                finally:
                    trace.close()


if __name__ == "__main__":
    unittest.main()
