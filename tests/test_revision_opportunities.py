# SPDX-License-Identifier: Apache-2.0
"""Host-authored offline revision syntax examples, not live model evidence."""

import json
import unittest

from flora.engine.runtime import Runtime
from flora.integrations.tools import ToolRegistry, ToolSpec
from flora.language.compiler import validate_bundle
from flora.language.frontend import lower_bundle
from flora.language.recovery import revision_example
from flora.language.revision_view import revision_state
from tests.helpers import block, bundle, pure
from tests.live_reliability_probe import mechanism_observations


def var(name):
    return {"var": name}


def op(name, dest, *args):
    return {"op": name, "dest": dest, "args": list(args)}


def batch_consumer():
    """A valid program with a consumer that faults on the actual observed shape."""
    return {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block(
                term={
                    "op": "effect",
                    "tool": "read_batch",
                    "args": {},
                    "bind": "reply",
                    "capture": {},
                    "resume": "consume",
                }
            ),
            "consume": block(
                ["reply"],
                [
                    op("get", "payload", var("reply"), "value"),
                    op("get", "count", var("payload"), "count"),
                ],
                {"op": "return", "value": var("count")},
            ),
        },
    }


def revised_consumer():
    """The proposed representation contains the batch value, not its envelope."""
    program = pure(var("count"), params=["record"])
    program["blocks"]["main"]["ops"] = [
        op("get", "items", var("record"), "items"),
        op("length", "count", var("items")),
    ]
    return program


def migrate_observed_reply():
    """Use real source registers at both historical and current checkpoints."""
    program = pure({"record": var("payload")}, params=["context"])
    program["blocks"]["main"]["ops"] = [
        op("get", "inputs", var("context"), "inputs"),
        op("get", "reply", var("inputs"), "reply"),
        op("get", "payload", var("reply"), "value"),
    ]
    return program


class RevisionOpportunityTests(unittest.TestCase):
    def test_visible_revision_example_is_valid_source_not_a_task_answer(self):
        example = json.loads(revision_example().splitlines()[-1])
        checked = validate_bundle(lower_bundle(example, syntax="block-list-v2"))
        self.assertEqual(checked["revisions"][0]["mode"], "EXTEND")
        self.assertEqual(checked["programs"][0]["program"], checked["revisions"][0]["program"])

    def test_state_projection_contains_real_shapes_without_values_or_nested_keys(self):
        runtime, _ = self.faulted_runtime()
        candidate = runtime.candidates["main"]
        candidate.machine.registers["metadata"] = {"private_nested_key": "private-value"}
        view = revision_state(
            candidate,
            runtime.contexts,
            epoch=runtime.trace.epoch,
            trace_digest=runtime.trace.digest,
        )
        self.assertTrue(view["anchor_current"])
        self.assertEqual(view["machine_status"], "fault")
        self.assertEqual(view["retained_context_count"], 1)
        self.assertEqual(view["checkpoint_shapes"][0]["register_types"], {"reply": "object"})
        self.assertEqual(view["register_types"]["metadata"], "object")
        self.assertNotIn("private", json.dumps(view))
        for n in range(100):
            candidate.machine.registers[f"register_{n}"] = "private-value"
        bounded = revision_state(candidate, runtime.contexts, epoch=-1, trace_digest="stale")
        self.assertFalse(bounded["anchor_current"])
        self.assertEqual(len(bounded["register_types"]), 32)
        self.assertGreater(bounded["registers_omitted"], 0)
        self.assertLessEqual(len(json.dumps(bounded).encode()), 8192)

    def faulted_runtime(self):
        calls = []

        def read_batch():
            calls.append("read_batch")
            return {"items": ["a", "b"]}

        runtime = Runtime(
            ToolRegistry(
                [
                    ToolSpec(
                        "read_batch",
                        read_batch,
                        "Consume and return one batch; do not repeat the observation.",
                        {"type": "object", "properties": {}, "additionalProperties": False},
                    )
                ]
            )
        )
        before = runtime.run("Count the observed batch items", bundle=bundle(batch_consumer()))
        self.assertEqual(before.status, "needs_program")
        self.assertEqual(calls, ["read_batch"])
        self.assertEqual(len(runtime.contexts), 1)
        self.assertTrue(
            any(
                report["kind"] == "consumer_check" and report["result"]["verdict"] == "FAIL"
                for report in runtime.reports
            )
        )
        return runtime, calls

    def test_extend_representation_checks_history_and_current_without_repeating_tool(self):
        runtime, calls = self.faulted_runtime()
        epoch, anchor = runtime.trace.epoch, runtime.trace.digest
        report = runtime.apply_revision(
            "main",
            revised_consumer(),
            migration=migrate_observed_reply(),
            mode="EXTEND",
            revision_id="normalize_batch",
        )
        self.assertTrue(report["accepted"], report)
        self.assertEqual([result["verdict"] for result in report["results"]], ["PASS"])
        self.assertEqual(report["current_check"]["verdict"], "PASS")
        self.assertEqual(report["task_improvement"], "UNMEASURED")
        self.assertEqual((runtime.trace.epoch, runtime.trace.digest), (epoch, anchor))
        self.assertEqual(
            runtime.candidates["main"].machine.registers, {"record": {"items": ["a", "b"]}}
        )
        after = runtime.run("Count the observed batch items")
        self.assertEqual(after.status, "completed")
        self.assertEqual(after.value, 2)
        self.assertEqual(calls, ["read_batch"])
        self.assertEqual(len(runtime.trace.records), 1)
        self.assertTrue(
            any(r["kind"] == "revision_checked" and r["accepted"] for r in runtime.reports)
        )
        activity = mechanism_observations(runtime.reports)
        self.assertEqual(activity["revision_modes"]["EXTEND"]["accepted"], 1)
        self.assertEqual(activity["history_and_current_pass_revisions"], 1)

    def test_preserve_cannot_claim_fault_to_return_is_the_same_boundary(self):
        runtime, calls = self.faulted_runtime()
        report = runtime.apply_revision(
            "main",
            revised_consumer(),
            migration=migrate_observed_reply(),
            mode="PRESERVE",
            revision_id="not_equivalent",
        )
        self.assertFalse(report["accepted"], report)
        self.assertTrue(any(result["verdict"] != "PASS" for result in report["results"]))
        self.assertEqual(runtime.candidates["main"].status, "FAULTED")
        self.assertEqual(calls, ["read_batch"])


if __name__ == "__main__":
    unittest.main()
