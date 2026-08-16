from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.postgresql17_finality_engine import (  # noqa: E402
    ExecutionTrace,
    FieldDescriptor,
    FinalitySnapshot,
    Observation,
    PolicyRegistry,
)
from scripts.postgresql17_reference_harness import (  # noqa: E402
    DockerCLI,
    ReferenceReceipt,
    compare_real_references,
)
from scripts.validate_postgresql17_finality import validate_static_contract  # noqa: E402


def make_snapshot(reference_id: str) -> FinalitySnapshot:
    descriptor = FieldDescriptor("pg_catalog", "pg_class", "r", "relname", "name")
    registry = PolicyRegistry((descriptor,))
    observation = Observation.from_value(
        reference_id=reference_id,
        descriptor=descriptor,
        policy=registry.policy_for(descriptor.key),
        row_identity="pg_catalog.pg_class:relation:public:sqag_profiles:r",
        object_kind="catalogue:r",
        value="sqag_profiles",
    )
    return FinalitySnapshot.build(
        reference_id=reference_id,
        observations=(observation,),
        registry=registry,
        trace=ExecutionTrace.live(registry.field_keys, 1),
    )


def make_receipt(reference_id: str, cluster_id: str, *, maintenance: bool = False) -> ReferenceReceipt:
    return ReferenceReceipt(
        reference_id=reference_id,
        collector_mode="live_postgresql17",
        synthetic=False,
        real_connection=True,
        executed_fields_count=1,
        field_values_count=1,
        reference_independence_verified=True,
        cleanup_verified=True,
        postgres_major=17,
        cluster_system_identity=cluster_id,
        container_identity=f"container-{reference_id}",
        volume_identity=f"volume-{reference_id}",
        database_name="sqag_a23_reference",
        migration_manifest_digest="manifest",
        migration_checksums=(("001.sql", "checksum"),),
        exact_replay_verified=True,
        maintenance_executed=maintenance,
        maintenance_variance_witness_count=1 if maintenance else 0,
        semantic_residue_free=True,
    )


class DockerBoundaryTests(unittest.TestCase):
    def test_missing_docker_inspect_array_is_not_treated_as_existing(self):
        docker = DockerCLI()
        with patch.object(docker, "run", return_value="[]"):
            self.assertFalse(docker.exists("container", "missing"))
        with patch.object(docker, "run", return_value="{}"):
            self.assertTrue(docker.exists("container", "present"))


class ReferenceReceiptGateTests(unittest.TestCase):
    def test_static_contract_is_explicitly_synthetic_and_not_live_proof(self):
        report = validate_static_contract()
        self.assertTrue(report["synthetic"])
        self.assertFalse(report["real_connection"])
        self.assertEqual(report["collector_mode"], "synthetic_contract_only")

    def test_four_independent_live_receipts_converge(self):
        pairs = [
            (make_receipt(label, f"cluster-{label}", maintenance=label == "P"), make_snapshot(label))
            for label in ("A", "B", "C", "P")
        ]
        report = compare_real_references(pairs, require_maintenance=True)
        self.assertTrue(report["converged"])
        self.assertEqual(report["reference_ids"], ["A", "B", "C", "P"])
        self.assertTrue(report["maintenance_verified"])

    def test_same_physical_cluster_is_rejected(self):
        pairs = [
            (make_receipt(label, "same-cluster", maintenance=label == "P"), make_snapshot(label))
            for label in ("A", "B", "C", "P")
        ]
        with self.assertRaisesRegex(RuntimeError, "same_physical_reference_reused"):
            compare_real_references(pairs, require_maintenance=True)

    def test_missing_p_maintenance_witness_is_rejected(self):
        pairs = [
            (make_receipt(label, f"cluster-{label}"), make_snapshot(label))
            for label in ("A", "B", "C", "P")
        ]
        with self.assertRaisesRegex(RuntimeError, "maintenance_operation_not_executed"):
            compare_real_references(pairs, require_maintenance=True)

    def test_public_receipt_has_no_host_or_port(self):
        payload = make_receipt("A", "cluster-a").public_dict()
        self.assertNotIn("host", payload)
        self.assertNotIn("port", payload)
        self.assertFalse(payload["synthetic"])


if __name__ == "__main__":
    unittest.main()
