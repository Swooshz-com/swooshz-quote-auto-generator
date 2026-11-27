"""Request budget: database work per HTTP route and job stage (#429).

Drives the real routes on the shared publication fixture (SQLite always, the disposable
PostgreSQL-17 service when CI provides it) and counts, per request: physical database
connections, connections still open when it ends, schema checks, application statements
(transaction control, pragmas and catalogue reads left out), transaction ends and object
reads. A transaction end is a commit or rollback that closes an open transaction: one round
trip on PostgreSQL, where RequestWork's rollback on return replaces a close that did not wait.
On PostgreSQL it is read from the connection's real state; on SQLite it is modelled the same
way (any statement since the last end opens one). Connections, schema checks and object reads
are asserted exactly on both engines; statements and transaction ends exactly on SQLite and
printed on PostgreSQL. The modelled time at the copy-of-live latencies (connect 65 ms plus the
role query, statement or transaction end 23 ms, object read 210 ms) and the wall time are
printed, never asserted.
"""
from __future__ import annotations

import contextlib
import copy
import http.client
import json
import sqlite3
import threading
import time
import unittest
from unittest import mock

from tests import test_runtime_privilege_contract as runtime
from tests.publication_storage_support import PublicationStorageCases, s

CONNECT_SECONDS, STATEMENT_SECONDS, OBJECT_SECONDS = 0.065, 0.023, 0.21
SKIPPED_STATEMENTS = ("pragma", "begin", "commit", "rollback", "savepoint", "release", "--")


def publication_fixture(database_url):
    """The publication fixture alone; its own journeys run in their modules, not from here."""

    class Publication(PublicationStorageCases, unittest.TestCase):
        pass

    fixture = Publication("reopen")
    fixture.database_url = database_url
    return fixture


class Meter:
    """Counts the database and object work of each request through the real openers."""

    def __init__(self, backend, stack: contextlib.ExitStack) -> None:
        self.lock = threading.Lock()
        self.reset()
        real_sqlite, real_postgres = s.sqlite_storage_connection, s.postgres_storage_connection
        meter = self

        class MeteredSqlite(sqlite3.Connection):
            """A real sqlite3.Connection that reports commits and rollbacks closing a transaction."""

            dirty = False

            def commit(self):
                meter.transaction_end(self)
                return super().commit()

            def rollback(self):
                meter.transaction_end(self)
                return super().rollback()

            def close(self):
                self.dirty = False
                return super().close()

        MeteredSqlite.__module__ = "sqlite3"  # the forensic store picks its dialect by class module
        real_connect = sqlite3.connect

        @contextlib.contextmanager
        def sqlite_counted(url):
            with real_sqlite(url) as connection:
                meter.add("opened")
                connection.set_trace_callback(lambda sql, c=connection: meter.statement(sql, c))
                try:
                    yield connection
                finally:
                    connection.set_trace_callback(None)
                    meter.add("closed")

        @contextlib.contextmanager
        def postgres_counted(url, **kwargs):
            with real_postgres(url, **kwargs) as connection:
                meter.add("opened")
                execute = connection.execute

                def counted(sql, params=()):
                    meter.statement(sql)
                    return execute(sql, params)
                connection.execute = counted
                for name in ("commit", "rollback"):
                    def ended(_end=getattr(connection, name)):
                        if connection.in_transaction:
                            meter.add("transaction_ends")
                        return _end()
                    setattr(connection, name, ended)
                try:
                    yield connection
                finally:
                    meter.add("closed")

        real_check = s.DatabaseSqagStorage._check_schema

        def schema_counted(storage, *args, **kwargs):
            meter.add("schema_checks")
            return real_check(storage, *args, **kwargs)

        retrieve = backend.retrieve_artifact

        def retrieve_counted(*args, **kwargs):
            meter.add("object_reads")
            return retrieve(*args, **kwargs)

        stack.enter_context(mock.patch.object(sqlite3, "connect",
                                              lambda *args, **kwargs: real_connect(*args, **{**kwargs, "factory": MeteredSqlite})))
        stack.enter_context(mock.patch.object(s, "sqlite_storage_connection", sqlite_counted))
        stack.enter_context(mock.patch.object(s, "postgres_storage_connection", postgres_counted))
        stack.enter_context(mock.patch.object(s.DatabaseSqagStorage, "_check_schema", schema_counted))
        stack.enter_context(mock.patch.object(backend, "retrieve_artifact", retrieve_counted))

    def reset(self) -> None:
        with self.lock:
            self.counts = {"opened": 0, "closed": 0, "schema_checks": 0, "statements": 0, "transaction_ends": 0,
                           "object_reads": 0}

    def add(self, name: str) -> None:
        with self.lock:
            self.counts[name] += 1

    def transaction_end(self, connection) -> None:
        if connection.dirty:
            connection.dirty = False
            self.add("transaction_ends")

    def statement(self, sql, connection=None) -> None:
        low = " ".join(str(sql).split()).lower()
        if connection is not None and not low.startswith(("pragma", "commit", "rollback", "release")):
            connection.dirty = True
        if low.startswith(SKIPPED_STATEMENTS) or "sqlite_master" in low or "information_schema" in low or "pg_catalog." in low:
            return
        self.add("statements")

    def settled(self) -> dict[str, int]:
        """The request's scope closes after its answer is sent; wait for that, then read."""
        deadline = time.monotonic() + 10
        while True:
            with self.lock:
                counts = dict(self.counts)
            if counts["opened"] == counts["closed"] or time.monotonic() > deadline:
                return counts
            time.sleep(0.01)


class RouteBudget:
    """Each row: what the request does, its status and its exact counts."""

    database_url = None
    statements_asserted = True

    # name: (status, connections, schema checks, statements and transaction ends on SQLite, object reads)
    EXPECTED = {
        "list signed out": (401, 0, 0, 0, 0, 0),
        "profiles": (200, 1, 2, 6, 6, 0),
        # One statement and one end fewer than before #443: the list no longer reads AI spend.
        "list, 2 exported and 1 draft": (200, 1, 2, 35, 27, 2),
        "list, 3 exported and 1 draft": (200, 1, 2, 51, 39, 3),
        "reopen exported quote": (200, 1, 2, 20, 16, 1),
        "download xlsx": (200, 1, 3, 17, 10, 1),
        "download pdf": (200, 1, 3, 17, 10, 1),
        "view pdf": (200, 1, 3, 17, 10, 1),
        "download with a wrong publication binding": (404, 1, 2, 3, 3, 0),
        "save draft": (200, 2, 3, 20, 13, 1),
        "confirm on a stale draft version": (409, 1, 2, 7, 7, 0),
        "normalize line items": (200, 1, 2, 22, 22, 0),
        "start Excel and PDF job": (202, 1, 3, 36, 17, 1),
        # 114 statements, was 113: since #435 the worker reads the pricing ledger record of any named
        # quote before a file is built, so a quote the user may not edit is refused first.
        "job worker, both stages": (None, 3, 5, 114, 58, 4),
        "job poll": (200, 1, 2, 19, 15, 1),
        "list that fails after reading": (500, 1, 2, 67, 51, 4),
    }

    def setUp(self):
        self.observed: dict[str, tuple] = {}

    def call(self, fixture, method, path, body=None, *, auth=True):
        headers = {}
        if auth:
            headers["Cookie"] = f"{s.SESSION_COOKIE_NAME}={s.signed_cookie_value(fixture.auth)}"
        if body is not None:
            headers.update({"Content-Type": "application/json", "Origin": fixture.runner.base_url,
                            s.configured_csrf_header_name(): s.csrf_token_for_cookie_header(headers.get("Cookie", ""))})
        host, port = fixture.runner.server.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=60)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def measure(self, meter, name, action):
        meter.settled()
        meter.reset()
        started = time.perf_counter()
        status = action()
        wall = time.perf_counter() - started
        counts = meter.settled()
        modelled = (counts["opened"] * (CONNECT_SECONDS + STATEMENT_SECONDS) + counts["schema_checks"] * STATEMENT_SECONDS
                    + (counts["statements"] + counts["transaction_ends"]) * STATEMENT_SECONDS
                    + counts["object_reads"] * OBJECT_SECONDS)
        print(f"[request-work-budget] {self.engine_name:8} {name}: status {status} connections {counts['opened']} "
              f"left open {counts['opened'] - counts['closed']} schema checks {counts['schema_checks']} statements "
              f"{counts['statements']} transaction ends {counts['transaction_ends']} object reads {counts['object_reads']} | modelled {modelled:.2f}s wall {wall:.2f}s")
        self.observed[name] = (status, counts["opened"], counts["schema_checks"], counts["statements"],
                               counts["transaction_ends"], counts["object_reads"], counts["opened"] - counts["closed"])
        return status

    def add_quote(self, fixture, suffix, *, exported):
        fixture.session_id = "quote-budget-" + suffix
        fixture.payload = copy.deepcopy(fixture.payload)
        fixture.payload["quote_session"]["session_id"] = fixture.session_id
        fixture.storage.create_or_update_quote_session(copy.deepcopy(fixture.payload))
        if exported:
            _, result, _ = fixture.attempt("budget-" + suffix)
            self.assertEqual(result["status"], "completed", result)
        return fixture.session_id, copy.deepcopy(fixture.payload)

    def test_route_budgets(self):
        fixture = publication_fixture(self.database_url)
        if self.database_url:
            fixture.database_url = self.database_url
        with fixture.case("memory"), contextlib.ExitStack() as stack:
            first, _ = self.add_quote(fixture, "a", exported=True)
            self.add_quote(fixture, "b", exported=True)
            draft, draft_payload = self.add_quote(fixture, "c", exported=False)
            meter = Meter(fixture.backend, stack)
            get = lambda path, **kw: lambda: self.call(fixture, "GET", path, **kw)[0]

            self.measure(meter, "list signed out", get("/api/quote-sessions", auth=False))
            self.measure(meter, "profiles", get("/api/profiles"))
            self.measure(meter, "list, 2 exported and 1 draft", get("/api/quote-sessions"))
            meter.settled()
            self.add_quote(fixture, "d", exported=True)
            self.measure(meter, "list, 3 exported and 1 draft", get("/api/quote-sessions"))

            reopened = {}

            def reopen():
                status, body = self.call(fixture, "GET", f"/api/quote-sessions/{first}")
                reopened.update(json.loads(body)["quote_session"])
                return status
            self.measure(meter, "reopen exported quote", reopen)
            exports = reopened["exports"]
            self.measure(meter, "download xlsx", get(exports["xlsx"]["url"]))
            self.measure(meter, "download pdf", get(exports["pdf"]["url"]))
            self.measure(meter, "view pdf", get(exports["pdf"]["view_url"]))
            wrong = exports["xlsx"]["url"].split("?publication=")[0] + "?publication=" + "0" * 64
            self.measure(meter, "download with a wrong publication binding", get(wrong))

            status, body = self.call(fixture, "GET", f"/api/quote-sessions/{draft}")
            self.assertEqual(status, 200, body)
            version = json.loads(body)["quote_session"]["draft_version"]
            save = {**copy.deepcopy(draft_payload), "expected_draft_version": version}
            self.measure(meter, "save draft", lambda: self.call(fixture, "POST", "/api/quote-sessions", save)[0])
            status, body = self.call(fixture, "GET", f"/api/quote-sessions/{draft}")
            version = json.loads(body)["quote_session"]["draft_version"]
            stale = {**copy.deepcopy(draft_payload), "expected_draft_version": "stale-draft-version",
                     "pricing_selections": [{"source_basis_line_id": "line-0", "catalog_item_id": "item-0", "expected_revision": 0}]}
            self.measure(meter, "confirm on a stale draft version",
                         lambda: self.call(fixture, "POST", "/api/quote-sessions/pricing-selections", stale)[0])
            normalize = {**copy.deepcopy(draft_payload), "expected_draft_version": version}
            self.measure(meter, "normalize line items",
                         lambda: self.call(fixture, "POST", "/api/line-items/normalize", normalize)[0])

            job_id = "job-budget-worker"
            fixture.outputs[job_id] = (b"PK-synthetic-budget-workbook", b"%PDF-1.4\n% Synthetic budget PDF\n%%EOF\n")
            dispatched = []
            real_worker = s.run_job_worker
            with mock.patch.object(s, "run_job_worker", side_effect=lambda *args: dispatched.append(args)):
                self.measure(meter, "start Excel and PDF job", lambda: self.call(
                    fixture, "POST", "/api/jobs", {"type": "generate_pdf", "payload": copy.deepcopy(draft_payload), "job_id": job_id})[0])
                deadline = time.monotonic() + 10
                while not dispatched and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertEqual(len(dispatched), 1)
            open_at_generator = []
            generate = fixture.generator.side_effect

            def generator(command, **kwargs):
                with meter.lock:
                    open_at_generator.append(meter.counts["opened"] - meter.counts["closed"])
                return generate(command, **kwargs)
            fixture.generator.side_effect = generator
            self.measure(meter, "job worker, both stages", lambda: real_worker(*dispatched[0]))
            self.assertEqual(open_at_generator, [0], "a database connection stayed open while the generator ran")
            self.assertEqual(s.get_job(job_id, fixture.auth)["status"], "completed")
            self.measure(meter, "job poll", get(f"/api/jobs/{job_id}"))

            with mock.patch.object(s, "iso_timestamp_sort_value", side_effect=RuntimeError("synthetic failure")):
                self.measure(meter, "list that fails after reading", get("/api/quote-sessions"))

        for name, expected in self.EXPECTED.items():
            with self.subTest(route=name):
                status, connections, schema_checks, statements, transaction_ends, object_reads, left_open = self.observed[name]
                self.assertEqual(left_open, 0, "connections left open when the request ended")
                want_status, want_connections, want_schema, want_statements, want_ends, want_objects = expected
                self.assertEqual((status, connections, schema_checks, object_reads),
                                 (want_status if want_status is not None else status, want_connections, want_schema, want_objects))
                if self.statements_asserted:
                    self.assertEqual((statements, transaction_ends), (want_statements, want_ends))
        # One more exported quote adds no connection to the list.
        self.assertEqual(self.observed["list, 3 exported and 1 draft"][1], self.observed["list, 2 exported and 1 draft"][1])


class SqliteRequestWorkBudgetTest(RouteBudget, unittest.TestCase):
    engine_name = "sqlite"


@unittest.skipUnless(runtime.postgres_test_enabled(), "real disposable PostgreSQL-17 service is not configured")
class PostgresRequestWorkBudgetTest(RouteBudget, unittest.TestCase):
    engine_name = "postgres"
    statements_asserted = False

    @classmethod
    def setUpClass(cls):
        runtime.RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()

    def setUp(self):
        super().setUp()
        fixture = runtime.RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.database_url = runtime.safe_postgres_url("sqag_runtime", fixture.database_name)


if __name__ == "__main__":
    unittest.main()
