"""Publication rejection names only the first incomplete authority condition."""

import copy
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from webapp import server as s


CONDITIONS = (
    ("session_id", ("subject", "session_id")),
    ("publication_id", ("publication", "id")),
    ("scope", ("subject", "scope")),
    ("workspace_id", ("subject", "workspace_id")),
    ("owner_id", ("subject", "owner_id")),
    ("output_revision", ("output_revision",)),
    ("pricing_reference_id", ("pricing_reference", "id")),
    ("pricing_reference_source", ("pricing_reference", "source")),
    ("profile_id", ("profile", "id")),
    ("profile_source", ("profile", "source")),
    ("pricing_reference_digest", ("pricing_reference", "digest")),
    ("profile_digest", ("profile", "digest")),
    ("profile_layout_digest", ("profile", "layout_digest")),
    ("profile_layout_rules_digest", ("profile", "layout_rules_digest")),
)


def blank_field(value, path):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = ""


class PublicationProofReasonsTest(unittest.TestCase):
    def inputs(self):
        digest = "a" * 64
        return {
            "metadata": {"session_id": "quote-synthetic", "exports": {"xlsx": {
                "filename": "quotation.xlsx", "publication_id": "pub-" + "a" * 32,
                "run_id": "", "sha256": digest, "size_bytes": 1,
            }}},
            "patch": {"draft_state": {"outputRevision": 0}},
            "publication_id": "pub-" + "a" * 32,
            "workspace_id": "synthetic-workspace",
            "owner_id": "synthetic-owner",
            "authority": {
                "pricing_reference": {"id": "Synthetic_Catalog", "source": "company", "digest": digest},
                "profile": {
                    "id": "Synthetic_Profile", "source": "company", "digest": digest,
                    "layout_digest": digest, "layout_rules_digest": digest,
                },
            },
        }

    def test_valid_input_returns_unchanged_proof(self):
        proof = s.quote_session_publication_proof(**self.inputs())
        before = copy.deepcopy(proof)
        self.assertIs(s._validate_quote_publication_authority_proof(proof), proof)
        self.assertEqual(proof, before)
        self.assertEqual(proof["output_revision"], 0)
        self.assertEqual(proof["profile"]["id"], "company:Synthetic_Profile")

    def test_each_projected_condition_has_its_own_reason(self):
        valid = s.quote_session_publication_proof(**self.inputs())
        for reason, path in CONDITIONS:
            with self.subTest(reason=reason):
                proof = copy.deepcopy(valid)
                blank_field(proof, path)
                with self.assertRaises(s.QuotePublicationAuthorityIncomplete) as caught:
                    s._validate_quote_publication_authority_proof(proof)
                self.assertIsInstance(caught.exception, ValueError)
                self.assertEqual(caught.exception.safe_reason, reason)
                self.assertEqual(str(caught.exception), "Quote publication authority evidence is incomplete.")

    def test_each_reachable_input_condition_is_rejected_at_publication_boundary(self):
        paths = {
            "session_id": ("metadata", "session_id"),
            "publication_id": ("publication_id",),
            "workspace_id": ("workspace_id",),
            "owner_id": ("owner_id",),
            "output_revision": ("patch", "draft_state", "outputRevision"),
        }
        for reason, path in CONDITIONS:
            if reason == "scope":
                # Scope is fixed to workspace by the production projection.
                continue
            with self.subTest(reason=reason):
                inputs = self.inputs()
                blank_field(inputs, paths.get(reason, ("authority",) + path))
                # An empty publication id otherwise fails the earlier artifact identity guard.
                artifact_check = (
                    mock.patch.object(s, "quote_publication_artifact_proof", return_value={})
                    if reason == "publication_id" else nullcontext()
                )
                with artifact_check:
                    with self.assertRaises(s.QuotePublicationAuthorityIncomplete) as caught:
                        s.quote_session_publication_proof(**inputs)
                self.assertEqual(caught.exception.safe_reason, reason)

    def test_first_failed_condition_wins(self):
        proof = s.quote_session_publication_proof(**self.inputs())
        for reason, path in reversed(CONDITIONS):
            blank_field(proof, path)
            with self.subTest(reason=reason), self.assertRaises(s.QuotePublicationAuthorityIncomplete) as caught:
                s._validate_quote_publication_authority_proof(proof)
            self.assertEqual(caught.exception.safe_reason, reason)

    def test_diagnostic_written_log_contains_reason_without_supplied_values(self):
        inputs = self.inputs()
        inputs["owner_id"] = "synthetic owner canary"
        try:
            s.quote_session_publication_proof(**inputs)
        except s.QuotePublicationAuthorityIncomplete as error:
            details = s.unexpected_error_log_details("ERR-01234567", error)
        else:
            self.fail("Expected incomplete authority.")
        self.assertEqual(details["failure_reason"], "owner_id")
        self.assertEqual(details["error_chain"], ["QuotePublicationAuthorityIncomplete"])
        self.assertTrue(details["failure_site"][0].startswith("webapp/server.py:_validate_quote_publication_authority_proof:"))
        log_root = s.PROJECT_ROOT / "_logs" / "test" / "publication-proof-reasons"
        log_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=log_root) as temporary:
            self.assertTrue(s.write_local_log("quote_session_update_failed", details, log_root=Path(temporary)))
            records = list(Path(temporary).rglob("*.jsonl"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["details"], details)
        text = json.dumps(record)
        for value in (inputs["owner_id"], inputs["workspace_id"], inputs["metadata"]["session_id"],
                      inputs["authority"]["profile"]["id"], "a" * 64,
                      "Quote publication authority evidence is incomplete."):
            self.assertNotIn(value, text)

    def test_unrelated_value_error_keeps_existing_diagnostics(self):
        details = s.unexpected_error_log_details("ERR-01234567", ValueError("synthetic message canary"))
        self.assertEqual(details["error_chain"], ["ValueError"])
        self.assertNotIn("failure_reason", details)
        self.assertNotIn("synthetic message canary", json.dumps(details))

    def test_reason_attribute_must_fully_match_bounded_safe_syntax(self):
        for reason in (None, 1, {}, "", "Uppercase", "owner_id\n", "a" * 61, "synthetic.canary", "synthetic canary"):
            with self.subTest(kind=type(reason).__name__):
                error = ValueError("synthetic message canary")
                error.safe_reason = reason
                details = s.unexpected_error_log_details("ERR-01234567", error)
                self.assertNotIn("failure_reason", details)
                self.assertEqual(details["error_chain"], ["ValueError"])
        error.safe_reason = "a" * 60
        self.assertEqual(s.unexpected_error_log_details("ERR-01234567", error)["failure_reason"], "a" * 60)

    def test_reason_getter_failure_is_fail_safe_without_partial_diagnostics(self):
        class BrokenReason(ValueError):
            @property
            def safe_reason(self):
                raise SystemExit("synthetic getter canary")

        error = BrokenReason("synthetic message canary")
        details = s.unexpected_error_log_details("ERR-01234567", error)
        self.assertEqual(details, {"error_reference": "ERR-01234567", "error_type": "BrokenReason"})

    def test_identity_helper_matrix_preserves_case_and_rejects_unsafe_resource_punctuation(self):
        values = (
            ("pid-alpha2-v1-" + "a" * 24, True, True),
            ("sqag-alpha2-test", True, True),
            ("Profile_ABC", True, True),
            ("Company Profile", False, False),
            ("company.example", False, False),
            ("company:Profile_ABC", False, True),
            ("company:Company Profile", False, False),
            ("company:company.example", False, False),
            ("A" * 256, True, True),
            ("internal-v1:" + "a" * 64, False, False),
        )
        for value, resource_ok, profile_ok in values:
            with self.subTest(length=len(value), resource_ok=resource_ok, profile_ok=profile_ok):
                self.assertEqual(s.safe_resource_id(value, ""), value if resource_ok else "")
                self.assertEqual(bool(s.profile_identity_value(value, "company")), profile_ok)
                self.assertEqual(bool(s.profile_identity_parts(value, "company")[1]), profile_ok)
                self.assertEqual(s.safe_quote_session_id(value, ""), "")
                self.assertEqual(s.safe_quote_publication_id(value, ""), "")
                self.assertEqual(s.safe_reference(value, "run-"), "")
        inputs = self.inputs()
        inputs["owner_id"] = s.privacy_safe_tracking_id("internal-v1:" + "a" * 64, "")
        self.assertEqual(s.publication_subject_id(inputs["owner_id"]), inputs["owner_id"])
        proof = s.quote_session_publication_proof(**inputs)
        self.assertEqual(proof["subject"]["owner_id"], inputs["owner_id"])

    def test_revision_freshness_requires_a_nonnegative_integer(self):
        for value in (None, "", "0", -1, True, False, 0.0, [], {}):
            with self.subTest(kind=type(value).__name__):
                self.assertEqual(s.quote_session_publication_freshness_proof(
                    {"draft_state": {"outputRevision": value}},
                )["output_revision"], -1)
        self.assertEqual(s.quote_session_publication_freshness_proof({})["output_revision"], -1)
        for value in (0, 1):
            self.assertEqual(s.quote_session_publication_freshness_proof(
                {"draft_state": {"outputRevision": value}},
            )["output_revision"], value)

    def test_normalised_digest_prefixes_and_run_fallback_remain_accepted(self):
        inputs = self.inputs()
        for section, fields in (("pricing_reference", ("digest",)), ("profile", ("digest", "layout_digest", "layout_rules_digest"))):
            for field in fields:
                inputs["authority"][section][field] = "SHA256:" + "A" * 64
        inputs["publication_id"] = ""
        inputs["run_id"] = "run-synthetic"
        inputs["metadata"]["exports"]["xlsx"].update(publication_id="run-synthetic", run_id="run-synthetic")
        proof = s.quote_session_publication_proof(**inputs)
        self.assertEqual(proof["publication"]["id"], "run-synthetic")
        self.assertEqual(proof["profile"]["layout_digest"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
