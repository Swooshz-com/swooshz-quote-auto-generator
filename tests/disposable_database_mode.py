"""Disposable PostgreSQL database for running the app in database storage mode.

Used by tests/internal_password_browser_harness.py --storage database so CI can
exercise the same storage modes live runs (SQAG_STORAGE_MODE=database,
SQAG_ARTIFACT_STORAGE_MODE=object) against the Validate app job's disposable
PostgreSQL service. Synthetic data only; never point it at a real database.

The bootstrap connection comes from SQAG_TEST_POSTGRES_HOST, _PORT and _USER
(trust auth, the CI service's superuser). It creates one uniquely named
database, the three application roles when they are missing, applies the
migrations live has (through 011; 012 stays unapplied there), grants the
runtime contract's table and routine privileges, and drops what it created.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
APP_ROLES = ("sqag_runtime", "sqag_migrator", "sqag_maintenance")
DATABASE_PREFIX = "sqag_ci_database_mode_"
# Live has migrations 001 to 011 applied; 012 (quote API admission) is not.
LIVE_MIGRATION_HEAD = "011_quote_ai_spend_postgres.sql"
ROLE_OPTIONS = (
    "login nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit connection limit -1"
)


class DisposableDatabaseError(RuntimeError):
    pass


def bootstrap_settings(env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if env is None else env
    settings = {
        "host": str(env.get("SQAG_TEST_POSTGRES_HOST") or "").strip(),
        "port": str(env.get("SQAG_TEST_POSTGRES_PORT") or "").strip(),
        "user": str(env.get("SQAG_TEST_POSTGRES_USER") or "").strip(),
    }
    if not all(settings.values()):
        raise DisposableDatabaseError(
            "database mode needs SQAG_TEST_POSTGRES_HOST, SQAG_TEST_POSTGRES_PORT and SQAG_TEST_POSTGRES_USER"
        )
    if settings["host"] not in {"127.0.0.1", "localhost", "::1"}:
        raise DisposableDatabaseError("database mode only uses a loopback PostgreSQL service")
    return settings


def role_url(settings: dict[str, str], role: str, database: str) -> str:
    host = quote(settings["host"], safe="")
    return f"postgresql://{quote(role, safe='')}@{host}:{quote(settings['port'], safe='')}/{quote(database, safe='')}"


def live_migrations():
    from webapp.postgres_migrations import migration_manifest

    migrations = migration_manifest(ROOT / "migrations")
    ids = [item.migration_id for item in migrations]
    if LIVE_MIGRATION_HEAD not in ids:
        raise DisposableDatabaseError("the live migration head is not in the manifest")
    return migrations[: ids.index(LIVE_MIGRATION_HEAD) + 1]


@dataclass
class DisposableDatabase:
    settings: dict[str, str]
    name: str = field(default_factory=lambda: DATABASE_PREFIX + uuid.uuid4().hex)
    created_roles: list[str] = field(default_factory=list)
    created: bool = False

    def _connect(self, database: str = "postgres"):
        import psycopg

        return psycopg.connect(role_url(self.settings, self.settings["user"], database), autocommit=True)

    @property
    def runtime_url(self) -> str:
        return role_url(self.settings, "sqag_runtime", self.name)

    def create(self) -> "DisposableDatabase":
        from psycopg import sql

        with self._connect() as connection:
            row = connection.execute(
                "select rolsuper from pg_catalog.pg_roles where rolname = current_user"
            ).fetchone()
            if not row or row[0] is not True:
                raise DisposableDatabaseError("the bootstrap PostgreSQL role must be a superuser")
            connection.execute(sql.SQL("create database {}").format(sql.Identifier(self.name)))
            self.created = True
            existing = {
                item[0]
                for item in connection.execute(
                    "select rolname from pg_catalog.pg_roles where rolname = any(%s)", (list(APP_ROLES),)
                ).fetchall()
            }
            for role in APP_ROLES:
                if role in existing:
                    continue
                connection.execute(sql.SQL("create role {} " + ROLE_OPTIONS).format(sql.Identifier(role)))
                self.created_roles.append(role)
        self._prepare_and_migrate()
        return self

    def _prepare_and_migrate(self) -> None:
        from psycopg import sql
        from scripts import validate_runtime_privilege_contract as contract
        from webapp import postgres_migrations as migration_contract
        from webapp import server as webapp

        database = sql.Identifier(self.name)
        with self._connect(self.name) as connection:
            connection.execute(sql.SQL("revoke all privileges on database {} from PUBLIC").format(database))
            for role in APP_ROLES:
                connection.execute(sql.SQL("grant connect on database {} to {}").format(database, sql.Identifier(role)))
            connection.execute("revoke all privileges on schema public from PUBLIC")
            connection.execute("grant usage, create on schema public to sqag_migrator")
            connection.execute("grant usage on schema public to sqag_runtime, sqag_maintenance")

        migrations = live_migrations()
        migrator_url = role_url(self.settings, "sqag_migrator", self.name)
        with webapp.postgres_storage_connection(migrator_url, expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE) as connection:
            migration_contract.apply_postgres_migrations(connection, migrations)
            connection.commit()

        applied = {item.migration_id for item in migrations}
        tables: set[str] = set()
        routines: set[tuple[str, str, str]] = set()
        for item in migration_contract.MIGRATION_OBJECTS:
            if item.migration_id in applied:
                tables.update(table.name for table in item.tables)
                routines.update(routine.key for routine in item.routines)
        with self._connect(self.name) as connection:
            for table in sorted(tables):
                for role, grants in (
                    ("sqag_runtime", contract.RUNTIME_TABLE_PRIVILEGES),
                    ("sqag_maintenance", contract.MAINTENANCE_TABLE_PRIVILEGES),
                ):
                    privileges = grants.get(table, ())
                    if privileges:
                        connection.execute(
                            sql.SQL("grant {} on table public.{} to {}").format(
                                sql.SQL(", ").join(sql.SQL(item) for item in privileges),
                                sql.Identifier(table),
                                sql.Identifier(role),
                            )
                        )
            for routine, arguments in sorted(contract.EXPECTED_TRIGGER_ROUTINE_KEYS):
                if ("public", routine, arguments) in routines:
                    connection.execute(
                        sql.SQL("grant execute on function public.{}({}) to sqag_migrator").format(
                            sql.Identifier(routine), sql.SQL(arguments)
                        )
                    )
            for routine, arguments in sorted(contract.EXPECTED_CALLABLE_ROUTINE_KEYS):
                if ("public", routine, arguments) in routines:
                    connection.execute(
                        sql.SQL("grant execute on function public.{}({}) to sqag_runtime").format(
                            sql.Identifier(routine), sql.SQL(arguments)
                        )
                    )

    def drop(self) -> None:
        from psycopg import sql

        with self._connect() as connection:
            if self.created:
                connection.execute(
                    "select pg_catalog.pg_terminate_backend(pid) from pg_catalog.pg_stat_activity "
                    "where datname = %s and pid <> pg_catalog.pg_backend_pid()",
                    (self.name,),
                )
                connection.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(self.name)))
                self.created = False
            for role in reversed(self.created_roles):
                connection.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(role)))
            self.created_roles.clear()
