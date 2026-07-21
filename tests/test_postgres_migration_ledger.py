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


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (
    EXPECTED_INDEXES,
    EXPECTED_ROUTINES,
    EXPECTED_TABLES,
    EXPECTED_TRIGGERS,
    LEDGER_TABLE,
    MIGRATION_LOCK_KEY,
    Migration,
    MigrationSafetyError,
    apply_postgres_migrations,
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
        self.database_name = "sqag_migration_test_" + uuid.uuid4().hex
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(self.sql.SQL("create database {}").format(self.sql.Identifier(self.database_name)))

    def tearDown(self):
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (self.database_name,),
            )
            connection.execute(
                self.sql.SQL("drop database if exists {}").format(self.sql.Identifier(self.database_name))
            )

    def connect(self) -> PostgresConnectionAdapter:
        raw = self.psycopg.connect(
            postgres_test_conninfo(self.database_name),
            row_factory=self.dict_row,
        )
        return PostgresConnectionAdapter(raw)

    def apply(self, migrations=None):
        connection = self.connect()
        try:
            result = apply_postgres_migrations(connection, migrations or self.manifest)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def inspect(self, migrations=None):
        connection = self.connect()
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
        finally:
            connection.rollback()
            connection.close()
        self.assertTrue(EXPECTED_INDEXES.issubset(indexes))
        self.assertTrue(EXPECTED_TRIGGERS.issubset(triggers))
        self.assertTrue(EXPECTED_ROUTINES.issubset(routines))

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
