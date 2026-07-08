import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DOC = REPO_ROOT / "docs" / "platform-integration-contract.md"


class PlatformIntegrationContractDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CONTRACT_DOC.read_text(encoding="utf-8")

    def test_contract_doc_cites_sqag_and_platform_evidence(self):
        text = self.text
        self.assertIn("5bce4d52e4273762375d97149b1d77e5716189b2", text)
        for expected in (
            "consume_platform_launch_token()",
            "safe_platform_launch_context()",
            "safe_platform_session_context()",
            "app_storage_for_auth_session()",
            "artifact_storage_for_auth_session()",
            "docs/sqag-integration-contract.md",
            "docs/app-access-contract.md",
            "src/http/route-contracts.ts",
            "src/platform/app-launch-token-consume-service.ts",
        ):
            self.assertIn(expected, text)

    def test_contract_doc_records_required_claims_and_fail_closed_rules(self):
        text = self.text
        for expected in (
            "user.userId",
            "workspace.workspaceId",
            "app.appKey",
            "appKey=sqag",
            "membershipRole",
            "launchTokenExpiresAt",
            "Unsupported roles fail closed",
            "Missing Platform workspace context blocks database storage access.",
            "Platform PR #79",
            "landed the Platform-owned",
            "Hosted Platform-to-SQAG smoke remains pending",
            "Missing workspace-owned profile/pricing/layout data does not fall back",
            "Production remains blocked",
        ):
            self.assertIn(expected, text)

    def test_contract_doc_marks_platform_app_key_migration_complete_but_hosted_smoke_pending(self):
        text = self.text
        self.assertIn("Platform app-key migration complete", text)
        self.assertIn("Hosted Platform-to-SQAG smoke remains pending", text)
        self.assertIn("The current Platform `origin/main` contract matches SQAG's adapter assumptions", text)
        self.assertNotIn("platform_app_key_migration_pending", text)

    def test_contract_doc_is_metadata_only(self):
        text = self.text
        forbidden_fragments = (
            "postgres://",
            "postgresql://",
            "mysql://",
            "sqlite:///",
            "mongodb://",
            "http://localhost",
            "https://localhost",
            "example.com",
            "example.test",
            "C:\\Users\\",
            "/Users/",
            "/home/",
            "GH_TOKEN=",
            "GITHUB_TOKEN=",
            "sk-",
            "ghp_",
            "github_pat_",
            "BEGIN PRIVATE KEY",
        )
        for forbidden in forbidden_fragments:
            self.assertNotIn(forbidden, text)

    def test_contract_doc_points_to_existing_sqag_coverage(self):
        text = self.text
        for expected in (
            "test_platform_launch_mode_consumes_header_token_and_sets_safe_session",
            "test_database_storage_scopes_profiles_pricing_and_sessions_by_platform_workspace",
            "test_database_storage_new_workspace_has_no_koncept_or_synthetic_defaults",
            "test_platform_session_context_blocks_local_quote_session_runtime_storage_in_local_app_mode",
            "test_platform_session_context_blocks_local_artifact_storage_in_local_app_mode",
            "test_platform_uat_smoke_launch_generate_list_and_download_database_artifact",
        ):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
