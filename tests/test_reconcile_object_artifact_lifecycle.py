from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from webapp import server as webapp
import reconcile_object_artifact_lifecycle as reconciler


def _operation_row(
    workspace_id: str,
    owner_type: str,
    owner_id: str,
    operation_seq: int,
    cleanup: list[dict[str, object]],
    *,
    created_at: str,
    updated_at: str,
) -> tuple[object, ...]:
    operation_id = "op-v2-" + hashlib.sha256(
        f"{workspace_id}:{owner_type}:{owner_id}:{operation_seq}".encode("utf-8")
    ).hexdigest()
    request_sha256 = hashlib.sha256(f"request-{operation_seq}".encode("utf-8")).hexdigest()
    plan = {
        "schema_version": 1,
        "workspace_id": workspace_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "operation_seq": operation_seq,
        "operation_id": operation_id,
        "request_sha256": request_sha256,
        "lock_identities": [[owner_type, owner_id]],
        "locked_owner_row_digests": [
            {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "sha256": hashlib.sha256(b"locked-owner").hexdigest(),
            }
        ],
        "managed_artifact_kinds": ["quotation_layout"],
        "predecessors": {
            "quotation_layout": dataclasses.asdict(webapp.ArtifactRowSnapshot(
                artifact_id="obj-v2-" + "a" * 64,
                workspace_id=workspace_id,
                owner_type=owner_type,
                owner_id=owner_id,
                platform_user_id=None,
                session_id=None,
                job_id=None,
                artifact_kind="quotation_layout",
                filename="layout.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                size_bytes=4,
                checksum_sha256="b" * 64,
                object_provider_type="s3_compatible",
                object_key_ref="workspaces/test/profile/test/quotation_layout/v2/inc-v2-" + "c" * 64 + "/" + "b" * 64 + "-layout.xlsx",
                status="active",
                retention_status="active",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ))
        },
        "successors": {},
        "published_rows": {},
        "unchanged_kinds": [],
        "omitted_kind_dispositions": {},
        "delete_targets": [],
        "delete_published_rows": [],
        "owner_delete_operation": False,
        "owner_row_digests": {
            "before": hashlib.sha256(b"before").hexdigest(),
            "after": hashlib.sha256(b"after").hexdigest(),
        },
    }
    return (
        workspace_id,
        owner_type,
        owner_id,
        operation_seq,
        operation_id,
        request_sha256,
        json.dumps(plan, sort_keys=True, separators=(",", ":")),
        "published",
        json.dumps(cleanup, sort_keys=True, separators=(",", ":")),
        created_at,
        updated_at,
    )


class ReconcileSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sqag-artifact-reconcile-")
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "sqag.sqlite3"
        self.database_url = f"sqlite:///{self.db_path.as_posix()}"
        self.workspace_id = "workspace-reconcile-test"
        self.owner_id = "profile-reconcile-test"
        env = mock.patch.dict(
            os.environ,
            {
                "APP_MODE": "local",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "object",
                "SQAG_DATABASE_URL": self.database_url,
            },
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)
        webapp.apply_sqag_storage_migrations(self.database_url)
        self.storage = webapp.DatabaseSqagStorage(
            self.database_url,
            self.workspace_id,
            role="maintenance",
            user_id="synthetic-reconciler-test",
        )
        self.storage.ensure_object_artifact_ready()

    def test_bounded_cleanup_query_selects_old_pending_before_recent_completed_rows(self):
        snapshot = dataclasses.asdict(webapp.ArtifactRowSnapshot(
            artifact_id="obj-v2-" + "a" * 64,
            workspace_id=self.workspace_id,
            owner_type="profile",
            owner_id=self.owner_id,
            platform_user_id=None,
            session_id=None,
            job_id=None,
            artifact_kind="quotation_layout",
            filename="layout.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=4,
            checksum_sha256="b" * 64,
            object_provider_type="s3_compatible",
            object_key_ref="workspaces/test/profile/test/quotation_layout/v2/inc-v2-" + "c" * 64 + "/" + "b" * 64 + "-layout.xlsx",
            status="active",
            retention_status="active",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            deleted_at=None,
        ))
        pending = [{"snapshot": snapshot, "state": "pending"}]
        completed: list[dict[str, object]] = []
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executemany(
                "insert into sqag_object_artifact_operations "
                "(workspace_id, owner_type, owner_id, operation_seq, operation_id, "
                "request_sha256, plan_json, state, cleanup_json, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    _operation_row(
                        self.workspace_id,
                        "profile",
                        self.owner_id,
                        1,
                        pending,
                        created_at="2020-01-01T00:00:00Z",
                        updated_at="2020-01-01T00:00:00Z",
                    ),
                    _operation_row(
                        self.workspace_id,
                        "profile",
                        self.owner_id,
                        2,
                        completed,
                        created_at="2026-01-01T00:00:00Z",
                        updated_at="2026-01-01T00:00:00Z",
                    ),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        selected: list[int] = []
        backend = webapp.InMemoryObjectStorageBackend()
        with mock.patch.object(
            webapp,
            "configured_object_storage_backend",
            return_value=backend,
        ), mock.patch.object(
            self.storage,
            "_cleanup_object_artifact_batch",
            side_effect=lambda plan: selected.append(plan.operation_seq),
        ):
            self.storage.reconcile_object_artifact_lifecycle(
                "profile",
                self.owner_id,
                limit=1,
                apply_cleanup=True,
            )

        self.assertEqual(selected, [1])

    def test_cli_uses_only_the_dedicated_maintenance_database(self):
        captured: dict[str, object] = {}

        class FakeStorage:
            def __init__(self, database_url, workspace_id, **kwargs):
                captured["database_url"] = database_url
                captured["workspace_id"] = workspace_id
                captured["kwargs"] = kwargs

            def reconcile_object_artifact_lifecycle(
                self, owner_type, owner_id, *, limit, apply_cleanup
            ):
                captured["reconcile"] = (owner_type, owner_id, limit, apply_cleanup)
                return {
                    "operations_inspected": 2,
                    "prepared_operations": 0,
                    "published_operations": 1,
                    "pending_cleanup_targets": 1,
                    "uncertain_cleanup_targets": 0,
                }

        args = Namespace(
            workspace_id="workspace-safe",
            owner_type="profile",
            owner_id="profile-safe",
            limit=7,
            apply_cleanup=True,
        )
        maintenance_url = "postgresql://maintenance.invalid/sqag"
        with (
            mock.patch.object(
                reconciler.webapp,
                "configured_maintenance_database_url",
                return_value=maintenance_url,
            ),
            mock.patch.object(
                reconciler.webapp,
                "postgres_database_url_is_supported",
                return_value=True,
            ),
            mock.patch.object(
                reconciler.webapp,
                "DatabaseSqagStorage",
                FakeStorage,
            ),
        ):
            report = reconciler.run(args)

        self.assertEqual(captured["database_url"], maintenance_url)
        self.assertEqual(
            captured["kwargs"],
            {
                "role": "maintenance",
                "user_id": "artifact-lifecycle-reconciler",
                "expected_session_role": webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            },
        )
        self.assertEqual(
            captured["reconcile"],
            ("profile", "profile-safe", 7, True),
        )
        self.assertEqual(report["status"], "inspected")
        serialized = json.dumps(report)
        self.assertNotIn(maintenance_url, serialized)
        self.assertEqual(report["privacy"]["database_url"], "omitted")

    def test_cli_rejects_unbounded_input_before_database_access(self):
        args = Namespace(
            workspace_id="workspace-safe",
            owner_type="profile",
            owner_id="profile-safe",
            limit=101,
            apply_cleanup=False,
        )
        with mock.patch.object(
            reconciler.webapp,
            "configured_maintenance_database_url",
        ) as configured:
            with self.assertRaises(ValueError):
                reconciler.run(args)
        configured.assert_not_called()

    def test_cli_hides_database_error_details(self):
        output = io.StringIO()
        with (
            mock.patch.object(
                reconciler,
                "run",
                side_effect=RuntimeError("private connection detail"),
            ),
            contextlib.redirect_stdout(output),
        ):
            status = reconciler.main(
                [
                    "--workspace-id",
                    "workspace-safe",
                    "--owner-type",
                    "profile",
                    "--owner-id",
                    "profile-safe",
                ]
            )

        self.assertEqual(status, 1)
        self.assertNotIn("private connection detail", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()