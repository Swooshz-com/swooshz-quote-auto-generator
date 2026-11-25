"""Which template lays out a quote, what the run records, and that old quotes never change."""
import contextlib
import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
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
# What a Windows child process needs when a test replaces the whole environment.
WINDOWS_PROCESS_ENV = {name: os.environ[name] for name in ("SystemRoot", "ComSpec", "PATHEXT") if name in os.environ}


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


class QuoteTemplateEvidenceCompactionTest(unittest.TestCase):
    def test_an_oversized_run_record_compacts_the_template_definition_before_the_brief(self):
        standard = quote_template.load_standard_quote_template()
        record = {"id": "tpl-" + "a" * 32, "name": "Standard copy", "save_number": 3, "builtin": False,
                  "sha256": quote_template.quote_template_fingerprint(standard), "note": ""}
        definition_size, definition_sha = s.forensic_json_fingerprint(standard)

        def manifest(padding):
            return {"generation_schema_version": 1, "job_id": "job-template-compaction",
                    "quote_template": {**record, "definition": copy.deepcopy(standard)},
                    "normalized_brief": {"notes": "synthetic " + "x" * padding},
                    "approved_basis": [], "output_rows": [], "artifacts": [], "artifacts_durable": False}

        # Size the brief so the record is over the limit by half the definition: compacting the definition
        # alone must bring it under, and nothing after it in the order may be touched.
        limit = s.MAX_GENERATION_MANIFEST_BYTES - s.FORENSIC_MANIFEST_ENVELOPE_RESERVE_BYTES
        base_size, _ = s.forensic_json_fingerprint(manifest(0))
        oversized = manifest(limit - base_size + definition_size // 2)
        self.assertGreater(s.forensic_json_fingerprint(oversized)[0], limit)
        compacted = s.compact_generation_canonical_manifest(oversized)
        self.assertEqual(compacted["evidence_compaction"]["compacted_fields"], ["quote_template.definition"])
        summary = compacted["quote_template"]["definition"]
        self.assertTrue(summary["snapshot_compacted"])
        self.assertEqual((summary["canonical_size_bytes"], summary["canonical_sha256"]), (definition_size, definition_sha))
        self.assertEqual({key: value for key, value in compacted["quote_template"].items() if key != "definition"}, record)
        self.assertEqual(compacted["normalized_brief"], oversized["normalized_brief"])


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

    def download(self, result):
        url = next(item["url"] for item in result["files"] if item["name"] == "quotation.xlsx")
        with mock.patch.object(s, "configured_output_root", return_value=self.root / "out"), \
                tw.LocalRunnerServer() as runner:
            return tw.local_http_get_bytes(runner, url)

    def test_editing_or_deleting_the_template_never_changes_an_old_quote(self):
        saved = self.store.create(definition("Original", total_label="Original sum"))
        first, xlsx, manifest, _ = self.generate(saved["id"])
        before, recorded = xlsx.read_bytes(), copy.deepcopy(manifest["quote_template"])
        self.assertEqual(self.download(first), (200, before))
        edited = self.store.update(saved["id"], definition("Edited", total_label="Edited sum"), 1)
        self.assertEqual(xlsx.read_bytes(), before)
        self.assertEqual(self.download(first), (200, before))
        _, second_xlsx, second_manifest, _ = self.generate(saved["id"])
        self.assertIn("Edited sum", sheet_text(second_xlsx))
        self.assertEqual(second_manifest["quote_template"]["save_number"], 2)
        self.store.delete(saved["id"], edited["save_number"])
        self.assertEqual(xlsx.read_bytes(), before)
        self.assertEqual(self.download(first), (200, before))
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


class DatabaseProfileTemplateTest(unittest.TestCase):
    """The rehearsal and live path: the profile and its template choice are read from the database.

    The generator subprocess is stubbed as in the web suite's database generation tests, so these
    check which template the job passes and records; QuoteTemplateGenerationTest covers the workbook.
    """

    platform_consume_payload = tw.WebappServerTest.platform_consume_payload
    platform_auth_session = tw.WebappServerTest.platform_auth_session

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        url = f"sqlite:///{(self.root / 'sqag.sqlite3').as_posix()}"
        env = {"SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": "database", "SQAG_DATABASE_URL": url,
               "QUOTE_DATA_ROOT": str(self.root / "data"), "QUOTE_OUTPUT_ROOT": str(self.root / "output"),
               "QUOTE_TMP_ROOT": str(self.root / "tmp"), "QUOTE_LOG_ROOT": str(self.root / "logs")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        s.apply_sqag_storage_migrations(url)
        self.session = self.platform_auth_session("workspace-template-profiles")
        self.storage = s.app_storage_for_auth_session(self.session)
        self.storage.save_pricing_reference(tw.workspace_pricing_reference("workspace-pricing"))
        self.store = s.QuoteTemplateStore(self.storage)

    def stored(self, profile_id):
        return self.storage._read_payload("sqag_profiles", "profile_id", profile_id)

    def generate(self, profile_id):
        payload = tw.payload_with_workspace_pricing("workspace-pricing")
        payload["profile_id"] = profile_id
        for key in ("company", "tax", "quote_text", "signature", "rich_text"):
            payload.pop(key, None)
        captured = {}

        def run(command, **kwargs):
            tw.write_generator_diagnostic_fixture(command)
            captured["command"] = list(command)
            if "--quote-template" in command:
                path = Path(command[command.index("--quote-template") + 1])
                captured["template"] = json.loads(path.read_text(encoding="utf-8"))
            return s.subprocess.CompletedProcess(args=[], returncode=0, stdout="Wrote quotation.xlsx\n", stderr="")

        with mock.patch.object(s.subprocess, "run", side_effect=run):
            result = s.run_quote_job(payload, output_root=self.root / "out", tmp_root=self.root / "tmp",
                                     auth_session=self.session)
        self.assertEqual(result["status"], "completed", result)
        return result, captured

    def test_saved_profiles_choose_the_template_and_saves_without_a_choice_keep_it(self):
        saved = self.store.create(definition("Linked look", total_label="Linked total"))
        linked = tw.workspace_profile_with_layout("linked-profile")
        linked["quote_template_id"] = saved["id"]
        self.storage.save_profile(linked)
        result, captured = self.generate("linked-profile")
        self.assertEqual(captured["template"], saved["definition"])
        self.assertEqual({key: result["quote_template"][key] for key in ("id", "name", "save_number", "note")},
                         {"id": saved["id"], "name": "Linked look", "save_number": 1, "note": ""})

        # A save from a screen without the template control (today's Company Profile screen) keeps the link.
        update = {key: value for key, value in linked.items() if key != "quote_template_id"}
        update["label"] = "Linked profile renamed"
        self.storage.save_profile(update, operation="update", revision=self.stored("linked-profile")["_revision"])
        self.assertEqual((self.stored("linked-profile")["label"], self.stored("linked-profile")["quote_template_id"]),
                         ("Linked profile renamed", saved["id"]))
        self.assertEqual(self.generate("linked-profile")[1]["template"]["name"], "Linked look")

        # After the template is deleted the profile's quotes use Standard and say why.
        self.store.delete(saved["id"], 1)
        result, captured = self.generate("linked-profile")
        self.assertEqual((result["quote_template"]["id"], result["quote_template"]["note"]),
                         ("standard", s.QUOTE_TEMPLATE_DELETED_NOTE))
        self.assertEqual(captured["template"], quote_template.load_standard_quote_template())

    def generate_for_real(self, profile_id):
        """The real generator on a stored profile; returns the workbook and the PDF made from it.

        The generator finds its PDF converter on PATH. Here it is a small program whose "PDF" lists the
        workbook parts it was given, so two PDFs are equal exactly when they were made from equal workbooks.
        """
        tools = self.root / "tools"
        if not tools.is_dir():
            tools.mkdir()
            script = tools / "fake_converter.py"
            script.write_text(
                "import hashlib, pathlib, sys, zipfile\n"
                "source = pathlib.Path(sys.argv[-1]); out = pathlib.Path(sys.argv[sys.argv.index('--outdir') + 1])\n"
                "book = zipfile.ZipFile(source)\n"
                "parts = [name + ' ' + hashlib.sha256(book.read(name)).hexdigest() for name in sorted(book.namelist())]\n"
                "(out / (source.stem + '.pdf')).write_bytes(b'%PDF-1.4\\n' + '\\n'.join(parts).encode() + b'\\n%%EOF\\n')\n",
                encoding="utf-8")
            if os.name == "nt":
                (tools / "soffice.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
            else:
                launcher = tools / "soffice"
                launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
                launcher.chmod(0o755)
        payload = tw.payload_with_workspace_pricing("workspace-pricing")
        payload["profile_id"] = profile_id
        for key in ("company", "tax", "quote_text", "signature", "rich_text"):
            payload.pop(key, None)
        payload["line_items"] = [{
            "section": "Graphics", "quantity": 3, "unit": "sqm", "description": "Workspace printed graphics",
            "pricing_keyword": "workspace-row", "source_basis_line_id": "switch-back-row",
        }]
        tw.with_server_analysis_seal(payload, self.session)
        shutil.rmtree(self.root / "out", ignore_errors=True)
        with mock.patch.dict(os.environ, {**WINDOWS_PROCESS_ENV, "PATH": str(tools)}):
            result = s.run_quote_job(payload, output_root=self.root / "out", tmp_root=self.root / "tmp",
                                     auth_session=self.session, pdf_mode="workbook")
        self.assertEqual(result["status"], "completed", result)
        output = Path(result["output_dir"])
        return result, members(output / "quotation.xlsx"), (output / "quotation.pdf").read_bytes()

    def test_switching_back_gives_the_same_excel_and_pdf_as_an_untouched_profile(self):
        saved = self.store.create(definition("Linked look", total_label="Linked total"))
        for profile_id in ("untouched-profile", "switched-profile"):
            self.storage.save_profile(tw.workspace_profile_with_layout(profile_id))
        untouched, untouched_book, untouched_pdf = self.generate_for_real("untouched-profile")
        self.assertNotIn("quote_template", untouched)
        self.assertTrue(untouched_pdf.startswith(b"%PDF-1.4"))

        changed = self.storage.change_profile_quote_template("switched-profile", saved["id"], revision=1)
        with_template, template_book, template_pdf = self.generate_for_real("switched-profile")
        self.assertEqual(with_template["quote_template"]["name"], "Linked look")
        self.assertNotEqual(template_book, untouched_book)
        self.assertNotEqual(template_pdf, untouched_pdf)

        self.storage.change_profile_quote_template("switched-profile", s.OWN_LAYOUT_CHOICE, revision=changed["revision"])
        back, back_book, back_pdf = self.generate_for_real("switched-profile")
        self.assertNotIn("quote_template", back)
        self.assertEqual(back_book, untouched_book)
        self.assertEqual(back_pdf, untouched_pdf)

        # A workbook uploaded to a profile that follows a template is a layout of its own from then on.
        fresh = tw.workspace_profile_with_layout("fresh-profile")
        layout = {key: fresh.pop(key) for key in ("_pack_assets", "pack", "profile_pack") if key in fresh}
        created = self.storage.save_profile(fresh, operation="create")
        self.assertIs(created["own_layout"], False)
        uploaded = self.storage.save_profile({**fresh, **layout, "id": created["id"]}, operation="update", revision=1)
        self.assertEqual((uploaded["own_layout"], uploaded["quote_template_id"]), (True, "default"))
        self.storage.change_profile_quote_template(created["id"], s.OWN_LAYOUT_CHOICE, revision=2)
        self.assertNotIn("quote_template", self.generate_for_real(created["id"])[0])

    def test_own_layout_is_offered_exactly_where_the_server_takes_it(self):
        """Database storage without artifact storage keeps no layout workbook, so the way back is neither listed nor taken."""
        self.storage.save_profile(tw.workspace_profile_with_layout("way-back-profile"))
        changed = self.storage.change_profile_quote_template("way-back-profile", "standard", revision=1)

        def listed():
            detail = next(item for item in self.storage.list_company_profiles() if item["id"] == "way-back-profile")
            summary = next(item for item in self.storage.list_profiles() if item["id"] == "way-back-profile")
            return (detail["own_layout"], summary["own_layout"])

        self.assertEqual((changed["own_layout"], listed()), (True, (True, True)))
        for setting in ("", "local"):
            with self.subTest(setting=setting), mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": setting}):
                self.assertEqual(listed(), (False, False))
                with self.assertRaisesRegex(ValueError, "no layout of its own"):
                    self.storage.change_profile_quote_template("way-back-profile", s.OWN_LAYOUT_CHOICE, revision=2)
        with mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "local"}):
            # A pick or a save made meanwhile keeps the way back for when artifact storage is there again.
            picked = self.storage.change_profile_quote_template("way-back-profile", "default", revision=2)
            self.assertIs(picked["own_layout"], False)
            profile = tw.workspace_profile_with_layout("way-back-profile")
            for key in ("_pack_assets", "pack", "profile_pack"):
                profile.pop(key, None)
            self.assertIs(self.storage.save_profile(profile, operation="update", revision=3)["own_layout"], False)
        with mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}):
            self.assertEqual(listed(), (True, True))
        self.assertEqual(listed(), (True, True))
        back = self.storage.change_profile_quote_template("way-back-profile", s.OWN_LAYOUT_CHOICE, revision=4)
        self.assertEqual((back["own_layout"], "quote_template_id" in back), (True, False))

    def test_profiles_with_their_own_workbook_stay_legacy_and_new_ones_follow_the_default(self):
        self.storage.save_profile(tw.workspace_profile_with_layout("legacy-profile"))
        self.assertNotIn("quote_template_id", self.stored("legacy-profile"))
        result, captured = self.generate("legacy-profile")
        self.assertNotIn("--quote-template", captured["command"])
        self.assertNotIn("quote_template", result)

        fresh = tw.workspace_profile_with_layout("fresh-profile")
        for key in ("_pack_assets", "pack", "profile_pack"):
            fresh.pop(key, None)
        created = self.storage.save_profile(fresh, operation="create")
        self.assertEqual(self.stored(created["id"])["quote_template_id"], "default")
        result, captured = self.generate(created["id"])
        self.assertEqual(result["quote_template"]["id"], "standard")
        self.assertEqual(captured["template"]["name"], "Standard")
        workspace_default = self.store.create(definition("Workspace look"))
        self.store.set_default(workspace_default["id"])
        self.assertEqual(self.generate(created["id"])[0]["quote_template"]["id"], workspace_default["id"])


if __name__ == "__main__":
    unittest.main()
