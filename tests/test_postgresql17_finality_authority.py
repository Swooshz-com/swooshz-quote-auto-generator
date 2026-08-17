from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import postgresql17_finality_authority as authority


def descriptor(relation: str, attnum: int, *, dropped: bool = False, namespace: str = "pg_catalog") -> dict[str, object]:
    return {
        "relation_semantic_id": relation,
        "namespace": namespace,
        "relation_name": relation.rsplit(".", 1)[-1],
        "relation_kind": "r",
        "catalogue_scope": "local",
        "shared": False,
        "attnum": attnum,
        "attname": None if dropped else ("__relation__" if attnum == 0 else "field"),
        "attname_state": "dropped" if dropped else ("relation" if attnum == 0 else "non_dropped"),
        "attisdropped": dropped,
        "type_identity": {"namespace": "pg_catalog", "name": "int4", "kind": "b"},
        "typmod": -1,
        "collation_identity": None,
        "length": 4,
        "alignment": "i",
        "storage": "p",
        "compression": "",
        "by_value": True,
        "generated": "",
        "identity": "",
        "applicability": "DROPPED" if dropped else ("RELATION" if attnum == 0 else ("SYSTEM" if attnum < 0 else "VALUE")),
        "toast_attachment": None,
        "descriptor_kind": "relation" if attnum == 0 else "attribute",
    }


class AuthorityTests(unittest.TestCase):
    def test_descriptor_digest_is_order_independent_but_coverage_order_is_locked(self) -> None:
        items = [descriptor("pg_catalog.pg_class", 0), descriptor("pg_catalog.pg_class", -1), descriptor("pg_catalog.pg_class", 1, dropped=True)]
        self.assertEqual(authority.descriptor_digest(items), authority.descriptor_digest(reversed(items)))
        coverage = {
            "$schema": authority.COVERAGE_SCHEMA,
            "package_version": authority.PACKAGE_VERSION,
            "proof_fixture_authority": {},
            "supported_compatibility_authority": {"postgres_major": 17},
            "discovery_scope": {"namespaces": ["pg_catalog", "pg_toast"], "include_dropped_attributes": True, "include_negative_attributes": True, "include_toast_attachment_metadata": True, "semantic_inference_during_discovery": False},
            "descriptors": sorted(items, key=authority.descriptor_key),
            "descriptor_count": len(items),
            "descriptor_digest": authority.descriptor_digest(items),
        }
        self.assertEqual(len(authority.validate_coverage(coverage)), 3)

    def test_selector_rejects_unreviewed_selector_fields(self) -> None:
        with self.assertRaises(authority.AuthorityError) as error:
            authority._selector_matches(descriptor("pg_catalog.pg_class", 1), {"oid": 1})
        self.assertEqual(error.exception.code, "generation_selector_unknown_key")

    def test_closed_world_rejects_missing_and_unexpected_descriptors(self) -> None:
        items = [descriptor("pg_catalog.pg_class", 0), descriptor("pg_catalog.pg_class", -1)]
        coverage = {"descriptors": sorted(items, key=authority.descriptor_key)}
        policy = {"safety_bindings": {authority.descriptor_key(item): {} for item in items}, "semantic_bindings": {authority.descriptor_key(item): {} for item in items}}
        authority.validate_closed_world(items, coverage, policy, [authority.descriptor_key(item) for item in items])
        with self.assertRaises(authority.AuthorityError):
            authority.validate_closed_world(items + [descriptor("pg_catalog.pg_class", 2)], coverage, policy)

    def test_canonical_json_rejects_nan(self) -> None:
        with self.assertRaises(ValueError):
            authority.canonical_json({"bad": float("nan")})


if __name__ == "__main__":
    unittest.main()
