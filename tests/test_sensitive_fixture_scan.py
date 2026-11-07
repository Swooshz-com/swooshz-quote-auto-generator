import contextlib
import io
import json
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from scripts import scan_sensitive_fixtures


DANGEROUS_OUT_OF_SCOPE_PATHS = {
    "koncept-images-pte-ltd.quote-company-profile.json": "private-profile-export",
    "private.quote-company-profile.json": "private-profile-export",
    "private-pricing-upload.xlsx": "private-pricing-upload",
    "local-pricing-upload.csv": "private-pricing-upload",
    "quotation.xlsx": "generated-quote-output",
    "generated-quote-123.xlsx": "generated-quote-output",
    "quote-output.pdf": "generated-quote-output",
}
IGNORED_LOCAL_ARTIFACT_PATHS = {
    "screenshots/quote.png": "local-output-artifact",
    "preview.screenshot.png": "local-output-artifact",
}


class SensitiveFixtureScanTest(unittest.TestCase):
    def test_quote_contract_exceptions_are_path_and_category_specific(self):
        allowed = ("docs/quotation-api-v1.md", "docs/quotation-api-v1.openapi.json")
        neighbours = ("docs/quotation-other.md", "fixtures/quotation-api-v1.openapi.json")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel_path in (*allowed, *neighbours):
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"internal_cost": "synthetic"}', encoding="utf-8")
                self.assertTrue(scan_sensitive_fixtures.in_default_scan_scope(rel_path))
                findings = scan_sensitive_fixtures.scan_file(path, root)
                with self.subTest(path=rel_path):
                    self.assertEqual(findings, set() if rel_path in allowed else {
                        scan_sensitive_fixtures.Finding(rel_path, "internal-pricing-field", "review")})

                # Construct a synthetic credential-shaped canary, never a real secret.
                canary = "sk-" + "syntheticFixtureCanary1234"
                path.write_text('{"internal_cost": "synthetic", "value": "' + canary + '"}',
                                encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = scan_sensitive_fixtures.main([
                        "--root", str(root), "--path", rel_path, "--fail-on-review"])
                with self.subTest(secret_path=rel_path):
                    self.assertEqual(code, 1)
                    self.assertIn("secret-credential-marker", output.getvalue())
                    self.assertNotIn(canary, output.getvalue())

    def test_marker_regex_matches_and_rejects_long_whitespace_quickly(self):
        cases = (
            (scan_sensitive_fixtures.REAL_COMPANY_RE, ("koncept", "images", "pte")),
            (scan_sensitive_fixtures.REAL_COMPANY_RE, ("koncept", "image", "pte")),
            (scan_sensitive_fixtures.CUSTOMER_SAMPLE_RE, ("kent", "group")),
        )
        for pattern, words in cases:
            for separator in (" ", "   ", "-", "---", " -  - ", "\t\n"):
                for transform in (str.lower, str.upper, str.swapcase):
                    text = transform(separator.join(words))
                    with self.subTest(pattern=pattern.pattern, text=text):
                        self.assertIsNotNone(pattern.fullmatch(text))
            for text in ("".join(words), " ".join((*words[:-1], "unrelated"))):
                with self.subTest(pattern=pattern.pattern, text=text):
                    self.assertIsNone(pattern.search(text))

        for pattern, prefix in (
            (scan_sensitive_fixtures.REAL_COMPANY_RE, "koncept"),
            (scan_sensitive_fixtures.REAL_COMPANY_RE, "koncept images"),
            (scan_sensitive_fixtures.CUSTOMER_SAMPLE_RE, "kent"),
        ):
            with self.subTest(pattern=pattern.pattern, prefix=prefix):
                start = time.perf_counter()
                match = pattern.search(prefix + " " * 50 + "unrelated")
                elapsed = time.perf_counter() - start
                self.assertIsNone(match)
                self.assertLess(elapsed, 2.0)

    def test_current_committed_fixture_scan_has_no_blocking_findings(self):
        findings = scan_sensitive_fixtures.scan_tracked_files(Path.cwd())
        blocking = [finding for finding in findings if finding.severity == "block"]

        self.assertEqual(blocking, [])
        self.assertEqual(findings, [])

    def test_dangerous_private_or_generated_files_are_in_default_scan_scope(self):
        for rel_path in DANGEROUS_OUT_OF_SCOPE_PATHS:
            with self.subTest(rel_path=rel_path):
                self.assertTrue(scan_sensitive_fixtures.in_default_scan_scope(rel_path))
                self.assertTrue(scan_sensitive_fixtures.in_default_scan_scope(f"arbitrary-folder/{rel_path}"))
        for rel_path in IGNORED_LOCAL_ARTIFACT_PATHS:
            with self.subTest(rel_path=rel_path):
                self.assertTrue(scan_sensitive_fixtures.in_default_scan_scope(rel_path))

    def test_cli_reports_path_and_category_without_sensitive_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_path = root / "private.quote-company-profile.json"
            export_path.write_text(
                json.dumps({
                    "company": "Do Not Echo Pte Ltd",
                    "bank": "Do Not Echo Bank",
                    "account_number": "1234567890",
                }),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = scan_sensitive_fixtures.main([
                    "--root",
                    str(root),
                    "--path",
                    str(export_path),
                ])

        text = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("private-profile-export", text)
        self.assertIn("bank-payment-marker", text)
        self.assertIn("private.quote-company-profile.json", text)
        self.assertNotIn("Do Not Echo", text)
        self.assertNotIn("1234567890", text)

    def test_synthetic_review_allowlist_does_not_suppress_blocking_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_path = (
                root
                / "tests"
                / "fixtures"
                / "quote-generator"
                / "profiles"
                / "synthetic-exhibition-fixture-template"
                / "profile.json"
            )
            profile_path.parent.mkdir(parents=True)
            profile_path.write_text(
                json.dumps({
                    "company": {
                        "name": "Synthetic Fixture Company",
                        "logo_data_url": "data:image/png;base64,ZmFrZQ==",
                        "bank": "Do Not Echo Bank",
                    }
                }),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = scan_sensitive_fixtures.main([
                    "--root",
                    str(root),
                    "--path",
                    str(profile_path),
                ])

        text = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("bank-payment-marker", text)
        self.assertNotIn("embedded-logo-reference", text)
        self.assertNotIn("Do Not Echo", text)

    def test_tracked_forced_private_or_generated_files_block_by_category_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            expected_categories = {**DANGEROUS_OUT_OF_SCOPE_PATHS, **IGNORED_LOCAL_ARTIFACT_PATHS}
            ignored_paths = list(expected_categories)
            (root / ".gitignore").write_text("\n".join(ignored_paths), encoding="utf-8")

            for rel_path in ignored_paths:
                path = root / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Do Not Echo Fixture Content", encoding="utf-8")

            subprocess.run(
                ["git", "add", ".gitignore"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["git", "add", "-f", *ignored_paths],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            findings = scan_sensitive_fixtures.scan_tracked_files(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                scan_sensitive_fixtures.print_findings(findings)

        blocking = {(finding.path, finding.category) for finding in findings if finding.severity == "block"}
        for rel_path, category in expected_categories.items():
            with self.subTest(rel_path=rel_path, category=category):
                self.assertIn((rel_path, category), blocking)

        text = output.getvalue()
        self.assertIn("BLOCK", text)
        self.assertIn("private-profile-export", text)
        self.assertIn("private-pricing-upload", text)
        self.assertIn("generated-quote-output", text)
        self.assertIn("local-output-artifact", text)
        self.assertNotIn("Do Not Echo Fixture Content", text)

    def test_unreviewed_company_or_customer_markers_fail_without_echoing_values(self):
        original_company_re = scan_sensitive_fixtures.REAL_COMPANY_RE
        original_customer_re = scan_sensitive_fixtures.CUSTOMER_SAMPLE_RE
        scan_sensitive_fixtures.REAL_COMPANY_RE = re.compile(r"synthetic\s+sensitive\s+company", re.IGNORECASE)
        scan_sensitive_fixtures.CUSTOMER_SAMPLE_RE = re.compile(r"synthetic\s+sensitive\s+customer", re.IGNORECASE)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fixture_path = root / "profiles" / "new" / "profile.json"
                fixture_path.parent.mkdir(parents=True)
                fixture_path.write_text(
                    json.dumps({
                        "company": "Synthetic Sensitive Company",
                        "customer": "Synthetic Sensitive Customer",
                    }),
                    encoding="utf-8",
                )

                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = scan_sensitive_fixtures.main([
                        "--root",
                        str(root),
                        "--path",
                        str(fixture_path),
                    ])
        finally:
            scan_sensitive_fixtures.REAL_COMPANY_RE = original_company_re
            scan_sensitive_fixtures.CUSTOMER_SAMPLE_RE = original_customer_re

        text = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("real-company-identity-marker", text)
        self.assertIn("customer-sample-marker", text)
        self.assertIn("profiles/new/profile.json", text)
        self.assertNotIn("Synthetic Sensitive Company", text)
        self.assertNotIn("Synthetic Sensitive Customer", text)


if __name__ == "__main__":
    unittest.main()
