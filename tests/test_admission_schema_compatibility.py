"""Offline catalogue projections; real PostgreSQL qualification is separate."""

import contextlib
import copy
from dataclasses import asdict
from pathlib import Path
import unittest
from unittest import mock

from webapp import postgres_migrations as migrations
from webapp import server
from scripts import validate_runtime_privilege_contract as contract


ROOT = Path(__file__).resolve().parents[1]
DEDICATED = "sqag_quote_api_reject_binding_change"
LEGACY_ROUTINES = {"sqag_reject_immutable_change", "sqag_require_retention_delete_authorization"}
# Frozen pre-012 dependencies, independent of the current manifest.
LEGACY_LINKS = {
    "sqag_reject_immutable_change": frozenset({
        ("sqag_generation_evidence_no_update", "sqag_generation_evidence"),
        ("sqag_audit_events_no_update", "sqag_audit_events"),
        ("sqag_feedback_linkage_no_update", "sqag_feedback"),
        ("sqag_telemetry_source_state_no_delete", "sqag_telemetry_source_state"),
        ("sqag_telemetry_events_no_update", "sqag_telemetry_events"),
    }),
    "sqag_require_retention_delete_authorization": frozenset({
        ("sqag_generation_evidence_guard_delete", "sqag_generation_evidence"),
        ("sqag_audit_events_guard_delete", "sqag_audit_events"),
        ("sqag_telemetry_events_guard_delete", "sqag_telemetry_events"),
    }),
}


def assert_runtime_surfaces(test, url):
    """Exercise actual startup, uncached health and request admission checks."""
    with (
        mock.patch.object(server, "configured_database_url", return_value=url),
        mock.patch.object(server, "configured_storage_mode", return_value="database"),
        mock.patch.object(server, "configured_artifact_storage_mode", return_value="local"),
        mock.patch.object(server, "configured_app_mode", return_value="local"),
        mock.patch.object(server, "platform_workspace_id_from_auth_session", return_value="test-workspace"),
        mock.patch.object(server, "platform_user_id_from_auth_session", return_value="test-user"),
        mock.patch.object(server, "privacy_safe_audit_tracking_id", return_value="actor-test"),
    ):
        storage = server.DatabaseSqagStorage(url, "test-workspace", expected_session_role="sqag_runtime")
        storage.ensure_runtime_forensic_ready()
        test.assertEqual(server.reconcile_forensic_runs_on_startup(), 0)
        health = server.uncached_health_status()
        checks = {row["name"]: row["ok"] for row in health["checks"]}
        test.assertTrue(checks["forensic_schema"])
        test.assertTrue(checks["database_metadata_schema"])
        with server.forensic_store_for_auth_session({}) as store:
            test.assertEqual(store.workspace_id, "test-workspace")


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Catalog:
    """Project canonical objects without mocking any safety predicate."""
    def __init__(self, applied_count):
        self.manifest = migrations.migration_manifest(ROOT / "migrations")
        self.ledger = [dict(sequence_no=m.sequence_no, migration_id=m.migration_id,
                            checksum_sha256=m.checksum_sha256, applied_at="synthetic")
                       for m in self.manifest[:applied_count]]
        self.relations, self.tables, self.indexes, self.triggers = [], {}, {}, []
        self.routines, self.acls, self.calls = {}, {}, []
        specs = migrations.MIGRATION_OBJECTS[:applied_count]
        table_names = {s.name for item in specs for s in item.tables}
        tables = [migrations._effective_table_spec(name, applied_count) for name in sorted(table_names)]
        tables.append(migrations._LEDGER_TABLE_SPEC)
        for table in tables:
            relation = dict(name=table.name, oid=len(self.relations) + 1, relkind="r",
                            relpersistence="p", relispartition=False, owner=table.owner)
            self.relations.append(relation)
            self.tables[table.name] = dict(
                relation=relation,
                columns=tuple((c.name, c.type_name, c.nullable, c.default_sql, c.identity, c.generated)
                              for c in table.columns),
                constraints=[asdict(c) for c in table.constraints],
            )
        for item in specs:
            for index in item.indexes:
                self.relations.append(dict(name=index.name, oid=len(self.relations) + 1, relkind="i"))
                self.indexes[index.name] = dict(
                    name=index.name, oid=self.relations[-1]["oid"], table_name=index.table_name,
                    owner=index.owner, unique=index.unique, constraint_backed=False, valid=True,
                    ready=True, key_definitions=index.key_semantics, predicate=index.predicate,
                )
            for routine in item.routines:
                body = migrations._canonical_routine_body(routine, self.manifest)
                self.routines[routine.key] = [dict(
                    schema_name="public", name=routine.name, identity_arguments=routine.identity_arguments,
                    prokind="f", result_type=routine.result_type, language=routine.language,
                    owner=routine.owner, security_definer=routine.security_definer,
                    volatility=routine.volatility, parallel=routine.parallel, leakproof=routine.leakproof,
                    proconfig=routine.proconfig, function_body=body, function_definition=body,
                    function_oid=len(self.routines) + 100,
                )]
                self.acls[routine.key] = [dict(grantee=g, privilege=p, grantable=a) for g, p, a in routine.direct_acl]
            for trigger in item.triggers:
                self.triggers.append(dict(name=trigger.name, table_schema="public", table_name=trigger.table_name,
                                          tgtype=3 | sum({"insert": 4, "delete": 8, "update": 16}[e] for e in trigger.events),
                                          columns=trigger.columns, enabled=trigger.enabled, routine_key=trigger.routine_key))

    def execute(self, sql, params=()):
        self.calls.append(sql)
        if "from public.sqag_schema_migrations order by" in sql:
            return Cursor(self.ledger)
        if "from information_schema.columns" in sql:
            return Cursor([dict(table_name=t, column_name=c[0]) for t in params
                           for c in self.tables[t]["columns"]])
        if "from pg_catalog.pg_indexes" in sql:
            return Cursor([dict(name=i["name"], definition="create " + ("unique " if i["unique"] else "") + "index")
                           for i in self.indexes.values()])
        if "from pg_catalog.pg_trigger t" in sql:
            return Cursor([dict(name=t["name"], table_schema="public", table_name=t["table_name"],
                                function_oid=self.routines[t["routine_key"]][0]["function_oid"],
                                function_name=t["routine_key"][1]) for t in self.triggers])
        if "from pg_catalog.pg_proc p" in sql:
            return Cursor([dict(function_oid=r["function_oid"], schema_name="public", name=r["name"],
                                kind=r["prokind"], identity_arguments=r["identity_arguments"],
                                result_type=r["result_type"], security_definer=r["security_definer"], owner=r["owner"])
                           for rows in self.routines.values() for r in rows if r["name"] in params])
        if sql.startswith("select distinct workspace_id from sqag_generation_runs"):
            return Cursor([])
        raise AssertionError("Unexpected SQL: " + sql)

    def inspect(self):
        with contextlib.ExitStack() as stack:
            for name, value in (("_fetch_public_relations", self.relations), ("_fetch_public_tables", self.tables),
                                ("_fetch_public_indexes", self.indexes), ("_fetch_public_triggers", self.triggers),
                                ("_fetch_public_routines", (self.routines, self.acls))):
                stack.enter_context(mock.patch.object(migrations, name, return_value=value))
            return migrations.inspect_postgres_migrations(self, self.manifest)

    def check_routines(self):
        """Feed real routine/ACL verification with the projected catalogue."""
        routine_rows = [dict(r, proname=r["name"], prosecdef=r["security_definer"],
                             provolatile=r["volatility"], proparallel=r["parallel"], proleakproof=r["leakproof"])
                        for rows in self.routines.values() for r in rows]
        trigger_rows = [dict(trigger_name=t["name"], table_schema="public", table_name=t["table_name"],
                             function_oid=self.routines[t["routine_key"]][0]["function_oid"],
                             function_name=t["routine_key"][1]) for t in self.triggers]
        acl_rows = [dict(proname=key[1], identity_arguments=key[2], grantee=a["grantee"],
                         privilege_type=a["privilege"], is_grantable=a["grantable"])
                    for key, entries in self.acls.items() for a in entries]
        errors = []
        with mock.patch.object(contract, "_rows", side_effect=[routine_rows, trigger_rows, acl_rows]):
            contract._check_routines(self, errors)
        return errors


class AdmissionSchemaCompatibilityTest(unittest.TestCase):
    def test_code_ahead_of_database_011_passes_full_manifest_and_runtime_surfaces(self):
        catalog = Catalog(10)
        report = catalog.inspect()
        self.assertTrue(report["safeToApply"], report)
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["appliedMigrationIds"], [m.migration_id for m in catalog.manifest[:10]])
        self.assertEqual(report["pendingMigrationIds"], ["012_quote_api_admission_postgres.sql"])
        with mock.patch.object(server.DatabaseSqagStorage, "connection", side_effect=lambda: contextlib.nullcontext(catalog)):
            assert_runtime_surfaces(self, "postgresql://sqag_runtime@localhost/synthetic")
        self.assertFalse(any("sqag_schema_migrations" in sql for sql in catalog.calls[1:]))

    def test_old_image_forensic_inventory_on_012_still_passes_and_shared_dependency_mutant_fails(self):
        catalog = Catalog(11)
        old_manifest = catalog.manifest[:-1]
        self.assertEqual(old_manifest[-1].migration_id, "011_quote_ai_spend_postgres.sql")
        old_ids = {m.migration_id for m in old_manifest}
        old_links = {name: frozenset((t.name, t.table_name)
                                    for item in migrations.MIGRATION_OBJECTS if item.migration_id in old_ids
                                    for t in item.triggers if t.routine_key[1] == name)
                     for name in LEGACY_ROUTINES}
        self.assertEqual(old_links, LEGACY_LINKS)
        self.assertEqual({name: migrations.EXPECTED_TRIGGER_ROUTINE_LINKS[name] for name in LEGACY_ROUTINES}, LEGACY_LINKS)
        # Model the exact pre-012 routine query/link inventory with the real check.
        with (mock.patch.object(server, "EXPECTED_TRIGGER_ROUTINE_LINKS", LEGACY_LINKS),
              mock.patch.object(server, "SQAG_FORENSIC_POSTGRES_REQUIRED_ROUTINES", LEGACY_ROUTINES),
              mock.patch.object(server.DatabaseSqagStorage, "connection", side_effect=lambda: contextlib.nullcontext(catalog))):
            assert_runtime_surfaces(self, "postgresql://sqag_runtime@localhost/synthetic")
            for trigger in catalog.triggers:
                if trigger["routine_key"][1] == DEDICATED:
                    trigger["routine_key"] = ("public", "sqag_reject_immutable_change", "")
            storage = server.DatabaseSqagStorage("postgresql://sqag_runtime@localhost/synthetic", "test-workspace")
            with self.assertRaises(server.SqagStorageAccessError):
                storage.ensure_runtime_forensic_ready()

    def test_011_runtime_mutant_requiring_012_dependencies_fails(self):
        catalog = Catalog(10)
        links = copy.deepcopy(migrations.EXPECTED_TRIGGER_ROUTINE_LINKS)
        links["sqag_reject_immutable_change"] |= links[DEDICATED]
        with (mock.patch.object(server, "EXPECTED_TRIGGER_ROUTINE_LINKS", links),
              mock.patch.object(server.DatabaseSqagStorage, "connection", side_effect=lambda: contextlib.nullcontext(catalog))):
            storage = server.DatabaseSqagStorage("postgresql://sqag_runtime@localhost/synthetic", "test-workspace")
            with self.assertRaises(server.SqagStorageAccessError) as raised:
                storage.ensure_runtime_forensic_ready()
            self.assertEqual(raised.exception.reason, "storage_runtime_forensics_not_migrated")

    def test_applied_012_objects_are_fully_checked_and_each_missing_object_or_routine_drift_fails(self):
        self.assertEqual(Catalog(11).inspect()["blockers"], [])
        self.assertEqual(Catalog(11).check_routines(), [])
        with mock.patch.object(server.DatabaseSqagStorage, "connection", side_effect=lambda: contextlib.nullcontext(Catalog(11))):
            assert_runtime_surfaces(self, "postgresql://sqag_runtime@localhost/synthetic")
        for kind in ("table", "index", "trigger", "routine", "body", "owner"):
            catalog = Catalog(11)
            spec = migrations.MIGRATION_OBJECTS[-1]
            if kind == "table":
                catalog.relations = [r for r in catalog.relations if r["name"] != spec.tables[0].name]
            elif kind == "index":
                del catalog.indexes[spec.indexes[0].name]
            elif kind == "trigger":
                catalog.triggers = [t for t in catalog.triggers if t["name"] != spec.triggers[0].name]
            elif kind == "routine":
                del catalog.routines[("public", DEDICATED, "")]
            else:
                key = ("public", DEDICATED, "")
                catalog.routines[key][0]["function_body" if kind == "body" else "owner"] = "invalid"
            with self.subTest(kind=kind):
                self.assertFalse(catalog.inspect()["safeToApply"])

    def test_dedicated_routine_properties_and_acl_drift_fail_real_privilege_verifier(self):
        for field, value in (("function_body", "begin return old; end"), ("language", "sql"),
                             ("volatility", "s"), ("parallel", "s"), ("leakproof", True),
                             ("proconfig", ("search_path=public",)), ("owner", "sqag_runtime"),
                             ("security_definer", True)):
            catalog = Catalog(11)
            catalog.routines[("public", DEDICATED, "")][0][field] = value
            with self.subTest(field=field):
                self.assertIn("routine_properties_mismatch:" + DEDICATED, catalog.check_routines())
        for role in ("PUBLIC", "sqag_runtime", "sqag_maintenance"):
            catalog = Catalog(11)
            catalog.acls[("public", DEDICATED, "")] = [dict(grantee=role, privilege="EXECUTE", grantable=False)]
            with self.subTest(role=role):
                self.assertIn("routine_escalation:" + DEDICATED, catalog.check_routines())

    def test_pending_suffix_control_rejects_premature_012_objects_without_ledger_entry(self):
        catalog = Catalog(11)
        catalog.ledger.pop()
        report = catalog.inspect()
        self.assertFalse(report["safeToApply"])
        self.assertTrue(any(b.startswith("pending_suffix_present:") for b in report["blockers"]))


if __name__ == "__main__":
    unittest.main()
