"""Synthetic registry controls. Names state the failure each test prevents."""

import contextlib
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from webapp import quote_api_registry as registry
from webapp.quote_api_auth import QuoteApiAdmission, AdmissionError, secret_verifier
from webapp.quote_context import QuoteContext

ROOT = Path(__file__).resolve().parents[1]
SQLITE_SOURCE = ROOT / "migrations/012_quote_api_admission.sql"
POLICY = json.dumps({"version": 1, "pricing_reference_ids": ["book_test"],
    "profile_ids": ["profile_test"], "active_pricing_reference_id": "book_test",
    "active_profile_id": "profile_test", "tax_rate": "0.09"})
CID = "A" * 22
SUBJECT = "service:" + "B" * 21 + "Q"
VERIFIER = secret_verifier(bytes(range(32)))


def assert_fixture_schema(test, connection):
    """Do not trust IF NOT EXISTS to hide a pre-existing different schema."""
    from webapp.postgres_migrations import MIGRATION_OBJECTS
    for table in MIGRATION_OBJECTS[-1].tables:
        rows=connection.execute('pragma table_info('+table.name+')').fetchall()
        observed=tuple((r[1],r[2].lower(),not bool(r[3]),r[4]) for r in rows)
        expected=tuple((c.name,{'bigint':'integer','smallint':'integer','bytea':'blob'}.get(c.type_name,c.type_name),
                        c.nullable,c.default_sql) for c in table.columns)
        test.assertEqual(observed,expected)
    expected_triggers={t.name for t in MIGRATION_OBJECTS[-1].triggers}
    test.assertEqual({r[0] for r in connection.execute("select name from sqlite_master where type='trigger'")},expected_triggers)
    for index in MIGRATION_OBJECTS[-1].indexes:
        rows=connection.execute('pragma index_info('+index.name+')').fetchall()
        test.assertEqual(tuple(r[2] for r in rows),index.key_semantics)
        flags={r[1]:r[2] for r in connection.execute('pragma index_list('+index.table_name+')')}
        test.assertEqual(bool(flags[index.name]),index.unique)


def seed(connection, sql=lambda s: s):
    """Test-only provisioning with reserved deterministic synthetic verifier bytes."""
    connection.execute(sql("insert into sqag_quote_api_tenants "
        "(tenant_id,workspace_id,state,policy_json,created_at,expires_at) values (?,?,?,?,?,?)"),
        ("tenant.test", "workspace_test", "active", POLICY, 0, 200000))
    connection.execute(sql("insert into sqag_quote_api_callers "
        "(tenant_id,caller_id,owner_subject,credential_id,verifier,state,operations_mask,created_at,expires_at) "
        "values (?,?,?,?,?,?,?,?,?)"), ("tenant.test", "caller.test", SUBJECT, CID, VERIFIER, "active", 255, 0, 200000))


def operator_change(connection, *, kind, expected=1, now=120, verifier=b"v" * 32, postgres=False):
    """Test-only locked CAS model. No operator helper is shipped in application code."""
    suffix = " for update" if postgres else ""
    connection.execute("select tenant_id from sqag_quote_api_tenants where tenant_id=?" + suffix,
                       ("tenant.test",)).fetchone()
    row = connection.execute("select state,credential_revision,authority_revision from sqag_quote_api_callers "
                             "where tenant_id=? and caller_id=?" + suffix,
                             ("tenant.test", "caller.test")).fetchone()
    state, revision, authority = tuple(row.values()) if isinstance(row, dict) else tuple(row)
    parent = connection.execute("select state from sqag_quote_api_tenants where tenant_id=?", ("tenant.test",)).fetchone()
    parent_state = parent["state"] if isinstance(parent, dict) else parent[0]
    if state == "revoked" and kind == "revoke":
        return False
    if (state == "revoked" or parent_state == "revoked" or revision != expected
            or revision >= registry.MAX_REVISION or (kind == "revoke" and authority >= registry.MAX_REVISION)):
        raise registry.RegistryDenied()
    if kind == "rotate":
        changed = connection.execute("update sqag_quote_api_callers set verifier=?,credential_revision=credential_revision+1 "
            "where tenant_id=? and caller_id=? and credential_revision=?", (verifier,"tenant.test","caller.test",expected))
    else:
        changed = connection.execute("update sqag_quote_api_callers set state='revoked',revoked_at=?,"
            "credential_revision=credential_revision+1,authority_revision=authority_revision+1 "
            "where tenant_id=? and caller_id=? and credential_revision=?", (now,"tenant.test","caller.test",expected))
    if changed.rowcount != 1:
        raise registry.RegistryDenied()
    return True


def context(record):
    return QuoteContext("service", record.owner_subject, record.tenant_id, record.workspace_id,
        record.caller_id, record.revision, record.operations, "CustomerEstimateView",
        record.authority_revision, (("registry_revision", record.authority_revision),), "test-reference")


class Audit:
    def stage(self, connection, event):
        # This fixture evidence shares the operation's transaction/savepoint.
        connection.execute("insert into fixture_audit values (?,?)", (event["operation"],event["state"]))


class RegistryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "registry.sqlite3"
        self.now = 120
        with contextlib.closing(sqlite3.connect(self.path)) as c:
            c.executescript(SQLITE_SOURCE.read_text(encoding="utf-8"))
            assert_fixture_schema(self,c)
            c.execute("create table fixture_audit (operation text,state text)")
            seed(c)
            c.commit()
        self.adapter = registry.SQLiteCredentialRegistry(connection_factory=self.transaction,
            clock=lambda: self.now, audit_sink=Audit())

    @contextlib.contextmanager
    def transaction(self):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            c.execute("pragma foreign_keys=on")
            c.execute("begin immediate")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def snapshot(self):
        with self.transaction() as c:
            return tuple(tuple(tuple(r) for r in c.execute("select * from " + table).fetchall())
                         for table in ("sqag_quote_api_tenants", "sqag_quote_api_callers", "fixture_audit"))

    def ctx(self):
        return context(self.adapter.lookup(CID))

    def test_lookup_does_not_cache_rotated_or_revoked_verifiers_or_reset_counters(self):
        before = self.adapter.lookup(CID)
        ctx=self.ctx()
        with self.transaction() as c:
            self.adapter.reserve(c, ctx, "create")
        counters = self.snapshot()[0][0][-6:]
        with self.transaction() as c:
            operator_change(c, kind="rotate")
        after = self.adapter.lookup(CID)
        self.assertEqual((after.caller_id, after.owner_subject, after.authority_revision),
                         (before.caller_id, before.owner_subject, before.authority_revision))
        self.assertNotEqual(after.verifier, before.verifier)
        self.assertEqual(after.revision, "2")
        with self.transaction() as c:
            operator_change(c,kind="revoke",expected=2)
        self.assertTrue(self.adapter.lookup(CID).revoked)
        self.assertEqual(counters, self.snapshot()[0][0][-6:])
        before = self.snapshot()
        with self.transaction() as c:
            self.assertFalse(operator_change(c,kind="revoke",expected=3))
            with self.assertRaises(registry.RegistryDenied): operator_change(c,kind="rotate",expected=3)
        self.assertEqual(before,self.snapshot())

    def test_same_revision_rotations_have_one_winner_without_lost_update(self):
        results=[]; errors=[]; barrier=threading.Barrier(2)
        def worker():
            try:
                barrier.wait(timeout=5)
                with self.transaction() as c: operator_change(c,kind="rotate")
                results.append("rotated")
            except registry.RegistryDenied: results.append("conflict")
            except BaseException as e: errors.append(type(e).__name__)
        workers=[threading.Thread(target=worker) for _ in range(2)]
        for w in workers: w.start()
        for w in workers: w.join(10); self.assertFalse(w.is_alive())
        self.assertEqual(errors,[]); self.assertCountEqual(results,["rotated","conflict"])
        self.assertEqual(self.adapter.lookup(CID).revision,"2")

    def test_two_workers_cannot_overrun_shared_active_job_cap(self):
        ctx=self.ctx(); results=[]; errors=[]; barrier=threading.Barrier(2)
        with self.transaction() as c:
            c.execute("update sqag_quote_api_tenants set active_jobs=1")
        def worker():
            try:
                barrier.wait(timeout=5)
                with self.transaction() as c: self.adapter.reserve(c,ctx,"create")
                results.append("reserved")
            except registry.RegistryError: results.append("denied")
            except BaseException as e: errors.append(type(e).__name__)
        workers=[threading.Thread(target=worker) for _ in range(2)]
        for w in workers: w.start()
        for w in workers: w.join(10); self.assertFalse(w.is_alive())
        self.assertEqual(errors,[]); self.assertCountEqual(results,["reserved","denied"])
        self.assertEqual(self.snapshot()[0][0][-2],2)
        self.assertEqual(len(self.snapshot()[2]),1)

    def test_shared_tenant_and_foreign_tenant_counters_do_not_mutate_other_callers(self):
        cid2='C'*21+'A'; cid3='D'*21+'A'
        with self.transaction() as c:
            c.execute("insert into sqag_quote_api_tenants (tenant_id,workspace_id,state,policy_json,created_at,expires_at) values ('other.test','other_workspace','active',?,0,200000)",(POLICY,))
            for tenant,caller,cid in (('tenant.test','second.test',cid2),('other.test','third.test',cid3)):
                c.execute("insert into sqag_quote_api_callers (tenant_id,caller_id,owner_subject,credential_id,verifier,state,operations_mask,created_at,expires_at) values (?,?,?,?,?,'active',255,0,200000)",
                          (tenant,caller,'service:'+cid,cid,VERIFIER))
        contexts=[self.ctx(),context(self.adapter.lookup(cid2)),context(self.adapter.lookup(cid3))]
        for ctx in contexts:
            with self.transaction() as c: self.adapter.reserve(c,ctx,'create')
        with self.transaction() as c:
            self.assertEqual([tuple(r) for r in c.execute('select tenant_id,active_jobs from sqag_quote_api_tenants order by tenant_id')],
                             [('other.test',1),('tenant.test',2)])
            self.assertEqual([r[0] for r in c.execute('select counter_revision from sqag_quote_api_callers')],[1,1,1])
        before=self.snapshot()
        with self.transaction() as c:
            with self.assertRaises(registry.RegistryError): self.adapter.reserve(c,replace(contexts[2],tenant_id='tenant.test'),'create')
        self.assertEqual(before,self.snapshot())

    def test_foreign_stale_state_expiry_scope_matrix_has_zero_counter_or_audit_writes(self):
        ctx=self.ctx()
        for changes in ({"tenant_id":"foreign"}, {"workspace_id":"foreign"}, {"caller_id":"foreign"},
                        {"owner_subject":"service:foreign"}, {"auth_revision":"0"},
                        {"authority_revision":"t0:c1"}, {"authority_snapshot":(("foreign","claim"),)},
                        {"operations":frozenset({"poll"})}):
            for op in registry.OPERATIONS:
                before=self.snapshot()
                with self.subTest(changes=changes,operation=op), self.transaction() as c:
                    with self.assertRaises(registry.RegistryError): self.adapter.revalidate(c,replace(ctx,**changes),op)
                self.assertEqual(before,self.snapshot())
        for table in ("sqag_quote_api_tenants","sqag_quote_api_callers"):
            for state in ("disabled","revoked"):
                with self.transaction() as c:
                    c.execute("update "+table+" set state=?, revoked_at=?",(state,120 if state=="revoked" else None))
                before=self.snapshot()
                for op in registry.OPERATIONS:
                    with self.transaction() as c:
                        with self.assertRaises(registry.RegistryError): self.adapter.revalidate(c,ctx,op)
                self.assertEqual(before,self.snapshot())
                with self.transaction() as c: c.execute("update "+table+" set state='active',revoked_at=null")
        for now in (199999,200000,200001):
            self.now=now; before=self.snapshot()
            with self.transaction() as c:
                if now<200000: self.adapter.revalidate(c,ctx,"create")
                else:
                    with self.assertRaises(registry.RegistryError): self.adapter.revalidate(c,ctx,"create")
            self.assertEqual(before,self.snapshot())

    def test_failed_audit_and_second_update_roll_back_both_counter_rows_even_if_caller_commits(self):
        ctx=self.ctx()
        class BrokenAudit:
            def stage(self,connection,event):
                Audit().stage(connection,event)
                raise RuntimeError("synthetic-private-canary")
        self.adapter._audit=BrokenAudit(); before=self.snapshot()
        with self.transaction() as c:
            with self.assertRaisesRegex(registry.RegistryError,"^Quote registry unavailable$"):
                self.adapter.reserve(c,ctx,"create")
        self.assertEqual(before,self.snapshot())
        self.adapter._audit=Audit()
        with self.transaction() as c:
            c.execute("create trigger fail_second before update on sqag_quote_api_callers begin select raise(abort,'synthetic-private-canary'); end")
        with self.transaction() as c:
            with self.assertRaises(registry.RegistryError): self.adapter.reserve(c,ctx,"create")
        self.assertEqual(before,self.snapshot())

    def test_each_counter_cap_and_revision_ceiling_prevents_overflow_without_partial_writes(self):
        ctx=self.ctx()
        cases=(("create_minute_count",15,"create",60),("create_day_count",100,"create",86400),
               ("active_jobs",2,"create",None),("poll_minute_count",120,"poll",60))
        for column,cap,op,size in cases:
            self.now=86400
            table="sqag_quote_api_callers" if op=="poll" else "sqag_quote_api_tenants"
            for n in (cap-1,cap,cap+1):
                with self.subTest(column=column,n=n):
                    with self.transaction() as c:
                        c.execute("update sqag_quote_api_tenants set create_minute_start=86400,create_day_start=86400,create_minute_count=0,create_day_count=0,active_jobs=0")
                        c.execute("update sqag_quote_api_callers set poll_minute_start=86400,poll_minute_count=0")
                        if n>cap:
                            with self.assertRaises(sqlite3.IntegrityError): c.execute("update "+table+" set "+column+"=?",(n,))
                            continue
                        c.execute("update "+table+" set "+column+"=?",(n,))
                    before=self.snapshot()
                    with self.transaction() as c:
                        if n<cap: self.adapter.reserve(c,ctx,op)
                        else:
                            with self.assertRaises(registry.RegistryError): self.adapter.reserve(c,ctx,op)
                    if n==cap: self.assertEqual(before,self.snapshot())
        for table in ("sqag_quote_api_tenants","sqag_quote_api_callers"):
            with self.transaction() as c: c.execute("update "+table+" set counter_revision=?",(registry.MAX_REVISION,))
            before=self.snapshot()
            with self.transaction() as c:
                with self.assertRaises(registry.RegistryError): self.adapter.reserve(c,ctx,"create")
            self.assertEqual(before,self.snapshot())

    def test_utc_rollover_resets_only_elapsed_buckets_and_never_active_jobs(self):
        ctx=self.ctx()
        with self.transaction() as c:
            c.execute("update sqag_quote_api_tenants set create_minute_start=86340,create_day_start=0,create_minute_count=15,create_day_count=100,active_jobs=1")
        self.now=86400
        with self.transaction() as c: self.adapter.reserve(c,ctx,"create")
        row=self.snapshot()[0][0]
        self.assertEqual(row[8:13],(86400,1,86400,1,2))
        self.now=86399; before=self.snapshot()
        with self.transaction() as c:
            with self.assertRaises(registry.RegistryError): self.adapter.reserve(c,ctx,"create")
        self.assertEqual(before,self.snapshot())

    def test_immutable_bindings_fk_verifier_and_unique_ids_prevent_authority_reassignment(self):
        for table,columns in (("sqag_quote_api_tenants",("tenant_id","workspace_id")),
                              ("sqag_quote_api_callers",("tenant_id","caller_id","owner_subject","credential_id"))):
            for column in columns:
                before=self.snapshot()
                with self.transaction() as c:
                    with self.assertRaises(sqlite3.IntegrityError): c.execute("update "+table+" set "+column+"="+column)
                self.assertEqual(before,self.snapshot())
        for size in (31,32,33):
            with self.transaction() as c:
                if size==32: c.execute("update sqag_quote_api_callers set verifier=?",(bytes(size),))
                else:
                    with self.assertRaises(sqlite3.IntegrityError): c.execute("update sqag_quote_api_callers set verifier=?",(bytes(size),))
        with self.transaction() as c:
            for changes in (("foreign","other",SUBJECT,CID), ("tenant.test","other",SUBJECT,"C"*21+"A"),
                            ("tenant.test","other","service:"+"C"*21+"A",CID)):
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("insert into sqag_quote_api_callers (tenant_id,caller_id,owner_subject,credential_id,verifier,created_at,expires_at) values (?,?,?,?,?,0,200000)",(*changes,VERIFIER))
        for column,value in (("operations_mask",256),("credential_revision",0),("credential_revision",registry.MAX_REVISION+1),
                             ("poll_minute_count",1.5),("poll_minute_start",61),("expires_at",0),("credential_id","x")):
            with self.transaction() as c:
                with self.assertRaises(sqlite3.IntegrityError): c.execute("update sqag_quote_api_callers set "+column+"=?",(value,))

    def test_malformed_policy_and_storage_failures_do_not_leak_canaries_or_gain_default_policy(self):
        for text in ('{}','[]',POLICY.replace('"version": 1','"version": 1, "version": 1'),
                     POLICY.replace('"0.09"','NaN'),POLICY.replace('"0.09"','"1e-2"'),
                     POLICY.replace('"profile_test"','null',1),POLICY.replace('"version": 1','"version": true')):
            with self.transaction() as c: c.execute("update sqag_quote_api_tenants set policy_json=?",(text,))
            before=self.snapshot()
            with self.assertRaisesRegex(registry.RegistryError,'^Quote registry unavailable$'): self.adapter.lookup(CID)
            self.assertEqual(before,self.snapshot())
        def broken(): raise RuntimeError('synthetic-verifier-policy-canary')
        adapter=registry.PostgresCredentialRegistry(connection_factory=broken)
        with self.assertRaisesRegex(registry.RegistryError,'^Quote registry unavailable$'): adapter.lookup(CID)

    def test_missing_audit_sink_cannot_report_success(self):
        self.adapter._audit=None; before=self.snapshot(); ctx=self.ctx()
        with self.transaction() as c:
            with self.assertRaises(registry.RegistryError): self.adapter.reserve(c,ctx,"create")
        self.assertEqual(before,self.snapshot())

    def test_lookup_and_revalidation_are_read_only_and_context_contains_no_verifier_or_policy(self):
        before=self.snapshot(); ctx=self.ctx()
        with self.transaction() as c: self.adapter.revalidate(c,ctx,"create")
        self.assertEqual(before,self.snapshot())
        self.assertNotIn('verifier',repr(ctx)); self.assertNotIn(POLICY,repr(ctx))
        self.assertIsNone(self.adapter.lookup("C"*21+"A"))


class RegistrySourceControls(unittest.TestCase):
    def test_fixture_if_not_exists_cannot_hide_changed_column_default(self):
        changed=SQLITE_SOURCE.read_text(encoding='utf-8').replace("default 'disabled'","default 'active'",1)
        with contextlib.closing(sqlite3.connect(':memory:')) as c:
            c.executescript(changed)
            c.executescript(SQLITE_SOURCE.read_text(encoding='utf-8'))
            with self.assertRaises(AssertionError): assert_fixture_schema(self,c)

    def test_manifest_addition_does_not_move_011_index_or_seed_authority(self):
        from webapp import postgres_migrations as m
        self.assertEqual(m.MIGRATION_OBJECTS[9].indexes[0].name,"sqag_quote_ai_spend_session_idx")
        self.assertEqual(m.migration_manifest(ROOT/'migrations')[-1].sequence_no,11)
        pg=(ROOT/'migrations/012_quote_api_admission_postgres.sql').read_text()
        self.assertEqual(len(m._split_sql_statements(pg)),18)
        self.assertEqual(len(m.MIGRATION_OBJECTS[-1].tables),2)
        self.assertEqual(len(m.MIGRATION_OBJECTS[-1].indexes),3)
        self.assertEqual(len(m.MIGRATION_OBJECTS[-1].triggers),2)
        self.assertNotRegex(pg.lower(),r'\b(?:insert|delete|truncate|drop)\s+(?:into|from|table)')

    def test_startup_flags_off_cannot_connect_or_auto_apply_registry(self):
        source=(ROOT/'webapp/server.py').read_text()
        self.assertNotIn('012_quote_api_admission.sql',source)
        self.assertNotIn('PostgresCredentialRegistry(',source)
        self.assertNotIn('SQLiteCredentialRegistry(',source)
        a=QuoteApiAdmission(registry=mock.Mock())
        with self.assertRaises(AdmissionError):
            a.admit(operation='create',headers=mock.Mock(),service_enabled=False,staff_enabled=False,
                reference='test',singleton_header=mock.Mock(),rate_limited=mock.Mock(),
                staff_context=mock.Mock(),staff_mutation_guard=mock.Mock())
        a._registry.lookup.assert_not_called()

    def test_wrong_postgres_session_cannot_issue_registry_sql(self):
        roles={}
        class Cursor:
            def fetchone(self): return roles
        class Connection:
            def __init__(self): self.calls=[]
            def execute(self,sql,*args): self.calls.append(sql); return Cursor()
        @contextlib.contextmanager
        def factory(): yield c
        adapter=registry.PostgresCredentialRegistry(connection_factory=factory)
        for session,active in ((role,role) for role in ('sqag_migrator','sqag_maintenance','PUBLIC','provider','bootstrap')):
            roles.update(session_role=session,active_role=active); c=Connection()
            with self.assertRaises(registry.RegistryError): adapter.lookup(CID)
            self.assertEqual(c.calls,['select session_user as session_role, current_user as active_role'])
        roles.update(session_role='sqag_migrator',active_role='sqag_runtime'); c=Connection()
        with self.assertRaises(registry.RegistryError): adapter.lookup(CID)
        self.assertEqual(len(c.calls),1)

    def test_postgres_lookup_projection_identity_and_snapshot_fail_closed_without_logging_verifier(self):
        class Cursor:
            def __init__(self,rows): self.rows=rows
            def fetchone(self): return self.rows[0]
            def fetchall(self): return self.rows
        rows=[]; isolation='read committed'; calls=[]
        class Connection:
            def execute(self,sql,params=()):
                calls.append(sql)
                if 'session_user' in sql: return Cursor([dict(session_role='sqag_runtime',active_role='sqag_runtime')])
                if 'transaction_isolation' in sql: return Cursor([dict(transaction_isolation=isolation)])
                return Cursor(rows)
        @contextlib.contextmanager
        def factory(): yield Connection()
        adapter=registry.PostgresCredentialRegistry(connection_factory=factory)
        self.assertIsNone(adapter.lookup(CID))
        good=dict(zip(registry.LOOKUP_COLUMNS,(CID,memoryview(VERIFIER),'tenant.test','workspace_test',
            'caller.test',SUBJECT,1,1,1,255,200000,'active','active',POLICY)))
        rows[:]=[good]; record=adapter.lookup(CID)
        self.assertIs(type(record.verifier),bytes)
        for change in ({'verifier':b'private-canary'}, {'credential_revision':True},
                       {'credential_id':'C'*21+'A'}, {'policy_json':'private-canary'}, {'unexpected':'private-canary'}):
            rows[:]=[{**good,**change}]
            with self.assertRaisesRegex(registry.RegistryError,'^Quote registry unavailable$'): adapter.lookup(CID)
        rows[:]=[good,good]
        with self.assertRaises(registry.RegistryError): adapter.lookup(CID)
        rows[:]=[good]; isolation='repeatable read'; calls.clear()
        with self.assertRaises(registry.RegistryError): adapter.lookup(CID)
        self.assertEqual(len(calls),2)


if __name__ == '__main__':
    unittest.main()
