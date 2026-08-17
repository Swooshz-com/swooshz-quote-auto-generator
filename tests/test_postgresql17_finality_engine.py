from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import postgresql17_finality_engine as engine
import postgresql17_finality_authority as authority


def row(*, namespace: str = "pg_catalog", relation: str = "pg_class", attnum: int | None = 1, attname: str | None = "relname", dropped: bool = False, parent_namespace: str | None = None, parent_relation: str | None = None) -> tuple[object, ...]:
    values: list[object] = [namespace, relation, "r", False, False, "p", "d", False, False, "postgres", "heap", False, None, None, None, parent_namespace, parent_relation, "r" if parent_relation else None, attnum, attname, dropped, "pg_catalog", "int4", "b", -1, 4, "i", "p", "", True, "", "", None, None]
    return tuple(values)


class FakeCursor:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def fetchall(self) -> list[object]:
        return self.rows


class DiscoveryConnection:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def execute(self, sql: str, params: object = ()) -> FakeCursor:
        if sql.lower().startswith("show server_version_num"):
            return FakeCursor([("170004",)])
        return FakeCursor(self.rows)


class EngineTests(unittest.TestCase):
    def test_discovery_keeps_negative_and_dropped_attributes_and_deduplicates_relation(self) -> None:
        rows = [row(attnum=-1, attname="tableoid"), row(attnum=1, attname=None, dropped=True), row(attnum=2, attname="relname"), row(namespace="pg_toast", relation="pg_toast_1", attnum=1, attname="chunk_id", parent_namespace="pg_catalog", parent_relation="pg_class")]
        descriptors = engine.discover_descriptors(DiscoveryConnection(rows))
        keys = [authority.descriptor_key(item) for item in descriptors]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(any(item["attnum"] < 0 for item in descriptors))
        self.assertTrue(any(item["attisdropped"] for item in descriptors))
        self.assertTrue(any(item["namespace"] == "pg_toast" and item["relation_semantic_id"].startswith("toast:") for item in descriptors))

    def test_positional_and_collection_comparators_do_not_normalize_away_meaning(self) -> None:
        with self.assertRaises(engine.FinalityRedError):
            engine.compare_paired_vectors([[1, 2], [3, 4]], [[1, 3], [2, 4]])
        with self.assertRaises(engine.FinalityRedError):
            engine.canonicalize_collection(["x", "x"], "unordered_set")
        self.assertEqual(engine.compare_observations(1.0, 2.0, "dynamic_scalar", dynamic_authorized=True)["receipt_type"], "PERMITTED_DYNAMIC_VARIANCE")
        with self.assertRaises(engine.FinalityRedError):
            engine.compare_observations(1.0, 2.0, "dynamic_scalar")

    def test_serializer_is_typed_and_public_receipt_is_safe(self) -> None:
        self.assertEqual(engine.safe_json_value(b"abc", "bytea_digest")["length"], 3)
        with self.assertRaises(engine.FinalityRedError):
            engine.safe_json_value(b"abc")
        self.assertIn('"status":"PASS"', engine.public_safe_receipt({"status": "PASS", "canary_status": "absent"}))
        with self.assertRaises(engine.FinalityRedError):
            engine.public_safe_receipt({"status": "PASS", "password": "nope"})

    def test_projection_guard_rejects_wildcard_and_raw_large_object_data(self) -> None:
        with self.assertRaises(engine.FinalityRedError):
            engine._validate_projection_sql("SELECT * FROM pg_catalog.pg_class", "bad")
        with self.assertRaises(engine.FinalityRedError):
            engine._validate_projection_sql("SELECT pg_largeobject.data FROM pg_catalog.pg_largeobject", "bad")

class ResidualExactTests(unittest.TestCase):
    def test_residual_exact_requires_locked_raw_allowed_after_compilation(self) -> None:
        key = "pg_catalog.pg_class|1"
        descriptors = [{"relation_semantic_id": "pg_catalog.pg_class", "attnum": 1}]
        policy = {
            "safety_bindings": {key: {"mode": "RAW_ALLOWED"}},
            "safe_projection_plans": {"residual": {"relation_semantic_id": "pg_catalog.pg_class", "mode": "SAFE_SQL", "sql": "SELECT 1 AS exact_value", "semantic_policy": "residual_exact", "value_fields": [key]}},
        }
        self.assertIn("residual", engine.compile_safe_projection(policy, descriptors))
        self.assertEqual(engine.compare_observations("x", "x", "residual_exact", raw_allowed=True)["receipt_type"], "SEMANTIC_EQUAL")
        with self.assertRaises(engine.FinalityRedError):
            engine.compare_observations("x", "x", "residual_exact")


if __name__ == "__main__":
    unittest.main()
