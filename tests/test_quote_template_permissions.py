"""Who may do what with quote templates, over real loopback HTTP; all identities are fake."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import quote_template
from tests import test_internal_password_auth_webapp as auth_http
from tests import test_webapp as tw
from webapp import server as s


def definition(name):
    template = copy.deepcopy(quote_template.load_standard_quote_template())
    template["name"] = name
    return template


class QuoteTemplatePermissionsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.url = "sqlite:///" + (Path(directory.name) / "http.sqlite3").as_posix()
        s.apply_sqag_storage_migrations(self.url)
        patcher = mock.patch.object(s, "configured_object_storage_backend", return_value=s.InMemoryObjectStorageBackend())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host = auth_http.InternalPasswordAuthWebappTest()
        self.host.setUp({"SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": "database",
                         "SQAG_DATABASE_URL": self.url,
                         "SQAG_TRACKING_HMAC_KEY": "synthetic-template-audit-tracking-key",
                         "SQAG_TRACKING_HMAC_KEY_VERSION": "synthetic-v1"})
        self.addCleanup(self.host.doCleanups)
        self.workspace = os.environ["SQAG_INTERNAL_WORKSPACE_ID"]

    def login(self, alias):
        status, headers, _ = self.host.login(alias)
        self.assertEqual(status, 302)
        cookie = s.SESSION_COOKIE_NAME + "=" + self.host.cookie(headers, s.SESSION_COOKIE_NAME)
        status, _, raw = self.host.http("GET", "/api/session", {"Cookie": cookie})
        self.assertEqual(status, 200)
        session = json.loads(raw)
        return {"Cookie": cookie, "Origin": auth_http.ORIGIN, "Content-Type": "application/json",
                session["csrf_header"]: session["csrf_token"]}

    def call(self, headers, method, route, body=None):
        status, _, raw = self.host.http(method, route, headers, json.dumps(body) if body is not None else None)
        return status, json.loads(raw) if raw else {}

    def saved_rows(self):
        storage = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")
        with storage.connection() as connection:
            return [dict(row) for row in connection.execute(
                "select template_id, name, save_number, is_default from sqag_quote_templates order by template_id")]

    def test_route_permission_table(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        status, body = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Seed")})
        self.assertEqual(status, 200, body)
        seed = body["template"]
        base = "/api/settings/quote-templates"
        # (action, method, route, body, admin status, operator status)
        table = (
            ("list", "GET", base, None, 200, 200),
            ("read saved", "GET", f"{base}/{seed['id']}", None, 200, 403),
            ("read standard", "GET", f"{base}/standard", None, 200, 403),
            ("check draft", "POST", f"{base}/preview", {"definition": definition("Draft")}, 200, 403),
            ("create", "POST", base, {"definition": definition("New")}, 200, 403),
            ("update", "POST", f"{base}/{seed['id']}", {"definition": definition("Renamed"), "save_number": 1}, 200, 403),
            ("set default", "POST", f"{base}/default", {"template_id": seed["id"]}, 200, 403),
            ("delete", "DELETE", f"{base}/{seed['id']}?save_number=2", None, 200, 403),
        )
        signed_out = {"Origin": auth_http.ORIGIN, "Content-Type": "application/json"}
        for action, method, route, payload, admin_status, operator_status in table:
            for who, headers, expected in (("signed out", signed_out, 401), ("operator", operator, operator_status),
                                           ("admin", admin, admin_status)):
                before = self.saved_rows()
                with self.subTest(action=action, who=who):
                    status, body = self.call(headers, method, route, payload)
                    self.assertEqual(status, expected, body)
                    if status != 200:
                        self.assertEqual(self.saved_rows(), before)
                    for secret in ("SESSION_SECRET", "synthetic-session-secret", "definition_json"):
                        self.assertNotIn(secret, json.dumps(body))
        self.assertEqual(self.saved_rows()[0]["name"], "New")
        # A state change without the CSRF header never reaches the store.
        no_csrf = {"Cookie": admin["Cookie"], "Origin": auth_http.ORIGIN, "Content-Type": "application/json"}
        before = self.saved_rows()
        self.assertEqual(self.call(no_csrf, "POST", base, {"definition": definition("Forged")})[0], 403)
        self.assertEqual(self.saved_rows(), before)

    def test_staff_list_shows_names_only_and_bad_requests_are_refused(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        self.assertEqual(self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Blue")})[0], 200)
        status, body = self.call(operator, "GET", "/api/settings/quote-templates")
        self.assertEqual(status, 200, body)
        self.assertEqual([item["name"] for item in body["templates"]], ["Standard", "Blue"])
        self.assertEqual((body["default_template_id"], body["limit"], body["saved_count"]), ("standard", 10, 1))
        self.assertNotIn("definition", json.dumps(body))
        for method, route, payload, status_expected in (
            ("POST", "/api/settings/quote-templates", {"definition": {"name": "=cmd()"}}, 400),
            ("POST", "/api/settings/quote-templates/preview", {"definition": "not an object"}, 400),
            ("POST", "/api/settings/quote-templates/default", {}, 400),
            ("POST", "/api/settings/quote-templates/standard", {"definition": definition("Mine"), "save_number": 0}, 403),
            ("DELETE", "/api/settings/quote-templates/standard?save_number=0", None, 403),
            ("DELETE", "/api/settings/quote-templates/tpl-" + "0" * 32 + "?save_number=x", None, 400),
            ("DELETE", "/api/settings/quote-templates/tpl-" + "0" * 32 + "?save_number=1", None, 404),
            ("GET", "/api/settings/quote-templates/tpl-" + "0" * 32, None, 404),
            ("GET", "/api/settings/quote-templates/preview", None, 404),
            ("DELETE", "/api/settings/quote-templates", None, 404),
        ):
            with self.subTest(method=method, route=route):
                self.assertEqual(self.call(admin, method, route, payload)[0], status_expected)
        self.assertEqual(len(self.saved_rows()), 1)

    def test_refused_saves_say_why_without_internals(self):
        admin = self.login(auth_http.email())
        base = "/api/settings/quote-templates"
        for name in ("First", "Second"):
            status, body = self.call(admin, "POST", base, {"definition": definition(name)})
            self.assertEqual(status, 200, body)
        saved = body["template"]
        missing = "tpl-" + "0" * 32
        cases = (
            # (refusal, method, route, body, status, reason, message)
            ("at the limit", "POST", base, {"definition": definition("One too many")}, 409, "template_limit_reached",
             "This workspace already has 2 quote templates, the most it can keep. Delete one before adding another."),
            ("old save number", "POST", f"{base}/{saved['id']}", {"definition": definition("Stale"), "save_number": 7},
             409, "template_save_conflict", "This template changed since it was opened. Reload it before trying again."),
            ("old save number on delete", "DELETE", f"{base}/{saved['id']}?save_number=7", None,
             409, "template_save_conflict", "This template changed since it was opened. Reload it before trying again."),
            ("missing template", "DELETE", f"{base}/{missing}?save_number=1", None, 404, "template_not_found",
             "Quote template not found."),
            ("Standard is built in", "DELETE", f"{base}/standard?save_number=0", None, 403, "template_builtin",
             "The Standard template is built in and cannot be changed or deleted."),
            ("profile names a missing template", "POST", "/api/settings/profiles",
             {"operation": "create", "id": "synthetic-refusal", "label": "Synthetic refusal", "defaults": {},
              "quote_template_id": missing}, 400, "template_not_found",
             "The chosen quote template does not exist. Pick another template."),
        )
        with mock.patch.dict(os.environ, {s.QUOTE_TEMPLATE_LIMIT_ENV_NAME: "2"}):
            for refusal, method, route, payload, status_expected, reason, message in cases:
                with self.subTest(refusal=refusal):
                    status, body = self.call(admin, method, route, payload)
                    self.assertEqual((status, body.get("reason"), body.get("errors")), (status_expected, reason, [message]))
                    self.assertRegex(body.get("error_reference", ""), r"^ERR-[0-9A-F]{8}$")
                    for internal in ("sqag_quote_templates", "Traceback", "workspace_id", self.workspace):
                        self.assertNotIn(internal, json.dumps(body))
        self.assertEqual(sorted(row["name"] for row in self.saved_rows()), ["First", "Second"])

    def test_profile_links_follow_profile_privacy(self):
        admin, operator = self.login(auth_http.email()), self.login(auth_http.email("operator"))
        status, body = self.call(admin, "POST", "/api/settings/quote-templates", {"definition": definition("Linked")})
        template_id = body["template"]["id"]
        profiles = "/api/settings/profiles"
        # Staff link their own personal profile; unknown or malformed template ids are refused.
        status, body = self.call(operator, "POST", profiles, {
            "operation": "create", "id": "synthetic-personal", "label": "Synthetic personal", "defaults": {},
            "quote_template_id": template_id})
        self.assertEqual(status, 200, body)
        personal = body["profile"]
        self.assertEqual(personal["quote_template_id"], template_id)
        for bad, reason in (("tpl-" + "f" * 32, 400), ("../other", 400), (7, 400)):
            with self.subTest(choice=bad):
                status, body = self.call(operator, "POST", profiles, {
                    "operation": "update", "id": personal["id"], "revision": personal["revision"],
                    "label": "Synthetic personal", "defaults": {}, "quote_template_id": bad})
                self.assertEqual(status, reason, body)
        # An admin cannot link, or even see, another member's personal profile.
        inspection = s.DatabaseSqagStorage(self.url, self.workspace, "admin", "inspection")
        before = inspection._read_payload("sqag_profiles", "profile_id", personal["id"])
        status, body = self.call(admin, "POST", profiles, {
            "operation": "update", "id": personal["id"], "revision": personal["revision"],
            "label": "Synthetic personal", "defaults": {}, "quote_template_id": "standard"})
        self.assertEqual(status, 404, body)
        self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", personal["id"]), before)
        for route in ("/api/settings/quote-templates", f"/api/settings/quote-templates/{template_id}"):
            self.assertNotIn(personal["id"], json.dumps(self.call(admin, "GET", route)[1]))
        # The shared company profile is visible to all, but only admins may change its template.
        shared = inspection.save_profile(s.normalize_profile_payload({"id": "shared-company", "defaults": {}}))
        payload = inspection._read_payload("sqag_profiles", "profile_id", shared["id"])
        payload.pop("_access")
        payload.pop("_revision")
        inspection._upsert_payload("sqag_profiles", "profile_id", shared["id"], payload)
        update = {"operation": "update", "id": shared["id"], "revision": 0, "defaults": {}, "quote_template_id": template_id}
        # Staff get 404, not 403: the shared-profile write rule answers as if the profile were not theirs to
        # change, the same as for any other shared-profile edit, so linking a template reveals nothing new.
        unlinked = inspection._read_payload("sqag_profiles", "profile_id", shared["id"])
        status, body = self.call(operator, "POST", profiles, update)
        self.assertEqual(status, 404, body)
        self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", shared["id"]), unlinked)
        self.assertEqual(unlinked["quote_template_id"], "default")
        status, body = self.call(admin, "POST", profiles, update)
        self.assertEqual(status, 200, body)
        self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", shared["id"])["quote_template_id"], template_id)
        listed = self.call(operator, "GET", profiles)[1]["company_profiles"]
        self.assertEqual({p["id"]: p.get("quote_template_id") for p in listed}[shared["id"]], template_id)


class QuoteTemplateViewerHttpTest(unittest.TestCase):
    """Viewers exist only as platform workspace members, so this row runs a platform session over HTTP."""

    platform_launch_env = tw.WebappServerTest.platform_launch_env
    platform_consume_payload = tw.WebappServerTest.platform_consume_payload
    platform_auth_session = tw.WebappServerTest.platform_auth_session
    http_json = tw.WebappServerTest.http_json

    def test_viewers_are_refused_on_every_route_and_change_nothing(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        url = "sqlite:///" + (Path(directory.name) / "viewer.sqlite3").as_posix()
        workspace = "workspace-template-viewer"
        env = self.platform_launch_env(SQAG_STORAGE_MODE="database", SQAG_ARTIFACT_STORAGE_MODE="database",
                                       SQAG_DATABASE_URL=url, QUOTE_DATA_ROOT=str(Path(directory.name) / "data"),
                                       QUOTE_LOG_ROOT=str(Path(directory.name) / "logs"))

        def headers(session):
            cookie = f"{s.SESSION_COOKIE_NAME}={s.signed_cookie_value(session)}"
            return cookie, {s.configured_csrf_header_name(): s.csrf_token_for_cookie_header(cookie)}

        with mock.patch.dict(os.environ, env, clear=True):
            s.apply_sqag_storage_migrations(url)
            store = s.QuoteTemplateStore(s.DatabaseSqagStorage(url, workspace, "admin", "seed"))
            seed = store.create(definition("Seed"))
            viewer_cookie, viewer_headers = headers(self.platform_auth_session(workspace, "viewer", "template-viewer"))
            owner_cookie, owner_headers = headers(self.platform_auth_session(workspace, "owner", "template-owner"))
            base = "/api/settings/quote-templates"
            routes = (
                ("list", "GET", base, None),
                ("read saved", "GET", f"{base}/{seed['id']}", None),
                ("read standard", "GET", f"{base}/standard", None),
                ("check draft", "POST", f"{base}/preview", {"definition": definition("Draft")}),
                ("create", "POST", base, {"definition": definition("New")}),
                ("update", "POST", f"{base}/{seed['id']}", {"definition": definition("Renamed"), "save_number": 1}),
                ("set default", "POST", f"{base}/default", {"template_id": seed["id"]}),
                ("delete", "DELETE", f"{base}/{seed['id']}?save_number=1", None),
            )
            with mock.patch.object(s, "validated_platform_auth_session", side_effect=lambda session: session), \
                    tw.LocalRunnerServer() as runner:
                for action, method, route, body in routes:
                    with self.subTest(action=action):
                        response = self.http_json(runner, method, route, cookie=viewer_cookie, body=body,
                                                  headers=viewer_headers)
                        self.assertEqual(response["status"], 403, response["text"])
                        self.assertEqual(response["body"]["errors"], ["You do not have permission to perform this action."])
                        self.assertNotIn("Seed", response["text"])
                        self.assertEqual(store.list(), [seed])
                # The same harness lets a workspace owner list, so the refusals above are the viewer's role.
                listed = self.http_json(runner, "GET", base, cookie=owner_cookie, headers=owner_headers)
                self.assertEqual(listed["status"], 200, listed["text"])
                self.assertEqual([item["name"] for item in listed["body"]["templates"]], ["Standard", "Seed"])


if __name__ == "__main__":
    unittest.main()
