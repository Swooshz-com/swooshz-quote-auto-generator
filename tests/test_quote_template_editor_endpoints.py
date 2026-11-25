"""Server routes behind the Quote Templates editor, over real loopback HTTP; all identities and data are fake."""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import io
import zipfile
from unittest import mock

from scripts import quote_template
from tests import test_internal_password_auth_webapp as auth_http
from tests import test_quote_template_permissions as permissions_test
from webapp import server as s

# The password-login host stubs the log writer and empties the environment; these are the real ones.
WRITE_LOCAL_LOG = s.write_local_log
WINDOWS_PROCESS_ENV = {name: os.environ[name] for name in ("SystemRoot", "ComSpec", "PATHEXT") if name in os.environ}


def definition(name):
    template = copy.deepcopy(quote_template.load_standard_quote_template())
    template["name"] = name
    return template


def reset_rate_limits():
    with s.RATE_LIMIT_LOCK:
        s.RATE_LIMIT_BUCKETS.clear()
        overflow = getattr(s, "RATE_LIMIT_OVERFLOW_BUCKETS", None)
        if overflow is not None:
            overflow.clear()
        if hasattr(s, "RATE_LIMIT_LAST_PRUNE_AT"):
            s.RATE_LIMIT_LAST_PRUNE_AT = 0.0


class QuoteTemplateEditorEndpointsTest(permissions_test.QuoteTemplatePermissionsTest):
    """Reuses the permissions test's sqlite + password-login host; its own tests are not re-run here."""

    test_route_permission_table = None
    test_staff_list_shows_names_only_and_bad_requests_are_refused = None
    test_refused_saves_say_why_without_internals = None
    test_profile_links_follow_profile_privacy = None

    def setUp(self):
        super().setUp()
        reset_rate_limits()
        self.addCleanup(reset_rate_limits)

    def raw(self, headers, method, route, body=None):
        return self.host.http(method, route, headers, json.dumps(body) if body is not None else None)

    def temp_dir(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        return Path(folder)

    def own_log_root(self):
        """Server logs of this test only, so a returned reference can be looked up in them."""
        root = self.temp_dir()
        for patcher in (mock.patch.object(s, "configured_log_root", return_value=root),
                        mock.patch.object(s, "write_local_log", WRITE_LOCAL_LOG)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return root

    def own_tmp_root(self):
        root = self.temp_dir()
        patcher = mock.patch.object(s, "configured_tmp_root", return_value=root)
        patcher.start()
        self.addCleanup(patcher.stop)
        return root

    def logged(self, root, reference):
        """The one server log entry that holds the reference the browser was given."""
        self.assertRegex(reference, r"^ERR-[0-9A-F]{8}$")
        records = [json.loads(line) for path in root.rglob("*.jsonl")
                   for line in path.read_text(encoding="utf-8").splitlines()]
        matches = [record for record in records if record["details"].get("error_reference") == reference]
        self.assertEqual(len(matches), 1, f"{reference} is not in the server log")
        return matches[0]

    def converter(self, body):
        """A folder to use as the whole PATH, holding a `soffice` that runs `body`; None for no converter at all.

        The preview child finds its PDF converter on PATH, so the real child process and the real conversion
        code run; only LibreOffice itself is replaced.
        """
        if os.name == "nt":
            installed = [path for path in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                                           r"C:\Program Files (x86)\LibreOffice\program\soffice.exe") if Path(path).exists()]
            if installed:
                self.skipTest("This Windows machine has LibreOffice at a fixed path; CI (Linux) runs this test.")
        folder = self.temp_dir()
        if body is not None:
            script = folder / "fake_converter.py"
            script.write_text("import pathlib, sys\nout = pathlib.Path(sys.argv[sys.argv.index('--outdir') + 1])\n"
                              "pdf = out / (pathlib.Path(sys.argv[-1]).stem + '.pdf')\n" + body + "\n", encoding="utf-8")
            if os.name == "nt":
                (folder / "soffice.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
            else:
                launcher = folder / "soffice"
                launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
                launcher.chmod(0o755)
        return mock.patch.dict(os.environ, {**WINDOWS_PROCESS_ENV, "PATH": str(folder)})

    def test_preview_sends_the_sample_quote_as_a_download(self):
        admin = self.login(auth_http.email())
        draft = definition("Draft")
        status, headers, body = self.raw(admin, "POST", "/api/settings/quote-templates/preview",
                                         {"definition": draft, "format": "xlsx"})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(headers.get("Content-Type"), s.QUOTE_TEMPLATE_PREVIEW_TYPES["xlsx"])
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="quote-template-preview.xlsx"')
        with zipfile.ZipFile(io.BytesIO(body)) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode()
            strings = workbook.read("xl/sharedStrings.xml").decode() if "xl/sharedStrings.xml" in workbook.namelist() else ""
        self.assertIn("Sample booth wall panels with printed graphics", sheet + strings)

    def test_preview_pdf_unavailable_and_failures_answer_plainly(self):
        admin = self.login(auth_http.email())
        route = "/api/settings/quote-templates/preview"
        with mock.patch.object(s, "render_quote_template_preview", side_effect=s.QuoteTemplatePreviewUnavailable()):
            status, body = self.call(admin, "POST", route, {"definition": definition("Draft"), "format": "pdf"})
        self.assertEqual((status, body["errors"]),
                         (503, ["PDF preview is not available on this server right now. Use Preview Excel instead."]))
        with mock.patch.object(s, "render_quote_template_preview", return_value=b"%PDF-1.4 synthetic"):
            status, headers, raw = self.raw(admin, "POST", route, {"definition": definition("Draft"), "format": "pdf"})
        self.assertEqual((status, raw), (200, b"%PDF-1.4 synthetic"))
        with mock.patch.object(s, "render_quote_template_preview", side_effect=RuntimeError("synthetic crash /tmp/x")):
            status, body = self.call(admin, "POST", route, {"definition": definition("Draft"), "format": "xlsx"})
        self.assertEqual(status, 500)
        self.assertRegex(body.get("error_reference", ""), r"^ERR-[0-9A-F]{8}$")
        self.assertNotIn("synthetic crash", json.dumps(body))
        for bad in ("docx", "", 7, None, [], ["xlsx"], {"xlsx": 1}):
            reset_rate_limits()  # more checks than the 6-a-minute preview limit allows
            with self.subTest(format=bad), mock.patch.object(s, "render_quote_template_preview") as render:
                status, body = self.call(admin, "POST", route, {"definition": definition("Draft"), "format": bad})
                self.assertEqual(status, 400, body)
                render.assert_not_called()
        # A broken draft is refused before anything renders, with the reader's own words.
        broken = definition("Draft")
        broken["blocks"] = []
        with mock.patch.object(s, "render_quote_template_preview") as render:
            status, body = self.call(admin, "POST", route, {"definition": broken, "format": "xlsx"})
        self.assertEqual(status, 400, body)
        render.assert_not_called()

    def test_preview_pdf_comes_from_the_real_child_and_its_failures_are_logged(self):
        admin = self.login(auth_http.email())
        log_root, tmp_root = self.own_log_root(), self.own_tmp_root()
        route = "/api/settings/quote-templates/preview"
        request = {"definition": definition("Draft"), "format": "pdf"}

        with self.converter(None):
            status, body = self.call(admin, "POST", route, request)
        self.assertEqual((status, body["errors"]),
                         (503, ["PDF preview is not available on this server right now. Use Preview Excel instead."]))

        with self.converter("pdf.write_bytes(b'%PDF-1.4 synthetic converter')"):
            status, headers, raw = self.raw(admin, "POST", route, request)
        self.assertEqual((status, raw), (200, b"%PDF-1.4 synthetic converter"))
        self.assertEqual(headers.get("Content-Type"), "application/pdf")

        failures = (
            # (case, what the converter does)
            ("converter fails", "raise SystemExit(1)"),
            ("converter fails after writing half a file", "pdf.write_bytes(b'%PDF-1.4 half'); raise SystemExit(1)"),
            ("converter says it worked but wrote nothing", "pass"),
        )
        for case, converter in failures:
            with self.subTest(case=case):
                with self.converter(converter):
                    status, body = self.call(admin, "POST", route, request)
                self.assertEqual(status, 500, body)
                record = self.logged(log_root, body["error_reference"])
                self.assertEqual(record["event"], "quote_template_preview_failed")
                self.assertEqual(record["details"]["preview_format"], "pdf")
                self.assertEqual(record["details"]["exit_code"], 1)
        self.assertEqual(list(tmp_root.iterdir()), [], "preview folders are removed")

    def test_preview_child_that_is_too_slow_or_crashes_is_refused_and_logged(self):
        admin = self.login(auth_http.email())
        log_root, tmp_root = self.own_log_root(), self.own_tmp_root()
        route = "/api/settings/quote-templates/preview"
        request = {"definition": definition("Draft"), "format": "xlsx"}

        # The real child, with less time than a Python process needs to start.
        with mock.patch.object(s, "QUOTE_TEMPLATE_PREVIEW_TIMEOUT_SECONDS", 0.01):
            status, body = self.call(admin, "POST", route, request)
        self.assertEqual(status, 500, body)
        self.assertEqual(self.logged(log_root, body["error_reference"])["details"]["error_type"], "TimeoutExpired")

        # A child that leaves a file behind and then crashes: the file is never sent.
        crashing = self.temp_dir() / "crashing_preview.py"
        crashing.write_text(
            "import pathlib, sys\nout = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\nout.mkdir(parents=True)\n"
            "(out / 'quote-template-preview.xlsx').write_bytes(b'PK half a workbook')\n"
            "raise RuntimeError('synthetic secret detail')\n", encoding="utf-8")
        with mock.patch.object(s, "QUOTE_TEMPLATE_PREVIEW_PATH", crashing):
            status, body = self.call(admin, "POST", route, request)
        self.assertEqual(status, 500, body)
        record = self.logged(log_root, body["error_reference"])
        self.assertEqual((record["details"]["exit_code"], record["details"]["preview_format"]), (1, "xlsx"))
        self.assertNotIn("synthetic secret detail", json.dumps([record, body]))
        self.assertEqual(list(tmp_root.iterdir()), [], "preview folders are removed")

    def test_any_unexpected_failure_answers_with_a_reference_the_server_logged(self):
        admin = self.login(auth_http.email())
        saved = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Saved")})[1]["template"]
        log_root = self.own_log_root()
        base = "/api/settings/quote-templates"
        # (store method that breaks, method, route, body)
        table = (
            ("list", "GET", base, None),
            ("get", "GET", f"{base}/{saved['id']}", None),
            ("create", "POST", base, {"definition": definition("New")}),
            ("update", "POST", f"{base}/{saved['id']}", {"definition": definition("New"), "save_number": 1}),
            ("set_default", "POST", f"{base}/default", {"template_id": saved["id"]}),
            ("delete", "DELETE", f"{base}/{saved['id']}?save_number=1", None),
        )
        for store_method, method, route, body in table:
            with self.subTest(route=f"{method} {route}"):
                with mock.patch.object(s.QuoteTemplateStore, store_method, side_effect=RuntimeError("synthetic crash /tmp/x")):
                    status, answer = self.call(admin, method, route, body)
                self.assertEqual(status, 500, answer)
                record = self.logged(log_root, answer["error_reference"])
                self.assertEqual(record["details"]["reason"], "quote_template_request_failed")
                self.assertNotIn("synthetic crash", json.dumps([record, answer]))
        # A database fault while the profile list reads the template names: 503, logged, never a guessed list.
        with mock.patch.object(s.QuoteTemplateStore, "_table_present", side_effect=RuntimeError("synthetic crash")):
            status, answer = self.call(admin, "GET", "/api/profiles")
        self.assertEqual(status, 503, answer)
        self.assertEqual(self.logged(log_root, answer["error_reference"])["details"]["reason"], "quote_templates_unavailable")

    def test_preview_is_rate_limited_per_client_before_rendering(self):
        admin = self.login(auth_http.email())
        route = "/api/settings/quote-templates/preview"
        limit = s.POST_RATE_LIMITS[route]
        self.assertEqual(limit, 6)
        for attempt in range(limit):
            with self.subTest(attempt=attempt):
                self.assertEqual(self.call(admin, "POST", route, {"definition": definition("Draft")})[0], 200)
        with mock.patch.object(s, "render_quote_template_preview") as render:
            status, _ = self.call(admin, "POST", route, {"definition": definition("Draft"), "format": "pdf"})
        self.assertEqual(status, 429)
        render.assert_not_called()
        # Saving is counted separately, so a burst of previews never blocks the save.
        self.assertEqual(self.call(admin, "POST", "/api/settings/quote-templates",
                                   {"definition": definition("Saved after previews")})[0], 200)

    def test_operator_cannot_preview_and_admin_list_carries_layout_and_usage(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        with mock.patch.object(s, "render_quote_template_preview") as render:
            status, _ = self.call(operator, "POST", "/api/settings/quote-templates/preview",
                                  {"definition": definition("Draft"), "format": "xlsx"})
        self.assertEqual(status, 403)
        render.assert_not_called()
        status, body = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Blue")})
        blue = body["template"]
        inspection = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")
        for profile_id, choice in (("synthetic-a", blue["id"]), ("synthetic-b", "default"), ("synthetic-c", "standard")):
            saved = inspection.save_profile(s.normalize_profile_payload({"id": profile_id, "defaults": {}}))
            payload = inspection._read_payload("sqag_profiles", "profile_id", saved["id"])
            payload["quote_template_id"] = choice
            inspection._upsert_payload("sqag_profiles", "profile_id", saved["id"], payload)
        listed = self.call(admin, "GET", "/api/settings/quote-templates")[1]
        by_name = {item["name"]: item for item in listed["templates"]}
        self.assertEqual((by_name["Standard"]["usage"], by_name["Blue"]["usage"]), (2, 1))  # "default" follows Standard
        self.assertEqual(by_name["Blue"]["definition"]["name"], "Blue")
        self.assertEqual(self.call(admin, "POST", "/api/settings/quote-templates/default", {"template_id": blue["id"]})[0], 200)
        by_name = {item["name"]: item for item in self.call(admin, "GET", "/api/settings/quote-templates")[1]["templates"]}
        self.assertEqual((by_name["Standard"]["usage"], by_name["Blue"]["usage"]), (1, 2))
        staff = self.call(operator, "GET", "/api/settings/quote-templates")[1]
        self.assertNotIn("definition", json.dumps(staff))
        self.assertNotIn("usage", json.dumps(staff))
        for item in staff["templates"] + listed["templates"]:
            self.assertNotIn("synthetic-a", json.dumps(item))

    def test_profile_list_sends_template_names_for_the_dropdown(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        # Another workspace's template, and its default, never show here.
        other = s.DatabaseSqagStorage(self.url, "workspace-other", "admin", "other-admin")
        theirs = s.QuoteTemplateStore(other).create(definition("Theirs"))
        s.QuoteTemplateStore(other).set_default(theirs["id"])
        status, body = self.call(operator, "GET", "/api/profiles")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["quote_templates"], {"available": True, "default_template_id": "standard",
                                                   "templates": [{"id": "standard", "name": "Standard", "builtin": True}]})
        blue = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Blue")})[1]["template"]
        self.call(admin, "POST", "/api/settings/quote-templates/default", {"template_id": blue["id"]})
        choices = self.call(operator, "GET", "/api/profiles")[1]["quote_templates"]
        self.assertEqual(choices["default_template_id"], blue["id"])
        self.assertEqual([(item["name"], item["builtin"]) for item in choices["templates"]],
                         [("Standard", True), ("Blue", False)])
        self.assertNotIn("definition", json.dumps(choices))
        others = s.quote_template_choices(other)
        self.assertEqual(([item["name"] for item in others["templates"]], others["default_template_id"]),
                         (["Standard", "Theirs"], theirs["id"]))
        # The page loads this list every time, so the names take one connection and no layouts are read.
        storage = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")
        opened, original = [], s.DatabaseSqagStorage.connection

        def counted(inner, *args, **kwargs):
            opened.append(1)
            return original(inner, *args, **kwargs)

        with mock.patch.object(s.DatabaseSqagStorage, "connection", counted), \
                mock.patch.object(s.QuoteTemplateStore, "_record", side_effect=AssertionError("a layout was read")):
            self.assertEqual(s.quote_template_choices(storage), choices)
        self.assertEqual(len(opened), 1)

    def test_profile_list_works_before_migration_013(self):
        """The live database has no template table yet: the page must load, with the dropdown locked."""
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        status, body = self.call(operator, "POST", "/api/settings/profiles", {
            "operation": "create", "id": "synthetic-own", "label": "Synthetic own", "defaults": {}})
        self.assertEqual(status, 200, body)
        storage = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")
        with storage.connection() as connection:
            connection.execute("drop table sqag_quote_templates")
            connection.commit()
        for who in (admin, operator):
            status, listed = self.call(who, "GET", "/api/profiles")
            self.assertEqual(status, 200, listed)
            self.assertEqual(listed["quote_templates"], {"available": False, "default_template_id": "standard",
                                                         "templates": [{"id": "standard", "name": "Standard", "builtin": True}]})
        self.assertEqual([item["label"] for item in listed["company_profiles"]], ["Synthetic own"])
        # Settings says the same, with a reference the server logged.
        log_root = self.own_log_root()
        status, answer = self.call(admin, "GET", "/api/settings/quote-templates")
        self.assertEqual(status, 503, answer)
        self.assertEqual(self.logged(log_root, answer["error_reference"])["details"]["reason"], "quote_templates_not_migrated")

    def test_template_operation_changes_only_the_template(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        blue = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Blue")})[1]["template"]
        profiles = "/api/settings/profiles"
        status, body = self.call(operator, "POST", profiles, {
            "operation": "create", "id": "synthetic-own", "label": "Synthetic own", "defaults": {"payment_terms": "Net 30"}})
        self.assertEqual(status, 200, body)
        own = body["profile"]
        inspection = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")
        before = inspection._read_payload("sqag_profiles", "profile_id", own["id"])
        status, body = self.call(operator, "POST", profiles, {
            "operation": "template", "id": own["id"], "quote_template_id": blue["id"], "revision": own["revision"],
            "label": "Ignored rename"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["profile"]["revision"], own["revision"] + 1)
        after = inspection._read_payload("sqag_profiles", "profile_id", own["id"])
        self.assertEqual(after["quote_template_id"], blue["id"])
        self.assertEqual({k: v for k, v in after.items() if k not in {"quote_template_id", "_revision"}},
                         {k: v for k, v in before.items() if k not in {"quote_template_id", "_revision"}})
        refusals = (
            ("old revision", {"quote_template_id": "standard", "revision": own["revision"]}, 409),
            ("no revision", {"quote_template_id": "standard"}, 400),
            ("unknown template", {"quote_template_id": "tpl-" + "f" * 32, "revision": own["revision"] + 1}, 400),
            ("malformed template", {"quote_template_id": "../x", "revision": own["revision"] + 1}, 400),
        )
        for label, extra, expected in refusals:
            with self.subTest(refusal=label):
                status, body = self.call(operator, "POST", profiles, {"operation": "template", "id": own["id"], **extra})
                self.assertEqual(status, expected, body)
                self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", own["id"]), after)
        # An admin cannot change another member's personal profile through this operation either.
        status, body = self.call(admin, "POST", profiles, {
            "operation": "template", "id": own["id"], "quote_template_id": "standard", "revision": own["revision"] + 1})
        self.assertEqual(status, 404, body)
        self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", own["id"]), after)
        self.assertEqual(self.call(admin, "POST", profiles, {
            "operation": "template", "id": "", "quote_template_id": "standard", "revision": 0})[0], 400)


    def test_a_profile_can_be_switched_back_to_its_own_layout(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        blue = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Blue")})[1]["template"]
        profiles = "/api/settings/profiles"
        inspection = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")

        def stored(profile_id):
            return inspection._read_payload("sqag_profiles", "profile_id", profile_id)

        def listed(headers, profile_id):
            body = self.call(headers, "GET", "/api/profiles")[1]
            return next((item for item in body["company_profiles"] if item["id"] == profile_id), None)

        def create(headers, label, *, legacy):
            status, body = self.call(headers, "POST", profiles, {"operation": "create", "id": "x", "label": label, "defaults": {}})
            self.assertEqual(status, 200, body)
            if legacy:  # as every profile saved before templates existed
                payload = stored(body["profile"]["id"])
                del payload["quote_template_id"]
                inspection._upsert_payload("sqag_profiles", "profile_id", payload["id"], payload)
            return body["profile"]

        def choose(headers, profile_id, choice, revision):
            reset_rate_limits()  # more profile changes than one minute allows
            return self.call(headers, "POST", profiles, {
                "operation": "template", "id": profile_id, "quote_template_id": choice, "revision": revision})

        own = create(operator, "Synthetic own", legacy=True)
        untouched = stored(own["id"])
        self.assertIs(listed(operator, own["id"])["own_layout"], True)
        for choice in (blue["id"], "default", "standard"):  # whatever was picked, and however often
            with self.subTest(choice=choice):
                revision = stored(own["id"])["_revision"]
                status, body = choose(operator, own["id"], choice, revision)
                self.assertEqual((status, body["profile"]["own_layout"]), (200, True), body)
                # A save from a screen without the template control keeps both the template and the way back.
                status, body = self.call(operator, "POST", profiles, {
                    "operation": "update", "id": own["id"], "label": "Synthetic own", "defaults": {}, "revision": revision + 1})
                self.assertEqual(status, 200, body)
                self.assertEqual(stored(own["id"])["quote_template_id"], choice)
                self.assertEqual((listed(operator, own["id"])["own_layout"], listed(operator, own["id"])["quote_template_id"]), (True, choice))
                before = stored(own["id"])
                for label, headers, expected, stale in (("stale revision", operator, 409, revision), ("another member", admin, 404, revision + 2)):
                    with self.subTest(refused=label):
                        status, body = choose(headers, own["id"], "own", stale)
                        self.assertEqual(status, expected, body)
                        self.assertEqual(stored(own["id"]), before)
                self.assertEqual(self.call(operator, "POST", profiles, {
                    "operation": "template", "id": own["id"], "quote_template_id": "own"})[0], 400, "no revision")
                status, body = choose(operator, own["id"], "own", revision + 2)
                self.assertEqual((status, body["profile"]["own_layout"], body["profile"]["revision"]), (200, True, revision + 3), body)
                self.assertNotIn("quote_template_id", body["profile"])
                kept = {"_revision", "saved_at"}
                self.assertEqual({k: v for k, v in stored(own["id"]).items() if k not in kept},
                                 {k: v for k, v in untouched.items() if k not in kept})
                self.assertNotIn("quote_template_id", listed(operator, own["id"]))

        # A shared profile: an admin may switch it back, an operator may not.
        shared = create(admin, "Synthetic shared", legacy=True)
        status, body = self.call(admin, "POST", profiles, {
            "operation": "access", "id": shared["id"], "revision": shared["revision"],
            "access": {"version": 1, "owner_user_id": None, "visibility": "shared"}})
        self.assertEqual(status, 200, body)
        revision = body["profile"]["revision"]
        self.assertEqual(choose(admin, shared["id"], blue["id"], revision)[0], 200)
        before = stored(shared["id"])
        self.assertEqual(choose(operator, shared["id"], "own", revision + 1)[0], 404)
        self.assertEqual(stored(shared["id"]), before)
        self.assertIs(listed(operator, shared["id"])["own_layout"], True)
        # The mark itself stays with the stored row: not in what the page gets, nor in what a quote records.
        self.assertIs(before["_own_layout"], True)
        self.assertNotIn("_own_layout", listed(admin, shared["id"]))
        self.assertNotIn("_own_layout", inspection.profile_detail(shared["id"], source="company"))
        # Nor in any settings answer: list, export, and what each kind of change sends back.
        access = {"version": 1, "owner_user_id": None, "visibility": "shared"}
        for label, method, route, body in (
            ("list", "GET", profiles, None),
            ("export", "GET", f"{profiles}/{shared['id']}/export.json", None),
            ("template", "POST", profiles, {"operation": "template", "quote_template_id": "standard"}),
            ("update", "POST", profiles, {"operation": "update", "label": "Synthetic shared", "defaults": {}}),
            ("access", "POST", profiles, {"operation": "access", "access": access}),
        ):
            with self.subTest(answer=label):
                reset_rate_limits()
                now = stored(shared["id"])["_revision"]
                status, _, raw = self.raw(admin, method, route, {"id": shared["id"], "revision": now, **body} if body else None)
                answer = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                self.assertEqual(status, 200, answer)
                self.assertIn(shared["id"], answer)
                self.assertNotIn("_own_layout", answer)
                self.assertIs(stored(shared["id"])["_own_layout"], True)
        self.assertEqual(choose(admin, shared["id"], "own", stored(shared["id"])["_revision"])[0], 200)

        # A profile created without a workbook has nothing to go back to: not offered, not accepted.
        fresh = create(operator, "Synthetic fresh", legacy=False)
        self.assertIs(listed(operator, fresh["id"])["own_layout"], False)
        before = stored(fresh["id"])
        # Who may change it, and the save number, come first: another member, or an old page, learns nothing about its layout.
        for label, headers, expected, revision in (("another member", admin, 404, fresh["revision"]),
                                                   ("stale revision", operator, 409, fresh["revision"] + 1)):
            with self.subTest(no_own_layout=label):
                status, body = choose(headers, fresh["id"], "own", revision)
                self.assertEqual(status, expected, body)
                self.assertNotIn("layout", json.dumps(body))
        self.assertEqual(stored(fresh["id"]), before)
        status, body = choose(operator, fresh["id"], "own", fresh["revision"])
        self.assertEqual((status, body["errors"]), (400, ["This profile has no layout of its own."]))
        self.assertEqual(stored(fresh["id"]), before)
        self.assertEqual(choose(operator, fresh["id"], blue["id"], fresh["revision"])[0], 200)
        self.assertIs(listed(operator, fresh["id"])["own_layout"], False)
        self.assertEqual(choose(operator, fresh["id"], "own", fresh["revision"] + 1)[0], 400)
        # The mark is the server's: a save cannot bring it, or the choice, along.
        for extra in ({"_own_layout": True}, {"quote_template_id": "own"}):
            status, body = self.call(operator, "POST", profiles, {
                "operation": "update", "id": fresh["id"], "label": "x", "defaults": {}, "revision": fresh["revision"] + 1, **extra})
            self.assertEqual(status, 400, body)
        # A workbook that is gone cannot be gone back to.
        with inspection.connection() as connection:
            connection.execute("delete from sqag_file_artifacts where owner_id = ?", (own["id"],))
            connection.commit()
        # Nor does another workspace's workbook count, even for a profile with the same id there.
        other = s.DatabaseSqagStorage(self.url, "workspace-other", "admin", "other-admin")
        other.save_profile({"id": own["id"], "label": "Synthetic theirs", "defaults": {}})
        self.assertTrue(other._profile_layout_artifact_exists(own["id"]))
        theirs = other._read_payload("sqag_profiles", "profile_id", own["id"])
        revision = stored(own["id"])["_revision"]
        self.assertEqual(choose(operator, own["id"], blue["id"], revision)[0], 200)
        before = stored(own["id"])
        self.assertEqual(choose(operator, own["id"], "own", revision + 1)[0], 400)
        self.assertEqual(stored(own["id"]), before)
        self.assertEqual(other._read_payload("sqag_profiles", "profile_id", own["id"]), theirs)


class QuoteTemplateChoicesUnitTest(unittest.TestCase):
    def test_local_file_storage_locks_the_dropdown_on_standard(self):
        storage = s.LocalSqagStorage.__new__(s.LocalSqagStorage)
        self.assertEqual(s.quote_template_choices(storage), {
            "available": False, "default_template_id": "standard",
            "templates": [{"id": "standard", "name": "Standard", "builtin": True}]})


if __name__ == "__main__":
    unittest.main()
