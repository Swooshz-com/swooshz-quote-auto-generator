"""Deterministic Responses contract: synthetic media, mocked send, network denied."""
import base64
import copy
import functools
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import unittest
import urllib.error
from unittest import mock

from PIL import Image
import pypdfium2 as pdfium

from webapp import server
from tests.gateway_test_support import synthetic_configuration, json_response, FixtureHttpResponse
from tests.gateway_test_support import install_mock_responses_transport
from webapp.ai_gateway import OPENROUTER_RESPONSES_URL

MISSING = object()


@functools.lru_cache(maxsize=32)
def synthetic_image(format="PNG", size=(2, 2), color="white"):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=format)
    return "data:image/" + format.lower() + ";base64," + base64.b64encode(stream.getvalue()).decode("ascii")


@functools.lru_cache(maxsize=16)
def synthetic_pdf(pages=1):
    stream = io.BytesIO()
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            page = document.new_page(72, 72)
            page.close()
        document.save(stream)
    finally:
        document.close()
    return "data:application/pdf;base64," + base64.b64encode(stream.getvalue()).decode("ascii")



@functools.lru_cache(maxsize=1)
def shared_browser_media_records():
    """Import only the pure seed module; no browser/server/provider is started."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is required to verify the shared browser seed.")
    root = Path(__file__).resolve().parents[1]
    probe = r"""
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { deflateSync } from "node:zlib";
import { pathToFileURL } from "node:url";
const root = process.argv[1];
const fixture = await import(pathToFileURL(path.join(root, "scripts/playwright-test-seeded-setup.mjs")));
const source = fs.readFileSync(path.join(root, "scripts/playwright-smoke.mjs"), "utf8");
const begin = source.indexOf("function g3SyntheticPngDataUrl() {");
const end = source.indexOf("\nconst G3_SYNTHETIC_PNG_DATA_URL", begin);
if (begin < 0 || end < 0) throw new Error("G3 public PNG source missing.");
const png = vm.runInNewContext(source.slice(begin, end) + "\ng3SyntheticPngDataUrl();", { Buffer, deflateSync });
const cases = [
  ["default_pdf", undefined],
  ["explicit_undefined", { reference: undefined }],
  ["custom_pdf", { reference: { name: "custom-reference.pdf", data_url: fixture.TEST_REFERENCE_MEDIA.data_url } }],
  ["g3_png", { reference: { name: "synthetic-g3-booth-render.png", data_url: png } }],
];
const records = [];
for (const [label, options] of cases) {
  const state = { profiles: [{}], images: [] };
  const persisted = [];
  const noop = () => {};
  const page = {
    locator: () => ({ waitFor: async () => {} }),
    evaluate: async (callback, argument) => {
      const browser = {
        argument, state,
        renderProfileOptions: noop, renderPresetOptions: noop,
        selectPricingReferenceOptionValue: noop, firstPricingReferenceOptionValue: noop,
        selectPresetValue: noop, firstAvailablePresetValue: noop,
        loadSelectedPreset: noop, updateGeneratorCopy: noop, applyQuoteDetails: noop,
        ensureContentFingerprint: async record => record,
        sessionFileRecordsFromDraft: () => state.images,
        persistSessionFiles: async rows => { persisted.push(structuredClone(rows)); },
        saveSessionState: noop, renderFiles: noop, setImageUploadStatus: noop,
        setWorkflowStage: noop, syncControlStates: noop,
      };
      await vm.runInNewContext("(" + callback.toString() + ")(argument)", browser);
    },
  };
  await fixture.seedQuoteDraftFromTestFixture(page, options);
  records.push({ label, record: state.images[0], persisted });
}
const invalid = [
  { ...fixture.TEST_REFERENCE_MEDIA, size: fixture.TEST_REFERENCE_MEDIA.size + 1 },
  { ...fixture.TEST_REFERENCE_MEDIA, type: "image/png" },
  { name: "custom-reference.pdf", data_url: "data:application/pdf;base64,!!!!" },
];
const rejected = [];
for (const [label, record] of [
  ["null", null], ["false", false], ["zero", 0], ["empty_string", ""],
  ...invalid.map((record, index) => ["metadata_" + index, record]),
]) {
  let pageEffects = 0;
  const page = {
    locator: () => { pageEffects++; return { waitFor: async () => { pageEffects++; } }; },
    evaluate: async () => { pageEffects++; },
  };
  let refused = false;
  try { await fixture.seedQuoteDraftFromTestFixture(page, { reference: record }); }
  catch { refused = true; }
  rejected.push({ label, refused, pageEffects });
}
console.log(JSON.stringify({ exported_default: fixture.TEST_REFERENCE_MEDIA, records, rejected }));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", probe, str(root)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
        timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return json.loads(result.stdout)

class DraftRequestContractTest(unittest.TestCase):
    def setUp(self):
        install_mock_responses_transport(self)
        self.real_read_dotenv_value = server.read_dotenv_value
        self.enterContext(mock.patch.dict(server.os.environ, {"APP_MODE": "local"}))
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Outbound network denied")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("Outbound network denied")))
        self.enterContext(mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader({**synthetic_configuration(credential="synthetic-key"), "OPENROUTER_API_KEY":"synthetic-key",
            server.OPENROUTER_DRAFT_MODEL_ENV_NAME: "openai/gpt-6.1-sol",
            server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME: "high",
            server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME: "openai/gpt-6-astra",
            server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME: "high",
        })))
        self.enterContext(mock.patch.object(server, "build_quote_draft_prompt", return_value="Synthetic prompt"))
        self.catalog = self.enterContext(mock.patch.object(server, "catalog_visual_image_entries_for_payload", return_value=[]))
        self.real_pdf_pages = server.pdf_reference_page_images
        self.enterContext(mock.patch.object(server, "normalize_ai_draft", return_value={"ok": True}))
        response = json_response("openai/gpt-6.1-sol", {})
        self.send = self.enterContext(mock.patch.object(server.urllib.request, "urlopen", return_value=response))
        self.image = synthetic_image()
        self.pdf = synthetic_pdf()
        pricing_detail = {
            "id": "synthetic-draft-contract-reference",
            "label": "Synthetic draft contract reference",
            "source": "local",
            "currency": "SGD",
            "tax": {"label": "GST", "rate": 0},
            "items": [],
        }
        pricing_detail["digest_sha256"] = server.pricing_reference_catalog_digest(pricing_detail)
        self.pricing_snapshot = server.PricingReferenceSnapshot(
            source="local",
            reference_id=pricing_detail["id"],
            digest=pricing_detail["digest_sha256"],
            currency="SGD",
            detail_json=json.dumps(pricing_detail, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        self.pricing_detail = pricing_detail

    def payload(self, *urls):
        return {
            "images": [{"name": f"synthetic-{i}", "data_url": url} for i, url in enumerate(urls or (self.image,))],
            "pricing_reference_id": self.pricing_snapshot.reference_id,
            "pricing_reference_source": self.pricing_snapshot.source,
            "pricing_reference": self.pricing_detail,
            "_pricing_reference_snapshot": self.pricing_snapshot,
        }

    def capture(self, payload=None):
        server.request_openai_quote_basis(payload or self.payload(), "synthetic-key")
        request = self.send.call_args.args[0]
        self.assertEqual(request.full_url, OPENROUTER_RESPONSES_URL)
        self.assertEqual(request.method, "POST")
        return {k:v for k,v in json.loads(request.data).items() if k not in {"provider", "store"}}

    def mock_batch_responses(self):
        def send(request, **kwargs):
            body = json.loads(request.data)
            parts = body["input"][0]["content"]
            if parts[0]["text"].startswith("Inspect each attached visual"):
                return json_response(
                    body["model"], {"observations": ["Observed scope"] * (len(parts) - 1)},
                    usage={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20, "cost": 0.125},
                )
            return json_response(body["model"], {})
        self.send.side_effect = send

    def assert_rejected(self, payload, forbidden_values=()):
        self.send.reset_mock()
        self.send.side_effect = None
        with self.assertRaises(server.OpenAIAnalysisError) as error:
            server.request_openai_quote_basis(payload, "synthetic-key")
        self.assertEqual(error.exception.diagnostics["failure_boundary"], "request_validation")
        self.assertEqual(error.exception.diagnostics["attempt_number"], 0)
        diagnostic_text = str(error.exception) + json.dumps(error.exception.diagnostics, sort_keys=True)
        for value in forbidden_values:
            self.assertNotIn(value, diagnostic_text)
        self.send.assert_not_called()

    @staticmethod
    def dotenv_reader(values):
        return lambda name: values.get(name, "")

    def envelope(self, model="openai/gpt-6.1-sol", effort="high"):
        return {
            "model": model,
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Synthetic prompt"},
                    {"type": "input_image", "image_url": self.image, "detail": "high"},
                ],
            }],
            "reasoning": {"effort": effort},
        }

    def test_valid_exact_envelopes_and_media_order(self):
        for urls in [(self.image,), (self.pdf,), (self.image, self.pdf), (self.pdf, self.image)]:
            with self.subTest(kinds=[url.split(";")[0] for url in urls]):
                self.send.reset_mock()
                body = self.capture(self.payload(*urls))
                self.send.assert_called_once()
                expected = [{"type": "input_text", "text": "Synthetic prompt"}]
                for index, url in enumerate(urls):
                    if url == self.pdf:
                        expected.extend([
                            {"type": "input_file", "filename": f"synthetic-{index}", "file_data": url},
                            {"type": "input_text", "text": (
                                f"Rendered PDF page images follow for synthetic-{index} (pages 1). "
                                "Use these images for visual layout, labels, dimensions, and render details; "
                                "the original PDF file remains attached for full document context."
                            )},
                            {"type": "input_image", "image_url": self.real_pdf_pages(
                                {"name": "synthetic.pdf", "data_url": url}, retain_debug=False,
                            )[0]["data_url"], "detail": "high"},
                        ])
                    else:
                        expected.append({"type": "input_image", "image_url": url, "detail": "high"})
                self.assertEqual(body, {"model": "openai/gpt-6.1-sol", "input": [{"role": "user", "content": expected}], "reasoning": {"effort": "high"}})

    def test_shared_browser_media_records_pass_actual_validator(self):
        observed = shared_browser_media_records()
        self.assertEqual(observed["records"][0]["record"], observed["exported_default"])
        self.assertEqual(observed["exported_default"]["name"], "test-workspace-reference.pdf")
        self.assertEqual(observed["exported_default"]["type"], "application/pdf")
        for case in observed["records"]:
            with self.subTest(case=case["label"]):
                record = case["record"]
                self.assertEqual(case["persisted"], [[record]])
                raw = base64.b64decode(record["data_url"].partition(",")[2], validate=True)
                self.assertEqual(record["size"], len(raw))
                self.assertTrue(record["data_url"].startswith("data:" + record["type"] + ";base64,"))
                self.assertEqual(server.validate_draft_references({"images": [record]}), [record])
                if record["type"] == "application/pdf":
                    document = pdfium.PdfDocument(io.BytesIO(raw))
                    try:
                        self.assertEqual(len(document), 1)
                        page = document[0]
                        try:
                            self.assertEqual(page.get_size(), (72.0, 72.0))
                        finally:
                            page.close()
                    finally:
                        document.close()
                else:
                    self.assertEqual(record["type"], "image/png")
        self.send.assert_not_called()

    def test_shared_browser_media_rejects_incoherent_records(self):
        for case in shared_browser_media_records()["rejected"]:
            with self.subTest(case=case["label"]):
                self.assertTrue(case["refused"])
                self.assertEqual(case["pageEffects"], 0)
        self.send.assert_not_called()

    def test_g3_seeds_its_actual_png_without_temporary_pdf(self):
        observed = shared_browser_media_records()
        case = next(case for case in observed["records"] if case["label"] == "g3_png")
        record = case["record"]
        self.assertEqual(case["persisted"], [[record]])
        self.assertEqual(record["name"], "synthetic-g3-booth-render.png")
        self.assertEqual(server.validate_draft_media(record["data_url"]), "image/png")
        self.send.assert_not_called()

    def test_header_only_browser_seed_rejects_before_transport(self):
        malformed = "data:application/pdf;base64,JVBERi0xLjQKJVRlc3QK"
        self.assertEqual(len(base64.b64decode(malformed.partition(",")[2])), 15)
        self.assert_rejected(self.payload(malformed))

    def test_supported_image_formats(self):
        for format in ("JPEG", "PNG", "WEBP"):
            url = synthetic_image(format)
            self.assertEqual(self.capture(self.payload(url))["input"][0]["content"][1]["image_url"], url)

    def test_catalog_and_pdf_page_semantics(self):
        self.catalog.return_value = [{"data_url": self.image, "id": "synthetic"}]
        parts = self.capture(self.payload(self.pdf, self.image))["input"][0]["content"]
        self.assertEqual([p["type"] for p in parts], ["input_text", "input_file", "input_text", "input_image", "input_image", "input_text", "input_image"])
        self.assertEqual([p["detail"] for p in parts if p["type"] == "input_image"], ["high", "high", "low"])
        self.assertEqual(parts[1]["file_data"], self.pdf)

    def test_prompt_reads_the_captured_pricing_reference_snapshot(self):
        payload = self.payload()
        detail = {
            "id": "synthetic-prompt-snapshot",
            "label": "Synthetic prompt snapshot",
            "source": "local",
            "currency": "SGD",
            "items": [{
                "id": "synthetic-snapshot-line",
                "section": "Synthetic",
                "description": "nos snapshot item",
                "unit_hint": "nos",
                "internal_cost": 10,
                "markup_multiplier": 1.25,
                "sale_unit_price": 12.5,
            }],
        }
        detail["digest_sha256"] = server.pricing_reference_catalog_digest(detail)
        payload.update({
            "pricing_reference_id": detail["id"],
            "pricing_reference_source": detail["source"],
            "pricing_reference": detail,
            "_pricing_reference_snapshot": server.PricingReferenceSnapshot(
                source=detail["source"],
                reference_id=detail["id"],
                digest=detail["digest_sha256"],
                currency=detail["currency"],
                detail_json=json.dumps(detail, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            ),
        })

        def prompt_from_snapshot(bound_payload, auth_session=None):
            rows = server.pricing_catalog_prompt_rows_for_payload(
                bound_payload,
                server.profile_id_from_payload(bound_payload),
                auth_session=auth_session,
            )
            self.assertEqual([row["id"] for row in rows], ["synthetic-snapshot-line"])
            self.assertEqual(rows[0]["internal_cost"], 10)
            self.assertEqual(rows[0]["markup_multiplier"], 1.25)
            return json.dumps({"pricing_catalog": rows}, sort_keys=True)

        with mock.patch.object(server, "build_quote_draft_prompt", side_effect=prompt_from_snapshot):
            body = self.capture(payload)
        prompt_text = body["input"][0]["content"][0]["text"]
        self.assertIn("synthetic-snapshot-line", prompt_text)

    def test_invalid_supplied_media_rejects_whole_request(self):
        malformed = [None, 3, "", self.image + "\n", self.image + ",extra", self.image.replace(";base64", ";charset=utf8;base64"), "data:image/png;base64,", "data:image/png;base64,!!!!", "data:image/png;base64,Zg===", "data:image/png;base64,Zh==", self.image.replace("image/png", "image/gif"), self.image.replace("image/png", "image/jpeg"), "https://private.invalid/render", "data:application/pdf;base64,JVBERi0xLjQK", "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\ncorrupt").decode()]
        for url in malformed:
            with self.subTest(case=malformed.index(url)):
                self.assert_rejected(self.payload(self.image, url))
        for entries in [None, {}, [], [None], [{"data_url": self.image}], [{"name": "ignored", "type": "text/plain"}]]:
            self.assert_rejected({"images": entries})

    def test_draft_pre_send_media_diagnostics_disambiguate_without_sending(self):
        corrupt = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\ncorrupt").decode("ascii")
        cases = (
            ("reference_media", "media_dimension_limit", synthetic_image(size=(4097, 1)), []),
            ("reference_media", "media_decode_failed", corrupt, []),
            ("catalog_media", "media_decode_failed", self.image, [{"data_url": corrupt}]),
        )
        for stage, reason, upload, catalog in cases:
            with self.subTest(stage=stage, reason=reason), mock.patch.object(
                server, "pricing_reference_authority_error", return_value=""
            ), mock.patch.object(server, "write_local_log") as logs:
                self.catalog.return_value = catalog
                self.send.reset_mock()
                payload = self.payload(upload)
                payload["images"][0]["name"] = "synthetic-private-filename.png"
                server.finish_draft_job("job-synthetic", payload)
                self.send.assert_not_called()
                attempts = [call.args[1] for call in logs.call_args_list if call.args[0] == "ai_call_attempt"]
                outer = [call.args[1] for call in logs.call_args_list if call.args[0] == "draft_failed"]
                self.assertEqual(len(attempts), 1)
                self.assertEqual(len(outer), 1)
                for record in (attempts[0], outer[0]):
                    self.assertEqual(record["failure_boundary"], "request_validation")
                    self.assertEqual(record["attempt_number"], 0)
                    self.assertEqual(record["request_validation_stage"], stage)
                    self.assertEqual(record["request_validation_reason"], reason)
                    self.assertNotIn("synthetic-private-filename", str(record))
                    self.assertNotIn("data:image", str(record))
        self.catalog.return_value = []
        self.capture()
        self.send.assert_called_once()
        self.assertEqual(server.safe_ai_output_diagnostics({
            "request_validation_stage": "arbitrary_value",
            "request_validation_reason": "arbitrary_value",
        }), {})

    def test_invalid_assembled_envelope(self):
        valid = self.capture()
        cases = []
        for key in ("messages", "temperature", "max_tokens", "max_output_tokens", "tools", "tool_choice"):
            body = copy.deepcopy(valid)
            body[key] = []
            cases.append(body)
        for patch in [{"file": {"file_data": self.pdf}}, {"image_url": {"url": self.image}}, {"detail": "auto"}, {"type": "image_url"}]:
            body = copy.deepcopy(valid)
            body["input"][0]["content"][1].update(patch)
            cases.append(body)
        for body in cases:
            with self.assertRaises(server.OpenAIAnalysisError):
                server.validate_draft_responses_envelope(body, "standard")
        self.catalog.return_value = [{"data_url": "data:image/png;base64,ZmFrZQ=="}]
        self.assert_rejected(self.payload())

    def test_reference_and_catalog_count_boundaries(self):
        for count in (server.MAX_REFERENCE_IMAGES - 1, server.MAX_REFERENCE_IMAGES, server.MAX_REFERENCE_IMAGES + 1):
            payload = self.payload(*([self.image] * count))
            if count > server.MAX_REFERENCE_IMAGES:
                self.assert_rejected(payload)
            else:
                self.assertEqual(len(self.capture(payload)["input"][0]["content"]), count + 1)
        for count in (server.MAX_PROMPT_CATALOG_VISUAL_IMAGES - 1, server.MAX_PROMPT_CATALOG_VISUAL_IMAGES, server.MAX_PROMPT_CATALOG_VISUAL_IMAGES + 1):
            self.catalog.return_value = [{"data_url": self.image}] * count
            if count > server.MAX_PROMPT_CATALOG_VISUAL_IMAGES:
                self.assert_rejected(self.payload())
            else:
                self.capture()

    def test_pdf_byte_limit_matches_browser_json_and_preserves_aggregate_ceiling(self):
        template = base64.b64decode(self.pdf.partition(",")[2])
        end = template.rindex(b"startxref")
        self.assertEqual(server.MAX_PDF_BYTES, 24 * 1024 * 1024)
        for size in (server.MAX_PDF_BYTES - 1, server.MAX_PDF_BYTES, server.MAX_PDF_BYTES + 1):
            with self.subTest(size=size):
                # Insert a synthetic PDF comment after xref so object offsets stay valid.
                padding = size - len(template)
                pdf = template[:end] + b"%" + b"x" * (padding - 2) + b"\n" + template[end:]
                data_url = "data:application/pdf;base64," + base64.b64encode(pdf).decode("ascii")
                payload = self.payload(data_url)
                if size > server.MAX_PDF_BYTES:
                    self.assert_rejected(payload)
                    continue
                # Exercise the same JSON and base64 encodings as the browser.
                direct_body = json.dumps(payload | {"_pricing_reference_snapshot": None}).encode()
                job_body = json.dumps({"type": "draft", "payload": json.loads(direct_body)}).encode()
                self.assertLessEqual(len(direct_body), server.request_body_limit("/api/draft"))
                self.assertLessEqual(len(job_body), server.request_body_limit("/api/jobs"))
                handler = object.__new__(server.QuoteRunnerHandler)
                handler.path = "/api/draft"
                handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(direct_body))}
                handler.rfile = io.BytesIO(direct_body)
                self.assertEqual(handler.read_json()["images"][0]["data_url"], data_url)
                self.send.reset_mock()
                self.mock_batch_responses()
                self.capture(payload)
                self.assertEqual(self.send.call_count, 2)
                self.assertNotIn("input_file", [part["type"] for call in self.send.call_args_list
                    for part in json.loads(call.args[0].data)["input"][0]["content"]])
        self.assertEqual(server.MAX_IMAGE_BYTES, 12 * 1024 * 1024)
        self.assertEqual(server.MAX_DRAFT_INPUT_FILE_TOTAL_BYTES, 50_000_000)

    def test_input_file_aggregate_bytes_boundaries_and_five_pdf_reproducer(self):
        decoded_pdf_bytes = len(base64.b64decode(self.pdf.split(",", 1)[1]))
        self.mock_batch_responses()
        for count, limit, expected_sends in ((2, 1, 2), (2, 2, 1), (5, 4, 2)):
            with self.subTest(count=count, limit=limit):
                self.send.reset_mock()
                with mock.patch.object(server, "MAX_DRAFT_INPUT_FILE_TOTAL_BYTES", limit * decoded_pdf_bytes):
                    self.capture(self.payload(*([self.pdf] * count)))
                self.assertEqual(self.send.call_count, expected_sends)

    def test_full_draft_model_and_effort_are_separate_by_mode(self):
        branches = (
            (
                "standard",
                server.DRAFT_ANALYSIS_MODE_STANDARD,
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
                "openai/gpt-6.1-sol",
                "high",
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
            ),
            (
                "high_quality",
                server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                "openai/gpt-6-astra",
                "high",
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
            ),
        )
        for label, mode, model_name, effort_name, model, effort, unselected_model, unselected_effort in branches:
            with self.subTest(mode=label):
                values = {**synthetic_configuration(credential="synthetic-key"),
                    model_name: model, effort_name: effort,
                    unselected_model: "synthetic/unselected", unselected_effort: "high",
                }
                payload = self.payload(self.pdf, self.image)
                if mode == server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY:
                    payload["analysis_mode"] = "high_quality"
                self.send.reset_mock()
                self.send.return_value = json_response(model, {})
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    body = self.capture(payload)
                self.send.assert_called_once()
                self.assertEqual(body["model"], model)
                self.assertEqual(body["reasoning"], {"effort": effort})
                self.assertEqual(
                    [part["type"] for part in body["input"][0]["content"]],
                    ["input_text", "input_file", "input_text", "input_image", "input_image"],
                )
                self.assertEqual(body["input"][0]["content"][1]["file_data"], self.pdf)
                self.assertEqual(body["input"][0]["content"][4]["image_url"], self.image)


    def test_full_draft_explicit_configuration_is_the_only_selection_authority(self):
        for mode, model_name, effort_name, effort in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME, "high"),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME, "max"),
        ):
            cases = [
                ("openai/gpt-6.1-sol", configured_effort)
                for configured_effort in ("low", "medium", "high", "xhigh", "max")
            ] + [
                ("openai/gpt-6-astra", "high"),
                ("synthetic/future-provider-model-2099", "high"),
                ("synthetic/future-model.v2-operator-123", "max"),
                ("openai/gpt-6-sol", "none"),
                ("synthetic/" + "M" * 134, "high"),
            ]
            for model_value, effort_value in cases:
                with self.subTest(mode=mode, model=model_value, effort=effort_value):
                    values = synthetic_configuration(credential="synthetic-key") | {
                        name: value for name, value in ((model_name, model_value), (effort_name, effort_value))
                        if value is not MISSING
                    }
                    payload = self.payload(self.pdf, self.image)
                    payload["analysis_mode"] = mode
                    self.send.reset_mock()
                    self.send.return_value = json_response(model_value, {})
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                        body = self.capture(payload)
                    self.send.assert_called_once()
                    self.assertEqual(body["model"], model_value)
                    self.assertEqual(body["reasoning"], {"effort": effort_value.strip().lower()})


    def test_missing_full_draft_configuration_rejects_without_source_or_other_mode_fallback(self):
        for mode, model_name, effort_name, unselected_model, unselected_effort in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME, server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME),
        ):
            for missing_name in (model_name, effort_name):
                for missing_value in (MISSING, "", " \t "):
                    with self.subTest(mode=mode, setting=missing_name, value=missing_value):
                        values = {**synthetic_configuration(credential="synthetic-key"), model_name: "openai/gpt-6.1-sol", effort_name: "high", unselected_model: "openai/gpt-6-luna", unselected_effort: "max"}
                        if missing_value is MISSING:
                            values.pop(missing_name)
                        else:
                            values[missing_name] = missing_value
                        payload = self.payload()
                        payload["analysis_mode"] = mode
                        with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                            self.assert_rejected(payload)

    def test_high_quality_astra_high_six_page_pdf_reaches_mocked_transport(self):
        pdf = synthetic_pdf(6)
        payload = self.payload(pdf)
        payload["analysis_mode"] = "high_quality"
        values = {**synthetic_configuration(credential="synthetic-key"),
            server.OPENROUTER_DRAFT_MODEL_ENV_NAME: "openai/gpt-6-luna",
            server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME: "max",
            server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME: "openai/gpt-6-astra",
            server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME: "high",
        }
        self.send.reset_mock()
        self.send.return_value = json_response("openai/gpt-6-astra", {})
        with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
            with mock.patch.object(server, "pdf_reference_page_images", wraps=self.real_pdf_pages):
                with mock.patch.object(server, "persist_pdf_page_debug_images") as retain:
                    body = self.capture(payload)
        self.send.assert_called_once()
        self.assertEqual(body["model"], "openai/gpt-6-astra")
        self.assertEqual(body["reasoning"], {"effort": "high"})
        content = body["input"][0]["content"]
        self.assertEqual(content[1]["file_data"], pdf)
        self.assertEqual(sum(part["type"] == "input_image" for part in content), min(6, server.MAX_RENDERED_PDF_PAGES))
        retain.assert_not_called()


    def test_deploy_missing_or_blank_settings_never_use_poisoned_dotenv(self):
        for mode, model_name, effort_name in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME),
        ):
            poison = {**synthetic_configuration(credential="synthetic-key"), model_name: "openai/gpt-6.1-sol", effort_name: "xhigh"}
            for missing_name in (model_name, effort_name):
                for missing_value in (MISSING, "", " \t "):
                    with self.subTest(mode=mode, setting=missing_name, value=missing_value):
                        env = {**synthetic_configuration(credential="synthetic-key"), "OPENROUTER_API_KEY":"synthetic-key", "APP_MODE": "deploy", **poison}
                        if missing_value is MISSING:
                            env.pop(missing_name)
                        else:
                            env[missing_name] = missing_value
                        payload = self.payload()
                        payload["analysis_mode"] = mode
                        with mock.patch.dict(server.os.environ, env, clear=True):
                            with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(poison)) as reader:
                                self.assert_rejected(payload)
                        self.assertFalse(any(call.args[0] in poison for call in reader.call_args_list))

            # Explicit deployed settings also win over divergent local values.
            env = {**synthetic_configuration(credential="synthetic-key"), "OPENROUTER_API_KEY":"synthetic-key", "APP_MODE": "deploy", model_name: "openai/gpt-6.1-sol", effort_name: "max"}
            payload = self.payload()
            payload["analysis_mode"] = mode
            self.send.reset_mock()
            with mock.patch.dict(server.os.environ, env, clear=True):
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader({model_name: "openai/gpt-6-luna", effort_name: "none"})):
                    body = self.capture(payload)
            self.send.assert_called_once()
            self.assertEqual(body["model"], "openai/gpt-6.1-sol")
            self.assertEqual(body["reasoning"], {"effort": "max"})

    def test_wrapper_payload_and_telemetry_share_one_immutable_configuration(self):
        for mode, model_name, effort_name, effort in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME, "high"),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME, "max"),
        ):
            with self.subTest(mode=mode):
                values = {**synthetic_configuration(credential="synthetic-key"), server.OPENROUTER_API_KEY_ENV_NAME: "synthetic-key", model_name: "openai/gpt-6.1-sol", effort_name: effort}

                def change_configuration_during_prompt(*args, **kwargs):
                    values.update({model_name: "openai/gpt-6-luna", effort_name: "none"})
                    return "Synthetic prompt"

                payload = self.payload(self.pdf, self.image)
                payload["analysis_mode"] = mode
                self.send.reset_mock()
                self.send.return_value = json_response("openai/gpt-6.1-sol", {})
                with (
                    mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)) as reader,
                    mock.patch.object(server, "build_quote_draft_prompt", side_effect=change_configuration_during_prompt),
                    mock.patch.object(server, "pricing_reference_authority_error", return_value=""),
                    mock.patch.object(server, "exact_pricing_reference_authority", return_value={
                        "ok": True, "id": self.pricing_snapshot.reference_id,
                        "source": self.pricing_snapshot.source, "detail": self.pricing_detail,
                    }),
                    mock.patch.object(server, "normalize_line_items", return_value=[]),
                    mock.patch.object(server, "default_line_items", return_value=[]),
                    mock.patch.object(server, "finalized_remote_draft_result", return_value={"status": "drafted"}) as finalize,
                    mock.patch.object(server, "write_local_log", return_value=True) as local_log,
                    mock.patch.object(server, "append_ai_attempt_telemetry", return_value={}) as telemetry,
                    mock.patch.object(server, "log_ai_call_attempt", wraps=server.log_ai_call_attempt) as log_attempt,
                    server.ai_log_tracking_scope({}, auth_session={"auth_mode": server.INTERNAL_AUTH_MODE,
                        "user": {"subject": "synthetic-actor", "account": "synthetic-workspace", "internal_role": "admin"}}),
                    server.AiTransportOperationScope(server.AI_LOG_TRACKING_CONTEXT, "draft_quote_basis") as parent,
                ):
                    server.begin_ai_transport_operation("draft_quote_basis", server.AI_PROVIDER_OPENROUTER, "synthetic/parent")
                    parent.operation.update(attempt_number=7, usage={"input_tokens": 101, "output_tokens": 202, "total_tokens": 303})
                    parent_operation = parent.operation
                    parent_before = copy.deepcopy(parent_operation)
                    result = server.draft_quote_basis(payload)
                    self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation, parent_operation)
                    self.assertIs(server.current_ai_transport_owner(), parent)
                    self.assertEqual(parent.operation, parent_before)
                self.assertEqual(result["status"], "drafted", result)
                self.send.assert_called_once()
                log_attempt.assert_called_once()
                terminals = [call.args[1] for call in local_log.call_args_list if call.args[0] == "ai_call_attempt"]
                self.assertEqual(len(terminals), 1)
                terminal = terminals[0]
                self.assertEqual(terminal["status"], "success")
                self.assertEqual(terminal["analysis_mode"], mode)
                self.assertEqual(terminal["attempt_number"], 1)
                self.assertNotEqual(terminal["retry_lineage_id"], parent_before["retry_lineage_id"])
                self.assertEqual(terminal["model"], "")
                telemetry.assert_called_once()
                self.assertEqual(telemetry.call_args.args[1], terminal)
                self.assertEqual(parent.pending, None)
                body = json.loads(self.send.call_args.args[0].data)
                self.assertEqual(body["model"], "openai/gpt-6.1-sol")
                self.assertEqual(body["reasoning"], {"effort": effort})
                self.assertEqual(finalize.call_args.args[6]["model"], body["model"])
                self.assertEqual(log_attempt.call_args.kwargs["model"], body["model"])
                self.assertEqual(log_attempt.call_args.kwargs["analysis_mode"], mode)
                self.assertEqual(finalize.call_args.args[6]["analysis_mode"], mode)
                self.assertNotIn(body["model"], json.dumps(terminals))
                self.assertNotIn("synthetic-key", json.dumps(terminals))
                for name in (model_name, effort_name):
                    self.assertEqual(sum(call.args == (name,) for call in reader.call_args_list), 1)
                self.send.return_value = json_response("openai/gpt-6-luna", {})
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    next_body = self.capture(payload)
                self.assertEqual(next_body["model"], "openai/gpt-6-luna")
                self.assertEqual(next_body["reasoning"], {"effort": "none"})


    def test_invalid_explicit_model_or_reasoning_rejects_before_mocked_transport(self):
        cases = (
            (
                "normal model",
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
                "openai/gpt-6.1-sol",
                "high",
                (server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME),
                ("!!!", " model", "model name", "model\nname", "m" * 201),
                ("bad effort", "effort!", "e" * 65),
                None,
            ),
            (
                "high quality model",
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                "openai/gpt-6.1-sol",
                "max",
                (server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME),
                ("!!!", " model", "model name", "model\nname", "m" * 201),
                ("bad effort", "effort!", "e" * 65),
                "high_quality",
            ),
        )
        for label, model_name, effort_name, model, effort, unselected, bad_models, bad_efforts, mode in cases:
            for config_name, bad_value in (
                *((model_name, value) for value in bad_models),
                *((effort_name, value) for value in bad_efforts),
            ):
                with self.subTest(mode=label, setting=config_name, value=bad_value):
                    values = {**synthetic_configuration(credential="synthetic-key"), model_name: model, effort_name: effort}
                    values.update({name: "synthetic/unselected" for name in unselected})
                    values[config_name] = bad_value
                    payload = self.payload()
                    if mode:
                        payload["analysis_mode"] = mode
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                        self.assert_rejected(payload, forbidden_values=(bad_value,))

    def test_high_quality_aliases_use_only_high_quality_configuration(self):
        for alias in ("high_quality", "xhigh", "high_accuracy"):
            with self.subTest(alias=alias):
                values = {**synthetic_configuration(credential="synthetic-key"),
                    server.OPENROUTER_DRAFT_MODEL_ENV_NAME: "synthetic/unselected",
                    server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME: "high",
                    server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME: "openai/gpt-6.1-sol",
                    server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME: "max",
                }
                payload = self.payload()
                payload["analysis_mode"] = alias
                self.send.reset_mock()
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    body = self.capture(payload)
                self.send.assert_called_once()
                self.assertEqual(body["model"], "openai/gpt-6.1-sol")
                self.assertEqual(body["reasoning"], {"effort": "max"})

    def test_deploy_configured_model_and_supported_effort_ignore_client_override(self):
        for mode, model_name, effort_name in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME),
        ):
            with self.subTest(mode=mode):
                payload = self.payload(self.pdf, self.image)
                payload.update({"analysis_mode": mode, "model": "client-model", "reasoning": {"effort": "client-effort"}})
                env = {**synthetic_configuration(credential="synthetic-key"), "OPENROUTER_API_KEY":"synthetic-key", "APP_MODE": "deploy", model_name: "synthetic/future-provider-model-2099", effort_name: "max"}
                self.send.reset_mock()
                self.send.return_value = json_response("synthetic/future-provider-model-2099", {})
                with mock.patch.dict(server.os.environ, env, clear=True):
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader({model_name: "local-model", effort_name: "local-effort"})):
                        body = self.capture(payload)
                self.send.assert_called_once()
                self.assertEqual(body["model"], "synthetic/future-provider-model-2099")
                self.assertEqual(body["reasoning"], {"effort": "max"})


    def test_malformed_normalized_reasoning_values_are_not_exposed(self):
        for mode, model_name, effort_name in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME),
        ):
            for raw_value in ("bad effort", "effort!", "e" * 65, " PRIVATE CANARY "):
                with self.subTest(mode=mode, value=raw_value):
                    values = {**synthetic_configuration(credential="synthetic-key"), model_name: "future-model", effort_name: raw_value}
                    payload = self.payload()
                    payload["analysis_mode"] = mode
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                        self.assert_rejected(payload, forbidden_values=(raw_value, raw_value.strip()))

    def test_reader_precedence_and_dotenv_values_are_isolated(self):
        for env_name in (
            server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
            server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
            server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
            server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
        ):
            with self.subTest(env_name=env_name):
                dotenv_path = mock.Mock()
                dotenv_path.exists.return_value = True
                with mock.patch.dict(server.os.environ, {}, clear=True):
                    dotenv_path.read_text.return_value = "OTHER_KEY=low\n"
                    self.assertEqual(self.real_read_dotenv_value(env_name, env_path=dotenv_path), "")

                    server.os.environ[env_name] = ""
                    dotenv_path.read_text.return_value = f"{env_name}=low\n"
                    self.assertEqual(self.real_read_dotenv_value(env_name, env_path=dotenv_path), "low")

                    server.os.environ[env_name] = " \t\r\n "
                    dotenv_path.read_text.reset_mock()
                    self.assertEqual(self.real_read_dotenv_value(env_name, env_path=dotenv_path), " \t\r\n ")
                    dotenv_path.read_text.assert_not_called()

                    server.os.environ.pop(env_name)
                    dotenv_path.read_text.return_value = f"{env_name}=bogus\n"
                    self.assertEqual(self.real_read_dotenv_value(env_name, env_path=dotenv_path), "bogus")

                    server.os.environ[env_name] = " HIGH "
                    dotenv_path.read_text.reset_mock()
                    self.assertEqual(self.real_read_dotenv_value(env_name, env_path=dotenv_path), " HIGH ")
                    dotenv_path.read_text.assert_not_called()

    def test_final_envelope_validation_remains_the_request_boundary(self):
        branches = (
            (
                server.DRAFT_ANALYSIS_MODE_STANDARD,
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                "openai/gpt-6.1-sol",
                "high",
            ),
            (
                server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
                "openai/gpt-6.1-sol",
                "max",
            ),
        )
        for mode, model_name, effort_name, unselected_model, unselected_effort, accepted_model, accepted_effort in branches:
            payload_mode = "high_quality" if mode == server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY else None
            cases = (
                ("malformed configured effort", accepted_model, "bad effort", accepted_model, "bad effort"),
                ("malformed configured model", "bad model", accepted_effort, "bad model", accepted_effort),
                ("body effort mismatch", accepted_model, accepted_effort, accepted_model, "high" if accepted_effort == "max" else "max"),
                ("body model mismatch", accepted_model, accepted_effort, "gpt-unlisted-model", accepted_effort),
            )
            for label, configured_model, configured_effort, body_model, body_effort in cases:
                with self.subTest(mode=mode, case=label):
                    values = {**synthetic_configuration(credential="synthetic-key"),
                        model_name: configured_model,
                        effort_name: configured_effort,
                        unselected_model: "synthetic/unselected",
                        unselected_effort: "high",
                    }
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                        self.send.reset_mock()
                        with self.assertRaises(server.OpenAIAnalysisError) as error:
                            server.validate_draft_responses_envelope(self.envelope(body_model, body_effort), mode)
                    self.assertEqual(error.exception.diagnostics["failure_boundary"], "request_validation")
                    self.assertEqual(error.exception.diagnostics["attempt_number"], 0)
                    self.send.assert_not_called()
                    if payload_mode:
                        self.assertNotIn("gpt-unlisted-model", str(error.exception) + json.dumps(error.exception.diagnostics))

    def test_draft_wrapper_propagates_request_validation_without_local_fallback(self):
        for model_name, effort_name, unselected_model, unselected_effort, analysis_mode in (
            (
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                "standard",
            ),
            (
                server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                server.OPENROUTER_DRAFT_MODEL_ENV_NAME,
                server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME,
                "high_quality",
            ),
        ):
            with self.subTest(mode=analysis_mode):
                values = {**synthetic_configuration(credential="synthetic-key"),
                    server.OPENROUTER_API_KEY_ENV_NAME: "synthetic-key",
                    model_name: "openai/gpt-6.1-sol",
                    effort_name: "invalid explicit effort",
                    unselected_model: "synthetic/unselected",
                    unselected_effort: "high",
                }
                payload = self.payload()
                if analysis_mode != "standard":
                    payload["analysis_mode"] = analysis_mode
                self.send.reset_mock()
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    with mock.patch.object(server, "pricing_reference_authority_error", return_value=""):
                        with mock.patch.object(server, "default_quote_basis") as fallback:
                            with self.assertRaises(server.OpenAIAnalysisError) as error:
                                server.draft_quote_basis(payload)
                self.assertEqual(error.exception.diagnostics["failure_boundary"], "request_validation")
                self.assertEqual(error.exception.diagnostics["attempt_number"], 0)
                self.send.assert_not_called()
                fallback.assert_not_called()

    def response_for_json_output(self, result):
        return json_response("openai/gpt-6-luna", result)

    def test_pricing_openai_routes_send_luna_high_and_preserve_json_schema(self):
        cases = (
            (
                server.request_openai_pricing_catalog_import,
                ("synthetic.csv", {"headers": [], "rows": []}, {"label": "GST", "rate": 0}),
                {"items": [{"section": "Synthetic", "description": "Synthetic panel", "unit_hint": "sqm", "internal_cost": 10, "markup_multiplier": 1.5}]},
            ),
            (
                server.request_openai_pricing_catalog_metadata,
                ("synthetic.csv", [{"id": "synthetic-row", "description": "Synthetic panel"}]),
                {"items": [{"id": "synthetic-row", "match_terms": ["synthetic panel"], "object_families": ["synthetic_panel"]}]},
            ),
        )
        values = {**synthetic_configuration(credential="synthetic-key"),
            "OPENROUTER_PRICING_IMPORT_MODEL": "openai/gpt-6-luna",
            "OPENROUTER_PRICING_METADATA_MODEL": "openai/gpt-6-luna",
        }
        for request_fn, args, output in cases:
            with self.subTest(route=request_fn.__name__):
                self.send.reset_mock()
                self.send.side_effect = None
                self.send.return_value = self.response_for_json_output(output)
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    parsed = request_fn(*args, "synthetic-key")
                self.send.assert_called_once()
                request = self.send.call_args.args[0]
                body = json.loads(request.data)
                self.assertEqual(body["model"], "openai/gpt-6-luna")
                self.assertEqual(body["reasoning"], {"effort": "high"})
                self.assertEqual(parsed, output)


    def test_invalid_small_route_models_reject_before_provider_transport(self):
        routes = (
            (server.request_openai_pricing_catalog_import, ("synthetic.csv", {"headers": [], "rows": []}, {"label": "GST", "rate": 0})),
            (server.request_openai_pricing_catalog_metadata, ("synthetic.csv", [{"id": "synthetic-row", "description": "Synthetic panel"}])),
        )
        for invalid_model in ("!!!", "unnamespaced-model"):
            invalid_values = {**synthetic_configuration(credential="synthetic-key"),
                "OPENROUTER_PRICING_IMPORT_MODEL": invalid_model,
                "OPENROUTER_PRICING_METADATA_MODEL": invalid_model,
            }
            for request_fn, args in routes:
                with self.subTest(route=request_fn.__name__, model=invalid_model):
                    self.send.reset_mock()
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(invalid_values)):
                        with self.assertRaises(server.OpenAIAnalysisError) as error:
                            request_fn(*args, "synthetic-key")
                    self.assertEqual(error.exception.diagnostics["failure_boundary"], "request_validation")
                    self.assertEqual(error.exception.diagnostics["attempt_number"], 0)
                    self.send.assert_not_called()
                    self.assertNotIn(invalid_model, str(error.exception) + json.dumps(error.exception.diagnostics))


    def test_pricing_metadata_enrichment_preserves_source_pricing_authority(self):
        source = [{
            "id": "synthetic-row",
            "section": "Synthetic",
            "description": "Synthetic panel source description",
            "pricing_reference_description": "Synthetic panel source description",
            "unit_hint": "sqm",
            "internal_cost": 10,
            "markup_multiplier": 1.5,
            "sale_unit_price": 15,
            "match_terms": ["source phrase"],
            "object_families": ["source_panel"],
        }]
        ai_output = [{
            "id": "synthetic-row",
            "description": "Changed AI description",
            "unit_hint": "nos",
            "internal_cost": 0,
            "markup_multiplier": 99,
            "sale_unit_price": 0,
            "match_terms": ["retrieval phrase"],
            "object_families": ["retrieval_panel"],
        }]
        enriched, errors = server.merge_pricing_reference_ai_metadata(source, ai_output)
        self.assertEqual(errors, [])
        self.assertEqual(len(enriched), 1)
        row = enriched[0]
        for key in ("section", "description", "pricing_reference_description", "unit_hint", "internal_cost", "markup_multiplier", "sale_unit_price"):
            self.assertEqual(row[key], source[0][key])
        self.assertEqual(row["match_terms"][0], "retrieval phrase")
        self.assertIn("synthetic panel source description", row["match_terms"])
        self.assertEqual(row["object_families"], ["retrieval_panel"])

    def test_decoded_byte_and_encoded_length_boundaries(self):
        for url, constant in [(self.image, "MAX_IMAGE_BYTES"), (self.pdf, "MAX_PDF_BYTES")]:
            length = len(base64.b64decode(url.split(",")[1]))
            for limit in (length - 1, length, length + 1):
                with mock.patch.object(server, constant, limit):
                    if length > limit:
                        self.assert_rejected(self.payload(url))
                    else:
                        self.capture(self.payload(url))
            with mock.patch.object(server, constant, 1), mock.patch.object(server.base64, "b64decode") as decode:
                self.assert_rejected(self.payload(url))
                decode.assert_not_called()

    def test_dimension_and_pixel_boundaries(self):
        for width in (4095, 4096, 4097):
            url = synthetic_image(size=(width, 1))
            if width > 4096:
                self.assert_rejected(self.payload(url))
            else:
                self.capture(self.payload(url))
        for height in (4095, 4096, 4097):
            url = synthetic_image(size=(1, height))
            if height > 4096:
                self.assert_rejected(self.payload(url))
            else:
                self.capture(self.payload(url))
        # Scale only the pixel ceiling for exact N-1/N/N+1 decoded images.
        with mock.patch.object(server, "MAX_PROMPT_IMAGE_PIXELS", 100):
            for pixels in (99, 100, 101):
                url = synthetic_image(size=(pixels, 1))
                if pixels > 100:
                    self.assert_rejected(self.payload(url))
                else:
                    self.capture(self.payload(url))

    def test_outbound_json_boundaries(self):
        body = self.capture()
        length = len(json.dumps(body).encode("utf-8"))
        for limit in (length - 1, length, length + 1):
            with mock.patch.object(server, "MAX_DRAFT_RESPONSES_BYTES", limit):
                if length > limit:
                    self.assert_rejected(self.payload())
                else:
                    self.capture()

    def test_inbound_json_boundaries(self):
        for path in ("/api/jobs", "/api/draft", "/api/quote-sessions"):
            for length in (99, 100, 101):
                handler = object.__new__(server.QuoteRunnerHandler)
                handler.path = path
                handler.headers = {"Content-Type": "application/json", "Content-Length": str(length)}
                handler.rfile = io.BytesIO(b'{}' + b' ' * (length - 2))
                with mock.patch.object(server, "MAX_JOB_REQUEST_BYTES", 100):
                    if length > 100:
                        with self.assertRaises(server.RequestBodyError) as caught:
                            handler.read_json()
                        self.assertEqual(caught.exception.status, 413)
                        self.assertEqual(handler.rfile.tell(), 0)
                    else:
                        self.assertEqual(handler.read_json(), {})

    def test_direct_draft_extended_limit_rejects_non_pdf_json(self):
        body = json.dumps(self.payload() | {"_pricing_reference_snapshot": None}).encode()
        handler = object.__new__(server.QuoteRunnerHandler)
        handler.path = "/api/draft"
        handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        with mock.patch.object(server, "MAX_REQUEST_BYTES", len(body) - 1):
            with self.assertRaises(server.RequestBodyError) as caught:
                handler.read_json()
        self.assertEqual(caught.exception.status, 413)

    def test_page_budget_and_derived_media_byte_boundaries(self):
        for count in (server.MAX_RENDERED_PDF_PAGES - 1, server.MAX_RENDERED_PDF_PAGES):
            self.send.reset_mock()
            parts = self.capture(self.payload(synthetic_pdf(count)))["input"][0]["content"]
            self.assertEqual(sum(part["type"] == "input_image" for part in parts), count)
            self.send.assert_called_once()
        length = len(base64.b64decode(self.image.split(",")[1]))
        self.enterContext(mock.patch.object(server, "render_pdf_pages_with_pdfium",
                                           return_value=[{"page": 1, "data_url": self.image}]))
        for limit in (length - 1, length, length + 1):
            for constant in ("MAX_RENDERED_PDF_PAGE_BYTES", "MAX_PRICING_REFERENCE_VISUAL_BYTES"):
                self.catalog.return_value = [{"data_url": self.image}]
                with mock.patch.object(server, constant, limit):
                    if length > limit:
                        self.assert_rejected(self.payload(self.pdf))
                    else:
                        self.capture(self.payload(self.pdf))

    def test_batch_coverage_mixed_sources_and_reserved_synthesis(self):
        self.mock_batch_responses()
        payload = self.payload(synthetic_pdf(8), self.image, synthetic_pdf(7))
        with mock.patch.object(server, "write_local_log", return_value=True) as log:
            synthesis = self.capture(payload)["input"][0]["content"][0]["text"]
        expected = [*(f"Reference 1, PDF page {page}" for page in range(1, 9)),
                    "Reference 2, uploaded image",
                    *(f"Reference 3, PDF page {page}" for page in range(1, 8))]
        self.assertEqual(self.send.call_count, 4)
        for source in expected:
            self.assertIn(f'"source": "{source}"', synthesis)
        totals = [call.args[1] for call in log.call_args_list if call.args[0] == "draft_batch_usage"][0]
        self.assertTrue(server.is_loggable_event("draft_batch_usage"))
        self.assertEqual(totals["total"], {
            "sends": 4, "unknown_usage_attempts": 1,
            "input_tokens": 36, "output_tokens": 24, "total_tokens": 60,
            "reported_cost_usd": 0.375, "unknown_cost_attempts": 1,
        })
        self.assertEqual(server.sanitize_log_value(totals)["by_model"],
                         [{"route": "draft", **totals["total"]}])
        self.assertNotIn("openai/gpt-6.1-sol", json.dumps(totals))
        terminal = [call.args[1] for call in log.call_args_list if call.args[0] == "ai_call_attempt"]
        self.assertEqual(len(terminal), 4)
        self.assertEqual([item["status"] for item in terminal], ["success"] * 4)

    def test_failed_batch_accounts_for_reported_cost_and_timeout_is_unknown(self):
        for terminal, expected_cost, unknown in (
            (json_response("openai/gpt-6.1-sol", {}, status="incomplete", usage={"cost": 0.125}), 0.125, 0),
            (TimeoutError(), 0, 1),
        ):
            with self.subTest(terminal=type(terminal)):
                self.send.reset_mock()
                self.send.side_effect = [terminal]
                with mock.patch.object(server, "write_local_log", return_value=True) as log:
                    with self.assertRaises(server.DraftBatchError):
                        self.capture(self.payload(synthetic_pdf(13)))
                totals = next(call.args[1]["total"] for call in log.call_args_list
                              if call.args[0] == "draft_batch_usage")
                self.assertEqual(totals["sends"], 1)
                self.assertEqual(totals["reported_cost_usd"], expected_cost)
                self.assertEqual(totals["unknown_cost_attempts"], unknown)
                self.send.assert_called_once()

    def test_single_send_reports_zero_cost_even_without_tokens(self):
        self.send.return_value = json_response("openai/gpt-6.1-sol", {}, usage={"cost": 0})
        with mock.patch.object(server, "write_local_log", return_value=True) as log:
            self.capture()
        totals = next(call.args[1]["total"] for call in log.call_args_list
                      if call.args[0] == "draft_batch_usage")
        self.assertEqual(totals["sends"], 1)
        self.assertEqual(totals["unknown_usage_attempts"], 1)
        self.assertEqual(totals["reported_cost_usd"], 0)
        self.assertEqual(totals["unknown_cost_attempts"], 0)

    def test_max_pages_and_seven_byte_limited_images_use_sixteen_sends(self):
        # Scale the byte ceiling down while preserving the production packing
        # relationship: six small pages fit and each large image fits alone.
        page = self.image
        large_image = "data:image/png;base64," + "A" * (6 * len(page))
        references = self.payload(synthetic_pdf(48), *([large_image] * 7))["images"]
        rendered = [{"page": number, "data_url": page} for number in range(1, 49)]
        with (mock.patch.object(server, "render_pdf_pages_with_pdfium", return_value=rendered),
              mock.patch.object(server, "MAX_DRAFT_BATCH_MEDIA_CHARS", len(large_image))):
            prepared = server.prepare_draft_media(references)
        self.assertEqual([len(batch) for batch in prepared.batches], [6] * 8 + [1] * 7)
        self.assertEqual(len(prepared.batches) + 1, 16)
        self.assertLess(len(prepared.batches) + 1, server.MAX_DRAFT_BATCH_CALLS)

    def test_preflight_rejects_total_page_cap_and_call_cap_without_sending(self):
        with self.assertRaises(server.DraftBatchError) as error:
            self.capture(self.payload(synthetic_pdf(30), synthetic_pdf(19)))
        self.assertEqual(error.exception.diagnostics["source_number"], 2)
        self.assertEqual(error.exception.diagnostics["batch_failure_reason"], "pdf_page_limit")
        with mock.patch.object(server, "MAX_DRAFT_BATCH_CALLS", 3):
            with self.assertRaises(server.DraftBatchError):
                self.capture(self.payload(synthetic_pdf(13)))
        self.send.assert_not_called()

    def test_failed_second_pdf_preflight_reports_only_source_and_page(self):
        original = server.render_pdf_pages_with_pdfium

        def render(raw, name, max_pages, **options):
            if name == "synthetic-1":
                raise server.DraftBatchError("pdf_page_render", page=2)
            return original(raw, name, max_pages, **options)

        with mock.patch.object(server, "render_pdf_pages_with_pdfium", side_effect=render):
            with self.assertRaises(server.DraftBatchError) as error:
                self.capture(self.payload(synthetic_pdf(13), synthetic_pdf(2)))
        details = server.ai_error_log_details("err-0123456789ab", error.exception)
        self.assertEqual((details["source_number"], details["page_number"]), (2, 2))
        self.assertNotIn("synthetic-1", str(details))
        self.send.assert_not_called()

    def test_batch_timeout_and_partial_observations_stop_without_synthesis(self):
        for response in (TimeoutError(), json_response("openai/gpt-6.1-sol", {"observations": ["partial"]})):
            with self.subTest(response=type(response)):
                self.send.reset_mock()
                self.send.side_effect = response if isinstance(response, Exception) else None
                self.send.return_value = response
                with self.assertRaises(server.DraftBatchError) as error:
                    self.capture(self.payload(synthetic_pdf(13)))
                self.assertEqual(error.exception.diagnostics["batch_number"], 1)
                self.send.assert_called_once()

    def test_invalid_media_never_enters_local_starter_fallback(self):
        with mock.patch.object(server, "default_quote_basis") as fallback:
            with self.assertRaises(server.OpenAIAnalysisError):
                server.draft_quote_basis(self.payload("data:image/png;base64,ZmFrZQ=="))
            fallback.assert_not_called()
            self.send.assert_not_called()

    def test_real_pdf_decode_without_debug_retention(self):
        # Bypass only the rendering stub, never the network denial backstop.
        with mock.patch.object(server, "persist_pdf_page_debug_images") as retain:
            pages = self.real_pdf_pages({"name": "synthetic.pdf", "data_url": self.pdf}, max_pages=1, retain_debug=False)
            self.assertEqual(len(pages), 1)
            self.assertEqual(server.validate_draft_media(pages[0]["data_url"]), "image/jpeg")
            retain.assert_not_called()

    def test_corrupt_complete_and_zero_page_pdfs_reject(self):
        corrupt = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.7\ncorrupt document\n%%EOF").decode("ascii")
        self.assert_rejected(self.payload(corrupt))
        self.assert_rejected(self.payload(synthetic_pdf(0)))

    def test_network_backstop_and_envelope_validation_precede_send(self):
        with self.assertRaisesRegex(AssertionError, "Outbound network denied"):
            socket.create_connection(("api.openai.com", 443))
        with mock.patch.object(server, "build_quote_draft_prompt", return_value={"invalid": "PRIVATE"}):
            self.assert_rejected(self.payload())

    def test_failed_response_diagnostics_drop_private_enums(self):
        response = FixtureHttpResponse({
            "status": "failed", "error": {"type": "private_customer", "code": "private_identifier", "param": "private_path"},
            "incomplete_details": {"reason": "private_reason"},
        })
        self.send.return_value = response
        with self.assertRaises(server.OpenAIAnalysisError) as caught:
            self.capture()
        self.assertNotIn("private", json.dumps(caught.exception.diagnostics))


    def test_one_send_even_on_retryable_failure_and_safe_diagnostics(self):
        for status in (400, 429, 500, 503):
            self.send.reset_mock()
            error = urllib.error.HTTPError(OPENROUTER_RESPONSES_URL, status, "Synthetic", {}, io.BytesIO(json.dumps({"error": {"type": "invalid_request_error", "code": "invalid_value", "param": "input[0].content[1].image_url", "message": "PRIVATE_PROVIDER_MESSAGE"}}).encode()))
            self.send.side_effect = error
            with mock.patch.object(server.time, "sleep") as sleep, self.assertRaises(server.OpenAIAnalysisError) as caught:
                self.capture()
            self.send.assert_called_once()
            sleep.assert_not_called()
            details = server.safe_ai_output_diagnostics(caught.exception.diagnostics)
            self.assertEqual(details["provider_error_param"], "input[0].content[1].image_url")
            self.assertEqual(details["attempt_number"], 1)
            self.assertRegex(details["request_shape_sha256"], r"^[a-f0-9]{64}$")
            self.assertNotIn("PRIVATE", str(caught.exception) + json.dumps(details))
            error.close()

    def test_provider_rejects_admitted_configuration_once_without_fallback_or_private_values(self):
        model, effort = "synthetic/PRIVATE_CANARY-future-model", "max"
        for mode, model_name, effort_name in (
            ("standard", server.OPENROUTER_DRAFT_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_REASONING_EFFORT_ENV_NAME),
            ("high_quality", server.OPENROUTER_DRAFT_HIGH_QUALITY_MODEL_ENV_NAME, server.OPENROUTER_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME),
        ):
            for param in ("model", "reasoning.effort"):
                with self.subTest(mode=mode, param=param):
                    payload = self.payload()
                    payload["analysis_mode"] = mode
                    values = {**synthetic_configuration(credential="synthetic-key"), server.OPENROUTER_API_KEY_ENV_NAME: "synthetic-key", model_name: model, effort_name: effort}
                    response = {"error": {"type": "invalid_request_error", "code": "unsupported_value", "param": param, "message": model + effort}}
                    error = urllib.error.HTTPError(OPENROUTER_RESPONSES_URL, 400, "Synthetic", {}, io.BytesIO(json.dumps(response).encode()))
                    self.send.reset_mock()
                    self.send.side_effect = error
                    with (
                        mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)),
                        mock.patch.object(server, "pricing_reference_authority_error", return_value=""),
                        mock.patch.object(server, "default_quote_basis") as fallback,
                        mock.patch.object(server, "write_local_log") as write_log,
                        mock.patch.object(server.time, "sleep") as sleep,
                        mock.patch.object(server, "protected_ai_draft_mode_enabled", return_value=True),
                    ):
                        result = server.draft_quote_basis(payload)
                    self.send.assert_called_once()
                    sent = json.loads(self.send.call_args.args[0].data)
                    self.assertEqual(sent["model"], model)
                    self.assertEqual(sent["reasoning"], {"effort": effort})
                    fallback.assert_not_called()
                    sleep.assert_not_called()
                    self.assertEqual(result["status"], "blocked")
                    self.assertTrue(result["error_reference"])
                    attempt = next(call.args[1] for call in write_log.call_args_list if call.args[0] == "ai_call_attempt")
                    self.assertEqual(attempt["attempt_number"], 1)
                    self.assertEqual(attempt["details"]["failure_boundary"], "provider_http")
                    self.assertEqual(attempt["details"]["provider_error_param"], param)
                    public = json.dumps(result) + json.dumps([server.sanitize_log_value(call.args[1]) for call in write_log.call_args_list])
                    self.assertNotIn(model, public)
                    self.assertEqual(sent["reasoning"]["effort"], effort)
                    error.close()
        self.send.side_effect = None


    def test_error_param_is_allowlisted_and_never_derived_from_message(self):
        for value in [None, "private_customer", "input[0].content[1].filename.private", "https://private.invalid", "input[1000].content[1]", "input[0].content[1].text\n"]:
            self.assertEqual(server.safe_draft_error_param(value), "")
        self.assertEqual(server.draft_provider_error_diagnostics({"error": {"message": "input[0].content[1].image_url", "code": "private_customer"}}), {})

    def test_shape_hash_excludes_all_private_values(self):
        body = self.capture(self.payload(self.image, self.pdf))
        _, original = server.validate_draft_responses_envelope(body, "standard")
        changed = copy.deepcopy(body)
        changed["input"][0]["content"][0]["text"] = "PRIVATE_PROMPT"
        changed["input"][0]["content"][1]["image_url"] = synthetic_image(color="black")
        changed["input"][0]["content"][2]["filename"] = "PRIVATE_FILENAME"
        changed["input"][0]["content"][2]["file_data"] = synthetic_pdf(2)
        self.assertEqual(server.validate_draft_responses_envelope(changed, "standard")[1], original)
        changed["model"] = "synthetic/PRIVATE_CANARY-future-model"
        changed["reasoning"] = {"effort": "max"}
        configuration = server.OpenAIDraftConfiguration("standard", changed["model"], changed["reasoning"]["effort"])
        self.assertEqual(server.validate_draft_responses_envelope(changed, "standard", draft_configuration=configuration)[1], original)
        self.assertEqual(server.validated_provider_model_label("openai", changed["model"]), "")
        changed["input"][0]["content"][1]["detail"] = "low"
        self.assertNotEqual(server.validate_draft_responses_envelope(changed, "standard", draft_configuration=configuration)[1], original)
        invalid=copy.deepcopy(changed)
        invalid["reasoning"]={"effort":"private_canary_effort"}
        invalid_configuration=server.OpenAIDraftConfiguration("standard",invalid["model"],"private_canary_effort")
        self.send.reset_mock()
        with self.assertRaises(server.OpenAIAnalysisError) as caught:
            server.validate_draft_responses_envelope(invalid,"standard",draft_configuration=invalid_configuration)
        self.send.assert_not_called()
        self.assertEqual(caught.exception.diagnostics["attempt_number"],0)
        self.assertNotIn("PRIVATE_CANARY",str(caught.exception)+json.dumps(caught.exception.diagnostics))
        self.assertNotIn("private_canary_effort",str(caught.exception)+json.dumps(caught.exception.diagnostics))


if __name__ == "__main__":
    unittest.main()
