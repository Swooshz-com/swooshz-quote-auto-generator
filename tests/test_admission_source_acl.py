"""Offline source/fixture ACL checks, not PostgreSQL privilege execution proof."""

from collections import Counter
import contextlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock

from webapp import postgres_migrations as migrations


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "migrations/012_quote_api_admission_postgres.sql"
LOOKUP = "public.sqag_quote_api_admission_lookup(text)"


def parse_acl(source):
    """Accept only the approved ACL syntax; retain order and reject extra effects."""
    statements = []

    class Capture:
        def execute(self, statement):
            statements.append(statement)

    migrations.execute_migration_sql(Capture(), source)
    effects = []
    for statement in statements:
        statement = re.sub(r"(?m)^\s*--[^\n]*(?:\n|$)", "", statement)
        statement = " ".join(statement.lower().rstrip(";").split())
        if not re.match(r"(?:grant|revoke)\b", statement):
            continue
        revoke = re.fullmatch(r"revoke all privileges on (table|function) (.+) from (.+)", statement)
        execute = re.fullmatch(r"grant execute on function (public\.\w+\(text\)) to (\w+)", statement)
        columns = re.fullmatch(r"grant (select|update) \(([\w, ]+)\) on (public\.\w+) to (\w+)", statement)
        if revoke:
            kind, targets, roles = revoke.groups()
            for target in targets.split(", "):
                for role in roles.split(", "):
                    effects.append(("revoke", kind, target, role, "ALL", ()))
        elif execute:
            target, role = execute.groups()
            effects.append(("grant", "function", target, role, "EXECUTE", ()))
        elif columns:
            privilege, names, target, role = columns.groups()
            effects.append(("grant", "table", target, role, privilege.upper(), tuple(names.split(", "))))
        else:
            raise AssertionError("Unapproved admission ACL statement: " + statement)
    return effects


def assert_source_acl(source, contract):
    routine = next(item for item in contract["namespace"]["callable_routines"]
                   if item["name"] == "sqag_quote_api_admission_lookup")
    target = "public." + routine["name"] + "(" + routine["identity_arguments"] + ")"
    expected = []
    for kind, name in [("function", target)] + [
        ("table", "public." + name) for name in contract["runtime_column_privileges"]
    ]:
        for role in ("public", "sqag_runtime", "sqag_maintenance"):
            expected.append(("revoke", kind, name, role, "ALL", ()))
    for role in routine["explicit_execute_roles"]:
        expected.append(("grant", "function", target, role, "EXECUTE", ()))
    for table, grants in contract["runtime_column_privileges"].items():
        for privilege, columns in grants.items():
            expected.append(("grant", "table", "public." + table, "sqag_runtime", privilege, tuple(columns)))
    observed = parse_acl(source)
    if Counter(observed) != Counter(expected):
        raise AssertionError("Admission source GRANT/REVOKE map differs from contract JSON")
    # A later REVOKE also cancels an earlier GRANT on a real server.
    active = set()
    for action, kind, name, role, privilege, columns in observed:
        if action == "revoke":
            active = {entry for entry in active if entry[:3] != (kind, name, role)}
        else:
            active.add((kind, name, role, privilege, columns))
    expected_active = {entry[1:] for entry in expected if entry[0] == "grant"}
    if active != expected_active:
        raise AssertionError("Admission ACL statement order cancels a required grant")


class SqlText(str):
    """Recording-only stand-in for psycopg.sql; no connection is constructed."""
    def format(self, *args):
        return SqlText(super().format(*args))

    def join(self, items):
        return SqlText(super().join(items))


class RecordingSql:
    SQL = SqlText

    @staticmethod
    def Identifier(name):
        return SqlText('"' + name.replace('"', '""') + '"')


class AdmissionSourceAclTest(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE.read_text(encoding="utf-8")
        self.contract = json.loads((ROOT / "docs/runtime-privilege-contract.json").read_text(encoding="utf-8"))

    def test_source_grants_and_revokes_exactly_match_closed_json_contract(self):
        assert_source_acl(self.source, self.contract)

    def test_source_acl_mutation_controls_reject_missing_extra_wrong_signature_and_order(self):
        grant = "grant execute on function " + LOOKUP + " to sqag_runtime;"
        revoke = "revoke all privileges on function " + LOOKUP + " from public, sqag_runtime, sqag_maintenance;"
        mutants = {
            "missing-execute": self.source.replace(grant, ""),
            "wrong-signature": self.source.replace(grant, grant.replace("(text)", "(bytea)")),
            "public-execute": self.source + grant.replace("sqag_runtime", "public"),
            "maintenance-execute": self.source + grant.replace("sqag_runtime", "sqag_maintenance"),
            "grant-option": self.source.replace(grant, grant[:-1] + " with grant option;"),
            "verifier-select": self.source + "grant select (verifier) on public.sqag_quote_api_callers to sqag_runtime;",
            "authority-update": self.source + "grant update (state) on public.sqag_quote_api_callers to sqag_runtime;",
            "table-select": self.source + "grant select on table public.sqag_quote_api_callers to sqag_runtime;",
            "missing-revoke": self.source.replace("from public, sqag_runtime, sqag_maintenance;", "from sqag_runtime, sqag_maintenance;"),
            "revoke-after-grant": self.source.replace(revoke, "") + revoke,
        }
        for name, source in mutants.items():
            with self.subTest(mutation=name):
                self.assertNotEqual(source, self.source)
                with self.assertRaises(AssertionError):
                    assert_source_acl(source, self.contract)

    def test_fixture_preserves_source_acl_including_missing_and_extra_grants(self):
        from tests.test_runtime_privilege_contract import RuntimePrivilegeContractPostgresIntegrationTest

        subject = RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        subject.database_name = "synthetic"
        subject.roles = ("sqag_runtime", "sqag_migrator", "sqag_maintenance")
        subject.migrations = migrations.migration_manifest(ROOT / "migrations")
        subject.sql = RecordingSql
        statements = []

        class Connection:
            def execute(self, statement):
                statements.append(str(statement).replace('"', '').lower())

        with mock.patch.object(subject, "_admin_connection", return_value=contextlib.nullcontext(Connection())):
            subject._configure_acl_contract()
        self.assertFalse(any("on all functions" in sql for sql in statements))
        self.assertFalse(any("sqag_quote_api_admission_lookup" in sql for sql in statements))
        self.assertFalse(any("on public." + table in sql for sql in statements
                             for table in self.contract["runtime_column_privileges"]))
        self.assertIn("grant usage on schema public to sqag_runtime, sqag_maintenance", statements)
        # Retain the old routine setup; admission must receive neither repair nor revoke.
        for name in ("sqag_reject_immutable_change()", "sqag_quote_session_deletion_hold_blocked(text, text)"):
            self.assertTrue(any("revoke all privileges on function public." + name in sql for sql in statements))

    def test_spend_upgrade_fixture_stays_bound_to_011_when_later_migrations_exist(self):
        from tests.test_postgres_migration_ledger import PostgresMigrationLedgerIntegrationTest

        subject = PostgresMigrationLedgerIntegrationTest("runTest")
        subject.manifest = migrations.migration_manifest(ROOT / "migrations")
        connection = mock.Mock()
        connection.execute.return_value.fetchone.side_effect = [{"n": 1}, {"n": 0}, {"n": 1}]
        applied_count = 0
        prefixes = []

        def apply(prefix=None):
            nonlocal applied_count
            prefix = subject.manifest if prefix is None else prefix
            prefixes.append([item.migration_id for item in prefix])
            result = {"appliedNow": prefixes[-1][applied_count:]}
            applied_count = len(prefix)
            return result

        with mock.patch.object(subject, "apply", side_effect=apply), mock.patch.object(subject, "connect", return_value=connection):
            subject.test_quote_ai_spend_upgrade_and_transactional_cascade()
        self.assertEqual(prefixes[0][-1], "010_telemetry_attempt_semantics_postgres.sql")
        self.assertEqual(prefixes[1], prefixes[0] + ["011_quote_ai_spend_postgres.sql"])

    def test_restore_rebuilds_all_trigger_dependencies_with_canonical_admission_columns(self):
        from tests.test_runtime_privilege_contract import RuntimePrivilegeContractPostgresIntegrationTest

        subject = RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        with mock.patch.object(subject, "_execute_admin") as execute:
            subject._restore_canonical_forensic_routines()
        statements = [call.args[0] for call in execute.call_args_list]
        drop_routine = statements.index("drop routine if exists public.sqag_reject_immutable_change()")
        specs = [trigger for trigger in migrations.TRIGGER_SPECS
                 if trigger.routine_key == ("public", "sqag_reject_immutable_change", "")]
        for trigger in specs:
            drop = "drop trigger if exists " + trigger.name + " on public." + trigger.table_name
            self.assertIn(drop, statements)
            self.assertLess(statements.index(drop), drop_routine)
            creates = [sql for sql in statements if sql.startswith("create trigger " + trigger.name + " ")]
            self.assertEqual(len(creates), 1)
        canonical = [sql for sql in migrations._split_sql_statements(self.source) if sql.startswith("create trigger ")]
        self.assertEqual([sql for sql in statements if sql in canonical], canonical)


if __name__ == "__main__":
    unittest.main()
