# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import random
import unittest

from flora.support.errors import ValidationError
from flora.support.resources import ResourceLimitExceeded, encoded_size
from flora.support.values import bounded_clone, canonical_json, clone, digest


class SerializationTests(unittest.TestCase):
    def test_canonical_bytes_and_hashes_match_original_streaming_encoder(self):
        rng = random.Random(5411)
        scalar = [
            None,
            True,
            False,
            0,
            -1,
            10**100,
            1.0,
            -0.0,
            1e-100,
            1e100,
            "",
            "中文✨",
            '"\\\n\t\0',
            "x" * 3000,
        ]
        encoder = json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        values = scalar + [{"b": scalar, "a": {"escaped": scalar[-2]}}]
        for _ in range(200):
            values.append(
                {
                    f"key{i}": [rng.choice(scalar) for _ in range(rng.randrange(12))]
                    for i in range(rng.randrange(8))
                }
            )
        for value in values:
            expected = "".join(encoder.iterencode(value))
            self.assertEqual(canonical_json(value), expected)
            self.assertEqual(encoded_size(value), len(expected.encode("utf-8")))
            self.assertEqual(digest(value), hashlib.sha256(expected.encode()).hexdigest())
            self.assertEqual(clone(value), json.loads(expected))

    def test_exact_byte_limits_unicode_escaping_and_boundaries(self):
        for value in ["中文", "\0" * 1000, {"a": [1, 1.0, True]}, ["x" * 1000, "y" * 1000]]:
            size = len(canonical_json(value).encode())
            self.assertEqual(encoded_size(value, limit=size), size)
            with self.assertRaises(ResourceLimitExceeded):
                encoded_size(value, limit=size - 1)
            self.assertEqual(bounded_clone(value, size), value)
            with self.assertRaises(ValidationError):
                bounded_clone(value, size - 1)

    def test_cycles_nan_subclasses_nodes_and_depth_remain_rejected(self):
        cycle = []
        cycle.append(cycle)

        class String(str):
            pass

        for bad in [cycle, float("nan"), float("inf"), String("x"), {1: "invalid"}, "\ud800"]:
            with self.subTest(kind=type(bad).__name__), self.assertRaises(ValidationError):
                canonical_json(bad)
        with self.assertRaises(ValidationError):
            encoded_size([1, 2, 3], max_nodes=3)
        with self.assertRaises(ValidationError):
            encoded_size([[[1]]], max_depth=2)

    def test_shared_dag_is_cloned_without_aliases(self):
        child = {"v": [1, 2]}
        result = clone([child, child])
        result[0]["v"].append(3)
        self.assertEqual(result[1], {"v": [1, 2]})
