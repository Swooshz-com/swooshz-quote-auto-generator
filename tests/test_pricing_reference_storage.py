"""Pricing save preconditions on disposable databases and synthetic objects."""
import base64
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
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


PRICING_INTERRUPTION_WORKER = """
import os
import shutil
import sys
from pathlib import Path
from unittest import mock
from tests.test_pricing_reference_storage import pricing_reference
from webapp import server

root = Path(sys.argv[1])
point = sys.argv[2]
reference = pricing_reference(30, width=3)
extra = dict(pricing_reference(30, width=4)["items"][0]["visual_references"][0])
extra["source"] = "xl/media/alternate.png"
reference["items"][0]["visual_references"].append(extra)
store_original = Path.write_bytes
text_original = Path.write_text
rename_original = os.replace
remove_original = shutil.rmtree

def write_bytes(path, data):
    result = store_original(path, data)
    if "stage" in path.parts and point == "image:" + path.name:
        os._exit(71)
    return result

def write_text(path, text, *args, **kwargs):
    result = text_original(path, text, *args, **kwargs)
    if "stage" in path.parts and point == path.name:
        os._exit(71)
    return result

def rename(source, dest):
    source, dest = Path(source), Path(dest)
    if point == "config-temp" and dest.name == "reference.json":
        os._exit(71)
    if point == "stage-complete" and (source == store.directory or source == store.stage):
        os._exit(71)
    result = rename_original(source, dest)
    if (point == "config-replace" and dest.name == "reference.json"
        or point == "old-moved" and dest == store.previous
        or point == "promoted" and source == store.stage):
        os._exit(71)
    return result

def remove(path, *args, **kwargs):
    if point == "cleanup" and path == store.previous:
        (path / "reference.json").unlink()
        os._exit(71)
    return remove_original(path, *args, **kwargs)

with mock.patch.object(server, "pricing_references_root", return_value=root):
    store = server.LocalPricingReferenceStore(reference["id"])
    with (mock.patch.object(Path, "write_bytes", write_bytes),
          mock.patch.object(Path, "write_text", write_text),
          mock.patch.object(os, "replace", rename),
          mock.patch.object(shutil, "rmtree", remove)):
        server.save_pricing_reference_pack(reference)
"""


class LocalPricingReferenceRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pricing-transaction-", dir=test_temp_root())))
        self.enterContext(mock.patch.object(server, "pricing_references_root", return_value=self.root))
        self.enterContext(mock.patch.object(server, "configured_storage_mode", return_value="local"))
        self.logs = self.enterContext(mock.patch.object(server, "write_local_log"))
        self.store = server.LocalPricingReferenceStore(REFERENCE_ID)
        self.selection = {"pricing_reference_id": REFERENCE_ID, "pricing_reference": {"id": REFERENCE_ID, "source": "local"}}

    @staticmethod
    def files(root):
        return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def make_legacy(self):
        server.save_pricing_reference_pack(pricing_reference(10))
        pack = self.store.directory
        old_catalog = pack / "pricing-catalog.json"
        old_markdown = pack / "pricing-catalog.ai-reference.md"
        old_catalog.rename(pack / "legacy-catalogue.json")
        old_markdown.rename(pack / "legacy-catalogue.md")
        config = json.loads((pack / "reference.json").read_bytes())
        config.pop("_save_revision")
        config.update(pricing_catalog="legacy-catalogue.json", pricing_reference="legacy-catalogue.md")
        (pack / "reference.json").write_text(json.dumps(config), encoding="utf-8")
        (pack / "unmentioned.bin").write_bytes(b"fixture keep")
        return self.files(pack)

    def cost(self):
        detail = server.pricing_reference_pack_detail(REFERENCE_ID, "local")
        return detail["items"][0]["internal_cost"] if detail else None

    def test_process_exit_recovery_matrix(self):
        precommit = ("image:panel.png", "image:alternate.png", "pricing-catalog.json",
                     "pricing-catalog.ai-reference.md", "config-temp", "config-replace", "stage-complete")
        for editing, points in ((False, precommit + ("promoted",)),
                                (True, precommit + ("old-moved", "promoted", "cleanup"))):
            for point in points:
                with self.subTest(editing=editing, point=point), tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    with mock.patch.object(server, "pricing_references_root", return_value=root):
                        self.store = server.LocalPricingReferenceStore(REFERENCE_ID)
                        expected = self.make_legacy() if editing else {}
                        worker = subprocess.run(
                            [sys.executable, "-B", "-c", PRICING_INTERRUPTION_WORKER, str(root), point],
                            capture_output=True, timeout=15,
                        )
                        self.assertEqual(worker.returncode, 71, worker.stderr.decode("utf-8", errors="replace"))
                        committed = point in {"promoted", "cleanup"}
                        if point == "old-moved":
                            self.assertFalse(self.store.directory.exists())
                            self.assertEqual(self.files(self.store.previous), expected)
                        # Listing is the first reader, including when the public name is absent.
                        self.assertEqual([pack["id"] for pack in server.list_local_pricing_references()],
                                         [REFERENCE_ID] if editing or committed else [])
                        self.assertEqual(self.cost(), 30 if committed else 10 if editing else None)
                        current = self.files(self.store.directory)
                        if committed and editing:
                            for name in ("legacy-catalogue.json", "legacy-catalogue.md", "unmentioned.bin"):
                                self.assertEqual(current[Path(name)], expected[Path(name)])
                        if not committed:
                            self.assertEqual(current, expected)
                        self.assertFalse(self.store.stage.exists())
                        self.assertFalse(self.store.previous.exists())
                        self.assertEqual(self.files(self.store.directory), current)

    def test_rollback_retry_and_postcommit_cleanup_failure(self):
        old = self.make_legacy()
        original_rename = os.replace

        def failing_rename(src, dst):
            if src in {self.store.stage, self.store.previous}:
                raise OSError("synthetic rename fault")
            original_rename(src, dst)

        with mock.patch.object(os, "replace", side_effect=failing_rename):
            with self.assertRaises(server.SqagStorageAccessError):
                server.save_pricing_reference_pack(pricing_reference(30))
            self.assertFalse(self.store.directory.exists())
            self.assertEqual(self.files(self.store.previous), old)
        self.assertEqual(self.cost(), 10)
        self.assertEqual(self.files(self.store.directory), old)

        with mock.patch("webapp.server.shutil.rmtree", side_effect=OSError("synthetic busy cleanup")):
            saved = server.save_pricing_reference_pack(pricing_reference(30))
            self.assertEqual(saved["id"], REFERENCE_ID)
            self.assertEqual(self.cost(), 30)
            self.assertTrue(self.store.previous.is_dir())
            with self.assertRaises(server.SqagStorageAccessError):
                server.save_pricing_reference_pack(pricing_reference(40))
        self.assertEqual(self.cost(), 30)
        self.assertFalse(self.store.previous.exists())
        self.assertIn(mock.call("error_pricing_reference_transaction_cleanup", {
            "reason": "pricing_reference_transaction_cleanup_pending", "slot": "previous",
        }), self.logs.call_args_list)

    def test_inconsistent_or_corrupt_committed_state_keeps_all_copies(self):
        self.make_legacy()
        self.store.previous.mkdir()
        self.store.stage.mkdir()
        copies = self.files(self.store.directory)
        with self.assertRaises(server.SqagStorageAccessError):
            self.cost()
        self.assertTrue(self.store.stage.exists())
        self.assertTrue(self.store.previous.exists())
        self.assertEqual(self.files(self.store.directory), copies)
        self.store.stage.rmdir()
        (self.store.directory / "reference.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(server.SqagStorageAccessError):
            self.cost()
        self.assertTrue(self.store.previous.exists())


    def test_unowned_transaction_slots_are_retained(self):
        self.store.stage.mkdir(parents=True)
        unrelated = self.store.stage / "unowned.bin"
        unrelated.write_bytes(b"fixture unrelated file")
        with self.assertRaises(server.SqagStorageAccessError):
            self.cost()
        self.assertEqual(unrelated.read_bytes(), b"fixture unrelated file")

    def test_legacy_digest_and_new_exact_revision_match_the_generator(self):
        from scripts.generate_quote import catalog_payload_digest

        adjacent = 9007199254740992
        raw = {"currency": "MYR", "schema_version": 1, "items": [
            {"id": "synthetic-panel", "section": "Structures", "description": "Synthetic panel", "unit_hint": "sqm",
             "internal_cost": 24, "markup_multiplier": 1.5, "category_order": str(adjacent + 1), "item_order": adjacent + 1},
            {"id": "synthetic-rail", "section": "Structures", "description": "Synthetic rail", "unit_hint": "sqm",
             "internal_cost": 40, "markup_multiplier": 1.5, "category_order": str(adjacent + 1), "item_order": adjacent},
        ]}
        expected_legacy = "sha256:9f0af94a10a3e04b2c3225567e302b86cfdc3e4ae38e58e46de9cfa83ce05d30"
        self.store.directory.mkdir()
        (self.store.directory / "reference.json").write_text(json.dumps({"id": REFERENCE_ID, "currency": "MYR"}), encoding="utf-8")
        (self.store.directory / "pricing-catalog.json").write_text(json.dumps(raw), encoding="utf-8")
        job = self.root / ".job"
        job.mkdir()

        def check(snapshot):
            payload = {**self.selection, "_pricing_reference_snapshot": snapshot}
            catalogue = json.loads(server.pricing_catalog_path_for_payload(payload, job).read_bytes())
            self.assertEqual(catalog_payload_digest(catalogue), snapshot.digest)
            self.assertEqual(server.pricing_reference_catalog_digest(snapshot.detail()), snapshot.digest)
            self.assertNotIn("_save_revision", snapshot.detail())
            return [item["item_order"] for item in catalogue["items"]]

        legacy = server.capture_pricing_reference_snapshot(self.selection)
        self.assertEqual(legacy.digest, expected_legacy)
        self.assertEqual(check(legacy), [adjacent, adjacent])
        for row in raw["items"]:
            row.update(with_required_pricing_metadata(row))
        payload = {"id": REFERENCE_ID, "label": "Synthetic exact order", **raw}
        saved = server.save_pricing_reference_pack(server.normalize_pricing_reference_payload(payload))
        exact = server.capture_pricing_reference_snapshot(self.selection)
        self.assertEqual(exact.digest, saved["digest_sha256"])
        self.assertEqual(check(exact), [adjacent, adjacent + 1])
        self.assertNotEqual(exact.digest, expected_legacy)
        raw["items"][0]["item_order"] = adjacent
        changed = server.save_pricing_reference_pack(server.normalize_pricing_reference_payload(payload))
        self.assertNotEqual(changed["digest_sha256"], exact.digest)
        self.assertEqual(check(exact), [adjacent, adjacent + 1])

    def test_readers_wait_for_promotion_and_generator_uses_the_same_snapshot(self):
        self.make_legacy()
        other_id = "synthetic-independent"
        server.save_pricing_reference_pack({**pricing_reference(12), "id": other_id})
        original_rename = os.replace
        in_gap = threading.Event()
        resume = threading.Event()
        started = threading.Barrier(5)
        job = self.root / ".job"
        job.mkdir()

        def pause_rename(src, dst):
            original_rename(src, dst)
            if dst == self.store.previous:
                in_gap.set()
                if not resume.wait(8):
                    raise RuntimeError("Test failed to resume the writer")

        def reader(read):
            started.wait(timeout=4)
            return read()

        with ThreadPoolExecutor(max_workers=5) as pool, mock.patch.object(os, "replace", side_effect=pause_rename):
            writer = pool.submit(server.save_pricing_reference_pack, pricing_reference(30, width=3))
            try:
                self.assertTrue(in_gap.wait(5))
                readers = [
                    pool.submit(reader, server.list_local_pricing_references),
                    pool.submit(reader, lambda: server.pricing_reference_pack_detail(REFERENCE_ID)),
                    pool.submit(reader, lambda: server.pricing_reference_export_xlsx(REFERENCE_ID, "local")),
                    pool.submit(reader, lambda: server.capture_pricing_reference_snapshot(self.selection)),
                ]
                started.wait(timeout=4)
                self.assertEqual(server.pricing_reference_pack_detail(other_id)["items"][0]["internal_cost"], 12)
                self.assertTrue(all(not read.done() for read in readers))
            finally:
                resume.set()
            saved = writer.result(timeout=6)
            listing, detail, exported, snapshot = [read.result(timeout=7) for read in readers]

        self.assertEqual(next(pack for pack in listing if pack["id"] == REFERENCE_ID)["digest_sha256"], saved["digest_sha256"])
        self.assertEqual(detail["digest_sha256"], snapshot.digest)
        self.assertEqual(detail["items"][0]["internal_cost"], 30)
        with zipfile.ZipFile(io.BytesIO(exported[1])) as workbook:
            images = [name for name in workbook.namelist() if name.startswith("xl/media/")]
            self.assertEqual([workbook.read(name) for name in images], [highly_compressible_rgb_png(3, 2)])
        self.assertEqual(snapshot.visual_images()[0]["data_url"].split(",")[1],
                         base64.b64encode(highly_compressible_rgb_png(3, 2)).decode("ascii"))
        generation_input = {**self.selection, "_pricing_reference_snapshot": snapshot}
        path = server.pricing_catalog_path_for_payload(generation_input, job)
        server.save_pricing_reference_pack(pricing_reference(40, width=4))
        self.assertTrue(path.is_relative_to(job))
        self.assertEqual(json.loads(path.read_bytes())["items"][0]["internal_cost"], 30)


class PricingReferenceStorageCases:
    artifact_modes: tuple[str, ...]

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
        for mode in self.artifact_modes:
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
                row, artifacts, object_bytes = self.stored_state(probe, mode)
                self.assertIsNotNone(row)
                expected_bytes = highly_compressible_rgb_png(2, 2)
                if mode == "local":
                    self.assertEqual(artifacts, [])
                    self.assertIsNone(object_bytes)
                elif mode == "database":
                    self.assertEqual(len(artifacts), 1)
                    self.assertEqual(bytes(artifacts[0]["content_blob"]), expected_bytes)
                    self.assertIsNone(object_bytes)
                else:
                    self.assertEqual(len(artifacts), 1)
                    self.assertEqual(artifacts[0]["artifact_kind"], "visual_1_1")
                    self.assertEqual(object_bytes, expected_bytes)

    def test_stale_pricing_save_cannot_change_workspace_payload_or_artifacts(self):
        for mode in self.artifact_modes:
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

    def test_corrupt_pricing_record_never_admits_a_save(self):
        corrupt_payloads = ["{", "null", "[]", "{}", '{"items":false}', '{"id":"other","items":[]}']
        for mode in self.artifact_modes:
            backend = server.InMemoryObjectStorageBackend()
            with (
                self.subTest(mode=mode),
                mock.patch.object(server, "configured_artifact_storage_mode", return_value=mode),
                mock.patch.object(server, "configured_object_storage_backend", return_value=backend),
            ):
                writer = self.storage(WORKSPACE_ID + "-corruption-" + mode)
                other = self.storage(writer.workspace_id + "-other")
                initial = pricing_reference(10)
                initial.pop("id")
                with writer.connection() as connection:
                    writer._execute_upsert_payload(connection, "sqag_pricing_references", "reference_id", REFERENCE_ID, initial)
                    connection.commit()
                self.assertEqual(writer.pricing_reference_detail(REFERENCE_ID)["id"], REFERENCE_ID)
                writer.save_pricing_reference(pricing_reference(10))
                other.save_pricing_reference(pricing_reference(90))
                other_before = self.stored_state(other, mode)
                edit = writer.pricing_reference_save_snapshot(REFERENCE_ID, "company")
                for corrupt in corrupt_payloads:
                    with self.subTest(corrupt=corrupt):
                        with writer.connection() as connection:
                            connection.execute(
                                "update sqag_pricing_references set payload_json = ? where workspace_id = ? and reference_id = ?",
                                (corrupt, writer.workspace_id, REFERENCE_ID),
                            )
                            connection.commit()
                        before = self.stored_state(writer, mode)
                        with (
                            mock.patch.object(backend, "store_artifact", wraps=backend.store_artifact) as store,
                            mock.patch.object(backend, "delete_artifact", wraps=backend.delete_artifact) as delete,
                        ):
                            for read in (
                                lambda: writer.pricing_reference_save_snapshot(REFERENCE_ID, "company"),
                                lambda: writer.pricing_reference_detail(REFERENCE_ID),
                                writer.list_pricing_references,
                            ):
                                with self.assertRaises(server.SqagStorageAccessError) as error:
                                    read()
                                self.assertEqual(error.exception.status, 503)
                            for expected in (edit, server.PricingReferenceSaveSnapshot(None, None), None):
                                with self.assertRaises(server.SqagStorageAccessError) as error:
                                    writer.save_pricing_reference(pricing_reference(20), before_save=expected)
                                self.assertEqual(error.exception.status, 503)
                            store.assert_not_called()
                            delete.assert_not_called()
                        self.assertEqual(self.stored_state(writer, mode), before)
                        self.assertEqual(self.stored_state(other, mode), other_before)


class SqlitePricingReferenceStorageTest(PricingReferenceStorageCases, unittest.TestCase):
    artifact_modes = ("local", "database", "object")

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pr-", dir=test_temp_root())))
        self.database_url = "sqlite:///" + (root / "pricing.sqlite3").as_posix()
        server.apply_sqag_storage_migrations(self.database_url)


@unittest.skipUnless(runtime.postgres_test_enabled(), "real disposable PostgreSQL-17 service is not configured")
class PostgresPricingReferenceStorageTest(PricingReferenceStorageCases, unittest.TestCase):
    artifact_modes = ("local", "object")

    @classmethod
    def setUpClass(cls):
        runtime.RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()

    def setUp(self):
        fixture = runtime.RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.database_url = runtime.safe_postgres_url("sqag_runtime", fixture.database_name)
