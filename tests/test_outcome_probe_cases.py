"""Offline outcome oracle rejection and paired fixture consistency checks."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from tests.outcome_probe_cases import EXPLICIT_CHECK, FAMILIES, assess_outcome, make_outcome_fixture


class OutcomeProbeCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, family="source_resolution", *, seed=71, layout="json-v1", explicit=False):
        root = self.root / str(len(list(self.root.iterdir())))
        root.mkdir()
        spec = make_outcome_fixture(family, root, seed, layout=layout, explicit=explicit)
        evidence = [
            {"path": p, "sha256": h, "complete": True}
            for p, h in spec["oracle"]["input_hashes"].items()
        ]
        return root, spec, evidence

    def grade(self, root, spec, evidence, value=None):
        return assess_outcome(
            "completed",
            spec["oracle"]["answer"] if value is None else value,
            root,
            spec["oracle"],
            observed_sources=evidence,
        )

    def test_paired_arms_only_add_one_request_and_leave_fixture_oracle_identical(self):
        for family in FAMILIES:
            for layout in ("json-v1", "csv-v2"):
                with self.subTest(family=family, layout=layout):
                    aroot, natural, _ = self.fixture(family, layout=layout)
                    broot, explicit, _ = self.fixture(family, layout=layout, explicit=True)
                    self.assertEqual(explicit["task"], natural["task"] + " " + EXPLICIT_CHECK)
                    self.assertEqual(explicit["oracle"], natural["oracle"])
                    self.assertEqual(
                        {p.name: p.read_bytes() for p in aroot.iterdir()},
                        {p.name: p.read_bytes() for p in broot.iterdir()},
                    )
                    self.assertNotIn("worker", natural["task"])
                    self.assertNotIn("read_file", natural["task"])
                    self.assertFalse(any("oracle" in p.name for p in aroot.iterdir()))

    def test_layout_shift_preserves_native_types_and_same_expected_outcome(self):
        decoded = []
        answers = []
        for layout in ("json-v1", "csv-v2"):
            root, spec, evidence = self.fixture(layout=layout)
            manifest = json.loads((root / "dossier.json").read_text())
            rows = []
            for source in manifest["sources"]:
                path = root / source["path"]
                if source["format"] == "json":
                    rows.extend(json.loads(path.read_text())["rows"])
                else:
                    with path.open(newline="") as handle:
                        rows.extend(
                            {
                                "entity": row["subject"],
                                "rank": int(row["sequence"]),
                                "official": json.loads(row["trusted"]),
                                "payload": json.loads(row["value_json"]),
                            }
                            for row in csv.DictReader(handle)
                        )
            decoded.append(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)))
            answers.append(spec["oracle"]["answer"])
            self.assertTrue(self.grade(root, spec, evidence)["passed"])
        self.assertEqual(decoded[0], decoded[1])
        self.assertEqual(answers[0], answers[1])

    def test_oracle_rejects_type_drift_null_conflation_and_arbitrary_conflict_resolution(self):
        root, spec, evidence = self.fixture()
        answer = spec["oracle"]["answer"]
        keys = list(answer)
        for index, changed in (
            (0, str(answer[keys[0]]["value"])),
            (1, int(answer[keys[1]]["value"])),
            (2, 1),
            (3, "null"),
        ):
            wrong = copy.deepcopy(answer)
            wrong[keys[index]]["value"] = changed
            self.assertFalse(self.grade(root, spec, evidence, wrong)["passed"])
        for index in (3, 4, 5):
            wrong = copy.deepcopy(answer)
            wrong[keys[index]]["status"] = "confirmed" if index != 3 else "unknown"
            self.assertFalse(self.grade(root, spec, evidence, wrong)["passed"])

    def test_json_numbers_share_type_but_booleans_do_not(self):
        root, spec, evidence = self.fixture()
        answer = copy.deepcopy(spec["oracle"]["answer"])
        numeric = next(k for k, v in answer.items() if type(v["value"]) is int)
        answer[numeric]["value"] = float(answer[numeric]["value"])
        self.assertTrue(self.grade(root, spec, evidence, answer)["passed"])
        boolean = next(k for k, v in answer.items() if type(v["value"]) is bool)
        answer[boolean]["value"] = 1.0
        self.assertFalse(self.grade(root, spec, evidence, answer)["passed"])

    def test_empty_directory_addition_is_a_workspace_change(self):
        root, spec, evidence = self.fixture()
        (root / "unrequested-empty").mkdir()
        self.assertFalse(self.grade(root, spec, evidence)["workspace_preserved"])

    def test_embedded_layout_labels_do_not_claim_distinct_schema_coverage(self):
        _, json_case, _ = self.fixture("embedded_task", layout="json-v1")
        _, csv_case, _ = self.fixture("embedded_task", layout="csv-v2")
        self.assertEqual(json_case, csv_case)
        self.assertEqual(json_case["oracle"]["layout"], "embedded-only")

    def test_allowed_conversion_requires_strings_and_preserves_missingness(self):
        root, spec, evidence = self.fixture("allowed_conversion", layout="csv-v2")
        answer = spec["oracle"]["answer"]
        values = list(answer.values())
        self.assertTrue(values[1].startswith("-"))
        self.assertEqual(values[2], "0.00")
        self.assertIsNone(values[3])
        self.assertTrue(self.grade(root, spec, evidence)["passed"])
        key = next(iter(answer))
        wrong = {**answer, key: float(answer[key])}
        self.assertFalse(self.grade(root, spec, evidence, wrong)["passed"])

    def test_evidence_is_required_without_prescribing_tools_or_actors(self):
        root, spec, evidence = self.fixture()
        self.assertTrue(self.grade(root, spec, evidence)["passed"])
        for entries in (
            [],
            evidence[:-1],
            [{**e, "complete": False} for e in evidence],
            [{**e, "sha256": "fabricated"} for e in evidence],
        ):
            self.assertFalse(self.grade(root, spec, entries)["passed"])
        for tool, actor in (
            ("read_document", "worker"),
            ("table_query", "parent"),
            ("read_file", "single-agent"),
        ):
            entries = [{**e, "tool": tool, "actor": actor} for e in evidence]
            result = self.grade(root, spec, entries)
            self.assertTrue(result["passed"])
            self.assertFalse(result["local_check_execution_verified"])

    def test_no_source_task_requires_neither_evidence_nor_tools(self):
        root, spec, evidence = self.fixture("embedded_task", explicit=True)
        self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(evidence, [])
        self.assertTrue(self.grade(root, spec, [])["passed"])
        answer = spec["oracle"]["answer"]
        wrong = {**answer, "labels": list(reversed(answer["labels"]))}
        self.assertFalse(self.grade(root, spec, [], wrong)["passed"])

    def test_mutation_unexpected_output_or_noncompletion_cannot_pass(self):
        root, spec, evidence = self.fixture()
        self.assertFalse(
            assess_outcome(
                "needs_program",
                spec["oracle"]["answer"],
                root,
                spec["oracle"],
                observed_sources=evidence,
            )["passed"]
        )
        wrong = {**spec["oracle"]["answer"], "extra": "unrequested"}
        self.assertFalse(self.grade(root, spec, evidence, wrong)["passed"])
        (root / "unrequested.txt").write_text("side effect")
        self.assertFalse(self.grade(root, spec, evidence)["workspace_preserved"])
        (root / "unrequested.txt").unlink()
        (root / "records-0.json").write_text("[]")
        self.assertFalse(self.grade(root, spec, evidence)["workspace_preserved"])

    def test_seed_varies_values_but_layout_is_an_independent_dimension(self):
        for family in FAMILIES:
            _, first, _ = self.fixture(family, seed=71)
            _, second, _ = self.fixture(family, seed=72)
            self.assertNotEqual(first["oracle"]["answer"], second["oracle"]["answer"])
        with self.assertRaises(ValueError):
            make_outcome_fixture("source_resolution", self.root, 1, layout="unknown")


if __name__ == "__main__":
    unittest.main()
