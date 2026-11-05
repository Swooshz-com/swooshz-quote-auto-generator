"""Bounded, server-only unexpected-error metadata using ordinary raised frames."""

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import generate_quote
from webapp import server as s


REFERENCE = "ERR-01234567"
DIAGNOSTIC_FIELDS = {"failure_site", "failure_site_external", "error_chain"}


def raise_chained_error():
    try:
        raise KeyError("canary-detail-text", "canary@example.test")
    except KeyError as cause:
        raise ValueError("canary-detail-text", "canary@example.test") from cause


def raise_supplied_error(error):
    raise error


def raise_recursive_error_with_a_deliberately_long_but_harmless_function_name_to_check_that_the_diagnostic_projection_limits_each_entry_to_its_budget(depth):
    if depth:
        return raise_recursive_error_with_a_deliberately_long_but_harmless_function_name_to_check_that_the_diagnostic_projection_limits_each_entry_to_its_budget(depth - 1)
    raise ValueError("canary-detail-text")


class UnexpectedErrorDiagnosticTest(unittest.TestCase):
    def capture(self, function, *args):
        try:
            function(*args)
        except Exception as error:
            return error
        self.fail("Expected the harmless helper to raise.")

    def assert_written_details(self, details):
        log_root = s.PROJECT_ROOT / "_logs" / "test" / "alpha2-part8b-diagnostics"
        log_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=log_root) as temporary:
            self.assertTrue(s.write_local_log("quote_session_update_failed", details, log_root=Path(temporary)))
            records = list(Path(temporary).rglob("*.jsonl"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["details"], details)
            return json.dumps(record)

    def test_raised_message_canaries_never_enter_metadata_or_written_log(self):
        error = self.capture(raise_chained_error)
        # The real test helper is a top-level source file under this test root.
        with mock.patch.object(s, "PROJECT_ROOT", Path(__file__).resolve().parent):
            details = s.unexpected_error_log_details(REFERENCE, error, job_id="job-synthetic")
        self.assertEqual(details["error_chain"], ["ValueError", "KeyError"])
        self.assertRegex(details["failure_site"][0], r"^test_unexpected_error_diagnostics\.py:raise_chained_error:\d+$")
        self.assertEqual(details["error_reference"], REFERENCE)
        self.assertEqual(details["error_type"], "ValueError")
        self.assertEqual(details["job_id"], "job-synthetic")
        text = self.assert_written_details(details)
        for canary in ("canary-detail-text", "canary@example.test", str(s.PROJECT_ROOT)):
            self.assertNotIn(canary, text)

    def test_external_stdlib_frame_contains_only_module_and_function(self):
        error = self.capture(zipfile.ZipFile, io.BytesIO(b"canary-detail-text"))
        details = s.unexpected_error_log_details(REFERENCE, error)
        self.assertEqual(details["failure_site_external"], "zipfile:_RealGetContents")
        self.assertEqual(details["error_chain"], ["BadZipFile"])
        self.assertNotIn("failure_site", details)
        text = self.assert_written_details(details)
        self.assertNotIn("canary-detail-text", text)
        self.assertNotIn(str(Path(zipfile.__file__).resolve()), text)

    def test_long_deep_frame_names_and_exception_chains_are_bounded(self):
        error = self.capture(
            raise_recursive_error_with_a_deliberately_long_but_harmless_function_name_to_check_that_the_diagnostic_projection_limits_each_entry_to_its_budget,
            150,
        )
        long_error = type("HarmlessDiagnosticName" + "n" * 200, (Exception,), {})
        current = error
        for _ in range(20):
            current.__cause__ = long_error("canary-detail-text")
            current = current.__cause__
        with mock.patch.object(s, "PROJECT_ROOT", Path(__file__).resolve().parent):
            details = s.unexpected_error_log_details(REFERENCE, error)
        self.assertEqual(len(details["failure_site"]), 4)
        self.assertEqual(len(details["error_chain"]), 4)
        self.assertEqual(details["error_chain"][1:], [long_error.__name__[:120]] * 3)
        helper = raise_recursive_error_with_a_deliberately_long_but_harmless_function_name_to_check_that_the_diagnostic_projection_limits_each_entry_to_its_budget
        first_line = helper.__code__.co_firstlineno
        self.assertTrue(details["failure_site"][0].endswith(":" + str(first_line + 3)))
        self.assertTrue(all(site.endswith(":" + str(first_line + 2)) for site in details["failure_site"][1:]))
        fields = {key: value for key, value in details.items() if key in DIAGNOSTIC_FIELDS}
        self.assertLessEqual(len(json.dumps(fields)), 1200)
        for value in fields.values():
            for entry in value if isinstance(value, list) else [value]:
                self.assertLessEqual(len(entry), 120)
        self.assert_written_details(details)

    def test_cause_and_context_cycles_terminate_with_cause_precedence(self):
        for link in ("__cause__", "__context__"):
            with self.subTest(link=link):
                first, second = ValueError(), TypeError()
                setattr(first, link, second)
                setattr(second, link, first)
                self.assertEqual(s.unexpected_error_log_details(REFERENCE, first)["error_chain"], ["ValueError", "TypeError"])
        first = ValueError()
        first.__cause__, first.__context__ = KeyError(), TypeError()
        self.assertEqual(s.unexpected_error_log_details(REFERENCE, first)["error_chain"], ["ValueError", "KeyError"])

    def test_source_scope_is_top_level_webapp_or_scripts_and_never_absolute(self):
        error = self.capture(raise_chained_error)
        self.assertNotIn("failure_site", s.unexpected_error_log_details(REFERENCE, error))
        cases = (
            (error, Path(__file__).resolve().parent, "test_unexpected_error_diagnostics.py:raise_chained_error:"),
            (self.capture(s._unexpected_error_diagnostics, None), s.PROJECT_ROOT, "webapp/server.py:_unexpected_error_diagnostics:"),
            (self.capture(generate_quote.col_to_index, "canary-detail-text"), s.PROJECT_ROOT, "scripts/generate_quote.py:col_to_index:"),
        )
        for raised, root, prefix in cases:
            with self.subTest(prefix=prefix), mock.patch.object(s, "PROJECT_ROOT", root):
                details = s.unexpected_error_log_details(REFERENCE, raised)
                self.assertTrue(details["failure_site"][0].startswith(prefix))
                for site in details["failure_site"]:
                    path, function, line = site.rsplit(":", 2)
                    self.assertFalse(Path(path).is_absolute())
                    self.assertNotRegex(path, r"[A-Za-z]:|\\|^/|(?:^|/)\.\.(?:/|$)")
                    self.assertTrue(line.isdecimal())
                    self.assertTrue(function)
        # A root outside these real frames cannot admit a repository source site.
        with mock.patch.object(s, "PROJECT_ROOT", s.PROJECT_ROOT / "unrelated-source-root"):
            details = s.unexpected_error_log_details(REFERENCE, error)
        self.assertNotIn("failure_site", details)
        self.assertNotIn(str(s.PROJECT_ROOT), json.dumps(details))
        self.assertRegex(details["failure_site_external"], r"^tests\.test_unexpected_error_diagnostics:[A-Za-z_]+$")

    def test_diagnostic_failure_including_system_exit_preserves_original_fields(self):
        original = ValueError("canary-detail-text")
        expected = {"error_reference": REFERENCE, "error_type": "ValueError", "job_id": "job-synthetic"}
        for failure in (OSError("canary-detail-text"), SystemExit("canary-detail-text")):
            with self.subTest(failure=type(failure).__name__), mock.patch.object(s, "_unexpected_error_diagnostics", side_effect=failure):
                details = s.unexpected_error_log_details(REFERENCE, original, job_id="job-synthetic")
                self.assertEqual(details, expected)
                self.assert_written_details(details)

        class BrokenChain(ValueError):
            def __getattribute__(self, name):
                if name == "__cause__":
                    raise SystemExit("canary-detail-text")
                return super().__getattribute__(name)

        error = self.capture(raise_supplied_error, BrokenChain("canary-detail-text"))
        # Collect source metadata before the broken chain fails: no partial fields survive.
        with mock.patch.object(s, "PROJECT_ROOT", Path(__file__).resolve().parent):
            details = s.unexpected_error_log_details(REFERENCE, error, job_id="job-synthetic")
        self.assertEqual(details, {**expected, "error_type": "BrokenChain"})
        self.assert_written_details(details)

    def test_ai_and_no_exception_callers_keep_their_existing_contract(self):
        expected = {"error_reference": REFERENCE, "job_id": "job-synthetic", "count": 0}
        with mock.patch.object(s, "_unexpected_error_diagnostics", side_effect=AssertionError("Unexpected collection")) as collector:
            self.assertEqual(s.unexpected_error_log_details(" " + REFERENCE + " ", job_id="job-synthetic", count=0, empty="", absent=None, rows=[], mapping={}), expected)
            for error in (ValueError("canary-detail-text"), "canary-detail-text"):
                with self.subTest(error_type=type(error).__name__):
                    legacy = {**expected, **s.ai_failure_metadata(error, error_reference=REFERENCE)}
                    self.assertEqual(s.ai_error_log_details(REFERENCE, error, job_id="job-synthetic", count=0), legacy)
                    self.assertFalse(DIAGNOSTIC_FIELDS.intersection(legacy))
            collector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
