"""Server routes behind the Quote Templates editor, over real loopback HTTP; all identities and data are fake."""
import copy
import json
import os
from pathlib import Path
import unittest
import io
import zipfile
from unittest import mock

from scripts import quote_template
from tests import test_internal_password_auth_webapp as auth_http
from tests import test_quote_template_permissions as permissions_test
from webapp import server as s


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
        reset_rate_limits()  # the checks below would pass the 6-a-minute preview limit
        for bad in ("docx", "", 7, None):
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


class QuoteTemplateChoicesUnitTest(unittest.TestCase):
    def test_local_file_storage_locks_the_dropdown_on_standard(self):
        storage = s.LocalSqagStorage.__new__(s.LocalSqagStorage)
        self.assertEqual(s.quote_template_choices(storage), {
            "available": False, "default_template_id": "standard",
            "templates": [{"id": "standard", "name": "Standard", "builtin": True}]})


if __name__ == "__main__":
    unittest.main()
