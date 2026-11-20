"""Automatic rollback when the live-data walk (Pass B) fails: scripts/live_rollback.py.

Offline: git and Coolify are fakes that record every call, so the tests prove
the decision, the refusals before any effect and the order of the effects.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.live_rollback as rollback  # noqa: E402

CANDIDATE = "sha256:" + "c" * 64
PREVIOUS = "sha256:" + "p" * 0 + "a" * 64
CANDIDATE_SRC = "1" * 40
PREVIOUS_SRC = "2" * 40
COMPOSE = (
    f"# Application image source: {CANDIDATE_SRC}\n"
    "# Coolify supplies the runtime environment file and proxy routing.\n"
    "services:\n  quote-internal:\n"
    f'    image: "{CANDIDATE}"\n'
    "    pull_policy: never\n"
)
LIVE_URL = "https://quote-internal.example.test"
SWITCHED_AT = "2026-11-21T03:00:00Z"
WALK_FACTS = {"walked_origin_sha256": hashlib.sha256(LIVE_URL.encode("utf-8")).hexdigest(),
              "started_at": "2026-11-21T03:05:00.000Z", "downloaded_files": False, "compared_with_pass_a": True}
PASSING_STEPS = ["sign in", "quote list loads", "quote list matches the dashboard", "walk targets",
                 "quote 1 reopens read-only", "served app.js", "no write attempted"]


class FakeGit:
    def __init__(self, calls: list, branch: str = "deployment/internal", clean: bool = True, fail: str = "") -> None:
        self.calls, self._branch, self._clean, self.fail = calls, branch, clean, fail

    def branch(self) -> str:
        return self._branch

    def clean(self) -> bool:
        return self._clean

    def commit_all(self, message: str) -> str:
        self.calls.append(("commit", message))
        return "f" * 40

    def push(self) -> None:
        if self.fail == "push":
            raise RuntimeError("git push failed (exit 1)")
        self.calls.append(("push",))


class FakeCoolify:
    def __init__(self, calls: list, name: str = "quote-internal", statuses=("in_progress", "finished")) -> None:
        self.calls, self.name, self.statuses = calls, name, list(statuses)

    def app_name(self, uuid: str) -> str:
        self.calls.append(("app_name", uuid))
        return self.name

    def deploy(self, uuid: str) -> str:
        self.calls.append(("deploy", uuid))
        return "dep123"

    def deployment_status(self, deployment: str) -> str:
        self.calls.append(("status", deployment))
        return self.statuses.pop(0) if self.statuses else "in_progress"


class LiveRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.checkout = self.base / "deploy-checkout"
        (self.checkout / "deploy" / "internal").mkdir(parents=True)
        self.compose = self.checkout / "deploy" / "internal" / "compose.yaml"
        self.compose.write_text(COMPOSE, encoding="utf-8")
        self.result = self.base / "live-walk.json"
        self.record = self.base / "rollback.json"
        self.calls: list = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def walk(self, result: str = "pass", steps=PASSING_STEPS, failed: str = "", **extra) -> None:
        body = {"schema": "sqag-rehearsal-check/v1", "profile": "live-readonly", "result": result,
                "steps": [{"name": name, "status": "fail" if name == failed else "pass"} for name in steps], **WALK_FACTS, **extra}
        self.result.write_text(json.dumps(body), encoding="utf-8")

    def go(self, *extra: str, git=None, coolify=None, **overrides: str) -> tuple[int, dict]:
        values = {"--candidate-image": CANDIDATE, "--candidate-source": CANDIDATE_SRC, "--previous-image": PREVIOUS,
                  "--previous-source": PREVIOUS_SRC, "--live-app-uuid": "abc123def456",
                  "--live-url": LIVE_URL, "--switched-at": SWITCHED_AT, **overrides}
        argv = ["--pass-b-result", str(self.result), "--deploy-checkout", str(self.checkout), "--record", str(self.record)]
        for key, value in values.items():
            argv += [key, value]
        git = git or FakeGit(self.calls)
        coolify = coolify or FakeCoolify(self.calls)
        return rollback.run(argv + list(extra), {}, git_factory=lambda _: git, coolify_factory=lambda _: coolify,
                            sleep=lambda _: None, clock=iter(range(0, 10**6, 5)).__next__)

    def test_a_full_passing_walk_keeps_the_candidate_and_does_nothing(self) -> None:
        self.walk()
        code, record = self.go("--execute")
        self.assertEqual((code, record["decision"], record["status"]), (0, "keep", "not_needed"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.compose.read_text(encoding="utf-8"), COMPOSE)

    def test_anything_short_of_a_full_pass_rolls_back(self) -> None:
        cases = {
            "walk failed": lambda: self.walk(result="fail"),
            "missing result": lambda: self.result.unlink(missing_ok=True),
            "unreadable result": lambda: self.result.write_text("{", encoding="utf-8"),
            "other profile": lambda: self.walk(profile="seed"),
            "walk not run (no manifest)": lambda: self.walk(steps=["sign in", "quote list loads", "served app.js", "no write attempted"]),
            "write refused": lambda: self.walk(failed="no write attempted"),
            "no quote reopened": lambda: self.walk(steps=[s for s in PASSING_STEPS if not s.startswith("quote 1")]),
            "walked another host (the copy)": lambda: self.walk(walked_origin_sha256=hashlib.sha256(b"https://quote-rehearsal-copy.example.test").hexdigest()),
            "walked before the switch (an old result)": lambda: self.walk(started_at="2026-11-21T02:59:59.000Z"),
            "no start time": lambda: self.walk(started_at=None),
            "Pass A result (downloaded files)": lambda: self.walk(downloaded_files=True, compared_with_pass_a=False),
            "not compared with Pass A": lambda: self.walk(compared_with_pass_a=False),
        }
        for label, prepare in cases.items():
            with self.subTest(label):
                self.calls.clear()
                self.compose.write_text(COMPOSE, encoding="utf-8")
                prepare()
                code, record = self.go()
                self.assertEqual((code, record["decision"], record["status"]), (3, "rollback", "planned"), label)
                self.assertEqual(self.calls, [], "a plan without --execute has no effect")

    def test_live_url_and_switch_time_must_be_well_formed(self) -> None:
        self.walk()
        self.assertEqual(self.go("--execute")[0], 0)
        # The same origin written with a trailing slash or default port is still live's.
        self.assertEqual(self.go("--execute", **{"--live-url": "https://QUOTE-INTERNAL.example.test:443/"})[0], 0)
        for overrides in ({"--live-url": "http://quote-internal.example.test"}, {"--live-url": "https://quote-internal.example.test/app"},
                          {"--switched-at": "yesterday"}, {"--switched-at": "2026-11-21T03:00:00"}):
            with self.subTest(overrides):
                self.calls.clear()
                code, record = self.go("--execute", **overrides)
                self.assertEqual((code, record["status"]), (2, "refused"))
                self.assertEqual(self.calls, [])

    def test_rollback_pins_the_previous_image_then_pushes_then_deploys(self) -> None:
        self.walk(result="fail")
        code, record = self.go("--execute")
        self.assertEqual((code, record["status"]), (10, "rolled_back"), record)
        self.assertEqual([call[0] for call in self.calls], ["app_name", "commit", "push", "deploy", "status", "status"])
        text = self.compose.read_text(encoding="utf-8")
        self.assertEqual(text, COMPOSE.replace(CANDIDATE_SRC, PREVIOUS_SRC).replace(CANDIDATE, PREVIOUS))
        self.assertIn(PREVIOUS_SRC[:7], self.calls[1][1])
        saved = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "rolled_back")
        self.assertNotIn("http", self.record.read_text(encoding="utf-8"))

    def test_refusals_happen_before_any_effect(self) -> None:
        self.walk(result="fail")
        cases = [
            ("wrong branch", {"git": FakeGit(self.calls, branch="main")}, {}),
            ("dirty checkout", {"git": FakeGit(self.calls, clean=False)}, {}),
            ("not live's app", {"coolify": FakeCoolify(self.calls, name="quote-rehearsal")}, {}),
            ("same image", {}, {"--previous-image": CANDIDATE}),
            ("short commit", {}, {"--previous-source": "2222222"}),
            ("bad image id", {}, {"--previous-image": "latest"}),
            ("bad uuid", {}, {"--live-app-uuid": "../x"}),
        ]
        for label, fakes, overrides in cases:
            with self.subTest(label):
                self.calls.clear()
                code, record = self.go("--execute", **fakes, **overrides)
                self.assertEqual((code, record["status"]), (2, "refused"), label)
                self.assertEqual([c for c in self.calls if c[0] != "app_name"], [], label)
                self.assertEqual(self.compose.read_text(encoding="utf-8"), COMPOSE, label)

    def test_a_checkout_not_pinned_to_the_candidate_is_refused(self) -> None:
        self.walk(result="fail")
        self.compose.write_text(COMPOSE.replace(CANDIDATE, "sha256:" + "e" * 64), encoding="utf-8")
        code, record = self.go("--execute")
        self.assertEqual((code, record["status"]), (2, "refused"))
        self.assertIn("not pinned to the candidate", record["error"])
        self.assertEqual(self.calls, [])

    def test_a_failed_push_stops_before_deploy_and_a_failed_deploy_is_urgent(self) -> None:
        self.walk(result="fail")
        code, record = self.go("--execute", git=FakeGit(self.calls, fail="push"))
        self.assertEqual((code, record["status"]), (1, "failed"))
        self.assertNotIn("deploy", [call[0] for call in self.calls])
        self.compose.write_text(COMPOSE, encoding="utf-8")
        self.calls.clear()
        code, record = self.go("--execute", coolify=FakeCoolify(self.calls, statuses=("in_progress", "failed")))
        self.assertEqual((code, record["status"]), (1, "failed"))
        self.assertIn("ended failed", record["error"])

    def test_a_deploy_that_never_finishes_times_out(self) -> None:
        self.walk(result="fail")
        code, record = self.go("--execute", coolify=FakeCoolify(self.calls, statuses=()))
        self.assertEqual((code, record["status"]), (1, "failed"))

    def test_coolify_settings_come_from_the_environment_only(self) -> None:
        with self.assertRaises(rollback.Refusal):
            rollback.Coolify({})
        with self.assertRaises(rollback.Refusal):
            rollback.Coolify({"COOLIFY_SWOOSHZ_API_URL": "http://coolify.example.test", "COOLIFY_SWOOSHZ_API_TOKEN": "x"})
        client = rollback.Coolify({"COOLIFY_SWOOSHZ_API_URL": "https://coolify.example.test/", "COOLIFY_SWOOSHZ_API_TOKEN": "x"})
        self.assertEqual(client.base, "https://coolify.example.test/api/v1")


if __name__ == "__main__":
    unittest.main()
