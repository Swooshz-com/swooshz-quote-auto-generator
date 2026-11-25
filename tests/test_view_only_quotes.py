"""A quote made by another staff member opens view only for an admin, and nobody can save over it or into a copy.

Synthetic data only; no paid AI. The browser run uses database storage, as live: quotes have owners.
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_webapp as fixtures
from tests.rendered_browser_support import owned_browser_evidence_directory

webapp = fixtures.webapp
QUOTE = "quote-view-only-source"


def patch(**extra):
    return {"quote_session": {
        "session_id": QUOTE, "customer_summary": {"project_name": "Synthetic Project"},
        "draft_state": {"activeSidePanel": "basis", "workflowStage": "basis_review"}, **extra,
    }}


class ViewOnlyQuoteStorageTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        url = "sqlite:///" + (Path(folder.name) / "quotes.sqlite3").as_posix()
        webapp.apply_sqag_storage_migrations(url)
        modes = mock.patch.dict(os.environ, {"SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": "database", "SQAG_DATABASE_URL": url})
        modes.start()
        self.addCleanup(modes.stop)
        self.owner = self.storage(url, "synthetic-owner", "operator")
        self.admin = self.storage(url, "synthetic-admin", "admin")
        self.member = self.storage(url, "synthetic-member", "operator")
        self.owner.create_or_update_quote_session(patch())
        self.saved = self.record()

    @staticmethod
    def storage(url, user_id, role):
        return webapp.DatabaseSqagStorage(url, "synthetic-workspace", user_id=user_id, role=role)

    def record(self):
        return self.owner._read_quote_session_metadata_for_workspace(QUOTE)[0]

    def quotes(self):
        with self.owner.connection() as connection:
            return sorted(row["session_id"] for row in connection.execute("select session_id from sqag_quote_sessions").fetchall())

    def assert_refused(self, call):
        with self.assertRaises(webapp.SqagStorageAccessError) as refused:
            call()
        self.assertEqual((refused.exception.status, refused.exception.reason), (403, "quote_session_not_editable"))
        self.assertEqual(self.quotes(), [QUOTE], "a refused save left another quote")
        self.assertEqual(self.record(), self.saved, "a refused save changed the quote")

    def test_only_an_admin_who_did_not_make_it_gets_it_view_only(self):
        self.assertIs(self.admin.get_quote_session(QUOTE, include_draft_state=True)["view_only"], True)
        self.assertNotIn("view_only", self.owner.get_quote_session(QUOTE, include_draft_state=True))
        # Staff see no more than before.
        self.assertIsNone(self.member.get_quote_session(QUOTE, include_draft_state=True))
        self.assertEqual(self.member.list_quote_sessions(), [])

    def test_a_save_never_makes_a_copy_whatever_it_carries(self):
        version = self.owner.get_quote_session(QUOTE, include_draft_state=True).get("draft_version") or "0" * 64
        for user in (self.admin, self.member):
            for name, payload in {
                "no version": patch(), "top level id": {"session_id": QUOTE, "quote_session": {"customer_summary": {}}},
                "a version": {**patch(), "expected_draft_version": version}, "as new": {**patch(), "expected_draft_version": ""},
            }.items():
                with self.subTest(user=user.role, payload=name):
                    self.assert_refused(lambda: webapp.save_quote_session_from_browser(copy.deepcopy(payload), user)
                                        if name != "top level id" else user.create_or_update_quote_session(copy.deepcopy(payload)))
            with self.subTest(user=user.role, payload="resolved id"):
                self.assert_refused(lambda: user.create_or_update_quote_session({"quote_session": {}}, session_id=QUOTE))
            with self.subTest(user=user.role, payload="selection"):
                self.assert_refused(lambda: webapp.apply_pricing_selections(patch(), [
                    {"source_basis_line_id": "row", "catalog_item_id": "item", "expected_revision": 0}], user))

    def test_a_quote_without_an_owner_is_read_only_for_everyone(self):
        with self.owner.connection() as connection:
            metadata = {key: value for key, value in self.saved.items() if key != "owner"}
            connection.execute("update sqag_quote_sessions set metadata_json = ? where session_id = ?", (json.dumps(metadata), QUOTE))
            connection.commit()
        self.saved = self.record()
        self.assertIs(self.admin.get_quote_session(QUOTE)["view_only"], True)
        self.assert_refused(lambda: self.admin.create_or_update_quote_session(patch()))

    def test_own_saves_and_new_quotes_are_unchanged(self):
        self.owner.create_or_update_quote_session(patch(customer_summary={"project_name": "Synthetic Renamed"}))
        self.assertEqual(self.record()["customer_summary"]["project_name"], "Synthetic Renamed")
        made = self.admin.create_or_update_quote_session({"quote_session": {"session_id": "quote-admin-own", "customer_summary": {}}})
        self.assertEqual(made["session_id"], "quote-admin-own")
        self.assertEqual(self.quotes(), ["quote-admin-own", QUOTE])


class ViewOnlyQuoteBrowserTest(unittest.TestCase):
    def test_rendered_admin_reads_another_users_quote_and_writes_nothing(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-view-only-quotes.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=300,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual((evidence['outputDownload'], evidence['dashboardDownload']), ('quotation.xlsx', 'quotation.xlsx'))
        self.assertEqual({evidence['refused'][name] for name in ('withoutVersion', 'withVersion', 'asNew')}, {403})
        self.assertEqual(evidence['refused']['generate without a version'], 'blocked')
        self.assertEqual(evidence['refused']['generate with a version'], 'blocked')


if __name__ == '__main__':
    unittest.main()
