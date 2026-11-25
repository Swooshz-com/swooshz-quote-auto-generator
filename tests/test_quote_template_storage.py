"""Saved quote templates on real SQLite and, in CI, a disposable PostgreSQL 17 database."""
import contextlib
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import uuid

from scripts import quote_template
from tests import test_postgres_migration_ledger as ledger
from webapp import server as s


WORKSPACE = "workspace-synthetic"


def definition(name):
    template = copy.deepcopy(quote_template.load_standard_quote_template())
    template["name"] = name
    return template


def base_env(url):
    return {"APP_MODE": "local", "SQAG_AUTH_MODE": "none", "SQAG_DISABLE_DOTENV": "1",
            "SQAG_STORAGE_MODE": "database", "SQAG_DATABASE_URL": url, "SQAG_ARTIFACT_STORAGE_MODE": "database"}


class QuoteTemplateStoreContract:
    """Behaviour every database backend must show; subclasses provide `storage()`."""

    def store(self, workspace=WORKSPACE, limit=None):
        return s.QuoteTemplateStore(self.storage(workspace), limit)

    def assert_storage_error(self, status, reason, action):
        with self.assertRaises(s.SqagStorageAccessError) as caught:
            action()
        self.assertEqual((caught.exception.status, caught.exception.reason), (status, reason))

    def test_create_list_read_and_standard_is_never_stored(self):
        store = self.store()
        self.assertTrue(store.ready())
        self.assertEqual(store.list(), [])
        saved = store.create(definition("Plain blue"))
        self.assertRegex(saved["id"], r"^tpl-[0-9a-f]{32}$")
        self.assertEqual((saved["name"], saved["save_number"], saved["is_default"]), ("Plain blue", 1, False))
        self.assertEqual(saved["definition"], quote_template.read_quote_template(definition("Plain blue")))
        self.assertEqual(saved["fingerprint"], quote_template.quote_template_fingerprint(saved["definition"]))
        self.assertEqual([record["id"] for record in store.list()], [saved["id"]])
        self.assertEqual(store.get(saved["id"]), saved)
        self.assertIsNone(store.get(s.STANDARD_QUOTE_TEMPLATE_ID))
        self.assertIsNone(store.get("not-a-template-id"))

    def test_invalid_definition_is_refused_and_nothing_is_stored(self):
        store = self.store()
        broken = definition("Broken")
        broken["page"] = "not a page"
        with self.assertRaises(quote_template.QuoteTemplateError):
            store.create(broken)
        self.assertEqual(store.list(), [])

    def test_limit_counts_saved_templates_only_and_frees_a_slot_on_delete(self):
        store = self.store(limit=2)
        first = store.create(definition("One"))
        store.create(definition("Two"))
        self.assert_storage_error(409, "template_limit_reached", lambda: store.create(definition("Three")))
        self.assertEqual(len(store.list()), 2)
        store.delete(first["id"], first["save_number"])
        self.assertEqual(store.create(definition("Three"))["name"], "Three")

    def test_default_limit_is_ten_and_the_setting_is_checked(self):
        with mock.patch.dict(os.environ, {s.QUOTE_TEMPLATE_LIMIT_ENV_NAME: ""}):
            self.assertEqual(self.store().limit, 10)
        with mock.patch.dict(os.environ, {s.QUOTE_TEMPLATE_LIMIT_ENV_NAME: "3"}):
            self.assertEqual(self.store().limit, 3)
        for bad in ("ten", "-1", "101"):
            with self.subTest(bad=bad), mock.patch.dict(os.environ, {s.QUOTE_TEMPLATE_LIMIT_ENV_NAME: bad}):
                store = self.store()
                self.assertEqual(store.list(), [])  # reading never depends on the limit setting
                self.assert_storage_error(503, "quote_template_limit_invalid", lambda: store.create(definition("X")))

    def test_concurrent_creates_at_the_last_free_slot_store_exactly_one(self):
        # Force the race: each create waits, after counting and before comparing with the limit, until
        # the other one has counted too. Without the workspace lock both see a free slot and both save;
        # with it the second create cannot count until the first has finished, so the meeting times out.
        limit = 3
        store = self.store(limit=limit)
        for index in range(limit - 1):
            store.create(definition(f"Kept {index}"))
        meeting = threading.Barrier(2, timeout=2)

        class MeetingStore(s.QuoteTemplateStore):
            @property
            def limit(self):
                with contextlib.suppress(threading.BrokenBarrierError):
                    meeting.wait()
                return s.QuoteTemplateStore.limit.fget(self)

        outcomes = []

        def create(index):
            try:
                MeetingStore(self.storage(), limit).create(definition(f"Racer {index}"))
                outcomes.append("saved")
            except s.SqagStorageAccessError as exc:
                outcomes.append(exc.reason)

        threads = [threading.Thread(target=create, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(outcomes), ["saved", "template_limit_reached"])
        self.assertEqual(len(store.list()), limit)

    def test_stale_save_number_never_overwrites_and_concurrent_updates_keep_one(self):
        store = self.store()
        saved = store.create(definition("Draft"))
        updated = store.update(saved["id"], definition("Draft v2"), 1)
        self.assertEqual((updated["name"], updated["save_number"]), ("Draft v2", 2))
        self.assert_storage_error(409, "template_save_conflict", lambda: store.update(saved["id"], definition("Old tab"), 1))
        self.assertEqual(store.get(saved["id"])["name"], "Draft v2")
        for bad in ("2", None, 2.0, True):
            with self.subTest(save_number=bad), self.assertRaises(ValueError):
                store.update(saved["id"], definition("X"), bad)
        barrier = threading.Barrier(4)
        outcomes = []

        def update(index):
            barrier.wait()
            try:
                self.store().update(saved["id"], definition(f"Editor {index}"), 2)
                outcomes.append("saved")
            except s.SqagStorageAccessError as exc:
                outcomes.append(exc.reason)

        threads = [threading.Thread(target=update, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(outcomes), ["saved"] + ["template_save_conflict"] * 3)
        self.assertEqual(store.get(saved["id"])["save_number"], 3)

    def test_delete_is_permanent_and_checks_the_save_number(self):
        store = self.store()
        saved = store.create(definition("Gone soon"))
        self.assert_storage_error(409, "template_save_conflict", lambda: store.delete(saved["id"], 5))
        store.delete(saved["id"], 1)
        self.assertIsNone(store.get(saved["id"]))
        self.assertEqual(store.list(), [])
        self.assert_storage_error(404, "template_not_found", lambda: store.delete(saved["id"], 1))
        self.assert_storage_error(404, "template_not_found", lambda: store.update(saved["id"], definition("Back"), 1))
        self.assertNotEqual(store.create(definition("Gone soon"))["id"], saved["id"])

    def test_one_default_per_workspace(self):
        store = self.store()
        first, second = store.create(definition("A")), store.create(definition("B"))
        self.assertIsNone(store.default())
        self.assertEqual(store.set_default(first["id"]), first["id"])
        self.assertEqual(store.default()["id"], first["id"])
        store.set_default(second["id"])
        self.assertEqual([record["id"] for record in store.list() if record["is_default"]], [second["id"]])
        store.set_default(s.STANDARD_QUOTE_TEMPLATE_ID)
        self.assertIsNone(store.default())
        self.assert_storage_error(404, "template_not_found", lambda: store.set_default("tpl-" + "0" * 32))
        store.set_default(first["id"])
        store.delete(first["id"], 1)
        self.assertIsNone(store.default())
        with self.storage().connection() as connection:
            connection.execute("update sqag_quote_templates set is_default = 1 where template_id = ?", (second["id"],))
            with self.assertRaises(Exception):
                connection.execute(
                    "insert into sqag_quote_templates (workspace_id, template_id, name, definition_json, fingerprint, "
                    "save_number, is_default, created_at, updated_at) values (?, ?, 'C', '{}', 'f', 1, 1, 'now', 'now')",
                    (WORKSPACE, "tpl-" + "1" * 32),
                )
            connection.rollback()

    def test_standard_template_cannot_be_changed_or_deleted(self):
        store = self.store()
        self.assert_storage_error(403, "template_builtin", lambda: store.update("standard", definition("Mine"), 0))
        self.assert_storage_error(403, "template_builtin", lambda: store.delete("standard", 0))
        self.assertEqual(s.standard_quote_template_record()["definition"], quote_template.load_standard_quote_template())

    def test_workspaces_never_see_or_change_each_others_templates(self):
        mine, theirs = self.store(), self.store("workspace-other")
        saved = mine.create(definition("Private look"))
        self.assertEqual(theirs.list(), [])
        self.assertIsNone(theirs.get(saved["id"]))
        self.assert_storage_error(404, "template_not_found", lambda: theirs.update(saved["id"], definition("Taken"), 1))
        self.assert_storage_error(404, "template_not_found", lambda: theirs.delete(saved["id"], 1))
        self.assert_storage_error(404, "template_not_found", lambda: theirs.set_default(saved["id"]))
        self.assertEqual(mine.get(saved["id"])["name"], "Private look")
        with mock.patch.dict(os.environ, {s.QUOTE_TEMPLATE_LIMIT_ENV_NAME: "1"}):
            self.assertEqual(theirs.create(definition("Their one"))["name"], "Their one")

    def test_names_defaults_and_profile_counts_stay_in_their_workspace(self):
        mine, theirs = self.store(), self.store("workspace-other")
        saved, other = mine.create(definition("Mine")), theirs.create(definition("Theirs"))

        def names(store):
            return [(item["name"], item["is_default"]) for item in store.names()]

        self.assertEqual((names(mine), names(theirs)), ([("Mine", False)], [("Theirs", False)]))
        # Making one template the default neither marks nor clears anything in the other workspace.
        mine.set_default(saved["id"])
        self.assertEqual((names(mine), names(theirs)), ([("Mine", True)], [("Theirs", False)]))
        theirs.set_default(other["id"])
        self.assertEqual((names(mine), names(theirs)), ([("Mine", True)], [("Theirs", True)]))
        theirs.set_default(s.STANDARD_QUOTE_TEMPLATE_ID)
        self.assertEqual((names(mine), names(theirs)), ([("Mine", True)], [("Theirs", False)]))
        # The primary key is workspace and id together, so even the same id in both stays apart.
        with self.storage().connection() as connection:
            connection.execute(
                "insert into sqag_quote_templates (workspace_id, template_id, name, definition_json, fingerprint, "
                "save_number, is_default, created_at, updated_at) select 'workspace-other', template_id, 'Same id', "
                "definition_json, fingerprint, 1, 0, created_at, updated_at from sqag_quote_templates "
                "where workspace_id = ? and template_id = ?", (WORKSPACE, saved["id"]),
            )
            connection.commit()
        before = theirs.list()
        mine.set_default(s.STANDARD_QUOTE_TEMPLATE_ID)
        mine.set_default(saved["id"])
        self.assertEqual(theirs.list(), before)
        mine.update(saved["id"], definition("Mine v2"), 1)
        self.assertEqual((mine.get(saved["id"])["name"], theirs.get(saved["id"])["name"]), ("Mine v2", "Same id"))
        # How many profiles use a template counts this workspace's profiles only.
        self.storage("workspace-other")._upsert_payload("sqag_profiles", "profile_id", "synthetic-theirs", {
            "id": "synthetic-theirs", "quote_template_id": saved["id"]})
        self.assertEqual((mine.usage("standard"), theirs.usage("standard")), ({}, {saved["id"]: 1}))
        mine.delete(saved["id"], 2)
        self.assertEqual((names(mine), [name for name, _ in names(theirs)]), ([], [record["name"] for record in before]))


class SqliteQuoteTemplateStoreTest(QuoteTemplateStoreContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.url = "sqlite:///" + (Path(directory.name) / "templates.sqlite3").as_posix()
        patcher = mock.patch.dict(os.environ, base_env(self.url))
        patcher.start()
        self.addCleanup(patcher.stop)
        s.apply_sqag_storage_migrations(self.url)

    def storage(self, workspace=WORKSPACE):
        return s.DatabaseSqagStorage(self.url, workspace, "admin", "admin-user")

    def test_before_migration_013_templates_are_off_and_quotes_fall_back(self):
        with self.storage().connection() as connection:
            connection.execute("drop table sqag_quote_templates")
            connection.commit()
        store = self.store()
        self.assertFalse(store.ready())
        self.assert_storage_error(503, "quote_templates_not_migrated", store.list)
        self.assert_storage_error(503, "quote_templates_not_migrated", lambda: store.create(definition("X")))
        standard = s.resolve_quote_template("default", store)
        self.assertEqual((standard.record["id"], standard.note), ("standard", ""))
        missing = s.resolve_quote_template("tpl-" + "a" * 32, store)
        self.assertEqual((missing.record["id"], missing.note), ("standard", s.QUOTE_TEMPLATE_DELETED_NOTE))

    def test_a_half_built_table_or_broken_database_is_an_error_not_a_fallback(self):
        with self.storage().connection() as connection:
            connection.execute("drop table sqag_quote_templates")
            connection.execute("create table sqag_quote_templates (workspace_id text, template_id text)")
            connection.commit()
        self.assert_storage_error(503, "quote_templates_not_migrated", lambda: s.resolve_quote_template("default", self.store()))
        with mock.patch.object(s.DatabaseSqagStorage, "connection", side_effect=OSError("disk gone")):
            self.assert_storage_error(503, "quote_templates_unavailable", lambda: s.resolve_quote_template("default", self.store()))


@unittest.skipUnless(ledger.postgres_test_conninfo(), "isolated PostgreSQL test service is not configured")
class PostgresQuoteTemplateStoreTest(QuoteTemplateStoreContract, unittest.TestCase):
    """Runs as the real runtime role against a database built by the real migration runner."""

    # Rebind the ledger's class setup to this class so psycopg, sql and the manifest land here.
    setUpClass = classmethod(ledger.PostgresMigrationLedgerIntegrationTest.setUpClass.__func__)
    _create_roles = ledger.PostgresMigrationLedgerIntegrationTest._create_roles
    create_database = ledger.PostgresMigrationLedgerIntegrationTest.create_database
    _cleanup_fixture = ledger.PostgresMigrationLedgerIntegrationTest._cleanup_fixture
    connect = ledger.PostgresMigrationLedgerIntegrationTest.connect
    apply = ledger.PostgresMigrationLedgerIntegrationTest.apply

    def setUp(self):
        self.database_names = []
        self.created_roles = []
        self.cleanup_done = False
        self.addCleanup(self._cleanup_fixture)
        self._create_roles()
        self.database_name = self.create_database()
        with self.psycopg.connect(ledger.postgres_test_conninfo(self.database_name), autocommit=True) as connection:
            connection.execute(self.sql.SQL("grant connect on database {} to sqag_runtime").format(
                self.sql.Identifier(self.database_name)))
            connection.execute("grant usage on schema public to sqag_runtime")
        self.apply()
        self.url = ledger.safe_postgres_url("sqag_runtime", self.database_name)
        patcher = mock.patch.dict(os.environ, base_env(self.url))
        patcher.start()
        self.addCleanup(patcher.stop)

    def storage(self, workspace=WORKSPACE):
        return s.DatabaseSqagStorage(self.url, workspace, "admin", "admin-user", expected_session_role="sqag_runtime")

    def test_runtime_role_cannot_change_the_table_shape(self):
        with self.storage().connection() as connection:
            with self.assertRaises(Exception):
                connection.execute("alter table sqag_quote_templates add column extra text")
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
