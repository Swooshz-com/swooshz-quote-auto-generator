"""Offline contract examples and reference-model tests, never runtime qualification.

Only standard-library dependencies; no app imports, sockets, credentials or files
other than the two directly maintained contract sources are used.
"""

import base64
import copy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "docs/quotation-api-v1.openapi.json").read_text(encoding="utf-8"))
DOC = (ROOT / "docs/quotation-api-v1.md").read_text(encoding="utf-8")
SCHEMAS = SPEC["components"]["schemas"]
OPERATIONS = {
    ("POST", "/api/v1/quote-requests"),
    ("POST", "/api/v1/quote-requests/{id}/drafts"),
    ("POST", "/api/v1/quote-steps/price"),
    ("POST", "/api/v1/quote-requests/{id}/exports"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("GET", "/api/v1/quote-requests/{id}"),
    ("GET", "/api/v1/quote-requests/{id}/result"),
    ("GET", "/api/v1/quote-requests/{id}/artifacts/{kind}"),
}
REQUEST_EDGES = {
    "drafting": {"awaiting_confirmation", "needs_input", "failed", "expired"},
    "awaiting_confirmation": {"drafting", "pricing", "generating", "expired"},
    "pricing": {"awaiting_confirmation", "needs_input", "failed", "expired"},
    "generating": {"completed", "needs_input", "failed", "expired"},
    "completed": {"drafting", "expired"},
    "needs_input": {"drafting", "expired"},
    "failed": {"drafting", "expired"},
    "expired": set(),
}
JOB_EDGES = {"queued": {"running", "failed"}, "running": {"completed", "failed"},
             "completed": set(), "failed": set()}
FORBIDDEN = {
    "internal_override", "role", "tenant", "tenant_id", "workspace_id", "company_id", "account_id",
    "price", "grant", "view", "confidence", "scores", "prompt", "proposal", "suggestions",
    "review_choices", "internal_cost", "gst", "markup", "supplier", "supplier_notes", "ai_spend",
    "profile", "admin", "audit", "ledger", "seal", "matches", "raw_source", "private_path", "provider_response",
}


class ContractViolation(ValueError):
    """A failed test contract assertion, not a production error mapper."""


def check(schema, value):
    """Small fail-closed checker for precisely the JSON Schema subset in this spec.

    It deliberately refuses unknown keywords instead of pretending to implement
    full OpenAPI/JSON Schema validation. Semantic money/media/auth checks are
    separate: a string pattern cannot establish those invariants.
    """
    supported = {"$ref", "type", "properties", "required", "additionalProperties", "items",
                 "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
                 "minimum", "maximum", "exclusiveMinimum", "enum", "const", "anyOf", "oneOf",
                 "format", "examples", "description"}
    if set(schema) - supported:
        raise ContractViolation("Unsupported schema keyword")
    if "$ref" in schema:
        target = SPEC
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        check(target, value)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for variant in schema[keyword]:
                try:
                    check(variant, value)
                    matches += 1
                except ContractViolation:
                    pass
            if matches == 0 or (keyword == "oneOf" and matches != 1):
                raise ContractViolation("No unique valid variant")
    kind = schema.get("type")
    valid_type = {
        "object": lambda: type(value) is dict,
        "array": lambda: type(value) is list,
        "string": lambda: type(value) is str,
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
        "boolean": lambda: type(value) is bool,
        "null": lambda: value is None,
    }
    if kind and (kind not in valid_type or not valid_type[kind]()):
        raise ContractViolation("Wrong type")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ContractViolation("Wrong constant")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise ContractViolation("Unknown enum value")
    if kind == "object":
        props = schema["properties"]
        if set(schema["required"]) - value.keys():
            raise ContractViolation("Missing field")
        if schema.get("additionalProperties") is not False or value.keys() - props.keys():
            raise ContractViolation("Unknown field or open schema")
        for key, child in value.items():
            check(props[key], child)
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ContractViolation("Array bound")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise ContractViolation("Duplicate item")
        for child in value:
            check(schema["items"], child)
    if kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise ContractViolation("String bound")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ContractViolation("String pattern")
        if schema.get("format") == "date-time":
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as exc:
                raise ContractViolation("Invalid timestamp") from exc
        elif "format" in schema and schema["format"] != "binary":
            raise ContractViolation("Unsupported format")
    if kind in ("number", "integer"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ContractViolation("Numeric bound")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ContractViolation("Exclusive bound")


def example(name):
    return copy.deepcopy(SCHEMAS[name]["examples"][0])


def walk_schema(schema, seen=None):
    """Visit all reachable schemas, including nested alternatives."""
    seen = set() if seen is None else seen
    yield schema
    if "$ref" in schema and schema["$ref"] not in seen:
        seen.add(schema["$ref"])
        yield from walk_schema(SCHEMAS[schema["$ref"].rsplit("/", 1)[1]], seen)
    for child in schema.get("properties", {}).values():
        yield from walk_schema(child, seen)
    if "items" in schema:
        yield from walk_schema(schema["items"], seen)
    for keyword in ("anyOf", "oneOf"):
        for child in schema.get(keyword, []):
            yield from walk_schema(child, seen)


def check_estimate(view):
    """Reference assertions beyond schema: decimal amounts and truthful totals."""
    check(SCHEMAS["CustomerEstimateView"], view)
    quantum = Decimal(1).scaleb(-view["currency_decimal_places"])

    def money(text):
        value = Decimal(text)
        if value.as_tuple().exponent != -view["currency_decimal_places"]:
            raise ContractViolation("Currency precision mismatch")
        return value

    priced = []
    unresolved = 0
    for line in view["lines"]:
        price = line["price"]
        if line["quantity"] is None or line["unit"] is None:
            if not line["needs_confirmation"] or price["status"] != "unresolved":
                raise ContractViolation("Unknown scope must stay unresolved")
        if price["status"] == "unresolved":
            unresolved += 1
        else:
            amount = money(price["amount"])
            unit_price = money(price["unit_price"])
            expected = (Decimal(str(line["quantity"])) * unit_price).quantize(quantum, rounding=ROUND_HALF_UP)
            if amount != expected:
                raise ContractViolation("Line amount mismatch")
            priced.append(amount)
    totals = view["totals"]
    known = sum(priced, Decimal(0)) if priced else None
    if (None if totals["known_subtotal"] is None else money(totals["known_subtotal"])) != known:
        raise ContractViolation("Known subtotal mismatch")
    if totals["unresolved_line_count"] != unresolved:
        raise ContractViolation("Wrong unresolved count")
    tax = view["tax"]
    if unresolved:
        if totals["status"] != "partial" or totals["subtotal"] is not None or totals["total"] is not None:
            raise ContractViolation("False full total")
        if tax is not None and tax["amount"] is not None:
            raise ContractViolation("False full tax amount")
    else:
        if totals["status"] != "complete" or totals["subtotal"] is None or totals["total"] is None:
            raise ContractViolation("Missing complete estimate total")
        tax_amount = Decimal(0)
        if tax is not None:
            if tax["amount"] is None:
                raise ContractViolation("Missing configured tax")
            tax_amount = (known * Decimal(tax["rate"])).quantize(quantum, rounding=ROUND_HALF_UP)
            if money(tax["amount"]) != tax_amount:
                raise ContractViolation("Tax mismatch")
        if money(totals["subtotal"]) != known or money(totals["total"]) != known + tax_amount:
            raise ContractViolation("Complete totals mismatch")


class FakeClock:
    def __init__(self):
        self.now = 0

    def advance(self, seconds):
        self.now += seconds


class FakeGateway:
    def __init__(self):
        self.calls = 0

    def analyse(self):
        self.calls += 1
        return copy.deepcopy(example("BasisView")["basis"])


class FakeStorage:
    """In-memory receipt/resource owner; no filesystem or database connection."""

    def __init__(self):
        self.receipts = {}
        self.resources = {}
        self.queue = []

    def transition(self, resource_id, target):
        row = self.resources[resource_id]
        if target not in REQUEST_EDGES.get(row["state"], set()):
            raise ContractViolation("STATE_CONFLICT")
        row["state"] = target


class FakeContractModel:
    """A small executable proposal model, not a substitute for future HTTP tests.

    Immediate fake dispatch makes duplicate work observable via gateway counts.
    Durable multi-worker atomicity, provider uncertainty and publication are not
    simulated or claimed here. Dependencies are explicitly owned by each test.
    """

    def __init__(self, storage, clock, gateway):
        self.storage, self.clock, self.gateway = storage, clock, gateway
        self.revoked = set()

    def read(self, tenant, caller, resource_id):
        if (tenant, caller) in self.revoked:
            raise ContractViolation("AUTH_REQUIRED")
        row = self.storage.resources.get(resource_id)
        if row is None or row["owner"] != (tenant, caller):
            raise ContractViolation("NOT_FOUND")
        return row

    def create(self, tenant, caller, key, body, method="POST", path="/api/v1/quote-requests", authority=1):
        if (tenant, caller) in self.revoked:
            raise ContractViolation("AUTH_REQUIRED")
        check(SCHEMAS["IdempotencyKey"], key)
        check(SCHEMAS["CreateQuoteRequest"], body)
        if len(body.get("notes", "").encode("utf-8")) > 16384:
            raise ContractViolation("INVALID_REQUEST")
        canonical = copy.deepcopy(body)
        for file in canonical["design_files"]:
            raw = base64.b64decode(file["data_url"].split(",", 1)[1], validate=True)
            file["data_url"] = hashlib.sha256(raw).hexdigest()
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True).encode("ascii")).hexdigest()
        scope = (tenant, caller, method, path, key)
        receipt = self.storage.receipts.get(scope)
        if receipt:
            self.read(tenant, caller, receipt["result"]["quote_request_id"])
            if self.clock.now >= receipt["created"] + 86400:
                raise ContractViolation("RESULT_EXPIRED")
            if receipt["digest"] != digest:
                raise ContractViolation("IDEMPOTENCY_CONFLICT")
            return copy.deepcopy(receipt["result"])
        number = len(self.storage.resources) + 1
        resource_id = "qr-test-" + str(number)
        result = {"quote_request_id": resource_id, "job_id": "job-test-" + str(number), "status": "queued",
                  "links": {"job": "/api/v1/jobs/job-test-" + str(number),
                            "request": "/api/v1/quote-requests/" + resource_id}}
        self.storage.resources[resource_id] = {"owner": (tenant, caller), "state": "drafting", "revision": 1,
            "authority": authority, "confirmed": None, "priced": None}
        self.storage.receipts[scope] = {"digest": digest, "created": self.clock.now, "authority": authority,
                                        "result": copy.deepcopy(result)}
        self.storage.queue.append(resource_id)
        self.storage.resources[resource_id]["basis"] = self.gateway.analyse()
        self.storage.transition(resource_id, "awaiting_confirmation")
        return result

    def command(self, tenant, caller, resource_id, command, revision, confirmed=None):
        row = self.read(tenant, caller, resource_id)
        if revision != row["revision"]:
            raise ContractViolation("BASIS_STALE")
        if command == "draft" and row["state"] in {"awaiting_confirmation", "completed", "needs_input", "failed"}:
            self.storage.transition(resource_id, "drafting")
            row.update(revision=revision + 1, confirmed=None, priced=None)
        elif command == "price" and row["state"] == "awaiting_confirmation" and confirmed == revision:
            row["confirmed"] = revision
            self.storage.transition(resource_id, "pricing")
        elif (command == "export" and row["state"] == "awaiting_confirmation"
              and confirmed == row["confirmed"] == row["priced"] == revision):
            self.storage.transition(resource_id, "generating")
        else:
            raise ContractViolation("STATE_CONFLICT")


class QuoteApiContractTest(unittest.TestCase):
    def setUp(self):
        self.storage, self.clock, self.gateway = FakeStorage(), FakeClock(), FakeGateway()
        self.model = FakeContractModel(self.storage, self.clock, self.gateway)

    def test_doc_and_spec_examples_validate_and_agree(self):
        blocks = re.findall(r"### (\w+)\n\n```json\n(.*?)\n```", DOC, re.S)
        expected = {name for name, schema in SCHEMAS.items() if "examples" in schema}
        self.assertEqual({name for name, _ in blocks}, expected)
        self.assertEqual(len(blocks), len(expected))
        for name, source in blocks:
            with self.subTest(schema=name):
                payload = json.loads(source)
                self.assertEqual(payload, example(name))
                check(SCHEMAS[name], payload)
        # Operation-level examples are checked too, not just schema annotations.
        count = 0
        def visit(node):
            nonlocal count
            if isinstance(node, dict):
                if "schema" in node and "example" in node:
                    check(node["schema"], node["example"])
                    count += 1
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(SPEC["paths"])
        self.assertEqual(count, 11)

    def test_checker_negative_controls_and_unsupported_keywords(self):
        for schema, value in [({"type": "integer"}, True), ({"type": "number"}, float("nan")),
                              ({"type": "number"}, float("inf")), ({"type": "string"}, 12),
                              ({"unknownValidation": True}, "anything")]:
            with self.subTest(schema=schema, value=value), self.assertRaises(ContractViolation):
                check(schema, value)
        for stamp in ("2026-02-30T00:00:00Z", "2026-11-06", "2026-11-06T00:00:00Z\n"):
            with self.assertRaises(ContractViolation):
                check(SCHEMAS["Timestamp"], stamp)

    def test_all_visible_objects_are_closed(self):
        object_count = 0
        for name, schema in SCHEMAS.items():
            for nested in walk_schema(schema):
                if nested.get("type") == "object":
                    object_count += 1
                    self.assertIs(nested.get("additionalProperties"), False, name)
                    self.assertLessEqual(set(nested.get("required", [])), nested["properties"].keys())
        self.assertGreater(object_count, 20)

    def test_mutations_reject_unknown_and_forged_authority(self):
        for name in ("CreateQuoteRequest", "QueueDraftRequest", "PriceRequest", "ExportRequest"):
            for field in FORBIDDEN | {"unknown", "pricing_reference_id", "profile_id", "catalog", "scope", "rows"}:
                payload = example(name)
                payload[field] = "internal" if field == "view" else "forged.test"
                with self.subTest(schema=name, field=field), self.assertRaises(ContractViolation):
                    check(SCHEMAS[name], payload)
        payload = example("CreateQuoteRequest")
        payload["design_files"][0]["price"] = "1.00"
        with self.assertRaises(ContractViolation):
            check(SCHEMAS["CreateQuoteRequest"], payload)
        for operations in SPEC["paths"].values():
            for op in operations.values():
                self.assertTrue(op["x-reject-unknown-query"])
                self.assertEqual(op["x-query-schema"], {"$ref": "#/components/schemas/EmptyQuery"})
                check(SCHEMAS["EmptyQuery"], {})
                with self.assertRaises(ContractViolation):
                    check(SCHEMAS["EmptyQuery"], {"view": "internal"})

    def test_service_request_properties_and_required_fields_are_exact(self):
        contracts = {
            "CreateQuoteRequest": ({"design_files", "notes", "client_reference"}, ["design_files"]),
            "QueueDraftRequest": ({"notes"}, []),
            "PriceRequest": ({"quote_request_id", "basis_revision", "confirmed_basis_revision"},
                             ["quote_request_id", "basis_revision", "confirmed_basis_revision"]),
            "ExportRequest": ({"basis_revision", "confirmed_basis_revision", "formats"},
                              ["basis_revision", "confirmed_basis_revision", "formats"]),
        }
        for name, (fields, required) in contracts.items():
            with self.subTest(schema=name):
                self.assertEqual(set(SCHEMAS[name]["properties"]), fields)
                self.assertEqual(SCHEMAS[name]["required"], required)
                # Exercise the actual schema, independently of the reference model.
                for field in required:
                    payload = example(name)
                    del payload[field]
                    with self.subTest(missing=field), self.assertRaises(ContractViolation):
                        check(SCHEMAS[name], payload)
        for name in ("PriceRequest", "ExportRequest"):
            for field in ("basis_revision", "confirmed_basis_revision"):
                for value in (0, -1, True, "1", None):
                    payload = example(name)
                    payload[field] = value
                    with self.subTest(schema=name, field=field, value=value), self.assertRaises(ContractViolation):
                        check(SCHEMAS[name], payload)

    def test_staff_price_rows_use_same_endpoint_but_never_service_authority(self):
        ref = lambda name: {"$ref": "#/components/schemas/" + name}
        price = SPEC["paths"]["/api/v1/quote-steps/price"]["post"]
        self.assertEqual(price["x-auth-request-schemas"], {
            "ServiceCredential": ref("PriceRequest"), "StaffSession": ref("StaffPriceRequest")})
        self.assertEqual(price["requestBody"]["content"]["application/json"]["schema"]["oneOf"],
                         [ref("PriceRequest"), ref("StaffPriceRequest")])
        policy = SPEC["x-auth-projections"]
        self.assertEqual(policy, {
            "selector": "authenticated_credential", "client_selector_allowed": False,
            "mixed_credentials": "reject", "shared_service": "QuoteApplicationService",
            "shared_steps_required": ["draft", "match", "price", "xlsx", "pdf", "poll", "artifact"],
            "ServiceCredential": {"view": "CustomerEstimateView", "request_mode": "closed_service_schema"},
            "StaffSession": {"view": "staff_internal", "implementation_slice": "S3a",
                             "request_mode": "staff_schema", "preserve_owner_edit_grant_checks": True},
            "internal_only": ["review_choices", "profiles", "pricing_references", "basis_chat", "saved_sessions",
                              "ai_spend", "admin", "audit", "support", "platform_launch"],
        })
        staff = SCHEMAS["StaffPriceRequest"]
        self.assertEqual(set(staff["properties"]), {"quote_request_id", "basis_revision", "rows"})
        self.assertEqual(staff["required"], ["rows"])  # Pre-save normalise must not create a quote.
        self.assertEqual(staff["properties"]["rows"]["items"], ref("StaffBasisRow"))
        self.assertEqual((staff["properties"]["rows"]["minItems"], staff["properties"]["rows"]["maxItems"]),
                         (1, 500))
        row_schema = SCHEMAS["StaffBasisRow"]
        self.assertEqual(set(row_schema["properties"]), {"row_id", "description", "quantity", "unit", "selections"})
        self.assertEqual(row_schema["required"], ["row_id", "description", "quantity", "unit", "selections"])
        self.assertEqual(row_schema["properties"]["selections"], {
            "type": "array", "items": ref("Id"), "maxItems": 32, "uniqueItems": True})
        body = example("StaffPriceRequest")
        body["rows"][0].update(quantity=15, unit="sqm", selections=["selection-example"])
        check(staff, body)
        with self.assertRaises(ContractViolation):
            check(SCHEMAS["PriceRequest"], body)
        forged = example("PriceRequest")
        forged["rows"] = body["rows"]
        with self.assertRaises(ContractViolation):
            check(SCHEMAS["PriceRequest"], forged)
        for field in ("view", "role", "grant", "catalog", "price", "tax_rate", "price_book_id"):
            for nested in (False, True):
                forged = copy.deepcopy(body)
                (forged["rows"][0] if nested else forged)[field] = "forged.test"
                with self.subTest(field=field, nested=nested), self.assertRaises(ContractViolation):
                    check(staff, forged)
        for field, value in (("quantity", -1), ("quantity", True), ("unit", "m2"), ("selections", ["x", "x"])):
            forged = copy.deepcopy(body)
            forged["rows"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractViolation):
                check(staff, forged)

    def test_mandatory_staff_migration_and_credential_selected_view_are_documented(self):
        prose = " ".join(DOC.split())
        for requirement in (
            "In S3a the staff UI must use the same `/api/v1` endpoints and application service",
            "as external callers for draft, match, price and XLSX/PDF output, including polling and artifact delivery.",
            "The server derives the caller view from the authenticated credential, never a client flag.",
            "For staff-authenticated callers only, the same endpoint and service must accept staff-edited basis rows",
            "Service callers cannot select, upload or replace pricing authority.",
            "Poll first; there are no webhooks.",
            "Staff pricing before the first save is non-persisting and cannot grant copied authority.",
        ):
            self.assertIn(requirement, prose)
        migration = DOC.split("### Staff UI migration in S3a\n", 1)[1].split("## Authentication", 1)[0]
        rows = re.findall(r"^\| ([^|]+) \| ([^|]+) \|$", migration, re.M)
        moved = [destination for source, destination in rows if destination.startswith("Move to")]
        self.assertEqual(len(moved), 5)
        for path in ("/drafts", "/exports", "/api/v1/jobs/{job_id}", "/api/v1/quote-steps/price", "proof-bound artifact"):
            self.assertTrue(any(path in destination for destination in moved), path)
        retained = " ".join(source for source, destination in rows if destination.startswith("Stay internal"))
        for name in ("basis_chat", "pricing-selections", "/api/quote-sessions", "/api/settings/profiles",
                     "/api/profiles", "/api/settings/pricing-references", "/api/pricing-reference/template.xlsx",
                     "/api/feedback", "/api/session", "/api/log", "/api/health", "/logout",
                     "AI spend/admin/audit/support", "Platform launch/finalisation"):
            self.assertIn(name, retained)

    def test_money_rate_unit_poll_and_notes_constraints_directly(self):
        for value in ("0", "0.00", "1.00", "10.000", "120"):
            check(SCHEMAS["Money"], value)
        for value in ("-1.00", "01.00", "NaN", "Infinity", "1e2", "1.00\n", 1):
            with self.subTest(money=value), self.assertRaises(ContractViolation):
                check(SCHEMAS["Money"], value)
        rate = SCHEMAS["TaxView"]["properties"]["rate"]
        for value in ("0", "0.09", "1", "1.00"):
            check(rate, value)
        for value in ("9", "-0.09", "1.01", "01", "0.09\n", 0.09):
            with self.subTest(rate=value), self.assertRaises(ContractViolation):
                check(rate, value)
        # Every customer/staff row schema owns its actual unit restriction.
        for name, schema in SCHEMAS.items():
            unit = schema.get("properties", {}).get("unit")
            if unit:
                check(unit, "sqm")
                with self.subTest(unit_schema=name), self.assertRaises(ContractViolation):
                    check(unit, "m2")
        poll = SCHEMAS["JobView"]["properties"]["poll_after_seconds"]
        self.assertEqual((poll["minimum"], poll["maximum"]), (2, 60))
        for value in (2, 60):
            check(poll, value)
        for value in (1, 61):
            with self.assertRaises(ContractViolation):
                check(poll, value)
        for name in ("CreateQuoteRequest", "QueueDraftRequest"):
            notes = SCHEMAS[name]["properties"]["notes"]
            self.assertEqual(notes.get("maxLength"), 16384)
            check(notes, "a" * 16384)
            with self.assertRaises(ContractViolation):
                check(notes, "a" * 16385)

    def test_response_evolution_priced_revision_and_file_publication(self):
        self.assertEqual(SPEC["x-response-evolution"], {
            "clients_ignore_unknown_response_fields": True, "server_projection_remains_closed": True,
            "requests_remain_closed": True})
        self.assertIn("Clients must ignore unknown response fields", DOC)
        schema = SCHEMAS["BasisView"]
        self.assertIn("priced_basis_revision", schema["required"])
        for value in (None, 1):
            body = example("BasisView")
            body["priced_basis_revision"] = value
            check(schema, body)
        for value in (0, -1, True, "1"):
            body["priced_basis_revision"] = value
            with self.subTest(value=value), self.assertRaises(ContractViolation):
                check(schema, body)
        self.assertEqual(SPEC["x-result-publication"], {"requires_artifact_export": True, "json_only_export": False})
        self.assertIn("JSON-only use still requires an export requesting at least one XLSX/PDF artifact", DOC)
        for formats in (["xlsx"], ["pdf"], ["xlsx", "pdf"]):
            payload = example("ExportRequest")
            payload["formats"] = formats
            check(SCHEMAS["ExportRequest"], payload)
        for formats in ([], ["json"], ["xlsx", "xlsx"]):
            payload["formats"] = formats
            with self.subTest(formats=formats), self.assertRaises(ContractViolation):
                check(SCHEMAS["ExportRequest"], payload)

    def test_projection_has_no_forbidden_internal_fields_at_any_depth(self):
        # 'price' is a permitted customer line object, not a caller override.
        forbidden = FORBIDDEN - {"price"}
        self.assertLessEqual(set(SPEC["x-forbidden-fields"]), forbidden)
        for name in ("CustomerEstimateView", "BasisView", "JobView", "Error"):
            for nested in walk_schema(SCHEMAS[name]):
                self.assertFalse(forbidden & {key.lower() for key in nested.get("properties", {})})
        view = example("CustomerEstimateView")
        for field in forbidden:
            for target in (view, view["lines"][0], view["lines"][0]["price"], view["totals"], view["links"]):
                changed = copy.deepcopy(view)
                # Locate the corresponding nested object in the independent copy.
                location = (changed if target is view else changed["lines"][0] if target is view["lines"][0]
                            else changed["lines"][0]["price"] if target is view["lines"][0]["price"]
                            else changed["totals"] if target is view["totals"] else changed["links"])
                location[field] = "private.test"
                with self.subTest(field=field), self.assertRaises(ContractViolation):
                    check(SCHEMAS["CustomerEstimateView"], changed)

    def test_customer_wording_only_uses_prohibited_terms_in_explicit_list(self):
        prefix, remainder = DOC.split("### Explicit forbidden-field list\n", 1)
        forbidden_list, suffix = remainder.split("### Export safety\n", 1)
        for word in ("final quotation", "internal cost", "supplier"):
            self.assertIn(word, forbidden_list.lower())
            self.assertNotIn(word, (prefix + suffix).lower())
        self.assertIn("Please upload the booth render images first so I can analyze the design and prepare the quote.", DOC)
        self.assertIn("Contract proposal, not implemented", DOC)

    def test_money_partial_null_and_unresolved_semantics(self):
        view = example("CustomerEstimateView")
        check_estimate(view)
        negatives = []
        for field, bad in (("amount", 0), ("amount", "0.00"), ("unit_price", "0.00"),
                           ("display", "Included"), ("display", "Price to be discussed")):
            changed = copy.deepcopy(view)
            changed["lines"][1]["price"][field] = bad
            negatives.append(changed)
        for field, bad in (("total", "120.00"), ("subtotal", "120.00"), ("known_subtotal", "0.00"),
                           ("unresolved_line_count", 0), ("status", "complete"), ("label", "Total")):
            changed = copy.deepcopy(view)
            changed["totals"][field] = bad
            negatives.append(changed)
        for changed in negatives:
            with self.subTest(payload=changed), self.assertRaises(ContractViolation):
                check_estimate(changed)
        view["lines"] = [view["lines"][1]]
        view["totals"]["known_subtotal"] = None
        check_estimate(view)
        view["totals"]["known_subtotal"] = "0.00"
        with self.assertRaises(ContractViolation):
            check_estimate(view)

    def complete_view(self):
        view = example("CustomerEstimateView")
        view["lines"] = view["lines"][:1]
        view["totals"].update(status="complete", subtotal="120.00", total="120.00", unresolved_line_count=0)
        return view

    def test_complete_estimate_admin_tax_blank_and_configured(self):
        view = self.complete_view()
        check_estimate(view)
        self.assertIsNone(view["tax"])
        view["tax"] = {"label": "Tax", "rate": "0.09", "amount": "10.80"}
        view["totals"]["total"] = "130.80"
        check_estimate(view)
        for field, bad in (("amount", None), ("amount", "10.00"), ("rate", "9")):
            changed = copy.deepcopy(view)
            changed["tax"][field] = bad
            with self.assertRaises(ContractViolation):
                check_estimate(changed)
        partial = example("CustomerEstimateView")
        partial["tax"] = {"label": "Tax", "rate": "0.09", "amount": None}
        check_estimate(partial)
        partial["tax"]["amount"] = "10.80"
        with self.assertRaises(ContractViolation):
            check_estimate(partial)

    def test_currency_precision_zero_unknown_quantity_and_rounding(self):
        for currency, places, unit_price, amount in (("JPY", 0, "10", "15"), ("SGD", 2, "10.01", "15.02"),
                                                    ("KWD", 3, "10.001", "15.002")):
            view = self.complete_view()
            view.update(currency=currency, currency_decimal_places=places)
            view["lines"][0].update(quantity=1.5)
            view["lines"][0]["price"].update(unit_price=unit_price, amount=amount)
            view["totals"].update(known_subtotal=amount, subtotal=amount, total=amount)
            check_estimate(view)
        for bad in ("NaN", "Infinity", "1e2", "-1.00", "01.00", "10", 10.00, "10.000"):
            view = self.complete_view()
            view["lines"][0]["price"]["unit_price"] = bad
            with self.subTest(value=bad), self.assertRaises(ContractViolation):
                check_estimate(view)
        view = self.complete_view()
        view["lines"][0]["price"].update(unit_price="0.00", amount="0.00")
        view["totals"].update(known_subtotal="0.00", subtotal="0.00", total="0.00")
        check_estimate(view)  # Verified zero is distinct from unknown.
        view = example("CustomerEstimateView")
        view["lines"][1].update(quantity=None, unit=None, needs_confirmation=["Confirm quantity and unit."])
        check_estimate(view)
        view["lines"][1]["needs_confirmation"] = []
        with self.assertRaises(ContractViolation):
            check_estimate(view)

    def test_error_shape_codes_status_mapping_and_no_raw_fields(self):
        error = example("Error")
        check(SCHEMAS["Error"], error)
        code_map = SPEC["x-error-status-codes"]
        self.assertEqual(set(SCHEMAS["ErrorDetail"]["properties"]["code"]["enum"]),
                         {code for codes in code_map.values() for code in codes})
        for status, codes in code_map.items():
            schema = SPEC["components"]["responses"]["Error" + status]["content"]["application/json"]["schema"]
            for code in codes:
                changed = copy.deepcopy(error)
                changed["error"]["code"] = code
                check(schema, changed)
            changed["error"]["code"] = "UNREVIEWED_CODE"
            with self.assertRaises(ContractViolation):
                check(schema, changed)
        for field, bad in (("reference", "ERR-raw-provider-text"), ("reference", "ERR-0123abcd"),
                           ("code", "KeyError"), ("message", ""), ("retryable", "yes"),
                           ("provider_response", "private.test"), ("traceback", "private.test")):
            changed = copy.deepcopy(error)
            changed["error"][field] = bad
            with self.subTest(field=field), self.assertRaises(ContractViolation):
                check(SCHEMAS["Error"], changed)
        for field in error["error"]:
            changed = copy.deepcopy(error)
            del changed["error"][field]
            with self.assertRaises(ContractViolation):
                check(SCHEMAS["Error"], changed)
        poll = example("JobView")
        poll.update(status="failed", request_state="failed", error=error["error"])
        check(SCHEMAS["JobView"], poll)

    def test_idempotency_key_boundaries_and_post_requirements(self):
        for key in ("a", "request-example-001", "A._-0", "a" * 128):
            check(SCHEMAS["IdempotencyKey"], key)
        for key in ("", "a" * 129, " leading", "trailing ", "a,b", "a\nb", "a\n", "-a", "a/b", "a?", "\u00e9", 1, None):
            with self.subTest(key=key), self.assertRaises(ContractViolation):
                check(SCHEMAS["IdempotencyKey"], key)
        for path, methods in SPEC["paths"].items():
            for method, op in methods.items():
                if method == "post":
                    self.assertIn({"$ref": "#/components/parameters/IdempotencyKey"}, op["parameters"])
                    accepted = op["responses"]["202"]
                    self.assertTrue({"Location", "Retry-After"} <= accepted["headers"].keys())
        self.assertEqual(SPEC["x-idempotency"]["scope"], ["tenant", "caller", "method", "path", "key"])

    def test_replay_canonical_digest_conflict_expiry_and_restart(self):
        body = example("CreateQuoteRequest")
        first = self.model.create("tenant.test", "caller.test", "same-key", body)
        reversed_body = dict(reversed(list(body.items())))
        replay = self.model.create("tenant.test", "caller.test", "same-key", reversed_body, authority=2)
        self.assertEqual(first, replay)
        self.assertEqual(self.gateway.calls, 1)
        self.assertEqual(len(self.storage.queue), 1)
        self.assertEqual(self.storage.resources[first["quote_request_id"]]["authority"], 1)
        self.model = FakeContractModel(self.storage, self.clock, self.gateway)
        self.assertEqual(first, self.model.create("tenant.test", "caller.test", "same-key", body))
        for mutate in (lambda b: b.update(notes="Changed scope."),
                       lambda b: b["design_files"][0].update(data_url="data:image/png;base64,VEVTVA==")):
            changed = copy.deepcopy(body)
            mutate(changed)
            with self.assertRaisesRegex(ContractViolation, "IDEMPOTENCY_CONFLICT"):
                self.model.create("tenant.test", "caller.test", "same-key", changed)
        self.clock.advance(86399)
        self.assertEqual(first, self.model.create("tenant.test", "caller.test", "same-key", body))
        self.clock.advance(1)
        with self.assertRaisesRegex(ContractViolation, "RESULT_EXPIRED"):
            self.model.create("tenant.test", "caller.test", "same-key", body)
        self.assertEqual(self.gateway.calls, 1)

    def test_replay_isolation_revocation_and_key_scope(self):
        body = example("CreateQuoteRequest")
        records = []
        for tenant, caller in (("a.test", "caller.test"), ("b.test", "caller.test"), ("a.test", "other.test")):
            records.append(self.model.create(tenant, caller, "same-key", body))
        self.assertEqual(len({row["quote_request_id"] for row in records}), 3)
        resource_id = records[0]["quote_request_id"]
        for tenant, caller in (("b.test", "caller.test"), ("a.test", "other.test")):
            with self.assertRaisesRegex(ContractViolation, "NOT_FOUND"):
                self.model.read(tenant, caller, resource_id)
        with self.assertRaisesRegex(ContractViolation, "NOT_FOUND"):
            self.model.read("a.test", "caller.test", "missing")
        self.model.revoked.add(("a.test", "caller.test"))
        with self.assertRaisesRegex(ContractViolation, "AUTH_REQUIRED"):
            self.model.create("a.test", "caller.test", "same-key", body)
        self.assertEqual(self.gateway.calls, 3)
        self.assertEqual(SPEC["x-idempotency"]["scope"][-3:], ["method", "path", "key"])

    def test_receipt_scope_separates_methods_and_exact_paths(self):
        body = example("CreateQuoteRequest")
        # The model isolates receipt scope, not HTTP route dispatch.
        identities = set()
        for method, path in (("POST", "/api/v1/quote-requests"),
                             ("POST", "/api/v1/quote-requests/qr-a/drafts"),
                             ("POST", "/api/v1/quote-requests/qr-b/drafts"),
                             ("DELETE", "/api/v1/quote-requests/qr-a/drafts")):
            result = self.model.create("tenant.test", "caller.test", "key", body, method=method, path=path)
            identities.add(result["quote_request_id"])
        self.assertEqual(len(identities), 4)
        self.assertEqual(self.gateway.calls, 4)

    def test_state_machines_are_closed_exact_and_exhaustive(self):
        for key, edges, schema_name in (("x-request-transitions", REQUEST_EDGES, "RequestState"),
                                        ("x-job-transitions", JOB_EDGES, "JobState")):
            self.assertEqual({s: set(ts) for s, ts in SPEC[key].items()}, edges)
            self.assertEqual(set(SCHEMAS[schema_name]["enum"]), set(edges))
            for source, target in itertools.product(list(edges) + ["forged"], repeat=2):
                permitted = target in edges.get(source, set())
                self.assertEqual(target in SPEC[key].get(source, []), permitted)
                if schema_name == "RequestState":
                    self.storage.resources["state-test"] = {"state": source}
                    if permitted:
                        self.storage.transition("state-test", target)
                        self.assertEqual(self.storage.resources["state-test"]["state"], target)
                    else:
                        with self.assertRaises(ContractViolation):
                            self.storage.transition("state-test", target)
            for state in edges:
                self.assertNotIn(state, edges[state])
            self.assertEqual(edges["expired"] if "expired" in edges else edges["completed"], set())
        request_table = dict(re.findall(r"^\| (drafting|awaiting_confirmation|pricing|generating|completed|needs_input|failed|expired) \| ([^|]+) \|$",
                                       DOC.split("| Job state |")[0], re.M))
        self.assertEqual({s: set(v.strip().split(", ")) if v.strip() != "none" else set()
                          for s, v in request_table.items()}, REQUEST_EDGES)

    def test_confirmation_then_price_then_export_and_stale_revision(self):
        self.assertEqual(SPEC["x-confirmation-policy"], {
            "before_price": True, "revision_fields_must_equal_current": ["basis_revision", "confirmed_basis_revision"],
            "export_requires_priced_confirmed_revision": True, "authority_change_invalidates_confirmation": True})
        result = self.model.create("tenant.test", "caller.test", "key", example("CreateQuoteRequest"))
        resource_id = result["quote_request_id"]
        self.assertEqual(self.gateway.calls, 1)
        row = self.model.read("tenant.test", "caller.test", resource_id)
        self.assertIsNone(row["confirmed"])
        for command in ("price", "export"):
            with self.assertRaises(ContractViolation):
                self.model.command("tenant.test", "caller.test", resource_id, command, 1)
        with self.assertRaises(ContractViolation):
            self.model.command("tenant.test", "caller.test", resource_id, "export", 1, 1)
        with self.assertRaisesRegex(ContractViolation, "BASIS_STALE"):
            self.model.command("tenant.test", "caller.test", resource_id, "price", 2, 2)
        self.model.command("tenant.test", "caller.test", resource_id, "price", 1, 1)
        row["priced"] = 1  # Explicit synthetic worker completion.
        self.storage.transition(resource_id, "awaiting_confirmation")
        self.model.command("tenant.test", "caller.test", resource_id, "export", 1, 1)
        self.storage.transition(resource_id, "completed")
        self.model.command("tenant.test", "caller.test", resource_id, "draft", 1)
        self.assertEqual(row["revision"], 2)
        self.assertIsNone(row["priced"])
        self.assertIsNone(row["confirmed"])

    def test_command_state_owner_matrix(self):
        allowed = {"draft": {"awaiting_confirmation", "completed", "needs_input", "failed"},
                   "price": {"awaiting_confirmation"}, "export": {"awaiting_confirmation"}}
        self.assertEqual({op: set(states) for op, states in SPEC["x-operation-states"].items()}, allowed)
        principals = [("a.test", "caller.test", True), ("b.test", "caller.test", False),
                      ("a.test", "other.test", False)]
        for command, state, principal in itertools.product(allowed, REQUEST_EDGES, principals):
            tenant, caller, owns = principal
            self.storage.resources["matrix"] = {"owner": ("a.test", "caller.test"), "state": state,
                "revision": 1, "confirmed": 1, "priced": 1}
            with self.subTest(command=command, state=state, caller=principal):
                if owns and state in allowed[command]:
                    self.model.command(tenant, caller, "matrix", command, 1, 1)
                else:
                    with self.assertRaises(ContractViolation):
                        self.model.command(tenant, caller, "matrix", command, 1, 1)

    def test_endpoints_security_media_and_errors_agree(self):
        spec_endpoints = {(method.upper(), path) for path, methods in SPEC["paths"].items() for method in methods}
        doc_endpoints = set(re.findall(r"^\| (GET|POST|DELETE) \| `([^`]+)` \|", DOC, re.M))
        self.assertEqual(spec_endpoints, OPERATIONS)
        self.assertEqual(doc_endpoints, OPERATIONS)
        self.assertEqual(SPEC["openapi"], "3.1.0")
        self.assertEqual(SPEC["security"], [{"ServiceCredential": []}, {"StaffSession": []}])
        auth = SPEC["components"]["securitySchemes"]["ServiceCredential"]
        self.assertEqual((auth["type"], auth["in"], auth["name"]),
                         ("apiKey", "header", "X-SQAG-Service-Authorization"))
        schemes = SPEC["components"]["securitySchemes"]
        self.assertEqual(set(schemes), {"ServiceCredential", "StaffSession", "StaffCsrf"})
        for name, location, field in (("StaffSession", "cookie", "swooshz_quote_session"),
                                      ("StaffCsrf", "header", "X-Swooshz-CSRF")):
            self.assertEqual((schemes[name]["type"], schemes[name]["in"], schemes[name]["name"]),
                             ("apiKey", location, field))
            self.assertIn("S3a, not implemented", schemes[name]["description"])
        artifact = SPEC["paths"]["/api/v1/quote-requests/{id}/artifacts/{kind}"]["get"]
        self.assertEqual(artifact["parameters"][1]["schema"]["enum"], ["xlsx", "pdf"])
        self.assertEqual(set(artifact["responses"]["200"]["content"]),
                         {"application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        for methods in SPEC["paths"].values():
            for method, op in methods.items():
                self.assertNotIn("security", op)  # No operation can silently opt out of authentication.
                if method == "post":
                    self.assertEqual(op["x-staff-mutation-security"], [{"StaffSession": [], "StaffCsrf": []}])
                self.assertLessEqual(SPEC["x-error-status-codes"].keys(), op["responses"].keys())
                success = op["responses"].get("200", op["responses"].get("202"))
                self.assertEqual(success["headers"]["Cache-Control"]["schema"]["const"], "private, no-store")
        code_rows = re.findall(r"^\| (\d{3}) \| ([A-Z_, ]+) \|$", DOC, re.M)
        self.assertEqual({status: codes.strip().split(", ") for status, codes in code_rows},
                         SPEC["x-error-status-codes"])

    def test_limits_represent_owner_sandbox_policy_and_schema_boundaries(self):
        self.assertEqual(SPEC["x-retention-seconds"], {"uploads": 7 * 86400, "results": 30 * 86400, "replay_receipts": 86400})
        limits = SPEC["x-limits"]
        self.assertEqual(limits, {"files": 8, "image_bytes": 12 * 1024**2, "pdf_bytes": 48 * 1024**2,
            "pdf_pages_total": 96, "decoded_bytes_total": 128 * 1024**2, "notes_utf8_bytes": 16384,
            "basis_lines": 500, "creates_per_minute_per_tenant": 15, "active_jobs_per_tenant": 2,
            "accepted_requests_per_utc_day_per_tenant": 100, "polls_per_minute_per_caller": 120})
        for count in (0, 1, 8, 9):
            body = example("CreateQuoteRequest")
            body["design_files"] *= count
            if 1 <= count <= 8:
                check(SCHEMAS["CreateQuoteRequest"], body)

            else:
                with self.assertRaises(ContractViolation):
                    check(SCHEMAS["CreateQuoteRequest"], body)
        for notes, valid in (("a" * 16384, True), ("a" * 16385, False), ("\u00e9" * 8193, False)):
            body = example("CreateQuoteRequest")
            body["notes"] = notes
            if valid:
                self.model.create("tenant.test", "caller.test", "notes-key", body)
            else:
                with self.assertRaises(ContractViolation):
                    self.model.create("tenant.test", "caller.test", "other-key", body)
        for data_url in ("https://example.com/render.png", "data:text/plain;base64,VEVTVA==", "not-base64"):
            body = example("CreateQuoteRequest")
            body["design_files"][0]["data_url"] = data_url
            with self.assertRaises(ContractViolation):
                check(SCHEMAS["CreateQuoteRequest"], body)

    def test_machine_readable_money_policy_and_schema_ref_integrity(self):
        policy = SPEC["x-money-policy"]
        self.assertEqual(policy["unresolved_display"], "price to be discussed")
        self.assertEqual(SCHEMAS["UnresolvedPrice"]["properties"]["display"]["const"],
                         policy["unresolved_display"])
        self.assertEqual(SCHEMAS["Totals"]["properties"]["label"]["const"],
                         "Known-priced subtotal; excludes unresolved items and tax")
        self.assertEqual(policy["rounding"], "ROUND_HALF_UP")
        self.assertEqual(policy["tax_authority"], "tenant_admin")
        for key in ("unresolved_unit_price", "unresolved_amount", "partial_subtotal", "partial_total", "partial_tax_amount", "blank_tax"):
            self.assertIsNone(policy[key])

        def visit(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    self.assertTrue(node["$ref"].startswith("#/components/"))
                    target = SPEC
                    for part in node["$ref"][2:].split("/"):
                        self.assertIn(part, target)
                        target = target[part]
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(SPEC)


if __name__ == "__main__":
    unittest.main()
