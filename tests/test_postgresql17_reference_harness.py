from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import postgresql17_reference_harness as harness


class HarnessTests(unittest.TestCase):
    def test_real_proof_requires_exact_A_B_C_P_reference_set(self) -> None:
        with self.assertRaises(harness.HarnessRedError) as error:
            harness.run_real_references(references=("A", "B", "C"))
        self.assertEqual(str(error.exception), "reference_set_must_be_A_B_C_P")

    def test_migration_manifest_is_exact_and_ordered(self) -> None:
        self.assertEqual(harness.MIGRATION_NAMES, ("001_platform_scoped_storage.sql", "003_object_artifact_metadata.sql", "004_generation_forensics_feedback_retention_postgres.sql", "005_forensic_postgres_delete_guards.sql", "006_quote_publication_versions_postgres.sql", "007_feedback_publication_binding_postgres.sql"))

    def test_immutable_image_authority_is_digest_bound(self) -> None:
        with self.assertRaises(harness.HarnessRedError):
            harness.immutable_image_digest("postgres:17")


if __name__ == "__main__":
    unittest.main()
