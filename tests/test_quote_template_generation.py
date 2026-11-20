"""Which template lays out a quote, what the run records, and that old quotes never change."""
import contextlib
import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import quote_template
from tests import test_webapp as tw
from webapp import server as s


ROOT = Path(__file__).resolve().parents[1]


def definition(name, *, total_label=None):
    template = copy.deepcopy(quote_template.load_standard_quote_template())
    template["name"] = name
    if total_label is not None:
        template["labels"]["total"] = total_label
    return template


def members(path):
    with zipfile.ZipFile(path) as archive:
        return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in sorted(archive.namelist())}


def sheet_text(path):
    with zipfile.ZipFile(path) as archive:
        return "".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                       if name.startswith("xl/worksheets/sheet") or name == "xl/sharedStrings.xml")


class QuoteTemplateResolutionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        url = "sqlite:///" + (Path(directory.name) / "resolve.sqlite3").as_posix()
        patcher = mock.patch.dict(os.environ, {"APP_MODE": "local", "SQAG_DISABLE_DOTENV": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        s.apply_sqag_storage_migrations(url)
        self.store = s.QuoteTemplateStore(s.DatabaseSqagStorage(url, "workspace-synthetic", "admin", "admin-user"))

    def resolved(self, choice, store="saved"):
        result = s.resolve_quote_template(choice, self.store if store == "saved" else None)
        return result.record.get("id", "legacy"), result.note

    def test_resolution_table(self):
        chosen = self.store.create(definition("Chosen"))
        fallback = self.store.create(definition("Workspace default"))
        gone = self.store.create(definition("Gone"))
        self.store.delete(gone["id"], 1)
        deleted = s.QUOTE_TEMPLATE_DELETED_NOTE
        rows = (
            # (profile choice, workspace default, expected template, expected note)
            (None, None, "legacy", ""),
            ("standard", None, "standard", ""),
            ("default", None, "standard", ""),
            (chosen["id"], None, chosen["id"], ""),
            (gone["id"], None, "standard", deleted),
            (None, fallback["id"], "legacy", ""),
            ("standard", fallback["id"], "standard", ""),
            ("default", fallback["id"], fallback["id"], ""),
            (chosen["id"], fallback["id"], chosen["id"], ""),
            (gone["id"], fallback["id"], fallback["id"], deleted),
        )
        for choice, default, expected, note in rows:
            self.store.set_default(default or "standard")
            with self.subTest(choice=choice, default=default):
                self.assertEqual(self.resolved(choice), (expected, note))
        # Local-file workspaces have no saved templates: only Standard or the profile layout.
        self.assertEqual(self.resolved("default", store=None), ("standard", ""))
        self.assertEqual(self.resolved(chosen["id"], store=None), ("standard", deleted))
        self.assertEqual(self.resolved(None, store=None), ("legacy", ""))

    def test_record_names_the_exact_template_used(self):
        chosen = self.store.create(definition("Recorded"))
        result = s.resolve_quote_template(chosen["id"], self.store)
        self.assertEqual(result.record, {"id": chosen["id"], "name": "Recorded", "save_number": 1, "builtin": False,
                                         "sha256": chosen["fingerprint"]})
        self.assertEqual(result.definition, chosen["definition"])
        standard = s.resolve_quote_template("standard", self.store)
        self.assertEqual(standard.record["sha256"],
                         quote_template.quote_template_fingerprint(quote_template.load_standard_quote_template()))
        self.assertTrue(standard.record["builtin"])


class QuoteTemplateGenerationTest(unittest.TestCase):
    """Real generator runs through the web job path with a profile that names a template."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=str(tw.test_temp_root()))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        url = "sqlite:///" + (self.root / "templates.sqlite3").as_posix()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.dict(os.environ, tw.isolated_env(
            SQAG_DISABLE_DOTENV="1", QUOTE_DATA_ROOT=str(self.root / "data")), clear=True))
        # The synthetic fixture profile and pricing reference the web tests use; no company data.
        empty_bundled = self.root / "empty-bundled-pricing"
        empty_bundled.mkdir()
        fixture_pricing_root = stack.enter_context(tw.local_pricing_fixture_root())
        for name, value in (
            ("DEFAULT_PROFILE_ID", "synthetic-exhibition-fixture-template"),
            ("DEFAULT_PRICING_REFERENCE_ID", "synthetic-exhibition-fixture-pricing"),
            ("BUNDLED_DEFAULT_PROFILE_ID", "synthetic-exhibition-fixture-template"),
            ("BUNDLED_DEFAULT_PRICING_REFERENCE_ID", "synthetic-exhibition-fixture-pricing"),
        ):
            stack.enter_context(mock.patch.object(s, name, value))
        stack.enter_context(mock.patch.object(s, "profiles_root", return_value=tw.QUOTE_GENERATOR_FIXTURE_ROOT / "profiles"))
        stack.enter_context(mock.patch.object(s, "pricing_references_root", return_value=fixture_pricing_root))
        stack.enter_context(mock.patch.object(s, "bundled_pricing_references_root", return_value=empty_bundled))
        s.apply_sqag_storage_migrations(url)
        self.storage = s.DatabaseSqagStorage(url, "workspace-synthetic", "admin", "admin-user")
        self.store = s.QuoteTemplateStore(self.storage)
        self.choice = None
        real_profile = s.profile_defaults_source_for_payload

        def profile_with_choice(*args, **kwargs):
            profile = real_profile(*args, **kwargs)
            if self.choice is None:
                return profile
            return dataclasses.replace(profile, config={**profile.config, "quote_template_id": self.choice})

        stack.enter_context(mock.patch.object(s, "profile_defaults_source_for_payload", side_effect=profile_with_choice))
        stack.enter_context(mock.patch.object(s, "database_profile_storage_for_auth_session", return_value=self.storage))
        self.finish = stack.enter_context(mock.patch.object(
            s, "finish_generation_forensics", wraps=s.finish_generation_forensics))
        self.commands = []
        real_run = s.subprocess.run

        def run(command, **kwargs):
            self.commands.append(list(command))
            return real_run(command, **kwargs)

        stack.enter_context(mock.patch.object(s.subprocess, "run", side_effect=run))

    def generate(self, choice):
        self.choice = choice
        payload = tw.valid_payload()
        payload["line_items"] = [{
            "section": "Synthetic Floors", "quantity": 12, "unit": "sqm",
            "description": "sqm synthetic carpet tile",
            "pricing_keyword": "synthetic-floors-synthetic-carpet-tile",
            "source_basis_line_id": "template-carpet",
        }]
        tw.with_server_analysis_seal(payload)
        result = s.run_quote_job(payload, output_root=self.root / "out", tmp_root=self.root / "tmp")
        self.assertEqual(result["status"], "completed", result)
        manifest = self.finish.call_args.kwargs["canonical_manifest"]
        return result, Path(result["output_dir"]) / "quotation.xlsx", manifest, self.commands[-1]

    def test_a_profile_without_a_choice_keeps_its_own_layout_unchanged(self):
        result, xlsx, manifest, command = self.generate(None)
        self.assertNotIn("--quote-template", command)
        self.assertNotIn("quote_template", result)
        self.assertEqual(manifest["quote_template"], {"legacy_layout": True})
        # The same brief through the plain generator gives the same workbook, member for member.
        direct = self.root / "direct"
        cli = [sys.executable, str(ROOT / "scripts" / "generate_quote.py"), *command[2:]]
        cli[cli.index("--out") + 1] = str(direct)
        completed = subprocess.run(cli, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(members(xlsx), members(direct / "quotation.xlsx"))

    def test_saved_template_lays_out_the_quote_and_is_recorded_in_full(self):
        saved = self.store.create(definition("Wide columns", total_label="Grand sum (sqm)"))
        result, xlsx, manifest, command = self.generate(saved["id"])
        template_path = Path(command[command.index("--quote-template") + 1])
        self.assertEqual(json.loads(template_path.read_text(encoding="utf-8")), saved["definition"])
        self.assertIn("Grand sum (sqm)", sheet_text(xlsx))
        self.assertNotIn("m2", sheet_text(xlsx))
        expected = {"id": saved["id"], "name": "Wide columns", "save_number": 1, "builtin": False,
                    "sha256": saved["fingerprint"], "note": ""}
        self.assertEqual(result["quote_template"], expected)
        self.assertEqual(manifest["quote_template"], {**expected, "definition": saved["definition"]})

    def test_editing_or_deleting_the_template_never_changes_an_old_quote(self):
        saved = self.store.create(definition("Original", total_label="Original sum"))
        _, xlsx, manifest, _ = self.generate(saved["id"])
        before, recorded = xlsx.read_bytes(), copy.deepcopy(manifest["quote_template"])
        edited = self.store.update(saved["id"], definition("Edited", total_label="Edited sum"), 1)
        self.assertEqual(xlsx.read_bytes(), before)
        _, second_xlsx, second_manifest, _ = self.generate(saved["id"])
        self.assertIn("Edited sum", sheet_text(second_xlsx))
        self.assertEqual(second_manifest["quote_template"]["save_number"], 2)
        self.store.delete(saved["id"], edited["save_number"])
        self.assertEqual(xlsx.read_bytes(), before)
        self.assertEqual(recorded["definition"]["name"], "Original")
        result, third_xlsx, third_manifest, _ = self.generate(saved["id"])
        self.assertEqual(result["quote_template"]["id"], "standard")
        self.assertEqual(result["quote_template"]["note"], s.QUOTE_TEMPLATE_DELETED_NOTE)
        self.assertEqual(third_manifest["quote_template"]["note"], s.QUOTE_TEMPLATE_DELETED_NOTE)
        self.assertNotIn("Edited sum", sheet_text(third_xlsx))

    def test_formula_like_template_text_stays_plain_text(self):
        saved = self.store.create(definition("Guarded", total_label="=SUM(A1:A9)"))
        _, xlsx, _, _ = self.generate(saved["id"])
        text = sheet_text(xlsx)
        self.assertNotIn("SUM(A1:A9)</f>", text)
        self.assertIn("SUM(A1:A9)", text)


if __name__ == "__main__":
    unittest.main()
