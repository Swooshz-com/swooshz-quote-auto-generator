"""Offline guards for admission ACL rollback and routine dependency mutations."""

import ast
import contextlib
import copy
import re
import unittest
from unittest import mock

from tests.test_admission_source_acl import ROOT, SOURCE, parse_acl
from scripts import validate_runtime_privilege_contract as contract


def _literal(node, bindings):
    if isinstance(node, ast.Name):
        return bindings[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(str(_literal(part.value, bindings)) if isinstance(part, ast.FormattedValue)
                       else part.value for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal(node.left, bindings) + _literal(node.right, bindings)
    return ast.literal_eval(node)


def assert_dependency_safe_drops(sources, admission_source):
    """Scan executable SQL calls, resolving literal loop bindings without execution."""
    dependencies = {}
    for trigger, table, routine in re.findall(
        r"create trigger (\w+)\b[^;]+?on public\.(\w+)\b[^;]+?"
        r"execute function public\.(\w+)\(\)", admission_source
    ):
        dependencies.setdefault(routine, set()).add((trigger, table))
    if not dependencies:
        raise AssertionError("Admission trigger dependencies were not discovered")
    sites = []
    for filename, source in sources.items():
        tree = ast.parse(source)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def ancestors(node):
            while node in parents:
                node = parents[node]
                yield node

        def sql_values(call):
            bindings = [{}]
            for parent in ancestors(call):
                if isinstance(parent, ast.For):
                    try:
                        values = ast.literal_eval(parent.iter)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(values, (tuple, list, set)):
                        continue
                    if not (isinstance(parent.target, ast.Name) or
                            isinstance(parent.target, (ast.Tuple, ast.List)) and
                            all(isinstance(item, ast.Name) for item in parent.target.elts)):
                        continue
                    names = ([parent.target.id] if isinstance(parent.target, ast.Name)
                             else [item.id for item in parent.target.elts])
                    bindings = [dict(binding, **dict(zip(names, [value] if len(names) == 1 else value)))
                                for binding in bindings for value in values]
            values = []
            for binding in bindings:
                try:
                    value = _literal(call.args[0], binding)
                except (ValueError, TypeError, KeyError):
                    continue
                if isinstance(value, str):
                    values.append(" ".join(value.lower().split()))
            return values

        all_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        calls = [node for node in all_calls if node.args
                 and isinstance(node.func, ast.Attribute) and node.func.attr in ("execute", "_execute_admin")]
        for call in calls:
            for sql in sql_values(call):
                drop = re.fullmatch(r"drop (?:function|routine)(?: if exists)? (?:public\.)?(\w+) ?\(\)(?: (cascade|restrict))?", sql)
                if not drop or drop[1] not in dependencies:
                    continue
                if drop[2] == "cascade":
                    raise AssertionError("Routine dependency probes must not use CASCADE")
                scope = next(node for node in ancestors(call) if isinstance(node, (ast.Lambda, ast.FunctionDef)))
                # A freshly empty target has no installed 012 dependencies to remove.
                database = next((kw.value for kw in call.keywords if kw.arg == "database_name"), None)
                if isinstance(database, ast.Name):
                    assignments = [node for node in ast.walk(scope) if isinstance(node, ast.Assign)
                                   and any(isinstance(target, ast.Name) and target.id == database.id
                                           for target in node.targets)]
                    if (len(assignments) == 1 and assignments[0].lineno < call.lineno
                            and isinstance(assignments[0].value, ast.Call)
                            and isinstance(assignments[0].value.func, ast.Attribute)
                            and assignments[0].value.func.attr == "_create_empty_isolated_database_fixture"):
                        # No later call may install migrations on that database.
                        uses = [node for node in all_calls if node.lineno < call.lineno and
                                any(isinstance(value, ast.Name) and value.id == database.id
                                    for value in [*node.args, *(kw.value for kw in node.keywords)])]
                        sql_uses = [node for node in uses if node in calls]
                        if all(node in sql_uses or isinstance(node.func, ast.Name) and
                               node.func.id == "assert_snapshot_stable" for node in uses) and not any(
                            re.search(r"\bcreate (?:table|trigger)\b", value)
                            for node in sql_uses for value in sql_values(node)
                        ):
                            sites.append((filename, call.lineno, "empty"))
                            continue
                prior = {value for node in calls if node in set(ast.walk(scope)) and node.lineno < call.lineno
                         for value in sql_values(node)}
                for trigger, table in dependencies[drop[1]]:
                    if not ({f"drop trigger {trigger} on public.{table}",
                             f"drop trigger if exists {trigger} on public.{table}"} & prior):
                        raise AssertionError(f"{filename}:{call.lineno}: missing dependency drop: {trigger}")
                if isinstance(scope, ast.Lambda):
                    probe = next((node for node in ancestors(scope) if isinstance(node, ast.Call)
                                  and isinstance(node.func, ast.Attribute) and node.func.attr == "_red_then_restore"), None)
                    if (probe is None or len(probe.args) < 2 or not isinstance(probe.args[1], ast.Attribute)
                            or probe.args[1].attr != "_restore_canonical_forensic_routines"):
                        raise AssertionError("Routine mutation must retain canonical restoration checks")
                sites.append((filename, call.lineno, "handled"))
    return sites


class AdmissionFixtureMutationTest(unittest.TestCase):
    def test_all_test_routine_drops_handle_canonical_012_dependencies(self):
        sources = {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
                   for path in sorted((ROOT / "tests").rglob("*.py"))}
        sites = assert_dependency_safe_drops(sources, SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(len(sites), 2)  # Dedicated restore helper and identity mutation.
        self.assertEqual([site[2] for site in sites].count("empty"), 0)

    def test_dependency_scan_mutants_reject_each_missing_drop_wrong_order_and_restore(self):
        path = ROOT / "tests/test_runtime_privilege_contract.py"
        source = path.read_text(encoding="utf-8")
        admission = SOURCE.read_text(encoding="utf-8")
        for table in ("tenants", "callers"):
            fragment = (f'"drop trigger sqag_quote_api_{table}_binding_immutable "\n'
                        f'                    "on public.sqag_quote_api_{table}"')
            self.assertEqual(source.count(fragment), 1)
            for occurrence in (0,):
                offset = [match.start() for match in re.finditer(re.escape(fragment), source)][occurrence]
                mutant = source[:offset] + source[offset:].replace(fragment, '"select 1"', 1)
                with self.subTest(table=table, occurrence=occurrence), self.assertRaises(AssertionError):
                    assert_dependency_safe_drops({str(path): mutant}, admission)
        trigger = '("sqag_quote_api_tenants_binding_immutable", "sqag_quote_api_tenants"),'
        with self.assertRaises(AssertionError):
            assert_dependency_safe_drops({str(path): source.replace(trigger, "", 1)}, admission)
        # Move a required drop after the routine: textual presence alone is insufficient.
        fragment = ('                self._execute_admin(\n'
                    '                    "drop trigger sqag_quote_api_tenants_binding_immutable "\n'
                    '                    "on public.sqag_quote_api_tenants"\n                ),\n')
        mutant = source.replace(fragment, "", 1)
        routine = '                self._execute_admin("drop function public.sqag_quote_api_reject_binding_change()"),\n'
        mutant = mutant.replace(routine, routine + fragment, 1)
        with self.assertRaises(AssertionError):
            assert_dependency_safe_drops({str(path): mutant}, admission)
        with self.assertRaises(AssertionError):
            assert_dependency_safe_drops({"rogue.py": 'def probe(c):\n    c.execute("drop function public.sqag_quote_api_reject_binding_change()")\n'}, admission)
        empty = ('def probe(self):\n'
                 '    target = self._create_empty_isolated_database_fixture()\n'
                 '    self._execute_admin("drop function public.sqag_quote_api_reject_binding_change()", database_name=target)\n')
        self.assertEqual(assert_dependency_safe_drops({"empty.py": empty}, admission)[0][2], "empty")
        with self.assertRaises(AssertionError):
            assert_dependency_safe_drops({"empty.py": empty.replace("_create_empty_isolated_database_fixture", "_create_isolated_database_fixture")}, admission)
        for extra in (" cascade", ""):
            installed_source = ('def probe(self):\n'
                      '    target = self._create_empty_isolated_database_fixture()\n'
                      '    self.apply(target)\n'
                      '    self._execute_admin("drop function public.sqag_quote_api_reject_binding_change()'
                      + extra + '", database_name=target)\n')
            with self.subTest(extra=extra), self.assertRaises(AssertionError):
                assert_dependency_safe_drops({"installed.py": installed_source}, admission)
        mutant = source.replace('self._restore_canonical_forensic_routines,\n            "dedicated binding function identity"',
                                'self._verify,\n            "dedicated binding function identity"', 1)
        self.assertNotEqual(mutant, source)
        with self.assertRaises(AssertionError):
            assert_dependency_safe_drops({str(path): mutant}, admission)

    def test_acl_negative_probes_rollback_without_erasing_source_column_grants(self):
        from tests.test_runtime_privilege_contract import RuntimePrivilegeContractPostgresIntegrationTest

        rows = {(target[7:], column, role, privilege)
                for action, kind, target, role, privilege, columns in parse_acl(SOURCE.read_text(encoding="utf-8"))
                if action == "grant" and kind == "table" for column in columns}

        class Cursor:
            def __init__(self, values):
                self.values = values

            def fetchall(self):
                return self.values

            def fetchone(self):
                return self.values[0]

        class Connection:
            def __init__(self):
                self.columns = set(rows)
                self.tables = set()
                self.saved = None
                self.probes = 0
                self.restored = 0

            def execute(self, sql, params=()):
                if sql == "begin":
                    assert self.saved is None
                    self.saved = (copy.copy(self.columns), copy.copy(self.tables))
                    return
                if "aclexplode(a.attacl)" in sql:
                    return Cursor([dict(table_name=t, column_name=c, grantee=r, privilege_type=p, is_grantable=False)
                                   for t, c, r, p in self.columns])
                if "has_column_privilege" in sql:
                    role, table, column, privilege = params[:4]
                    allowed = ((table[7:], column, role, privilege) in self.columns
                               or (table[7:], role, privilege) in self.tables)
                    return Cursor([dict(effective=allowed, grantable=False)])
                change = re.fullmatch(r"(grant|revoke) (select|update)(?: \((\w+)\))? on public\.(\w+) (?:to|from) sqag_runtime", sql)
                if change:
                    action, privilege, column, table = change.groups()
                    privilege = privilege.upper()
                    if action == "grant":
                        self.probes += 1
                        if column:
                            self.columns.add((table, column, "sqag_runtime", privilege))
                        else:
                            self.tables.add((table, "sqag_runtime", privilege))
                    elif column:
                        self.columns.discard((table, column, "sqag_runtime", privilege))
                    else:
                        self.tables.discard((table, "sqag_runtime", privilege))
                        self.columns = {entry for entry in self.columns
                                        if (entry[0], entry[2], entry[3]) != (table, "sqag_runtime", privilege)}
                    return
                raise PermissionError("Synthetic denied authority operation")

            def rollback(self):
                if self.saved is not None:
                    self.columns, self.tables = self.saved
                    self.saved = None
                    self.restored += 1

        connection = Connection()
        subject = RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        subject.manifest = contract.validate_manifest()

        def verify(conn, manifest=None):
            errors = []
            contract._check_columns(conn, errors)
            if connection.tables:
                errors.append("table_acl")
            if errors:
                raise contract.RuntimePrivilegeContractError(";".join(errors))
            return {"status": "verified"}

        with (mock.patch.object(subject, "_role_connection", side_effect=lambda role: contextlib.nullcontext(connection)),
              mock.patch.object(subject, "_admin_connection", side_effect=lambda *a: contextlib.nullcontext(connection)),
              mock.patch.object(subject, "_execute_admin", side_effect=connection.execute),
              mock.patch.object(subject, "_verify", side_effect=lambda: verify(connection)),
              mock.patch.object(contract, "verify_postgres_privilege_contract", side_effect=verify)):
            subject.test_admission_source_acl_denies_verifier_authority_and_table_grants_without_fixture_repair()
        self.assertEqual(connection.columns, rows)
        self.assertEqual(connection.tables, set())
        self.assertIsNone(connection.saved)
        self.assertEqual((connection.probes, connection.restored), (3, 3))
        # Original cleanup reproduces precisely the 14 missing caller SELECTs,
        # while tenant SELECTs and both counter UPDATE lists remain intact.
        connection.execute("grant select on public.sqag_quote_api_callers to sqag_runtime")
        connection.execute("revoke select on public.sqag_quote_api_callers from sqag_runtime")
        errors = []
        contract._check_columns(connection, errors)
        self.assertEqual(set(errors), {"column_privilege_inventory_mismatch"} | {
            f"effective_column_privilege:{table}:{column}:{role}:{privilege}"
            for table, column, role, privilege in rows
            if table == "sqag_quote_api_callers" and privilege == "SELECT"
        })


if __name__ == "__main__":
    unittest.main()
