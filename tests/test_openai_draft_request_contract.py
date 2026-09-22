"""Deterministic Responses contract: synthetic media, mocked send, network denied."""
import base64
import copy
import functools
import io
import json
import socket
import unittest
import urllib.error
from unittest import mock

from PIL import Image
import pypdfium2 as pdfium

from webapp import server

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


class DraftRequestContractTest(unittest.TestCase):
    def setUp(self):
        self.real_read_dotenv_value = server.read_dotenv_value
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Outbound network denied")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("Outbound network denied")))
        self.enterContext(mock.patch.object(server, "read_dotenv_value", return_value=""))
        self.enterContext(mock.patch.object(server, "build_quote_draft_prompt", return_value="Synthetic prompt"))
        self.catalog = self.enterContext(mock.patch.object(server, "catalog_visual_image_entries_for_payload", return_value=[]))
        self.real_pdf_pages = server.pdf_reference_page_images
        self.pages = self.enterContext(mock.patch.object(server, "pdf_reference_page_images", return_value=[]))
        self.enterContext(mock.patch.object(server, "normalize_ai_draft", return_value={"ok": True}))
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"output_text":"{}"}'
        self.send = self.enterContext(mock.patch.object(server.urllib.request, "urlopen", return_value=response))
        self.image = synthetic_image()
        self.pdf = synthetic_pdf()

    def payload(self, *urls):
        return {"images": [{"name": f"synthetic-{i}", "data_url": url} for i, url in enumerate(urls or (self.image,))]}

    def capture(self, payload=None):
        server.request_openai_quote_basis(payload or self.payload(), "synthetic-key")
        request = self.send.call_args.args[0]
        self.assertEqual(request.full_url, server.OPENAI_RESPONSES_URL)
        self.assertEqual(request.method, "POST")
        return json.loads(request.data)

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

    def envelope(self, model="gpt-5.5", effort="high"):
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
                    expected.append({"type": "input_file", "filename": f"synthetic-{index}", "file_data": url} if url == self.pdf else {"type": "input_image", "image_url": url, "detail": "high"})
                self.assertEqual(body, {"model": "gpt-5.5", "input": [{"role": "user", "content": expected}], "reasoning": {"effort": "high"}})

    def test_supported_image_formats(self):
        for format in ("JPEG", "PNG", "WEBP"):
            url = synthetic_image(format)
            self.assertEqual(self.capture(self.payload(url))["input"][0]["content"][1]["image_url"], url)

    def test_catalog_and_pdf_page_semantics(self):
        self.pages.return_value = [{"page": 1, "data_url": self.image}]
        self.catalog.return_value = [{"data_url": self.image, "id": "synthetic"}]
        parts = self.capture(self.payload(self.pdf, self.image))["input"][0]["content"]
        self.assertEqual([p["type"] for p in parts], ["input_text", "input_file", "input_text", "input_image", "input_image", "input_text", "input_image"])
        self.assertEqual([p["detail"] for p in parts if p["type"] == "input_image"], ["high", "high", "low"])
        self.assertEqual(parts[1]["file_data"], self.pdf)

    def test_invalid_supplied_media_rejects_whole_request(self):
        malformed = [None, 3, "", self.image + "\n", self.image + ",extra", self.image.replace(";base64", ";charset=utf8;base64"), "data:image/png;base64,", "data:image/png;base64,!!!!", "data:image/png;base64,Zg===", "data:image/png;base64,Zh==", self.image.replace("image/png", "image/gif"), self.image.replace("image/png", "image/jpeg"), "https://private.invalid/render", "data:application/pdf;base64,JVBERi0xLjQK", "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\ncorrupt").decode()]
        for url in malformed:
            with self.subTest(case=malformed.index(url)):
                self.assert_rejected(self.payload(self.image, url))
        for entries in [None, {}, [], [None], [{"data_url": self.image}], [{"name": "ignored", "type": "text/plain"}]]:
            self.assert_rejected({"images": entries})

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

    def test_input_file_aggregate_bytes_boundaries_and_five_pdf_reproducer(self):
        decoded_pdf_bytes = len(base64.b64decode(self.pdf.split(",", 1)[1]))
        payload = self.payload(self.pdf, self.pdf)
        for limit in (2 * decoded_pdf_bytes - 1, 2 * decoded_pdf_bytes, 2 * decoded_pdf_bytes + 1):
            with self.subTest(limit=limit):
                with mock.patch.object(server, "MAX_DRAFT_INPUT_FILE_TOTAL_BYTES", limit):
                    if 2 * decoded_pdf_bytes > limit:
                        self.assert_rejected(payload)
                    else:
                        self.capture(payload)

        # The accepted G4 reproducer is five valid PDFs whose aggregate bytes
        # exceed the final input_file ceiling before the mocked transport.
        with mock.patch.object(server, "MAX_DRAFT_INPUT_FILE_TOTAL_BYTES", 4 * decoded_pdf_bytes):
            self.assert_rejected(self.payload(*([self.pdf] * 5)))

    def test_gpt_55_invalid_reasoning_effort_rejects_before_mocked_transport(self):
        def dotenv(name):
            if name == server.OPENAI_DRAFT_MODEL_ENV_NAME:
                return "gpt-5.5"
            if name == server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME:
                return "minimal"
            return ""

        with mock.patch.object(server, "read_dotenv_value", side_effect=dotenv):
            self.assert_rejected(self.payload())

    def test_gpt_55_supported_reasoning_efforts_remain_valid(self):
        for effort in ("none", "low", "high", "xhigh"):
            with self.subTest(effort=effort):
                def dotenv(name):
                    if name == server.OPENAI_DRAFT_MODEL_ENV_NAME:
                        return "gpt-5.5"
                    if name == server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME:
                        return effort
                    return ""

                with mock.patch.object(server, "read_dotenv_value", side_effect=dotenv):
                    body = self.capture()
                self.assertEqual(body["model"], "gpt-5.5")
                self.assertEqual(body["reasoning"], {"effort": effort})

    def test_reasoning_configuration_matrix_is_explicit_and_fail_closed(self):
        matrix = (
            ("absent", MISSING, MISSING),
            ("empty", "", MISSING),
            ("whitespace", " \t\r\n ", MISSING),
            ("none", "none", "none"),
            ("low", "low", "low"),
            ("high", "high", "high"),
            ("normalized high", " HIGH ", "high"),
            ("xhigh", "xhigh", "xhigh"),
            ("minimal", "minimal", None),
            ("medium", "medium", None),
            ("bogus", "bogus", None),
        )
        branches = (
            (
                "standard",
                server.DRAFT_ANALYSIS_MODE_STANDARD,
                server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_REASONING_EFFORT,
            ),
            (
                "high_quality",
                server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT,
            ),
        )
        for branch_name, mode, selected_name, unselected_name, default in branches:
            for label, configured, expected in matrix:
                with self.subTest(branch=branch_name, value=label):
                    values = {
                        server.OPENAI_DRAFT_MODEL_ENV_NAME: "gpt-5.5",
                        unselected_name: "bogus",
                    }
                    if configured is not MISSING:
                        values[selected_name] = configured
                    payload = self.payload()
                    if mode == server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY:
                        payload["analysis_mode"] = "high_quality"
                    self.send.reset_mock()
                    self.send.side_effect = None
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                        if expected is None:
                            forbidden = (str(configured).strip(), str(configured).strip().lower())
                            self.assert_rejected(payload, forbidden_values=forbidden)
                        else:
                            body = self.capture(payload)
                            self.send.assert_called_once()
                            self.assertEqual(body["model"], "gpt-5.5")
                            expected_effort = default if expected is MISSING else expected
                            self.assertEqual(body["reasoning"], {"effort": expected_effort})
                            self.assertEqual(
                                body["input"],
                                [{
                                    "role": "user",
                                    "content": [
                                        {"type": "input_text", "text": "Synthetic prompt"},
                                        {"type": "input_image", "image_url": self.image, "detail": "high"},
                                    ],
                                }],
                            )

    def test_normalized_unsupported_reasoning_values_are_not_exposed(self):
        for raw in (" BoGuS ", "max", "ultra", "PRIVATE_CANARY_R704"):
            with self.subTest(value=raw):
                values = {
                    server.OPENAI_DRAFT_MODEL_ENV_NAME: "gpt-5.5",
                    server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME: raw,
                }
                forbidden = (raw, raw.strip(), raw.strip().lower())
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    self.assert_rejected(self.payload(), forbidden_values=forbidden)

    def test_high_quality_aliases_use_only_the_high_quality_variable(self):
        for alias in ("high_quality", "xhigh", "high_accuracy"):
            with self.subTest(alias=alias):
                values = {
                    server.OPENAI_DRAFT_MODEL_ENV_NAME: "gpt-5.5",
                    server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME: "bogus",
                    server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME: "none",
                }
                payload = self.payload()
                payload["analysis_mode"] = alias
                self.send.reset_mock()
                self.send.side_effect = None
                with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                    body = self.capture(payload)
                self.send.assert_called_once()
                self.assertEqual(body["reasoning"], {"effort": "none"})

    def test_reader_precedence_and_dotenv_values_are_isolated(self):
        env_name = server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME
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
                "standard",
                server.DRAFT_ANALYSIS_MODE_STANDARD,
                server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                "high",
            ),
            (
                "high_quality",
                server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME,
                "xhigh",
            ),
        )
        for branch_name, mode, selected_name, unselected_name, accepted in branches:
            for case, configured, body_effort, body_model in (
                ("unsupported configured and body effort", "bogus", "bogus", "gpt-5.5"),
                ("accepted configured and body mismatch", accepted, "none", "gpt-5.5"),
                ("model mismatch", accepted, accepted, "gpt-other-model"),
            ):
                with self.subTest(branch=branch_name, case=case):
                    values = {
                        server.OPENAI_DRAFT_MODEL_ENV_NAME: "gpt-5.5",
                        selected_name: configured,
                        unselected_name: "bogus",
                    }
                    with mock.patch.object(server, "read_dotenv_value", side_effect=self.dotenv_reader(values)):
                        self.send.reset_mock()
                        with self.assertRaises(server.OpenAIAnalysisError) as error:
                            server.validate_draft_responses_envelope(self.envelope(body_model, body_effort), mode)
                    self.assertEqual(error.exception.diagnostics["failure_boundary"], "request_validation")
                    self.assertEqual(error.exception.diagnostics["attempt_number"], 0)
                    self.send.assert_not_called()

    def test_draft_wrapper_propagates_request_validation_without_local_fallback(self):
        for mode, selected_name, unselected_name, analysis_mode in (
            (
                server.DRAFT_ANALYSIS_MODE_STANDARD,
                server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                "standard",
            ),
            (
                server.DRAFT_ANALYSIS_MODE_HIGH_QUALITY,
                server.OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT_ENV_NAME,
                server.OPENAI_DRAFT_REASONING_EFFORT_ENV_NAME,
                "high_quality",
            ),
        ):
            with self.subTest(mode=mode):
                values = {
                    server.OPENAI_API_KEY_ENV_NAME: "synthetic-key",
                    server.OPENAI_DRAFT_MODEL_ENV_NAME: "gpt-5.5",
                    selected_name: "bogus",
                    unselected_name: "",
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
        for length in (99, 100, 101):
            handler = object.__new__(server.QuoteRunnerHandler)
            handler.path = "/api/jobs"
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

    def test_page_budget_and_derived_media_byte_boundaries(self):
        for count in (server.MAX_RENDERED_PDF_PAGES - 1, server.MAX_RENDERED_PDF_PAGES, server.MAX_RENDERED_PDF_PAGES + 1):
            self.pages.return_value = [{"page": index + 1, "data_url": self.image} for index in range(count)]
            parts = self.capture(self.payload(self.pdf))["input"][0]["content"]
            self.assertEqual(sum(part["type"] == "input_image" for part in parts), min(count, server.MAX_RENDERED_PDF_PAGES))
            self.assertFalse(self.pages.call_args.kwargs["retain_debug"])
        length = len(base64.b64decode(self.image.split(",")[1]))
        for limit in (length - 1, length, length + 1):
            for constant in ("MAX_RENDERED_PDF_PAGE_BYTES", "MAX_PRICING_REFERENCE_VISUAL_BYTES"):
                self.catalog.return_value = [{"data_url": self.image}]
                with mock.patch.object(server, constant, limit):
                    if length > limit:
                        self.assert_rejected(self.payload(self.pdf))
                    else:
                        self.capture(self.payload(self.pdf))

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
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "status": "failed", "error": {"type": "private_customer", "code": "private_identifier", "param": "private_path"},
            "incomplete_details": {"reason": "private_reason"},
        }).encode()
        self.send.return_value = response
        with self.assertRaises(server.OpenAIAnalysisError) as caught:
            self.capture()
        self.assertNotIn("private", json.dumps(caught.exception.diagnostics))

    def test_one_send_even_on_retryable_failure_and_safe_diagnostics(self):
        for status in (400, 429, 500, 503):
            self.send.reset_mock()
            error = urllib.error.HTTPError(server.OPENAI_RESPONSES_URL, status, "Synthetic", {}, io.BytesIO(json.dumps({"error": {"type": "invalid_request_error", "code": "invalid_value", "param": "input[0].content[1].image_url", "message": "PRIVATE_PROVIDER_MESSAGE"}}).encode()))
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
        changed["input"][0]["content"][1]["detail"] = "low"
        self.assertNotEqual(server.validate_draft_responses_envelope(changed, "standard")[1], original)


if __name__ == "__main__":
    unittest.main()
