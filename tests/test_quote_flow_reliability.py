"""Quote-step admission, awaited loading, failure/retry and saved-detail boundaries."""
import subprocess
import unittest
from tests.test_webapp import ROOT, require_node


class QuoteFlowReliabilityTest(unittest.TestCase):
    def run_contract(self, mode):
        result = subprocess.run(
            [require_node(self), "tests/quote_flow_reliability_harness.cjs", mode],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pending_loading_duplicate_failure_and_retry(self):
        self.run_contract("loading")

    def test_validation_dependency_choice_and_nested_basis_await(self):
        self.run_contract("admission")

    def test_failed_malformed_and_successful_detail_read(self):
        self.run_contract("detail")

    def test_current_pdf_reuse_and_failed_generation_cleanup(self):
        self.run_contract("pdf")

    def test_all_next_save_acknowledgements_and_retry(self):
        self.run_contract("ack")

    def test_checkpoint_multiline_address_equivalence_preserves_substantive_changes(self):
        self.run_contract("checkpoint")
