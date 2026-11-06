"""Offline real-SQLite profile isolation, with BLOB and synthetic object adapters."""
import contextlib
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from webapp import server as s


class ProfileStorageCase:
    @contextlib.contextmanager
    def stores(self, artifact_mode="database"):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite:///" + (Path(directory) / "profiles.sqlite3").as_posix()
            env = {"APP_MODE": "local", "SQAG_AUTH_MODE": "none", "SQAG_DISABLE_DOTENV": "1",
                   "SQAG_STORAGE_MODE": "database", "SQAG_DATABASE_URL": url,
                   "SQAG_ARTIFACT_STORAGE_MODE": artifact_mode}
            with mock.patch.dict(os.environ, env), mock.patch.object(
                s, "configured_object_storage_backend", return_value=s.InMemoryObjectStorageBackend()
            ), mock.patch.object(s.urllib.request, "urlopen", side_effect=AssertionError("Network forbidden")):
                s.apply_sqag_storage_migrations(url)
                def store(user, role="operator", workspace="workspace-synthetic"):
                    return s.DatabaseSqagStorage(url, workspace, role, user)
                yield store

    def profile(self, label="Synthetic profile"):
        return s.normalize_profile_payload({"id": "synthetic-profile", "label": label,
                                            "defaults": {"company": {"name": "Synthetic Company"}}})


class ProfileAccessStorageTest(ProfileStorageCase, unittest.TestCase):
    def test_ownership_copy_exports_layout_and_no_forbidden_effects(self):
        for mode in ("database", "object"):
            with self.subTest(mode=mode), self.stores(mode) as store:
                owner, other, admin = store("staff-a"), store("staff-b"), store("support", "admin")
                saved = owner.save_profile(self.profile(), operation="create")
                identity = saved["id"]
                self.assertNotEqual(identity, "synthetic-profile")
                self.assertEqual(saved["revision"], 1)
                self.assertTrue(saved["permissions"]["canDelete"])
                self.assertEqual(other.list_company_profiles(), [])
                self.assertEqual(store("staff-a", workspace="workspace-other").list_profiles(), [])
                self.assertIsNone(other.profile_detail(identity))
                self.assertIsNone(other.profile_layout_artifact(identity))
                self.assertIsNone(other.company_profile_export_payload(identity))
                before = owner.profile_detail(identity)
                with mock.patch.object(other, "_prepare_profile_layout_artifact", side_effect=AssertionError("Forbidden effect")):
                    with self.assertRaises(s.SqagStorageAccessError):
                        other.save_profile({**self.profile(), "id": identity}, operation="update", revision=1)
                    with self.assertRaises(s.SqagStorageAccessError):
                        other.delete_profile(identity, revision=1)
                self.assertEqual(owner.profile_detail(identity), before)
                exported = owner.company_profile_export_payload(identity)
                self.assertNotIn("_access", exported["profile"])
                copied = other.save_profile(s.normalize_profile_payload(exported), operation="create")
                self.assertNotEqual(copied["id"], identity)
                self.assertEqual(other.profile_layout_artifact(copied["id"])["content"], owner.profile_layout_artifact(identity)["content"])
                shared = admin.change_profile_access(identity, {"version": 1, "owner_user_id": "staff-a", "visibility": "shared"}, revision=1)
                self.assertFalse(owner.list_company_profiles()[0]["permissions"]["canUpdate"])
                self.assertIsNotNone(other.company_profile_export_payload(identity))
                for actor in (owner, other):
                    with self.assertRaises(s.SqagStorageAccessError):
                        actor.delete_profile(identity, revision=shared["revision"])
                self.assertEqual(admin.profile_detail(identity), before)
                self.assertTrue(admin.delete_profile(identity, revision=shared["revision"]))
                self.assertIsNone(admin.profile_detail(identity))
                with self.assertRaises(s.SqagStorageAccessError):
                    owner.save_profile({**self.profile(), "id": identity}, operation="update", revision=shared["revision"])

    def test_stale_revision_forgery_and_same_label(self):
        with self.stores() as store:
            owner = store("staff-a")
            first = owner.save_profile(self.profile(), operation="create")
            second = owner.save_profile(self.profile(), operation="create")
            self.assertNotEqual(first["id"], second["id"])
            updated = owner.save_profile({**self.profile(), "id": first["id"]}, operation="update", revision=1)
            self.assertEqual(updated["revision"], 2)
            for action in (
                lambda: owner.delete_profile(first["id"], revision=1),
                lambda: owner.save_profile({**self.profile(), "id": first["id"]}, operation="update", revision=1),
            ):
                with self.assertRaises(s.SqagStorageAccessError):
                    action()
            for key in ("_access", "_revision", "permissions"):
                with self.assertRaises(ValueError):
                    owner.save_profile({**self.profile(), key: {}}, operation="create")

    def test_legacy_visibility_invalid_metadata_and_local_fail_closed(self):
        for mode in ("database", "object"):
            with self.subTest(mode=mode), self.stores(mode) as store:
                admin = store("support", "admin")
                legacy = {"id": "legacy", "defaults": {}}
                admin._upsert_payload("sqag_profiles", "profile_id", "legacy", legacy)
                for actor in (store("staff-a"), store("staff-b"), store("reader", "viewer"), admin):
                    self.assertIsNotNone(actor.profile_detail("legacy"))
                    listed = actor.list_company_profiles()
                    self.assertEqual([item["id"] for item in listed], ["legacy"])
                    self.assertEqual(listed[0]["revision"], 0)
                    self.assertEqual(listed[0]["permissions"]["canDelete"], actor is admin)
                    if actor is not admin:
                        with self.assertRaises(s.SqagStorageAccessError) as update:
                            actor.save_profile(legacy, revision=listed[0]["revision"])
                        self.assertEqual(update.exception.status, 404)
                        with self.assertRaises(s.SqagStorageAccessError) as delete:
                            actor.delete_profile("legacy", revision=listed[0]["revision"])
                        self.assertEqual(delete.exception.status, 404)
                        self.assertEqual(admin._read_payload("sqag_profiles", "profile_id", "legacy"), legacy)
                self.assertIsNone(store("staff-a", workspace="workspace-other").profile_detail("legacy"))
                updated = admin.save_profile(legacy, revision=0)
                self.assertEqual(updated["revision"], 1)
                self.assertNotIn("_access", admin._read_payload("sqag_profiles", "profile_id", "legacy"))
                self.assertIsNotNone(store("staff-b").profile_detail("legacy"))
                admin._upsert_payload("sqag_profiles", "profile_id", "invalid", {"id": "invalid", "_access": {}})
                self.assertIsNone(admin.profile_detail("invalid"))
                self.assertEqual(len(admin.list_profiles()), 1)
                self.assertTrue(admin.delete_profile("legacy", revision=updated["revision"]))
        with mock.patch.object(s, "internal_password_mode_enabled", return_value=True):
            with self.assertRaises(s.SqagStorageAccessError):
                s.LocalSqagStorage()


if __name__ == "__main__":
    unittest.main()
