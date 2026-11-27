"""Compatibility checker controls (#306 slice B). Runs in the fast "Quotation API spec" CI job."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from webapp.api_compat import compatibility_breaks
from webapp.api_spec import SPEC_PATH, build_spec
from scripts import check_api_compat

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "docs/api/quotation-api-v1-baseline.json"
BASELINE = build_spec()


def schemas(spec):
    return spec["components"]["schemas"]


def changed(mutate):
    spec = copy.deepcopy(BASELINE)
    mutate(spec)
    return compatibility_breaks(BASELINE, spec)


def add_operation(spec):
    spec["paths"]["/api/v1/quote-requests/{id}/history"] = {
        "get": copy.deepcopy(spec["paths"]["/api/v1/quote-requests/{id}"]["get"]) | {"operationId": "history"}}


BLOCKED = {
    "removed operation": (lambda s: s["paths"].pop("/api/v1/jobs/{job_id}"), "operation removed"),
    "renamed operation": (lambda s: s["paths"]["/api/v1/jobs/{job_id}"]["get"].update(operationId="poll_job"),
                          "operationId changed"),
    "stable moved to preview": (lambda s: s["paths"]["/api/v1/jobs/{job_id}"]["get"].update(
        {"x-stability": "preview"}), "moved to preview"),
    "removed request field": (lambda s: schemas(s)["CreateQuoteRequest"]["properties"].pop("notes"),
                              "field notes removed"),
    "removed response field": (lambda s: schemas(s)["JobView"]["properties"].pop("poll_after_seconds"),
                               "field poll_after_seconds removed"),
    "new required request field": (lambda s: (
        schemas(s)["QueueDraftRequest"]["properties"].update(reason={"type": "string"}),
        schemas(s)["QueueDraftRequest"]["required"].append("reason")), "field reason became required"),
    "request field became required": (lambda s: schemas(s)["CreateQuoteRequest"]["required"].append("notes"),
                                      "field notes became required"),
    "response field became optional": (lambda s: schemas(s)["AcceptedJob"]["required"].remove("links"),
                                       "field links became optional"),
    "narrowed request enum": (lambda s: schemas(s)["ExportRequest"]["properties"]["formats"]["items"]["enum"]
                              .remove("pdf"), "enum values removed: pdf"),
    "narrowed path enum": (lambda s: s["paths"]["/api/v1/quote-requests/{id}/artifacts/{kind}"]["get"]
                           ["parameters"][1]["schema"]["enum"].remove("pdf"), "enum values removed: pdf"),
    "narrowed response enum": (lambda s: schemas(s)["JobState"]["enum"].remove("failed"),
                               "enum values removed: failed"),
    "changed type": (lambda s: schemas(s)["Revision"].update(type="string"), "type changed"),
    "changed format": (lambda s: schemas(s)["Timestamp"].update(format="date"), "format changed"),
    "tighter request bound": (lambda s: schemas(s)["CreateQuoteRequest"]["properties"]["notes"].update(
        maxLength=100), "maxLength tightened"),
    "removed variant": (lambda s: schemas(s)["EstimateLine"]["properties"]["price"]["oneOf"].pop(),
                        "oneOf variant removed"),
    "new response variant": (lambda s: schemas(s)["EstimateLine"]["properties"]["price"]["oneOf"].append(
        {"type": "string"}), "variant added to a response"),
    "changed error status": (lambda s: (s["x-error-status-codes"]["409"].remove("BASIS_STALE"),
                                        s["x-error-status-codes"]["400"].append("BASIS_STALE")),
                             "error BASIS_STALE: status 409 changed to 400"),
    "removed error code": (lambda s: s["x-error-status-codes"]["503"].remove("RECOVERY_REQUIRED"),
                           "status 503 changed to removed"),
    "changed money rule": (lambda s: s["x-money-policy"].update(rounding="ROUND_HALF_EVEN"),
                           "money or rounding rule changed"),
    "lowered limit": (lambda s: s["x-limits"].update(files=4), "limit files: lowered"),
    "shrunk scope": (lambda s: s["x-scopes"]["quote.read"].remove("poll"), "lost operations poll"),
    "removed response": (lambda s: s["paths"]["/api/v1/jobs/{job_id}"]["get"]["responses"].pop("200"),
                         "response 200 removed"),
    "removed parameter": (lambda s: s["paths"]["/api/v1/quote-requests"]["post"]["parameters"].clear(),
                          "parameter Idempotency-Key removed"),
}

ALLOWED = {
    "new operation": add_operation,
    "new optional request field": lambda s: schemas(s)["QueueDraftRequest"]["properties"].update(
        reason={"type": "string"}),
    "new response field": lambda s: (schemas(s)["JobView"]["properties"].update(progress={"type": "integer"}),
                                     schemas(s)["JobView"]["required"].append("progress")),
    "new response enum value": lambda s: schemas(s)["JobState"]["enum"].append("cancelled"),
    "new request enum value": lambda s: schemas(s)["ExportRequest"]["properties"]["formats"]["items"]["enum"]
    .append("csv"),
    "looser request bound": lambda s: schemas(s)["CreateQuoteRequest"]["properties"]["notes"].update(maxLength=32768),
    "higher limit": lambda s: s["x-limits"].update(files=12),
    "new error code": lambda s: s["x-error-status-codes"]["429"].append("SLOW_DOWN"),
    "new scope operation": lambda s: s["x-scopes"]["quote.read"].append("history"),
    "description change": lambda s: s["paths"]["/api/v1/jobs/{job_id}"]["get"].update(summary="Poll a job"),
}


class CompatibilityTest(unittest.TestCase):
    def test_each_blocked_change_kind_fails(self):
        for name, (mutate, expected) in BLOCKED.items():
            with self.subTest(change=name):
                breaks = changed(mutate)
                self.assertTrue(any(expected in line for line in breaks), breaks)

    def test_each_allowed_change_kind_passes(self):
        for name, mutate in ALLOWED.items():
            with self.subTest(change=name):
                self.assertEqual(changed(mutate), [])

    def test_a_preview_operation_is_outside_the_promise(self):
        baseline = copy.deepcopy(BASELINE)
        add_operation(baseline)
        baseline["paths"]["/api/v1/quote-requests/{id}/history"]["get"]["x-stability"] = "preview"
        self.assertEqual(compatibility_breaks(baseline, copy.deepcopy(BASELINE)), [])

    def test_committed_spec_is_compatible_with_the_committed_baseline(self):
        self.assertEqual(compatibility_breaks(json.loads(BASELINE_PATH.read_text(encoding="utf-8")),
                                              json.loads(SPEC_PATH.read_text(encoding="utf-8"))), [])

    def test_cli_exits_1_and_names_each_break(self):
        with tempfile.TemporaryDirectory() as folder:
            spec = copy.deepcopy(BASELINE)
            spec["paths"].pop("/api/v1/jobs/{job_id}")
            spec_path, baseline_path = Path(folder) / "spec.json", Path(folder) / "baseline.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            baseline_path.write_text(json.dumps(BASELINE), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = check_api_compat.main(["--spec", str(spec_path), "--baseline", str(baseline_path)])
            self.assertEqual(code, 1)
            self.assertIn("GET /api/v1/jobs/{job_id}: operation removed", err.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_api_compat.main(["--spec", str(baseline_path), "--baseline",
                                                        str(baseline_path)]), 0)


if __name__ == "__main__":
    unittest.main()
