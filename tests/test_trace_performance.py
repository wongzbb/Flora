# SPDX-License-Identifier: Apache-2.0
"""Deterministic cache-work bounds and durable journal integrity regressions."""

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.engine.budget import Budget
from flora.engine.effects import EffectExecutor
from flora.integrations.binding import make_registry
from flora.state import trace as trace_module
from flora.state.trace import GENESIS, MemoryTrace, SQLiteTrace
from flora.support.errors import (
    InterruptedEffect,
    StaleAnchor,
    TraceIntegrityError,
    ValidationError,
)
from flora.support.resources import ResourceLimitExceeded
from flora.support.values import canonical_json, digest


def append_effect(trace, value=None):
    event_id = trace.begin(
        "tick", {"nested": [1]}, expected_epoch=trace.epoch, expected_digest=trace.digest
    )
    trace.settle(event_id, {"status": "returned", "value": value})
    return event_id


class TraceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trace.sqlite"

    def sqlite(self):
        trace = SQLiteTrace(self.path)
        self.addCleanup(trace.close)
        return trace

    def test_unchanged_reads_neither_rehash_nor_rescan_the_prefix(self):
        for trace in (MemoryTrace(), self.sqlite()):
            with self.subTest(store=type(trace).__name__):
                for i in range(20):
                    append_effect(trace, i)
                statements = []
                if isinstance(trace, SQLiteTrace):
                    trace._db.set_trace_callback(statements.append)
                with (
                    patch.object(trace_module, "_reduce", wraps=trace_module._reduce) as reduce,
                    patch.object(trace_module, "digest", wraps=trace_module.digest) as hash_value,
                ):
                    for _ in range(20):
                        self.assertEqual(trace.epoch, 20)
                        self.assertNotEqual(trace.digest, GENESIS)
                        self.assertEqual(len(trace.records), 20)
                        self.assertEqual(len(trace.journal()), 40)
                    self.assertEqual(reduce.call_count, 0)
                    self.assertEqual(hash_value.call_count, 0)
                self.assertFalse(any("FROM journal" in sql for sql in statements))

    def test_local_appends_hash_only_new_entries(self):
        for trace in (MemoryTrace(), self.sqlite()):
            with (
                self.subTest(store=type(trace).__name__),
                patch.object(trace_module, "digest", wraps=trace_module.digest) as hash_value,
            ):
                for i in range(30):
                    append_effect(trace, i)
                # Construction + verification of each begin and settlement.
                self.assertEqual(hash_value.call_count, 4 * 30)
                self.assertEqual(trace.records, trace_module._reduce(trace.journal())[0])

    def test_public_values_never_alias_verified_state(self):
        for trace in (MemoryTrace(), self.sqlite()):
            with self.subTest(store=type(trace).__name__):
                args = {"nested": [{"v": 1}]}
                event_id = trace.begin("tick", args, expected_epoch=0, expected_digest=GENESIS)
                args["nested"][0]["v"] = 2
                outcome = {"status": "returned", "value": {"nested": [3]}}
                receipt = trace.settle(event_id, outcome)
                outcome["value"]["nested"].append(4)
                receipt["args"]["nested"].clear()
                receipt["value"]["nested"].clear()
                records = trace.records
                records[0]["value"]["nested"].append(5)
                journal = trace.journal()
                journal[0]["payload"]["args"].clear()
                exported = trace.export()
                restored = MemoryTrace.from_dict(exported)
                exported["journal"][0]["payload"]["args"].clear()
                self.assertEqual(trace.records[0]["args"], {"nested": [{"v": 1}]})
                self.assertEqual(trace.records[0]["value"], {"nested": [3]})
                self.assertEqual(restored.records, trace.records)
                self.assertEqual(restored.digest, trace.digest)

    def test_unknown_resolution_and_invalid_operations_preserve_cached_state(self):
        for trace in (MemoryTrace(), self.sqlite()):
            with self.subTest(store=type(trace).__name__):
                event_id = trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
                trace.settle(
                    event_id,
                    {
                        "status": "interrupted_unknown",
                        "error": {"type": "Lost", "message": "unknown"},
                    },
                )
                before = trace.export()
                with self.assertRaises(InterruptedEffect):
                    trace.begin(
                        "tick", {}, expected_epoch=trace.epoch, expected_digest=trace.digest
                    )
                with self.assertRaises(ValidationError):
                    trace.resolve(event_id, {"status": "returned", "value": 1}, reason=" ")
                self.assertEqual(trace.export(), before)
                trace.resolve(
                    event_id, {"status": "returned", "value": 1}, reason="Verified receipt"
                )
                append_effect(trace, 2)
                self.assertEqual(trace.records, trace_module._reduce(trace.journal())[0])

    def test_cached_prefix_still_enforces_append_and_admission_limits(self):
        for trace in (
            MemoryTrace(max_journal_bytes=1000),
            SQLiteTrace(self.path, max_journal_bytes=1000),
        ):
            try:
                event_id = trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
                before = trace.export()
                with self.assertRaises(ResourceLimitExceeded):
                    trace.settle(event_id, {"status": "returned", "value": "x" * 2000})
                self.assertEqual(trace.export(), before)
                trace.settle(event_id, {"status": "returned", "value": 1})
                before = trace.export()
                with self.assertRaises(ResourceLimitExceeded):
                    trace.reserve_effect("tick", {}, max_output_bytes=1)
                self.assertEqual(trace.export(), before)
            finally:
                trace.close()

    def test_cached_appends_keep_aggregate_node_and_depth_limits(self):
        for trace in (MemoryTrace(), self.sqlite()):
            with self.subTest(store=type(trace).__name__):
                event_id = trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
                before = trace.export()
                for constant, limit, value in (
                    ("TRACE_PART_NODES", 30, 1),
                    ("TRACE_PART_DEPTH", 5, [[[]]]),
                ):
                    with self.subTest(limit=constant), patch.object(trace_module, constant, limit):
                        with self.assertRaises(ValidationError):
                            trace.settle(event_id, {"status": "returned", "value": value})
                    self.assertEqual(trace.export(), before)
                trace.settle(event_id, {"status": "returned", "value": 1})

    def test_imported_invalid_chain_and_hashed_invalid_operation_are_rejected(self):
        trace = MemoryTrace()
        append_effect(trace, 1)
        for mutation in ("hash", "semantics"):
            with self.subTest(mutation=mutation):
                data = trace.export()
                entry = data["journal"][-1]
                if mutation == "hash":
                    entry["payload"]["outcome"]["value"] = 2
                else:
                    entry["payload"]["event_id"] = 4
                    entry["hash"] = digest({k: v for k, v in entry.items() if k != "hash"})
                with self.assertRaises(TraceIntegrityError):
                    MemoryTrace.from_dict(data)

    def test_external_in_place_tampering_invalidates_all_read_apis(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        before = trace.journal()
        with sqlite3.connect(self.path) as other:
            entry = json.loads(other.execute("SELECT entry FROM journal WHERE seq=0").fetchone()[0])
            entry["payload"]["args"]["nested"][0] = 2
            other.execute("UPDATE journal SET entry=? WHERE seq=0", (canonical_json(entry),))
        for read in (
            lambda: trace.epoch,
            lambda: trace.digest,
            lambda: trace.records,
            trace.journal,
        ):
            with self.assertRaises(TraceIntegrityError):
                read()
        # A failed refresh must not publish partially verified entries/receipts.
        self.assertEqual(trace._entries, before)
        with self.assertRaises(TraceIntegrityError):
            SQLiteTrace(self.path)

    def test_external_sequence_gap_is_detected_despite_cached_tail(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        append_effect(trace, 2)
        with sqlite3.connect(self.path) as other:
            other.execute("DELETE FROM journal WHERE seq=1")
        with self.assertRaisesRegex(TraceIntegrityError, "contiguous"):
            _ = trace.digest

    def test_external_oversized_append_is_rejected_before_loading_rows(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        trace.max_journal_bytes = 1000
        self.assertEqual(trace.epoch, 1)
        with sqlite3.connect(self.path) as other:
            other.execute("INSERT INTO journal(seq,entry) VALUES(2,?)", ('"' + "x" * 2000 + '"',))
        with self.assertRaises(ResourceLimitExceeded):
            _ = trace.epoch

    def test_noncanonical_stored_bytes_remain_bounded_after_cached_appends(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        with sqlite3.connect(self.path) as other:
            other.execute("UPDATE journal SET entry=' ' || entry || ? WHERE seq=0", (" " * 500,))
            raw_bytes, rows = other.execute(
                "SELECT SUM(length(CAST(entry AS BLOB))), COUNT(*) FROM journal"
            ).fetchone()
        trace.max_journal_bytes = raw_bytes + rows + 51
        before = trace.export()
        with self.assertRaises(ResourceLimitExceeded):
            trace.begin("tick", {}, expected_epoch=trace.epoch, expected_digest=trace.digest)
        self.assertEqual(trace.export(), before)
        self.assertEqual(self.sqlite().records, trace.records)

    def test_padded_prefix_reserves_fallback_before_dispatch(self):
        trace = SQLiteTrace(self.path, max_journal_bytes=30_000)
        self.addCleanup(trace.close)
        append_effect(trace, 1)
        with sqlite3.connect(self.path) as other:
            rows = other.execute("SELECT seq, entry FROM journal ORDER BY seq").fetchall()
            raw_size = sum(len(encoded.encode("utf-8")) for _, encoded in rows) + len(rows) + 1
            other.execute(
                "UPDATE journal SET entry=entry || ? WHERE seq=0", (" " * (29_650 - raw_size),)
            )
            stored_before = other.execute("SELECT seq, entry FROM journal ORDER BY seq").fetchall()
        before = trace.export()
        # The same canonical history has ample capacity in memory. Only durable
        # padding makes this admission unsafe, and must not change MemoryTrace.
        MemoryTrace.from_dict(before).reserve_effect("tick", {}, max_output_bytes=1000)
        calls = []

        def tick() -> str:
            calls.append("dispatched")
            return "x" * 500

        budget = Budget()
        executor = EffectExecutor(make_registry([tick]), trace, budget, max_output_bytes=1000)
        with self.assertRaises(ResourceLimitExceeded):
            executor.execute(
                {"tool": "tick", "args": {}}, epoch=trace.epoch, trace_digest=trace.digest
            )
        self.assertEqual(calls, [])
        self.assertEqual(budget.tool_calls, 0)
        self.assertEqual(trace.export(), before)
        self.assertEqual(
            trace._db.execute("SELECT seq, entry FROM journal ORDER BY seq").fetchall(),
            stored_before,
        )

    def test_padding_overhead_remains_exact_across_local_appends_and_rollback(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        with sqlite3.connect(self.path) as other:
            other.execute("UPDATE journal SET entry=entry || ? WHERE seq=0", (" " * 500,))
        self.assertEqual(trace.epoch, 1)
        self.assertEqual(trace._admission_overhead(), 500)
        append_effect(trace, {"unicode": "✨"})
        self.assertEqual(trace._admission_overhead(), 500)
        actual_bytes, rows = trace._db.execute(
            "SELECT SUM(length(CAST(entry AS BLOB))), COUNT(*) FROM journal"
        ).fetchone()
        self.assertEqual(trace._stored_journal_bytes, actual_bytes + rows + 1)
        self.assertEqual(
            trace._canonical_journal_bytes, len(canonical_json(trace.journal()).encode())
        )
        real_append = trace._append

        def fail_after_append(*args, **kwargs):
            real_append(*args, **kwargs)
            raise RuntimeError("After cached byte counters changed")

        with patch.object(trace, "_append", side_effect=fail_after_append):
            with self.assertRaises(RuntimeError):
                trace.begin("tick", {}, expected_epoch=trace.epoch, expected_digest=trace.digest)
        self.assertEqual(trace._admission_overhead(), 500)
        self.assertEqual(trace._stored_journal_bytes, actual_bytes + rows + 1)

    def test_two_connections_refresh_and_compare_the_exact_settlement_anchor(self):
        first, second = self.sqlite(), self.sqlite()
        self.assertEqual((second.epoch, second.digest), (0, GENESIS))
        event_id = first.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
        with self.assertRaises(StaleAnchor):
            second.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
        pending_digest = second.digest
        first.settle(event_id, {"status": "returned", "value": 1})
        self.assertEqual(second.records, first.records)
        with self.assertRaises(StaleAnchor):
            second.begin("tick", {}, expected_epoch=1, expected_digest=pending_digest)
        append_effect(second, 2)
        self.assertEqual(first.epoch, 2)
        self.assertEqual(first.digest, second.digest)

    def test_concurrent_begin_allows_only_one_admission(self):
        traces = [self.sqlite(), self.sqlite()]
        barrier = threading.Barrier(2)
        results = []

        def begin(trace):
            barrier.wait()
            try:
                results.append(trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS))
            except StaleAnchor:
                results.append("stale")

        threads = [threading.Thread(target=begin, args=(trace,)) for trace in traces]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, [0, "stale"])
        self.assertEqual(traces[0].records, traces[1].records)

    def test_rollback_after_cache_update_restores_prior_durable_state(self):
        trace = self.sqlite()
        real_append = trace._append

        def fail_after_append(*args, **kwargs):
            real_append(*args, **kwargs)
            raise RuntimeError("Failure after insertion")

        with patch.object(trace, "_append", side_effect=fail_after_append):
            with self.assertRaisesRegex(RuntimeError, "after insertion"):
                trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
        self.assertEqual((trace.epoch, trace.digest, trace.journal()), (0, GENESIS, []))
        event_id = trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
        pending = trace.export()
        with patch.object(trace, "_append", side_effect=fail_after_append):
            with self.assertRaises(RuntimeError):
                trace.settle(event_id, {"status": "returned", "value": 1})
        self.assertEqual(trace.export(), pending)
        trace.settle(event_id, {"status": "returned", "value": 2})
        self.assertEqual(self.sqlite().records, trace.records)

    def test_commit_failure_does_not_leave_cached_uncommitted_admission(self):
        trace = self.sqlite()
        connection = trace._db

        class FailCommit:
            def __getattr__(self, name):
                return getattr(connection, name)

            def execute(self, sql, *args):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("Injected commit failure")
                return connection.execute(sql, *args)

        with patch.object(trace, "_db", FailCommit()):
            # The rollback refresh also commits a read snapshot. Even if that
            # fails too, the invalid cache must remain unusable afterward.
            with self.assertRaises(sqlite3.OperationalError):
                trace.begin("tick", {}, expected_epoch=0, expected_digest=GENESIS)
        self.assertEqual((trace.epoch, trace.digest), (0, GENESIS))
        append_effect(trace, 2)
        self.assertEqual(self.sqlite().records, trace.records)

    def test_snapshot_race_never_marks_old_rows_as_the_new_version(self):
        reader, writer = self.sqlite(), self.sqlite()
        append_effect(writer, 1)
        injected = []

        def during_snapshot(sql):
            if sql == "SELECT seq, entry FROM journal ORDER BY seq" and not injected:
                injected.append(True)
                append_effect(writer, 2)

        reader._db.set_trace_callback(during_snapshot)
        self.assertEqual(reader.epoch, 1)  # snapshot predates the concurrent commit
        self.assertTrue(injected)
        self.assertEqual(reader.epoch, 2)  # next read must observe that commit
        self.assertEqual(reader.records, writer.records)

    def test_checkpoint_saves_do_not_rescan_but_detect_external_journal_changes(self):
        trace, other = self.sqlite(), self.sqlite()
        append_effect(trace, 1)
        statements = []
        trace._db.set_trace_callback(statements.append)
        for i in range(10):
            trace.save_checkpoint({"i": i})
            self.assertEqual(trace.epoch, 1)
        self.assertFalse(any("FROM journal" in sql for sql in statements))
        append_effect(other, 2)
        trace.save_checkpoint({"i": 10})
        self.assertEqual(trace.epoch, 2)

    def test_unexpected_insert_trigger_cannot_hide_corrupted_prefix(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        before = trace.export()
        trace._db.execute(
            "CREATE TRIGGER damage AFTER INSERT ON journal WHEN NEW.seq=2 "
            "BEGIN UPDATE journal SET entry='{}' WHERE seq=0; END"
        )
        with self.assertRaises(TraceIntegrityError):
            trace.begin("tick", {}, expected_epoch=trace.epoch, expected_digest=trace.digest)
        self.assertEqual(trace.export(), before)

    def test_checkpoint_trigger_cannot_bless_corrupted_journal(self):
        trace = self.sqlite()
        append_effect(trace, 1)
        trace._db.execute(
            "CREATE TRIGGER damage_checkpoint AFTER INSERT ON checkpoint "
            "BEGIN UPDATE journal SET entry='{}' WHERE seq=0; END"
        )
        trace.save_checkpoint({"i": 1})
        with self.assertRaises(TraceIntegrityError):
            _ = trace.records


if __name__ == "__main__":
    unittest.main()
