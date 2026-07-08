import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local-uat-sqag-start.ps1"
RUNBOOK = ROOT / "docs" / "platform-uat-smoke-runbook.md"


class LocalUatHelperTest(unittest.TestCase):
    def test_windows_helper_documents_safe_platform_uat_start_contract(self):
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("param(", script)
        for name in (
            "PlatformBaseUrl",
            "SqagDatabaseUrl",
            "UatRoot",
            "SkipMigrations",
            "SqagHost",
            "SqagPort",
        ):
            self.assertIn(f"${name}", script)

        self.assertLess(script.index('Command = "py"'), script.index('Command = "python"'))
        self.assertLess(script.index('Command = "python"'), script.index('Command = "python3"'))
        self.assertRegex(script, r"Arguments\s*=\s*@\('-3'\)")
        self.assertIn("[System.Security.Cryptography.RandomNumberGenerator]::Create()", script)
        self.assertIn("$rng.GetBytes($bytes)", script)
        self.assertIn("$rng.Dispose()", script)
        self.assertNotIn("RandomNumberGenerator]::Fill", script)

        for env_name in (
            "APP_MODE",
            "AUTH_REQUIRED",
            "SESSION_SECRET",
            "SQAG_PLATFORM_LAUNCH_MODE",
            "SQAG_PLATFORM_BASE_URL",
            "SQAG_STORAGE_MODE",
            "SQAG_ARTIFACT_STORAGE_MODE",
            "SQAG_DATABASE_URL",
            "QUOTE_DATA_ROOT",
            "QUOTE_OUTPUT_ROOT",
            "QUOTE_TMP_ROOT",
            "QUOTE_LOG_ROOT",
        ):
            self.assertIn(env_name, script)

        self.assertIn("sqag-platform-uat", script)
        self.assertIn("custom path configured", script)
        self.assertIn("sqlite:///", script)
        self.assertIn("scripts/migrate_sqag_storage.py", script.replace("\\", "/"))
        self.assertIn("-m", script)
        self.assertIn("webapp.server", script)
        self.assertIn("PLATFORM_SQAG_APP_BASE_URL=http://127.0.0.1:", script)

        self.assertNotRegex(script, r"Write-(?:Host|Output).*SESSION_SECRET")
        self.assertNotRegex(script, r"Write-(?:Host|Output).*SQAG_DATABASE_URL")
        self.assertNotRegex(script, re.compile(r"launch[_-]?token", re.IGNORECASE))

    def test_platform_uat_runbook_recommends_helper_without_removing_manual_fallback(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("scripts/local-uat-sqag-start.ps1", runbook)
        self.assertIn("recommended Windows local path", runbook)
        self.assertIn("Manual fallback", runbook)
        self.assertIn("python -m webapp.server", runbook)
        self.assertIn("X-App-Launch-Token", runbook)


if __name__ == "__main__":
    unittest.main()
