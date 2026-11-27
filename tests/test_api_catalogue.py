"""Feature catalogue controls (#306 slice A). Names state the failure each test prevents."""
import ast
from dataclasses import replace
import importlib
import json
from pathlib import Path
import re
import unittest

from tests import field_shape_support
# A module, not its classes: importing a TestCase class would run its tests twice.
from tests import test_quote_api_registry as registry_fixtures
from webapp import api_catalogue, quote_api_auth, quote_api_registry, quote_context
from webapp.ai_response_contracts import Field, obj
from webapp.api_catalogue import CATALOGUE, ApiCatalogue, Operation

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "docs/quotation-api-v1.openapi.json").read_text(encoding="utf-8"))

# The meaning live keys have stored in sqag_quote_api_callers.operations_mask since
# migration 012 (applied on live 26 Nov 2026). Never edit a row: a reorder or removal
# would silently change what every stored key may do.
STORED_BITS = {"create": 0, "draft": 1, "price": 2, "export": 3,
               "poll": 4, "basis": 5, "result": 6, "artifact": 7}

# The v1 route table as written by hand before the catalogue (base cfd238b).
_OLD_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
OLD_ROUTES = (
    ("POST", r"/api/v1/quote-requests", "create"),
    ("POST", rf"/api/v1/quote-requests/{_OLD_ID}/drafts", "draft"),
    ("POST", r"/api/v1/quote-steps/price", "price"),
    ("POST", rf"/api/v1/quote-requests/{_OLD_ID}/exports", "export"),
    ("GET", rf"/api/v1/jobs/{_OLD_ID}", "poll"),
    ("GET", rf"/api/v1/quote-requests/{_OLD_ID}", "basis"),
    ("GET", rf"/api/v1/quote-requests/{_OLD_ID}/result", "result"),
    ("GET", rf"/api/v1/quote-requests/{_OLD_ID}/artifacts/(?:xlsx|pdf)", "artifact"),
)


def old_match(method, path):
    return next((op for verb, pattern, op in OLD_ROUTES
                 if method == verb and re.fullmatch(pattern, path, re.ASCII)), None)


def entry(**changes):
    base = Operation("probe", "quote.read", "GET", "/api/v1/probes/{id}", "webapp.server:QuoteRunnerHandler",
                     "sync", "Read a probe", path_params=(("id", Field("string")),))
    return replace(base, **changes)


class StoredPermissionBitsTest(unittest.TestCase):
    setUp = registry_fixtures.RegistryFixture.setUp
    transaction = registry_fixtures.RegistryFixture.transaction

    def test_names_and_bit_order_match_what_migration_012_stored(self):
        self.assertEqual({op.name: op.mask_bit for op in CATALOGUE.operations}, STORED_BITS)
        self.assertEqual(quote_api_registry.OPERATIONS, tuple(sorted(STORED_BITS, key=STORED_BITS.get)))
        for name, bit in STORED_BITS.items():
            with self.subTest(operation=name):
                with self.transaction() as c:
                    c.execute("update sqag_quote_api_callers set operations_mask=?", (1 << bit,))
                self.assertEqual(self.adapter.lookup(registry_fixtures.CID).operations, frozenset({name}))
        with self.transaction() as c:
            c.execute("update sqag_quote_api_callers set operations_mask=0")
        self.assertEqual(self.adapter.lookup(registry_fixtures.CID).operations, frozenset())

    def test_mask_reader_refuses_values_the_column_cannot_hold(self):
        self.assertEqual(CATALOGUE.operations_from_mask(255), frozenset(STORED_BITS))
        for mask in (-1, 256, True, 1.0, "1", None):
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                CATALOGUE.operations_from_mask(mask)


class SingleSourceTest(unittest.TestCase):
    def test_admission_modules_hold_no_copy_of_the_operation_list_or_routes(self):
        for module in (quote_context, quote_api_auth, quote_api_registry):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            # A route string, or a literal naming three or more operations, is a copy of the list.
            # A rule naming one or two (the create and poll quota counters) is fixed policy.
            copies = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                      and isinstance(node.value, str) and node.value.startswith("/api/v1/")]
            copies += [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Set, ast.Tuple, ast.List))
                       and sum(isinstance(e, ast.Constant) and e.value in STORED_BITS for e in node.elts) >= 3]
            with self.subTest(module=module.__name__):
                self.assertEqual(copies, [])
        self.assertEqual(quote_context.QUOTE_OPERATIONS, frozenset(STORED_BITS))
        self.assertEqual(quote_context.QUOTE_READ_OPERATIONS, frozenset({"poll", "basis", "result", "artifact"}))
        self.assertEqual(quote_api_auth._STAFF_MUTATIONS, frozenset({"create", "draft", "price", "export"}))

    def test_derived_routes_match_the_old_table_on_valid_and_hostile_paths(self):
        long_id = "a" * 128
        ids = ("qr-test", "Q_1", long_id, long_id + "a", "-qr", "_qr", "", "qr.test", "qr test",
               "qr%2Ftest", "\u0661", "qr\n", "..", "QR")
        paths = ["/api/v1/quote-requests", "/api/v1/quote-steps/price", "/api/v1/quote-requests/",
                 "/api/v1//quote-requests", "/API/v1/quote-requests", "/api/v1/quote-steps/price/",
                 "/api/v2/quote-requests", "/api/v1/jobs", "/api/v1/jobs/"]
        for value in ids:
            paths += [f"/api/v1/quote-requests/{value}", f"/api/v1/quote-requests/{value}/drafts",
                      f"/api/v1/quote-requests/{value}/exports", f"/api/v1/jobs/{value}",
                      f"/api/v1/quote-requests/{value}/result", f"/api/v1/quote-requests/{value}/result/"]
            for kind in ("xlsx", "pdf", "XLSX", "csv", "xlsx/", "", "pdf\n"):
                paths.append(f"/api/v1/quote-requests/{value}/artifacts/{kind}")
        matched = set()
        for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS", "get", "HEAD"):
            for path in paths:
                with self.subTest(method=method, path=path):
                    expected = old_match(method, path)
                    self.assertEqual(CATALOGUE.match(method, path), expected)
                    self.assertEqual(quote_api_auth.quote_operation(method, path), expected)
                    matched.add(expected)
        self.assertEqual(matched - {None}, set(STORED_BITS))


class FastSpecJobTest(unittest.TestCase):
    def test_the_fast_ci_job_runs_every_api_spec_module_and_both_checks(self):
        # tests/api_spec has no __init__.py, so Validate app's discovery skips it; this job is its only run.
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        modules = sorted(path.stem for path in (ROOT / "tests/api_spec").glob("test_*.py"))
        self.assertTrue(modules)
        self.assertFalse((ROOT / "tests/api_spec/__init__.py").exists())
        for needed in ["python scripts/build_api_spec.py --check", "python scripts/check_api_compat.py",
                       *(f"tests.api_spec.{name}" for name in modules)]:
            with self.subTest(needed=needed):
                self.assertIn(needed, workflow)


class DeclaredPolicyTest(unittest.TestCase):
    def test_owner_revision_3_audiences_and_caller_policies(self):
        for op in CATALOGUE.operations:
            with self.subTest(operation=op.name):
                self.assertEqual(op.stability, "stable")
                self.assertNotIn("platform", op.audiences)
                if op.name in ("price", "export"):
                    self.assertEqual(op.audiences, {"staff"})
                    self.assertEqual(op.caller_policies, {"confirm_first"})
                else:
                    self.assertEqual(op.audiences, {"service", "staff"})
                    self.assertEqual(op.caller_policies, {"confirm_first", "straight_through"})
                write = op.scope == "quote.write"
                self.assertEqual((op.method, op.kind), ("POST", "job") if write else ("GET", "sync"))

    def test_an_entry_that_names_no_audience_or_policy_is_staff_only_and_confirm_first(self):
        probe = entry()
        self.assertEqual(probe.audiences, {"staff"})
        self.assertEqual(probe.caller_policies, {"confirm_first"})
        self.assertEqual(probe.stability, "preview")
        self.assertIsNone(probe.mask_bit)

    def test_handlers_resolve_to_the_code_that_answers_today(self):
        for op in CATALOGUE.operations:
            module, _, attribute = op.handler.partition(":")
            target = importlib.import_module(module)
            for part in attribute.split("."):
                target = getattr(target, part)
            with self.subTest(operation=op.name):
                self.assertTrue(callable(target))

    def test_invalid_entries_are_refused(self):
        bad = ({"name": "Probe"}, {"name": ""}, {"scope": "quote.admin"}, {"method": "DELETE"},
               {"kind": "stream"}, {"stability": "beta"}, {"handler": "webapp.server"}, {"handler": "x:y z"},
               {"audiences": frozenset()}, {"audiences": frozenset({"public"})}, {"audiences": "staff"},
               {"caller_policies": frozenset()}, {"caller_policies": frozenset({"skip_review"})},
               {"mask_bit": 8}, {"mask_bit": -1}, {"mask_bit": True}, {"path": "/api/v2/probes/{id}"},
               {"path": "/api/v1/probes/{id}/"}, {"path": "/api/v1/probes/{other}"},
               {"path_params": ()}, {"path_params": (("id", Field("integer")),)},
               {"path_params": (("id", Field("string", nullable=True)),)},
               {"request": obj(note=Field("string"))}, {"response": Field("array", item=Field("string"))},
               {"method": "POST", "request_schema": "Probe"}, {"summary": ""}, {"summary": " Read"},
               {"description": "x" * 1025}, {"from_states": ("bogus",)}, {"from_states": ("drafting", "drafting")},
               {"request_schema": "Probe"}, {"response_schema": "probe"},
               {"scope": "quote.write", "method": "GET"}, {"scope": "quote.write", "method": "POST"},
               {"scope": "quote.write", "method": "POST", "request_schema": "Probe", "staff_request_schema": "Probe"})
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                entry(**changes)

    def test_write_scope_and_post_go_together_so_every_write_gets_the_staff_csrf_check(self):
        write = entry(scope="quote.write", method="POST", request_schema="Probe")
        self.assertEqual(write.scope, "quote.write")
        for changes in ({"method": "POST", "request_schema": "Probe"}, {"scope": "quote.write"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                entry(**changes)
        self.assertEqual(quote_api_auth._STAFF_MUTATIONS,
                         frozenset(op.name for op in CATALOGUE.operations if op.method == "POST"))

    def test_overlapping_path_templates_are_refused_whatever_the_entry_order(self):
        basis, artifact = CATALOGUE.get("basis"), CATALOGUE.get("artifact")
        overlapping = (replace(entry(), name="latest", path="/api/v1/quote-requests/latest", path_params=()),
                       replace(entry(), name="pdf_copy", path="/api/v1/quote-requests/{id}/artifacts/pdf"),
                       replace(entry(), name="any_kind", path="/api/v1/quote-requests/{id}/artifacts/{other}",
                               path_params=(("id", Field("string")), ("other", Field("string")))))
        for extra, existing in zip(overlapping, (basis, artifact, artifact)):
            for order in ((existing, extra), (extra, existing)):
                with self.subTest(extra=extra.name, first=order[0].name), self.assertRaises(ValueError):
                    ApiCatalogue(order)
        apart = (replace(entry(), name="zip_copy", path="/api/v1/quote-requests/{id}/artifacts/zip"),
                 replace(entry(), name="latest_write", scope="quote.write", method="POST",
                         path="/api/v1/quote-requests/latest", path_params=(), request_schema="Probe"),
                 replace(entry(), name="jobs_list", path="/api/v1/jobs", path_params=()))
        self.assertEqual(len(ApiCatalogue((*CATALOGUE.operations, *apart)).operations), len(CATALOGUE.operations) + 3)

    def test_duplicate_names_endpoints_or_bits_are_refused(self):
        create = CATALOGUE.get("create")
        for extra in (replace(entry(), name="create"), replace(create, name="create_again", mask_bit=None),
                      replace(entry(), mask_bit=create.mask_bit)):
            with self.subTest(extra=extra.name), self.assertRaises(ValueError):
                ApiCatalogue((create, extra))
        for operations in ((), (create, "poll")):
            with self.assertRaises(ValueError):
                ApiCatalogue(operations)


def _resolve(node):
    while "$ref" in node:
        target = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _structure(node):
    """Type, keys, required keys, closure, items and enum; patterns and bounds are left to the spec."""
    node = _resolve(node)
    nullable = False
    if "anyOf" in node:
        branches = [b for b in node["anyOf"] if b != {"type": "null"}]
        nullable, node = len(branches) == 1, _resolve(branches[0])
    kind = node["type"]
    result = {"type": sorted({*(kind if isinstance(kind, list) else [kind]), *(["null"] if nullable else [])})}
    if "properties" in node:
        result["properties"] = {k: _structure(v) for k, v in node["properties"].items()}
        result["required"] = sorted(node.get("required", []))
        result["additionalProperties"] = node.get("additionalProperties")
    if "items" in node:
        result["items"] = _structure(node["items"])
    if "enum" in node:
        result["enum"] = [v for v in node["enum"] if v is not None]
    return result


class SpecAgreementTest(unittest.TestCase):
    """Catalogue body shapes agree with the component schemas the spec builder writes."""

    def spec_operation(self, op):
        return SPEC["paths"][op.path][op.method.lower()]

    def test_every_entry_is_a_spec_endpoint_and_nothing_else_is(self):
        endpoints = {(method.upper(), path) for path, item in SPEC["paths"].items()
                     for method in item if method in ("get", "post")}
        self.assertEqual({(op.method, op.path) for op in CATALOGUE.operations}, endpoints)

    def test_path_parameters_match_the_spec(self):
        for op in CATALOGUE.operations:
            params = [_resolve(p) for p in self.spec_operation(op).get("parameters", [])]
            path = [p for p in params if p.get("in") == "path"]
            with self.subTest(operation=op.name):
                self.assertEqual([p["name"] for p in path], [key for key, _ in op.path_params])
                for (key, field), spec in zip(op.path_params, path):
                    self.assertEqual(list(field.enum), _resolve(spec["schema"]).get("enum", []))

    def test_body_shapes_match_the_spec_service_schemas(self):
        checked = 0
        for op in CATALOGUE.operations:
            spec = self.spec_operation(op)
            accepted = next(code for code in spec["responses"] if code.startswith("2"))
            pairs = (("request", op.request, spec.get("requestBody", {}).get("content", {})),
                     ("response", op.response, spec["responses"][accepted].get("content", {})))
            for label, field, content in pairs:
                if field is None:
                    continue
                schema = content["application/json"]["schema"]
                # The price body is a documentation union; the service branch is the closed contract.
                schema = schema.get("oneOf", [schema])[0]
                with self.subTest(operation=op.name, body=label):
                    self.assertEqual(_structure(field.schema()), _structure(schema))
                    self.assertEqual(field_shape_support.omission_mismatches(field), [])
                checked += 1
        self.assertEqual(checked, 8)


if __name__ == "__main__":
    unittest.main()
