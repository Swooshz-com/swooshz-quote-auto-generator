"""Publication subjects preserve admitted storage identities without aliasing owners."""

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import generate_quote
from tests import test_webapp as fixtures
from tests import test_internal_password_auth as auth_fixtures
from tests import test_internal_password_auth_webapp as http_fixtures
from tests import test_publication_proof_reasons as proof_fixtures
from webapp import internal_password_auth as auth
from webapp import server as s


def identity_for(binding=None):
    values = auth_fixtures.policy_values()
    values.update(allowed_admin_emails="", bindings_json=json.dumps([] if binding is None else [{
        "user_id": binding, "email": auth_fixtures.email("operator"), "role": "operator",
    }]))
    identities, _ = auth.InternalAuthPolicy.validate_values(**values)
    return identities[0]


def proof_record(owner):
    inputs = proof_fixtures.PublicationProofReasonsTest().inputs()
    inputs["owner_id"] = owner
    inputs["authority"]["profile"]["id"] = "company:Synthetic_Profile"
    inputs["patch"].update(status={"quote_generated": True})
    inputs["patch"]["draft_state"]["activeSidePanel"] = "output"
    proof = s.quote_session_publication_proof(**inputs)
    metadata = copy.deepcopy(inputs["metadata"] | inputs["patch"])
    metadata["publication"] = {
        "state": "published", "active_publication_id": inputs["publication_id"],
        "run_id": "", "job_id": "", "proof": proof,
    }
    return inputs, json.loads(json.dumps(metadata))


class PublicationOwnerSubjectsTest(unittest.TestCase):
    def assert_readback(self, owner):
        inputs, metadata = proof_record(owner)
        proof = s.quote_session_current_v2_publication_proof(
            metadata, metadata, workspace_id=inputs["workspace_id"],
            owner_id=owner, authority=inputs["authority"],
        )
        self.assertIsNotNone(proof)
        self.assertEqual(proof["subject"]["owner_id"], owner)
        self.assertTrue(s.quote_session_publication_freshness_proof_matches(metadata, metadata))

    def test_unbound_identity_survives_auth_storage_projection_and_proof_readback(self):
        identity = identity_for()
        self.assertRegex(identity.user_id, r"^internal-v1:[0-9a-f]{64}$")
        self.assertEqual(s.privacy_safe_tracking_id(identity.user_id), identity.user_id)
        self.assert_readback(identity.user_id)

    def test_bound_and_legacy_subjects_preserve_exact_identity(self):
        for binding in ("bound-operator", "bound.operator:one", "B" * 128, "B" * 255):
            with self.subTest(binding_length=len(binding)):
                identity = identity_for(binding)
                owner = s.privacy_safe_tracking_id(identity.user_id)
                self.assertEqual(owner, auth.project_binding_id(binding))
                self.assert_readback(owner)
        for owner in ("local-user", "platform-user-123", "Legacy_OWNER", "x"):
            with self.subTest(owner=owner):
                self.assert_readback(owner)

    def test_invalid_subjects_fail_with_owner_reason(self):
        invalid = (None, 1, "", " ", " owner", "owner ", "owner name", "owner\n",
                   "owner\rname", "owner\x00name", "owner\x7fname", "owner\tname",
                   "x" * 129, "wrong/owner", "mailto:owner@example.test", "owner\u00e9")
        for owner in invalid:
            with self.subTest(kind=type(owner).__name__, length=len(owner) if isinstance(owner, str) else 0):
                with self.assertRaises(s.QuotePublicationAuthorityIncomplete) as caught:
                    proof_record(owner)
                self.assertEqual(caught.exception.safe_reason, "owner_id")

    def test_cross_owner_and_noncanonical_requester_are_rejected(self):
        owners = (identity_for().user_id, identity_for("bound-operator").user_id,
                  identity_for("bound.operator:one").user_id)
        for owner in owners:
            inputs, metadata = proof_record(owner)
            for requester in (*owners, "another-owner", owner.upper(), " " + owner, owner + "\n"):
                with self.subTest(owner_format=owner.split(":")[0], same=requester == owner):
                    proof = s.quote_session_current_v2_publication_proof(
                        metadata, metadata, workspace_id=inputs["workspace_id"],
                        owner_id=requester, authority=inputs["authority"],
                    )
                    self.assertEqual(proof is not None, requester == owner)
            for changed in ("another-owner", owner + "\n"):
                tampered = copy.deepcopy(metadata)
                tampered["publication"]["proof"]["subject"]["owner_id"] = changed
                self.assertIsNone(s.quote_session_current_v2_publication_proof(
                    tampered, tampered, workspace_id=inputs["workspace_id"],
                    owner_id=owner, authority=inputs["authority"],
                ))

    def test_resource_grammar_remains_unchanged(self):
        self.assertEqual(s.safe_resource_id(identity_for().user_id, ""), "")
        self.assertEqual(s.safe_resource_id("bound.operator:one", ""), "")
        self.assertEqual(s.safe_resource_id("Legacy_OWNER", ""), "Legacy_OWNER")

    def test_platform_storage_projections_are_preserved(self):
        for value in ("platform.user:one", ":platform-subject", "platform-user",
                      auth_fixtures.email("platform"), "p" * 129):
            with self.subTest(length=len(value)):
                session = {"user": {"platform": {"user": {"userId": value}}}}
                owner = s.platform_user_id_from_auth_session(session)
                self.assertEqual(owner, s.privacy_safe_tracking_id(value))
                self.assert_readback(owner)

    def test_invalid_projected_owner_also_retains_owner_reason(self):
        _, metadata = proof_record("legacy-owner")
        for owner in (None, "", "bad/owner", "owner\n", "x" * 129):
            with self.subTest(kind=type(owner).__name__):
                proof = copy.deepcopy(metadata["publication"]["proof"])
                proof["subject"]["owner_id"] = owner
                with self.assertRaises(s.QuotePublicationAuthorityIncomplete) as caught:
                    s._validate_quote_publication_authority_proof(proof)
                self.assertEqual(caught.exception.safe_reason, "owner_id")


class PublicationOwnerWorkerTest(unittest.TestCase):
    def exercise_worker(self, binding=None):
        data = fixtures.WebappServerTest()
        data.setUp()
        self.addCleanup(data.doCleanups)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_url = "sqlite:///" + (root / "storage.sqlite3").as_posix()
            output_root, tmp_root = root / "output", root / "scratch"
            env = data.hosted_storage_env(SQAG_DATABASE_URL=database_url) | {
                "QUOTE_DATA_ROOT": str(root / "data"), "QUOTE_OUTPUT_ROOT": str(output_root),
                "QUOTE_TMP_ROOT": str(tmp_root),
                "QUOTE_LOG_ROOT": str(s.PROJECT_ROOT / "_logs" / "test" / "publication-owners"),
                "SQAG_INTERNAL_ALLOWED_EMAILS": ";".join(auth_fixtures.email(alias) for alias in ("operator", "peer")),
                "SQAG_INTERNAL_IDENTITY_BINDINGS_JSON": json.dumps([] if binding is None else [{
                    "user_id": binding, "email": auth_fixtures.email("operator"), "role": "operator",
                }]),
                "SQAG_TRACKING_HMAC_KEY": auth_fixtures.policy_values()["password"],
                "SQAG_TRACKING_HMAC_KEY_VERSION": "test-v1",
            }
            http = http_fixtures.InternalPasswordAuthWebappTest()
            http.setUp(extra_env=env)
            self.addCleanup(http.doCleanups)
            backend = s.InMemoryObjectStorageBackend()
            calls = []

            def real_generator(command, **kwargs):
                stream = io.StringIO()
                with mock.patch.object(sys, "argv", ["generate_quote.py", *command[2:]]), mock.patch("sys.stdout", stream):
                    code = generate_quote.main()
                calls.append(code)
                return subprocess.CompletedProcess(command, code, stdout=stream.getvalue(), stderr="")

            with (
                mock.patch.object(s, "configured_object_storage_backend", return_value=backend),
                mock.patch.object(s, "read_dotenv_value", side_effect=fixtures.env_only_dotenv_value),
                mock.patch.object(s.urllib.request, "urlopen", side_effect=AssertionError("EXTERNAL_NETWORK_DENIED")),
                mock.patch.object(s.subprocess, "run", side_effect=real_generator),
                mock.patch.dict(s.JOBS, {}, clear=True),
            ):
                status, headers, _ = http.login(auth_fixtures.email("operator"))
                self.assertEqual(status, 302)
                cookie_value = http.cookie(headers, s.SESSION_COOKIE_NAME)
                session = http.server.internal_auth.validate_session(
                    s.verified_cookie_payload(cookie_value), policy_fingerprint=http.server.internal_policy.fingerprint,
                    current_mode=s.INTERNAL_AUTH_MODE,
                )
                identity = identity_for(binding)
                self.assertEqual(session["user"]["subject"], identity.user_id)
                async_session = s.safe_auth_session_for_async(session)
                s.apply_sqag_storage_migrations(database_url)
                storage = s.app_storage_for_auth_session(async_session)
                self.assertEqual(storage.user_id, auth.project_binding_id(identity.user_id))
                self.assertEqual(storage.workspace_id, session["user"]["account"])
                payload = data._run466_payload("quote-owner-worker", "Synthetic printed graphics", 0, quote_generated=False)
                fixtures.bind_database_publication_authority(storage, payload, suffix="owner-worker")
                row = payload["line_items"][0]
                row.update(source_basis_line_id="reviewed-row", pricing_keyword="", price_mode="Priced", unit_price_override=25)
                row["pricing_authority"] = s.build_pricing_authority(
                    "manual", row, price=25, reference_authority=s.exact_pricing_reference_authority(payload, auth_session=session),
                )
                payload["quote_session"]["draft_state"]["outputRows"] = [copy.deepcopy(row)]
                storage.create_or_update_quote_session(copy.deepcopy(payload))
                with mock.patch.object(s.threading, "Thread") as thread:
                    queued = s.create_job("generate", payload, auth_session=session, requested_job_id="job-owner-worker")
                    self.assertEqual(queued["status"], "running", queued)
                    thread.return_value.start.assert_called_once()
                    dispatch = thread.call_args.kwargs
                self.assertIs(dispatch["target"], s.run_job_worker)
                self.assertIs(dispatch["args"][0], s.finish_generate_job)
                dispatch["target"](*dispatch["args"])
                completed = s.get_job("job-owner-worker", session)
                self.assertEqual(calls, [0])
                diagnostics = [call.args[1] for call in http.logs.call_args_list
                               if len(call.args) > 1 and isinstance(call.args[1], dict)
                               and "failure_reason" in call.args[1]]
                self.assertEqual(completed["status"], "completed", diagnostics)
                reopened = s.app_storage_for_auth_session(session)
                metadata, _ = reopened._read_quote_session_metadata("quote-owner-worker")
                self.assertEqual(metadata["publication"]["proof"]["subject"]["owner_id"], storage.user_id)
                artifact = reopened.quote_session_export_artifact("quote-owner-worker", "xlsx", require_current_proof=True)
                self.assertIsNotNone(artifact)
                with zipfile.ZipFile(io.BytesIO(artifact["content"])) as workbook:
                    self.assertIsNone(workbook.testzip())
                url = completed["result"]["quote_session"]["exports"]["xlsx"]["url"]
                self.assertIn("/download/xlsx?publication=", url)
                cookie = s.SESSION_COOKIE_NAME + "=" + cookie_value
                self.assertEqual(http.http("GET", url, {"Cookie": cookie})[::2], (200, artifact["content"]))
                self.assertIn(http.http("GET", url)[0], {401, 403})
                peer_status, peer_headers, _ = http.login(auth_fixtures.email("peer"))
                self.assertEqual(peer_status, 302)
                peer_cookie = s.SESSION_COOKIE_NAME + "=" + http.cookie(peer_headers, s.SESSION_COOKIE_NAME)
                self.assertEqual(http.http("GET", url, {"Cookie": peer_cookie})[0], 404)
                other = copy.deepcopy(session)
                other["user"]["subject"] = "another-operator"
                peer = s.app_storage_for_auth_session(other)
                self.assertIsNone(peer.quote_session_export_artifact("quote-owner-worker", "xlsx", require_current_proof=True))
                self.assertFalse((output_root / "job-owner-worker").exists())
                self.assertFalse((tmp_root / "job-owner-worker").exists())

    def test_real_worker_publishes_protected_xlsx_for_unbound_identity(self):
        self.exercise_worker()

    def test_real_worker_publishes_protected_xlsx_for_bound_identity(self):
        self.exercise_worker("bound.operator:one")

    def test_real_worker_publishes_protected_xlsx_for_legacy_bound_identity(self):
        self.exercise_worker("bound-operator")


if __name__ == "__main__":
    unittest.main()
