"""The exact Run-34 test selection projected into the closed fixture."""

from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path
from typing import Any

import scripts.validate_runtime_privilege_contract as contract_validator


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PROVIDER_CONTROL_ROW = {
    "role": "sqag_runtime",
    "member": "neondb_owner",
    "grantor": "cloud_admin",
    "admin_option": True,
    "inherit_option": False,
    "set_option": False,
}


class RuntimeMembershipEdgeEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = copy.deepcopy(contract_validator.HISTORICAL_MANIFEST)

    def _errors(
        self,
        rows: list[dict[str, Any]],
        manifest: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        return contract_validator.validate_runtime_membership_edges(
            manifest or self.manifest,
            rows,
        )

    def _assert_rejected(
        self,
        rows: list[dict[str, Any]],
        expected_fragment: str,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        errors = self._errors(rows, manifest)
        self.assertTrue(
            any(expected_fragment in error for error in errors),
            f"missing {expected_fragment!r} in {errors!r}",
        )

    @staticmethod
    def _membership_row(
        role: str,
        member: str,
        *,
        grantor: str = "unrelated_grantor",
        admin_option: bool = False,
        inherit_option: bool = False,
        set_option: bool = False,
    ) -> dict[str, Any]:
        return {
            "role": role,
            "member": member,
            "grantor": grantor,
            "admin_option": admin_option,
            "inherit_option": inherit_option,
            "set_option": set_option,
        }

    def test_unrelated_parent_to_sqag_migrator_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_migrator")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_unrelated_parent_to_sqag_app_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_app")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_unrelated_parent_to_neon_superuser_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "neon_superuser")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_sqag_migrator_to_unrelated_member_is_rejected(self) -> None:
        row = self._membership_row("sqag_migrator", "unrelated_member")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_sqag_app_to_unrelated_member_is_rejected(self) -> None:
        row = self._membership_row("sqag_app", "unrelated_member")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_neon_superuser_to_unrelated_member_is_rejected(self) -> None:
        row = self._membership_row("neon_superuser", "unrelated_member")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_protected_role_used_as_grantor_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "unrelated_member", grantor="sqag_migrator")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_grantor_forbidden")

    def test_inherit_true_on_unrelated_parent_protected_member_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_migrator", inherit_option=True)
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_inherit_option_forbidden")

    def test_set_true_on_unrelated_parent_protected_member_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_app", set_option=True)
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_set_option_forbidden")

    def test_admin_true_on_unauthorised_protected_role_row_is_rejected(self) -> None:
        row = self._membership_row("neon_superuser", "unrelated_member", admin_option=True)
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_admin_option_forbidden")

    def test_multiple_protected_role_rows_alongside_exact_edge_are_rejected(self) -> None:
        rows = [
            PRODUCTION_PROVIDER_CONTROL_ROW,
            self._membership_row("unrelated_parent", "sqag_migrator"),
            self._membership_row("sqag_app", "unrelated_member"),
        ]
        self._assert_rejected(rows, "protected_role_row_count_invalid")

    def test_recursive_protected_role_path_not_beginning_with_runtime_is_rejected(self) -> None:
        rows = [
            PRODUCTION_PROVIDER_CONTROL_ROW,
            self._membership_row("sqag_migrator", "unrelated_bridge"),
            self._membership_row("unrelated_bridge", "sqag_migrator"),
        ]
        self._assert_rejected(rows, "recursive_protected_role_membership_path")

    def test_duplicate_unrelated_membership_rows_are_rejected(self) -> None:
        unrelated = self._membership_row("unrelated_parent", "unrelated_member")
        self._assert_rejected(
            [PRODUCTION_PROVIDER_CONTROL_ROW, unrelated, copy.deepcopy(unrelated)],
            "duplicate_role_membership_row",
        )

    def test_unknown_participant_connected_to_protected_role_is_rejected(self) -> None:
        row = self._membership_row("unknown_parent", "neondb_owner")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "unknown_protected_edge_participant")

    def test_truly_unrelated_membership_row_is_outside_contract(self) -> None:
        unrelated = self._membership_row("unrelated_parent", "unrelated_member")
        self.assertEqual(self._errors([PRODUCTION_PROVIDER_CONTROL_ROW, unrelated]), ())


class RequirementEvidenceMapTest(unittest.TestCase):
    def test_membership_query_narrative_has_exact_six_field_unfiltered_contract(self) -> None:
        documentation = (ROOT / "docs" / "runtime-privilege-contract.md").read_text(encoding="utf-8")
        section_match = re.search(
            r"### Membership-query narrative contract\s+(.*?)(?=\n### |\n## |\Z)",
            documentation,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(section_match, "membership-query narrative section is missing")
        paragraph = " ".join(section_match.group(1).split())
        required_phrases = (
            "exact aliases `role`, `member`, `grantor`, `admin_option`, `inherit_option`, and `set_option`",
            "complete unfiltered membership result",
            "validates the `grantor`",
            "distinguishes ADMIN authority from INHERIT and SET authority",
            "No column may be omitted",
            "no value may be supplied by a substituted default",
            "no unexpected row may be filtered away",
        )
        for phrase in required_phrases:
            self.assertIn(phrase, paragraph)

        mutations = {
            "grantor_omitted": paragraph.replace("`grantor`, ", "", 1),
            "inherit_omitted": paragraph.replace("`inherit_option`, ", "", 1),
            "set_omitted": paragraph.replace(", and `set_option`", "", 1),
            "only_three_fields": re.sub(
                r"exact aliases `role`.*?`set_option`",
                "exact aliases `role`, `member`, and `admin_option`",
                paragraph,
                count=1,
            ),
            "incorrect_alias": paragraph.replace("`grantor`", "`grantor_name`", 1),
            "filtering_permitted": paragraph.replace(
                "no unexpected row may be filtered away",
                "unexpected rows may be filtered away",
                1,
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                self.assertTrue(
                    any(phrase not in mutation for phrase in required_phrases),
                    f"narrative mutation {label} was not detected",
                )
