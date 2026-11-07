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
                for denied in (other, admin, store("support", "management")):
                    self.assertEqual(denied.list_company_profiles(), [])
                    self.assertEqual(denied.list_profiles(), [])
                    self.assertIsNone(denied.profile_detail(identity))
                    self.assertIsNone(denied.profile_layout_artifact(identity))
                    self.assertIsNone(denied.company_profile_export_payload(identity))
                    original = owner._read_payload("sqag_profiles", "profile_id", identity)
                    objects = copy.deepcopy(s.configured_object_storage_backend()._objects)
                    with mock.patch.object(denied, "_prepare_profile_layout_artifact", side_effect=AssertionError("Forbidden effect")):
                        for name, action, status in (
                            ("update", lambda: denied.save_profile({**self.profile(), "id": identity}, operation="update", revision=1), 404),
                            ("delete", lambda: denied.delete_profile(identity, revision=1), 404),
                            ("access", lambda: denied.change_profile_access(identity, {
                                "version": 1, "owner_user_id": "staff-a", "visibility": "shared"}, revision=1),
                                404 if denied.profile_policy.admin else 403),
                        ):
                            with self.subTest(role=denied.role, action=name):
                                with self.assertRaises(s.SqagStorageAccessError) as caught:
                                    action()
                                self.assertEqual(caught.exception.status, status)
                                self.assertEqual(caught.exception.reason, "profile_access_denied" if status == 403 else "profile_unavailable")
                    self.assertEqual(owner._read_payload("sqag_profiles", "profile_id", identity), original)
                    self.assertEqual(s.configured_object_storage_backend()._objects, objects)
                self.assertEqual(store("staff-a", workspace="workspace-other").list_profiles(), [])
                before = owner.profile_detail(identity)
                exported = owner.company_profile_export_payload(identity)
                self.assertNotIn("_access", exported["profile"])
                copied = other.save_profile(s.normalize_profile_payload(exported), operation="create")
                self.assertNotEqual(copied["id"], identity)
                self.assertEqual(other.profile_layout_artifact(copied["id"])["content"], owner.profile_layout_artifact(identity)["content"])
                # Seed shared metadata as fixture setup, never via cross-user admin access.
                admin._upsert_payload("sqag_profiles", "profile_id", identity, {
                    **original, "_access": {"version": 1, "owner_user_id": "staff-a", "visibility": "shared"}})
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

    def test_admin_own_personal_and_shared_management(self):
        for mode in ("database", "object"):
            for role in ("admin", "management"):
                with self.subTest(mode=mode, role=role), self.stores(mode) as store:
                    admin, peer = store("support", role), store("staff-a")
                    saved = admin.save_profile(self.profile(), operation="create")
                    identity = saved["id"]
                    self.assertTrue(all(saved["permissions"].values()))
                    self.assertIsNotNone(admin.profile_detail(identity))
                    self.assertIsNotNone(admin.company_profile_export_payload(identity))
                    updated = admin.save_profile({**self.profile(), "id": identity}, operation="update", revision=1)
                    shared = admin.change_profile_access(identity, {
                        "version": 1, "owner_user_id": "staff-a", "visibility": "shared"}, revision=updated["revision"])
                    self.assertTrue(all(shared["permissions"].values()))
                    self.assertIsNotNone(peer.company_profile_export_payload(identity))
                    changed = admin.save_profile({**self.profile(), "id": identity}, operation="update", revision=shared["revision"])
                    private = admin.change_profile_access(identity, {
                        "version": 1, "owner_user_id": "staff-a", "visibility": "user"}, revision=changed["revision"])
                    self.assertFalse(any(private["permissions"].values()))
                    self.assertIsNone(admin.profile_detail(identity))
                    self.assertTrue(peer.delete_profile(identity, revision=private["revision"]))
                    own = admin.save_profile(self.profile(), operation="create")
                    self.assertTrue(admin.delete_profile(own["id"], revision=own["revision"]))

    def test_legacy_visibility_and_invalid_metadata(self):
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

    def test_local_internal_profile_refusal_preserves_other_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            config = s.CompanyConfigStore(Path(directory))
            with mock.patch.object(s, "company_config_store", return_value=config):
                local = s.LocalSqagStorage()
                company = local.company_id()
                with mock.patch.object(s, "internal_password_mode_enabled", return_value=False):
                    saved = local.save_profile(self.profile())
                    self.assertEqual(len(local.list_company_profiles()), 1)
                    self.assertIsNotNone(local.company_profile_export_payload(saved["id"]))
                before = {p.relative_to(directory): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}
                with mock.patch.object(s, "internal_password_mode_enabled", return_value=True):
                    local = s.LocalSqagStorage()
                    with mock.patch.object(config, "_read_collection", side_effect=AssertionError("Saved profile read")):
                        self.assertEqual(local.list_company_profiles(), [])
                        self.assertIsNone(s.load_company_profile_pack(saved["id"], company))
                        self.assertIsNone(s.workspace_quote_company_profile())
                    for action in (
                        lambda: local.save_profile(self.profile()),
                        lambda: local.save_profile({**self.profile(), "id": saved["id"]}, operation="update", revision=1),
                        lambda: local.delete_profile(saved["id"]),
                        lambda: local.company_profile_export_payload(saved["id"]),
                    ):
                        with self.assertRaises(s.SqagStorageAccessError) as denied:
                            action()
                        self.assertEqual(denied.exception.status, 403)
                        self.assertEqual(denied.exception.reason, "profile_local_actor_unavailable")
                    with mock.patch.object(s, "list_profiles", return_value=[{"id": "generic"}]):
                        self.assertEqual(local.list_profiles(), [{"id": "generic"}])
                    with mock.patch.object(s, "list_pricing_references", return_value=[{"id": "pricing"}]):
                        self.assertEqual(local.list_pricing_references(), [{"id": "pricing"}])
                    with mock.patch.object(s, "list_quote_sessions", return_value=[]):
                        self.assertEqual(local.list_quote_sessions(), [])
                self.assertEqual({p.relative_to(directory): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}, before)
                with mock.patch.object(s, "internal_password_mode_enabled", return_value=False):
                    self.assertTrue(local.delete_profile(saved["id"]))

    def test_member_cannot_change_own_profile_access(self):
        for mode in ("database", "object"):
            with self.subTest(mode=mode), self.stores(mode) as store:
                owner, admin = store("staff-a"), store("staff-a", "admin")
                saved = owner.save_profile(self.profile(), operation="create")
                identity = saved["id"]
                for visibility in ("user", "shared"):
                    saved = admin.change_profile_access(identity, {
                        "version": 1, "owner_user_id": "staff-a", "visibility": visibility,
                    }, revision=saved["revision"])
                    before = admin._read_payload("sqag_profiles", "profile_id", identity)
                    for action, access in (
                        ("share", {"version": 1, "owner_user_id": "staff-a", "visibility": "shared"}),
                        ("unshare", {"version": 1, "owner_user_id": "staff-a", "visibility": "user"}),
                        ("transfer", {"version": 1, "owner_user_id": "staff-b", "visibility": "user"}),
                    ):
                        with self.subTest(visibility=visibility, action=action):
                            with mock.patch.object(owner, "_run_storage_transaction", side_effect=AssertionError("Forbidden write")):
                                with self.assertRaises(s.SqagStorageAccessError) as denied:
                                    owner.change_profile_access(identity, access, revision=saved["revision"])
                            self.assertEqual(denied.exception.status, 403)
                            self.assertEqual(denied.exception.reason, "profile_access_denied")
                            self.assertEqual(admin._read_payload("sqag_profiles", "profile_id", identity), before)


if __name__ == "__main__":
    unittest.main()
