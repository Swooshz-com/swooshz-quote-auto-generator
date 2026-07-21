import contextlib
import datetime as dt
import inspect
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import enforce_forensic_retention
from webapp.forensics import ForensicStore
from webapp import server as webapp

TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "_tmp" / "tests"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)



class Pr140CurrentFiveBlockerTests(unittest.TestCase):
    def platform_auth_session(
        self,
        workspace_id: str,
        user_id: str = "synthetic-owner",
    ) -> dict:
        launch_expiry = (
            webapp.dt.datetime.now(webapp.dt.timezone.utc) + webapp.dt.timedelta(minutes=5)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        context = webapp.safe_platform_launch_context(
            {
                "outcome": "consumed",
                "user": {
                    "userId": user_id,
                    "email": f"{user_id}@example.test",
                    "displayName": f"Synthetic {user_id}",
                    "status": "active",
                },
                "workspace": {
                    "workspaceId": workspace_id,
                    "workspaceSlug": workspace_id,
                    "workspaceName": f"Synthetic {workspace_id}",
                },
                "app": {"appKey": "sqag", "appName": "SQAG"},
                "membershipRole": "owner",
                "launchTokenExpiresAt": launch_expiry,
                "validationGrantId": f"synthetic-grant-{workspace_id}-{user_id}",
            }
        )
        return {"user": webapp.user_from_platform_launch_context(context)}

    def database_env(self, database_url: str) -> dict[str, str]:
        return {
            "APP_MODE": "deploy",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "database",
            "SQAG_DATABASE_URL": database_url,
            "SQAG_TRACKING_HMAC_KEY": "synthetic-current-five-tracking-key",
            "SQAG_TRACKING_HMAC_KEY_VERSION": "test-v1",
        }

    def output_dir(self, root: Path, name: str, content: bytes) -> Path:
        output = root / name
        output.mkdir(parents=True)
        (output / "quotation.xlsx").write_bytes(content)
        return output

    def create_run(
        self,
        database_url: str,
        workspace_id: str,
        *,
        run_id: str,
        job_id: str,
        session_id: str,
        status: str = "completed",
        now: dt.datetime | None = None,
    ) -> None:
        with webapp.sqlite_storage_connection(database_url) as connection:
            store = ForensicStore(connection, workspace_id, "synthetic-owner")
            created = store.record_run_started(
                "generate",
                {"synthetic": True},
                run_id=run_id,
                job_id=job_id,
                now=now,
            )
            self.assertEqual(created, run_id)
            store.finish_run(
                run_id,
                status,
                quote_session_id=session_id,
                canonical_manifest={"artifacts": []},
                now=now,
            )

    def test_current_publication_run_is_retained_while_sibling_exists(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-current-publication"
            session_id = "quote-current-publication"
            run_a = "run-current-publication-a"
            run_b = "run-current-publication-b"
            run_c = "run-current-publication-c"
            env = self.database_env(database_url)
            with mock.patch.dict(os.environ, env, clear=True):
                webapp.apply_sqag_storage_migrations(database_url)
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_a,
                    job_id="job-current-publication-a",
                    session_id=session_id,
                    now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                )
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_b,
                    job_id="job-current-publication-b",
                    session_id=session_id,
                    status="failed",
                )
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    workspace_id,
                    role="admin",
                    user_id="synthetic-owner",
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "publication-a", b"publication-a"),
                    generation_run_id=run_a,
                    generation_job_id="job-current-publication-a",
                )
                with storage.connection() as connection:
                    run_before = connection.execute(
                        "select original_retention_expires_at from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, run_a),
                    ).fetchone()
                    original_expiry = run_before["original_retention_expires_at"]
                    evidence_count = connection.execute(
                        "select count(*) from sqag_generation_evidence "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, run_a),
                    ).fetchone()[0]
                    audit_count = connection.execute(
                        "select count(*) from sqag_audit_events "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, run_a),
                    ).fetchone()[0]

                retention_output = io.StringIO()
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "enforce_forensic_retention.py",
                            "--workspace-id",
                            workspace_id,
                            "--database-url",
                            database_url,
                            "--apply",
                            "--now",
                            "2024-01-02T00:00:00+00:00",
                        ],
                    ),
                    contextlib.redirect_stdout(retention_output),
                ):
                    self.assertEqual(enforce_forensic_retention._main(), 0)

                retention_report = json.loads(retention_output.getvalue())
                self.assertEqual(retention_report["publication_retained"], 1)
                self.assertEqual(retention_report["deleted"], 0)

                with storage.connection() as connection:
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_generation_runs "
                            "where workspace_id = ? and run_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        1,
                    )
                    retained = connection.execute(
                        "select original_retention_expires_at from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, run_a),
                    ).fetchone()
                    self.assertEqual(
                        retained["original_retention_expires_at"], original_expiry
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_generation_evidence "
                            "where workspace_id = ? and run_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        evidence_count,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_audit_events "
                            "where workspace_id = ? and run_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        audit_count,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_deletion_receipts "
                            "where workspace_id = ? and record_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        0,
                    )
                artifact = storage.quote_session_export_artifact(session_id, "xlsx")
                self.assertIsNotNone(artifact)
                self.assertEqual(artifact["content"], b"publication-a")

                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_c,
                    job_id="job-current-publication-c",
                    session_id=session_id,
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "publication-c", b"publication-c"),
                    generation_run_id=run_c,
                    generation_job_id="job-current-publication-c",
                )
                second_output = io.StringIO()
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "enforce_forensic_retention.py",
                            "--workspace-id",
                            workspace_id,
                            "--database-url",
                            database_url,
                            "--apply",
                            "--now",
                            "2024-01-02T00:00:00+00:00",
                        ],
                    ),
                    contextlib.redirect_stdout(second_output),
                ):
                    self.assertEqual(enforce_forensic_retention._main(), 0)
                second_report = json.loads(second_output.getvalue())
                self.assertGreater(second_report["deleted"], 0)
                with storage.connection() as connection:
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_generation_runs "
                            "where workspace_id = ? and run_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_quote_publication_versions "
                            "where workspace_id = ? and run_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_deletion_receipts "
                            "where workspace_id = ? and record_id = ?",
                            (workspace_id, run_a),
                        ).fetchone()[0],
                        1,
                    )
                artifact = storage.quote_session_export_artifact(session_id, "xlsx")
                self.assertEqual(artifact["content"], b"publication-c")

    def test_metadata_only_publication_lookup_does_not_read_or_hash_blob(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-metadata-only"
            session_id = "quote-metadata-only"
            run_id = "run-metadata-only"
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    workspace_id,
                    role="admin",
                    user_id="synthetic-owner",
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "metadata", b"metadata-only"),
                    generation_run_id=run_id,
                    generation_job_id="job-metadata-only",
                )
                with mock.patch.object(
                    webapp,
                    "artifact_checksum",
                    side_effect=AssertionError(
                        "metadata-only lookup hashed artifact bytes"
                    ),
                ):
                    artifact = storage._publication_version_artifact(
                        session_id,
                        run_id,
                        "xlsx",
                        include_content=False,
                    )
                self.assertIsNotNone(artifact)
                self.assertNotIn("content", artifact)

                with storage.connection() as connection:
                    connection.execute(
                        "update sqag_quote_publication_artifacts set content_blob = ? "
                        "where workspace_id = ? and run_id = ? and artifact_kind = ?",
                        (b"corrupt", workspace_id, run_id, "xlsx"),
                    )
                    connection.commit()
                self.assertIsNotNone(
                    storage._publication_version_artifact(
                        session_id, run_id, "xlsx", include_content=False
                    )
                )
                self.assertIsNone(
                    storage._publication_version_artifact(
                        session_id, run_id, "xlsx", include_content=True
                    )
                )

            source = inspect.getsource(
                webapp.DatabaseSqagStorage._publication_version_artifact
            )
            self.assertNotIn(
                "select * from sqag_quote_publication_artifacts",
                source,
            )

    def test_active_run_hold_blocks_owner_quote_session_deletion(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-session-hold"
            session_id = "quote-session-hold"
            run_id = "run-session-hold"
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_id,
                    job_id="job-session-hold",
                    session_id=session_id,
                )
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    workspace_id,
                    role="owner",
                    user_id="synthetic-owner",
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "held", b"held-publication"),
                    generation_run_id=run_id,
                    generation_job_id="job-session-hold",
                )
                with webapp.sqlite_storage_connection(database_url) as connection:
                    store = ForensicStore(
                        connection, workspace_id, "synthetic-counsel"
                    )
                    self.assertTrue(
                        store.set_legal_hold(
                            "sqag_generation_runs",
                            "run_id",
                            run_id,
                            True,
                        )
                    )

                self.assertFalse(storage.delete_quote_session(session_id))
                self.assertIsNotNone(storage.get_quote_session(session_id))
                self.assertEqual(
                    storage.quote_session_export_artifact(session_id, "xlsx")[
                        "content"
                    ],
                    b"held-publication",
                )

    def test_child_legal_holds_block_session_delete_until_released(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-child-hold"
            session_id = "quote-child-hold"
            run_id = "run-child-hold"
            auth_session = self.platform_auth_session(workspace_id)
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_id,
                    job_id="job-child-hold",
                    session_id=session_id,
                )
                storage = webapp.app_storage_for_auth_session(auth_session)
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "child-held", b"child-held"),
                    generation_run_id=run_id,
                    generation_job_id="job-child-hold",
                )
                feedback = webapp.submit_feedback_for_auth_session(
                    {
                        "category": "bug",
                        "title": "Synthetic child hold",
                        "message": "Synthetic child hold.",
                        "session_id": session_id,
                    },
                    auth_session,
                )
                with storage.connection() as connection:
                    evidence_id = connection.execute(
                        "select evidence_id from sqag_generation_evidence "
                        "where workspace_id = ? and run_id = ? order by evidence_id limit 1",
                        (workspace_id, run_id),
                    ).fetchone()["evidence_id"]
                    audit_id = connection.execute(
                        "select event_id from sqag_audit_events "
                        "where workspace_id = ? and run_id = ? order by event_id limit 1",
                        (workspace_id, run_id),
                    ).fetchone()["event_id"]
                    history_id = connection.execute(
                        "select history_id from sqag_feedback_status_history "
                        "where workspace_id = ? and feedback_id = ? limit 1",
                        (workspace_id, feedback["feedback_id"]),
                    ).fetchone()["history_id"]

                targets = (
                    ("sqag_generation_evidence", "evidence_id", evidence_id),
                    ("sqag_audit_events", "event_id", audit_id),
                    ("sqag_feedback_status_history", "history_id", history_id),
                )
                for table, column, record_id in targets:
                    with self.subTest(table=table):
                        with storage.connection() as connection:
                            store = ForensicStore(
                                connection, workspace_id, "synthetic-counsel"
                            )
                            self.assertTrue(
                                store.set_legal_hold(
                                    table, column, record_id, True
                                )
                            )
                        self.assertFalse(storage.delete_quote_session(session_id))
                        self.assertIsNotNone(storage.get_quote_session(session_id))
                        self.assertEqual(
                            storage.quote_session_export_artifact(
                                session_id, "xlsx"
                            )["content"],
                            b"child-held",
                        )
                        with storage.connection() as connection:
                            store = ForensicStore(
                                connection, workspace_id, "synthetic-counsel"
                            )
                            self.assertTrue(
                                store.set_legal_hold(
                                    table, column, record_id, False
                                )
                            )

                self.assertTrue(storage.delete_quote_session(session_id))
                self.assertIsNone(storage.get_quote_session(session_id))

    def test_blocked_generation_preserves_validated_existing_session(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-blocked-session"
            session_id = "quote-blocked-session"
            auth_session = self.platform_auth_session(workspace_id)
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.app_storage_for_auth_session(auth_session)
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                )
                with (
                    mock.patch.object(
                        webapp,
                        "generation_payload_with_profile_defaults",
                        side_effect=lambda value, **_kwargs: value,
                    ),
                    mock.patch.object(
                        webapp,
                        "validate_generation_payload",
                        return_value=["Synthetic blocked generation."],
                    ),
                ):
                    result = webapp.run_quote_job(
                        {"quote_session": {"session_id": session_id}},
                        output_root=root / "out",
                        tmp_root=root / "tmp",
                        job_id="job-blocked-session",
                        auth_session=auth_session,
                    )

                run_id = result["generation_run_id"]
                with storage.connection() as connection:
                    row = connection.execute(
                        "select quote_session_id from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, run_id),
                    ).fetchone()
                self.assertEqual(row["quote_session_id"], session_id)
                context = webapp.feedback_context_for_auth_session(
                    {"run_id": run_id, "session_id": session_id},
                    auth_session,
                )
                self.assertEqual(context["link_type"], "generation_run")
                self.assertEqual(context["run_id"], run_id)

                async_result = webapp.create_job(
                    "generate",
                    {"quote_session": {"session_id": session_id}},
                    auth_session=auth_session,
                    requested_job_id="job-blocked-session-async",
                )
                async_run_id = async_result["generation_run_id"]
                with storage.connection() as connection:
                    async_row = connection.execute(
                        "select quote_session_id from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, async_run_id),
                    ).fetchone()
                self.assertEqual(async_row["quote_session_id"], session_id)

                submitted = webapp.submit_feedback_for_auth_session(
                    {
                        "category": "bug",
                        "title": "Synthetic blocked run",
                        "message": "Synthetic blocked run.",
                        "run_id": async_run_id,
                        "session_id": session_id,
                    },
                    auth_session,
                )
                with webapp.forensic_store_for_auth_session(auth_session) as store:
                    report = store._feedback(submitted["feedback_id"])
                self.assertEqual(report["run_id"], async_run_id)
                self.assertEqual(
                    report["link_resolution_source"], "current_blocked_run"
                )

    def test_generation_session_mismatch_and_cross_workspace_session_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-session-validation"
            other_workspace_id = "workspace-session-validation-other"
            session_a = "quote-session-validation-a"
            session_b = "quote-session-validation-b"
            foreign_session = "quote-session-validation-foreign"
            auth_session = self.platform_auth_session(workspace_id)
            other_auth_session = self.platform_auth_session(other_workspace_id)
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.app_storage_for_auth_session(auth_session)
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_a}}
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_b}}
                )
                webapp.app_storage_for_auth_session(
                    other_auth_session
                ).create_or_update_quote_session(
                    {"quote_session": {"session_id": foreign_session}}
                )

                run_id = webapp.begin_generation_forensics(
                    "generate",
                    {"quote_session": {"session_id": session_a}},
                    auth_session,
                    job_id="job-session-validation-mismatch",
                    claim_status="running",
                    validated_session_id=session_a,
                )
                mismatch = webapp.finish_generation_forensics(
                    run_id,
                    {
                        "status": "completed",
                        "quote_session": {"session_id": session_b},
                    },
                    auth_session,
                    validated_session_id=session_a,
                    canonical_manifest={
                        "artifacts": [
                            {
                                "name": "quotation.xlsx",
                                "bytes": 1,
                                "sha256": "a" * 64,
                            }
                        ],
                        "artifacts_durable": True,
                    },
                )
                self.assertEqual(mismatch["status"], "failed")
                with storage.connection() as connection:
                    run = connection.execute(
                        "select status, quote_session_id from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, run_id),
                    ).fetchone()
                    manifest = json.loads(
                        connection.execute(
                            "select evidence_json from sqag_generation_evidence "
                            "where workspace_id = ? and run_id = ? "
                            "and evidence_type = 'generation_manifest'",
                            (workspace_id, run_id),
                        ).fetchone()["evidence_json"]
                    )
                self.assertEqual(run["status"], "failed")
                self.assertEqual(run["quote_session_id"], session_a)
                self.assertEqual(manifest["artifacts"], [])
                self.assertFalse(manifest["artifacts_durable"])

                foreign_result = webapp.create_job(
                    "generate",
                    {"quote_session": {"session_id": foreign_session}},
                    auth_session=auth_session,
                    requested_job_id="job-session-validation-foreign",
                )
                with storage.connection() as connection:
                    foreign_run = connection.execute(
                        "select quote_session_id from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ?",
                        (workspace_id, foreign_result["generation_run_id"]),
                    ).fetchone()
                self.assertIsNone(foreign_run["quote_session_id"])

    def test_session_only_feedback_is_bound_to_report_time_publication(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-feedback-publication"
            session_id = "quote-feedback-publication"
            run_a = "run-feedback-publication-a"
            run_b = "run-feedback-publication-b"
            run_c = "run-feedback-publication-c"
            auth_session = self.platform_auth_session(workspace_id)
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_a,
                    job_id="job-feedback-publication-a",
                    session_id=session_id,
                )
                storage = webapp.app_storage_for_auth_session(auth_session)
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "feedback-a", b"feedback-a"),
                    generation_run_id=run_a,
                    generation_job_id="job-feedback-publication-a",
                )
                submitted = webapp.submit_feedback_for_auth_session(
                    {
                        "category": "bug",
                        "title": "Synthetic publication feedback",
                        "message": "Synthetic publication feedback.",
                        "session_id": session_id,
                    },
                    auth_session,
                )
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_b,
                    job_id="job-feedback-publication-b",
                    session_id=session_id,
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "feedback-b", b"feedback-b"),
                    generation_run_id=run_b,
                    generation_job_id="job-feedback-publication-b",
                )

                submitted_b = webapp.submit_feedback_for_auth_session(
                    {
                        "category": "bug",
                        "title": "Synthetic publication feedback B",
                        "message": "Synthetic publication feedback B.",
                        "session_id": session_id,
                    },
                    auth_session,
                )
                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_c,
                    job_id="job-feedback-publication-c",
                    session_id=session_id,
                    status="failed",
                )

                with webapp.forensic_store_for_auth_session(auth_session) as store:
                    report = store._feedback(submitted["feedback_id"])
                    report_b = store._feedback(submitted_b["feedback_id"])
                    resolved = store.resolve_feedback_evidence_run(
                        report,
                        publication_context_factory=lambda: {
                            "state": "published",
                            "run_id": run_b,
                        },
                    )
                self.assertEqual(report["run_id"], run_a)
                self.assertEqual(report["publication_version_id"], run_a)
                self.assertEqual(
                    report["link_resolution_source"], "current_published_run"
                )
                self.assertTrue(report["link_resolved_at"])
                self.assertEqual(resolved, run_a)
                self.assertEqual(report_b["run_id"], run_b)
                self.assertEqual(report_b["publication_version_id"], run_b)

                with storage.connection() as connection:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "update sqag_feedback set run_id = ? "
                            "where workspace_id = ? and feedback_id = ?",
                            (run_c, workspace_id, submitted["feedback_id"]),
                        )
                    connection.rollback()

    def test_unbound_session_feedback_does_not_bind_to_future_publication(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            workspace_id = "workspace-feedback-unbound"
            session_id = "quote-feedback-unbound"
            run_id = "run-feedback-unbound-future"
            auth_session = self.platform_auth_session(workspace_id)
            with mock.patch.dict(
                os.environ, self.database_env(database_url), clear=True
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.app_storage_for_auth_session(auth_session)
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}}
                )
                submitted = webapp.submit_feedback_for_auth_session(
                    {
                        "category": "bug",
                        "title": "Synthetic unbound feedback",
                        "message": "Synthetic unbound feedback.",
                        "session_id": session_id,
                    },
                    auth_session,
                )
                with webapp.forensic_store_for_auth_session(auth_session) as store:
                    before = store._feedback(submitted["feedback_id"])
                self.assertIsNone(before["run_id"])
                self.assertIsNone(before["publication_version_id"])
                self.assertEqual(
                    before["link_resolution_source"], "session_without_run"
                )

                self.create_run(
                    database_url,
                    workspace_id,
                    run_id=run_id,
                    job_id="job-feedback-unbound-future",
                    session_id=session_id,
                )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=self.output_dir(root, "future", b"future"),
                    generation_run_id=run_id,
                    generation_job_id="job-feedback-unbound-future",
                )
                with webapp.forensic_store_for_auth_session(auth_session) as store:
                    after = store._feedback(submitted["feedback_id"])
                    with self.assertRaisesRegex(
                        LookupError, "Forensic evidence is not available"
                    ):
                        store.resolve_feedback_evidence_run(after)
                self.assertIsNone(after["run_id"])
                self.assertIsNone(after["publication_version_id"])


if __name__ == "__main__":
    unittest.main()
