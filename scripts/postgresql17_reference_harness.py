"""Disposable PostgreSQL-17 A/B/C/P reference harness for A23.

Each label owns a fresh Docker volume and container.  The harness never
connects to a configured provider or production database and never reuses a
failed reference.  The migration runner, live collector, policy registry and
comparator are imported from the shared production surfaces.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.postgresql17_finality_engine import (  # noqa: E402
    ENGINE_VERSION,
    POSTGRES_MAJOR,
    FinalityError,
    FinalitySnapshot,
    LiveCatalogueCollector,
    ReferenceReceipt,
    compare_real_references,
    compare_snapshots,
    load_coverage_manifest,
    _digest,
)
from webapp.postgres_migrations import (  # noqa: E402
    apply_postgres_migrations,
    inspect_postgres_migrations,
    migration_manifest,
)
from webapp.server import PostgresConnectionAdapter  # noqa: E402


POSTGRES_IMAGE = "postgres:17"
REFERENCE_LABELS = ("A", "B", "C", "P")
ROLE_NAMES = ("sqag_migrator", "sqag_runtime", "sqag_app", "neondb_owner")


def _safe_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sql_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise FinalityError("unsafe_harness_identifier")
    return '"' + value + '"'


class DockerCommandError(FinalityError):
    def __init__(self, operation: str, detail: str = "") -> None:
        super().__init__("docker_operation_failed", operation if not detail else f"{operation}:{detail}")


class DockerCLI:
    def run(self, args: Sequence[str], *, check: bool = True) -> str:
        try:
            completed = subprocess.run(
                ["docker", *args],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerCommandError(args[0] if args else "docker") from exc
        if check and completed.returncode != 0:
            # Docker output is deliberately reduced to a stable operation
            # category.  It must never become a receipt or leak connection
            # material.
            raise DockerCommandError(args[0] if args else "docker")
        return completed.stdout.strip()

    def exists(self, object_type: str, name: str) -> bool:
        output = self.run(["inspect", "--type", object_type, name], check=False)
        return bool(output and output != "[]")


@dataclass
class DisposablePostgresReference:
    label: str
    run_id: str
    docker: DockerCLI
    container_name: str = ""
    volume_name: str = ""
    container_id: str = ""
    port: int = 0
    database_name: str = ""
    cluster_system_identity: str = ""
    _raw_connections: list[Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._raw_connections = []

    def _admin_conninfo(self, database: str = "postgres") -> dict[str, Any]:
        return {"host": "127.0.0.1", "port": self.port, "user": "postgres", "dbname": database}

    def _connect(self, database: str, *, autocommit: bool = False) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - requirements contract
            raise FinalityError("psycopg_unavailable") from exc
        connection = psycopg.connect(**self._admin_conninfo(database), autocommit=autocommit)
        self._raw_connections.append(connection)
        return connection

    def start(self) -> None:
        suffix = uuid.uuid4().hex[:12]
        self.container_name = f"sqag-a23-{self.run_id}-{self.label.lower()}-{suffix}"
        self.volume_name = f"sqag-a23-volume-{self.run_id}-{self.label.lower()}-{suffix}"
        self.docker.run(["volume", "create", self.volume_name])
        self.container_id = self.docker.run(
            [
                "run",
                "-d",
                "--rm",
                "--name",
                self.container_name,
                "--label",
                f"sqag.a23.run={self.run_id}",
                "--label",
                f"sqag.a23.reference={self.label}",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-p",
                "127.0.0.1::5432",
                "-v",
                f"{self.volume_name}:/var/lib/postgresql/data",
                POSTGRES_IMAGE,
            ]
        )
        port_text = self.docker.run(["port", self.container_name, "5432/tcp"])
        match = re.search(r":(\d+)\s*$", port_text)
        if not match:
            raise DockerCommandError("port")
        self.port = int(match.group(1))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                self.docker.run(
                    ["exec", self.container_name, "pg_isready", "-U", "postgres", "-d", "postgres"],
                    check=True,
                )
                break
            except DockerCommandError:
                pass
            time.sleep(1)
        else:
            raise DockerCommandError("pg_isready")
        with self._connect("postgres", autocommit=True) as connection:
            row = connection.execute(
                "select current_setting('server_version_num') as version_num, "
                "(select system_identifier from pg_catalog.pg_control_system()) as system_identifier"
            ).fetchone()
            version_num = int(row["version_num"] if isinstance(row, dict) else row[0])
            system_id = str(row["system_identifier"] if isinstance(row, dict) else row[1])
            if version_num // 10000 != 17:
                raise FinalityError("postgres_major_mismatch", self.label)
            self.cluster_system_identity = _safe_digest(system_id)
            self._create_roles_and_database(connection)

    def _create_roles_and_database(self, connection: Any) -> None:
        # These are disposable, deterministic role attributes.  The provider
        # role is the only role with CREATEROLE; the runtime role is created by
        # that role on PostgreSQL 17 so the creator-admin edge is observed by
        # the live extractor rather than invented in a fixture.
        for role in ("sqag_migrator", "sqag_app"):
            connection.execute(
                f"create role {_sql_identifier(role)} nosuperuser nocreatedb "
                "nocreaterole noinherit nologin noreplication nobypassrls"
            )
        connection.execute(
            'create role "neondb_owner" nosuperuser nocreatedb createrole inherit nologin noreplication nobypassrls'
        )
        connection.execute('set role "neondb_owner"')
        connection.execute(
            'create role "sqag_runtime" nosuperuser nocreatedb nocreaterole inherit nologin noreplication nobypassrls'
        )
        connection.execute("reset role")
        database_run_id = re.sub(r"[^a-z0-9_]+", "_", self.run_id.lower()).strip("_") or "run"
        database_run_id = database_run_id[:40]
        # Each cluster is independent, so the database name can be stable
        # and remain part of the comparable live pg_database universe.
        self.database_name = f"sqag_a23_{database_run_id}_reference"
        connection.execute(f"create database {_sql_identifier(self.database_name)} owner {_sql_identifier('sqag_migrator')}")

    def _configure_acl_topology(self, connection: Any) -> None:
        database = _sql_identifier(self.database_name)
        connection.execute(f"revoke temporary on database {database} from public")
        connection.execute(f"grant connect, create, temporary on database {database} to \"sqag_migrator\"")
        connection.execute(f"grant connect on database {database} to \"sqag_runtime\"")
        connection.execute(f"grant connect on database {database} to \"sqag_app\"")
        connection.execute("revoke create on schema public from public")
        connection.execute('grant usage on schema public to "sqag_runtime"')
        connection.execute('revoke create on schema public from "sqag_runtime"')
        connection.execute('grant usage on schema public to "sqag_app"')

        connection.execute('revoke all on all tables in schema public from public')
        connection.execute('revoke all on all sequences in schema public from public')
        connection.execute('revoke all on all functions in schema public from public')
        for table, privileges in {
            "sqag_profiles": "select, insert, update, delete",
            "sqag_pricing_references": "select, insert, update, delete",
            "sqag_quote_sessions": "select, insert, update, delete",
            "sqag_generation_runs": "select, insert, update",
            "sqag_generation_evidence": "select, insert",
            "sqag_audit_events": "select, insert",
            "sqag_feedback": "select, insert, update",
            "sqag_feedback_status_history": "select, insert",
            "sqag_object_artifacts": "select, insert, update",
            "sqag_quote_publication_versions": "select, insert, update, delete",
            "sqag_quote_publication_artifacts": "select, insert, delete",
        }.items():
            connection.execute(f"grant {privileges} on table {_sql_identifier(table)} to \"sqag_runtime\"")
        connection.execute(
            'grant update (checksum_sha256) on table "sqag_quote_publication_artifacts" to "sqag_runtime"'
        )
        for object_type in ("tables", "sequences", "functions", "types", "schemas"):
            if object_type == "schemas":
                continue
            connection.execute(
                f"alter default privileges for role \"sqag_migrator\" in schema public "
                f"revoke all on {object_type} from public, \"sqag_runtime\", \"sqag_app\""
            )

    def replay_and_prepare(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        from webapp.postgres_migrations import migration_manifest

        migrations = migration_manifest(ROOT / "migrations")
        with self._connect(self.database_name) as raw:
            adapter = PostgresConnectionAdapter(raw)
            adapter.execute('set role "sqag_migrator"')
            try:
                result = apply_postgres_migrations(adapter, migrations)
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            if result["appliedNow"] != [item.migration_id for item in migrations]:
                raise FinalityError("partial_migration_replay", self.label)
        with self._connect(self.database_name, autocommit=True) as admin:
            self._configure_acl_topology(admin)
        # Normalize the controlled table's initial statistics in every
        # reference so P can prove a restored catalogue universe after its
        # real insert/VACUUM (ANALYZE)/delete sequence.
        with self._connect(self.database_name, autocommit=True) as statistics:
            statistics.execute('set role "sqag_migrator"')
            statistics.execute(
                "insert into public.sqag_profiles "
                "(workspace_id, profile_id, payload_json, created_at, updated_at) "
                "values (%s, %s, %s, %s, %s) on conflict do nothing",
                ("a23-baseline", "seed", "{}", "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z"),
            )
            statistics.execute("analyze public.sqag_profiles")
        with self._connect(self.database_name) as raw:
            adapter = PostgresConnectionAdapter(raw)
            adapter.execute("set transaction read only")
            report = inspect_postgres_migrations(adapter, migrations)
            raw.rollback()
        if report["status"] != "ready" or report["pendingMigrationIds"]:
            raise FinalityError("migration_replay_verification_failed", self.label)
        checksums = tuple((item.migration_id, item.checksum_sha256) for item in migrations)
        return _digest(checksums), checksums

    def extract(self) -> tuple[FinalitySnapshot, LiveCatalogueCollector]:
        raw = self._connect(self.database_name)
        collector = LiveCatalogueCollector(PostgresConnectionAdapter(raw), self.label)
        observations, trace = collector.collect()
        snapshot = FinalitySnapshot.build(
            reference_id=self.label,
            observations=observations,
            registry=collector.registry,
            trace=trace,
            boundary_receipts=collector.boundary_receipts,
        )
        return snapshot, collector

    def _maintenance_activity(self) -> tuple[FinalitySnapshot, int, bool]:
        baseline, collector = self.extract()
        maintenance_conn = self._connect(self.database_name, autocommit=True)
        try:
            maintenance_conn.execute('set role "sqag_migrator"')
            rows = [
                (f"a23-maintenance-{self.label.lower()}", f"row-{index}", "x" * 2048, "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z")
                for index in range(2048)
            ]
            with maintenance_conn.cursor() as maintenance_cursor:
                maintenance_cursor.executemany(
                    "insert into public.sqag_profiles "
                    "(workspace_id, profile_id, payload_json, created_at, updated_at) values (%s, %s, %s, %s, %s)",
                    rows,
                )
            maintenance_conn.execute("vacuum (analyze) public.sqag_profiles")
        finally:
            maintenance_conn.close()
            if maintenance_conn in self._raw_connections:
                self._raw_connections.remove(maintenance_conn)
        perturbed, _ = self.extract()
        before = {item.identity_key: item for item in baseline.observations}
        after = {item.identity_key: item for item in perturbed.observations}
        witness = sum(
            1
            for key, left in before.items()
            if key in after
            and left.policy_class == "postgresql_maintained_dynamic_estimate_maintenance_state"
            and left.raw_digest != after[key].raw_digest
        )
        restore_conn = self._connect(self.database_name, autocommit=True)
        try:
            restore_conn.execute('set role "sqag_migrator"')
            restore_conn.execute(
                "delete from public.sqag_profiles where workspace_id = %s",
                (f"a23-maintenance-{self.label.lower()}",),
            )
            restore_conn.execute("vacuum (analyze) public.sqag_profiles")
            count = restore_conn.execute(
                "select count(*) from public.sqag_profiles where workspace_id = %s",
                (f"a23-maintenance-{self.label.lower()}",),
            ).fetchone()
            residue_free = int(count[0] if not isinstance(count, dict) else next(iter(count.values()))) == 0
        finally:
            restore_conn.close()
            if restore_conn in self._raw_connections:
                self._raw_connections.remove(restore_conn)
        restored, _ = self.extract()
        compare_snapshots(baseline, restored)
        return restored, witness, residue_free

    def stop_and_verify_cleanup(self) -> bool:
        for connection in list(self._raw_connections):
            try:
                connection.close()
            except Exception:
                pass
        self._raw_connections.clear()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.container_name:
                self.docker.run(["rm", "-f", self.container_name], check=False)
            if self.volume_name:
                self.docker.run(["volume", "rm", "-f", self.volume_name], check=False)
            if not self.docker.exists("container", self.container_name) and not self.docker.exists("volume", self.volume_name):
                return True
            time.sleep(0.25)
        return not self.docker.exists("container", self.container_name) and not self.docker.exists("volume", self.volume_name)

    def run(self) -> tuple[ReferenceReceipt, FinalitySnapshot]:
        migration_digest = ""
        checksums: tuple[tuple[str, str], ...] = ()
        final_snapshot: FinalitySnapshot | None = None
        maintenance_executed = self.label == "P"
        witness = 0
        residue_free = True
        cleanup_verified = False
        try:
            self.start()
            migration_digest, checksums = self.replay_and_prepare()
            if self.label == "P":
                final_snapshot, witness, residue_free = self._maintenance_activity()
            else:
                final_snapshot, _ = self.extract()
            if final_snapshot is None:
                raise FinalityError("missing_final_snapshot", self.label)
        finally:
            cleanup_verified = self.stop_and_verify_cleanup()
        receipt = ReferenceReceipt(
            reference_id=self.label,
            collector_mode="live_postgresql17",
            synthetic=False,
            real_connection=True,
            executed_fields_count=len(final_snapshot.trace.executed_fields) if final_snapshot else 0,
            field_values_count=final_snapshot.trace.field_values if final_snapshot else 0,
            reference_independence_verified=bool(self.container_id and self.volume_name and self.cluster_system_identity),
            cleanup_verified=cleanup_verified,
            postgres_major=POSTGRES_MAJOR,
            cluster_system_identity=self.cluster_system_identity,
            container_identity=_safe_digest(self.container_id),
            volume_identity=_safe_digest(self.volume_name),
            database_name=self.database_name,
            migration_manifest_digest=migration_digest,
            migration_checksums=checksums,
            exact_replay_verified=bool(migration_digest and checksums),
            maintenance_executed=maintenance_executed,
            maintenance_variance_witness_count=witness,
            semantic_residue_free=residue_free,
        )
        receipt.validate_live()
        return receipt, final_snapshot


def run_real_references(run_id: str | None = None) -> dict[str, Any]:
    run_id = run_id or ("run-" + uuid.uuid4().hex[:12])
    load_coverage_manifest(ROOT)
    docker = DockerCLI()
    results: list[tuple[ReferenceReceipt, FinalitySnapshot]] = []
    for label in REFERENCE_LABELS:
        reference = DisposablePostgresReference(label, run_id, docker)
        results.append(reference.run())
    return compare_real_references(results, require_maintenance=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the real PostgreSQL-17 A/B/C/P SQAG A23 proof.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = run_real_references(args.run_id)
    except FinalityError as exc:
        payload = {"status": "RED", "error_code": exc.code}
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"RED: {exc.code}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print("A23 real PostgreSQL-17 A/B/C/P convergence: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
