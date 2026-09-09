"""Focused CI documentation contract tests.

These tests intentionally avoid pinning Toolkit/AGENTS.md governance prose. Agent
policy evolves in the Toolkit and should be validated by Toolkit's own contract
coverage, not by literal downstream wording assertions in SQAG.

This module keeps only repository-specific CI documentation invariants that are
material to SQAG runtime validation.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CICD_STATUS_MD = ROOT / "docs" / "current-cicd-status.md"


class TestCICDPostgreSQLDocumentation(unittest.TestCase):
    def test_ci_docs_state_postgresql_17(self):
        """Current CI documentation states PostgreSQL 17."""
        text = CICD_STATUS_MD.read_text(encoding="utf-8")
        self.assertIn("PostgreSQL 17", text)

    def test_ci_docs_no_postgresql_16_claim(self):
        """Current CI documentation does not claim PostgreSQL 16 for the active
        disposable service, without rewriting historical dated records."""
        text = CICD_STATUS_MD.read_text(encoding="utf-8")
        self.assertNotIn("PostgreSQL 16", text)

    def test_postgresql_major_assertion_step_documented(self):
        """CI documentation describes the PostgreSQL major version assertion."""
        text = CICD_STATUS_MD.read_text(encoding="utf-8")
        self.assertIn("asserts the running PostgreSQL", text)
        self.assertIn("assert_postgres17.py", text)


if __name__ == "__main__":
    unittest.main()
