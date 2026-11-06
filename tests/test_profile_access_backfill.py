"""Pure offline inventories; no DB connection or customer profile fixtures."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.backfill_profile_access import OfflineBackfill, digest, main
from webapp.profile_access import content_profile


class ProfileAccessBackfillTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"workspace_id": "workspace-synthetic", "profiles": [
            {"id": "profile-a", "defaults": {"company": {"name": "Synthetic Company"}}},
            {"id": "profile-b", "defaults": {}},
            {"id": "deleted", "_sqag_deleted": True},
        ]}
        self.manifest = {"workspace_id": "workspace-synthetic", "assignments": [
            {"profile_id": "profile-a", "access": {"version": 1, "owner_user_id": "staff-a", "visibility": "user"}},
            {"profile_id": "profile-b", "access": {"version": 1, "owner_user_id": None, "visibility": "shared"}},
        ]}

    def test_plan_is_pure_idempotent_reversible_and_content_preserving(self):
        original = copy.deepcopy(self.snapshot)
        output, receipt, listing = OfflineBackfill(self.snapshot).plan(self.manifest)
        self.assertEqual(self.snapshot, original)
        self.assertNotIn("defaults", json.dumps(receipt))
        self.assertTrue(listing[0]["narrows_workspace_visibility"])
        self.assertFalse(listing[1]["narrows_workspace_visibility"])
        for before, after in zip(original["profiles"], output["profiles"]):
            self.assertEqual(digest(content_profile(before)), digest(content_profile(after)))
        self.assertEqual(OfflineBackfill(output).plan(self.manifest)[1]["changes"], [])
        self.assertEqual(OfflineBackfill(output).restore(receipt), original)
        output["profiles"][0]["description"] = "Intervening legitimate edit"
        with self.assertRaisesRegex(ValueError, "Intervening"):
            OfflineBackfill(output).restore(receipt)

    def test_ambiguous_unknown_deleted_malformed_and_workspace_fail_closed(self):
        for edit in ("unresolved", "workspace", "unknown", "deleted", "malformed", "duplicate"):
            manifest = copy.deepcopy(self.manifest)
            if edit == "unresolved":
                manifest["assignments"].pop()
            elif edit == "workspace":
                manifest["workspace_id"] = "workspace-other"
            elif edit in {"unknown", "deleted"}:
                manifest["assignments"][0]["profile_id"] = edit
            elif edit == "malformed":
                manifest["assignments"][0]["access"]["owner_user_id"] = None
            else:
                manifest["assignments"].append(copy.deepcopy(manifest["assignments"][0]))
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                OfflineBackfill(self.snapshot).plan(manifest)

    def test_cli_dry_run_never_writes_and_apply_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, manifest = root / "inventory.json", root / "manifest.json"
            inventory.write_text(json.dumps(self.snapshot), encoding="utf-8")
            manifest.write_text(json.dumps(self.manifest), encoding="utf-8")
            original = inventory.read_bytes()
            args = ["--inventory", str(inventory), "--manifest", str(manifest)]
            output, receipt = root / "mapped.json", root / "rollback.json"
            with mock.patch("scripts.backfill_profile_access.write_new", side_effect=AssertionError("Dry-run write")), contextlib.redirect_stdout(io.StringIO()) as listing:
                self.assertEqual(main(args), 0)
            self.assertTrue(json.loads(listing.getvalue())["dry_run"])
            self.assertEqual(inventory.read_bytes(), original)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args + ["--apply", "--output", str(output), "--receipt", str(receipt)]), 0)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args + ["--apply", "--output", str(inventory), "--receipt", str(root / "new.json")])
            restored = root / "restored.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--inventory", str(output), "--rollback", str(receipt), "--apply", "--output", str(restored)])
            self.assertEqual(json.loads(restored.read_text()), self.snapshot)


if __name__ == "__main__":
    unittest.main()
