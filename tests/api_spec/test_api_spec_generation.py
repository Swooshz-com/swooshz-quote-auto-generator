"""Generated API spec controls (#306 slice B). Runs in the fast "Quotation API spec" CI job."""
import contextlib
import io
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from webapp import api_schema, api_spec, server
from webapp.api_catalogue import CATALOGUE, ApiCatalogue
from scripts import build_api_spec

ROOT = Path(__file__).resolve().parents[2]


def differences(old, new, path=""):
    """Paths where two JSON values differ."""
    if isinstance(old, dict) and isinstance(new, dict):
        return [item for key in sorted(old.keys() | new.keys())
                for item in (differences(old[key], new[key], f"{path}/{key}") if key in old and key in new
                             else [f"{path}/{key}"])]
    return [] if old == new else [path]


class GeneratedSpecTest(unittest.TestCase):
    def test_committed_spec_is_exactly_the_generated_one(self):
        self.assertEqual(api_spec.SPEC_PATH.read_text(encoding="utf-8"), api_spec.render(api_spec.build_spec()))
        self.assertTrue(json.loads(api_spec.SPEC_PATH.read_text(encoding="utf-8"))["x-generated"])

    def test_check_mode_fails_on_a_stale_spec_and_passes_on_a_current_one(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "spec.json"
            with mock.patch.object(build_api_spec, "SPEC_PATH", target), mock.patch.object(build_api_spec, "ROOT",
                                                                                         Path(folder)):
                target.write_text(api_spec.render(api_spec.build_spec()).replace("Poll job", "Poll a job"),
                                  encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(build_api_spec.main(["--check"]), 1)
                self.assertIn("Stale", err.getvalue())
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(build_api_spec.main([]), 0)
                    self.assertEqual(build_api_spec.main(["--check"]), 0)

    def test_a_server_limit_change_reaches_x_limits_with_no_other_edit(self):
        before = api_spec.build_spec()
        with mock.patch.object(server, "MAX_DRAFT_PDF_PAGES", server.MAX_DRAFT_PDF_PAGES + 1):
            after = api_spec.build_spec()
        self.assertEqual(differences(before, after), ["/x-limits/pdf_pages_total"])
        self.assertEqual(after["x-limits"]["pdf_pages_total"], server.MAX_DRAFT_PDF_PAGES + 1)

    def test_a_shared_limit_reaches_both_the_limits_and_the_schema_bound(self):
        before = api_spec.build_spec()
        with mock.patch.object(server, "MAX_REFERENCE_IMAGES", 9):
            after = api_spec.build_spec()
        self.assertEqual(differences(before, after), [
            "/components/schemas/CreateQuoteRequest/properties/design_files/maxItems", "/x-limits/files"])

    def test_error_states_and_entries_come_from_their_owners(self):
        table = dict(api_spec.ERROR_STATUS, SLOW_DOWN=429)
        with mock.patch.object(api_spec, "ERROR_STATUS", table):
            spec = api_spec.build_spec()
        self.assertIn("SLOW_DOWN", spec["x-error-status-codes"]["429"])
        self.assertIn("SLOW_DOWN", spec["components"]["schemas"]["ErrorDetail"]["properties"]["code"]["enum"])
        self.assertIn("SLOW_DOWN", json.dumps(spec["components"]["responses"]["Error429"]))
        probe = replace(CATALOGUE.get("basis"), name="history", path="/api/v1/quote-requests/{id}/history",
                        summary="Read revision history", stability="preview", mask_bit=None,
                        audiences=frozenset({"staff"}))
        spec = api_spec.build_spec(catalogue=ApiCatalogue((*CATALOGUE.operations, probe)))
        operation = spec["paths"]["/api/v1/quote-requests/{id}/history"]["get"]
        self.assertEqual((operation["operationId"], operation["x-stability"], operation["x-audiences"]),
                         ("history", "preview", ["staff"]))
        self.assertIn("history", spec["x-scopes"]["quote.read"])

    def test_every_example_matches_its_schema_and_every_keyword_is_checkable(self):
        spec = api_spec.build_spec()

        def nodes(schema):
            yield schema
            for child in (*schema.get("properties", {}).values(), *schema.get("anyOf", []),
                          *schema.get("oneOf", []), *([schema["items"]] if "items" in schema else [])):
                yield from nodes(child)

        for name, schema in spec["components"]["schemas"].items():
            for node in nodes(schema):
                with self.subTest(schema=name):
                    self.assertLessEqual(set(node), api_schema.SUPPORTED_KEYWORDS)
        for name, schema in spec["components"]["schemas"].items():
            for example in schema.get("examples", []):
                with self.subTest(schema=name):
                    api_schema.check(schema, example, root=spec)
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                bodies = [operation.get("requestBody", {}).get("content", {}).get("application/json")]
                bodies += [response.get("content", {}).get("application/json")
                           for response in operation["responses"].values() if "$ref" not in response]
                for body in filter(None, bodies):
                    with self.subTest(path=path, method=method):
                        api_schema.check(body["schema"], body["example"], root=spec)

    def test_catalogue_artifact_kinds_must_match_the_app_export_kinds(self):
        with mock.patch.object(server, "QUOTE_SESSION_EXPORT_KINDS", {"xlsx": "quotation.xlsx"}):
            with self.assertRaises(ValueError):
                api_spec.build_spec()

    def test_checker_refuses_what_it_does_not_implement(self):
        spec = api_spec.build_spec()
        for schema, value in (({"type": "string", "patternProperties": {}}, "x"), ({"$ref": "http://x"}, "x"),
                              ({"$ref": "#/components/schemas/Missing"}, "x")):
            with self.subTest(schema=schema), self.assertRaises(api_schema.SchemaViolation):
                api_schema.check(schema, value, root=spec)


if __name__ == "__main__":
    unittest.main()
