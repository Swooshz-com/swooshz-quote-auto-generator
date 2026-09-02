import argparse
import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import enforce_forensic_retention as retention_worker
from webapp import server as webapp
from webapp.forensics import (
    MAX_GENERATION_MANIFEST_BYTES,
    ForensicStore,
    bounded_digest_json,
)


ROOT = Path(__file__).resolve().parents[1]
FORENSIC_MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"
TELEMETRY_MIGRATION = ROOT / "migrations" / "009_telemetry_events.sql"
TEST_TMP_ROOT = Path("C:/tmp") if os.name == "nt" and Path("C:/tmp").is_dir() else None


class Pr140FiveBlockerRedTest(unittest.TestCase):
    def test_oversized_async_generation_is_accepted_into_one_blocked_run(self):
        payload = {
            "line_items": [
                {"description": "x" * (webapp.MAX_FORENSIC_REQUEST_EVIDENCE_BYTES + 1)}
            ]
        }
        with (
            mock.patch.object(webapp, "begin_generation_forensics", return_value="run-oversized123") as begin,
            mock.patch.object(
                webapp,
                "finish_generation_forensics",
                side_effect=lambda run_id, result, *_args, **_kwargs: {
                    **result,
                    "generation_run_id": run_id,
                },
            ) as finish,
        ):
            result = webapp.create_job(
                "generate",
                payload,
                requested_job_id="job-oversized123",
            )

        begin.assert_called_once()
        finish.assert_called_once()
        self.assertEqual(result["generation_run_id"], "run-oversized123")

    def test_support_feedback_detail_has_a_dedicated_rate_limit(self):
        route = webapp.rate_limit_path_key("/api/support/feedback/feedback-synthetic")
        self.assertEqual(route, "/api/support/feedback/:id")
        self.assertIn(route, webapp.POST_RATE_LIMITS)
        self.assertGreater(webapp.POST_RATE_LIMITS[route], webapp.POST_RATE_LIMITS["/api/support/feedback/:id/evidence"])
        self.assertLess(webapp.POST_RATE_LIMITS[route], webapp.POST_RATE_LIMITS["/api/support/feedback/:id/status"])

    def test_canonical_generation_evidence_is_compacted_and_hard_bounded(self):
        manifest = {
            "generation_schema_version": 1,
            "job_id": "job-evidence-bound",
            "normalized_brief": {},
            "approved_basis": [],
            "output_rows": [],
            "profile": {"snapshot": {}},
            "pricing_reference": {
                "snapshot": {
                    "items": [
                        {
                            "description": "private-marker-" + ("x" * 10000)
                        }
                        for _index in range(140)
                    ]
                }
            },
            "layout_rules": {"snapshot": {}},
            "artifacts": [],
            "artifacts_durable": False,
        }
        compacted = webapp.compact_generation_canonical_manifest(manifest)
        self.assertTrue(compacted["evidence_compaction"]["compacted"])
        self.assertIn(
            "pricing_reference.snapshot",
            compacted["evidence_compaction"]["compacted_fields"],
        )
        self.assertTrue(
            compacted["pricing_reference"]["snapshot"]["snapshot_compacted"]
        )
        enveloped = {
            **compacted,
            "schema": "synthetic",
            "generation_run_id": "run-evidence-bound",
            "workspace_id": "workspace-five",
            "actor_tracking_id": "pid-v1-five",
            "actor_key_version": "v1",
            "terminal_state": "completed",
            "terminal_at": "2026-01-01T00:00:00Z",
        }
        body, _digest = bounded_digest_json(enveloped)
        self.assertLessEqual(
            len(body.encode("utf-8")), MAX_GENERATION_MANIFEST_BYTES
        )
        self.assertNotIn("private-marker", body)
        with self.assertRaisesRegex(ValueError, "canonical size limit"):
            bounded_digest_json(
                {"raw": "x" * (MAX_GENERATION_MANIFEST_BYTES + 1)}
            )


    def test_database_retention_worker_deletes_expired_sessionless_run(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            database_url = (
                f"sqlite:///{(Path(tmp) / 'retention.sqlite3').as_posix()}"
            )
            env = {
                "APP_MODE": "local",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "database",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    "workspace-five",
                    role="admin",
                    user_id="retention-worker",
                )
                opened = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
                with storage.connection() as connection:
                    store = ForensicStore(
                        connection, "workspace-five", "retention-worker"
                    )
                    run_id = store.record_run_started(
                        "generate", {"image_count": 1}, now=opened
                    )
                    store.finish_run(
                        run_id,
                        "blocked",
                        result_summary={"status": "blocked"},
                        canonical_manifest={
                            "generator_executed": False,
                            "artifacts": [],
                        },
                        now=opened,
                    )
                args = argparse.Namespace(
                    workspace_id="workspace-five",
                    database_url=database_url,
                    use_configured_database=False,
                    apply=True,
                    dry_run=False,
                    batch_size=10,
                    now="2024-01-01T00:00:00Z",
                )
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        retention_worker, "parse_args", return_value=args
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(retention_worker._main(), 0)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["status"], "completed")
                self.assertGreaterEqual(report["deleted"], 1)
                self.assertEqual(report["failed"], 0)
                with storage.connection() as connection:
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_generation_runs "
                            "where workspace_id = ? and run_id = ?",
                            ("workspace-five", run_id),
                        ).fetchone()[0],
                        0,
                    )


    def test_database_retention_worker_deletes_current_publication_with_session(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'retention.sqlite3').as_posix()}"
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "quotation.xlsx").write_bytes(b"synthetic-current")
            env = {
                "APP_MODE": "local",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "database",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    "workspace-five",
                    role="admin",
                    user_id="retention-worker",
                )
                opened = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
                run_id = "run-current-expired"
                session_id = "quote-current-expired"
                with storage.connection() as connection:
                    store = ForensicStore(
                        connection, "workspace-five", "retention-worker"
                    )
                    store.record_run_started(
                        "generate",
                        {"image_count": 1},
                        run_id=run_id,
                        now=opened,
                    )
                    store.finish_run(
                        run_id,
                        "completed",
                        quote_session_id=session_id,
                        result_summary={"status": "completed"},
                        canonical_manifest={
                            "generator_executed": True,
                            "artifacts": [],
                        },
                        now=opened,
                    )
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    result={"status": "completed"},
                    output_dir=output_dir,
                    generation_run_id=run_id,
                    generation_job_id="job-current-expired",
                )
                args = argparse.Namespace(
                    workspace_id="workspace-five",
                    database_url=database_url,
                    use_configured_database=False,
                    apply=True,
                    dry_run=False,
                    batch_size=10,
                    now="2024-01-01T00:00:00Z",
                )
                stdout = io.StringIO()
                with (
                    mock.patch.object(
                        retention_worker, "parse_args", return_value=args
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(retention_worker._main(), 0)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["failed"], 0)
                with storage.connection() as connection:
                    for table in (
                        "sqag_generation_runs",
                        "sqag_quote_sessions",
                        "sqag_quote_publication_versions",
                        "sqag_quote_publication_artifacts",
                    ):
                        self.assertEqual(
                            connection.execute(
                                f"select count(*) from {table} where workspace_id = ?",
                                ("workspace-five",),
                            ).fetchone()[0],
                            0,
                        )

    def test_reconciled_abandoned_run_has_a_canonical_manifest(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(FORENSIC_MIGRATION.read_text(encoding="utf-8"))
        connection.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
        try:
            store = ForensicStore(connection, "workspace-five", "pid-v1-five")
            opened = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            run_id = store.record_run_started(
                "generate",
                {"schema": "synthetic-request", "approved_basis": ["private-marker"]},
                job_id="job-abandoned123",
                now=opened,
            )
            self.assertTrue(store.set_run_state(run_id, "queued", expected_status="received"))

            self.assertEqual(
                store.reconcile_non_terminal_runs(
                    now=opened + dt.timedelta(hours=1),
                    stale_after_seconds=60,
                ),
                1,
            )
            manifest_count = connection.execute(
                "select count(*) from sqag_generation_evidence where workspace_id = ? and run_id = ? and evidence_type = 'generation_manifest'",
                ("workspace-five", run_id),
            ).fetchone()[0]
            self.assertEqual(manifest_count, 1)
            manifest = json.loads(
                connection.execute(
                    "select evidence_json from sqag_generation_evidence "
                    "where workspace_id = ? and run_id = ? and evidence_type = ?",
                    ("workspace-five", run_id, "generation_manifest"),
                ).fetchone()[0]
            )
            self.assertEqual(manifest["terminal_status"], "abandoned")
            self.assertEqual(manifest["artifacts"], [])
            self.assertNotIn("private-marker", json.dumps(manifest))
            self.assertEqual(
                store.reconcile_non_terminal_runs(
                    now=opened + dt.timedelta(hours=2), stale_after_seconds=60,
                ),
                0,
            )
            audit_count = connection.execute(
                "select count(*) from sqag_audit_events where workspace_id = ? "
                "and run_id = ? and event_type = ?",
                ("workspace-five", run_id, "generation_abandoned"),
            ).fetchone()[0]
            self.assertEqual(audit_count, 1)

            active_run = store.record_run_started(
                "generate", {"schema": "synthetic-request"},
                job_id="job-active123", now=opened,
            )
            self.assertTrue(store.set_run_state(active_run, "running", expected_status="received"))
            self.assertEqual(
                store.reconcile_non_terminal_runs(
                    active_job_ids=["job-active123"],
                    now=opened + dt.timedelta(hours=2),
                    stale_after_seconds=60,
                ),
                0,
            )

            rollback_run = store.record_run_started(
                "generate", {"schema": "synthetic-request"},
                job_id="job-rollback123", now=opened,
            )
            self.assertTrue(store.set_run_state(rollback_run, "queued", expected_status="received"))
            original_append = store.append_evidence
            with mock.patch.object(store, "append_evidence", side_effect=RuntimeError("synthetic")):
                with self.assertRaisesRegex(RuntimeError, "synthetic"):
                    store.reconcile_non_terminal_runs(
                        active_job_ids=["job-active123"],
                        now=opened + dt.timedelta(hours=2),
                        stale_after_seconds=60,
                    )
            status = connection.execute(
                "select status from sqag_generation_runs where workspace_id = ? and run_id = ?",
                ("workspace-five", rollback_run),
            ).fetchone()[0]
            self.assertEqual(status, "queued")
            store.append_evidence = original_append
            self.assertEqual(
                store.reconcile_non_terminal_runs(
                    active_job_ids=["job-active123"],
                    now=opened + dt.timedelta(hours=2),
                    stale_after_seconds=60,
                ),
                1,
            )
        finally:
            connection.close()

    def test_failed_replacement_staging_keeps_published_bytes_online(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            published_dir = root / "published"
            replacement_dir = root / "replacement"
            published_dir.mkdir()
            replacement_dir.mkdir()
            published_bytes = b"synthetic-published-version-a"
            replacement_bytes = b"synthetic-staged-version-b"
            (published_dir / "quotation.xlsx").write_bytes(published_bytes)
            (replacement_dir / "quotation.xlsx").write_bytes(replacement_bytes)
            env = {
                "APP_MODE": "deploy",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "database",
                "SQAG_DATABASE_URL": database_url,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    "workspace-five",
                    role="admin",
                    user_id="synthetic-user",
                )
                payload = {"quote_session": {"session_id": "quote-versioned123"}}
                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=published_dir,
                    generation_run_id="run-published-a",
                    generation_job_id="job-published-a",
                )
                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=replacement_dir,
                    publish=False,
                    generation_run_id="run-staged-b",
                    generation_job_id="job-staged-b",
                )

                current = storage.quote_session_export_artifact("quote-versioned123", "xlsx")
                self.assertIsNotNone(current)
                self.assertEqual(current["content"], published_bytes)

    def test_database_replacement_failure_and_promotion_are_version_safe(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            env = {
                "APP_MODE": "deploy",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "database",
                "SQAG_DATABASE_URL": database_url,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url, "workspace-five", role="admin", user_id="synthetic-user",
                )
                payload = {"quote_session": {"session_id": "quote-versioned-promote"}}

                def output(name, content):
                    path = root / name
                    path.mkdir()
                    (path / "quotation.xlsx").write_bytes(content)
                    return path

                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("a", b"synthetic-version-a"),
                    generation_run_id="run-version-a",
                    generation_job_id="job-version-a",
                )
                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("b", b"synthetic-version-b"),
                    publish=False,
                    generation_run_id="run-version-b",
                    generation_job_id="job-version-b",
                )
                self.assertTrue(
                    storage.mark_quote_session_publication_failed(
                        "quote-versioned-promote", "run-version-b", "synthetic_failure",
                    )
                )
                self.assertEqual(
                    storage.quote_session_export_artifact("quote-versioned-promote", "xlsx")["content"],
                    b"synthetic-version-a",
                )

                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("c", b"synthetic-version-c"),
                    publish=False,
                    generation_run_id="run-version-c",
                    generation_job_id="job-version-c",
                )
                c_files = storage.quote_session_evidence_files(
                    "quote-versioned-promote", "run-version-c",
                )
                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("d", b"synthetic-version-d"),
                    publish=False,
                    generation_run_id="run-version-d",
                    generation_job_id="job-version-d",
                )
                with self.assertRaisesRegex(ValueError, "another generation run"):
                    storage._run_storage_transaction(
                        lambda connection: storage.publish_quote_session_forensic_transaction(
                            connection, "quote-versioned-promote", "run-version-c", c_files,
                        )
                    )
                d_files = storage.quote_session_evidence_files(
                    "quote-versioned-promote", "run-version-d",
                )
                storage._run_storage_transaction(
                    lambda connection: storage.publish_quote_session_forensic_transaction(
                        connection, "quote-versioned-promote", "run-version-d", d_files,
                    )
                )
                self.assertEqual(
                    storage.quote_session_export_artifact("quote-versioned-promote", "xlsx")["content"],
                    b"synthetic-version-d",
                )
                self.assertEqual(
                    storage._support_forensic_export_artifact(
                        "quote-versioned-promote", "run-version-a", "xlsx",
                    )["content"],
                    b"synthetic-version-a",
                )

                with storage.connection() as connection:
                    connection.execute(
                        "update sqag_quote_publication_versions set legal_hold = 1 "
                        "where workspace_id = ? and run_id = ?",
                        ("workspace-five", "run-version-a"),
                    )
                    connection.commit()
                self.assertEqual(
                    storage.delete_quote_publication_version_for_retention(
                        "run-version-a", finalize_graph=lambda _connection: None,
                    ),
                    webapp.PUBLICATION_RETENTION_HELD,
                )
                with storage.connection() as connection:
                    connection.execute(
                        "update sqag_quote_publication_versions set legal_hold = 0 "
                        "where workspace_id = ? and run_id = ?",
                        ("workspace-five", "run-version-a"),
                    )
                    connection.commit()
                self.assertEqual(
                    storage.delete_quote_publication_version_for_retention(
                        "run-version-a", finalize_graph=lambda _connection: None,
                    ),
                    webapp.PUBLICATION_RETENTION_DELETED,
                )

                self.assertTrue(
                    storage.quote_publication_version_is_current(
                        "run-version-d", "quote-versioned-promote"
                    )
                )
                self.assertEqual(
                    storage.delete_quote_publication_version_for_retention(
                        "run-version-d",
                        finalize_graph=lambda _connection: None,
                    ),
                    webapp.PUBLICATION_RETENTION_CURRENT,
                )
                self.assertTrue(
                    storage.delete_quote_session_for_retention(
                        "quote-versioned-promote",
                        finalize_graph=lambda _connection, **_kwargs: None,
                    )
                )
                with storage.connection() as connection:
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_quote_publication_versions "
                            "where workspace_id = ? and session_id = ?",
                            ("workspace-five", "quote-versioned-promote"),
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_quote_publication_artifacts "
                            "where workspace_id = ? and session_id = ?",
                            ("workspace-five", "quote-versioned-promote"),
                        ).fetchone()[0],
                        0,
                    )
                self.assertIsNone(
                    storage.get_quote_session("quote-versioned-promote")
                )


    def test_object_replacement_uses_distinct_keys_and_preserves_current_bytes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            backend = webapp.InMemoryObjectStorageBackend()
            env = {
                "APP_MODE": "deploy",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "object",
                "SQAG_DATABASE_URL": database_url,
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url, "workspace-five", role="admin", user_id="synthetic-user",
                )
                payload = {"quote_session": {"session_id": "quote-object-versioned"}}

                def output(name, content):
                    path = root / name
                    path.mkdir()
                    (path / "quotation.xlsx").write_bytes(content)
                    return path

                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("object-a", b"synthetic-object-a"),
                    generation_run_id="run-object-a",
                    generation_job_id="job-object-a",
                )
                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("object-b", b"synthetic-object-b"),
                    publish=False,
                    generation_run_id="run-object-b",
                    generation_job_id="job-object-b",
                )
                with storage.connection() as connection:
                    rows = connection.execute(
                        "select object_key_ref from sqag_object_artifacts where workspace_id = ? "
                        "and owner_type = ? and owner_id in (?, ?) and status = ?",
                        (
                            "workspace-five", "generated_quote_version",
                            "run-object-a", "run-object-b", "active",
                        ),
                    ).fetchall()
                self.assertEqual(len({row["object_key_ref"] for row in rows}), 2)
                self.assertTrue(
                    storage.mark_quote_session_publication_failed(
                        "quote-object-versioned", "run-object-b", "synthetic_failure",
                    )
                )
                self.assertEqual(
                    storage.quote_session_export_artifact("quote-object-versioned", "xlsx")["content"],
                    b"synthetic-object-a",
                )
                storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output("object-c", b"synthetic-object-c"),
                    publish=False,
                    generation_run_id="run-object-c",
                    generation_job_id="job-object-c",
                )
                files = storage.quote_session_evidence_files(
                    "quote-object-versioned", "run-object-c",
                )
                storage._run_storage_transaction(
                    lambda connection: storage.publish_quote_session_forensic_transaction(
                        connection, "quote-object-versioned", "run-object-c", files,
                    )
                )
                self.assertEqual(
                    storage.quote_session_export_artifact("quote-object-versioned", "xlsx")["content"],
                    b"synthetic-object-c",
                )


                with storage.connection() as connection:
                    connection.execute(
                        "update sqag_quote_publication_versions set legal_hold = 1 "
                        "where workspace_id = ? and run_id = ?",
                        ("workspace-five", "run-object-b"),
                    )
                    connection.commit()
                self.assertFalse(
                    storage.delete_quote_session_for_retention(
                        "quote-object-versioned",
                        finalize_graph=lambda _connection, **_kwargs: None,
                    )
                )
                self.assertTrue(backend._objects)
                with storage.connection() as connection:
                    connection.execute(
                        "update sqag_quote_publication_versions set legal_hold = 0 "
                        "where workspace_id = ? and run_id = ?",
                        ("workspace-five", "run-object-b"),
                    )
                    connection.commit()
                self.assertTrue(
                    storage.delete_quote_session_for_retention(
                        "quote-object-versioned",
                        finalize_graph=lambda _connection, **_kwargs: None,
                    )
                )
                self.assertEqual(backend._objects, {})
                with storage.connection() as connection:
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_quote_publication_versions "
                            "where workspace_id = ? and session_id = ?",
                            ("workspace-five", "quote-object-versioned"),
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_object_artifacts "
                            "where workspace_id = ? and session_id = ? "
                            "and status = 'active'",
                            ("workspace-five", "quote-object-versioned"),
                        ).fetchone()[0],
                        0,
                    )


    def test_direct_transient_success_does_not_claim_durable_artifacts(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as tmp:
            root = Path(tmp)
            pricing_path = root / "pricing-catalog.json"
            pricing_path.write_text(json.dumps({"items": []}), encoding="utf-8")
            profile = webapp.ProfilePack(
                "synthetic-profile",
                webapp.DEFAULT_QUOTE_LAYOUT_TEMPLATE_PATH.parent,
                {"quotation_layout": webapp.DEFAULT_QUOTE_LAYOUT_TEMPLATE_PATH.name},
            )
            captured = {}

            def run_generator(command, **_kwargs):
                output_dir = Path(command[command.index("--out") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "quotation.xlsx").write_bytes(b"synthetic-transient-output")
                return webapp.subprocess.CompletedProcess(command, 0, "", "")

            def finish(run_id, result, *_args, **kwargs):
                captured["manifest"] = kwargs["canonical_manifest"]
                return {**result, "generation_run_id": run_id}

            with (
                mock.patch.object(webapp, "begin_generation_forensics", return_value="run-transient123"),
                mock.patch.object(webapp, "generation_payload_with_profile_defaults", side_effect=lambda value, **_kwargs: value),
                mock.patch.object(webapp, "validate_generation_payload", return_value=[]),
                mock.patch.object(webapp, "append_runtime_telemetry"),
                mock.patch.object(webapp, "payload_with_database_pricing_reference_detail", side_effect=lambda value, **_kwargs: value),
                mock.patch.object(webapp, "ensure_quote_artifact_storage_available_for_auth_session"),
                mock.patch.object(webapp, "configured_storage_mode", return_value="local"),
                mock.patch.object(webapp, "configured_artifact_storage_mode", return_value="filesystem"),
                mock.patch.object(webapp, "load_profile_pack", return_value=profile),
                mock.patch.object(webapp, "pricing_catalog_path_for_payload", return_value=pricing_path),
                mock.patch.object(webapp, "save_uploaded_images", return_value=[]),
                mock.patch.object(webapp, "payload_to_brief", return_value={}),
                mock.patch.object(webapp.subprocess, "run", side_effect=run_generator),
                mock.patch.object(webapp, "finish_generation_forensics", side_effect=finish),
            ):
                result = webapp.run_quote_job(
                    {},
                    output_root=root / "output",
                    tmp_root=root / "tmp",
                    job_id="job-transient123",
                )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(captured["manifest"]["artifacts"], [])
            self.assertFalse(captured["manifest"]["artifacts_durable"])


if __name__ == "__main__":
    unittest.main()
