# SPDX-License-Identifier: Apache-2.0
"""Exact legacy retention equivalence and deterministic aggregate-work bounds."""

import json
import math
import random
import unittest
from functools import partial
from unittest.mock import patch

from flora.engine.runtime import Runtime, RuntimeConfig
from flora.integrations.binding import make_registry
from flora.support import resources
from flora.support.errors import ValidationError
from flora.support.resources import ResourceLimitExceeded, encoded_size, retain_newest
from flora.support.values import MAX_DEPTH, MAX_ENCODED_BYTES, MAX_NODES


def _linear_retain_newest(records, *, max_bytes, max_records=None, resource="optional records"):
    """Frozen pre-optimization implementation used as a differential oracle."""
    resources.validate_limit(max_bytes, "max_bytes")
    if max_records is not None and (type(max_records) is not int or max_records < 0):
        raise ValidationError("max_records must be a nonnegative integer")
    kept = []
    total = 2
    for record in reversed(records):
        if max_records is not None and len(kept) >= max_records:
            break
        try:
            size = resources.encoded_size(record, limit=max_bytes, resource=resource)
        except (ResourceLimitExceeded, ValidationError):
            continue
        added = size + bool(kept)
        if total + added > max_bytes:
            break
        kept.append(record)
        total += added
    kept.reverse()
    while kept:
        try:
            resources.encoded_size(kept, limit=max_bytes, resource=resource)
            break
        except (ResourceLimitExceeded, ValidationError):
            del kept[0]
    return kept, {
        "dropped_records": len(records) - len(kept),
        "retained_records": len(kept),
        "retained_bytes": resources.encoded_size(kept, limit=max_bytes, resource=resource),
    }


def _nested_record(depth):
    value = 0
    for _ in range(depth - 1):
        value = [value]
    return {"nested": value}


class RetentionTests(unittest.TestCase):
    def assert_legacy_equal(self, records, **limits):
        before = [id(record) for record in records]
        expected, expected_stats = _linear_retain_newest(records, **limits)
        actual, actual_stats = retain_newest(records, **limits)
        self.assertEqual([id(record) for record in actual], [id(record) for record in expected])
        self.assertEqual(actual_stats, expected_stats)
        self.assertEqual([id(record) for record in records], before)
        self.assertEqual(
            actual_stats["retained_bytes"],
            len(
                json.dumps(
                    actual, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ),
        )
        return actual, actual_stats

    def test_empty_zero_record_cap_and_invalid_limits_match_legacy(self):
        for records, max_records in (([], None), ([{"a": 1}], 0)):
            actual, stats = self.assert_legacy_equal(records, max_bytes=2, max_records=max_records)
            self.assertEqual(actual, [])
            self.assertEqual(stats["retained_bytes"], 2)
        for limits in (
            {"max_bytes": 1},
            {"max_bytes": True},
            {"max_bytes": MAX_ENCODED_BYTES + 1},
            {"max_bytes": 100, "max_records": -1},
            {"max_bytes": 100, "max_records": True},
            {"max_bytes": 100, "max_records": 1.0},
        ):
            with self.subTest(limits=limits):
                for retain in (_linear_retain_newest, retain_newest):
                    with self.assertRaises(ValidationError):
                        retain([], **limits)

    def test_individually_invalid_and_oversized_records_are_skipped(self):
        cycle = []
        cycle.append(cycle)

        class DictSubclass(dict):
            pass

        invalid = [
            {"payload": "x" * 500},
            {"payload": cycle},
            {"payload": float("nan")},
            {"payload": float("inf")},
            {"payload": 1 << 4096},
            {"payload": "\ud800"},
            {"\ud800": 1},
            {"payload": (1, 2)},
            {1: "invalid key"},
            DictSubclass(payload=1),
            _nested_record(MAX_DEPTH + 1),
        ]
        old, newest = {"id": "old"}, {"id": "new"}
        actual, _ = self.assert_legacy_equal([old, *invalid, newest], max_bytes=100)
        self.assertEqual(actual, [old, newest])

    def test_capacity_exhaustion_stops_instead_of_skipping_to_smaller_older_records(self):
        records = [{"id": 0}, {"padding": "x" * 40}, {"id": 2}]
        self.assertLessEqual(encoded_size(records[1]), 60)
        actual, _ = self.assert_legacy_equal(records, max_bytes=60)
        self.assertEqual(actual, [records[-1]])

    def test_exact_unicode_escaping_and_record_count_boundaries(self):
        records = [
            {"id": 0, "text": "中文✨"},
            {"id": 1, "text": '"\\\n\t\0'},
            {"id": 2, "text": "é"},
        ]
        for max_bytes in range(2, encoded_size(records) + 2):
            for max_records in (None, 0, 1, 2, 3, 4):
                with self.subTest(max_bytes=max_bytes, max_records=max_records):
                    self.assert_legacy_equal(records, max_bytes=max_bytes, max_records=max_records)

    def test_wrapper_depth_failure_removes_the_same_complete_prefix(self):
        deep = _nested_record(MAX_DEPTH)
        encoded_size(deep)
        with self.assertRaises(ValidationError):
            encoded_size([deep])
        for newest in (0, 1, 2, 7):
            records = [{"old": i} for i in range(5)] + [deep]
            records += [{"new": i} for i in range(newest)]
            actual, _ = self.assert_legacy_equal(records, max_bytes=10_000)
            self.assertEqual(actual, records[len(records) - newest :] if newest else [])

    def test_production_node_limit_and_shared_dag_accounting_match_legacy(self):
        shared = list(range(10_000))
        records = [{"id": i, "payload": shared} for i in range(25)]
        actual, stats = self.assert_legacy_equal(records, max_bytes=MAX_ENCODED_BYTES)
        # Each record contributes 10,005 nodes; shared references count each time.
        self.assertEqual(len(actual), (MAX_NODES - 1) // 10_005)
        self.assertEqual(stats["dropped_records"], 6)
        oversized = {"payload": [0] * MAX_NODES}
        actual, _ = self.assert_legacy_equal(
            [{"old": True}, oversized, {"new": True}], max_bytes=MAX_ENCODED_BYTES
        )
        self.assertEqual(actual, [{"old": True}, {"new": True}])

    def test_randomized_differential_bytes_nodes_depth_and_invalid_records(self):
        rng = random.Random(0xF10A)
        shared = {"items": [1, "中文", {"escaped": "\0"}]}
        cycle = []
        cycle.append(cycle)
        payloads = [
            None,
            True,
            -12,
            1.25,
            "",
            "中文✨",
            "\0" * 20,
            "x" * 500,
            [0] * 75,
            shared,
            [shared, shared],
            cycle,
            float("nan"),
            "\ud800",
            (1, 2),
            {1: "bad"},
            1 << 4096,
        ]
        for case in range(750):
            records = [
                (
                    _nested_record(rng.randrange(1, 12))
                    if rng.randrange(6) == 0
                    else {
                        "id": i,
                        "verdict": rng.choice(["PASS", "FAIL", "UNKNOWN"]),
                        "payload": rng.choice(payloads),
                    }
                )
                for i in range(rng.randrange(65))
            ]
            limits = {
                "max_bytes": rng.choice([2, 4, 30, 60, 150, 400, 1000, 8000]),
                "max_records": rng.choice([None, 0, 1, 2, 7, 30, 100]),
            }
            max_nodes = rng.choice([4, 16, 40, 100, 300, 1000])
            max_depth = rng.choice([1, 2, 3, 5, 8, MAX_DEPTH])
            limited = partial(encoded_size, max_nodes=max_nodes, max_depth=max_depth)
            with (
                self.subTest(case=case, **limits, nodes=max_nodes, depth=max_depth),
                patch.object(resources, "encoded_size", side_effect=limited),
            ):
                self.assert_legacy_equal(records, **limits)

    def test_aggregate_validity_is_monotone_over_individually_valid_suffixes(self):
        rng = random.Random(401)
        limited = partial(encoded_size, max_nodes=200, max_depth=5)
        for case in range(100):
            records = [
                _nested_record(rng.randrange(1, 6))
                if rng.randrange(4) == 0
                else {"items": [0] * rng.randrange(80)}
                for _ in range(rng.randrange(1, 30))
            ]
            for record in records:
                limited(record, limit=10_000)
            validity = []
            for start in range(len(records) + 1):
                try:
                    limited(records[start:], limit=10_000)
                except (ResourceLimitExceeded, ValidationError):
                    validity.append(False)
                else:
                    validity.append(True)
            with self.subTest(case=case):
                self.assertEqual(validity, sorted(validity))
                self.assertTrue(validity[-1])
                with patch.object(resources, "encoded_size", side_effect=limited):
                    self.assert_legacy_equal(records, max_bytes=10_000)

    def test_verdicts_are_unchanged_and_report_eviction_never_evicts_real_journal(self):
        runtime = Runtime(make_registry([]), config=RuntimeConfig(max_reports=3))
        for i in range(4):
            event_id = runtime.trace.begin(
                "tick",
                {"i": i},
                expected_epoch=runtime.trace.epoch,
                expected_digest=runtime.trace.digest,
            )
            outcome = {"status": "returned", "value": i}
            if i == 3:
                outcome = {
                    "status": "interrupted_unknown",
                    "error": {"type": "Interrupted", "message": "Unknown actual outcome"},
                }
            runtime.trace.settle(event_id, outcome)
        before = runtime.trace.export()
        for verdict in ["PASS", "FAIL", "UNKNOWN"] * 4:
            runtime._report("contract_witness", verdict=verdict)
        self.assertEqual(
            [report["verdict"] for report in runtime.reports], ["PASS", "FAIL", "UNKNOWN"]
        )
        self.assertEqual(runtime.retention["reports_dropped"], 9)
        self.assertEqual(runtime.trace.export(), before)
        self.assertEqual(len(runtime.trace.journal()), 8)


class RetentionWorkBoundTests(unittest.TestCase):
    def test_valid_aggregate_is_encoded_once_and_its_size_is_reused(self):
        records = [{"id": i, "payload": "中文"} for i in range(20)]
        with patch.object(resources, "encoded_size", wraps=encoded_size) as measure:
            actual, stats = retain_newest(records, max_bytes=10_000, max_records=9)
        aggregates = [call for call in measure.call_args_list if type(call.args[0]) is list]
        self.assertEqual(len(aggregates), 1)
        self.assertEqual(measure.call_count, 10)
        self.assertEqual(actual, records[-9:])
        self.assertEqual(stats["retained_bytes"], encoded_size(actual))

    def test_node_limit_search_uses_logarithmically_many_aggregate_validations(self):
        records = [{"id": i, "payload": [0] * 32} for i in range(1024)]
        limited = partial(encoded_size, max_nodes=4000)
        with patch.object(resources, "encoded_size", side_effect=limited) as measure:
            actual, stats = retain_newest(records, max_bytes=MAX_ENCODED_BYTES)
        aggregate_calls = [call for call in measure.call_args_list if type(call.args[0]) is list]
        self.assertGreater(stats["dropped_records"], 900)
        self.assertLessEqual(len(aggregate_calls), 1 + math.ceil(math.log2(len(records))))
        self.assertEqual(measure.call_count, len(records) + len(aggregate_calls))
        self.assertEqual(actual, records[-((4000 - 1) // 37) :])
        self.assertEqual(stats["retained_bytes"], limited(actual))

    def test_invalid_newest_wrapper_depth_reaches_empty_with_logarithmic_work(self):
        records = [{"id": i} for i in range(1023)] + [_nested_record(MAX_DEPTH)]
        with patch.object(resources, "encoded_size", wraps=encoded_size) as measure:
            actual, stats = retain_newest(records, max_bytes=MAX_ENCODED_BYTES)
        aggregate_calls = [call for call in measure.call_args_list if type(call.args[0]) is list]
        self.assertEqual(actual, [])
        self.assertEqual(stats["retained_bytes"], 2)
        self.assertLessEqual(len(aggregate_calls), 1 + math.ceil(math.log2(len(records))))


if __name__ == "__main__":
    unittest.main()
