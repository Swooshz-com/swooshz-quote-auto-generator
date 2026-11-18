"""Release settings record and names-only checker tests."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.check_release_settings as checker  # noqa: E402

SETTINGS = ROOT / "deploy" / "internal" / "release-settings.json"
TEMPLATE = ROOT / "deploy" / "internal-uat" / "coolify" / "sqag.uat.env.example"


def run_checker(*args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = checker.main(list(args))
    return code, buffer.getvalue()


class ReleaseSettingsFileTests(unittest.TestCase):
    def test_repo_file_passes(self) -> None:
        code, out = run_checker()
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["status"], "ok")

    def test_names_cover_the_env_template_exactly(self) -> None:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(checker.settings_names(data), set(checker.parse_template(TEMPLATE)))

    def test_require_complete_fails_while_values_are_pending(self) -> None:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        code, out = run_checker("--require-complete")
        if checker.pending_names(data):
            self.assertEqual(code, 1)
            self.assertIn("pending", out)
        else:
            self.assertEqual(code, 0, out)

    def write_settings(self, folder: Path, mutate) -> Path:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        mutate(data)
        path = folder / "settings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_secret_like_shared_value_is_refused_without_echo(self) -> None:
        secret = "sk-or-v1-" + "a1b2c3d4" * 6
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_settings(Path(tmp), lambda d: d["shared"].__setitem__("OPENROUTER_DRAFT_MODEL", secret))
            code, out = run_checker("--settings", str(path), "--template", str(TEMPLATE))
        self.assertEqual(code, 1)
        self.assertNotIn(secret, out)

    def test_name_in_two_groups_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_settings(Path(tmp), lambda d: d["per_app"].append("SESSION_SECRET"))
            code, out = run_checker("--settings", str(path), "--template", str(TEMPLATE))
        self.assertEqual(code, 1)
        self.assertIn("SESSION_SECRET", out)

    def test_fixed_template_value_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_settings(Path(tmp), lambda d: d["shared"].__setitem__("APP_MODE", "local"))
            code, out = run_checker("--settings", str(path), "--template", str(TEMPLATE))
        self.assertEqual(code, 1)
        self.assertIn("APP_MODE", out)

    def test_print_shared_env_refuses_while_pending(self) -> None:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if not checker.pending_names(data):
            self.skipTest("no pending values")
        code, out = run_checker("--print-shared-env")
        self.assertEqual(code, 1)
        self.assertNotIn("APP_MODE=deploy", out)

    def test_print_shared_env_prints_only_shared_names_when_complete(self) -> None:
        def fill(d):
            for name, value in d["shared"].items():
                if value is None:
                    d["shared"][name] = "" if name in d["optional"] else "reviewed-value"

        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_settings(Path(tmp), fill)
            code, out = run_checker("--settings", str(path), "--template", str(TEMPLATE), "--print-shared-env")
        self.assertEqual(code, 0, out)
        names = {line.split("=", 1)[0] for line in out.splitlines()}
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(names, set(data["shared"]))
        self.assertFalse(names & set(data["secrets"]))


class EnvNamesComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        self.all_names = sorted(checker.settings_names(self.data))
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def names_file(self, lines: list[str], name: str = "names.txt") -> Path:
        path = self.folder / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_full_name_list_passes(self) -> None:
        code, out = run_checker("--env-names", str(self.names_file(self.all_names)))
        self.assertEqual(code, 0, out)
        report = json.loads(out)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["unexpected"], [])

    def test_missing_zdr_is_named(self) -> None:
        names = [n for n in self.all_names if n != "OPENROUTER_ZDR"]
        code, out = run_checker("--env-names", str(self.names_file(names)))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["missing"], ["OPENROUTER_ZDR"])

    def test_optional_names_may_be_absent(self) -> None:
        names = [n for n in self.all_names if n not in self.data["optional"]]
        code, out = run_checker("--env-names", str(self.names_file(names)))
        self.assertEqual(code, 0, out)

    def test_value_line_is_refused_without_echo(self) -> None:
        secret_value = "hunter2-very-private-value"
        names = self.all_names + [f"SESSION_SECRET={secret_value}"]
        code, out = run_checker("--env-names", str(self.names_file(names)))
        self.assertEqual(code, 1)
        self.assertIn("contains '='", out)
        self.assertNotIn(secret_value, out)

    def test_unexpected_and_must_be_unset_names_fail(self) -> None:
        names = self.all_names + ["SOMETHING_NEW", "OIDC_CLIENT_ID", "OPENROUTER_DRAFT_SCHEMA_ENABLED"]
        code, out = run_checker("--env-names", str(self.names_file(names)))
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["unexpected"], ["SOMETHING_NEW"])
        self.assertEqual(report["must_be_unset_present"], ["OIDC_CLIENT_ID", "OPENROUTER_DRAFT_SCHEMA_ENABLED"])

    def test_platform_and_image_names_are_ignored(self) -> None:
        names = self.all_names + ["HOSTNAME", "COOLIFY_FQDN", "PATH", "NIXPACKS_PATH"]
        ignore = self.names_file(["PATH", "NIXPACKS_PATH"], "image.txt")
        code, out = run_checker("--env-names", str(self.names_file(names)), "--ignore-names", str(ignore))
        self.assertEqual(code, 0, out)


class AppNeverReadsSettingsTests(unittest.TestCase):
    def test_webapp_source_never_names_the_settings_file(self) -> None:
        for path in (ROOT / "webapp").rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".html", ".json", ".css"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn("release-settings", text, str(path))
                self.assertNotIn("release_settings", text, str(path))

    def test_server_config_load_never_opens_the_settings_file(self) -> None:
        probe = (
            "import sys\n"
            "def hook(event, args):\n"
            "    if event == 'open' and args and 'release-settings' in str(args[0]):\n"
            "        raise SystemExit('opened release settings')\n"
            "sys.addaudithook(hook)\n"
            "sys.path.insert(0, 'webapp')\n"
            "import server\n"
            "status = server.deploy_uat_preflight_status()\n"
            "print('checks', len(status['checks']))\n"
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("SQAG_", "OPENROUTER_", "QUOTE_", "AUTH_", "APP_MODE", "SESSION_"))
        }
        env.update({"SQAG_DISABLE_DOTENV": "1", "APP_MODE": "deploy", "PYTHONDONTWRITEBYTECODE": "1"})
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("checks", result.stdout)


if __name__ == "__main__":
    unittest.main()
