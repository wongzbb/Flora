"""External coverage oracle adversaries use actual workspace/document tool values."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from flora.coding.navigation import CodingFiles
from flora.general.documents import DocumentTools, DocumentWorkspace
from flora.general.storage import ObservationStore
from tests.live_reliability_probe import (
    assess,
    complete_versions,
    observed_paths,
    prepare_case,
    publication_checks,
    sufficient_paths,
)
from tests.test_stress_evaluation import receipt, worker


class EvidenceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"
        self.root.mkdir()
        self.files = CodingFiles(self.root)
        (Path(self.tmp.name) / "sources").mkdir()
        self.store = ObservationStore(Path(self.tmp.name) / "sources")
        self.docs = DocumentTools(DocumentWorkspace(self.root), self.store, lambda: "test")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def call(self, tool, **args):
        owner = (
            self.store
            if tool == "read_source"
            else self.docs
            if tool in {"read_document", "table_query"}
            else self.files
        )
        return {
            "tool": tool,
            "args": args,
            "status": "returned",
            "value": getattr(owner, tool)(**args),
        }

    def result(self, value, receipts):
        return {"status": "completed", "value": value, "_receipts": receipts}

    def test_partial_full_hash_unrelated_tail_and_search_are_access_not_complete_evidence(self):
        expected = prepare_case("read", self.root, 24)
        answer = {"project": expected["project"], "total": expected["total"]}
        size = (self.root / "evidence.json").stat().st_size
        for r in (
            self.call("read_file", path="evidence.json", max_bytes=1),
            self.call("read_file", path="evidence.json", offset=size - 1),
            self.call("search_files", path=".", query="project"),
        ):
            with self.subTest(tool=r["tool"], args=r["args"]):
                self.assertIn("evidence.json", observed_paths([r]))
                self.assertEqual(sufficient_paths([r], expected), set())
                self.assertFalse(
                    assess("read", self.result(answer, [r]), self.root, [], expected)["passed"]
                )
        full = self.call("read_file", path="evidence.json")
        self.assertTrue(
            assess("read", self.result(answer, [full]), self.root, [], expected)["passed"]
        )

    def test_byte_windows_require_full_consistent_utf8_content_of_one_revision(self):
        raw = "首行\nsecond\n末尾\n".encode()
        (self.root / "a.txt").write_bytes(raw)
        pieces, offset = [], 0
        while True:
            piece = self.call("read_file", path="a.txt", max_bytes=5, offset=offset)
            pieces.append(piece)
            offset = piece["value"]["next_offset"]
            if offset is None:
                break
        sha = hashlib.sha256(raw).hexdigest()
        self.assertEqual(complete_versions(pieces), {"a.txt": {sha}})
        self.assertEqual(complete_versions(pieces[1:]), {})
        changed = copy.deepcopy(pieces)
        changed[0]["value"]["content"] = "xxxxx"
        self.assertEqual(complete_versions(changed), {})
        # Each genuine receipt is valid, but versions must never be stitched.
        (self.root / "a.txt").write_bytes(b"XXX" + raw[3:])
        newer = self.call("read_file", path="a.txt", offset=pieces[0]["value"]["next_offset"])
        self.assertEqual(complete_versions([pieces[0], newer]), {})

    def test_line_windows_cover_actual_lines_and_cannot_mix_revisions_or_forge_full_hash(self):
        (self.root / "a.txt").write_text("alpha\nβeta\nend\n")
        first = self.call("read_lines", path="a.txt", start_line=1, end_line=1)
        last = self.call("read_lines", path="a.txt", start_line=2, end_line=3)
        sha = first["value"]["sha256"]
        self.assertEqual(complete_versions([first]), {})
        self.assertEqual(complete_versions([first, last]), {"a.txt": {sha}})
        (self.root / "a.txt").write_text("ALPHA\nβeta\nend\n")
        new = self.call("read_lines", path="a.txt", start_line=2, end_line=3)
        self.assertEqual(complete_versions([first, new]), {})
        last["value"]["content"] = "wrong\nend\n"
        self.assertEqual(complete_versions([first, last]), {})

    def test_document_source_requires_all_pages_bound_to_same_raw_version(self):
        (self.root / "long.txt").write_text("a" * 7000)
        first = self.call("read_document", path="long.txt")
        self.assertEqual(complete_versions([first]), {})
        tail = self.call("read_source", source_id=first["value"]["source_id"], offset=6000)
        sha = first["value"]["sha256"]
        self.assertEqual(complete_versions([first, tail]), {"long.txt": {sha}})
        (self.root / "long.txt").write_text("b" * 7000)
        new = self.call("read_document", path="long.txt")
        new_tail = self.call("read_source", source_id=new["value"]["source_id"], offset=6000)
        self.assertEqual(complete_versions([first, new_tail]), {})

    def test_full_unfiltered_table_pages_are_content_but_arbitrary_query_is_not(self):
        (self.root / "rows.csv").write_text("name,amount\na,2\nb,3\nc,4\n")
        first = self.call("table_query", path="rows.csv", sheet="Sheet1", limit=1)
        tail = self.call("table_query", path="rows.csv", sheet="Sheet1", offset=1)
        sha = first["value"]["input_sha256"]
        self.assertEqual(complete_versions([first]), {})
        self.assertEqual(complete_versions([first, tail]), {"rows.csv": {sha}})
        filtered = self.call(
            "table_query", path="rows.csv", filters=[{"column": "name", "op": "eq", "value": "a"}]
        )
        self.assertEqual(complete_versions([filtered]), {})
        total = self.call(
            "table_query", path="rows.csv", metrics=[{"op": "sum", "column": "amount"}]
        )
        self.assertEqual(complete_versions([total]), {})

    def test_oversize_table_rows_require_complete_real_source_and_reject_aggregate_source(self):
        (self.root / "big.csv").write_text("name,amount\n" + "x" * 61000 + ",3\n")
        query = self.call("table_query", path="big.csv")
        self.assertTrue(query["value"]["rows_in_source"])
        pages, offset = [query], 0
        while True:
            page = self.call("read_source", source_id=query["value"]["source_id"], offset=offset)
            pages.append(page)
            offset = page["value"]["next_offset"]
            if offset is None:
                break
        self.assertEqual(complete_versions(pages[:-1]), {})
        self.assertEqual(complete_versions(pages), {"big.csv": {query["value"]["input_sha256"]}})
        aggregate = self.call(
            "table_query", path="big.csv", metrics=[{"op": "sum", "column": "amount"}]
        )
        aggregate_source = self.call("read_source", source_id=aggregate["value"]["source_id"])
        self.assertEqual(complete_versions([aggregate, aggregate_source]), {})

    def test_table_task_requires_its_actual_exact_sum_not_unrelated_aggregate(self):
        expected = prepare_case("table", self.root, 8)
        value = expected["total"]
        sum_receipt = self.call(
            "table_query",
            path="sales.csv",
            metrics=[{"op": "sum", "column": "amount", "as": "total"}],
        )
        self.assertTrue(
            assess("table", self.result(value, [sum_receipt]), self.root, [], expected)["passed"]
        )
        count = self.call("table_query", path="sales.csv", metrics=[{"op": "count", "as": "count"}])
        self.assertFalse(
            assess("table", self.result(value, [count]), self.root, [], expected)["passed"]
        )

    def test_parent_and_worker_each_require_complete_independent_source_receipts(self):
        expected = prepare_case("multi", self.root, 9)
        workers = [
            worker(self.root, name, name + ".json", {"count": count, "path": name + ".json"})
            for name, count in zip(("left", "right"), expected["counts"], strict=True)
        ]
        result = self.result(
            expected["total"], [self.call("read_file", path=p + ".json") for p in ("left", "right")]
        )
        result["workers"] = workers
        self.assertTrue(assess("multi", result, self.root, [], expected)["passed"])
        for parent in (True, False):
            trial = copy.deepcopy(result)
            target = trial["_receipts"] if parent else trial["workers"][0]["_receipts"]
            target[0] = self.call("read_file", path="left.json", max_bytes=1)
            self.assertFalse(assess("multi", trial, self.root, [], expected)["passed"])

    def edit_result(self):
        expected = prepare_case("edit_preserve", self.root, 27)
        reads = [self.call("read_file", path=p) for p in ("settings.json", "request.json")]
        write = self.call(
            "write_file",
            path="settings.json",
            content=json.dumps(expected["files"]["settings.json"]),
            expected_sha256=reads[0]["value"]["sha256"],
        )
        readback = self.call("read_file", path="settings.json")
        return expected, self.result(expected["answer"], [*reads, write, readback])

    def test_readback_needs_all_final_content_after_last_successful_write(self):
        expected, result = self.edit_result()
        self.assertTrue(assess("edit_preserve", result, self.root, [], expected)["passed"])
        old_read = result["_receipts"][0]
        for replacement in (old_read, self.call("read_file", path="settings.json", max_bytes=1)):
            trial = copy.deepcopy(result)
            trial["_receipts"][-1] = replacement
            grade = assess("edit_preserve", trial, self.root, [], expected)
            self.assertFalse(grade["passed"])
            self.assertFalse(grade["publication_checks"]["final_version_readback"])
        trial = copy.deepcopy(result)
        trial["_receipts"][2]["value"]["sha256"] = old_read["value"]["sha256"]
        self.assertFalse(assess("edit_preserve", trial, self.root, [], expected)["passed"])
        result["_receipts"].insert(0, result["_receipts"].pop())
        self.assertFalse(assess("edit_preserve", result, self.root, [], expected)["passed"])

    def test_edit_requires_original_before_mutation_and_counts_legitimate_multiple_edits(self):
        expected, result = self.edit_result()
        trial = copy.deepcopy(result)
        trial["_receipts"].insert(2, trial["_receipts"].pop(0))
        self.assertFalse(assess("edit_preserve", trial, self.root, [], expected)["passed"])
        sha = result["_receipts"][-1]["value"]["sha256"]
        # A second intentional edit/republication is observable, not globally forbidden.
        again = self.call(
            "write_file",
            path="settings.json",
            content=json.dumps(expected["files"]["settings.json"]),
            expected_sha256=sha,
        )
        result["_receipts"].extend([again, self.call("read_file", path="settings.json")])
        grade = assess("edit_preserve", result, self.root, [], expected)
        self.assertTrue(grade["passed"])
        self.assertEqual(
            grade["publication_checks"]["successful_writes_by_path"], {"settings.json": 2}
        )
        # Only an explicit external task constraint activates the once-only check.
        expected["single_publication_paths"] = ["settings.json"]
        grade = assess("edit_preserve", result, self.root, [], expected)
        self.assertFalse(grade["passed"])
        self.assertFalse(grade["publication_checks"]["single_publication_checks"]["settings.json"])

    def test_present_primary_rejects_no_match_search_covering_fallback(self):
        expected = prepare_case("branch", self.root, 1)
        self.assertTrue(expected["optional_present"])
        primary = self.call("read_file", path="optional.json")
        result = self.result(expected["branch_project"], [primary])
        self.assertTrue(assess("branch", result, self.root, [], expected)["passed"])
        search = self.call("search_files", path=".", query="NO_MATCH_9823643")
        self.assertEqual(search["value"]["matches"], [])
        result["_receipts"].append(search)
        self.assertFalse(assess("branch", result, self.root, [], expected)["passed"])
        result["_receipts"][-1] = self.call(
            "search_files", path=".", query="NO_MATCH_9823643", glob="optional.json"
        )
        self.assertTrue(assess("branch", result, self.root, [], expected)["passed"])

    def test_missing_primary_cannot_be_retroactively_established_after_fallback_access(self):
        expected = prepare_case("branch", self.root, 2)
        self.assertFalse(expected["optional_present"])
        source = self.call("read_file", path="evidence.json")
        missing = receipt(self.root, path="optional.json", status="raised")
        correct = self.result(expected["project"], [missing, source])
        self.assertTrue(assess("branch", correct, self.root, [], expected)["passed"])
        for early in (
            source,
            self.call("read_file", path="evidence.json", max_bytes=1),
            self.call("search_files", path=".", query="no-match"),
        ):
            trial = self.result(expected["project"], [early, missing, source])
            self.assertFalse(assess("branch", trial, self.root, [], expected)["passed"])

    def test_source_observation_after_publication_cannot_retroactively_justify_write(self):
        expected = prepare_case("write", self.root, 25)
        value = {"project": expected["project"], "total": expected["total"]}
        published = self.call(
            "write_file", path="summary.json", content=json.dumps(value), create=True
        )
        source = self.call("read_file", path="evidence.json")
        result = self.result("summary.json", [published, source])
        self.assertFalse(assess("write", result, self.root, [], expected)["passed"])
        result["_receipts"] = [source, published]
        self.assertTrue(assess("write", result, self.root, [], expected)["passed"])

    def test_release_parent_sources_must_be_complete_before_publication(self):
        expected = prepare_case("release_audit", self.root, 31)
        workers = [
            worker(self.root, str(i), answer["source"], answer)
            for i, answer in enumerate(expected["worker_answers"])
        ]
        workers[1]["_receipts"].insert(
            0, receipt(self.root, path=expected["missing_primary"], status="raised")
        )
        reads = [self.call("read_file", path=p) for p in expected["required_reads"]]
        write = self.call(
            "write_file", path="audit.json", content=json.dumps(expected["answer"]), create=True
        )
        readback = self.call("read_file", path="audit.json")
        result = self.result(expected["answer"], [*reads, write, readback])
        result["workers"] = workers
        self.assertTrue(assess("release_audit", result, self.root, [], expected)["passed"])
        result["_receipts"] = [write, *reads, readback]
        self.assertFalse(assess("release_audit", result, self.root, [], expected)["passed"])
        result["_receipts"] = [*reads, write, readback]
        expected["single_publication_paths"] = ["audit.json"]
        self.assertTrue(assess("release_audit", result, self.root, [], expected)["passed"])
        duplicate = self.call(
            "write_file",
            path="audit.json",
            content=json.dumps(expected["answer"]),
            expected_sha256=write["value"]["sha256"],
        )
        result["_receipts"].extend([duplicate, self.call("read_file", path="audit.json")])
        grade = assess("release_audit", result, self.root, [], expected)
        self.assertTrue(grade["publication_checks"]["final_version_readback"])
        self.assertFalse(grade["publication_checks"]["single_publication_checks"]["audit.json"])
        self.assertFalse(grade["passed"])

    def test_no_publication_or_failed_attempt_does_not_count_as_success(self):
        (self.root / "a.txt").write_text("content")
        failed = {"tool": "write_file", "args": {"path": "a.txt"}, "status": "raised"}
        report = publication_checks([failed], self.root, {"single_publication_paths": ["a.txt"]})
        self.assertFalse(report["passed"])
        self.assertEqual(report["successful_writes_by_path"], {})


if __name__ == "__main__":
    unittest.main()
