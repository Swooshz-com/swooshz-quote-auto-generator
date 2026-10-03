import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROLES = ("sqag_migrator", "sqag_runtime", "sqag_maintenance")
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (
    CatalogProjectionError,
    ColumnSpec,
    ConstraintSpec,
    EXPECTED_CALLABLE_ROUTINE_KEYS,
    EXPECTED_INDEXES,
    EXPECTED_ROUTINES,
    EXPECTED_ROUTINE_KEYS,
    EXPECTED_TRIGGER_ROUTINE_KEYS,
    EXPECTED_TABLES,
    EXPECTED_TRIGGER_KEYS,
    LEDGER_TABLE,
    MIGRATION_LOCK_KEY,
    MIGRATION_FILE_NAMES,
    Migration,
    MigrationSafetyError,
    TableSpec,
    _fetch_public_indexes,
    _constraint_fingerprint,
    _effective_table_spec,
    _observed_constraint_fingerprint,
    _table_matches,
    TABLE_SPECS_BY_NAME,
    canonicalize_check_expression,
    execute_migration_sql,
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


def safe_postgres_url(user: str, database_name: str) -> str:
    conninfo = postgres_test_conninfo(database_name)
    if not conninfo:
        raise unittest.SkipTest("disposable PostgreSQL-17 service is not configured")
    parts = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in conninfo.split()
        if "=" in item
    }
    from urllib.parse import quote

    return (
        f"postgresql://{quote(user, safe='')}@{quote(parts['host'], safe='')}:{quote(parts['port'], safe='')}/"
        f"{quote(database_name, safe='')}"
    )


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
    def test_telemetry_attempt_constraint_is_prefix_aware_and_named_at_010(self):
        final_spec = TABLE_SPECS_BY_NAME["sqag_telemetry_events"]
        before_010 = _effective_table_spec("sqag_telemetry_events", 8)
        self.assertEqual(
            len([item for item in before_010.constraints if item.kind == "c" and "attempt_number" in (item.expression or "")]),
            1,
        )
        self.assertNotIn("attempt_number = 0", next(
            item.expression for item in before_010.constraints
            if item.kind == "c" and "attempt_number" in (item.expression or "")
        ))

        def observed_table(spec, *, attempt_name=None, attempt_validated=True, duplicate_attempt=False):
            constraints = []
            for index, item in enumerate(spec.constraints):
                observed = {
                    "name": item.name or f"sqag_events_check_{index}",
                    "kind": item.kind,
                    "columns": item.columns,
                    "referenced_schema": item.referenced_schema,
                    "referenced_table": item.referenced_table,
                    "referenced_columns": item.referenced_columns,
                    "match_type": item.match_type,
                    "on_delete": item.on_delete,
                    "on_update": item.on_update,
                    "expression": item.expression,
                    "validated": attempt_validated if "attempt_number" in (item.expression or "") else item.validated,
                    "deferrable": item.deferrable,
                    "deferred": item.deferred,
                }
                if "attempt_number" in (item.expression or "") and attempt_name is not None:
                    observed["name"] = attempt_name
                constraints.append(observed)
                if "attempt_number" in (item.expression or "") and duplicate_attempt:
                    constraints.append(dict(observed, name="sqag_duplicate_attempt_check"))
            return {
                "relation": {
                    "name": spec.name,
                    "relkind": "r",
                    "relpersistence": "p",
                    "relispartition": False,
                    "owner": spec.owner,
                },
                "columns": [
                    (
                        column.name,
                        column.type_name,
                        column.nullable,
                        column.default_sql,
                        column.identity,
                        column.generated,
                    )
                    for column in spec.columns
                ],
                "constraints": constraints,
            }

        historical_actual = observed_table(before_010, attempt_name="sqag_telemetry_events_attempt_number_check")
        self.assertTrue(_table_matches(historical_actual, before_010))
        successor_actual = observed_table(final_spec)
        self.assertTrue(_table_matches(successor_actual, final_spec))
        self.assertFalse(_table_matches(successor_actual, before_010))
        self.assertFalse(_table_matches(
            observed_table(final_spec, attempt_name="renamed_attempt_check"),
            final_spec,
        ))
        self.assertFalse(_table_matches(
            observed_table(final_spec, duplicate_attempt=True),
            final_spec,
        ))
        self.assertFalse(_table_matches(
            observed_table(final_spec, attempt_validated=False),
            final_spec,
        ))
        missing_attempt = observed_table(before_010)
        missing_attempt["constraints"] = [
            item for item in missing_attempt["constraints"]
            if "attempt_number" not in (item.get("expression") or "")
        ]
        self.assertFalse(_table_matches(missing_attempt, before_010))

    def test_check_deparse_canonicalizer_allows_only_bounded_equivalences(self):
        table = TableSpec(
            "fixture",
            (
                ColumnSpec("varchar_value", "varchar"),
                ColumnSpec("char_value", "char(64)"),
                ColumnSpec("integer_value", "integer"),
            ),
        )
        equivalent = (
            (
                "varchar_value::text ~ '^[a-z]+$'",
                "varchar_value ~ '^[a-z]+$'",
            ),
            (
                "char_value::text !~ 'x'",
                "char_value !~ 'x'",
            ),
            (
                "'received'::text",
                "'received'",
            ),
            (
                "now()",
                "current_timestamp",
            ),
            (
                "status = ANY (ARRAY['received', 'queued'])",
                "status IN ('received', 'queued')",
            ),
            (
                "status = ANY (ARRAY['received'::text, 'queued'::text]::text[])",
                "status IN ('received', 'queued')",
            ),
            (
                "((status = 'received'))",
                "status = 'received'",
            ),
        )
        for first, second in equivalent:
            with self.subTest(first=first):
                self.assertEqual(
                    canonicalize_check_expression(first, table),
                    canonicalize_check_expression(second, table),
                )

        semantic_drift = (
            ("varchar_value::text = 'x'", "varchar_value = 'x'"),
            ("char_value::text = 'x'", "char_value = 'x'"),
            ("integer_value::text = '1'", "integer_value = '1'"),
            ("status = ANY (ARRAY[status])", "status IN (status)"),
            ("""varchar_value COLLATE "C" = 'x'""", "varchar_value = 'x'"),
        )
        for first, second in semantic_drift:
            with self.subTest(first=first):
                self.assertNotEqual(
                    canonicalize_check_expression(first, table),
                    canonicalize_check_expression(second, table),
                )

        self.assertNotEqual(
            canonicalize_check_expression("status = (", table),
            canonicalize_check_expression("status = 'received'", table),
        )

    def test_index_catalog_projection_is_exact_and_typed(self):
        fields = (
            "indexrelid", "index_name", "table_name", "indisunique",
            "indisvalid", "indisready", "constraint_backed", "owner",
            "predicate", "key_definitions",
        )

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class Connection:
            def __init__(self, row):
                self.row = row
                self.sql = ""

            def execute(self, sql):
                self.sql = sql
                return Cursor([self.row])

        valid = {
            "indexrelid": 1,
            "index_name": "sqag_fixture_idx",
            "table_name": "sqag_fixture",
            "indisunique": False,
            "indisvalid": True,
            "indisready": True,
            "constraint_backed": False,
            "owner": "sqag_migrator",
            "predicate": None,
            "key_definitions": ["workspace_id"],
        }
        connection = Connection(valid)
        result = _fetch_public_indexes(connection)
        self.assertEqual(result["sqag_fixture_idx"]["constraint_backed"], False)
        self.assertIn("as constraint_backed", connection.sql)
        self.assertIn("exists", connection.sql.lower())
        self.assertEqual(set(valid), set(fields))

        for mutation in (
            lambda row: row.pop("constraint_backed"),
            lambda row: row.update(unexpected_alias=True),
            lambda row: row.update(constraint_backed="false"),
            lambda row: row.update(key_definitions="workspace_id"),
        ):
            candidate = dict(valid)
            mutation(candidate)
            with self.subTest(candidate=candidate):
                with self.assertRaises(CatalogProjectionError):
                    _fetch_public_indexes(Connection(candidate))

        tuple_row = (
            17,
            "sqag_tuple_idx",
            "sqag_tuple_table",
            True,
            False,
            True,
            False,
            "tuple_owner",
            "workspace_id > 0",
            ["workspace_id", "profile_id"],
        )
        tuple_result = _fetch_public_indexes(Connection(tuple_row))
        self.assertEqual(
            tuple_result["sqag_tuple_idx"],
            {
                "oid": 17,
                "name": "sqag_tuple_idx",
                "table_name": "sqag_tuple_table",
                "unique": True,
                "valid": False,
                "ready": True,
                "constraint_backed": False,
                "owner": "tuple_owner",
                "predicate": "workspace_id > 0",
                "key_definitions": ("workspace_id", "profile_id"),
            },
        )
        with self.assertRaises(CatalogProjectionError):
            _fetch_public_indexes(Connection(tuple_row[:-1]))
        with self.assertRaises(CatalogProjectionError):
            _fetch_public_indexes(Connection(tuple_row + ("overlong",)))

        wrong_type_rows = (
            ("17", *tuple_row[1:]),
            (tuple_row[0], 17, *tuple_row[2:]),
            (tuple_row[0], tuple_row[1], 17, *tuple_row[3:]),
            (tuple_row[0], tuple_row[1], tuple_row[2], "true", *tuple_row[4:]),
            (tuple_row[0], tuple_row[1], tuple_row[2], tuple_row[3], "false", *tuple_row[5:]),
            (tuple_row[0], tuple_row[1], tuple_row[2], tuple_row[3], tuple_row[4], 1, *tuple_row[6:]),
            (tuple_row[0], tuple_row[1], tuple_row[2], tuple_row[3], tuple_row[4], tuple_row[5], None, *tuple_row[7:]),
            (tuple_row[0], tuple_row[1], tuple_row[2], tuple_row[3], tuple_row[4], tuple_row[5], tuple_row[6], None, *tuple_row[8:]),
            (tuple_row[0], tuple_row[1], tuple_row[2], tuple_row[3], tuple_row[4], tuple_row[5], tuple_row[6], tuple_row[7], 17, tuple_row[9]),
            (tuple_row[0], tuple_row[1], tuple_row[2], tuple_row[3], tuple_row[4], tuple_row[5], tuple_row[6], tuple_row[7], tuple_row[8], "workspace_id"),
        )
        for candidate in wrong_type_rows:
            with self.subTest(candidate=candidate):
                with self.assertRaises(CatalogProjectionError):
                    _fetch_public_indexes(Connection(candidate))

    def test_fk_identity_includes_referenced_schema_and_match_type(self):
        expected = ConstraintSpec(
            kind="f",
            columns=("local_id",),
            referenced_schema="public",
            referenced_table="sqag_parent",
            referenced_columns=("id",),
            match_type="s",
            on_delete="c",
            on_update="a",
        )
        observed = {
            "kind": "f",
            "columns": ("local_id",),
            "referenced_schema": "public",
            "referenced_table": "sqag_parent",
            "referenced_columns": ("id",),
            "match_type": "s",
            "on_delete": "c",
            "on_update": "a",
            "validated": True,
            "deferrable": False,
            "deferred": False,
        }
        self.assertEqual(
            _constraint_fingerprint(expected),
            _observed_constraint_fingerprint(observed),
        )
        for field, value in (
            ("referenced_schema", "other_schema"),
            ("match_type", "f"),
            ("columns", ("other_local",)),
            ("referenced_columns", ("other_id",)),
            ("referenced_table", "other_parent"),
            ("on_delete", "r"),
            ("on_update", "c"),
            ("validated", False),
            ("deferrable", True),
            ("deferred", True),
        ):
            candidate = dict(observed)
            candidate[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(
                    _constraint_fingerprint(expected),
                    _observed_constraint_fingerprint(candidate),
                )

    def test_migration_splitter_preserves_dollar_quoted_routine_bodies(self):
        connection = RecordingConnection()
        execute_migration_sql(
            connection,
            "create table sqag_split_probe (id integer);"
            "-- SQAG_STATEMENT_BOUNDARY"
            "create function sqag_split_probe() returns trigger "
            "language plpgsql as $$ begin raise exception 'x; y'; end $$"
            "-- SQAG_STATEMENT_BOUNDARY"
            "drop table sqag_split_probe",
        )
        self.assertEqual(len(connection.calls), 3)
        self.assertIn("raise exception 'x; y'", connection.calls[1][0])
        self.assertNotIn("raise exception 'x", connection.calls[0][0])

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
        self.created_roles = []
        self.cleanup_done = False
        self.addCleanup(self._cleanup_fixture)
        self._create_roles()
        self.database_name = self.create_database()

    def _create_roles(self):
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            existing = connection.execute(
                "select rolname from pg_catalog.pg_roles where rolname = any(%s)",
                (list(MIGRATION_ROLES),),
            ).fetchall()
            if existing:
                raise RuntimeError("CLEANUP_UNKNOWN")
            for role in MIGRATION_ROLES:
                connection.execute(
                    self.sql.SQL(
                        "create role {} login nosuperuser nocreatedb nocreaterole "
                        "noreplication nobypassrls noinherit connection limit -1"
                    ).format(self.sql.Identifier(role))
                )
                self.created_roles.append(role)

    def create_database(self) -> str:
        database_name = "sqag_migration_test_" + uuid.uuid4().hex
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(self.sql.SQL("create database {}").format(self.sql.Identifier(database_name)))
        self.database_names.append(database_name)
        with self.psycopg.connect(postgres_test_conninfo(database_name), autocommit=True) as connection:
            database = self.sql.Identifier(database_name)
            for grantee in ("PUBLIC", *MIGRATION_ROLES):
                connection.execute(
                    self.sql.SQL("revoke all privileges on database {} from {}").format(
                        database,
                        self.sql.SQL("PUBLIC") if grantee == "PUBLIC" else self.sql.Identifier(grantee),
                    )
                )
            connection.execute(
                self.sql.SQL("grant connect on database {} to sqag_migrator").format(database)
            )
            connection.execute(
                "revoke all privileges on schema public from PUBLIC, sqag_runtime, "
                "sqag_migrator, sqag_maintenance"
            )
            connection.execute("grant usage, create on schema public to sqag_migrator")
        return database_name

    def tearDown(self):
        self._cleanup_fixture()

    def _cleanup_fixture(self):
        if self.cleanup_done:
            return
        self.cleanup_done = True
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
            for role in reversed(self.created_roles):
                connection.execute(
                    self.sql.SQL("drop role if exists {}").format(
                        self.sql.Identifier(role)
                    )
                )
            residual_roles = connection.execute(
                "select rolname from pg_catalog.pg_roles where rolname = any(%s)",
                (list(MIGRATION_ROLES),),
            ).fetchall() if len(self.created_roles) == len(MIGRATION_ROLES) else []
            if residual_roles:
                raise RuntimeError("CLEANUP_UNKNOWN")

    def connect(self, database_name=None) -> PostgresConnectionAdapter:
        raw = self.psycopg.connect(
            safe_postgres_url("sqag_migrator", database_name or self.database_name),
            row_factory=self.dict_row,
        )
        connection = PostgresConnectionAdapter(raw)
        identity = connection.execute("select session_user, current_user").fetchone()
        if identity["session_user"] != "sqag_migrator" or identity["current_user"] != "sqag_migrator":
            connection.close()
            raise AssertionError("migration fixture did not establish true migrator identity")
        return connection

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

    def attempt_constraints(self, connection):
        return connection.execute(
            "select constraint_row.conname, constraint_row.convalidated, "
            "pg_catalog.pg_get_constraintdef(constraint_row.oid) as definition "
            "from pg_catalog.pg_constraint constraint_row "
            "where constraint_row.conrelid = 'public.sqag_telemetry_events'::regclass "
            "and constraint_row.contype = 'c' "
            "and pg_catalog.pg_get_constraintdef(constraint_row.oid) ilike '%%attempt_number%%' "
            "order by constraint_row.conname"
        ).fetchall()

    def replace_attempt_constraint(self, connection, name: str, expression: str, *, not_valid: bool = False):
        for row in self.attempt_constraints(connection):
            connection._connection.execute(
                self.sql.SQL("alter table public.sqag_telemetry_events drop constraint {}").format(
                    self.sql.Identifier(row["conname"])
                )
            )
        suffix = " not valid" if not_valid else ""
        connection._connection.execute(
            self.sql.SQL("alter table public.sqag_telemetry_events add constraint {} check ({})" + suffix).format(
                self.sql.Identifier(name),
                self.sql.SQL(expression),
            )
        )

    def test_attempt_semantics_010_migrates_renamed_check_and_preserves_rows(self):
        self.apply(self.manifest[:8])
        connection = self.connect()
        try:
            now = "2026-10-02T00:00:00Z"
            connection.execute(
                "insert into public.sqag_telemetry_source_state "
                "(workspace_id, source_product, created_at, updated_at) values (%s, %s, %s, %s)",
                ("workspace-010", "sqag", now, now),
            )
            connection.execute(
                "insert into public.sqag_telemetry_events "
                "(workspace_id, event_id, source_product, source_sequence, event_type, event_status, "
                "actor_tracking_id, actor_key_version, retry_lineage_id, attempt_number, provider, model, "
                "occurred_at, immutable_metadata_digest, retention_expires_at, original_retention_expires_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    "workspace-010", "event-before-010", "sqag", 1, "ai_provider_attempt", "success",
                    "actor-010", "actor-key-v1", "lineage-010", 1, "openai", "gpt-6-luna",
                    now, "a" * 64, "2027-10-02T00:00:00Z", "2027-07-02T00:00:00Z",
                ),
            )
            historical = self.attempt_constraints(connection)
            self.assertEqual(len(historical), 1)
            old_name = historical[0]["conname"]
            connection._connection.execute(
                self.sql.SQL(
                    "alter table public.sqag_telemetry_events rename constraint {} to {}"
                ).format(
                    self.sql.Identifier(old_name),
                    self.sql.Identifier("sqag_telemetry_events_attempt_history_renamed"),
                )
            )
            before = connection.execute(
                "select workspace_id, event_id, source_sequence, retry_lineage_id, attempt_number, model "
                "from public.sqag_telemetry_events where event_id = %s",
                ("event-before-010",),
            ).fetchone()
            connection.commit()
        finally:
            connection.close()

        result = self.apply(self.manifest[:9])
        self.assertEqual(result["appliedNow"], [self.manifest[8].migration_id])
        report = self.inspect()
        self.assertEqual(report["status"], "ready")
        connection = self.connect()
        try:
            successor = self.attempt_constraints(connection)
            self.assertEqual(len(successor), 1)
            self.assertEqual(successor[0]["conname"], "sqag_telemetry_events_attempt_semantics_ck")
            self.assertTrue(successor[0]["convalidated"])
            after = connection.execute(
                "select workspace_id, event_id, source_sequence, retry_lineage_id, attempt_number, model "
                "from public.sqag_telemetry_events where event_id = %s",
                ("event-before-010",),
            ).fetchone()
            self.assertEqual(after, before)
            now = "2026-10-02T00:00:00Z"
            connection.execute(
                "insert into public.sqag_telemetry_events "
                "(workspace_id, event_id, source_product, source_sequence, event_type, event_status, "
                "actor_tracking_id, actor_key_version, retry_lineage_id, attempt_number, purpose, failure_class, "
                "usage_available, cost_available, occurred_at, immutable_metadata_digest, retention_expires_at, "
                "original_retention_expires_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    "workspace-010", "event-zero-valid", "sqag", 2, "validation", "blocked",
                    "actor-010", "actor-key-v1", "lineage-zero-010", 0, "request_validation", "configuration",
                    0, 0, now, "b" * 64, "2027-10-02T00:00:00Z", "2027-07-02T00:00:00Z",
                ),
            )
            connection.commit()
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                connection.execute(
                    "insert into public.sqag_telemetry_events "
                    "(workspace_id, event_id, source_product, source_sequence, event_type, event_status, "
                    "actor_tracking_id, actor_key_version, retry_lineage_id, attempt_number, purpose, failure_class, "
                    "model, usage_available, cost_available, occurred_at, immutable_metadata_digest, retention_expires_at, "
                    "original_retention_expires_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        "workspace-010", "event-zero-invalid", "sqag", 3, "ai_provider_attempt", "failed",
                        "actor-010", "actor-key-v1", "lineage-zero-invalid", 0, "basis_chat", "provider_error",
                        "gpt-6-luna", 0, 0, now, "c" * 64, "2027-10-02T00:00:00Z", "2027-07-02T00:00:00Z",
                    ),
                )
            connection.rollback()
            zero_columns = (
                "workspace_id", "event_id", "source_product", "source_sequence",
                "event_type", "event_status", "actor_tracking_id", "actor_key_version",
                "retry_lineage_id", "attempt_number", "purpose", "failure_class",
                "usage_available", "cost_available", "occurred_at",
                "immutable_metadata_digest", "retention_expires_at",
                "original_retention_expires_at",
            )
            insert_zero_sql = (
                "insert into public.sqag_telemetry_events ("
                + ", ".join(zero_columns)
                + ") values ("
                + ", ".join(["%s"] * len(zero_columns))
                + ")"
            )
            for index, nullable_field in enumerate(
                ("purpose", "failure_class", "usage_available", "cost_available"),
                start=1,
            ):
                malformed_zero = {
                    "workspace_id": "workspace-010",
                    "event_id": f"event-zero-null-{nullable_field}",
                    "source_product": "sqag",
                    "source_sequence": 3 + index,
                    "event_type": "validation",
                    "event_status": "blocked",
                    "actor_tracking_id": "actor-010",
                    "actor_key_version": "actor-key-v1",
                    "retry_lineage_id": None,
                    "attempt_number": 0,
                    "purpose": "request_validation",
                    "failure_class": "configuration",
                    "usage_available": 0,
                    "cost_available": 0,
                    "occurred_at": now,
                    "immutable_metadata_digest": "d" * 64,
                    "retention_expires_at": "2027-10-02T00:00:00Z",
                    "original_retention_expires_at": "2027-07-02T00:00:00Z",
                }
                malformed_zero[nullable_field] = None
                with self.subTest(nullable_field=nullable_field):
                    with self.assertRaises(self.psycopg.errors.CheckViolation):
                        connection.execute(
                            insert_zero_sql,
                            tuple(malformed_zero[column] for column in zero_columns),
                        )
                    connection.rollback()
        finally:
            connection.close()
        self.assertEqual(self.apply()["appliedNow"], [])

    def test_attempt_semantics_prefix_mismatches_fail_closed(self):
        historical_expression = "attempt_number is null or attempt_number >= 1"
        successor_expression = (
            "attempt_number is null or attempt_number >= 1 or ("
            "(attempt_number = 0) and (event_type = 'validation') and (event_status = 'blocked') and "
            "(purpose is not null) and (purpose = 'request_validation') and (failure_class is not null) and "
            "(failure_class = 'configuration') and (model is null) and "
            "(usage_available is not null) and (usage_available = 0) and "
            "(cost_available is not null) and (cost_available = 0) and (input_tokens is null) and (output_tokens is null) and "
            "(total_tokens is null) and (cache_read_tokens is null) and (cache_write_tokens is null) and "
            "(estimated_cost is null) and (actual_cost is null) and (currency is null) and (cost_version is null))"
        )

        for case in ("missing", "duplicate", "drifted", "unvalidated", "premature"):
            with self.subTest(case=case):
                database_name = self.create_database()
                self.apply(self.manifest[:8], database_name)
                connection = self.connect(database_name)
                try:
                    if case == "missing":
                        row = self.attempt_constraints(connection)[0]
                        connection._connection.execute(
                            self.sql.SQL("alter table public.sqag_telemetry_events drop constraint {}").format(
                                self.sql.Identifier(row["conname"])
                            )
                        )
                    elif case == "duplicate":
                        connection.execute(
                            "alter table public.sqag_telemetry_events add constraint sqag_duplicate_attempt_check "
                            "check (attempt_number is null or attempt_number >= 1)"
                        )
                    elif case == "drifted":
                        self.replace_attempt_constraint(connection, "sqag_drifted_attempt_check", "attempt_number is null or attempt_number >= 2")
                    elif case == "unvalidated":
                        self.replace_attempt_constraint(
                            connection,
                            "sqag_unvalidated_attempt_check",
                            historical_expression,
                            not_valid=True,
                        )
                    else:
                        self.replace_attempt_constraint(
                            connection,
                            "sqag_telemetry_events_attempt_semantics_ck",
                            successor_expression,
                        )
                    connection.commit()
                finally:
                    connection.close()
                report = self.inspect(self.manifest[:9], database_name)
                self.assertNotEqual(report["status"], "ready")
                with self.assertRaises(MigrationSafetyError):
                    self.apply(self.manifest[:9], database_name)

        stale_database = self.create_database()
        self.apply(self.manifest, stale_database)
        connection = self.connect(stale_database)
        try:
            self.replace_attempt_constraint(connection, "sqag_old_attempt_check", historical_expression)
            connection.commit()
        finally:
            connection.close()
        report = self.inspect(self.manifest, stale_database)
        self.assertNotEqual(report["status"], "ready")
        with self.assertRaises(MigrationSafetyError):
            self.apply(self.manifest, stale_database)

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
            trigger_identities = [
                (
                    row["table_schema"],
                    row["table_name"],
                    row["trigger_name"],
                )
                for row in connection.execute(
                    "select table_namespace.nspname as table_schema, "
                    "table_class.relname as table_name, trigger_row.tgname as trigger_name "
                    "from pg_catalog.pg_trigger trigger_row "
                    "join pg_catalog.pg_class table_class "
                    "on table_class.oid = trigger_row.tgrelid "
                    "join pg_catalog.pg_namespace table_namespace "
                    "on table_namespace.oid = table_class.relnamespace "
                    "where table_namespace.nspname = 'public' "
                    "and not trigger_row.tgisinternal "
                    "order by table_namespace.nspname, table_class.relname, "
                    "trigger_row.tgname, trigger_row.oid"
                ).fetchall()
            ]
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
                    "where n.nspname = 'public' and p.proname like ?",
                    ("sqag_%",),
                ).fetchall()
            }
        finally:
            connection.rollback()
            connection.close()
        self.assertTrue(EXPECTED_INDEXES.issubset(indexes))
        self.assertEqual(
            Counter(
                identity
                for identity in trigger_identities
                if identity[2].startswith("sqag_")
            ),
            Counter(EXPECTED_TRIGGER_KEYS),
        )
        self.assertTrue(EXPECTED_ROUTINES.issubset(routines))
        self.assertEqual(routine_identities & EXPECTED_ROUTINE_KEYS, EXPECTED_ROUTINE_KEYS)
        self.assertEqual(
            EXPECTED_CALLABLE_ROUTINE_KEYS,
            {
                ("sqag_quote_session_deletion_hold_blocked", "text, text"),
                ("sqag_quote_session_deletion_hold_blocked_v2", "text, text"),
            },
        )
        self.assertEqual(
            EXPECTED_TRIGGER_ROUTINE_KEYS,
            {
                ("sqag_reject_immutable_change", ""),
                ("sqag_require_retention_delete_authorization", ""),
            },
        )

    def test_real_pg17_fk_identity_matrix_fails_closed_and_restores(self):
        self.apply()
        with self.psycopg.connect(
            postgres_test_conninfo(self.database_name),
            row_factory=self.dict_row,
            autocommit=True,
        ) as connection:
            connection.execute("create schema provider_fk_schema")
            connection.execute(
                "create table provider_fk_schema.provider_fk_runs "
                "(run_id text not null, workspace_id text not null, "
                "primary key (run_id, workspace_id))"
            )
            connection.execute(
                "create table public.provider_fk_runs "
                "(run_id text not null, workspace_id text not null, "
                "primary key (run_id, workspace_id))"
            )
            connection.execute(
                "create unique index provider_fk_reverse_uidx "
                "on public.sqag_generation_runs (workspace_id, run_id)"
            )
            connection.execute(
                "grant usage on schema provider_fk_schema to sqag_migrator"
            )
            connection.execute(
                "grant references on table provider_fk_schema.provider_fk_runs "
                "to sqag_migrator"
            )
            connection.execute(
                "grant references on table public.provider_fk_runs to sqag_migrator"
            )
            constraint_row = connection.execute(
                "select conname from pg_catalog.pg_constraint "
                "where conrelid = 'public.sqag_generation_evidence'::regclass "
                "and contype = 'f'"
            ).fetchone()
            self.assertIsNotNone(constraint_row)
            constraint_name = str(constraint_row["conname"])

        def execute_migrator(statement):
            with self.psycopg.connect(
                safe_postgres_url("sqag_migrator", self.database_name),
                row_factory=self.dict_row,
                options="-c search_path=public,pg_catalog",
                autocommit=True,
            ) as connection:
                connection.execute(statement)

        def drop_constraint(name):
            execute_migrator(
                self.sql.SQL(
                    "alter table public.sqag_generation_evidence "
                    "drop constraint {}"
                ).format(self.sql.Identifier(name))
            )

        def add_constraint(
            name,
            schema,
            table,
            local_columns="run_id, workspace_id",
            referenced_columns="run_id, workspace_id",
            tail="",
        ):
            execute_migrator(
                self.sql.SQL(
                    "alter table public.sqag_generation_evidence "
                    "add constraint {} foreign key ({}) references {}.{} ({}) {}"
                ).format(
                    self.sql.Identifier(name),
                    self.sql.SQL(local_columns),
                    self.sql.Identifier(schema),
                    self.sql.Identifier(table),
                    self.sql.SQL(referenced_columns),
                    self.sql.SQL(tail),
                )
            )

        expected_table = "applied_prefix_drift:table:public.sqag_generation_evidence"
        variants = (
            ("referenced schema", "provider_fk_schema", "provider_fk_runs", "run_id, workspace_id", "run_id, workspace_id", "", False),
            ("match type", "public", "sqag_generation_runs", "run_id, workspace_id", "run_id, workspace_id", "match full", False),
            ("local and referenced order", "public", "sqag_generation_runs", "workspace_id, run_id", "workspace_id, run_id", "", False),
            ("referenced table", "public", "provider_fk_runs", "run_id, workspace_id", "run_id, workspace_id", "", False),
            ("delete action", "public", "sqag_generation_runs", "run_id, workspace_id", "run_id, workspace_id", "on delete cascade", False),
            ("update action", "public", "sqag_generation_runs", "run_id, workspace_id", "run_id, workspace_id", "on update cascade", False),
            ("validation state", "public", "sqag_generation_runs", "run_id, workspace_id", "run_id, workspace_id", "not valid", False),
            ("deferrability state", "public", "sqag_generation_runs", "run_id, workspace_id", "run_id, workspace_id", "deferrable initially deferred", False),
            ("duplicate multiplicity", "public", "sqag_generation_runs", "run_id, workspace_id", "run_id, workspace_id", "", True),
        )
        for label, schema, table, local_columns, referenced_columns, tail, duplicate in variants:
            mutated_name = "sqag_generation_evidence_duplicate_fk" if duplicate else constraint_name
            with self.subTest(fk_drift=label):
                if duplicate:
                    add_constraint(
                        mutated_name,
                        schema,
                        table,
                        local_columns,
                        referenced_columns,
                        tail,
                    )
                else:
                    drop_constraint(constraint_name)
                    add_constraint(
                        mutated_name,
                        schema,
                        table,
                        local_columns,
                        referenced_columns,
                        tail,
                    )
                try:
                    report = self.inspect()
                    self.assertFalse(report["safeToApply"])
                    self.assertIn(expected_table, report["blockers"])
                finally:
                    drop_constraint(mutated_name)
                    if not duplicate:
                        add_constraint(
                            constraint_name,
                            "public",
                            "sqag_generation_runs",
                        )
                self.assertTrue(self.inspect()["safeToApply"])

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

    def test_missing_ledger_allows_unrelated_public_provider_objects(self):
        connection = self.connect()
        try:
            connection.execute("create table provider_noise (id integer)")
            connection.execute("create index provider_noise_idx on provider_noise (id)")
            connection.execute(
                "create function provider_noise_trigger_function() returns trigger "
                "language plpgsql as $$ begin return new; end $$"
            )
            connection.execute(
                "create trigger provider_noise_trigger before insert on provider_noise "
                "for each row execute function provider_noise_trigger_function()"
            )
            connection.commit()
        finally:
            connection.close()

        report = self.inspect()
        self.assertTrue(report["safeToApply"])
        self.assertEqual(report["ledgerState"], "missing")
        self.assertEqual(report["appliedMigrationIds"], [])
        self.assertEqual(
            report["pendingMigrationIds"],
            [migration.migration_id for migration in self.manifest],
        )
        self.assertEqual(report["blockers"], [])

    def test_missing_ledger_rejects_known_and_unknown_sqag_namespace_objects(self):
        connection = self.connect()
        try:
            connection.execute("create table public.sqag_profiles (id integer)")
            connection.commit()
        finally:
            connection.close()

        known_report = self.inspect()
        self.assertFalse(known_report["safeToApply"])
        self.assertIn(
            "pending_suffix_present:table:public.sqag_profiles",
            known_report["blockers"],
        )

        connection = self.connect()
        try:
            connection.execute("drop table public.sqag_profiles")
            connection.execute("create table public.sqag_unexpected (id integer)")
            connection.commit()
        finally:
            connection.close()

        unknown_report = self.inspect()
        self.assertFalse(unknown_report["safeToApply"])
        self.assertIn(
            "managed_namespace_extra:relation:public.sqag_unexpected",
            unknown_report["blockers"],
        )

        connection = self.connect()
        try:
            connection.execute("drop table public.sqag_unexpected")
            connection.execute("create table public.provider_noise (id integer)")
            connection.execute(
                "alter table public.provider_noise add constraint "
                "sqag_provider_unexpected_constraint unique (id)"
            )
            connection.commit()
        finally:
            connection.close()

        constraint_index_report = self.inspect()
        self.assertFalse(constraint_index_report["safeToApply"])
        self.assertIn(
            "managed_namespace_extra:relation:public.sqag_provider_unexpected_constraint",
            constraint_index_report["blockers"],
        )

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
