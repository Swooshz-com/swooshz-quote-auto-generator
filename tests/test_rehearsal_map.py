"""Rehearsal map and compose file tests (offline)."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.check_rehearsal_map as checker  # noqa: E402

MAP = ROOT / "deploy" / "rehearsal" / "resources.json"
COMPOSE = ROOT / "deploy" / "rehearsal" / "compose.yaml"
SETTINGS = ROOT / "deploy" / "internal" / "release-settings.json"


class RehearsalMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        for source in (MAP, COMPOSE, SETTINGS):
            target = self.base / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.map_path = self.base / MAP.relative_to(ROOT)
        self.compose_path = self.base / COMPOSE.relative_to(ROOT)
        self.data = json.loads(MAP.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def check_with(self, data=None, compose_text: str | None = None) -> list[str]:
        if data is not None:
            self.map_path.write_text(json.dumps(data), encoding="utf-8")
        if compose_text is not None:
            self.compose_path.write_text(compose_text, encoding="utf-8")
        return checker.run_checks(self.map_path)

    def test_repo_map_passes(self) -> None:
        self.assertEqual(checker.run_checks(MAP), [])
        self.assertEqual(checker.main(["--map", str(MAP)]), 0)

    def test_copy_passes(self) -> None:
        self.assertEqual(self.check_with(), [])

    def test_resource_without_delete_step_fails(self) -> None:
        data = copy.deepcopy(self.data)
        data["resources"][0]["delete"] = ""
        self.assertTrue(any("has no delete" in e for e in self.check_with(data)))

    def test_value_in_a_secret_source_fails_without_echo(self) -> None:
        data = copy.deepcopy(self.data)
        secret = "sk-or-v1-" + "9f8e7d6c" * 6
        data["env_sources"]["credential_store"]["OPENROUTER_API_KEY"] = secret
        errors = self.check_with(data)
        self.assertTrue(errors)
        self.assertFalse(any(secret in e for e in errors))

    def test_random_token_anywhere_fails(self) -> None:
        data = copy.deepcopy(self.data)
        data["resources"][0]["created_by"] = "token Xk29fLq0aZ8mN3bV7cT1yU5rE4wQ6pO2iH9gJ"
        self.assertTrue(any("looks like it holds a secret" in e for e in self.check_with(data)))

    def test_database_url_fails(self) -> None:
        data = copy.deepcopy(self.data)
        data["run_values"]["SQAG_PUBLIC_BASE_URL"] = "postgresql://user:pw@host/db"
        self.assertTrue(self.check_with(data))

    def test_live_name_fails(self) -> None:
        data = copy.deepcopy(self.data)
        data["resources"][0]["name"] = "quote-internal"
        errors = self.check_with(data)
        self.assertTrue(any("live name" in e for e in errors))
        self.assertTrue(any("rehearsal" in e for e in errors))

    def test_per_run_name_must_say_rehearsal(self) -> None:
        data = copy.deepcopy(self.data)
        for resource in data["resources"]:
            if resource["id"] == "neon-branch":
                resource["name"] = "test-<run id>"
        self.assertTrue(any("'rehearsal'" in e for e in self.check_with(data)))

    def test_only_credential_entries_and_the_rehearsal_bucket_are_kept(self) -> None:
        data = copy.deepcopy(self.data)
        data["resources"][0]["lifetime"] = "kept"
        self.assertTrue(any("may outlive a run" in e for e in self.check_with(data)))

    def test_bucket_must_say_rehearsal(self) -> None:
        data = copy.deepcopy(self.data)
        data["storage"]["bucket"] = "sqag-live"
        self.assertTrue(self.check_with(data))

    def test_secret_written_into_compose_fails(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8").replace(
            'SESSION_SECRET: "${SESSION_SECRET}"', 'SESSION_SECRET: "not-a-real-secret-but-written-in"'
        )
        self.assertTrue(any("SESSION_SECRET" in e for e in self.check_with(compose_text=text)))

    def test_shared_setting_hardcoded_in_compose_fails(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8").replace(
            'OPENROUTER_ZDR: "${OPENROUTER_ZDR}"', 'OPENROUTER_ZDR: "true"'
        )
        self.assertTrue(any("OPENROUTER_ZDR" in e for e in self.check_with(compose_text=text)))

    def test_env_name_without_source_fails(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8").replace(
            '      PORT: "8765"\n', '      PORT: "8765"\n      EXTRA_SETTING: "x"\n'
        )
        self.assertTrue(any("EXTRA_SETTING" in e for e in self.check_with(compose_text=text)))

    def test_missing_env_name_fails(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8").replace('      OPENROUTER_ZDR: "${OPENROUTER_ZDR}"\n', "")
        self.assertTrue(any("missing OPENROUTER_ZDR" in e for e in self.check_with(compose_text=text)))

    def test_compose_has_one_service_and_no_sidecar(self) -> None:
        compose = checker.parse_mini_yaml(COMPOSE.read_text(encoding="utf-8"))
        self.assertEqual(list(compose["services"]), ["quote-rehearsal"])
        self.assertNotIn("volumes", compose)
        sidecar = COMPOSE.read_text(encoding="utf-8").replace(
            "services:\n", 'services:\n  storage:\n    image: "example/s3:1"\n', 1
        )
        self.assertTrue(any("exactly one service" in e for e in self.check_with(compose_text=sidecar)))

    def test_published_port_env_file_or_volume_fails(self) -> None:
        base = COMPOSE.read_text(encoding="utf-8")
        for extra in ('    ports:\n      - "8765:8765"\n', '    env_file:\n      - ".env"\n', '    volumes:\n      - "/srv:/srv"\n'):
            text = base.replace("    pull_policy: never\n", "    pull_policy: never\n" + extra)
            self.assertTrue(self.check_with(compose_text=text), extra)

    def test_compose_has_no_live_reference_and_pinned_image_input(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        for live_name in self.data["live_names"]:
            self.assertNotIn(live_name, text)
        self.assertIn("${REHEARSAL_IMAGE_ID", text)
        self.assertIn("pull_policy: never", text)
        mentions_live = text.replace("# Same app shape", "# quote-internal shape")
        self.assertTrue(any("live name" in e for e in self.check_with(compose_text=mentions_live)))

    def test_clear_command_names_only_the_rehearsal_bucket(self) -> None:
        data = copy.deepcopy(self.data)
        data["storage"]["clear_command"] = data["storage"]["clear_command"].replace(
            "--expect-bucket sqag-rehearsal", "--expect-bucket other"
        )
        self.assertTrue(any("clear_command" in e for e in self.check_with(data)))

    def test_mini_yaml_matches_pyyaml_when_available(self) -> None:
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("PyYAML not installed")

        def normalise(value):
            if isinstance(value, dict):
                return {str(k): normalise(v) for k, v in value.items()}
            if isinstance(value, list):
                return [normalise(v) for v in value]
            return None if value is None else str(value)

        text = COMPOSE.read_text(encoding="utf-8")
        self.assertEqual(normalise(yaml.safe_load(text)), normalise(checker.parse_mini_yaml(text)))

    def test_mini_yaml_refuses_unsupported_constructs(self) -> None:
        for text in ("a: {b: 1}\n", "a: &x 1\n", "a: |\n  text\n", "a:\n  - b: 1\n"):
            with self.assertRaises(checker.MiniYamlError, msg=text):
                checker.parse_mini_yaml(text)


if __name__ == "__main__":
    unittest.main()
