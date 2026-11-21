"""Production database guard tests: offline, with a fake Neon client and temp maps.

No network. The Neon API is faked; the NeonClient tests patch the HTTP opener, so nothing
leaves the process. Hosts, regions and ids are fakes: the production endpoint is a fake one
recorded in each test's map, so the rules run against a fixture and live's real names stay
only in deploy/rehearsal/resources.json. Credentials, query values and the key are synthetic.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import http.client
import io
import json
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import neon_branch_guard as guard  # noqa: E402
from tests.neon_guard_fakes import (  # noqa: E402
    COPY_BRANCH,
    PROJECT_ID,
    PROJECT_NAME,
    REHEARSAL_BRANCH,
    FakeNeon,
    api_error,
    list_refused,
)

REGION = "c-0.fake-region-1.aws.neon.tech"
PRODUCTION_ENDPOINT = "ep-fake-production-000001"
PRODUCTION_BRANCH = "br-fake-production-000001"
PRODUCTION_HOST = f"{PRODUCTION_ENDPOINT}.{REGION}"
PRODUCTION_POOLER_HOST = f"{PRODUCTION_ENDPOINT}-pooler.{REGION}"
REHEARSAL_ENDPOINT = "ep-fake-rehearsal-000001"
REHEARSAL_HOST = f"{REHEARSAL_ENDPOINT}.{REGION}"
REHEARSAL_POOLER_HOST = f"{REHEARSAL_ENDPOINT}-pooler.{REGION}"
OTHER_PROJECT_ID = "fake-project-000002"
USER = "fake-user-planted"
PASSWORD = "fake-password-planted"
QUERY_VALUE = "fake-query-planted"
KEY = "fake-key-planted"
PROVIDER_TEXT = "planted-provider-text"
PLANTED = (USER, PASSWORD, QUERY_VALUE, "sslmode", KEY)
RESULT_KEYS = {
    "schema",
    "status",
    "host",
    "endpoint_id",
    "branch_id",
    "branch_name",
    "branch_default",
    "neon_api",
    "checked_at",
    "reason",
}
UNKNOWN = guard.UNKNOWN_HOST
NO_PRODUCTION = "no production endpoint recorded"
NEON_BASE = "https://console.neon.tech/api/v2"


def url(host: str, *, query: str = f"sslmode=require&application_name={QUERY_VALUE}") -> str:
    return f"postgresql://{USER}:{PASSWORD}@{host}/neondb?{query}"


def guard_map(**changes: Any) -> dict[str, Any]:
    section: dict[str, Any] = {
        "neon_project": PROJECT_NAME,
        "neon_project_id": PROJECT_ID,
        "branch_prefix": "rehearsal-",
        "forbidden_endpoints": [
            {"name": "live default branch compute", "endpoint_id": PRODUCTION_ENDPOINT, "branch_id": PRODUCTION_BRANCH}
        ],
        "source": "test fixture",
    }
    section.update(changes)
    return {"production_guard": section}


class GuardTestCase(unittest.TestCase):
    def passed(self, database_url: str, rehearsal_map: dict | None = None, neon: Any = None) -> tuple[dict, str]:
        """(result, stderr text) for a target that must pass."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = guard.check_database_target(
                database_url, guard_map() if rehearsal_map is None else rehearsal_map, neon
            )
        return result, err.getvalue()

    def refused(self, database_url: str, rehearsal_map: dict | None = None, neon: Any = None) -> tuple[Exception, str]:
        """(GuardRefusal, stderr text) for a target that must be refused."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(guard.GuardRefusal) as caught:
            guard.check_database_target(database_url, guard_map() if rehearsal_map is None else rehearsal_map, neon)
        return caught.exception, err.getvalue()

    def assert_no_planted(self, *texts: str) -> None:
        for text in texts:
            for planted in PLANTED:
                self.assertNotIn(planted, text)


class GuardTests(GuardTestCase):
    def test_refused_targets(self) -> None:
        cases = [
            ("production endpoint", url(PRODUCTION_HOST), None, "recorded production endpoint", None),
            ("production pooler form", url(PRODUCTION_POOLER_HOST), None, "recorded production endpoint", None),
            ("production host in upper case", url(PRODUCTION_HOST.upper()), None, "recorded production endpoint", None),
            (
                "production endpoint with a port",
                f"postgresql://{USER}:{PASSWORD}@{PRODUCTION_HOST}:5432/neondb",
                None,
                "recorded production endpoint",
                None,
            ),
            ("not a Neon host", url("db.example.test"), None, UNKNOWN, None),
            ("Neon name only as a prefix", url(f"{REHEARSAL_ENDPOINT}.c-3.neon.tech.example.test"), None, UNKNOWN, None),
            ("label before the endpoint", url(f"evil.{REHEARSAL_HOST}"), None, UNKNOWN, None),
            ("empty endpoint label", url(".c-3.neon.tech"), None, UNKNOWN, None),
            ("endpoint label with no name part", url("ep-.c-3.neon.tech"), None, UNKNOWN, None),
            (
                "second host after a port",
                f"postgresql://{USER}:{PASSWORD}@{REHEARSAL_HOST}:5432,{PRODUCTION_HOST}:5432/neondb",
                None,
                UNKNOWN,
                None,
            ),
            (
                "second host after the name",
                f"postgresql://{USER}:{PASSWORD}@{REHEARSAL_HOST},{PRODUCTION_HOST}/neondb",
                None,
                UNKNOWN,
                None,
            ),
            ("host override in the query", url(REHEARSAL_HOST, query=f"host={PRODUCTION_HOST}"), None, "host override", None),
            ("hostaddr override in the query", url(REHEARSAL_HOST, query="hostaddr=203.0.113.7"), None, "host override", None),
            ("service override in the query", url(REHEARSAL_HOST, query="service=fake-service-planted"), None, "host override", None),
            # Neon reads the endpoint from options when SNI is off, so either key can name another endpoint.
            (
                "endpoint option in the query",
                url(REHEARSAL_HOST, query=f"sslmode=require&options=endpoint%3D{PRODUCTION_ENDPOINT}"),
                None,
                "host override",
                None,
            ),
            ("SNI turned off in the query", url(REHEARSAL_HOST, query="sslmode=require&sslsni=0"), None, "host override", None),
            ("second at sign", f"postgresql://{USER}:{PASSWORD}@raw@{REHEARSAL_HOST}/neondb", None, UNKNOWN, None),
            ("no host", "postgresql:///neondb", None, UNKNOWN, None),
            ("keyword form", f"host={PRODUCTION_HOST} dbname=neondb", None, UNKNOWN, None),
            ("bracketed address", f"postgresql://{USER}:{PASSWORD}@[::1]/neondb", None, UNKNOWN, None),
            ("unparseable URL", "postgresql://[bad/neondb", None, UNKNOWN, None),
            ("user and password as the host", f"postgresql://{USER}:{PASSWORD}/neondb", None, UNKNOWN, None),
            ("empty production list", url(REHEARSAL_HOST), guard_map(forbidden_endpoints=[]), NO_PRODUCTION, None),
            ("no production section", url(REHEARSAL_HOST), {"storage": {}}, NO_PRODUCTION, None),
            (
                "production record without a branch",
                url(REHEARSAL_HOST),
                guard_map(forbidden_endpoints=[{"name": "x", "endpoint_id": PRODUCTION_ENDPOINT}]),
                "production endpoint record is malformed",
                None,
            ),
            (
                "section without the project name",
                url(REHEARSAL_HOST),
                guard_map(neon_project=""),
                "needs neon_project and branch_prefix",
                None,
            ),
        ]
        for label, database_url, rehearsal_map, reason, neon in cases:
            with self.subTest(label):
                caught, err = self.refused(database_url, rehearsal_map, neon)
                self.assertIn(reason, str(caught))
                self.assertEqual(caught.result["status"], "refused")
                self.assertEqual(caught.result["reason"], str(caught))
                self.assertTrue(err.startswith("target host "), err)
                self.assertTrue(err.endswith("\n"), err)
                self.assert_no_planted(json.dumps(caught.result), err)

    def test_rehearsal_targets_pass_without_the_neon_key(self) -> None:
        for label, host in (("direct", REHEARSAL_HOST), ("pooler", REHEARSAL_POOLER_HOST)):
            with self.subTest(label):
                result, err = self.passed(url(host))
                self.assertEqual(set(result), RESULT_KEYS)
                self.assertEqual(
                    (result["status"], result["neon_api"], result["branch_id"], result["branch_name"], result["branch_default"], result["reason"]),
                    ("pass", "not_checked", None, None, None, None),
                )
                self.assertEqual((result["host"], result["endpoint_id"]), (host, REHEARSAL_ENDPOINT))
                self.assertEqual(err, f"target host {host} branch not checked\n")
                dt.datetime.strptime(result["checked_at"], "%Y-%m-%dT%H:%M:%SZ")
                self.assert_no_planted(json.dumps(result), err)

    def test_rehearsal_branch_passes_when_the_neon_api_confirms_it(self) -> None:
        neon = FakeNeon()
        result, err = self.passed(url(REHEARSAL_HOST), neon=neon)
        self.assertEqual(set(result), RESULT_KEYS)
        # The branch id is recorded for the role password reset, which Neon names by branch.
        self.assertEqual(
            (result["status"], result["neon_api"], result["branch_id"], result["branch_name"], result["branch_default"], result["reason"]),
            ("pass", "checked", REHEARSAL_BRANCH, COPY_BRANCH, False, None),
        )
        self.assertEqual((result["host"], result["endpoint_id"]), (REHEARSAL_HOST, REHEARSAL_ENDPOINT))
        self.assertEqual(neon.calls, ["projects", "endpoint", "branch"])
        self.assertEqual(err, f"target host {REHEARSAL_HOST} branch {COPY_BRANCH}\n")
        self.assert_no_planted(json.dumps(result), err)

    def test_production_endpoint_is_refused_before_any_neon_call(self) -> None:
        neon = FakeNeon()
        caught, _ = self.refused(url(PRODUCTION_POOLER_HOST), neon=neon)
        self.assertEqual(neon.calls, [])
        self.assertEqual(str(caught), "target is a recorded production endpoint")

    def test_neon_answers_that_are_not_a_rehearsal_branch_are_refused(self) -> None:
        cases = [
            ("branch is the default", FakeNeon(name="main", default=True), "Neon's default (production) branch", "checked"),
            ("branch named main", FakeNeon(name="main"), "rehearsal prefix", "checked"),
            (
                "branch id is a recorded production branch",
                FakeNeon(branch_id=PRODUCTION_BRANCH),
                "recorded production branch",
                "checked",
            ),
            (
                "project name matches two projects",
                FakeNeon(project_ids=[PROJECT_ID, OTHER_PROJECT_ID]),
                "exactly one project",
                "not_checked",
            ),
            ("project name matches none", FakeNeon(project_ids=[]), "exactly one project", "not_checked"),
        ]
        for label, neon, reason, neon_api in cases:
            with self.subTest(label):
                caught, err = self.refused(url(REHEARSAL_HOST), neon=neon)
                self.assertIn(reason, str(caught))
                self.assertEqual(caught.result["neon_api"], neon_api)
                self.assertEqual(err.count("\n"), 1)
                self.assert_no_planted(json.dumps(caught.result), err)

    def test_neon_call_failures_refuse_with_the_fixed_reason(self) -> None:
        failures = [
            ("runtime error", RuntimeError(PROVIDER_TEXT)),
            ("timeout", socket.timeout(PROVIDER_TEXT)),
            ("api error", guard.NeonApiError(PROVIDER_TEXT)),
        ]
        for label, error in failures:
            with self.subTest(label):
                caught, err = self.refused(url(REHEARSAL_HOST), neon=FakeNeon(error=error))
                self.assertEqual(str(caught), guard.NEON_UNREACHABLE)
                self.assertEqual(caught.result["neon_api"], "not_checked")
                self.assertNotIn(PROVIDER_TEXT, json.dumps(caught.result) + err)
                self.assertEqual(err, f"target host {REHEARSAL_HOST} branch not checked\n")

    def test_real_client_failure_refuses_through_the_guard(self) -> None:
        with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=socket.timeout(PROVIDER_TEXT)):
            caught, err = self.refused(url(REHEARSAL_HOST), neon=guard.NeonClient(KEY))
        self.assertEqual(str(caught), guard.NEON_UNREACHABLE)
        self.assertNotIn(PROVIDER_TEXT, json.dumps(caught.result) + err)
        self.assert_no_planted(json.dumps(caught.result), err)


class ProjectScopedKeyTests(GuardTestCase):
    """A key scoped to one project cannot list projects (Neon answers 404), so the guard
    confirms the project by the id the map records, and still requires the map's name."""

    def test_listing_refused_then_the_recorded_id_names_the_project_and_the_branch_passes(self) -> None:
        neon = FakeNeon(list_error=list_refused())
        result, err = self.passed(url(REHEARSAL_HOST), neon=neon)
        self.assertEqual(
            (result["status"], result["neon_api"], result["branch_id"], result["branch_name"], result["branch_default"]),
            ("pass", "checked", REHEARSAL_BRANCH, COPY_BRANCH, False),
        )
        self.assertEqual(neon.calls, ["projects", "project", "endpoint", "branch"])
        self.assertEqual(neon.project_ids_used, [PROJECT_ID, PROJECT_ID, PROJECT_ID])
        self.assertEqual(err, f"target host {REHEARSAL_HOST} branch {COPY_BRANCH}\n")

    def test_the_by_id_path_keeps_every_branch_rule(self) -> None:
        cases = [
            ("branch is the default", dict(name="main", default=True), "Neon's default (production) branch"),
            ("branch id is a recorded production branch", dict(branch_id=PRODUCTION_BRANCH), "recorded production branch"),
            ("branch without the rehearsal prefix", dict(name="main"), "rehearsal prefix"),
        ]
        for label, answers, reason in cases:
            with self.subTest(label):
                caught, _ = self.refused(url(REHEARSAL_HOST), neon=FakeNeon(list_error=list_refused(), **answers))
                self.assertIn(reason, str(caught))
                self.assertEqual(caught.result["neon_api"], "checked")

    def test_a_recorded_id_that_names_another_project_is_refused(self) -> None:
        neon = FakeNeon(list_error=list_refused(), project_name=PROJECT_NAME + "-other")
        caught, err = self.refused(url(REHEARSAL_HOST), neon=neon)
        self.assertEqual(str(caught), "the map's neon_project_id does not name the map's Neon project")
        self.assertEqual(caught.result["neon_api"], "not_checked")
        self.assertEqual(neon.calls, ["projects", "project"])
        self.assertEqual(err, f"target host {REHEARSAL_HOST} branch not checked\n")

    def test_listing_refused_and_no_recorded_id_is_refused(self) -> None:
        for label, project_id in (("missing", None), ("blank", "  ")):
            with self.subTest(label):
                rehearsal_map = guard_map(neon_project_id=project_id)
                if project_id is None:
                    del rehearsal_map["production_guard"]["neon_project_id"]
                neon = FakeNeon(list_error=list_refused())
                caught, _ = self.refused(url(REHEARSAL_HOST), rehearsal_map, neon)
                self.assertEqual(str(caught), "Neon refused to list projects and the map records no neon_project_id")
                self.assertEqual(neon.calls, ["projects"])

    def test_other_listing_errors_still_refuse_without_the_by_id_path(self) -> None:
        for label, error in (
            ("forbidden", api_error(403)),
            ("unauthorised", api_error(401)),
            ("server error", api_error(500)),
            ("no status", api_error(None)),
            ("timeout", socket.timeout(PROVIDER_TEXT)),
        ):
            with self.subTest(label):
                neon = FakeNeon(list_error=error)
                caught, err = self.refused(url(REHEARSAL_HOST), neon=neon)
                self.assertEqual(str(caught), guard.NEON_UNREACHABLE)
                self.assertEqual(neon.calls, ["projects"])
                self.assertNotIn(PROVIDER_TEXT, json.dumps(caught.result) + err)

    def test_a_failed_by_id_lookup_refuses_with_the_fixed_reason(self) -> None:
        neon = FakeNeon(list_error=list_refused())
        neon.project_name = mock.Mock(side_effect=api_error(404))
        caught, _ = self.refused(url(REHEARSAL_HOST), neon=neon)
        self.assertEqual(str(caught), guard.NEON_UNREACHABLE)
        self.assertEqual(caught.result["neon_api"], "not_checked")


class ProductionEndpointPrefixTests(GuardTestCase):
    """Neon also serves a compute under per-compute hosts: its endpoint id, a binding suffix
    and an optional -pooler. Each one is live's compute, so each is refused, key or not."""

    def test_per_compute_hosts_of_the_production_endpoint_are_refused_without_a_key(self) -> None:
        for label, host in (
            ("binding host", f"{PRODUCTION_ENDPOINT}-gvz.{REGION}"),
            ("binding pooler host", f"{PRODUCTION_ENDPOINT}-gvz-pooler.{REGION}"),
            ("longer binding", f"{PRODUCTION_ENDPOINT}-a1-b2.{REGION}"),
            ("binding host in upper case", f"{PRODUCTION_ENDPOINT}-gvz.{REGION}".upper()),
        ):
            with self.subTest(label):
                caught, err = self.refused(url(host))
                self.assertEqual(str(caught), "target is a recorded production endpoint")
                self.assertTrue(err.startswith(f"target host {host.lower()} "), err)
                self.assert_no_planted(json.dumps(caught.result), err)

    def test_per_compute_hosts_are_refused_before_any_neon_call(self) -> None:
        neon = FakeNeon()
        caught, _ = self.refused(url(f"{PRODUCTION_ENDPOINT}-gvz-pooler.{REGION}"), neon=neon)
        self.assertEqual(str(caught), "target is a recorded production endpoint")
        self.assertEqual(neon.calls, [])

    def test_an_endpoint_that_only_shares_leading_characters_is_not_live(self) -> None:
        # No dash after the recorded id: a different endpoint, so the endpoint rule lets it through.
        result, _ = self.passed(url(f"{PRODUCTION_ENDPOINT}x.{REGION}"))
        self.assertEqual((result["status"], result["endpoint_id"]), ("pass", PRODUCTION_ENDPOINT + "x"))


class NeonCheckedRequirementTests(unittest.TestCase):
    """Steps that write to a copy accept only a guard result that Neon's API confirmed."""

    def test_only_a_checked_result_is_accepted(self) -> None:
        guard.require_neon_checked({"status": "pass", "neon_api": "checked"})
        for label, result in (
            ("not checked", {"status": "pass", "neon_api": "not_checked"}),
            ("missing", {"status": "pass"}),
            ("not a record", None),
        ):
            with self.subTest(label), self.assertRaises(guard.GuardRefusal) as caught:
                guard.require_neon_checked(result)
            self.assertIn(guard.NEON_KEY_ENV, str(caught.exception))


class NeonClientTests(GuardTestCase):
    @staticmethod
    def fake_response(body: bytes) -> mock.MagicMock:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = body
        return response

    def test_key_only_in_the_authorization_header_with_a_20_second_timeout(self) -> None:
        body = json.dumps(
            {
                "projects": [
                    {"id": PROJECT_ID, "name": PROJECT_NAME},
                    {"id": OTHER_PROJECT_ID, "name": PROJECT_NAME + "-live"},
                ]
            }
        ).encode()
        with mock.patch.object(
            urllib.request.OpenerDirector, "open", return_value=self.fake_response(body)
        ) as opened:
            ids = guard.NeonClient(KEY).project_ids_named(PROJECT_NAME)
        self.assertEqual(ids, [PROJECT_ID])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, f"{NEON_BASE}/projects?search={PROJECT_NAME}")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
        self.assertNotIn(KEY, request.full_url)
        self.assertEqual(opened.call_args.kwargs["timeout"], 20)

    def test_endpoint_and_branch_answers_are_read_from_their_fields(self) -> None:
        client = guard.NeonClient(KEY)
        with mock.patch.object(
            urllib.request.OpenerDirector,
            "open",
            return_value=self.fake_response(b'{"endpoint": {"branch_id": "' + REHEARSAL_BRANCH.encode() + b'"}}'),
        ) as opened:
            self.assertEqual(client.endpoint_branch_id(PROJECT_ID, REHEARSAL_ENDPOINT), REHEARSAL_BRANCH)
        self.assertEqual(
            opened.call_args.args[0].full_url, f"{NEON_BASE}/projects/{PROJECT_ID}/endpoints/{REHEARSAL_ENDPOINT}"
        )
        branch_body = json.dumps({"branch": {"name": COPY_BRANCH, "default": False}}).encode()
        with mock.patch.object(
            urllib.request.OpenerDirector, "open", return_value=self.fake_response(branch_body)
        ) as opened:
            self.assertEqual(client.branch(PROJECT_ID, REHEARSAL_BRANCH), (COPY_BRANCH, False))
        self.assertEqual(
            opened.call_args.args[0].full_url, f"{NEON_BASE}/projects/{PROJECT_ID}/branches/{REHEARSAL_BRANCH}"
        )

    def test_transport_and_shape_failures_become_one_fixed_error(self) -> None:
        cases = [
            ("timeout", {"side_effect": socket.timeout(PROVIDER_TEXT)}, "Neon API call failed"),
            ("http error", {"side_effect": OSError(PROVIDER_TEXT)}, "Neon API call failed"),
            ("http client error", {"side_effect": http.client.HTTPException(PROVIDER_TEXT)}, "Neon API call failed"),
            ("not JSON", {"return_value": self.fake_response(b"<html>" + PROVIDER_TEXT.encode())}, "Neon API call failed"),
            (
                "unexpected shape",
                {"return_value": self.fake_response(b'{"projects": {}}')},
                "unexpected Neon API response",
            ),
        ]
        for label, patch_kwargs, message in cases:
            with self.subTest(label), mock.patch.object(urllib.request.OpenerDirector, "open", **patch_kwargs):
                with self.assertRaises(guard.NeonApiError) as caught:
                    guard.NeonClient(KEY).project_ids_named(PROJECT_NAME)
                self.assertEqual(str(caught.exception), message)
                self.assertNotIn(PROVIDER_TEXT, str(caught.exception))

    def test_branch_with_a_non_boolean_default_flag_is_an_error(self) -> None:
        body = json.dumps({"branch": {"name": COPY_BRANCH, "default": "no"}}).encode()
        with mock.patch.object(urllib.request.OpenerDirector, "open", return_value=self.fake_response(body)):
            with self.assertRaises(guard.NeonApiError):
                guard.NeonClient(KEY).branch(PROJECT_ID, REHEARSAL_BRANCH)

    def test_project_name_is_read_by_id(self) -> None:
        body = json.dumps({"project": {"id": PROJECT_ID, "name": PROJECT_NAME}}).encode()
        with mock.patch.object(
            urllib.request.OpenerDirector, "open", return_value=self.fake_response(body)
        ) as opened:
            self.assertEqual(guard.NeonClient(KEY).project_name(PROJECT_ID), PROJECT_NAME)
        self.assertEqual(opened.call_args.args[0].full_url, f"{NEON_BASE}/projects/{PROJECT_ID}")
        with mock.patch.object(
            urllib.request.OpenerDirector, "open", return_value=self.fake_response(b'{"project": {"name": ""}}')
        ):
            with self.assertRaises(guard.NeonApiError):
                guard.NeonClient(KEY).project_name(PROJECT_ID)

    def test_an_http_error_answer_keeps_its_status_and_drops_its_text(self) -> None:
        # The project-scoped key's answer to a project listing: HTTP 404 with a provider message.
        error = urllib.error.HTTPError(
            f"{NEON_BASE}/projects", 404, PROVIDER_TEXT, {}, io.BytesIO(PROVIDER_TEXT.encode())
        )
        with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=error):
            with self.assertRaises(guard.NeonApiError) as caught:
                guard.NeonClient(KEY).project_ids_named(PROJECT_NAME)
        self.assertEqual((str(caught.exception), caught.exception.status), ("Neon API call failed", 404))
        self.assertNotIn(PROVIDER_TEXT, str(caught.exception))
        with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=socket.timeout(PROVIDER_TEXT)):
            with self.assertRaises(guard.NeonApiError) as caught:
                guard.NeonClient(KEY).project_ids_named(PROJECT_NAME)
        self.assertIsNone(caught.exception.status)

    def test_a_project_scoped_key_passes_through_the_real_client(self) -> None:
        answers = {
            f"{NEON_BASE}/projects/{PROJECT_ID}": {"project": {"id": PROJECT_ID, "name": PROJECT_NAME}},
            f"{NEON_BASE}/projects/{PROJECT_ID}/endpoints/{REHEARSAL_ENDPOINT}": {"endpoint": {"branch_id": REHEARSAL_BRANCH}},
            f"{NEON_BASE}/projects/{PROJECT_ID}/branches/{REHEARSAL_BRANCH}": {"branch": {"name": COPY_BRANCH, "default": False}},
        }
        seen: list[str] = []

        def answer(request: Any, timeout: float) -> Any:
            seen.append(request.full_url)
            if request.full_url not in answers:
                raise urllib.error.HTTPError(request.full_url, 404, PROVIDER_TEXT, {}, io.BytesIO(PROVIDER_TEXT.encode()))
            return self.fake_response(json.dumps(answers[request.full_url]).encode())

        with mock.patch.object(urllib.request.OpenerDirector, "open", autospec=True, side_effect=lambda _self, request, timeout: answer(request, timeout)):
            result, err = self.passed(url(REHEARSAL_HOST), neon=guard.NeonClient(KEY))
        self.assertEqual((result["status"], result["neon_api"], result["branch_id"]), ("pass", "checked", REHEARSAL_BRANCH))
        self.assertEqual(seen[0], f"{NEON_BASE}/projects?search={PROJECT_NAME}")
        self.assertEqual(seen[1:], list(answers))
        self.assert_no_planted(json.dumps(result), err, PROVIDER_TEXT)

    def test_client_is_built_only_when_the_store_key_name_is_set(self) -> None:
        self.assertEqual(guard.NEON_KEY_ENV, "NEONDB_SWOOSHZ_SQAG_API_KEY")
        self.assertIsNone(guard.neon_client_from_env({}))
        # The old name is not read: the key is passed under its credential-store name only.
        self.assertIsNone(guard.neon_client_from_env({"NEON_API_KEY": KEY}))
        self.assertIsInstance(guard.neon_client_from_env({"NEONDB_SWOOSHZ_SQAG_API_KEY": KEY}), guard.NeonClient)
        # A blank key still counts as set, so the API check runs and fails closed.
        self.assertIsInstance(guard.neon_client_from_env({"NEONDB_SWOOSHZ_SQAG_API_KEY": ""}), guard.NeonClient)


class CliTests(GuardTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.map_path = self.dir / "map.json"
        self.map_path.write_text(json.dumps(guard_map()), encoding="utf-8")
        self.result = self.dir / "result.json"

    def run_cli(
        self,
        *,
        url_value: str | None = None,
        name: str = "FAKE_DB_URL",
        extra_env: dict[str, str] | None = None,
        map_path: Path | None = None,
    ) -> tuple[int, str, str]:
        env = dict(extra_env or {})
        if url_value is not None:
            env[name] = url_value
        argv = [
            "--database-url-env", name, "--result", str(self.result), "--map", str(map_path or self.map_path)
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = guard.main(argv, env=env)
        return code, out.getvalue(), err.getvalue()

    def read_result(self) -> tuple[dict, str]:
        text = self.result.read_text(encoding="utf-8")
        return json.loads(text), text

    def test_rehearsal_target_exits_zero_and_writes_a_pass_result(self) -> None:
        code, out, err = self.run_cli(url_value=url(REHEARSAL_HOST))
        result, _ = self.read_result()
        self.assertEqual(code, 0)
        self.assertEqual((result["status"], result["neon_api"], result["reason"]), ("pass", "not_checked", None))
        self.assertEqual(err, f"target host {REHEARSAL_HOST} branch not checked\n")
        self.assertEqual(out, "")

    def test_production_target_exits_two_and_writes_a_refused_result(self) -> None:
        code, out, err = self.run_cli(url_value=url(PRODUCTION_HOST))
        result, _ = self.read_result()
        self.assertEqual(code, 2)
        self.assertEqual((result["status"], result["reason"]), ("refused", "target is a recorded production endpoint"))
        self.assertEqual(result["endpoint_id"], PRODUCTION_ENDPOINT)

    def test_result_and_output_hold_no_url_parts(self) -> None:
        texts = []
        for value in (url(REHEARSAL_HOST), url(PRODUCTION_HOST), url("db.example.test")):
            _, out, err = self.run_cli(url_value=value)
            _, text = self.read_result()
            texts += [text, out, err]
        self.assert_no_planted(*texts)

    def test_unset_or_blank_variable_exits_two(self) -> None:
        for label, value in (("unset", None), ("blank", "   ")):
            with self.subTest(label):
                code, out, err = self.run_cli(url_value=value)
                result, _ = self.read_result()
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "refused")
                self.assertIn("unset or empty", result["reason"])

    def test_a_url_is_not_accepted_as_the_variable_name(self) -> None:
        code, out, err = self.run_cli(name=url(REHEARSAL_HOST))
        result, text = self.read_result()
        self.assertEqual(code, 2)
        self.assertIn("must name an environment variable", result["reason"])
        self.assert_no_planted(text, out, err)

    def test_unreadable_or_non_object_map_exits_two(self) -> None:
        bad_map = self.dir / "not-an-object.json"
        bad_map.write_text("[]", encoding="utf-8")
        for label, path, reason in (
            ("missing", self.dir / "missing.json", "rehearsal map is unreadable"),
            ("not an object", bad_map, "rehearsal map must be a JSON object"),
        ):
            with self.subTest(label):
                code, _, _ = self.run_cli(url_value=url(REHEARSAL_HOST), map_path=path)
                result, _ = self.read_result()
                self.assertEqual((code, result["status"]), (2, "refused"))
                self.assertEqual(result["reason"], reason)

    def test_unexpected_error_exits_one_with_the_type_name_only(self) -> None:
        with mock.patch.object(guard, "check_database_target", side_effect=RuntimeError("planted-leak-detail")):
            code, out, err = self.run_cli(url_value=url(REHEARSAL_HOST))
        result, text = self.read_result()
        self.assertEqual(code, 1)
        self.assertEqual((result["status"], result["reason"]), ("refused", "guard error: RuntimeError"))
        self.assertNotIn("planted-leak-detail", text + err + out)

    def test_unwritable_result_file_exits_one(self) -> None:
        self.result = self.dir / "no-such-folder" / "result.json"
        code, out, err = self.run_cli(url_value=url(REHEARSAL_HOST))
        self.assertEqual(code, 1)
        self.assertIn("result file could not be written", err)

    def test_key_set_routes_the_check_through_neon_and_fails_closed(self) -> None:
        with mock.patch.object(
            guard.NeonClient, "project_ids_named", side_effect=OSError(PROVIDER_TEXT)
        ) as call:
            code, out, err = self.run_cli(url_value=url(REHEARSAL_HOST), extra_env={"NEONDB_SWOOSHZ_SQAG_API_KEY": KEY})
        result, text = self.read_result()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], guard.NEON_UNREACHABLE)
        self.assertEqual(call.call_count, 1)
        self.assert_no_planted(text, out, err, PROVIDER_TEXT)


if __name__ == "__main__":
    unittest.main()
