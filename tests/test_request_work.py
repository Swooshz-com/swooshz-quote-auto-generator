"""RequestWork: one owner for the database connections and schema checks of one request (#429).

Closure matrix row 6 (connection) as tables. Engine rows run on a real SQLite file always
and on the disposable PostgreSQL-17 service when CI provides it; driver rows use a fake
PostgreSQL driver so the role check and socket faults run everywhere.
"""
from __future__ import annotations

import contextlib
import contextvars
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import test_runtime_privilege_contract as runtime
from webapp import server as s

WORKSPACE = "workspace-request-work"


class OpenCounter:
    """Counts physical connections opened and closed through one module-level opener."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.opened = 0
        self.closed = 0
        self._real = getattr(s, name)

    @contextlib.contextmanager
    def _counted(self, *args, **kwargs):
        with self._real(*args, **kwargs) as connection:
            self.opened += 1
            try:
                yield connection
            finally:
                self.closed += 1

    def patch(self):
        return mock.patch.object(s, self.name, self._counted)


class SqliteEngine:
    opener = "sqlite_storage_connection"

    def __init__(self, test: unittest.TestCase) -> None:
        folder = tempfile.TemporaryDirectory(prefix="request-work-", ignore_cleanup_errors=True)
        test.addCleanup(folder.cleanup)
        self.url = "sqlite:///" + (Path(folder.name) / "work.sqlite3").as_posix()
        with s.sqlite_storage_connection(self.url) as connection:
            connection.execute("create table probe (x integer)")
            connection.execute("create table parent (id integer primary key)")
            connection.execute(
                "create table child (id integer, parent_id integer references parent(id) deferrable initially deferred)"
            )
            connection.commit()

    def storage(self, role: object = None) -> s.DatabaseSqagStorage:
        if role is None:
            return s.DatabaseSqagStorage(self.url, WORKSPACE, role="admin", user_id="request-work-user")
        return s.DatabaseSqagStorage(self.url, WORKSPACE, role="admin", user_id="request-work-user", expected_session_role=role)

    def other_key_storage(self) -> s.DatabaseSqagStorage:
        return self.storage(role=s.SQAG_RUNTIME_DATABASE_ROLE)

    def ident(self, connection):
        return id(connection)

    def write(self, connection) -> None:
        connection.execute("insert into probe values (1)")

    def sees_write(self, connection) -> bool:
        return connection.execute("select count(*) as n from probe").fetchone()["n"] > 0

    def hold(self, connection) -> None:
        connection.execute("insert into probe values (2)")

    def held_elsewhere(self, connection) -> bool:
        # Another connection cannot see the outer block's uncommitted row.
        return connection.execute("select count(*) as n from probe where x = 2").fetchone()["n"] == 0

    def fail(self, connection) -> None:
        # A real failed commit: the deferred foreign key is checked at commit.
        connection.execute("insert into child values (1, 99)")
        connection.commit()

    def saw_failed_work(self, connection) -> bool:
        return connection.execute("select count(*) as n from child").fetchone()["n"] > 0


class PostgresEngine:
    opener = "postgres_storage_connection"
    LOCK_KEY = 4290001

    def __init__(self, test: unittest.TestCase, url: str) -> None:
        self.url = url

    def storage(self, role: object = None) -> s.DatabaseSqagStorage:
        return s.DatabaseSqagStorage(self.url, WORKSPACE, role="admin", user_id="request-work-user",
                                     expected_session_role=role or s.SQAG_RUNTIME_DATABASE_ROLE)

    def other_key_storage(self) -> s.DatabaseSqagStorage:
        return s.DatabaseSqagStorage(self.url + "?application_name=request-work-other", WORKSPACE, role="admin",
                                     user_id="request-work-user", expected_session_role=s.SQAG_RUNTIME_DATABASE_ROLE)

    def ident(self, connection):
        return connection.execute("select pg_backend_pid() as pid").fetchone()["pid"]

    def write(self, connection) -> None:
        connection.execute("select set_config('sqag.request_work_probe', 'dirty', true)")

    def sees_write(self, connection) -> bool:
        row = connection.execute("select coalesce(current_setting('sqag.request_work_probe', true), '') as v").fetchone()
        return row["v"] == "dirty"

    def hold(self, connection) -> None:
        connection.execute("select pg_advisory_xact_lock(?)", (self.LOCK_KEY,))

    def held_elsewhere(self, connection) -> bool:
        return not connection.execute("select pg_try_advisory_xact_lock(?) as ok", (self.LOCK_KEY,)).fetchone()["ok"]

    def fail(self, connection) -> None:
        self.write(connection)
        connection.execute("select 1 / 0")

    def saw_failed_work(self, connection) -> bool:
        return self.sees_write(connection)


class EngineRows:
    """Row 6 of the closure matrix; each row returns what it observed, compared to the table."""

    def engine(self):
        raise NotImplementedError

    def setUp(self):
        self.db = self.engine()
        self.counter = OpenCounter(self.db.opener)
        patcher = self.counter.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def row_sequential_blocks_share_one_connection(self):
        storage = self.db.storage()
        with s.RequestWork.scope():
            idents = []
            for _ in range(3):
                with storage.connection() as connection:
                    idents.append(self.db.ident(connection))
            opened_inside = self.counter.opened
        return {"opened": opened_inside, "same": len(set(idents)) == 1, "left_open": self.counter.opened - self.counter.closed}

    def row_uncommitted_work_is_dropped_on_return(self):
        storage = self.db.storage()
        with s.RequestWork.scope():
            with storage.connection() as connection:
                first = self.db.ident(connection)
                self.db.write(connection)
            with storage.connection() as connection:
                same = self.db.ident(connection) == first
                seen = self.db.sees_write(connection)
        return {"same": same, "seen": seen, "left_open": self.counter.opened - self.counter.closed}

    def row_nested_block_gets_its_own_connection(self):
        storage = self.db.storage()
        with s.RequestWork.scope():
            with storage.connection() as outer:
                self.db.hold(outer)
                with storage.connection() as inner:
                    other = self.db.ident(inner) != self.db.ident(outer)
                    held = self.db.held_elsewhere(inner)
            opened_inside = self.counter.opened
        return {"opened": opened_inside, "other": other, "held": held, "left_open": self.counter.opened - self.counter.closed}

    def row_failure_closes_the_connection(self):
        storage = self.db.storage()
        with s.RequestWork.scope():
            raised = False
            try:
                with storage.connection() as connection:
                    first = self.db.ident(connection)
                    self.db.fail(connection)
            except Exception:
                raised = True
            closed_at_failure = self.counter.closed
            with storage.connection() as connection:
                fresh = self.db.ident(connection) != first
                saw = self.db.saw_failed_work(connection)
        return {"raised": raised, "closed_at_failure": closed_at_failure, "fresh": fresh, "saw": saw,
                "left_open": self.counter.opened - self.counter.closed}

    def row_other_thread_never_shares(self):
        storage = self.db.storage()
        seen: dict[str, object] = {}
        with s.RequestWork.scope():
            with storage.connection() as connection:
                mine = self.db.ident(connection)
            context = contextvars.copy_context()

            def other():
                seen["work"] = s.RequestWork.current()
                with storage.connection() as connection:
                    seen["ident"] = self.db.ident(connection)
            worker = threading.Thread(target=lambda: context.run(other))
            worker.start()
            worker.join(10)
            closed_after_thread = self.counter.closed
        return {"work": seen["work"], "shared": seen["ident"] == mine, "thread_closed_its_own": closed_after_thread,
                "left_open": self.counter.opened - self.counter.closed}

    def row_request_ends_with_zero_open_on_error(self):
        storage = self.db.storage()
        try:
            with s.RequestWork.scope():
                with storage.connection() as outer:
                    self.db.ident(outer)
                    with storage.connection() as inner:
                        self.db.ident(inner)
                raise RuntimeError("synthetic handler failure")
        except RuntimeError:
            pass
        return {"opened": self.counter.opened, "left_open": self.counter.opened - self.counter.closed}

    def row_different_key_never_shares(self):
        first, second = self.db.storage(), self.db.other_key_storage()
        with s.RequestWork.scope():
            with first.connection() as connection:
                self.db.ident(connection)
            with second.connection() as connection:
                self.db.ident(connection)
            opened_inside = self.counter.opened
        return {"opened": opened_inside, "left_open": self.counter.opened - self.counter.closed}

    def row_without_request_work_each_block_opens_its_own(self):
        storage = self.db.storage()
        for _ in range(3):
            with storage.connection() as connection:
                self.db.ident(connection)
        return {"opened": self.counter.opened, "left_open": self.counter.opened - self.counter.closed}

    ROWS = (
        ("row_sequential_blocks_share_one_connection", {"opened": 1, "same": True, "left_open": 0}),
        ("row_uncommitted_work_is_dropped_on_return", {"same": True, "seen": False, "left_open": 0}),
        ("row_nested_block_gets_its_own_connection", {"opened": 2, "other": True, "held": True, "left_open": 0}),
        ("row_failure_closes_the_connection",
         {"raised": True, "closed_at_failure": 1, "fresh": True, "saw": False, "left_open": 0}),
        ("row_other_thread_never_shares",
         {"work": None, "shared": False, "thread_closed_its_own": 1, "left_open": 0}),
        ("row_request_ends_with_zero_open_on_error", {"opened": 2, "left_open": 0}),
        ("row_different_key_never_shares", {"opened": 2, "left_open": 0}),
        ("row_without_request_work_each_block_opens_its_own", {"opened": 3, "left_open": 0}),
    )

    def test_connection_rows(self):
        for name, expected in self.ROWS:
            with self.subTest(row=name):
                self.counter.opened = self.counter.closed = 0
                self.assertEqual(getattr(self, name)(), expected)


class SqliteRequestWorkTest(EngineRows, unittest.TestCase):
    def engine(self):
        return SqliteEngine(self)


@unittest.skipUnless(runtime.postgres_test_enabled(), "real disposable PostgreSQL-17 service is not configured")
class PostgresRequestWorkTest(EngineRows, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()

    def engine(self):
        fixture = runtime.RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return PostgresEngine(self, runtime.safe_postgres_url("sqag_runtime", fixture.database_name))

    def test_role_mismatch_is_refused_and_never_kept(self):
        # The URL logs in as sqag_runtime; a storage expecting the maintenance role must be refused every time.
        storage = self.db.storage(role=s.SQAG_MAINTENANCE_DATABASE_ROLE)
        with s.RequestWork.scope() as work:
            for _ in range(2):
                with self.assertRaises(s.SqagStorageAccessError) as refused:
                    with storage.connection():
                        self.fail("SQL ran after a role mismatch")
                self.assertEqual((refused.exception.status, refused.exception.reason),
                                 (503, "storage_postgres_session_role_mismatch"))
            self.assertEqual(work.open_connections, 0)


class FakeDriver:
    """A fake psycopg connect(): every physical connection answers the role query and records what ran."""

    def __init__(self, role: str = s.SQAG_RUNTIME_DATABASE_ROLE) -> None:
        self.role = role
        self.raw: list[runtime._FakeIdentityRawConnection] = []
        self.break_rollback = False

    def connect(self, _database_url):
        raw = runtime._FakeIdentityRawConnection(self.role)
        if self.break_rollback:
            def broken():
                raise OSError("synthetic dropped socket")
            raw.rollback = broken
        self.raw.append(raw)
        return raw


class DriverRowsTest(unittest.TestCase):
    URL = "postgresql://sqag_runtime:synthetic-secret@db.example.test/sqag"
    ROLE_QUERY = "select session_user as session_role, current_user as active_role"

    def setUp(self):
        self.driver = FakeDriver()
        patcher = mock.patch.object(s, "postgres_driver_connection_factory", return_value=self.driver.connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.storage = s.DatabaseSqagStorage(self.URL, WORKSPACE, role="admin", user_id="request-work-user",
                                             expected_session_role=s.SQAG_RUNTIME_DATABASE_ROLE)

    def blocks(self, count):
        for _ in range(count):
            with self.storage.connection() as connection:
                connection.execute("select 1")

    def row_role_query_once_per_physical_connection(self):
        with s.RequestWork.scope():
            self.blocks(3)
        return {"physical": len(self.driver.raw), "role_queries": self.driver.raw[0].statements.count(self.ROLE_QUERY),
                "rollbacks": self.driver.raw[0].rollback_calls, "closed": self.driver.raw[0].closed}

    def row_role_mismatch_is_refused_every_time(self):
        self.driver.role = "synthetic_other_role"
        refusals = []
        with s.RequestWork.scope() as work:
            for _ in range(2):
                try:
                    self.blocks(1)
                except s.SqagStorageAccessError as error:
                    refusals.append((error.status, error.reason))
            open_inside = work.open_connections
        return {"refusals": refusals, "physical": len(self.driver.raw), "all_closed": all(raw.closed for raw in self.driver.raw),
                "open_inside": open_inside, "app_sql_ran": any("select 1" in raw.statements for raw in self.driver.raw)}

    def row_dropped_socket_is_replaced(self):
        self.driver.break_rollback = True
        with s.RequestWork.scope():
            self.blocks(2)
        return {"physical": len(self.driver.raw), "all_closed": all(raw.closed for raw in self.driver.raw)}

    def row_idle_too_long_is_replaced(self):
        now = [0.0]
        work = s.RequestWork(clock=lambda: now[0])
        token = s.REQUEST_WORK_CONTEXT.set(work)
        try:
            self.blocks(1)
            now[0] = s.RequestWork.IDLE_REUSE_SECONDS
            self.blocks(1)
            reused_at_limit = len(self.driver.raw)
            first_closed_at_limit = self.driver.raw[0].closed
            now[0] = 2 * s.RequestWork.IDLE_REUSE_SECONDS + 0.001
            self.blocks(1)
            physical = len(self.driver.raw)
            first_closed = self.driver.raw[0].closed
        finally:
            s.REQUEST_WORK_CONTEXT.reset(token)
            work.close()
        return {"reused_at_limit": reused_at_limit, "first_closed_at_limit": first_closed_at_limit,
                "physical": physical, "first_closed": first_closed, "all_closed": all(raw.closed for raw in self.driver.raw)}

    def row_end_stage_closes_idle_connections(self):
        with s.RequestWork.scope() as work:
            self.blocks(1)
            s.RequestWork.end_stage()
            closed_at_stage = self.driver.raw[0].closed
            open_at_stage = work.open_connections
            self.blocks(1)
        return {"closed_at_stage": closed_at_stage, "open_at_stage": open_at_stage, "physical": len(self.driver.raw)}

    def row_nested_scope_reuses_the_outer_work(self):
        with s.RequestWork.scope() as outer:
            with s.RequestWork.scope() as inner:
                self.blocks(1)
                same = inner is outer
            still_active = s.RequestWork.current() is outer
            self.blocks(1)
        return {"same": same, "still_active": still_active, "physical": len(self.driver.raw),
                "after": s.RequestWork.current()}

    ROWS = (
        ("row_role_query_once_per_physical_connection",
         {"physical": 1, "role_queries": 1, "rollbacks": 3, "closed": True}),
        ("row_role_mismatch_is_refused_every_time",
         {"refusals": [(503, "storage_postgres_session_role_mismatch")] * 2, "physical": 2, "all_closed": True,
          "open_inside": 0, "app_sql_ran": False}),
        ("row_dropped_socket_is_replaced", {"physical": 2, "all_closed": True}),
        ("row_idle_too_long_is_replaced",
         {"reused_at_limit": 1, "first_closed_at_limit": False, "physical": 2, "first_closed": True, "all_closed": True}),
        ("row_end_stage_closes_idle_connections", {"closed_at_stage": True, "open_at_stage": 0, "physical": 2}),
        ("row_nested_scope_reuses_the_outer_work", {"same": True, "still_active": True, "physical": 1, "after": None}),
    )

    def test_driver_rows(self):
        for name, expected in self.ROWS:
            with self.subTest(row=name):
                self.driver.raw.clear()
                self.driver.role = s.SQAG_RUNTIME_DATABASE_ROLE
                self.driver.break_rollback = False
                self.assertEqual(getattr(self, name)(), expected)

    def test_without_request_work_behaviour_is_unchanged(self):
        self.blocks(3)
        self.assertEqual(len(self.driver.raw), 3)
        self.assertTrue(all(raw.closed for raw in self.driver.raw))
        self.assertTrue(all(raw.statements.count(self.ROLE_QUERY) == 1 for raw in self.driver.raw))
        self.assertTrue(all(raw.rollback_calls == 0 for raw in self.driver.raw))


class SchemaCheckRowsTest(unittest.TestCase):
    """One schema check per request or job stage; a failed check is never kept."""

    def setUp(self):
        self.calls = []
        self.failures = []

        def check(storage, required, *, reason, require_telemetry_successor=False):
            self.calls.append((storage.expected_session_role, reason))
            if self.failures:
                raise self.failures.pop(0)

        patcher = mock.patch.object(s.DatabaseSqagStorage, "_check_schema", check)
        patcher.start()
        self.addCleanup(patcher.stop)
        # _check_schema is replaced, so this file is never opened.
        self.url = "sqlite:///" + (Path(tempfile.gettempdir()) / f"request-work-schema-{uuid.uuid4().hex}.sqlite3").as_posix()
        self.storage = s.DatabaseSqagStorage(self.url, WORKSPACE, role="admin", user_id="request-work-user")

    def test_schema_rows(self):
        other_role = s.DatabaseSqagStorage(self.url, WORKSPACE, role="admin", user_id="request-work-user",
                                           expected_session_role=s.SQAG_RUNTIME_DATABASE_ROLE)
        refusal = s.SqagStorageAccessError("not migrated", status=503, reason="storage_database_not_migrated")

        def repeated_in_one_request():
            with s.RequestWork.scope():
                for _ in range(3):
                    self.storage.ensure_ready()
                self.storage.ensure_object_artifact_ready()
                other_role.ensure_ready()

        def outside_a_request():
            for _ in range(3):
                self.storage.ensure_ready()

        def failure_is_not_kept():
            self.failures.append(refusal)
            outcomes = []
            with s.RequestWork.scope():
                for _ in range(3):
                    try:
                        self.storage.ensure_ready()
                        outcomes.append("ready")
                    except s.SqagStorageAccessError as error:
                        outcomes.append(error.reason)
            return outcomes

        def new_stage_checks_again():
            with s.RequestWork.scope():
                self.storage.ensure_ready()
                s.RequestWork.end_stage()
                self.storage.ensure_ready()

        rows = (
            ("repeated in one request", repeated_in_one_request, None, 3),
            ("outside a request", outside_a_request, None, 3),
            ("failure is not kept", failure_is_not_kept, ["storage_database_not_migrated", "ready", "ready"], 2),
            ("new stage checks again", new_stage_checks_again, None, 2),
        )
        for name, run, outcome, checks in rows:
            with self.subTest(row=name):
                self.calls.clear()
                self.assertEqual(run(), outcome)
                self.assertEqual(len(self.calls), checks, self.calls)


if __name__ == "__main__":
    unittest.main()
