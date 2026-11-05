"""Quote-scoped spend, exercised with synthetic data and transport only."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import datetime as dt
from pathlib import Path
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tests.gateway_test_support import synthetic_configuration
from tests.test_openrouter_gateway import RecordingTransport, completed, envelope
from tests.test_webapp import QUOTE_GENERATOR_FIXTURE_ROOT, valid_payload, wait_for_job
from webapp import ai_gateway, server
from webapp.forensics import ForensicStore
from webapp.internal_password_auth import InternalAuthPolicy
from webapp.quote_ai_spend import QuoteSpendReplay, QuoteSpendUnavailable


class QuoteAiSpendTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.enterContext(patch.dict(os.environ, {"SQAG_DISABLE_DOTENV": "1"}, clear=True))
        self.enterContext(patch.object(server, "configured_data_root", return_value=self.root))
        self.enterContext(patch.object(server, "write_local_log"))
        self.enterContext(patch.dict(server.JOBS, {}, clear=True))
        self.configuration = ai_gateway.GatewayConfiguration.capture(synthetic_configuration().get)

    def database(self):
        url = f"sqlite:///{(self.root / 'storage.sqlite3').as_posix()}"
        self.enterContext(patch.dict(os.environ, {"SQAG_STORAGE_MODE": "database", "SQAG_DATABASE_URL": url}))
        server.apply_sqag_storage_migrations(url)
        return server.DatabaseSqagStorage(url, "workspace-test", "operator", "user-test")

    def save(self, store, session_id):
        payload = valid_payload()
        payload["quote_session"] = {"session_id": session_id, "ai_spend": {"total": {"display": "USD 999"}}}
        store.create_or_update_quote_session(payload)
        return payload

    def bind(self, store, payload, category="draft"):
        with patch.object(server, "quote_session_storage_for_auth_session", return_value=store):
            return server.bind_quote_spend(payload, None, category)

    def gateway(self, operation, transport):
        return ai_gateway.AiGateway(
            self.configuration, credential_reader=lambda: "synthetic", transport=transport,
            sleep=lambda _: None, quote_spend=operation,
        )

    def test_concurrent_claims_and_changed_identity_replay(self):
        store = self.database()
        first = self.bind(store, self.save(store, "quote-test-first"))
        second = self.bind(store, self.save(store, "quote-test-second"))
        key = "job-replay-test-1"

        def claim():
            try:
                return first.claim(key)
            except QuoteSpendReplay:
                return None

        with ThreadPoolExecutor(max_workers=4) as executor:
            attempts = list(executor.map(lambda _: claim(), range(8)))
        self.assertEqual(sum(attempt is not None for attempt in attempts), 1)
        self.assertIsNone(second.claim(key))
        other_type = self.bind(store, {"quote_session": {"session_id": first.session_id}}, "basis_chat")
        self.assertIsNone(other_type.claim(key))
        with self.assertRaises(QuoteSpendReplay):
            first.claim(key)

        def spend(index):
            operation = first.claim(f"job-concurrent-{index}")
            operation.settle(operation.reserve(), Decimal("0.01"))

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(spend, range(8)))
        self.assertEqual(store.get_quote_session(first.session_id)["ai_spend"]["total"]["display"], "USD 0.08")

    def test_gateway_retries_failed_provider_charge_and_excludes_pricing(self):
        store = self.database()
        payload = self.save(store, "quote-test-gateway")
        draft = self.bind(store, payload).claim("job-analysis-test")
        route = self.configuration.route("draft")
        exact = ai_gateway._load_json('{"cost": 0.014999999999999999999999999999}')["cost"]
        transport = RecordingTransport([completed(route, usage={"cost": exact}), completed(route, usage={"cost": 0})])
        subject = self.gateway(draft, transport)
        # A draft synthesis send takes the same route and reserves a distinct attempt.
        for _ in range(2):
            subject.request("draft", envelope(route))
        result = store.get_quote_session(draft.binding.session_id)["ai_spend"]
        self.assertEqual(result["draft"], {"status": "reported", "display": "USD 0.01"})

        chat = self.bind(store, payload, "basis_chat").claim("job-basis-chat-test")
        route = self.configuration.route("basis_answer")
        body = json.dumps({"model": route.model, "usage": {"cost": 0.025}})
        failed_http = urllib.error.HTTPError(
            ai_gateway.OPENROUTER_RESPONSES_URL, 503, "synthetic", {}, io.BytesIO(body.encode()),
        )
        opener = Mock()
        opener.open.side_effect = failed_http
        with self.assertRaises(ai_gateway.GatewayError) as error:
            ai_gateway.BoundedHttpsTransport(opener=opener).send(b"{}", credential="synthetic", timeout=1)
        retry = error.exception
        self.assertTrue(retry.retryable)
        timeout = ai_gateway.GatewayError("connection_failed", retryable=True)
        transport = RecordingTransport([retry, timeout, completed(route, usage={"cost": 0})])
        self.gateway(chat, transport).request("basis_answer", envelope(route))
        self.assertEqual(len(transport.calls), 3)
        result = store.get_quote_session(chat.binding.session_id)["ai_spend"]
        self.assertEqual(result["basis_chat"], {"status": "partial", "display": "USD 0.03"})
        self.assertEqual(result["total"], {"status": "partial", "display": "USD 0.04"})
        route = self.configuration.route("pricing_import")
        self.gateway(chat, RecordingTransport([completed(route, usage={"cost": 10})])).request("pricing_import", envelope(route))
        self.assertEqual(store.get_quote_session(chat.binding.session_id)["ai_spend"], result)

    def test_failed_reservation_never_calls_transport_and_lost_settlement_is_unknown(self):
        store = self.database()
        payload = self.save(store, "quote-test-failure")
        operation = self.bind(store, payload).claim("job-storage-failed")
        route = self.configuration.route("draft")
        transport = RecordingTransport([completed(route, usage={"cost": 1})])
        gateway = self.gateway(operation, transport)
        with patch.object(operation, "reserve", side_effect=QuoteSpendUnavailable):
            with self.assertRaises(QuoteSpendUnavailable):
                gateway.request("draft", envelope(route))
        self.assertEqual(transport.calls, [])
        with patch.object(operation, "settle", side_effect=QuoteSpendUnavailable):
            with self.assertRaises(QuoteSpendUnavailable):
                gateway.request("draft", envelope(route))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(store.get_quote_session(operation.binding.session_id)["ai_spend"]["total"], {
            "status": "partial", "display": None,
        })

    def test_settlement_zero_unknown_reopen_and_deletion_in_both_modes(self):
        for store in (server.LocalSqagStorage(), self.database()):
            with self.subTest(store=store.storage_backend):
                payload = self.save(store, "quote-test-lifecycle")
                binding = self.bind(store, payload)
                operation = binding.claim("job-lifecycle-test")
                zero = operation.reserve()
                operation.settle(zero, 0)
                operation.settle(zero, Decimal("20"))
                summary = store.get_quote_session(binding.session_id)["ai_spend"]
                self.assertEqual(summary["total"], {"status": "reported", "display": "USD 0.00"})
                unknown = operation.reserve()
                operation.settle(unknown, None)
                operation.settle(unknown, Decimal("20"))
                operation.settle(operation.reserve(), Decimal("0.00001"))
                self.save(store, binding.session_id)
                self.assertEqual(store.get_quote_session(binding.session_id)["ai_spend"]["total"], {
                    "status": "partial", "display": "< USD 0.01",
                })
                pending = operation.reserve()
                self.assertTrue(store.delete_quote_session(binding.session_id))
                operation.settle(pending, 20)
                with self.assertRaises(QuoteSpendUnavailable):
                    operation.reserve()
                self.assertIsNone(store.get_quote_session(binding.session_id))
                if isinstance(store, server.DatabaseSqagStorage):
                    with store.connection() as connection:
                        self.assertEqual(connection.execute("select count(*) from sqag_quote_ai_spend").fetchone()[0], 0)
                else:
                    self.assertFalse((server.quote_sessions_root() / binding.session_id).exists())

    def test_legal_hold_preserves_spend_and_telemetry_expiry_does_not_remove_it(self):
        store = self.database()
        binding = self.bind(store, self.save(store, "quote-test-held"))
        operation = binding.claim("job-legal-hold-test")
        operation.settle(operation.reserve(), 1)
        with store.connection() as connection:
            forensics = ForensicStore(connection, store.workspace_id, "synthetic-actor")
            event = forensics.append_telemetry_event(
                "retention", "completed", event_id="event-spend-hold",
                session_reference=binding.session_id,
                now=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            )
            self.assertTrue(forensics.set_legal_hold(
                "sqag_telemetry_events", "event_id", event["event_id"], True,
            ))
            self.assertFalse(store.delete_quote_session(binding.session_id))
            self.assertTrue(forensics.set_legal_hold(
                "sqag_telemetry_events", "event_id", event["event_id"], False,
            ))
            expired = forensics.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.UTC))
            self.assertEqual(expired.telemetry_deleted, 1)
        self.assertEqual(store.get_quote_session(binding.session_id)["ai_spend"]["total"], {
            "status": "reported", "display": "USD 1.00",
        })

    def test_upgrade_and_old_writer_preserve_amount_but_remove_coverage(self):
        url = f"sqlite:///{(self.root / 'legacy.sqlite3').as_posix()}"
        with server.sqlite_storage_connection(url) as connection:
            migration = (server.PROJECT_ROOT / "migrations/001_platform_scoped_storage.sql").read_text(encoding="utf-8")
            connection.executescript(migration)
            metadata = server.blank_quote_session_metadata("quote-test-legacy", server.utc_timestamp())
            metadata["owner"] = {"user_id": "user-test"}
            connection.execute(
                "insert into sqag_quote_sessions values (?, ?, ?, ?, ?, ?)",
                ("workspace-test", metadata["session_id"], json.dumps(metadata), "[]",
                 metadata["created_at"], metadata["updated_at"]),
            )
            connection.commit()
        server.apply_sqag_storage_migrations(url)
        server.apply_sqag_storage_migrations(url)
        store = server.DatabaseSqagStorage(url, "workspace-test", "operator", "user-test")
        session_id = metadata["session_id"]
        self.assertEqual(store.get_quote_session(session_id)["ai_spend"]["total"]["status"], "unavailable")
        operation = self.bind(store, {"quote_session": {"session_id": session_id}}).claim("job-legacy-test")
        operation.settle(operation.reserve(), 1)
        self.assertEqual(store.get_quote_session(session_id)["ai_spend"]["total"], {
            "status": "partial", "display": "USD 1.00",
        })
        fresh = self.save(store, "quote-test-rollback")
        operation = self.bind(store, fresh).claim("job-rollback-test")
        operation.settle(operation.reserve(), 1)
        with store.connection() as connection:
            metadata, _ = store._read_quote_session_metadata_for_workspace_on_connection(connection, operation.binding.session_id)
            metadata.pop("ai_spend_coverage")
            connection.execute(
                "update sqag_quote_sessions set metadata_json = ? where workspace_id = ? and session_id = ?",
                (json.dumps(metadata), store.workspace_id, operation.binding.session_id),
            )
            connection.commit()
        self.assertEqual(store.get_quote_session(operation.binding.session_id)["ai_spend"]["total"], {
            "status": "partial", "display": "USD 1.00",
        })

    def test_summary_only_admin_and_workspace_authority(self):
        store = self.database()
        payload = self.save(store, "quote-test-private")
        binding = self.bind(store, payload)
        operation = binding.claim("job-private-test")
        operation.settle(operation.reserve(), Decimal("0.25"))
        admin = server.DatabaseSqagStorage(store.database_url, store.workspace_id, "admin", "admin-test")
        peer = server.DatabaseSqagStorage(store.database_url, store.workspace_id, "operator", "peer-test")
        other = server.DatabaseSqagStorage(store.database_url, "other-workspace", "admin", "admin-test")
        policy = InternalAuthPolicy(store.workspace_id, (), "synthetic", {})
        self.assertEqual(admin.list_quote_sessions(staff_policy=policy)[0]["ai_spend"], {
            "total": {"status": "reported", "display": "USD 0.25"},
        })
        for requester in (admin, peer, other):
            with self.subTest(role=requester.role, workspace=requester.workspace_id):
                self.assertIsNone(requester.get_quote_session(binding.session_id))
                with self.assertRaises(server.SqagStorageAccessError):
                    self.bind(requester, payload)
        self.assertEqual(other.list_quote_sessions(staff_policy=policy), [])

    def test_job_cache_replay_and_payload_cannot_supply_spend_context(self):
        store = server.LocalSqagStorage()
        payload = self.save(store, "quote-test-job")
        different = self.save(store, "quote-test-other")
        key = "job-spend-contract"
        route = self.configuration.route("draft")
        transport = RecordingTransport([completed(route, usage={"cost": 1})])

        def draft(_payload, **_kwargs):
            operation = server.QUOTE_SPEND_CONTEXT.get()
            self.assertEqual(operation.binding.session_id, "quote-test-job")
            self.gateway(operation, transport).request("draft", envelope(route))
            return {"status": "drafted", "source": "openai", "quote_basis": {}, "line_items": []}

        payload["quote_spend"] = {"session_id": "quote-test-other"}
        with (
            patch.object(server, "draft_quote_basis", side_effect=draft),
            patch.object(server, "pricing_references_root", return_value=QUOTE_GENERATOR_FIXTURE_ROOT / "pricing-references"),
        ):
            started = server.create_job("draft", payload, requested_job_id=key)
            self.assertEqual(started.get("job_id"), key, started)
            self.assertEqual(wait_for_job(key)["status"], "completed")
            self.assertEqual(server.create_job("draft", payload, requested_job_id=key)["job_id"], key)
            self.assertEqual(server.create_job("draft", different, requested_job_id=key)["status"], "blocked")
            with server.JOBS_LOCK:
                server.JOBS.pop(key)
            replay = server.create_job("draft", payload, requested_job_id=key)
            self.assertTrue(replay["idempotent_replay"])
            unbound = {name: value for name, value in payload.items() if name != "quote_session"}
            with self.assertRaises(server.SqagStorageAccessError):
                server.create_job("draft", unbound, requested_job_id=key)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(store.get_quote_session("quote-test-job")["ai_spend"]["total"]["display"], "USD 1.00")
        self.assertEqual(store.get_quote_session("quote-test-other")["ai_spend"]["total"]["status"], "none")
