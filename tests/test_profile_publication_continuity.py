"""Historical profile binding controls; full generation remains a separate journey."""
import contextlib
import copy
import hashlib
import json
import unittest
from unittest import mock

from webapp import server as s
from webapp.profile_access import historical_profile_authority, content_profile
from webapp.forensics import EVIDENCE_SCHEMA_VERSION


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ProfilePublicationContinuityTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"id": "profile-synthetic", "source": "company", "defaults": {}}
        self.manifest = {"schema": EVIDENCE_SCHEMA_VERSION, "generation_run_id": "run-synthetic",
                         "job_id": "job-synthetic", "workspace_id": "workspace-synthetic",
                         "profile": {"snapshot": self.snapshot, "sha256": checksum(self.snapshot)},
                         "layout_template": {"sha256": "a" * 64},
                         "layout_rules": {"snapshot": {}, "sha256": checksum({})}}
        self.context = {"run_id": "run-synthetic", "job_id": "job-synthetic", "workspace_id": "workspace-synthetic"}

    def test_metadata_does_not_change_content_digest_and_snapshot_does_not_grant_live_access(self):
        before = checksum(self.snapshot)
        for owner, visibility in (("staff-a", "user"), ("staff-b", "user"), (None, "shared")):
            with_metadata = {**self.snapshot, "_access": {"version": 1, "owner_user_id": owner, "visibility": visibility}, "_revision": 8}
            self.assertEqual(checksum(content_profile(with_metadata)), before)
        authority = historical_profile_authority(self.manifest, **self.context)
        self.assertEqual(authority["digest"], before)
        from webapp.profile_access import ProfilePolicy
        self.assertFalse(ProfilePolicy("staff-a", "operator").can_read({**self.snapshot,
            "_access": {"version": 1, "owner_user_id": "staff-b", "visibility": "user"}}))

    def test_snapshot_tamper_missing_wrong_run_job_workspace_are_rejected(self):
        for key in self.context:
            self.assertEqual(historical_profile_authority(self.manifest, **{**self.context, key: "wrong"}), {})
        for key in ("profile", "layout_template", "layout_rules"):
            manifest = copy.deepcopy(self.manifest)
            manifest.pop(key)
            self.assertEqual(historical_profile_authority(manifest, **self.context), {})
        tampered = copy.deepcopy(self.manifest)
        tampered["profile"]["snapshot"]["label"] = "Tampered"
        self.assertEqual(historical_profile_authority(tampered, **self.context), {})

    def test_storage_requires_verified_persisted_version_and_manifest(self):
        profile = historical_profile_authority(self.manifest, **self.context)
        proof = {"profile": profile, "pricing_reference": {"id": "reference-synthetic", "source": "company", "digest": "b" * 64}}
        metadata = {"session_id": "quote-synthetic", "publication": {"run_id": "run-synthetic", "job_id": "job-synthetic", "proof": proof}}
        version = {"state": "published", "session_id": "quote-synthetic", "metadata_json": json.dumps(metadata)}
        raw = json.dumps(self.manifest)
        evidence = {"evidence_type": "generation_manifest", "evidence_json": raw, "evidence_sha256": hashlib.sha256(raw.encode()).hexdigest()}
        run = {"job_id": "job-synthetic", "quote_session_id": "quote-synthetic"}
        connection = mock.MagicMock()
        def execute(sql, params):
            self.assertIn("workspace_id = ?", sql)
            self.assertEqual(params, ("workspace-synthetic", "run-synthetic"))
            return mock.Mock(fetchone=lambda: run, fetchall=lambda: [evidence])
        connection.execute.side_effect = execute
        storage = object.__new__(s.DatabaseSqagStorage)
        storage.workspace_id = "workspace-synthetic"
        @contextlib.contextmanager
        def connected():
            yield connection
        with mock.patch.object(storage, "connection", connected), mock.patch.object(storage, "_publication_version_row", return_value=version):
            self.assertEqual(storage.historical_publication_profile(metadata), profile)
            evidence["evidence_sha256"] = "0" * 64
            self.assertEqual(storage.historical_publication_profile(metadata), {})
        self.assertTrue(s.quote_session_publication_authority_matches(metadata, proof))
        changed_pricing = copy.deepcopy(proof)
        changed_pricing["pricing_reference"]["digest"] = "c" * 64
        self.assertFalse(s.quote_session_publication_authority_matches(metadata, changed_pricing))
        changed_profile = copy.deepcopy(proof)
        changed_profile["profile"]["layout_digest"] = "d" * 64
        self.assertFalse(s.quote_session_publication_authority_matches(metadata, changed_profile))


if __name__ == "__main__":
    unittest.main()
