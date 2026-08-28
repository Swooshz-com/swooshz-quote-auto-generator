import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (
    EXPECTED_CALLABLE_ROUTINE_KEYS,
    EXPECTED_INDEXES,
    EXPECTED_ROUTINES,
    EXPECTED_ROUTINE_KEYS,
    EXPECTED_TRIGGER_ROUTINE_KEYS,
    EXPECTED_TABLES,
    EXPECTED_TRIGGERS,
    LEDGER_TABLE,
    MIGRATION_LOCK_KEY,
    MIGRATION_FILE_NAMES,
    Migration,
    MigrationSafetyError,
    apply_postgres_migrations,
    canonical_migration_payload,
    inspect_postgres_migrations,
    migration_manifest,
)
from webapp.server import PostgresConnectionAdapter


def postgres_test_conninfo(database_name: str = "postgres") -> str | None:
    host = os.getenv("SQAG_TEST_POSTGRES_HOST", "").strip()
    port = os.getenv("SQAG_TEST_POSTGRES_PORT", "").strip()
    user = os.getenv("SQAG_TEST_POSTGRES_USER", "").strip()
    if not host or not port or not user:
        return None
    return f"host={host} port={port} user={user} dbname={database_name}"


def write_migration_copy(destination: Path, line_ending: bytes) -> tuple[Migration, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    for file_name in MIGRATION_FILE_NAMES:
        canonical = canonical_migration_payload(ROOT / "migrations" / file_name)
        (destination / file_name).write_bytes(canonical.replace(b"\n", line_ending))
    return migration_manifest(destination)


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self


class MigrationPayloadCanonicalizationTest(unittest.TestCase):
    def test_lf_crlf_and_bare_cr_have_identical_payloads_and_checksums(self):
        variants = (
            b"select 1;\nselect 2;\n",
            b"select 1;\r\nselect 2;\r\n",
            b"select 1;\rselect 2;\r",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            payloads = []
            checksums = []
            for index, variant in enumerate(variants):
                path = Path(temp_dir) / f"variant-{index}.sql"
                path.write_bytes(variant)
                payload = canonical_migration_payload(path)
                payloads.append(payload)
                checksums.append(sha256(payload).hexdigest())

        self.assertEqual(payloads, [b"select 1;\nselect 2;\n"] * 3)
        self.assertEqual(len(set(checksums)), 1)

    def test_non_eol_change_has_different_canonical_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.sql"
            second = Path(temp_dir) / "second.sql"
            first.write_bytes(b"select 1;\r\n")
            second.write_bytes(b"select 2;\n")

            first_checksum = sha256(canonical_migration_payload(first)).hexdigest()
            second_checksum = sha256(canonical_migration_payload(second)).hexdigest()

        self.assertNotEqual(first_checksum, second_checksum)

    def test_invalid_utf8_fails_closed_with_file_only_blocker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "004_invalid.sql"
            path.write_bytes(b"select '\xff';")

            with self.assertRaises(MigrationSafetyError) as raised:
                canonical_migration_payload(path)

        self.assertEqual(raised.exception.blocker, "migration_source_invalid_utf8:004_invalid.sql")

    def test_manifest_and_execution_use_same_canonical_payload_and_ledger_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            migrations_dir = Path(temp_dir)
            manifest = write_migration_copy(migrations_dir, b"\r\n")
            for migration in manifest:
                migration.path.write_bytes(migration.path.read_bytes().replace(b"\r\n", b"\n"))

            before = {
                "safeToApply": True,
                "ledgerState": "missing",
                "appliedMigrationIds": [],
                "blockers": [],
            }
            after = {
                "safeToApply": True,
                "pendingMigrationIds": [],
                "blockers": [],
            }
            connection = RecordingConnection()
            with patch(
                "webapp.postgres_migrations.inspect_postgres_migrations",
                side_effect=(before, after),
            ), patch("webapp.postgres_migrations.execute_migration_sql") as execute_sql:
                result = apply_postgres_migrations(connection, manifest)

        self.assertEqual(result["appliedNow"], list(MIGRATION_FILE_NAMES))
        self.assertEqual(
            [call.args[1] for call in execute_sql.call_args_list],
            [
                canonical_migration_payload(ROOT / "migrations" / name).decode("utf-8")
                for name in MIGRATION_FILE_NAMES
            ],
        )
        ledger_checksums = [
            params[2]
            for sql, params in connection.calls
            if sql.startswith("insert into public.sqag_schema_migrations")
        ]
        self.assertEqual(ledger_checksums, [migration.checksum_sha256 for migration in manifest])

    def test_substantive_change_between_manifest_and_execution_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = write_migration_copy(Path(temp_dir), b"\n")
            manifest[0].path.write_bytes(manifest[0].path.read_bytes() + b"select 2;\n")
            before = {
                "safeToApply": True,
                "ledgerState": "missing",
                "appliedMigrationIds": [],
                "blockers": [],
            }
            with patch("webapp.postgres_migrations.inspect_postgres_migrations", return_value=before):
                with self.assertRaises(MigrationSafetyError) as raised:
                    apply_postgres_migrations(RecordingConnection(), manifest)

        self.assertEqual(
            raised.exception.blocker,
            f"migration_source_changed_during_run:{MIGRATION_FILE_NAMES[0]}",
        )


@unittest.skipUnless(postgres_test_conninfo(), "isolated PostgreSQL test service is not configured")
class PostgresMigrationLedgerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.sql = sql
        cls.dict_row = staticmethod(dict_row)
        cls.manifest = migration_manifest(ROOT / "migrations")

    def setUp(self):
        self.database_names = []
        self.database_name = self.create_database()

    def create_database(self) -> str:
        database_name = "sqag_migration_test_" + uuid.uuid4().hex
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(self.sql.SQL("create database {}").format(self.sql.Identifier(database_name)))
        self.database_names.append(database_name)
        return database_name

    def tearDown(self):
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            for database_name in reversed(self.database_names):
                connection.execute(
                    "select pg_terminate_backend(pid) from pg_stat_activity "
                    "where datname = %s and pid <> pg_backend_pid()",
                    (database_name,),
                )
                connection.execute(
                    self.sql.SQL("drop database if exists {}").format(self.sql.Identifier(database_name))
                )

    def connect(self, database_name=None) -> PostgresConnectionAdapter:
        raw = self.psycopg.connect(
            postgres_test_conninfo(database_name or self.database_name),
            row_factory=self.dict_row,
        )
        return PostgresConnectionAdapter(raw)

    def apply(self, migrations=None, database_name=None):
        connection = self.connect(database_name)
        try:
            result = apply_postgres_migrations(connection, migrations or self.manifest)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def inspect(self, migrations=None, database_name=None):
        connection = self.connect(database_name)
        try:
            connection.execute("set transaction read only")
            return inspect_postgres_migrations(connection, migrations or self.manifest)
        finally:
            connection.rollback()
            connection.close()

    def public_tables(self) -> set[str]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select tablename from pg_catalog.pg_tables where schemaname = 'public'"
            ).fetchall()
            return {str(row["tablename"]) for row in rows}
        finally:
            connection.rollback()
            connection.close()

    def test_fresh_apply_complete_ledger_and_second_run_noop(self):
        first = self.apply()
        second = self.apply()
        report = self.inspect()

        self.assertEqual(first["appliedNow"], [migration.migration_id for migration in self.manifest])
        self.assertEqual(second["appliedNow"], [])
        self.assertTrue(EXPECTED_TABLES.issubset(self.public_tables()))
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["pendingMigrationIds"], [])
        self.assertEqual(report["appliedMigrationIds"], [migration.migration_id for migration in self.manifest])

        connection = self.connect()
        try:
            rows = connection.execute(
                "select sequence_no, migration_id, checksum_sha256 from public.sqag_schema_migrations order by sequence_no"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual([row["sequence_no"] for row in rows], list(range(1, len(self.manifest) + 1)))
        self.assertEqual([row["checksum_sha256"] for row in rows], [item.checksum_sha256 for item in self.manifest])

        connection = self.connect()
        try:
            indexes = {
                row["indexname"]
                for row in connection.execute(
                    "select indexname from pg_catalog.pg_indexes where schemaname = 'public'"
                ).fetchall()
            }
            triggers = {
                row["trigger_name"]
                for row in connection.execute(
                    "select trigger_name from information_schema.triggers where trigger_schema = 'public'"
                ).fetchall()
            }
            routines = {
                row["routine_name"]
                for row in connection.execute(
                    "select routine_name from information_schema.routines where routine_schema = 'public'"
                ).fetchall()
            }
            routine_identities = {
                (row["proname"], row["identity_arguments"])
                for row in connection.execute(
                    "select p.proname, pg_get_function_identity_arguments(p.oid) as identity_arguments "
                    "from pg_catalog.pg_proc p "
                    "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                    "where n.nspname = 'public' and p.proname like 'sqag_%'"
                ).fetchall()
            }
        finally:
            connection.rollback()
            connection.close()
        self.assertTrue(EXPECTED_INDEXES.issubset(indexes))
        self.assertTrue(EXPECTED_TRIGGERS.issubset(triggers))
        self.assertTrue(EXPECTED_ROUTINES.issubset(routines))
        self.assertEqual(routine_identities & EXPECTED_ROUTINE_KEYS, EXPECTED_ROUTINE_KEYS)
        self.assertEqual(EXPECTED_CALLABLE_ROUTINE_KEYS, {("sqag_quote_session_deletion_hold_blocked", "text, text")})
        self.assertEqual(EXPECTED_TRIGGER_ROUTINE_KEYS, {
            ("sqag_reject_immutable_change", ""),
            ("sqag_require_retention_delete_authorization", ""),
        })

    def test_lf_and_crlf_create_equivalent_stored_routine_definitions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lf_manifest = write_migration_copy(root / "lf", b"\n")
            crlf_manifest = write_migration_copy(root / "crlf", b"\r\n")
            crlf_database = self.create_database()

            self.apply(lf_manifest)
            self.apply(crlf_manifest, crlf_database)

            routine_sources = []
            for database_name in (self.database_name, crlf_database):
                connection = self.connect(database_name)
                try:
                    row = connection.execute(
                        "select prosrc from pg_catalog.pg_proc p "
                        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                        "where n.nspname = 'public' "
                        "and p.proname = 'sqag_require_retention_delete_authorization'"
                    ).fetchone()
                    routine_sources.append(row["prosrc"])
                finally:
                    connection.rollback()
                    connection.close()

        self.assertEqual(routine_sources[0], routine_sources[1])
        self.assertNotIn("\r", routine_sources[0])

    def test_cross_eol_manifest_accepts_existing_ledger_and_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lf_manifest = write_migration_copy(root / "lf", b"\n")
            crlf_manifest = write_migration_copy(root / "crlf", b"\r\n")

            self.apply(lf_manifest)
            report = self.inspect(crlf_manifest)

            connection = self.connect()
            try:
                rows = connection.execute(
                    "select checksum_sha256 from public.sqag_schema_migrations order by sequence_no"
                ).fetchall()
            finally:
                connection.rollback()
                connection.close()

        self.assertEqual(
            [migration.checksum_sha256 for migration in lf_manifest],
            [migration.checksum_sha256 for migration in crlf_manifest],
        )
        self.assertEqual(
            [row["checksum_sha256"] for row in rows],
            [migration.checksum_sha256 for migration in crlf_manifest],
        )
        self.assertTrue(report["safeToApply"])
        self.assertEqual(report["pendingMigrationIds"], [])

    def test_checksum_modification_fails_closed(self):
        self.apply()
        modified = (replace(self.manifest[0], checksum_sha256="0" * 64), *self.manifest[1:])

        report = self.inspect(modified)
        self.assertFalse(report["safeToApply"])
        self.assertEqual(report["blockers"], [f"checksum_drift:{self.manifest[0].migration_id}"])
        with self.assertRaises(MigrationSafetyError):
            self.apply(modified)

    def test_unexpected_ledger_entry_fails_closed(self):
        self.apply()
        connection = self.connect()
        try:
            connection.execute(
                "insert into public.sqag_schema_migrations (sequence_no, migration_id, checksum_sha256) values (?, ?, ?)",
                (len(self.manifest) + 1, "999_unexpected.sql", "f" * 64),
            )
            connection.commit()
        finally:
            connection.close()

        report = self.inspect()
        self.assertFalse(report["safeToApply"])
        self.assertIn("unexpected_applied_migration", report["blockers"])

    def test_concurrent_execution_is_serialized_by_advisory_lock(self):
        lock_holder = self.connect()
        lock_holder.execute("select pg_catalog.pg_advisory_xact_lock(?)", (MIGRATION_LOCK_KEY,))
        finished = threading.Event()
        errors = []

        def run_migration():
            try:
                self.apply()
            except Exception as exc:  # pragma: no cover - asserted through errors
                errors.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=run_migration, daemon=True)
        thread.start()
        time.sleep(0.25)
        self.assertFalse(finished.is_set())
        lock_holder.rollback()
        lock_holder.close()
        thread.join(timeout=15)

        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(self.inspect()["pendingMigrationIds"], [])

    def test_read_only_preflight_makes_no_mutations(self):
        report = self.inspect()

        self.assertTrue(report["safeToApply"])
        self.assertEqual(report["ledgerState"], "missing")
        self.assertEqual(report["pendingMigrationIds"], [migration.migration_id for migration in self.manifest])
        self.assertEqual(self.public_tables(), set())

    def test_failed_migration_does_not_record_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "001_failure.sql"
            payload = "create table should_rollback (id integer); select from invalid syntax;"
            path.write_text(payload, encoding="utf-8")
            failing = (
                Migration(
                    sequence_no=1,
                    migration_id=path.name,
                    path=path,
                    checksum_sha256=sha256(payload.encode("utf-8")).hexdigest(),
                ),
            )
            with self.assertRaises(Exception):
                self.apply(failing)

        self.assertNotIn(LEDGER_TABLE, self.public_tables())
        self.assertNotIn("should_rollback", self.public_tables())

    def test_existing_unledgered_schema_is_not_silently_adopted(self):
        connection = self.connect()
        try:
            connection.execute("create table existing_untrusted_schema (id integer)")
            connection.commit()
        finally:
            connection.close()

        report = self.inspect()
        self.assertEqual(report["blockers"], ["existing_schema_without_trusted_ledger"])
        with self.assertRaises(MigrationSafetyError):
            self.apply()
        self.assertNotIn(LEDGER_TABLE, self.public_tables())

    def test_empty_ledger_does_not_adopt_preexisting_sqag_tables(self):
        connection = self.connect()
        try:
            connection.execute(
                "create table public.sqag_schema_migrations ("
                "sequence_no integer unique, migration_id text primary key, "
                "checksum_sha256 char(64), applied_at timestamptz default current_timestamp)"
            )
            connection.execute("create table sqag_profiles (id integer)")
            connection.commit()
        finally:
            connection.close()

        report = self.inspect()
        self.assertFalse(report["safeToApply"])
        self.assertEqual(
            report["blockers"],
            ["schema_ledger_inconsistent_unapplied_tables:sqag_profiles"],
        )


if __name__ == "__main__":
    unittest.main()
