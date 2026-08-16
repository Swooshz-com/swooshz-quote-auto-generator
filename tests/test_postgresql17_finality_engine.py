from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.postgresql17_finality_engine import (  # noqa: E402
    ExecutionTrace,
    FieldDescriptor,
    FieldPolicy,
    FinalityError,
    FinalitySnapshot,
    Observation,
    PolicyRegistry,
    SemanticIdentityResolver,
    _row_identity_seed,
    classify_field_policy,
    compare_snapshots,
)


def make_descriptors() -> tuple[FieldDescriptor, ...]:
    return (
        FieldDescriptor("pg_catalog", "pg_class", "r", "relname", "name"),
        FieldDescriptor("pg_catalog", "pg_class", "r", "relnamespace", "oid"),
        FieldDescriptor("pg_catalog", "pg_class", "r", "relpages", "integer"),
        FieldDescriptor("pg_catalog", "pg_authid", "r", "rolname", "name"),
    )


def make_snapshot(
    reference_id: str,
    *,
    relname: str = "sqag_profiles",
    relnamespace_edge: str = "pg_catalog.pg_namespace:schema:public",
    relpages: int = 1,
    rolname: str = "postgres",
    boundary_digest: str = "toast-acl-a",
) -> FinalitySnapshot:
    descriptors = make_descriptors()
    registry = PolicyRegistry(descriptors)
    values = {
        descriptors[0].key: relname,
        descriptors[1].key: 123,
        descriptors[2].key: relpages,
        descriptors[3].key: rolname,
    }
    observations = []
    for descriptor in descriptors:
        policy = registry.policy_for(descriptor.key)
        edge = relnamespace_edge if descriptor.field_name == "relnamespace" else None
        value_digest = "stable-edge" if edge else None
        observations.append(
            Observation.from_value(
                reference_id=reference_id,
                descriptor=descriptor,
                policy=policy,
                row_identity="pg_catalog.pg_class:relation:public:sqag_profiles:r",
                object_kind="catalogue:r",
                value=values[descriptor.key],
                value_digest=value_digest,
                normalized_edge=edge,
            )
        )
    return FinalitySnapshot.build(
        reference_id=reference_id,
        observations=observations,
        registry=registry,
        trace=ExecutionTrace.synthetic(registry.field_keys, len(observations)),
        boundary_receipts=(
            {
                "kind": "toast_schema_acl_boundary",
                "identity": "schema:pg_toast",
                "digest": boundary_digest,
            },
        ),
    )


class PostgreSQL17PolicyClassificationTests(unittest.TestCase):
    def test_dynamic_policy_is_generic_for_maintenance_estimates(self):
        for field_name in ("relpages", "reltuples", "relallvisible", "stadistinct", "stanullfrac"):
            descriptor = FieldDescriptor("pg_catalog", "pg_class", "r", field_name, "integer")
            self.assertEqual(
                classify_field_policy(descriptor).policy_class,
                "postgresql_maintained_dynamic_estimate_maintenance_state",
                field_name,
            )

    def test_raw_oid_is_not_part_of_row_identity_seed(self):
        seed = _row_identity_seed(
            "pg_catalog",
            "pg_class",
            {"relname": "sqag_profiles", "relkind": "r", "relnamespace": 12345},
        )
        self.assertNotIn("12345", seed)

    def test_classoid_and_objoid_resolve_using_generic_context(self):
        resolver = SemanticIdentityResolver(
            {1259: "pg_class"},
            {("pg_class", 123): "pg_catalog.pg_class:relation:public:sqag_profiles:r"},
        )
        self.assertEqual(resolver.resolve(field_name="classoid", value=1259), "pg_class")
        self.assertEqual(
            resolver.resolve(field_name="objoid", value=123, context={"classoid": 1259}),
            "pg_catalog.pg_class:relation:public:sqag_profiles:r",
        )
        with self.assertRaisesRegex(FinalityError, "unresolved_nonzero_oid"):
            resolver.resolve(field_name="objoid", value=999, context={"classoid": 1259})


class PostgreSQL17SnapshotGateTests(unittest.TestCase):
    def test_identical_snapshots_converge_and_register_dynamic_variance(self):
        left = make_snapshot("A", relpages=1)
        right = make_snapshot("B", relpages=2)
        receipt = compare_snapshots(left, right)
        self.assertTrue(receipt["converged"])
        self.assertTrue(receipt["dynamic_variance_receipts"])

    def test_exact_semantic_drift_is_red(self):
        with self.assertRaisesRegex(FinalityError, "semantic_value_drift"):
            compare_snapshots(make_snapshot("A"), make_snapshot("B", relname="changed"))

    def test_authority_drift_is_red(self):
        with self.assertRaisesRegex(FinalityError, "authority_drift"):
            compare_snapshots(make_snapshot("A"), make_snapshot("B", rolname="other"))

    def test_relationship_drift_is_red(self):
        with self.assertRaisesRegex(FinalityError, "relationship_dependency_drift"):
            compare_snapshots(
                make_snapshot("A"),
                make_snapshot("B", relnamespace_edge="pg_catalog.pg_namespace:schema:other"),
            )

    def test_presence_drift_is_red(self):
        descriptor = FieldDescriptor("pg_catalog", "pg_class", "r", "relname", "name")
        registry = PolicyRegistry((descriptor,))

        def build(value):
            observation = Observation.from_value(
                reference_id="ref",
                descriptor=descriptor,
                policy=registry.policy_for(descriptor.key),
                row_identity="row",
                object_kind="catalogue:r",
                value=value,
            )
            return FinalitySnapshot.build(
                reference_id="ref",
                observations=(observation,),
                registry=registry,
                trace=ExecutionTrace.synthetic(registry.field_keys, 1),
            )

        with self.assertRaisesRegex(FinalityError, "coverage_universe_drift"):
            compare_snapshots(build(None), build("present"))

    def test_authority_boundary_drift_is_red(self):
        with self.assertRaisesRegex(FinalityError, "authority_digest_drift"):
            compare_snapshots(make_snapshot("A"), make_snapshot("B", boundary_digest="toast-acl-b"))

    def test_dynamic_variance_can_be_disallowed(self):
        with self.assertRaisesRegex(FinalityError, "dynamic_variance_not_allowed"):
            compare_snapshots(make_snapshot("A"), make_snapshot("B", relpages=2), allow_dynamic_variance=False)

    def test_duplicate_observation_and_unexecutable_policy_fail_closed(self):
        descriptor = FieldDescriptor("pg_catalog", "pg_class", "r", "relname", "name")
        registry = PolicyRegistry((descriptor,))
        observation = Observation.from_value(
            reference_id="ref",
            descriptor=descriptor,
            policy=registry.policy_for(descriptor.key),
            row_identity="row",
            object_kind="catalogue:r",
            value="x",
        )
        with self.assertRaisesRegex(FinalityError, "duplicate_observation"):
            FinalitySnapshot.build(
                reference_id="ref",
                observations=(observation, observation),
                registry=registry,
                trace=ExecutionTrace.synthetic(registry.field_keys, 2),
            )
        with self.assertRaisesRegex(FinalityError, "unexecutable_policy"):
            FieldPolicy(descriptor.key, "a23.exact_semantic_value.v1", "exact_semantic_value", executable=False)


if __name__ == "__main__":
    unittest.main()
