"""Pricing save preconditions on disposable databases and synthetic objects."""
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_runtime_privilege_contract as runtime
from tests.test_webapp import highly_compressible_rgb_png, test_temp_root, with_required_pricing_metadata
from webapp import server


REFERENCE_ID = "synthetic-panel"
WORKSPACE_ID = "workspace-pricing"
OWNER = ("pricing_reference", REFERENCE_ID)


def pricing_reference(cost, *, width=2, visual=True):
    item = with_required_pricing_metadata({
        "id": "panel", "section": "Structures", "description": "Synthetic panel",
        "unit_hint": "sqm", "internal_cost": cost, "markup_multiplier": 1.5,
    })
    if visual:
        encoded = base64.b64encode(highly_compressible_rgb_png(width, 2)).decode("ascii")
        item["visual_references"] = [{"source": "xl/media/panel.png", "data_url": "data:image/png;base64," + encoded}]
    return server.normalize_pricing_reference_payload({
        "id": REFERENCE_ID, "label": "Synthetic panel", "currency": "MYR", "items": [item],
    })


class PricingReferenceStorageCases:
    def storage(self, workspace=WORKSPACE_ID):
        return server.DatabaseSqagStorage(self.database_url, workspace, expected_session_role="sqag_runtime")

    def stored_state(self, storage, mode):
        with storage.connection() as connection:
            row = connection.execute(
                "select payload_json from sqag_pricing_references where workspace_id = ? and reference_id = ?",
                (storage.workspace_id, REFERENCE_ID),
            ).fetchone()
            artifacts = []
            if mode != "local":
                table = "sqag_object_artifacts" if mode == "object" else "sqag_file_artifacts"
                artifacts = [
                    dict(entry) for entry in connection.execute(
                        f"select * from {table} where workspace_id = ? and owner_type = ? and owner_id = ? order by artifact_kind, created_at",
                        (storage.workspace_id, *OWNER),
                    ).fetchall()
                ]
        if mode == "object" and artifacts:
            artifact = storage._read_object_file_artifact(*OWNER, "visual_1_1")
            contents = artifact["content"] if artifact else None
        else:
            contents = None
        return dict(row) if row else None, artifacts, contents

    def assert_writer_holds_lock(self, other):
        with other.connection() as probe:
            if other.database_family == "sqlite":
                probe.execute("pragma busy_timeout = 0")
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    probe.execute("begin immediate")
            else:
                result = probe.execute(
                    "select pg_try_advisory_xact_lock(?) as acquired",
                    (other._object_lifecycle_lock_key(*OWNER),),
                ).fetchone()
                self.assertFalse(result["acquired"])
                # PostgreSQL advisory locks scope a matching ID by workspace.
                key = self.storage(other.workspace_id + "-other")._object_lifecycle_lock_key(*OWNER)
                self.assertTrue(probe.execute(
                    "select pg_try_advisory_xact_lock(?) as acquired", (key,),
                ).fetchone()["acquired"])

    def test_pricing_save_compares_and_writes_under_one_lock(self):
        for mode in ("local", "database", "object"):
            with (
                self.subTest(mode=mode),
                mock.patch.object(server, "configured_artifact_storage_mode", return_value=mode),
                mock.patch.object(server, "configured_object_storage_backend", return_value=server.InMemoryObjectStorageBackend()),
            ):
                writer, probe = (self.storage(WORKSPACE_ID + "-" + mode) for _ in range(2))
                self.assertIsNot(writer, probe)
                absent = writer.pricing_reference_save_snapshot(REFERENCE_ID, "company")
                original_read = writer._read_pricing_reference_for_save
                original_upsert = writer._execute_upsert_payload
                compared = []

                def read(connection, reference_id):
                    self.assert_writer_holds_lock(probe)
                    compared.append(connection)
                    return original_read(connection, reference_id)

                def upsert(connection, *args):
                    self.assert_writer_holds_lock(probe)
                    self.assertEqual(compared, [connection])
                    return original_upsert(connection, *args)

                with (
                    mock.patch.object(writer, "_read_pricing_reference_for_save", side_effect=read),
                    mock.patch.object(writer, "_execute_upsert_payload", side_effect=upsert),
                ):
                    saved = writer.save_pricing_reference(pricing_reference(10), before_save=absent)
                self.assertEqual(saved["item_count"], 1)
                self.assertIsNotNone(self.stored_state(probe, mode)[0])

    def test_stale_pricing_save_cannot_change_workspace_payload_or_artifacts(self):
        for mode in ("local", "database", "object"):
            backend = server.InMemoryObjectStorageBackend()
            with (
                self.subTest(mode=mode),
                mock.patch.object(server, "configured_artifact_storage_mode", return_value=mode),
                mock.patch.object(server, "configured_object_storage_backend", return_value=backend),
            ):
                workspace = WORKSPACE_ID + "-" + mode
                first, second, other = self.storage(workspace), self.storage(workspace), self.storage(workspace + "-other")
                initial = pricing_reference(10)
                winner = pricing_reference(30, width=3)
                stale = pricing_reference(20, visual=False)
                missing = first.pricing_reference_save_snapshot(REFERENCE_ID, "")
                other_missing = other.pricing_reference_save_snapshot(REFERENCE_ID, "")
                second.save_pricing_reference(initial, before_save=missing)
                # Simulate a pack written before save revisions were introduced.
                with second.connection() as connection:
                    legacy, _ = second._read_pricing_reference_for_save(connection, REFERENCE_ID)
                    legacy.pop("_save_revision")
                    second._execute_upsert_payload(connection, "sqag_pricing_references", "reference_id", REFERENCE_ID, legacy)
                    connection.commit()
                edited = first.pricing_reference_save_snapshot(REFERENCE_ID, "company")
                self.assertFalse(first.pricing_reference_save_snapshot(REFERENCE_ID, "bundled").selected)
                self.assertEqual(edited.selected["items"][0]["internal_cost"], 10)

                def assert_conflict(snapshot):
                    before = self.stored_state(first, mode)
                    with (
                        mock.patch.object(backend, "store_artifact", wraps=backend.store_artifact) as upload,
                        mock.patch.object(backend, "delete_artifact", wraps=backend.delete_artifact) as delete,
                    ):
                        for proposed in (stale, pricing_reference(20, width=4)):
                            with self.assertRaises(server.SqagStorageAccessError) as conflict:
                                first.save_pricing_reference(proposed, before_save=snapshot)
                            self.assertEqual((conflict.exception.status, conflict.exception.reason),
                                             (409, "pricing_reference_revision_conflict"))
                        upload.assert_not_called()
                        delete.assert_not_called()
                    self.assertEqual(self.stored_state(second, mode), before)

                assert_conflict(missing)
                second.save_pricing_reference(winner)
                assert_conflict(edited)
                winner_snapshot = first.pricing_reference_save_snapshot(REFERENCE_ID, "company")
                self.assertTrue(second.delete_pricing_reference(REFERENCE_ID))
                assert_conflict(winner_snapshot)
                second.save_pricing_reference(winner)
                assert_conflict(winner_snapshot)
                other.save_pricing_reference(stale, before_save=other_missing)
                self.assertEqual(other.pricing_reference_detail(REFERENCE_ID)["items"][0]["internal_cost"], 20)
                self.assertEqual(first.pricing_reference_detail(REFERENCE_ID)["items"][0]["internal_cost"], 30)


class SqlitePricingReferenceStorageTest(PricingReferenceStorageCases, unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pr-", dir=test_temp_root())))
        self.database_url = "sqlite:///" + (root / "pricing.sqlite3").as_posix()
        server.apply_sqag_storage_migrations(self.database_url)


@unittest.skipUnless(runtime.postgres_test_enabled(), "real disposable PostgreSQL-17 service is not configured")
class PostgresPricingReferenceStorageTest(PricingReferenceStorageCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()

    def setUp(self):
        fixture = runtime.RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.database_url = runtime.safe_postgres_url("sqag_runtime", fixture.database_name)
