"""Tests for scripts/rehearsal_database_url.py: offline, with fake Coolify and step runners.

No network and no database. Hosts, ids, credentials and the Coolify token are fakes; the
production endpoint is a fake one recorded in a temp map, so the guard's rules run against a
fixture. A URL value must never reach a record, stdout or stderr.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import live_rollback  # noqa: E402
from scripts import neon_branch_guard as guard  # noqa: E402
from scripts import rehearsal_database_url as dburl  # noqa: E402
from tests.neon_guard_fakes import COPY_BRANCH, KEY, REHEARSAL_BRANCH, FakeNeon, fake_client_from_env  # noqa: E402

REGION = "c-0.fake-region-1.aws.neon.tech"
PRODUCTION_ENDPOINT = "ep-fake-production-000001"
PRODUCTION_BRANCH = "br-fake-production-000001"
COPY_ENDPOINT = "ep-fake-copy-000001"
OTHER_ENDPOINT = "ep-fake-other-000001"
SERVICE_UUID = "fakeserviceuuid000001"
TOKEN = "fake-coolify-token-planted"
USER = "fake-user-planted"
PASSWORD = "fake-password-planted"
NEW_PASSWORD = "fake-new-password-planted"
PLANTED = (USER, PASSWORD, NEW_PASSWORD, TOKEN, "neondb", KEY)
NOT_CHECKED = "did not check the branch with Neon's API"


def url(endpoint: str, *, password: str = PASSWORD, pooler: bool = False, role: str = USER) -> str:
    host = f"{endpoint}{'-pooler' if pooler else ''}.{REGION}"
    return f"postgresql://{role}:{password}@{host}/neondb?sslmode=require"


def write_map(folder: Path) -> Path:
    path = folder / "map.json"
    path.write_text(
        json.dumps(
            {
                "resources": [
                    {"name": "quote-rehearsal", "kind": "coolify_service"},
                    {"name": "quote-rehearsal-copy", "kind": "coolify_service"},
                    {"name": "sqag-rehearsal", "kind": "r2_bucket"},
                ],
                "production_guard": {
                    "neon_project": "fake-project",
                    "branch_prefix": "rehearsal-",
                    "forbidden_endpoints": [
                        {"name": "fake live compute", "endpoint_id": PRODUCTION_ENDPOINT, "branch_id": PRODUCTION_BRANCH}
                    ],
                    "source": "test fixture",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def guard_result(
    endpoint: str = COPY_ENDPOINT, *, status: str = "pass", age: timedelta = timedelta(minutes=5), neon_api: str = "checked"
) -> dict:
    checked = (datetime.now(timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": guard.RESULT_SCHEMA,
        "status": status,
        "host": f"{endpoint}.{REGION}",
        "endpoint_id": endpoint,
        "branch_id": REHEARSAL_BRANCH if neon_api == "checked" else None,
        "branch_name": COPY_BRANCH if neon_api == "checked" else None,
        "branch_default": False if neon_api == "checked" else None,
        "neon_api": neon_api,
        "checked_at": checked,
        "reason": None if status == "pass" else "planted refusal",
    }


class FakeCoolify:
    """Stands in for live_rollback.Coolify: records every call, answers from a script."""

    def __init__(self, name: str = "quote-rehearsal-copy", patch_error: int | None = None) -> None:
        self.name = name
        self.patch_error = patch_error
        self.calls: list[tuple[str, str, Any]] = []

    def call(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        if method == "GET":
            return {"name": self.name, "uuid": SERVICE_UUID}
        if method == "PATCH" and self.patch_error is not None:
            raise live_rollback.CoolifyHttpError(method, self.patch_error)
        return {"uuid": "fake-env-uuid"}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.map = write_map(self.dir)
        self.guard_file = self.dir / "db-guard.json"
        self.record = self.dir / "record.json"
        # Neon's API is faked: it confirms a rehearsal branch whenever the key's name is set.
        self.neon = FakeNeon()
        patcher = mock.patch.object(guard, "neon_client_from_env", fake_client_from_env(self.neon))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_guard(self, result: dict) -> None:
        self.guard_file.write_text(json.dumps(result), encoding="utf-8")

    def read_record(self) -> tuple[dict, str]:
        text = self.record.read_text(encoding="utf-8")
        return json.loads(text), text

    def assert_no_planted(self, *texts: str) -> None:
        for text in texts:
            for planted in PLANTED:
                self.assertNotIn(planted, text)


class SetServiceTests(Base):
    def set_service(
        self, env: dict[str, str], coolify: FakeCoolify, uuid: str = SERVICE_UUID, *, neon_key: bool = True
    ) -> tuple[int, str, str]:
        env = {guard.NEON_KEY_ENV: KEY, **env} if neon_key else env
        argv = [
            "set-service", "--guard-result", str(self.guard_file), "--database-url-env", "COPY_URL",
            "--service-uuid", uuid, "--result", str(self.record), "--map", str(self.map),
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dburl.main(argv, env=env, coolify_factory=lambda _env: coolify)
        return code, out.getvalue(), err.getvalue()

    def test_the_guarded_url_is_written_to_the_rehearsal_service_and_the_record_holds_no_url(self) -> None:
        self.write_guard(guard_result())
        coolify = FakeCoolify()
        value = url(COPY_ENDPOINT, password=NEW_PASSWORD, pooler=True)
        code, out, err = self.set_service({"COPY_URL": value}, coolify)
        record, text = self.read_record()
        self.assertEqual(code, 0, err)
        self.assertEqual(
            coolify.calls,
            [
                ("GET", f"/services/{SERVICE_UUID}", None),
                ("PATCH", f"/services/{SERVICE_UUID}/envs", {"key": "SQAG_DATABASE_URL", "value": value}),
            ],
        )
        self.assertEqual(record["schema"], dburl.SET_SCHEMA)
        self.assertEqual(
            (record["status"], record["service_name"], record["service_uuid"], record["key"], record["endpoint_id"]),
            ("pass", "quote-rehearsal-copy", SERVICE_UUID, "SQAG_DATABASE_URL", COPY_ENDPOINT),
        )
        self.assertEqual(record["host"], f"{COPY_ENDPOINT}-pooler.{REGION}")
        self.assertEqual(record["guard_checked_at"], json.loads(self.guard_file.read_text())["checked_at"])
        self.assert_no_planted(text, out, err)

    def test_a_missing_key_is_created(self) -> None:
        self.write_guard(guard_result())
        coolify = FakeCoolify(patch_error=404)
        code, _, err = self.set_service({"COPY_URL": url(COPY_ENDPOINT)}, coolify)
        self.assertEqual(code, 0, err)
        self.assertEqual([call[0] for call in coolify.calls], ["GET", "PATCH", "POST"])
        self.assertEqual(coolify.calls[2][1:], (f"/services/{SERVICE_UUID}/envs", {"key": "SQAG_DATABASE_URL", "value": url(COPY_ENDPOINT)}))

    def test_refusals_write_nothing_to_coolify(self) -> None:
        cases = [
            # The owner's failure: the guard passed for the copy branch, the URL given is live's.
            ("live's URL with a passing guard for the copy", guard_result(), url(PRODUCTION_ENDPOINT), "recorded production endpoint", []),
            ("live's pooler URL", guard_result(), url(PRODUCTION_ENDPOINT, pooler=True), "recorded production endpoint", []),
            ("a URL that is not the guarded branch", guard_result(), url(OTHER_ENDPOINT), "not on the guarded branch", []),
            ("a guard that refused", guard_result(status="refused"), url(COPY_ENDPOINT), "did not pass", []),
            ("a guard older than 12 hours", guard_result(age=timedelta(hours=13)), url(COPY_ENDPOINT), "older than 12 hours", []),
            ("a guard for live's endpoint", guard_result(PRODUCTION_ENDPOINT), url(PRODUCTION_ENDPOINT), "recorded production endpoint", []),
            ("not a Neon host", guard_result(), "postgresql://u:p@db.example.test/neondb", "unknown host", []),
        ]
        for label, result, value, reason, calls in cases:
            with self.subTest(label):
                self.write_guard(result)
                coolify = FakeCoolify()
                code, out, err = self.set_service({"COPY_URL": value}, coolify)
                record, text = self.read_record()
                self.assertEqual(code, 2, err)
                self.assertEqual(record["status"], "refused")
                self.assertIn(reason, record["reason"])
                self.assertEqual(coolify.calls, calls)
                self.assert_no_planted(text, out, err)

    def test_a_guard_that_neon_did_not_confirm_writes_nothing_to_coolify(self) -> None:
        cases = [
            # The endpoint-only guard: it passes a host it cannot tie to a branch.
            ("a guard result with neon_api not_checked", guard_result(neon_api="not_checked"), True),
            ("a checked guard result, but no key for the rerun", guard_result(), False),
        ]
        for label, result, neon_key in cases:
            with self.subTest(label):
                self.write_guard(result)
                coolify = FakeCoolify()
                code, out, err = self.set_service({"COPY_URL": url(COPY_ENDPOINT)}, coolify, neon_key=neon_key)
                record, text = self.read_record()
                self.assertEqual((code, record["status"]), (2, "refused"), err)
                self.assertIn(NOT_CHECKED, record["reason"])
                self.assertIn(guard.NEON_KEY_ENV, record["reason"])
                self.assertEqual(coolify.calls, [])
                self.assert_no_planted(text, out, err)

    def test_a_service_that_is_not_a_rehearsal_service_is_refused_before_the_write(self) -> None:
        self.write_guard(guard_result())
        coolify = FakeCoolify(name="quote-internal")
        code, _, err = self.set_service({"COPY_URL": url(COPY_ENDPOINT)}, coolify)
        record, _ = self.read_record()
        self.assertEqual(code, 2, err)
        self.assertIn("not a rehearsal service", record["reason"])
        self.assertEqual([call[0] for call in coolify.calls], ["GET"])

    def test_bad_inputs_are_refused_before_coolify(self) -> None:
        cases = [
            ("unset variable", {}, SERVICE_UUID, "unset or empty"),
            ("malformed service id", {"COPY_URL": url(COPY_ENDPOINT)}, "../applications/x", "--service-uuid"),
        ]
        self.write_guard(guard_result())
        for label, env, uuid, reason in cases:
            with self.subTest(label):
                coolify = FakeCoolify()
                code, _, _ = self.set_service(env, coolify, uuid)
                record, _ = self.read_record()
                self.assertEqual((code, record["status"]), (2, "refused"))
                self.assertIn(reason, record["reason"])
                self.assertEqual(coolify.calls, [])

    def test_a_coolify_error_exits_one_with_the_status_only(self) -> None:
        self.write_guard(guard_result())
        coolify = FakeCoolify(patch_error=500)
        code, out, err = self.set_service({"COPY_URL": url(COPY_ENDPOINT)}, coolify)
        record, text = self.read_record()
        self.assertEqual(code, 1)
        self.assertEqual(record["reason"], "Coolify PATCH returned HTTP 500")
        self.assert_no_planted(text, out, err)


class MigratorTests(Base):
    def run_step(self, step: str, env: dict[str, str], *, neon_key: bool = True) -> tuple[int, list[list[str]], str, str]:
        env = {guard.NEON_KEY_ENV: KEY, **env} if neon_key else env
        ran: list[list[str]] = []

        def runner(command: list[str], step_env: dict[str, str]) -> int:
            ran.append(command)
            return 0

        argv = ["run-migrator", "--guard-result", str(self.guard_file), "--step", step, "--result", str(self.record), "--map", str(self.map)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dburl.main(argv, env=env, runner=runner)
        return code, ran, out.getvalue(), err.getvalue()

    def test_each_step_runs_its_command_after_the_guard(self) -> None:
        self.write_guard(guard_result())
        migrator = {"SQAG_MIGRATOR_DATABASE_URL": url(COPY_ENDPOINT, role="fake-migrator-planted")}
        both = {**migrator, "SQAG_DATABASE_URL": url(COPY_ENDPOINT, pooler=True), "SQAG_MAINTENANCE_DATABASE_URL": url(COPY_ENDPOINT)}
        cases = [
            ("check", migrator, []),
            ("pre-apply", migrator, [[sys.executable, str(ROOT / "scripts" / "preflight_sqag_migrations.py"), "--phase", "pre-apply"]]),
            ("apply", migrator, [[sys.executable, str(ROOT / "scripts" / "migrate_sqag_storage.py")]]),
            ("post-apply", both, [[sys.executable, str(ROOT / "scripts" / "preflight_sqag_migrations.py"), "--phase", "post-apply"]]),
        ]
        for step, env, commands in cases:
            with self.subTest(step):
                code, ran, out, err = self.run_step(step, env)
                record, text = self.read_record()
                self.assertEqual(code, 0, err)
                self.assertEqual(ran, commands)
                self.assertEqual((record["schema"], record["status"], record["step"], record["endpoint_id"]), (dburl.MIGRATOR_SCHEMA, "pass", step, COPY_ENDPOINT))
                self.assertEqual(record["checked_variables"], sorted(env))
                self.assertEqual(record["neon_api"], "checked")
                self.assert_no_planted(text, out, err, "fake-migrator-planted")

    def test_an_unguarded_migrator_url_runs_nothing(self) -> None:
        cases = [
            ("live's migrator URL", {"SQAG_MIGRATOR_DATABASE_URL": url(PRODUCTION_ENDPOINT)}, "recorded production endpoint"),
            ("another branch's migrator URL", {"SQAG_MIGRATOR_DATABASE_URL": url(OTHER_ENDPOINT)}, "not on the guarded branch"),
            ("no migrator URL", {}, "SQAG_MIGRATOR_DATABASE_URL is unset or empty"),
            (
                "a runtime URL on live beside a guarded migrator URL",
                {"SQAG_MIGRATOR_DATABASE_URL": url(COPY_ENDPOINT), "SQAG_DATABASE_URL": url(PRODUCTION_ENDPOINT)},
                "recorded production endpoint",
            ),
        ]
        self.write_guard(guard_result())
        for label, env, reason in cases:
            for step in ("check", "apply"):
                with self.subTest(label, step=step):
                    code, ran, out, err = self.run_step(step, env)
                    record, text = self.read_record()
                    self.assertEqual(code, 2, err)
                    self.assertEqual(ran, [])
                    self.assertIn(reason, record["reason"])
                    self.assert_no_planted(text, out, err)

    def test_a_guard_that_neon_did_not_confirm_runs_no_step(self) -> None:
        env = {"SQAG_MIGRATOR_DATABASE_URL": url(COPY_ENDPOINT), "SQAG_DATABASE_URL": url(COPY_ENDPOINT, pooler=True),
               "SQAG_MAINTENANCE_DATABASE_URL": url(COPY_ENDPOINT)}
        cases = [
            ("a guard result with neon_api not_checked", guard_result(neon_api="not_checked"), True),
            ("a checked guard result, but no key for the rerun", guard_result(), False),
        ]
        for label, result, neon_key in cases:
            # check gates the role password reset; the others are the migration steps.
            for step in sorted(dburl.STEPS):
                with self.subTest(label, step=step):
                    self.write_guard(result)
                    code, ran, out, err = self.run_step(step, env, neon_key=neon_key)
                    record, text = self.read_record()
                    self.assertEqual((code, ran, record["status"]), (2, [], "refused"), err)
                    self.assertIn(NOT_CHECKED, record["reason"])
                    self.assert_no_planted(text, out, err)

    def test_post_apply_needs_the_runtime_and_maintenance_urls(self) -> None:
        self.write_guard(guard_result())
        code, ran, _, _ = self.run_step("post-apply", {"SQAG_MIGRATOR_DATABASE_URL": url(COPY_ENDPOINT)})
        record, _ = self.read_record()
        self.assertEqual((code, ran), (2, []))
        self.assertIn("SQAG_DATABASE_URL is unset or empty", record["reason"])

    def test_a_failed_step_keeps_its_exit_code(self) -> None:
        self.write_guard(guard_result())
        argv = ["run-migrator", "--guard-result", str(self.guard_file), "--step", "apply", "--result", str(self.record), "--map", str(self.map)]
        with contextlib.redirect_stderr(io.StringIO()):
            code = dburl.main(
                argv, env={guard.NEON_KEY_ENV: KEY, "SQAG_MIGRATOR_DATABASE_URL": url(COPY_ENDPOINT)}, runner=lambda command, env: 7
            )
        record, _ = self.read_record()
        self.assertEqual((code, record["status"], record["exit_code"]), (7, "step_failed", 7))


class CoolifyBodyTests(unittest.TestCase):
    def test_a_json_body_is_sent_with_the_token_only_in_the_header_and_no_redirects(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"uuid": "x"}'
        client = live_rollback.Coolify({"COOLIFY_SWOOSHZ_API_URL": "https://coolify.example.test", "COOLIFY_SWOOSHZ_API_TOKEN": TOKEN})
        with mock.patch.object(urllib.request.OpenerDirector, "open", return_value=response) as opened:
            client.call("PATCH", "/services/x/envs", {"key": "K", "value": "planted-value"})
        request = opened.call_args.args[0]
        self.assertEqual((request.get_method(), request.full_url), ("PATCH", "https://coolify.example.test/api/v1/services/x/envs"))
        self.assertEqual(json.loads(request.data), {"key": "K", "value": "planted-value"})
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, request.full_url)

    def test_http_errors_carry_the_method_and_status_only(self) -> None:
        client = live_rollback.Coolify({"COOLIFY_SWOOSHZ_API_URL": "https://coolify.example.test", "COOLIFY_SWOOSHZ_API_TOKEN": TOKEN})
        error = urllib.error.HTTPError("https://coolify.example.test", 302, "planted-provider-text", {}, None)
        with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=error):
            with self.assertRaises(live_rollback.CoolifyHttpError) as caught:
                client.call("GET", "/services/x")
        self.assertEqual((str(caught.exception), caught.exception.status), ("Coolify GET returned HTTP 302", 302))


if __name__ == "__main__":
    unittest.main()
