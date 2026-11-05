"""Real PostgreSQL-17 publication, using CI's disposable migration/role fixture.

Discovered by the existing CI unittest command. Without the disposable service,
every case is explicitly skipped; SQLite controls cannot count as a PG pass.
"""
import threading
import unittest
from unittest import mock

from tests import test_runtime_privilege_contract as runtime
from tests.publication_storage_support import PublicationStorageCases
from tests.publication_storage_support import s


@unittest.skipUnless(runtime.postgres_test_enabled(), "real disposable PostgreSQL-17 service is not configured")
class PublicationStoragePostgresTest(PublicationStorageCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()

    def setUp(self):
        # Compose, rather than inherit, so discovery does not duplicate the
        # large privilege-contract suite in this publication module.
        fixture = runtime.RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.database_url = runtime.safe_postgres_url("sqag_runtime", fixture.database_name)

    def test_concurrent_publication_save_and_delete_use_real_database_locks(self):
        for adapter in ("memory", "s3"):
            with self.subTest(adapter=adapter), self.case(adapter):
                _, prior, _ = self.attempt("before-race")
                entered, release = threading.Event(), threading.Event()
                results, errors = [], []
                store = self.backend.store_artifact
                def paused_upload(**kwargs):
                    if kwargs["owner_type"] == "generated_quote_version" and kwargs["artifact_kind"] == "xlsx":
                        entered.set()
                        if not release.wait(10):
                            raise AssertionError("Publication race barrier timed out")
                    return store(**kwargs)
                def save():
                    try:
                        results.append(self.attempt("raced-save"))
                    except Exception as error:
                        errors.append(error)
                worker = threading.Thread(target=save, daemon=True)
                with mock.patch.object(self.backend, "store_artifact", side_effect=paused_upload):
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(10), "Save did not reach its object upload while holding SQL locks")
                        # Another connection must fail closed on pg_try_advisory_xact_lock,
                        # rather than remove the previous version or the in-flight output.
                        with self.assertRaises(s.SqagStorageAccessError) as denied:
                            self.reopen().delete_quote_session(self.session_id)
                        self.assertEqual(denied.exception.reason, "object_artifact_storage_unavailable")
                        self.assertEqual(self.metadata()["publication"]["run_id"], prior["generation_run_id"])
                    finally:
                        release.set()
                        worker.join(15)
                self.assertFalse(worker.is_alive(), "Publication worker did not stop")
                if errors:
                    raise errors[0]
                self.assertEqual(len(results), 1)
                _, saved, content = results[0]
                self.assert_downloads(saved, content)

    def test_concurrent_delete_rejects_save_without_resurrecting_publication(self):
        for adapter in ("memory", "s3"):
            with self.subTest(adapter=adapter), self.case(adapter):
                _, prior, _ = self.attempt("before-delete")
                entered, release = threading.Event(), threading.Event()
                results, errors = [], []
                delete = self.backend.delete_artifact
                def paused_delete(metadata, *, workspace_id):
                    entered.set()
                    if not release.wait(10):
                        raise AssertionError("Deletion race barrier timed out")
                    return delete(metadata, workspace_id=workspace_id)
                def remove():
                    try:
                        results.append(self.reopen().delete_quote_session(self.session_id))
                    except Exception as error:
                        errors.append(error)
                worker = threading.Thread(target=remove, daemon=True)
                with mock.patch.object(self.backend, "delete_artifact", side_effect=paused_delete):
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(10), "Delete did not reach the object backend while holding SQL locks")
                        with self.assertRaises(s.SqagStorageAccessError) as denied:
                            self.reopen().create_or_update_quote_session(self.payload)
                        self.assertEqual(denied.exception.reason, "object_artifact_storage_unavailable")
                    finally:
                        release.set()
                        worker.join(15)
                self.assertFalse(worker.is_alive(), "Deletion worker did not stop")
                if errors:
                    raise errors[0]
                self.assertEqual(results, [True])
                self.assertIsNone(self.reopen().get_quote_session(self.session_id))
                for kind in ("xlsx", "pdf"):
                    url = prior["result"]["quote_session"]["exports"][kind]["url"]
                    self.assertEqual(self.request(url)[0], 404)
                with self.reopen().connection() as connection:
                    versions = connection.execute("select count(*) as n from sqag_quote_publication_versions where workspace_id = ? and session_id = ?",
                        (self.storage.workspace_id, self.session_id)).fetchone()
                    active = connection.execute("select count(*) as n from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and status = ? and deleted_at is null",
                        (self.storage.workspace_id, "generated_quote_version", prior["generation_run_id"], "active")).fetchone()
                self.assertEqual(versions["n"], 0)
                self.assertEqual(active["n"], 0)


if __name__ == "__main__":
    unittest.main()
