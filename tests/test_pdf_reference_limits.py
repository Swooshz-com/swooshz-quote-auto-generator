"""Offline synthetic PDF admission, planning, persistence and safe limit messages."""
import base64
import contextlib
import io
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.test_openai_draft_request_contract import synthetic_pdf
from webapp import server


ROOT = Path(__file__).resolve().parents[1]


def reference(pages):
    return {"name": "synthetic-reference.pdf", "data_url": synthetic_pdf(pages)}


def padded_pdf(size):
    raw = base64.b64decode(synthetic_pdf().partition(",")[2])
    end = raw.rindex(b"startxref")
    # A comment before startxref preserves every object and xref offset.
    raw = raw[:end] + b"%" + b"x" * (size - len(raw) - 2) + b"\n" + raw[end:]
    return "data:application/pdf;base64," + base64.b64encode(raw).decode("ascii")


class PdfReferenceLimitsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("Network disabled")))
        self.transport = self.enterContext(mock.patch.object(server.urllib.request, "urlopen", side_effect=AssertionError("Provider disabled")))
        self.addCleanup(self.transport.assert_not_called)

    def test_96_pages_render_and_plan_within_18_sends(self):
        references = [reference(96)]
        server.validate_draft_references({"images": references})
        prepared = server.prepare_draft_media(references)
        self.assertFalse(prepared.single_send)
        self.assertEqual([len(batch) for batch in prepared.batches], [6] * 16)
        self.assertEqual(sum(len(batch) for batch in prepared.batches), 96)
        budget = server.DraftSendBudget("draft", "synthetic/model", len(prepared.batches))
        for _ in prepared.batches:
            budget.next_stage()
            budget.claim("draft", "synthetic/model")
            budget.finish(None)
        budget.next_stage(synthesis=True)
        budget.claim("draft", "synthetic/model")
        budget.finish(None)
        self.assertEqual(budget.summary()["total"]["sends"], 17)
        self.assertEqual(server.MAX_DRAFT_BATCH_CALLS, 18)
        with self.assertRaises(server.DraftBatchError):
            budget.next_stage(synthesis=True)
            budget.claim("draft", "synthetic/model")

    def test_maximum_encoded_page_sizes_still_fit_six_per_batch(self):
        image = "data:image/jpeg;base64," + "A" * (4 * ((server.MAX_RENDERED_PDF_PAGE_BYTES + 2) // 3))
        pages = [{"page": n, "data_url": image} for n in range(1, 97)]
        with (mock.patch.object(server, "render_pdf_pages_with_pdfium", return_value=pages),
              mock.patch.object(server, "validate_draft_media", return_value="image/jpeg")):
            prepared = server.prepare_draft_media([reference(96)])
        self.assertEqual([len(batch) for batch in prepared.batches], [6] * 16)
        self.assertTrue(all(sum(len(image["image_url"]) for _, image in batch) <= server.MAX_DRAFT_BATCH_MEDIA_CHARS
                            for batch in prepared.batches))

    def test_97_pages_fail_validation_and_renderer(self):
        entry = reference(97)
        for action in (
            lambda: server.validate_draft_references({"images": [entry]}),
            lambda: server.prepare_draft_media([entry]),
        ):
            with self.subTest(action=action), self.assertRaises(server.DraftBatchError) as caught:
                action()
            self.assertEqual(caught.exception.diagnostics["batch_failure_reason"], "pdf_page_limit")
            self.assertEqual(caught.exception.diagnostics["pdf_total_pages"], 97)
            self.assertEqual(caught.exception.diagnostics["pdf_page_limit"], 96)

    def test_pages_across_two_pdfs_share_one_cap(self):
        admitted = [reference(48), reference(48)]
        server.validate_draft_references({"images": admitted})
        prepared = server.prepare_draft_media(admitted)
        self.assertEqual([len(batch) for batch in prepared.batches], [6] * 16)
        refused = [reference(60), reference(60)]
        server.validate_draft_references({"images": refused})
        with self.assertRaises(server.DraftBatchError) as caught:
            server.prepare_draft_media(refused)
        diagnostics = caught.exception.diagnostics
        self.assertEqual(diagnostics["batch_failure_reason"], "pdf_page_limit")
        self.assertEqual(diagnostics["source_number"], 2)
        self.assertEqual(diagnostics["pdf_total_pages"], 120)
        self.assertEqual(diagnostics["pdf_total_bytes"], sum(len(base64.b64decode(e["data_url"].partition(",")[2])) for e in refused))
        self.assert_safe_counts(caught.exception, pages=120)

    def assert_safe_counts(self, exception, *, pages):
        details = server.sanitize_log_value(server.ai_error_log_details("ERR-PDF-LIMIT", exception))
        self.assertEqual(details["pdf_total_pages"], pages)
        self.assertEqual(details["pdf_page_limit"], 96)
        self.assertEqual(details["pdf_byte_limit"], 48 * 1024 * 1024)
        self.assertGreater(details["pdf_total_bytes"], 0)
        self.assertNotIn("synthetic-reference.pdf", json.dumps(details))
        for key in ("pdf_total_pages", "pdf_total_bytes", "pdf_page_limit", "pdf_byte_limit", "pdf_page_count_complete"):
            self.assertIs(type(details[key]), int)
        return details

    def test_48_mib_boundaries_and_pdf_only_checkpoint_allowance(self):
        self.assertEqual(server.MAX_PDF_BYTES, 48 * 1024 * 1024)
        for size in (server.MAX_PDF_BYTES - 1, server.MAX_PDF_BYTES, server.MAX_PDF_BYTES + 1):
            with self.subTest(size=size):
                entry = {"name": "synthetic-reference.pdf", "data_url": padded_pdf(size)}
                if size > server.MAX_PDF_BYTES:
                    with self.assertRaises(server.OpenAIAnalysisError) as caught:
                        server.validate_draft_references({"images": [entry]})
                    details = self.assert_safe_counts(caught.exception, pages=0)
                    self.assertEqual(details["pdf_total_bytes"], size)
                    self.assertEqual(details["pdf_page_count_complete"], 0)
                    self.assertEqual(details["batch_failure_reason"], "pdf_byte_limit")
                    continue
                self.assertEqual(server.validate_draft_media(entry["data_url"]), "application/pdf")
                record = {**entry, "session_file_key": "synthetic-reference", "file_role": "reference", "size": size}
                self.assertEqual(server.quote_session_draft_file_record(record)["data_url"], entry["data_url"])
                payload = {"draft_files": [record]}
                length = len(json.dumps(payload).encode("utf-8"))
                self.assertTrue(server.pdf_request_fits_standard_budget("/api/quote-sessions", payload, length))
                self.assertLess(length, server.request_body_limit("/api/quote-sessions"))
                self.assertFalse(server.pdf_request_fits_standard_budget(
                    "/api/quote-sessions", {"draft_files": [{**record, "file_role": "logo"}]}, length))

    def test_48_mib_reference_persists_in_database_and_object_adapter(self):
        content = b"x" * server.MAX_PDF_BYTES
        self.assertEqual(server.MAX_QUOTE_ARTIFACT_BYTES, len(content))
        with contextlib.closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(server.SQAG_ARTIFACT_STORAGE_SQL)
            owner = SimpleNamespace(workspace_id="synthetic-workspace")
            server.DatabaseSqagStorage._execute_upsert_file_artifact(
                owner, connection, "uploaded_reference", "synthetic-quote", "reference",
                "synthetic-reference.pdf", "application/pdf", content,
            )
            row = connection.execute("select size_bytes, length(content_blob) as stored_bytes from sqag_file_artifacts").fetchone()
            self.assertEqual((row["size_bytes"], row["stored_bytes"]), (len(content), len(content)))
            with self.assertRaises(ValueError):
                server.DatabaseSqagStorage._execute_upsert_file_artifact(
                    owner, connection, "uploaded_reference", "synthetic-quote", "reference",
                    "synthetic-reference.pdf", "application/pdf", content + b"x",
                )
        client = SimpleNamespace(put_object=mock.Mock())
        backend = server.S3CompatibleObjectStorageBackend(bucket="synthetic-bucket", client=client)
        metadata = backend.store_artifact(
            workspace_id="synthetic-workspace", owner_type="uploaded_reference", owner_id="synthetic-quote",
            artifact_kind="reference", filename="synthetic-reference.pdf", content_type="application/pdf", content=content,
        )
        self.assertEqual(metadata.size_bytes, len(content))
        self.assertEqual(client.put_object.call_args.kwargs["Body"], content)

    def test_total_source_size_failure_has_numeric_pdf_counts(self):
        entries = [reference(2), reference(3)]
        with mock.patch.object(server, "MAX_DRAFT_SOURCE_BYTES", 1):
            with self.assertRaises(server.DraftBatchError) as caught:
                server.validate_draft_references({"images": entries})
        self.assertEqual(caught.exception.diagnostics["source_byte_limit"], 1)
        self.assert_safe_counts(caught.exception, pages=5)

    def test_numeric_diagnostics_refuse_text_booleans_and_negative_values(self):
        for value in ("synthetic-reference.pdf", True, -1, 1 << 64):
            self.assertNotIn("pdf_total_bytes", server.safe_ai_output_diagnostics({"pdf_total_bytes": value}))
        self.assertEqual(server.safe_ai_output_diagnostics({"pdf_total_bytes": 128 * 1024 * 1024}),
                         {"pdf_total_bytes": 128 * 1024 * 1024})

    def test_limit_messages_and_visible_copy(self):
        html = (ROOT / "webapp/static/index.html").read_text(encoding="utf-8")
        js = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
        self.assertIn("PDFs up to 48 MiB each and 96 pages in total across PDFs", html)
        self.assertIn("const MAX_REFERENCE_PDF_MIB = 48;", js)
        self.assertIn("96 pages in total across PDFs", js)
        for reason, limit in (("pdf_page_limit", "96"), ("pdf_byte_limit", "48"), ("source_byte_limit", "128")):
            exception = server.DraftBatchError(reason)
            result = server.failed_result_payload("ERR-PDF-LIMIT", exception=exception)
            self.assertIn(limit, result["errors"][0])
            self.assertIn("ERR-PDF-LIMIT", result["errors"][0])
            self.assertIn(limit, server.ai_failure_metadata(exception)["safe_error_summary"])
        self.assertEqual(server.failed_result_payload("ERR-PDF-LIMIT", exception=ValueError("synthetic-reference.pdf")),
                         server.failed_result_payload("ERR-PDF-LIMIT"))

    def test_business_draft_preserves_page_failure_before_generic_block(self):
        from tests.test_openai_draft_request_contract import DraftRequestContractTest

        fixture = DraftRequestContractTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        payload = fixture.payload(synthetic_pdf(60), synthetic_pdf(60))
        with (mock.patch.object(server, "pricing_reference_authority_error", return_value=""),
              mock.patch.object(server, "protected_ai_draft_mode_enabled", return_value=False),
              mock.patch.object(server, "write_local_log")):
            with self.assertRaises(server.DraftBatchError) as caught:
                server.draft_quote_basis(payload)
        self.assert_safe_counts(caught.exception, pages=120)
        fixture.send.assert_not_called()

    def test_async_draft_failure_keeps_safe_limit_message_and_log(self):
        exception = server.DraftBatchError("pdf_page_limit", diagnostics={
            "pdf_total_pages": 120, "pdf_total_bytes": 12345, "pdf_page_count_complete": 1,
        })
        with (mock.patch.object(server, "draft_quote_basis", side_effect=exception),
              mock.patch.object(server, "write_local_log") as log,
              mock.patch.object(server, "set_job_state") as state):
            server.finish_draft_job("synthetic-job", {})
        self.assertIn("96", state.call_args.kwargs["result"]["errors"][0])
        failure = next(call.args[1] for call in log.call_args_list if call.args[0] == "draft_failed")
        self.assertEqual(failure["pdf_total_pages"], 120)
        self.assertEqual(failure["pdf_total_bytes"], 12345)


if __name__ == "__main__":
    unittest.main()
