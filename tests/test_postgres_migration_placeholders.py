"""Catch psycopg placeholder failures before disposable PostgreSQL is available."""

from pathlib import Path
import re
import unittest
from unittest.mock import patch

from webapp.postgres_migrations import canonical_migration_payload, execute_migration_sql, migration_manifest
from webapp.server import PostgresConnectionAdapter, postgres_query

try:
    from psycopg import ProgrammingError
    from psycopg import _queries
except ImportError:
    QUERY_PARSER = None
    ProgrammingError = None
else:
    QUERY_PARSER = getattr(_queries, "_query2pg", None)


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def assert_parameter_safe(source):
    payload = source.encode("utf-8")
    if QUERY_PARSER is None:
        # Migrations have zero bind arguments. Every percent, even in a SQL
        # string/comment, must be paired; the scanner is SQL-context unaware.
        if re.search(rb"%", payload.replace(b"%%", b"")):
            raise AssertionError("migration SQL has an unpaired percent")
    else:
        try:
            _, formats, _, _ = QUERY_PARSER(payload, "utf-8")
        except ProgrammingError as exc:
            raise AssertionError("migration SQL fails psycopg placeholder parsing") from exc
        if formats:
            raise AssertionError("migration SQL requires unexpected bind arguments")


class ParsingConnection:
    def __init__(self):
        self.statements = 0

    def execute(self, sql, params):
        if params != ():
            raise AssertionError("migration statement must have no bind arguments")
        assert_parameter_safe(sql)
        self.statements += 1


class PostgresMigrationPlaceholderTest(unittest.TestCase):
    def test_every_runner_migration_parses_with_empty_psycopg_parameters(self):
        migrations = migration_manifest(MIGRATIONS_DIR)
        self.assertTrue(migrations)
        for migration in migrations:
            with self.subTest(migration=migration.migration_id):
                source = canonical_migration_payload(migration.path).decode("utf-8")
                # Scan the entire source as well as the actual split/translated
                # statements. Comments and routine strings need escaping too.
                assert_parameter_safe(postgres_query(source))
                raw = ParsingConnection()
                execute_migration_sql(PostgresConnectionAdapter(raw), source)
                self.assertGreater(raw.statements, 0)

    def test_scanner_rejects_operators_strings_comments_and_unbound_placeholders(self):
        for source in ("select 1 % 60", "select '%s'", "-- bare % comment\nselect 1", "select %(name)s"):
            with self.subTest(source=source):
                with self.assertRaises(AssertionError):
                    assert_parameter_safe(source)
        assert_parameter_safe("select '%%', 1 -- paired %% comment")

    def test_fallback_rejects_every_unpaired_percent_without_skipping_the_guard(self):
        with patch(__name__ + ".QUERY_PARSER", None):
            self.test_scanner_rejects_operators_strings_comments_and_unbound_placeholders()
            for source in ("select '%b'", "select '%t'", "-- %", "-- %%%"):
                with self.subTest(source=source):
                    with self.assertRaises(AssertionError):
                        assert_parameter_safe(source)
