"""An exchange rate the request names but cannot use (0, negative, blank or text) is refused by every quote
route with a plain message, never saved or priced as 1 (#442 C1). A request that names no rate keeps 1.
Synthetic data, loopback only, no paid AI."""
import copy
import http.client
import json
import unittest
import urllib.parse
from unittest import mock

from tests import test_webapp as fixtures

webapp = fixtures.webapp
INVALID_RATES = (0, -1, "abc", "", None)
REFUSED = {"status": "blocked", "errors": ["Enter an exchange rate above 0 on the Customer step."], "reason_code": "exchange_rate_invalid"}


def request(runner, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    parsed = urllib.parse.urlparse(runner.base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request(method, path, body=json.dumps(body).encode("utf-8") if body is not None else None,
                           headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        text = response.read().decode("utf-8")
        cookies = [value.split(";", 1)[0] for name, value in response.getheaders() if name.lower() == "set-cookie"]
    finally:
        connection.close()
    return response.status, json.loads(text) if text else {}, cookies


def page_headers(runner) -> dict:
    """The headers the signed-in page sends: its session cookie, CSRF token and own origin."""
    status, session, cookies = request(runner, "GET", "/api/session")
    assert status == 200, session
    return {"Cookie": "; ".join(cookies), session["csrf_header"]: session["csrf_token"], "Origin": runner.base_url}


def new_quote_payload(rate) -> dict:
    payload = fixtures.valid_payload()
    payload["quote_currency"] = "USD"
    payload["quote_exchange_rate"] = rate
    return payload


def new_quote_save(rate) -> dict:
    return {
        "session_id": "quote-fx-refused",
        "customer_summary": {"customer_name": "Synthetic Customer"},
        "commercials": {"currency": "USD", "tax_label": "GST", "tax_rate": 0.09, "exchange_rate": rate},
        "status": {"quote_generated": False},
    }


def route_requests(rate) -> list[tuple[str, str, dict]]:
    return [
        *[(f"/api/jobs:{kind}", "/api/jobs", {"type": kind, "payload": new_quote_payload(rate)})
          for kind in ("draft", "basis_chat", "generate", "generate_pdf")],
        ("draft", "/api/draft", new_quote_payload(rate)),
        ("normalize", "/api/line-items/normalize", new_quote_payload(rate)),
        ("generate", "/api/generate", new_quote_payload(rate)),
        ("quote_sessions", "/api/quote-sessions", new_quote_save(rate)),
    ]


class ExchangeRateRefusalTest(unittest.TestCase):
    def setUp(self):
        # Reuse the webapp suite's synthetic profile, catalogue and transport setup.
        helper = fixtures.WebappServerTest("test_local_mutable_storage_defaults_stay_inside_repo")
        helper.setUp()
        self.addCleanup(helper.doCleanups)

    def test_every_quote_route_refuses_an_unusable_rate_before_any_work(self):
        jobs_before = copy.deepcopy(webapp.JOBS)
        with fixtures.LocalRunnerServer() as runner, mock.patch.multiple(
            webapp,
            run_job_worker=mock.DEFAULT, normalize_line_items_for_quote_basis_review=mock.DEFAULT,
            draft_quote_basis=mock.DEFAULT, quote_session_storage_for_auth_session=mock.DEFAULT,
            stage_local_quote_publication=mock.DEFAULT, begin_generation_forensics=mock.DEFAULT,
        ) as downstream:
            for work, patched in downstream.items():
                patched.side_effect = AssertionError(work + " ran")
            headers = page_headers(runner)
            for rate in INVALID_RATES:
                for label, path, body in route_requests(rate):
                    with self.subTest(route=label, rate=rate):
                        status, reply, _ = request(runner, "POST", path, body, headers)
                        self.assertEqual((status, reply), (400, REFUSED))
        self.assertEqual(webapp.JOBS, jobs_before)

    def test_a_saved_quote_with_an_unusable_rate_is_refused(self):
        for rate in INVALID_RATES:
            with self.subTest(rate=rate):
                payload = fixtures.recovered_convergence_payload(exchange_rate=1.37)
                payload["quote_session"]["draft_state"]["quoteDetails"]["exchange_rate"] = rate
                state = webapp.quote_commercial_state(payload)
                if state.get("review_required"):
                    # A saved quote whose snapshot no longer matches is already blocked for review.
                    self.assertIsNotNone(webapp.quote_commercial_review_preflight(payload))
                    continue
                self.assertTrue(state.get("owned"))
                self.assertEqual(webapp.quote_commercial_review_preflight(payload), REFUSED)

    def test_the_rate_a_job_saves_its_quote_session_with_is_checked_too(self):
        # A generation saves the quote session from `quote_session.commercials`, so a rate there is named too.
        for rate in INVALID_RATES:
            with self.subTest(rate=rate):
                payload = new_quote_payload(1.35)
                payload["quote_session"] = {"session_id": "quote-fx-nested", "commercials": {"currency": "USD", "exchange_rate": rate}}
                self.assertEqual(webapp.quote_commercial_review_preflight(payload), REFUSED)
        payload = new_quote_payload(1.35)
        payload["quote_session"] = {"session_id": "quote-fx-nested", "commercials": {"currency": "USD", "exchange_rate": 1.35}}
        self.assertIsNone(webapp.quote_commercial_review_preflight(payload))

    def test_a_usable_or_unnamed_rate_is_not_refused_and_same_currency_keeps_one(self):
        for rate in (1.35, "1.35", 1):
            with self.subTest(rate=rate):
                self.assertIsNone(webapp.quote_commercial_review_preflight(new_quote_payload(rate)))
                self.assertIsNone(webapp.quote_commercial_review_preflight(new_quote_save(rate)))
        owned = fixtures.recovered_convergence_payload(exchange_rate=1.37)
        self.assertIsNone(webapp.quote_commercial_review_preflight(owned))
        unnamed = fixtures.valid_payload()
        self.assertNotIn("quote_exchange_rate", unnamed)
        self.assertIsNone(webapp.quote_commercial_review_preflight(unnamed))
        save = new_quote_save(1)
        save["commercials"] = {"currency": "SGD"}
        self.assertIsNone(webapp.quote_commercial_review_preflight(save))
        self.assertEqual(webapp.quote_session_commercials(save, save)["exchange_rate"], 1)
        self.assertEqual(webapp.quote_exchange_rate_from_payload(unnamed), None)


if __name__ == "__main__":
    unittest.main()
