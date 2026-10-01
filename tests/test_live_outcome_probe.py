"""Offline driver and actual tool receipt coverage checks; no network requests.

Before integration into the current release, FLORA_TEST_PROBE_SUPPORT may name
its newer complete_versions-enabled helper for these tests only.
"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from flora.coding.navigation import CodingFiles
from flora.general.agent import GeneralAgent
from flora.general.documents import DocumentTools, DocumentWorkspace
from flora.general.storage import ObservationStore
from flora.integrations.providers import ModelResponse, TransportError
from tests import live_reliability_probe as canonical
from tests.helpers import bundle, pure
from tests.live_outcome_probe import BASE_URL, configure_support, load_support, main, source_entries
from tests.outcome_probe_cases import assess_outcome, make_outcome_fixture


class LiveOutcomeProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text(json.dumps({"general": {}, "compiler": {"max_repairs": 0}}))
        support_path = os.environ.get("FLORA_TEST_PROBE_SUPPORT")
        self.support = load_support(support_path)

    def tearDown(self):
        self.tmp.cleanup()

    def options(self, families=None, arm="natural"):
        return Namespace(
            model="deepseek-flash",
            families=families or ["embedded_task"],
            arm=arm,
            layout="json-v1",
        )

    def args(self, options, output=None):
        return Namespace(
            models=options.model,
            cases=",".join(options.families),
            rounds=1,
            seed=19,
            profile=self.profile,
            output=output or self.root / "output",
            base_url=BASE_URL,
            resume_attempts=0,
            reopen_after_write=False,
            allow_insecure_http=False,
            continue_on_transport_error=False,
        )

    def install_provider(self, provider):
        def construct(**kwargs):
            kwargs["profile"].pop("provider", None)
            kwargs["provider"], kwargs["session_key"] = provider, None
            return GeneralAgent(**kwargs)

        self.support.GeneralAgent = construct

    def test_adapter_does_not_mutate_canonical_probe_and_records_task_hash(self):
        old_tasks = copy.deepcopy(canonical.TASKS)
        old_main, old_evaluate = canonical.main, canonical.evaluate
        options = self.options()
        configure_support(self.support, options)
        views = []

        class Provider:
            def complete(self, messages, *, max_tokens):
                view = json.loads(messages[1]["content"])
                views.append(view)
                task = view["task"]
                records = json.loads(task.split("Records: ", 1)[1].split(". Do not change", 1)[0])
                answer = {
                    "total": sum(r["units"] for r in records),
                    "labels": list(dict.fromkeys(r["label"] for r in records)),
                }
                return ModelResponse(
                    json.dumps(bundle(pure(answer), view["epoch"], view["trace_digest"])), 1, 1
                )

        self.install_provider(Provider())
        with patch("builtins.print"):
            rows = self.support.evaluate(self.args(options), "offline-only")
        self.assertTrue(rows[0]["grade"]["passed"], rows)
        self.assertEqual(len(views), 1)
        self.assertEqual(
            rows[0]["task_sha256"],
            hashlib.sha256(self.support.TASKS["embedded_task"].encode()).hexdigest(),
        )
        self.assertEqual(canonical.TASKS, old_tasks)
        self.assertIs(canonical.main, old_main)
        self.assertIs(canonical.evaluate, old_evaluate)
        oracle = self.root / "output/embedded_task/case-0001/oracle.json"
        self.assertTrue(oracle.exists())
        self.assertFalse(list(oracle.with_name("workspace").iterdir()))
        self.assertNotIn("oracle", views[0]["memory"]["data"])
        report = json.loads((self.root / "output/outcome-evaluation.json").read_text())
        self.assertEqual(
            report["source"]["outcome_driver"]["task_contexts"]["embedded_task"]["task_sha256"],
            rows[0]["task_sha256"],
        )

    def test_fixed_cli_adapter_and_finite_profile_limits_are_preserved(self):
        calls = []
        self.support.main = lambda: calls.append(list(__import__("sys").argv)) or 0
        original = list(__import__("sys").argv)
        with patch.object(self.support.os, "environ", LookupForbidden()):
            self.assertEqual(
                main(
                    [
                        "--model",
                        "deepseek-v4-pro",
                        "--family",
                        "embedded_task",
                        "--arm",
                        "explicit",
                        "--layout",
                        "csv-v2",
                        "--profile",
                        str(self.profile),
                        "--output",
                        str(self.root / "cli"),
                        "--api-key-env",
                        "UNUSED",
                    ],
                    support=self.support,
                ),
                0,
            )
        self.assertEqual(__import__("sys").argv, original)
        self.assertIn(BASE_URL, calls[0])
        self.assertIn("deepseek-v4-pro", calls[0])
        self.assertEqual(calls[0][calls[0].index("--rounds") + 1], "1")
        self.profile.write_text(
            json.dumps(
                {
                    "budget": {"max_model_calls": 17, "max_wall_seconds": 777},
                    "general": {"subagents": {"budget": {"max_model_calls": 9}}},
                }
            )
        )
        from tests.live_outcome_probe import validate_bounded_profile

        limits = validate_bounded_profile(self.support, self.profile)
        self.assertEqual(limits["parent"]["max_model_calls"], 17)
        self.assertEqual(limits["parent"]["max_wall_seconds"], 777)
        self.assertEqual(limits["per_child"]["max_model_calls"], 9)

    def test_http_rejection_stops_remaining_families_without_more_provider_calls(self):
        for status in (401, 402, 403):
            support = load_support(os.environ.get("FLORA_TEST_PROBE_SUPPORT"))
            self.support = support
            options = self.options(["embedded_task", "source_resolution", "allowed_conversion"])
            configure_support(support, options)
            calls = []

            class Rejected:
                def complete(self, messages, *, max_tokens):
                    calls.append(1)
                    raise TransportError("access denied", category="http", status=status)

            self.install_provider(Rejected())
            with patch("builtins.print"):
                rows = support.evaluate(
                    self.args(options, self.root / f"reject-{status}"), "offline-only"
                )
            self.assertEqual(calls, [1])
            self.assertEqual([r["status"] for r in rows[1:]], ["not_run", "not_run"])
            self.assertEqual(rows[0]["stop"]["http_status"], status)

    def test_non_http_balance_denial_pauses_and_stops_next_case_without_invented_http(self):
        options = self.options(["embedded_task", "allowed_conversion"])
        configure_support(self.support, options)
        calls = []

        class Provider:
            def complete(self, messages, *, max_tokens):
                calls.append(1)
                raise TransportError("insufficient_balance", category="response", retryable=True)

        self.install_provider(Provider())
        with patch("builtins.print"):
            rows = self.support.evaluate(self.args(options), "offline-only")
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows[1]["status"], "not_run")
        self.assertEqual(rows[0]["stop"], {"kind": "balance_rejected", "http_status": None})

    def test_limits_and_endpoint_rejected_before_environment_lookup(self):
        options = self.options()
        configure_support(self.support, options)
        for changes in (
            {"base_url": "https://evil.invalid"},
            {"models": "glm-fixture"},
            {"rounds": 2},
            {"resume_attempts": 1},
        ):
            args = self.args(options)
            for k, v in changes.items():
                setattr(args, k, v)
            with self.assertRaises(ValueError):
                self.support.validate_options(args)
        self.profile.write_text(json.dumps({"budget": {"max_model_calls": None}}))
        with patch.object(self.support.os, "environ", LookupForbidden()) as environment:
            with patch(
                "sys.argv",
                [
                    "probe",
                    "--models",
                    "deepseek-flash",
                    "--cases",
                    "embedded_task",
                    "--base-url",
                    BASE_URL,
                    "--rounds",
                    "1",
                    "--profile",
                    str(self.profile),
                    "--output",
                    str(self.root / "output"),
                    "--api-key-env",
                    "UNUSED",
                ],
            ):
                with patch("sys.stderr"), self.assertRaises(SystemExit):
                    self.support.main()
        self.assertEqual(environment, {})
        with patch.object(self.support.os, "environ", LookupForbidden()):
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                main(
                    [
                        "--model",
                        "gpt-forbidden",
                        "--family",
                        "embedded_task",
                        "--profile",
                        str(self.profile),
                        "--output",
                        str(self.root / "output"),
                        "--api-key-env",
                        "UNUSED",
                    ],
                    support=self.support,
                )

    def test_actual_receipt_coverage_supports_file_document_table_without_citation_shortcut(self):
        for layout in ("json-v1", "csv-v2"):
            root = self.root / layout
            root.mkdir()
            spec = make_outcome_fixture("source_resolution", root, 31, layout=layout)
            files = CodingFiles(root)
            store_dir = self.root / (layout + "-store")
            store_dir.mkdir()
            store = ObservationStore(store_dir)
            docs = DocumentTools(DocumentWorkspace(root), store, lambda: "fixture")
            try:
                receipts = []
                for path in spec["oracle"]["required_sources"]:
                    tool = "table_query" if path.endswith(".csv") else "read_file"
                    owner = docs if tool == "table_query" else files
                    args = {"path": path}
                    receipts.append(
                        {
                            "tool": tool,
                            "args": args,
                            "status": "returned",
                            "value": getattr(owner, tool)(**args),
                        }
                    )
                result = {"_receipts": receipts[:1], "workers": [{"_receipts": receipts[1:]}]}
                evidence = source_entries(result, self.support.complete_versions)
                self.assertTrue(
                    assess_outcome(
                        "completed",
                        spec["oracle"]["answer"],
                        root,
                        spec["oracle"],
                        observed_sources=evidence,
                    )["passed"]
                )
                document = docs.read_document("dossier.json")
                document_evidence = source_entries(
                    {
                        "_receipts": [
                            {
                                "tool": "read_document",
                                "args": {"path": "dossier.json"},
                                "status": "returned",
                                "value": document,
                            }
                        ]
                    },
                    self.support.complete_versions,
                )
                self.assertIn(
                    {
                        "path": "dossier.json",
                        "sha256": spec["oracle"]["input_hashes"]["dossier.json"],
                        "complete": True,
                    },
                    document_evidence,
                )
                partial = {
                    "_receipts": [
                        {
                            "tool": "read_file",
                            "args": {"path": "dossier.json", "max_bytes": 1},
                            "status": "returned",
                            "value": files.read_file("dossier.json", max_bytes=1),
                        }
                    ],
                    "value": {"citations": evidence},
                }
                self.assertEqual(source_entries(partial, self.support.complete_versions), [])
            finally:
                store.close()


class LookupForbidden(dict):
    def get(self, key, *args, **kwargs):
        if key == "UNUSED":
            raise AssertionError("Authentication lookup must not occur")
        return super().get(key, *args, **kwargs)


if __name__ == "__main__":
    unittest.main()
