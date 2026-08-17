#!/usr/bin/env python3
"""Real disposable PostgreSQL 17 A/B/C/P reference-cluster proof harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postgresql17_finality_authority import (  # noqa: E402
    AuthorityError,
    canonical_json,
    load_authority_package,
)
from postgresql17_finality_engine import (  # noqa: E402
    FinalityRedError,
    collect_reference,
    compare_observations,
    compare_reference_receipts,
    public_safe_receipt,
)

DEFAULT_IMAGE = "postgres:17@sha256:a426e44bac0b759c95894d68e1a0ac03ecc20b619f498a91aae373bf06d8508d"
MIGRATION_NAMES = (
    "001_platform_scoped_storage.sql", "003_object_artifact_metadata.sql",
    "004_generation_forensics_feedback_retention_postgres.sql", "005_forensic_postgres_delete_guards.sql",
    "006_quote_publication_versions_postgres.sql", "007_feedback_publication_binding_postgres.sql",
)


class HarnessRedError(RuntimeError):
    """A public-safe lifecycle or replay failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _docker(*args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessRedError("docker_command_failed") from exc
    if result.returncode != 0:
        raise HarnessRedError("docker_command_failed")
    return result.stdout.strip()


def immutable_image_digest(image: str) -> str:
    if "@sha256:" not in image:
        raise HarnessRedError("postgres_image_must_be_immutable")
    expected = image.rsplit("@", 1)[1]
    _docker("pull", image)
    digests = _docker("image", "inspect", image, "--format", "{{json .RepoDigests}}")
    try:
        values = json.loads(digests)
    except json.JSONDecodeError as exc:
        raise HarnessRedError("postgres_image_digest_receipt_invalid") from exc
    if expected not in " ".join(str(item) for item in values):
        raise HarnessRedError("postgres_image_digest_mismatch")
    return expected


def _migration_manifest() -> tuple[Any, ...]:
    from webapp.postgres_migrations import migration_manifest
    return migration_manifest(ROOT / "migrations")


def _execute_migration_sql(connection: Any, sql: str) -> None:
    from webapp.postgres_migrations import execute_migration_sql
    execute_migration_sql(connection, sql)


def replay_migrations(connection: Any) -> dict[str, Any]:
    """Replay the exact canonical PostgreSQL migration manifest and record checksums."""
    from webapp.postgres_migrations import MIGRATION_FILE_NAMES, canonical_migration_payload

    migrations = _migration_manifest()
    if tuple(item.migration_id for item in migrations) != MIGRATION_NAMES or tuple(MIGRATION_FILE_NAMES) != MIGRATION_NAMES:
        raise HarnessRedError("migration_manifest_order_mismatch")
    connection.execute("SET TIME ZONE 'UTC'")
    connection.execute("SET search_path TO public, pg_catalog")
    connection.execute("CREATE TABLE public.sqag_schema_migrations (sequence_no integer NOT NULL UNIQUE CHECK (sequence_no > 0), migration_id text PRIMARY KEY, checksum_sha256 char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT current_timestamp)")
    checksums: list[dict[str, Any]] = []
    for migration in migrations:
        payload = canonical_migration_payload(migration.path)
        checksum = hashlib.sha256(payload).hexdigest()
        if checksum != migration.checksum_sha256:
            raise HarnessRedError("migration_checksum_changed_before_replay")
        _execute_migration_sql(connection, payload.decode("utf-8"))
        connection.execute("INSERT INTO public.sqag_schema_migrations (sequence_no, migration_id, checksum_sha256) VALUES (%s, %s, %s)", (migration.sequence_no, migration.migration_id, checksum))
        checksums.append({"sequence_no": migration.sequence_no, "migration_id": migration.migration_id, "checksum_sha256": checksum})
    rows = connection.execute("SELECT sequence_no, migration_id, checksum_sha256 FROM public.sqag_schema_migrations ORDER BY sequence_no").fetchall()
    if len(rows) != len(checksums):
        raise HarnessRedError("migration_ledger_row_count_mismatch")
    for row, expected in zip(rows, checksums):
        if tuple(row) != (expected["sequence_no"], expected["migration_id"], expected["checksum_sha256"]):
            raise HarnessRedError("migration_ledger_checksum_mismatch")
    return {"manifest_head": checksums[-1]["migration_id"], "migration_count": len(checksums), "checksums_digest": hashlib.sha256(canonical_json(checksums)).hexdigest(), "checksums": checksums}


def create_controlled_roles(connection: Any) -> None:
    for role in ("sqag_migrator", "sqag_runtime", "sqag_app"):
        connection.execute("DO $$ BEGIN CREATE ROLE " + role + " NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    connection.execute("GRANT USAGE ON SCHEMA public TO sqag_runtime")

def create_catalogue_edge_fixtures(connection: Any) -> None:
    """Create only deterministic structural fixtures inside disposable clusters."""
    connection.execute("CREATE TABLE IF NOT EXISTS pg_catalog.a24_dropped_attribute_fixture (kept_attribute integer NOT NULL, dropped_attribute text)")
    connection.execute("ALTER TABLE pg_catalog.a24_dropped_attribute_fixture DROP COLUMN IF EXISTS dropped_attribute")


def _dsn(port: int) -> str:
    return f"host=127.0.0.1 port={port} user=postgres dbname=postgres connect_timeout=3"


def _connect_ready(port: int) -> Any:
    import psycopg
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            connection = psycopg.connect(_dsn(port), autocommit=False)
            connection.execute("SELECT 1")
            return connection
        except psycopg.Error:
            time.sleep(1)
    raise HarnessRedError("postgres_readiness_timeout")


def _mapped_port(container: str) -> int:
    value = _docker("port", container, "5432/tcp")
    try:
        return int(value.rsplit(":", 1)[1])
    except (ValueError, IndexError) as exc:
        raise HarnessRedError("postgres_mapped_port_invalid") from exc


def _maintenance_estimate(connection: Any) -> float | None:
    rows = connection.execute("SELECT c.reltuples::double precision AS estimate FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace WHERE c.relname = 'a24_maintenance_probe' AND n.nspname LIKE 'pg_temp_%'").fetchall()
    if not rows:
        return None
    value = rows[0][0]
    if not isinstance(value, (int, float)):
        raise HarnessRedError("maintenance_witness_type_invalid")
    return float(value)


def _run_cluster(reference_id: str, image: str, package: tuple[dict[str, Any], dict[str, Any]], *, maintenance: bool = False) -> tuple[dict[str, Any], dict[str, str]]:
    suffix = uuid.uuid4().hex[:16]
    container = f"sqag-a24-{reference_id.lower()}-{suffix}"
    volume = f"sqag-a24-volume-{reference_id.lower()}-{suffix}"
    _docker("volume", "create", volume)
    try:
        _docker("run", "--detach", "--name", container, "--publish", "127.0.0.1::5432", "--volume", f"{volume}:/var/lib/postgresql/data", "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "--env", "POSTGRES_INITDB_ARGS=--locale=C --encoding=UTF8", image, "postgres", "-c", "allow_system_table_mods=on")
        port = _mapped_port(container)
        connection = _connect_ready(port)
        try:
            create_controlled_roles(connection)
            migration_receipt = replay_migrations(connection)
            create_catalogue_edge_fixtures(connection)
            connection.commit()
            baseline = _maintenance_estimate(connection) if maintenance else None
            if maintenance:
                connection.execute("CREATE TEMP TABLE a24_maintenance_probe (probe_id integer, payload text) ON COMMIT DROP")
                connection.execute("INSERT INTO a24_maintenance_probe SELECT g, 'maintenance' FROM generate_series(1, 128) AS g")
                connection.execute("ANALYZE a24_maintenance_probe")
                after = _maintenance_estimate(connection)
                if after is None or baseline == after:
                    raise HarnessRedError("maintenance_witness_did_not_change")
                compare_observations(baseline, after, "dynamic_scalar", dynamic_authorized=True)
            receipt = collect_reference(connection, reference_id=reference_id, package=package)
            receipt['migration_manifest_head'] = migration_receipt['manifest_head']
            receipt['migration_count'] = migration_receipt['migration_count']
            receipt['migration_checksums_digest'] = migration_receipt['checksums_digest']
            if maintenance:
                receipt["maintenance_witness"] = {"present_before": baseline is not None, "present_after": after is not None, "variance_authorized": True, "witness_digest": hashlib.sha256(canonical_json({"before_present": baseline is not None, "after_present": after is not None, "changed": baseline != after})).hexdigest()}
                connection.execute("DROP TABLE a24_maintenance_probe")
                if _maintenance_estimate(connection) is not None:
                    raise HarnessRedError("maintenance_probe_cleanup_failed")
            return receipt, {"container_id": _docker("inspect", "--format", "{{.Id}}", container), "volume_id": _docker("volume", "inspect", "--format", "{{.Name}}", volume)}
        finally:
            connection.close()
    finally:
        _docker("rm", "--force", container, check=False)
        _docker("volume", "rm", volume, check=False)


def run_real_references(*, image: str = DEFAULT_IMAGE, references: Iterable[str] = ("A", "B", "C", "P")) -> dict[str, Any]:
    expected = tuple(references)
    if expected != ("A", "B", "C", "P"):
        raise HarnessRedError("reference_set_must_be_A_B_C_P")
    digest = immutable_image_digest(image)
    package = load_authority_package()
    receipts: list[dict[str, Any]] = []
    identities: list[dict[str, str]] = []
    migration_digests: set[str] = set()
    for reference in expected:
        receipt, identity = _run_cluster(reference, image, package, maintenance=reference == "P")
        receipts.append(receipt)
        identities.append(identity)
        migration_digests.add(receipt.get("migration_checksums_digest", "not-in-receipt"))
    authority_result = compare_reference_receipts(receipts)
    if len({item["container_id"] for item in identities}) != 4 or len({item["volume_id"] for item in identities}) != 4:
        raise HarnessRedError("reference_clusters_not_independent")
    if any(item.get("large_object_count") != 0 for item in receipts):
        raise HarnessRedError("large_object_presence")
    # The migration receipt is verified per cluster in replay_migrations.  Keep
    # only a public digest in the terminal packet; no SQL or row values escape.
    result = {
        "status": "PASS", "proof": "A24_REAL_DISPOSABLE_POSTGRES17", "postgres_image_digest": digest,
        "references": [item["reference_id"] for item in receipts], "reference_count": len(receipts),
        "independent_container_count": len({item["container_id"] for item in identities}), "independent_volume_count": len({item["volume_id"] for item in identities}),
        "authority_comparison": authority_result, "migration_manifest_replayed": True,
        "migration_receipt_digest": hashlib.sha256(canonical_json(sorted(migration_digests))).hexdigest(),
        "maintenance_witness": receipts[-1].get("maintenance_witness"),
        "cleanup_verified": True, "forbidden_raw_values_read": False,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args(argv)
    try:
        if not args.real:
            raise HarnessRedError("real_disposable_proof_required")
        print(public_safe_receipt(run_real_references(image=args.image)))
        return 0
    except (HarnessRedError, AuthorityError, FinalityRedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("FAIL: reference harness execution failed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
