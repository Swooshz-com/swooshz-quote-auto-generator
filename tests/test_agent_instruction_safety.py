"""Focused tests for AGENTS.md instruction-safety and CI documentation rules.

Proves the post-merge remediation corrections from issue #158 are present in the
repository state:

1. Strong explicit-approval wording exists.
2. Automatic metadata-write exception wording is absent.
3. ``sensitive operational details`` exists in the managed-memory rule.
4. The weaker phrase ``sensitive operations`` is absent from that rule.
5. Current CI documentation states PostgreSQL 17.
6. Current CI documentation does not claim PostgreSQL 16.
7. PostgreSQL-major assertion step is documented.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = ROOT / "AGENTS.md"
CICD_STATUS_MD = ROOT / "docs" / "current-cicd-status.md"


class TestAgentInstructionApprovalRule(unittest.TestCase):
    def test_explicit_approval_wording_exists(self):
        """Strong explicit-approval wording is present."""
        text = AGENTS_MD.read_text(encoding="utf-8")
        self.assertIn("Every issue or PR metadata mutation requires explicit "
                       "current-turn approval", text)

    def test_automatic_metadata_write_exception_absent(self):
        """The automatic scoped external-write exception wording is absent."""
        text = AGENTS_MD.read_text(encoding="utf-8")
        self.assertNotIn("scoped external-write exception", text)


class TestAgentManagedMemoryRule(unittest.TestCase):
    def test_sensitive_operational_details_exists(self):
        """sensitive operational details phrase exists in managed-memory rule."""
        text = AGENTS_MD.read_text(encoding="utf-8")
        self.assertIn("sensitive operational details", text)

    def test_weaker_sensitive_operations_absent(self):
        """Weaker 'sensitive operations' phrase absent from managed-memory line."""
        lines = AGENTS_MD.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if "sensitive operational details" in line:
                self.assertNotIn("sensitive operations", line)
                break
        else:
            self.fail("managed-memory line with 'sensitive operational details' not found")


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
