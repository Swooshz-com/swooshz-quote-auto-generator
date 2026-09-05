"""Regression and disposable integration coverage for the durable runner."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from scripts import converge_runtime_privilege_contract as runner
from scripts import validate_runtime_privilege_contract as canonical


def _manifest() -> dict[str, Any]:
    return canonical.validate_manifest()


def _observations(*, database_owner: str = "sqag_migrator", schema_owner: str = "pg_database_owner", schema_acl: list[str] | None = None, table_acl: list[tuple[str, str, str, str, bool]] | None = None, column_acl: list[tuple[str, str, str, str]] | None = None, unrelated_data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "database": {"owner": database_owner, "acl": ["legacy"]},
        "schema": {"owner": schema_owner, "acl": schema_acl if schema_acl is not None else ["legacy"]},
        "table_acl": table_acl if table_acl is not None else [("sqag_profiles", "sqag_migrator", "sqag_runtime", "SELECT", False), ("sqag_profiles", "sqag_migrator", "sqag_maintenance", "UPDATE", False), ("unrelated_table", "unrelated_owner", "unrelated_role", "SELECT", False)],
        "column_acl": column_acl if column_acl is not None else [("public", "sqag_profiles", "id", "sqag_runtime")],
        "membership": [("sqag_runtime", "neondb_owner", "cloud_admin", True, False, False)],
        "role_attributes": {"sqag_runtime": {"inherit": False}},
        "object_ownership": [("public", "unrelated_table", "unrelated_owner")],
        "default_acls": [],
        "unrelated_objects": [("public", "unrelated_view")],
        "unrelated_data": unrelated_data if unrelated_data is not None else {"synthetic_row_count": 2},
    }


def _pre_and_target() -> tuple[runner.PrivilegeSnapshot, runner.PrivilegeSnapshot]:
    pre = runner.PrivilegeSnapshot.from_mapping(_observations())
    target = runner.PrivilegeSnapshot.from_mapping(_observations(database_owner="neondb_owner", schema_acl=["canonical"], table_acl=[("sqag_profiles", "sqag_migrator", "sqag_runtime", "SELECT", False), ("sqag_profiles", "sqag_migrator", "sqag_maintenance", "SELECT", False), ("unrelated_table", "unrelated_owner", "unrelated_role", "SELECT", False)], column_acl=[]))
    return pre, target


class _FixtureAdapter:
    def __init__(self, *, fail_admission: bool = False, fail_forward: bool = False, fail_forward_unexpected: bool = False, fail_context: str | None = None, fail_check: str | None = None, ambiguous_restore: bool = False) -> None:
        self.pre, self.target = _pre_and_target()
        self.current = self.pre
        self.fail_admission = fail_admission
        self.fail_forward = fail_forward
        self.fail_forward_unexpected = fail_forward_unexpected
        self.fail_context = fail_context
        self.fail_check = fail_check
        self.ambiguous_restore = ambiguous_restore
        self.mutation_started = False
        self.rollback_calls = 0
        self.verified_contexts: list[str] = []

    def admit(self, manifest: dict[str, Any]) -> runner.AdmissionEvidence:
        if self.fail_admission:
            raise runner.GateError(runner.SemanticCode.PRESTATE_MISMATCH, runner.Phase.ADMISSION)
        return runner.AdmissionEvidence(runner.Identity("neondb_owner", "neondb_owner"), runner.Identity("sqag_migrator", "sqag_migrator"), True, 17, 0)

    def read_snapshot(self) -> runner.PrivilegeSnapshot:
        return self.current

    def target_snapshot(self, manifest: dict[str, Any]) -> runner.PrivilegeSnapshot:
        return self.target

    def capture_inverse(self, prestate: runner.PrivilegeSnapshot) -> runner.InverseCapture:
        return runner.InverseCapture(prestate)

    def apply_authorised_delta(self, plan: runner.ConvergencePlan) -> runner.MutationOutcome:
        self.mutation_started = True
        if self.fail_forward:
            if self.fail_forward_unexpected:
                raise RuntimeError("synthetic implementation detail")
            raise runner.GateError(runner.SemanticCode.FORWARD_MUTATION, runner.Phase.FORWARD, mutation_started=True)
        candidate = {key: copy.deepcopy(value) for key, value in _observations(database_owner="neondb_owner", schema_acl=["canonical"], table_acl=list(self.target.value("table_acl") or ()), column_acl=list(self.target.value("column_acl") or ())).items()}
        if self.fail_check == runner.SemanticCode.AUTHORISED_DELTA_DATABASE.value:
            candidate["database"] = _observations()["database"]
        elif self.fail_check == runner.SemanticCode.AUTHORISED_DELTA_SCHEMA.value:
            candidate["schema"] = _observations()["schema"]
        elif self.fail_check == runner.SemanticCode.AUTHORISED_DELTA_TABLE_ACL.value:
            candidate["table_acl"] = _observations()["table_acl"]
        elif self.fail_check == runner.SemanticCode.AUTHORISED_DELTA_COLUMN_ACL.value:
            candidate["column_acl"] = _observations()["column_acl"]
        elif self.fail_check == runner.SemanticCode.PRESERVED_COMPLEMENT.value:
            candidate["unrelated_data"] = {"synthetic_row_count": 3}
        self.current = runner.PrivilegeSnapshot.from_mapping(candidate)
        return runner.MutationOutcome(True)

    def verify_canonical(self, context: str, manifest: dict[str, Any]) -> dict[str, Any]:
        self.verified_contexts.append(context)
        if self.fail_context == context:
            return {"status": "failed", "internal": "synthetic detail"}
        if self.fail_context == "unexpected" and context == "provider_admin":
            raise RuntimeError("synthetic unexpected detail")
        return {"status": "verified", "raw_values_materialized": False}

    def rollback(self, inverse: runner.InverseCapture) -> None:
        self.rollback_calls += 1
        self.current = inverse.prestate

    def verify_restoration(self, inverse: runner.InverseCapture) -> bool:
        return not self.ambiguous_restore


class Run233MaskingRedRegressionTest(unittest.TestCase):
    def test_known_pre_final_gateerror_keeps_exact_safe_code(self) -> None:
        public = runner.run_convergence(_FixtureAdapter(fail_admission=True)).to_public_dict()
        self.assertEqual(public["semantic_failure_code"], "prestate_mismatch")
        self.assertFalse(public["mutation_started"])
        self.assertFalse(public["final_verification_reached"])
        self.assertEqual(public["rollback"], "not_required")


class DurableRunnerFailureMatrixTest(unittest.TestCase):
    def test_forward_gateerror_preserves_code_and_restores(self) -> None:
        public = runner.run_convergence(_FixtureAdapter(fail_forward=True)).to_public_dict()
        self.assertEqual(public["semantic_failure_code"], "forward_mutation")
        self.assertTrue(public["mutation_started"])
        self.assertFalse(public["final_verification_reached"])
        self.assertEqual(public["rollback"], "restored")
        self.assertTrue(public["restoration_verified"])

    def test_provider_and_operator_verifier_failures_are_distinct(self) -> None:
        provider = runner.run_convergence(_FixtureAdapter(fail_context="provider_admin")).to_public_dict()
        operator = runner.run_convergence(_FixtureAdapter(fail_context="operator")).to_public_dict()
        self.assertEqual(provider["semantic_failure_code"], "canonical_provider_admin_verifier")
        self.assertEqual(operator["semantic_failure_code"], "canonical_operator_verifier")
        self.assertTrue(provider["final_verification_reached"])
        self.assertTrue(operator["final_verification_reached"])
        self.assertEqual(provider["rollback"], "restored")
        self.assertEqual(operator["rollback"], "restored")

    def test_all_authorised_and_preserved_checks_have_exact_failure_codes(self) -> None:
        for code in ("authorized_delta_database", "authorized_delta_schema", "authorized_delta_table_acl", "authorized_delta_column_acl", "preserved_complement"):
            with self.subTest(code=code):
                public = runner.run_convergence(_FixtureAdapter(fail_check=code)).to_public_dict()
                self.assertEqual(public["semantic_failure_code"], code)
                self.assertEqual(public["checks"][code], "FAIL")
                self.assertEqual(public["rollback"], "restored")

    def test_unexpected_exception_is_separate_and_sanitized(self) -> None:
        public = runner.run_convergence(_FixtureAdapter(fail_forward=True, fail_forward_unexpected=True)).to_public_dict()
        self.assertIsNone(public["semantic_failure_code"])
        self.assertEqual(public["unexpected_exception_code"], "unexpected_exception")
        self.assertNotIn("synthetic implementation detail", json.dumps(public, sort_keys=True))
        self.assertEqual(public["rollback"], "restored")

    def test_final_unexpected_exception_is_separate_and_sanitized(self) -> None:
        public = runner.run_convergence(_FixtureAdapter(fail_context="unexpected")).to_public_dict()
        self.assertEqual(public["unexpected_exception_code"], "unexpected_exception")
        self.assertNotIn("synthetic unexpected detail", json.dumps(public, sort_keys=True))
        self.assertEqual(public["rollback"], "restored")

    def test_ambiguous_restoration_cannot_pass(self) -> None:
        public = runner.run_convergence(_FixtureAdapter(fail_context="provider_admin", ambiguous_restore=True)).to_public_dict()
        self.assertEqual(public["verdict"], "BLOCKED")
        self.assertEqual(public["rollback"], "ambiguous")
        self.assertFalse(public["restoration_verified"])

    def test_success_has_both_canonical_verifiers_all_nine_checks_and_owners(self) -> None:
        adapter = _FixtureAdapter()
        public = runner.run_convergence(adapter).to_public_dict()
        self.assertEqual(public["verdict"], "PASS")
        self.assertTrue(public["mutation_started"])
        self.assertTrue(public["final_verification_reached"])
        self.assertEqual(public["rollback"], "not_required")
        self.assertEqual(adapter.verified_contexts, ["provider_admin", "operator"])
        self.assertEqual(public["final_database_owner"], "neondb_owner")
        self.assertEqual(public["final_public_schema_owner"], "pg_database_owner")
        self.assertTrue(all(value == "PASS" for value in public["checks"].values()))


class DurableRunnerPartitionAndAclTest(unittest.TestCase):
    def test_tuple_shape_is_exact_and_fail_closed(self) -> None:
        with self.assertRaises(runner.GateError) as raised:
            runner.PrivilegeSnapshot.from_mapping(_observations(table_acl=[("sqag_profiles", "sqag_migrator", "sqag_runtime", "SELECT")]))
        self.assertEqual(raised.exception.code, "table_acl_tuple_shape_invalid")

    def test_old_row_zero_filter_is_red_and_correct_row_two_filter_is_green_for_34_rows(self) -> None:
        manifest = _manifest()
        tables = list(manifest["namespace"]["tables"])
        roles = ("sqag_runtime", "sqag_maintenance")
        rows = [(table, "sqag_migrator", role, "SELECT", False) for table in tables for role in roles]
        rows.extend([(tables[0], "sqag_migrator", "sqag_runtime", "INSERT", False), (tables[0], "sqag_migrator", "sqag_maintenance", "UPDATE", False)])
        self.assertEqual(len(rows), 34)
        self.assertEqual(len([row for row in rows if row[0] in roles]), 0)
        pre = runner.PrivilegeSnapshot.from_mapping(_observations(table_acl=rows))
        plan = runner.build_plan(pre, pre, manifest)
        self.assertEqual(len(plan.authorised["table_acl"].before), 34)
        self.assertTrue(plan.partition.coverage)
        self.assertTrue(plan.partition.disjointness)

    def test_plan_is_deterministic_and_partition_is_disjoint_complete(self) -> None:
        pre, target = _pre_and_target()
        first = runner.build_plan(pre, target, _manifest())
        second = runner.build_plan(pre, target, _manifest())
        self.assertEqual(first, second)
        self.assertTrue(first.partition.coverage)
        self.assertTrue(first.partition.disjointness)
        self.assertEqual(first.inverse.prestate, pre)

    def test_public_receipt_contains_no_snapshot_rows_or_private_exception_text(self) -> None:
        public = runner.run_convergence(_FixtureAdapter()).to_public_json()
        self.assertNotIn("sqag_profiles", public)
        self.assertNotIn("synthetic detail", public)
        self.assertNotIn("password", public.lower())
        self.assertNotIn("token", public.lower())


try:
    from tests.test_runtime_privilege_contract import RuntimePrivilegeContractPostgresIntegrationTest, postgres_test_enabled, safe_postgres_url
    from webapp import server as webapp
except ImportError:
    RuntimePrivilegeContractPostgresIntegrationTest = None  # type: ignore[assignment]
    postgres_test_enabled = lambda: False  # type: ignore[assignment]
    safe_postgres_url = None  # type: ignore[assignment]
    webapp = None  # type: ignore[assignment]


class _PostgresRunnerAdapter:
    def __init__(self, fixture: Any, *, fail_context: str | None = None) -> None:
        self.fixture = fixture
        self.fail_context = fail_context
        with fixture._admin_connection("postgres") as connection:
            connection.execute("alter role neondb_owner login")
        self.target = self._snapshot()
        with fixture._admin_connection("postgres") as connection:
            connection.execute(fixture.sql.SQL("alter database {} owner to sqag_migrator").format(fixture.sql.Identifier(fixture.database_name)))
        self.pre = self._snapshot()

    def _role_connection(self, role: str):
        return self.fixture.psycopg.connect(safe_postgres_url(role, self.fixture.database_name), row_factory=self.fixture.dict_row, options="-c search_path=public,pg_catalog", autocommit=True)

    def admit(self, manifest: dict[str, Any]) -> runner.AdmissionEvidence:
        identities: dict[str, tuple[str, str, str]] = {}
        for role in ("neondb_owner", "sqag_migrator"):
            with self._role_connection(role) as connection:
                row = connection.execute("select session_user, current_user, current_database()").fetchone()
                identities[role] = (row["session_user"], row["current_user"], row["current_database"])
        if any(session != role or current != role or database != self.fixture.database_name for role, (session, current, database) in identities.items()):
            raise runner.GateError(runner.SemanticCode.PRESTATE_MISMATCH, runner.Phase.ADMISSION)
        with self.fixture._admin_connection() as connection:
            version = int(connection.execute("select current_setting('server_version_num') as server_version_num").fetchone()["server_version_num"]) // 10000
        return runner.AdmissionEvidence(runner.Identity("neondb_owner", "neondb_owner"), runner.Identity("sqag_migrator", "sqag_migrator"), identities["neondb_owner"][2] == identities["sqag_migrator"][2], version, 0)

    def _snapshot(self) -> runner.PrivilegeSnapshot:
        with self.fixture._admin_connection() as connection:
            database = connection.execute("select owner.rolname as owner from pg_catalog.pg_database d join pg_catalog.pg_roles owner on owner.oid = d.datdba where d.datname = current_database()").fetchone()
            database_acl = connection.execute("select case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_database d left join lateral aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where d.datname = current_database() order by grantee, acl.privilege_type").fetchall()
            schema = connection.execute("select owner.rolname as owner from pg_catalog.pg_namespace n join pg_catalog.pg_roles owner on owner.oid = n.nspowner where n.nspname = 'public'").fetchone()
            schema_acl = connection.execute("select case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_namespace n left join lateral aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' order by grantee, acl.privilege_type").fetchall()
            table_rows = connection.execute("select c.relname, owner.rolname as owner, case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace join pg_catalog.pg_roles owner on owner.oid = c.relowner left join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'sqag_%' order by c.relname, grantee, acl.privilege_type").fetchall()
            column_rows = connection.execute("select n.nspname, c.relname, a.attname, case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_attribute a join pg_catalog.pg_class c on c.oid = a.attrelid join pg_catalog.pg_namespace n on n.oid = c.relnamespace left join lateral pg_catalog.aclexplode(a.attacl) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'sqag_%' and a.attnum > 0 and not a.attisdropped and a.attacl is not null order by c.relname, a.attname, grantee, acl.privilege_type").fetchall()
            membership = connection.execute("select parent.rolname, member.rolname, grantor.rolname, am.admin_option, am.inherit_option, am.set_option from pg_catalog.pg_auth_members am join pg_catalog.pg_roles parent on parent.oid = am.roleid join pg_catalog.pg_roles member on member.oid = am.member join pg_catalog.pg_roles grantor on grantor.oid = am.grantor where parent.rolname in ('sqag_runtime','sqag_migrator','sqag_maintenance') or member.rolname in ('sqag_runtime','sqag_migrator','sqag_maintenance') or grantor.rolname in ('sqag_runtime','sqag_migrator','sqag_maintenance') order by parent.rolname, member.rolname, grantor.rolname").fetchall()
            role_attributes = connection.execute("select rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls, rolconnlimit from pg_catalog.pg_roles where rolname in ('sqag_runtime','sqag_migrator','sqag_maintenance') order by rolname").fetchall()
            ownership = connection.execute("select c.relname, owner.rolname from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace join pg_catalog.pg_roles owner on owner.oid = c.relowner where n.nspname = 'public' and c.relname like 'sqag_%' order by c.relname").fetchall()
            defaults = connection.execute("select coalesce(grantee_role.rolname, 'PUBLIC') as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_default_acl d left join lateral aclexplode(d.defaclacl) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where d.defaclrole in (select oid from pg_catalog.pg_roles where rolname in ('sqag_runtime','sqag_migrator','sqag_maintenance'))").fetchall()
            unrelated = connection.execute("select c.relname, c.relkind, owner.rolname from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace join pg_catalog.pg_roles owner on owner.oid = c.relowner where n.nspname = 'public' and c.relname not like 'sqag_%' order by c.relname").fetchall()
        return runner.PrivilegeSnapshot.from_mapping({"database": {"owner": database["owner"], "acl": [tuple(row.values()) for row in database_acl]}, "schema": {"owner": schema["owner"], "acl": [tuple(row.values()) for row in schema_acl]}, "table_acl": [tuple(row.values()) for row in table_rows], "column_acl": [tuple(row.values()) for row in column_rows], "membership": [tuple(row.values()) for row in membership], "role_attributes": [tuple(row.values()) for row in role_attributes], "object_ownership": [tuple(row.values()) for row in ownership], "default_acls": [tuple(row.values()) for row in defaults], "unrelated_objects": [tuple(row.values()) for row in unrelated], "unrelated_data": {"business_rows_read": False}})

    def read_snapshot(self) -> runner.PrivilegeSnapshot:
        return self._snapshot()

    def target_snapshot(self, manifest: dict[str, Any]) -> runner.PrivilegeSnapshot:
        return self.target

    def capture_inverse(self, prestate: runner.PrivilegeSnapshot) -> runner.InverseCapture:
        return runner.InverseCapture(prestate)

    def apply_authorised_delta(self, plan: runner.ConvergencePlan) -> runner.MutationOutcome:
        with self.fixture._admin_connection("postgres") as connection:
            connection.execute(self.fixture.sql.SQL("alter database {} owner to neondb_owner").format(self.fixture.sql.Identifier(self.fixture.database_name)))
        self.fixture._configure_acl_contract(self.fixture.database_name)
        return runner.MutationOutcome(True)

    def verify_canonical(self, context: str, manifest: dict[str, Any]) -> dict[str, Any]:
        if self.fail_context == context:
            return {"status": "failed"}
        role = "neondb_owner" if context == "provider_admin" else "sqag_migrator"
        with self._role_connection(role) as raw:
            identity = raw.execute("select session_user, current_user").fetchone()
            if identity["session_user"] != role or identity["current_user"] != role:
                code = runner.SemanticCode.CANONICAL_PROVIDER_ADMIN_VERIFIER if context == "provider_admin" else runner.SemanticCode.CANONICAL_OPERATOR_VERIFIER
                raise runner.GateError(code, runner.Phase.FINAL_VERIFY, mutation_started=True)
            adapter = webapp.PostgresConnectionAdapter(raw)
            return canonical.verify_postgres_privilege_contract(adapter, manifest)

    def rollback(self, inverse: runner.InverseCapture) -> None:
        with self.fixture._admin_connection("postgres") as connection:
            database = self.fixture.sql.Identifier(self.fixture.database_name)
            connection.execute(self.fixture.sql.SQL("alter database {} owner to sqag_migrator").format(database))
            for grantee in ("PUBLIC", "sqag_runtime", "sqag_migrator", "sqag_maintenance"):
                target = self.fixture.sql.SQL("PUBLIC") if grantee == "PUBLIC" else self.fixture.sql.Identifier(grantee)
                connection.execute(self.fixture.sql.SQL("revoke all privileges on database {} from {}").format(database, target))
            connection.execute(self.fixture.sql.SQL("grant connect on database {} to PUBLIC").format(database))
            connection.execute(self.fixture.sql.SQL("grant connect on database {} to sqag_runtime, sqag_maintenance").format(database))
            connection.execute(self.fixture.sql.SQL("grant connect, create, temporary on database {} to sqag_migrator").format(database))

    def verify_restoration(self, inverse: runner.InverseCapture) -> bool:
        return self._snapshot() == inverse.prestate


if RuntimePrivilegeContractPostgresIntegrationTest is not None:
    @unittest.skipUnless(postgres_test_enabled(), "disposable PostgreSQL17 service is not configured")
    class DurableRunnerPostgresIntegrationTest(unittest.TestCase):
        @classmethod
        def setUpClass(cls) -> None:
            RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()

        def setUp(self) -> None:
            self.fixture = RuntimePrivilegeContractPostgresIntegrationTest("test_real_pg17_option_a_provider_rows_and_effective_database_matrix")
            self.fixture.setUp()
            self.addCleanup(self.fixture.doCleanups)

        def test_disposable_success_uses_both_direct_contexts_and_canonical_verifier(self) -> None:
            adapter = _PostgresRunnerAdapter(self.fixture)
            public = runner.run_convergence(adapter).to_public_dict()
            self.assertEqual(public["verdict"], "PASS")
            self.assertEqual(public["rollback"], "not_required")
            self.assertTrue(public["final_verification_reached"])
            self.assertEqual(public["final_database_owner"], "neondb_owner")
            self.assertEqual(public["final_public_schema_owner"], "pg_database_owner")
            self.assertTrue(all(value == "PASS" for value in public["checks"].values()))

        def test_disposable_final_failure_executes_inverse_and_restores_owner(self) -> None:
            adapter = _PostgresRunnerAdapter(self.fixture, fail_context="provider_admin")
            public = runner.run_convergence(adapter).to_public_dict()
            self.assertEqual(public["verdict"], "BLOCKED")
            self.assertEqual(public["semantic_failure_code"], "canonical_provider_admin_verifier")
            self.assertEqual(public["rollback"], "restored")
            self.assertTrue(public["restoration_verified"])
            self.assertEqual(adapter.read_snapshot().value("database"), adapter.pre.value("database"))


if __name__ == "__main__":
    unittest.main()
