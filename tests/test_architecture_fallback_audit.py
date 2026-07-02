import contextlib
import io
import json
import shutil
import unittest
import uuid
from pathlib import Path

from scripts import audit_architecture_fallbacks


@contextlib.contextmanager
def temporary_workspace_dir():
    base = Path.cwd() / "_tmp" / "test-architecture-fallback-audit"
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"case-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ArchitectureFallbackAuditTest(unittest.TestCase):
    def test_detects_known_fallback_and_local_storage_markers(self):
        with temporary_workspace_dir() as tmp:
            root = Path(tmp)
            script = root / "webapp" / "server.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                "\n".join(
                    [
                        "def send_download(path):",
                        "    try:",
                        "        fallback_to = 'local'",
                        "    except Exception:",
                        "        pass",
                        "KQAG_STORAGE_MODE = 'database'",
                        "QUOTE_DATA_ROOT = 'redacted'",
                        "load_profile_pack('default')",
                        "loadSample('kent-group')",
                        "# Load Sample Kent fake example",
                    ]
                ),
                encoding="utf-8",
            )

            report = audit_architecture_fallbacks.build_report(root, max_hits_per_pattern=10)

        self.assertGreater(report["pattern_totals"]["fallback_to"], 0)
        self.assertGreater(report["pattern_totals"]["KQAG_STORAGE_MODE"], 0)
        self.assertGreater(report["pattern_totals"]["QUOTE_DATA_ROOT"], 0)
        self.assertGreater(report["pattern_totals"]["load_profile_pack"], 0)
        self.assertGreater(report["pattern_totals"]["Load Sample"], 0)
        self.assertGreater(report["pattern_totals"]["loadSample"], 0)
        self.assertGreater(report["pattern_totals"]["Kent"], 0)
        self.assertGreater(report["pattern_totals"]["fake"], 0)
        self.assertGreater(report["category_totals"]["load_sample_surface"], 0)
        self.assertGreater(report["category_totals"]["broad_exception_boundary"], 0)
        self.assertGreater(report["category_totals"]["artifact_download_boundary"], 0)

    def test_output_does_not_echo_private_source_contents_or_absolute_root(self):
        with temporary_workspace_dir() as tmp:
            root = Path(tmp)
            path = root / "scripts" / "private_case.py"
            path.parent.mkdir(parents=True)
            private_path = "C:/Users/Private/Koncept Runtime"
            path.write_text(
                f"secret = '{private_path}'\n# fallback local sample\n",
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = audit_architecture_fallbacks.main([
                    "--root",
                    str(root),
                    "--max-hits-per-pattern",
                    "10",
                ])

        text = output.getvalue()
        parsed = json.loads(text)
        self.assertEqual(exit_code, 0)
        self.assertEqual(parsed["scanned_files_count"], 1)
        self.assertIn("scripts/private_case.py", text)
        self.assertNotIn(private_path, text)
        self.assertNotIn(str(root), text)
        self.assertNotIn("secret =", text)

    def test_current_repo_reports_expected_architecture_markers(self):
        report = audit_architecture_fallbacks.build_report(Path.cwd(), max_hits_per_pattern=1)

        self.assertGreater(report["pattern_totals"]["KQAG_STORAGE_MODE"], 0)
        self.assertGreater(report["pattern_totals"]["KQAG_ARTIFACT_STORAGE_MODE"], 0)
        self.assertGreater(report["pattern_totals"]["send_download"], 0)
        self.assertGreater(report["pattern_totals"]["/api/jobs"], 0)
        self.assertGreater(report["pattern_totals"]["Load Sample"], 0)
        self.assertGreater(report["definition_totals"]["referenced"], 0)


if __name__ == "__main__":
    unittest.main()
