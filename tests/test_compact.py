# SPDX-License-Identifier: Apache-2.0
import copy
import json
import unittest

from flora.engine.runtime import Runtime
from flora.examples import calendar_bundle, pagination_bundle
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler, validate_bundle
from flora.language.frontend import lower_block_list, lower_bundle, lower_program
from flora.language.prompts import compact_prompt, examples
from flora.language.vm import new_machine, run_until_boundary
from flora.support.errors import CompilerError, StaleAnchor, ValidationError
from tests.helpers import bundle, context, pure
from tests.test_frontend import observe
from tests.test_recovery import SequenceProvider


def listed(program):
    order = [program["entry"]] + [k for k in program["blocks"] if k != program["entry"]]
    return [{"label": k, **copy.deepcopy(program["blocks"][k])} for k in order]


class CompactCompilerTests(unittest.TestCase):
    def test_complete_prompt_examples_are_valid_and_preserve_values(self):
        for example in examples():
            lowered = lower_bundle(example)
            validate_bundle(lowered)
            source = copy.deepcopy(example)
            for collection in ("programs", "diagnostics"):
                for item in source.get(collection, []):
                    item["program"] = listed(item["program"])
            self.assertEqual(lower_bundle(source, syntax="block-list-v1"), lowered)
        self.assertEqual(
            run_until_boundary(
                new_machine(lower_bundle(examples()[0])["programs"][0]["program"])
            ).value,
            "Hello! How can I help?",
        )

    def test_diagnostic_prompt_example_executes_one_probe_and_observed_continuation(self):
        for resource in ("a", "b"):
            calls = []

            def example_locate():
                calls.append(("locate", {}))
                return {"resource": resource}

            def example_read(resource: str):
                calls.append(("read", resource))
                return {"count": {"a": 17, "b": 29}[resource]}

            runtime = Runtime(tools=make_registry([example_locate, example_read]))
            result = runtime.run(
                "Read the count from the located resource", bundle=lower_bundle(examples()[2])
            ).to_dict()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["value"], {"a": 17, "b": 29}[resource])
            self.assertEqual(calls, [("locate", {}), ("read", resource)])
            report = next(
                r["report"] for r in result["reports"] if r["kind"] == "diagnostic_evaluated"
            )
            self.assertGreater(report["score"], 0)
            self.assertTrue(report["hypothetical"])
            self.assertFalse(report["contract_evidence"])

    def test_list_entry_is_first_not_alphabetical_and_source_not_mutated(self):
        source = [
            {
                "label": "z_entry",
                "params": [],
                "ops": [],
                "term": {"op": "jump", "target": "a_end", "args": {}},
            },
            {"label": "a_end", "params": [], "ops": [], "term": {"op": "return", "value": 17}},
        ]
        before = copy.deepcopy(source)
        lowered = lower_block_list(source)
        self.assertEqual(lowered["entry"], "z_entry")
        self.assertEqual(run_until_boundary(new_machine(lowered)).value, 17)
        self.assertEqual(source, before)

    def test_calendar_and_pagination_candidates_diagnostics_identical(self):
        for original in (calendar_bundle(), pagination_bundle()):
            source = copy.deepcopy(original)
            for collection in ("programs", "diagnostics", "revisions"):
                for item in source.get(collection, []):
                    item["program"] = listed(item["program"])
                    if "migration" in item:
                        item["migration"] = listed(item["migration"])
            self.assertEqual(lower_bundle(source, syntax="block-list-v1"), original)

    def test_list_observation_still_has_explicit_consumers_and_unknown_halt(self):
        self.assertEqual(lower_block_list(listed(observe())), lower_program(observe()))
        bad = listed(observe())
        del bad[0]["term"]["error"]
        with self.assertRaises(ValidationError):
            lower_block_list(bad)

    def test_observe_reports_both_consumers_and_all_blocks_without_mutation(self):
        source = observe()
        source["blocks"]["next"] = copy.deepcopy(source["blocks"]["main"])
        source["blocks"]["ok"]["params"] = ["wrong_success"]
        source["blocks"]["err"]["params"] = ["wrong_error"]
        source = listed(source)
        before = copy.deepcopy(source)
        with self.assertRaises(ValidationError) as caught:
            lower_block_list(source)
        message = str(caught.exception)
        for label in ("main", "next"):
            for outcome in ("success", "error"):
                self.assertIn(f"block '{label}': {outcome} target", message)
        self.assertEqual(source, before)

    def test_aggregate_errors_repaired_in_one_accounted_compilation(self):
        good = listed(observe())
        bad = copy.deepcopy(good)
        bad[1]["params"] = ["wrong_success"]
        bad[2]["params"] = ["wrong_error"]
        requests, charged = [], []

        class RecordingProvider(SequenceProvider):
            def complete(self, messages, *, max_tokens):
                requests.append(copy.deepcopy(messages))
                return super().complete(messages, max_tokens=max_tokens)

        provider = RecordingProvider(
            ModelResponse(json.dumps(bundle(bad)), 1, 1),
            ModelResponse(json.dumps(bundle(good)), 1, 1),
        )
        compiler = LLMCompiler(
            provider,
            syntax="block-list-v1",
            prompt_style="compact-v1",
            max_repairs=1,
            before_call=charged.append,
        )
        compiled = compiler.compile(context(tools=[{"name": "read_value"}]))
        self.assertEqual(compiled, lower_bundle(bundle(observe())))
        repair = json.loads(requests[1][-1]["content"])
        self.assertIn("success target", repair["validation_error"])
        self.assertIn("error target", repair["validation_error"])
        self.assertLessEqual(len(repair["validation_error"]), 4096)
        self.assertEqual(len(charged), 2)
        self.assertEqual(provider.calls, 2)

    def test_observe_aggregation_is_bounded(self):
        source = observe()
        source["blocks"]["ok"]["params"] = ["wrong"]
        for i in range(24):
            source["blocks"][f"probe_{i}"] = copy.deepcopy(source["blocks"]["main"])
        with self.assertRaises(ValidationError) as caught:
            lower_block_list(listed(source))
        self.assertEqual(str(caught.exception).count("success target"), 16)

    def test_list_rejects_duplicates_unknown_fields_labels_and_size(self):
        block = listed(pure("ok"))[0]
        for bad in (
            [],
            [block, block],
            [dict(block, extra=1)],
            [dict(block, label="")],
            [dict(block, term=None)],
            [block] * 513,
            "not a program",
        ):
            with self.subTest(bad=str(bad)[:70]), self.assertRaises(ValidationError):
                lower_block_list(bad)
        with self.assertRaises(ValidationError):
            lower_bundle(bundle(), syntax="unknown")

    def test_literal_data_is_not_interpreted_as_blocks_or_programs(self):
        literal = [{"label": "fake", "term": {"op": "observe"}, "params": [], "ops": []}]
        source = bundle(listed(pure(literal)))
        source["programs"][0]["inputs"] = {}
        lowered = lower_bundle(source, syntax="block-list-v1")
        self.assertEqual(
            run_until_boundary(new_machine(lowered["programs"][0]["program"])).value, literal
        )

    def test_inline_operation_is_rejected_not_silently_returned_or_executed(self):
        data = {"op": "concat", "args": ["a", "b"]}
        with self.assertRaisesRegex(ValidationError, "inline operation 'concat'"):
            lower_block_list(listed(pure(data)))
        # Intentional code-as-data and legacy semantics remain fully expressible.
        escaped = lower_block_list(listed(pure({"literal": data})))
        self.assertEqual(run_until_boundary(new_machine(escaped)).value, data)
        self.assertEqual(run_until_boundary(new_machine(lower_program(pure(data)))).value, data)
        nested = listed(pure({"answer": data}))
        with self.assertRaisesRegex(ValidationError, "term.value"):
            lower_block_list(nested)

    def test_compact_has_complete_context_exact_task_last_and_core_rules(self):
        p = SequenceProvider()
        c = LLMCompiler(p, syntax="block-list-v1", prompt_style="compact-v1")
        ctx = context()
        messages = c.build_messages(ctx)
        view = json.loads(messages[1]["content"])
        self.assertEqual(view["task"], ctx.task)
        self.assertEqual(list(view)[-1], "task")
        self.assertEqual(view["tools"], ctx.tools)
        self.assertEqual(view["epoch"], ctx.epoch)
        self.assertEqual(view["trace_digest"], ctx.trace_digest)
        for phrase in (
            "DIAGNOSTICS",
            "SCHEDULING IS REAL",
            "A diagnostic never needs to be the incumbent",
            "return that value itself",
            "witnesses",
            "REVISIONS",
            "PRESERVE",
            "EXTEND",
            "CHANGE",
            "PASS/FAIL/UNKNOWN",
            "current-input rechecks",
            "ONE\nreal trajectory",
            "visibility",
            "Interrupted or unknown effects HALT",
        ):
            self.assertIn(phrase, messages[0]["content"])
        self.assertIn("PROGRAM is an ARRAY", messages[0]["content"])
        self.assertNotIn('PROGRAM = {"version":1', messages[0]["content"])
        self.assertEqual(compact_prompt("observe-v1").count('PROGRAM = {"version":1'), 1)

    def test_list_compile_uses_same_capabilities_and_stale_anchor_gate(self):
        source = bundle(listed(pure("hi")))
        provider = SequenceProvider(ModelResponse(json.dumps(source), 10, 5))
        compiled = LLMCompiler(provider, syntax="block-list-v1", prompt_style="compact-v1").compile(
            context()
        )
        self.assertEqual(compiled, bundle(pure("hi")))
        source["expected_epoch"] += 1
        with self.assertRaises(StaleAnchor):
            LLMCompiler(
                SequenceProvider(ModelResponse(json.dumps(source), 10, 5)),
                syntax="block-list-v1",
                prompt_style="compact-v1",
            ).compile(context())
        source = bundle(listed(observe("ungranted")))
        with self.assertRaises(CompilerError):
            LLMCompiler(
                SequenceProvider(ModelResponse(json.dumps(source), 10, 5)),
                syntax="block-list-v1",
                prompt_style="compact-v1",
                max_repairs=0,
            ).compile(context())

    def test_list_migration_still_cannot_call_tools(self):
        source = bundle(listed(pure("ok")))
        migration = listed(observe())
        migration[0]["params"] = ["context"]
        source["revisions"] = [
            {
                "id": "rev",
                "target_candidate": "old",
                "program": listed(pure("ok")),
                "migration": migration,
                "mode": "PRESERVE",
            }
        ]
        lowered = lower_bundle(source, syntax="block-list-v1")
        with self.assertRaises(ValidationError):
            validate_bundle(lowered)

    def test_legacy_modes_and_invalid_option_combinations(self):
        self.assertEqual(LLMCompiler(SequenceProvider()).prompt_style, "full-v1")
        self.assertNotIn(
            "compiler_prompt_style",
            json.loads(LLMCompiler(SequenceProvider()).build_messages(context())[1]["content"]),
        )
        for options in (
            {"prompt_style": []},
            {"prompt_style": "compact-v1"},
            {"syntax": "block-list-v1"},
            {"prompt_style": "missing"},
        ):
            with self.assertRaises(ValidationError):
                LLMCompiler(SequenceProvider(), **options)
