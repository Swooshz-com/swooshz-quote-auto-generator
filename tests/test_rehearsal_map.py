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
COPY_COMPOSE = ROOT / "deploy" / "rehearsal" / "compose-live-copy.yaml"
MANIFEST = ROOT / "deploy" / "rehearsal" / "live-cases.json"
COPY_SCRIPT = ROOT / "scripts" / "copy_live_objects_to_rehearsal.py"
GUARD_SCRIPT = ROOT / "scripts" / "neon_branch_guard.py"


class RehearsalMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        for source in (MAP, COMPOSE, SETTINGS, COPY_COMPOSE, MANIFEST, COPY_SCRIPT, GUARD_SCRIPT):
            target = self.base / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.map_path = self.base / MAP.relative_to(ROOT)
        self.compose_path = self.base / COMPOSE.relative_to(ROOT)
        self.copy_compose_path = self.base / COPY_COMPOSE.relative_to(ROOT)
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
        planted = "sk-or-v1-" + "9f8e7d6c" * 6
        data["env_sources"]["credential_store"]["OPENROUTER_API_KEY"] = planted
        errors = self.check_with(data)
        self.assertTrue(errors)
        self.assertFalse(any(planted in e for e in errors))

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

    def test_clear_runs_the_copied_script_not_the_image_copy(self) -> None:
        storage = self.data["storage"]
        self.assertIn(f"{storage['tools_dir']}/scripts/clear_rehearsal_bucket.py", storage["clear_command"])
        self.assertNotIn("/app", storage["clear_command"])
        data = copy.deepcopy(self.data)
        data["storage"]["clear_command"] = (
            "docker exec <rehearsal app container> bash -lc "
            "'cd /app && python scripts/clear_rehearsal_bucket.py --expect-bucket sqag-rehearsal --mode clear'"
        )
        self.assertTrue(any("storage.tools_dir" in e for e in self.check_with(data)))

    def test_clear_setup_copies_script_and_map_from_the_build_folder(self) -> None:
        for dropped in ("scripts/clear_rehearsal_bucket.py", "deploy/rehearsal/resources.json"):
            data = copy.deepcopy(self.data)
            setup = data["storage"]["clear_setup"]
            data["storage"]["clear_setup"] = setup.replace(f"/root/sqag-build-<run id>/{dropped}", "/root/other")
            self.assertTrue(any("clear_setup" in e for e in self.check_with(data)), dropped)

    def test_branch_create_reassigns_owners_and_pins_the_password_reset(self) -> None:
        for needle, _step in checker.BRANCH_CREATE_STEPS:
            data = copy.deepcopy(self.data)
            for resource in data["resources"]:
                if resource["kind"] == "neon_branch":
                    resource["created_by"] = resource["created_by"].replace(needle, "")
            errors = self.check_with(data)
            self.assertTrue(any("neon-branch created_by" in e for e in errors), needle)

    def check_copy_compose(self, old: str, new: str) -> list[str]:
        text = COPY_COMPOSE.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.copy_compose_path.write_text(text.replace(old, new), encoding="utf-8")
        return checker.run_checks(self.map_path)

    def test_live_copy_app_keeps_the_rehearsal_shape_with_identity_run_values_only(self) -> None:
        cases = [
            ('SQAG_INTERNAL_WORKSPACE_ID: "${SQAG_INTERNAL_WORKSPACE_ID}"', 'SQAG_INTERNAL_WORKSPACE_ID: "ws-live"', "SQAG_INTERNAL_WORKSPACE_ID must be set as"),
            ('SQAG_INTERNAL_ALLOWED_EMAILS_ADMIN: "${SQAG_INTERNAL_ALLOWED_EMAILS_ADMIN}"', 'SQAG_INTERNAL_ALLOWED_EMAILS_ADMIN: "owner@example.com"', "ALLOWED_EMAILS_ADMIN must be set as"),
            ('SQAG_OBJECT_STORAGE_BUCKET: "sqag-rehearsal"', 'SQAG_OBJECT_STORAGE_BUCKET: "sqag-live"', "SQAG_OBJECT_STORAGE_BUCKET must equal"),
            ('pull_policy: never', 'pull_policy: always', "service settings must equal"),
            ('  quote-rehearsal-copy:', '  quote-internal:', "exactly one service"),
            ('      PORT: "8765"\n', '      PORT: "8765"\n      SQAG_EXTRA: "x"\n', "env names must be"),
            ('# Map: deploy/rehearsal/resources.json', '# Map for quote-internal', "refers to live name"),
        ]
        for old, new, expected in cases:
            errors = self.check_copy_compose(old, new)
            self.assertTrue(any(expected in e for e in errors), (expected, errors))

    def test_live_copy_section_rules(self) -> None:
        cases = [
            (lambda d: d.pop("live_copy"), "live_copy must describe"),
            (lambda d: d["live_copy"].update(ai_cap_usd=2), "ai_cap_usd must be 1"),
            (lambda d: d["live_copy"].update(app_service="quote-rehearsal"), "app_service must say rehearsal and differ"),
            (lambda d: d["live_copy"]["run_names"].pop(), "run_names must be exactly"),
            (lambda d: d["live_copy"]["run_values"].pop("SQAG_INTERNAL_WORKSPACE_ID"), "must say where SQAG_INTERNAL_WORKSPACE_ID"),
            (lambda d: d["live_copy"].update(copy_script="scripts/missing.py"), "copy_script must name a file"),
        ]
        for change, expected in cases:
            data = copy.deepcopy(self.data)
            change(data)
            errors = self.check_with(data)
            self.assertTrue(any(expected in e for e in errors), (expected, errors))

    def test_production_guard_lists_live_endpoint_and_matches_the_branches(self) -> None:
        guard = self.data["production_guard"]
        self.assertTrue(guard["forbidden_endpoints"])
        for entry in guard["forbidden_endpoints"]:
            self.assertRegex(entry["endpoint_id"], r"^ep-")
            self.assertRegex(entry["branch_id"], r"^br-")
        cases = [
            (lambda d: d.pop("production_guard"), "production_guard must list"),
            (lambda d: d["production_guard"].update(forbidden_endpoints=[]), "forbidden_endpoints must list"),
            (lambda d: d["production_guard"]["forbidden_endpoints"][0].update(endpoint_id="weathered"), "needs an endpoint_id"),
            (lambda d: d["production_guard"]["forbidden_endpoints"][0].pop("branch_id"), "needs a branch_id"),
            (lambda d: d["production_guard"].update(neon_project="other"), "neon_project must be the project"),
            (lambda d: d["production_guard"].update(branch_prefix="copy-"), "branch_prefix must say rehearsal"),
            (lambda d: d["production_guard"].update(script="scripts/missing.py"), "script must name a file"),
            (lambda d: d["production_guard"].update(source=""), "source must say where"),
        ]
        for change, expected in cases:
            data = copy.deepcopy(self.data)
            change(data)
            errors = self.check_with(data)
            self.assertTrue(any(expected in e for e in errors), (expected, errors))

    def test_copy_stays_up_for_uat_and_is_deleted_after_the_live_deploy(self) -> None:
        resources = {r["id"]: r for r in self.data["resources"]}
        for rid in ("coolify-copy-service", "neon-data-branch"):
            self.assertIn("step 9", resources[rid]["delete"])
            self.assertIn("after the live deploy", resources[rid]["delete"])
        uat = resources["uat-login-password"]
        self.assertEqual((uat["kind"], uat["lifetime"], uat["name"]), ("credential_store_entry", "per_run", "SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD"))

    def test_data_branch_resets_its_own_passwords_and_stays_a_rehearsal_branch(self) -> None:
        for needle, _step in checker.DATA_BRANCH_CREATE_STEPS:
            data = copy.deepcopy(self.data)
            branch = next(r for r in data["resources"] if r["id"] == "neon-data-branch")
            branch["created_by"] = branch["created_by"].replace(needle, "")
            self.assertTrue(any("neon-data-branch created_by" in e for e in self.check_with(data)), needle)
        data = copy.deepcopy(self.data)
        branch = next(r for r in data["resources"] if r["id"] == "neon-data-branch")
        branch["delete"] = "Delete the branch."
        self.assertTrue(any("neon-data-branch delete" in e for e in self.check_with(data)))
        branch["branch_type"] = "full copy"
        self.assertTrue(any("branch_type must start" in e for e in self.check_with(data)))

    def test_service_changes_go_through_the_deploy_api(self) -> None:
        data = copy.deepcopy(self.data)
        for resource in data["resources"]:
            if resource["kind"] == "coolify_service":
                resource["created_by"] = "Operator in Coolify; restart the service after each change."
        self.assertTrue(any("POST /api/v1/deploy" in e for e in self.check_with(data)))

    def test_proxy_cidr_is_derived_from_the_service_network(self) -> None:
        for value in (
            "10.0.10.0/24",
            "The subnet of the server's Coolify proxy network, read on the server with docker network inspect.",
            "Derived from the rehearsal service's own network, never a fixed value, e.g. 10.0.10.0/24.",
        ):
            data = copy.deepcopy(self.data)
            data["run_values"]["SQAG_TRUSTED_PROXY_CIDRS"] = value
            errors = self.check_with(data)
            self.assertTrue(any("SQAG_TRUSTED_PROXY_CIDRS" in e for e in errors), value)

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
