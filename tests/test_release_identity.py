"""Synthetic, offline release identity regressions; no deployment or host calls."""

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import verify_release_identity as identity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = hashlib.sha1(b"synthetic accepted source").hexdigest()
FIX = hashlib.sha1(b"synthetic required fix").hexdigest()
CHERRY_PICK = hashlib.sha1(b"synthetic reviewed equivalent").hexdigest()
STALE = hashlib.sha1(b"synthetic older source").hexdigest()
ASSET = b"// Synthetic served application fixture.\n"


class ReleaseIdentityTest(unittest.TestCase):
    def setUp(self):
        self.expected = {
            "source_sha": SOURCE,
            "image_id": "sha256:" + hashlib.sha256(b"synthetic image").hexdigest(),
            "asset_sha256": hashlib.sha256(ASSET).hexdigest(),
            "required_fixes": [FIX],
        }
        self.observed = {key: self.expected[key] for key in ("source_sha", "image_id")}
        self.ancestry = mock.Mock(return_value=True)
        self.network = mock.patch("socket.socket.connect", side_effect=AssertionError("outbound network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def verify(self, asset=ASSET):
        return identity.verify_release_identity(self.expected, self.observed, asset, is_ancestor=self.ancestry)

    def review(self):
        self.expected["reviewed_cherry_picks"] = [{
            "fix_sha": FIX, "cherry_pick_sha": CHERRY_PICK,
            "review": "synthetic-equivalence-review-1",
        }]

    def test_matching_source_image_asset_and_required_fix_pass(self):
        before = copy.deepcopy((self.expected, self.observed))
        self.assertEqual(self.verify(), {"status": "passed", "evidence_scope": "offline_release_identity_comparison"})
        self.ancestry.assert_called_once_with(FIX, SOURCE)
        self.assertEqual((self.expected, self.observed), before)

    def test_stale_source_image_and_asset_each_fail_independently(self):
        for field, value, code in (
            ("source_sha", STALE, "source_mismatch"),
            ("image_id", "sha256:" + hashlib.sha256(b"stale image").hexdigest(), "image_mismatch"),
        ):
            with self.subTest(field=field):
                original = self.observed[field]
                self.observed[field] = value
                with self.assertRaisesRegex(identity.IdentityMismatch, code):
                    self.verify()
                self.observed[field] = original
        with self.assertRaisesRegex(identity.IdentityMismatch, "asset_mismatch"):
            self.verify(b"// Stale browser asset.\n")
        self.ancestry.assert_not_called()

    def test_asset_hash_uses_actual_bytes_without_line_ending_normalisation(self):
        with self.assertRaisesRegex(identity.IdentityMismatch, "asset_mismatch"):
            self.verify(ASSET.replace(b"\n", b"\r\n"))
        for asset in (b"", None, self.expected["asset_sha256"]):
            with self.subTest(asset_type=type(asset).__name__), self.assertRaisesRegex(identity.IdentityMismatch, "missing_served_asset"):
                self.verify(asset)

    def test_missing_required_fix_fails_even_when_all_identity_pins_match(self):
        self.ancestry.return_value = False
        with self.assertRaisesRegex(identity.IdentityMismatch, "required_fix_missing"):
            self.verify()

    def test_every_required_fix_must_be_present(self):
        self.expected["required_fixes"].append(STALE)
        self.ancestry.side_effect = [True, False]
        with self.assertRaisesRegex(identity.IdentityMismatch, "required_fix_missing"):
            self.verify()
        self.assertEqual(self.ancestry.call_args_list, [mock.call(FIX, SOURCE), mock.call(STALE, SOURCE)])

    def test_explicitly_reviewed_equivalent_in_candidate_history_passes(self):
        self.review()
        self.ancestry.side_effect = [False, True]
        self.assertEqual(self.verify()["status"], "passed")
        self.assertEqual(self.ancestry.call_args_list, [mock.call(FIX, SOURCE), mock.call(CHERRY_PICK, SOURCE)])

    def test_review_without_equivalent_ancestry_still_fails(self):
        self.review()
        self.ancestry.return_value = False
        with self.assertRaisesRegex(identity.IdentityMismatch, "required_fix_missing"):
            self.verify()

    def test_review_never_waives_stale_identity_or_asset(self):
        self.review()
        self.observed["source_sha"] = STALE
        with self.assertRaisesRegex(identity.IdentityMismatch, "source_mismatch"):
            self.verify()
        self.observed["source_sha"] = SOURCE
        with self.assertRaisesRegex(identity.IdentityMismatch, "asset_mismatch"):
            self.verify(b"stale")
        self.ancestry.assert_not_called()

    def test_unknown_ancestry_never_becomes_a_reviewed_pass(self):
        self.review()
        self.ancestry.side_effect = identity.IdentityMismatch("ancestry_unverified")
        with self.assertRaisesRegex(identity.IdentityMismatch, "ancestry_unverified"):
            self.verify()
        self.ancestry.assert_called_once_with(FIX, SOURCE)
        self.ancestry.side_effect = None
        for unknown in (None, "true", 1):
            with self.subTest(unknown=unknown), self.assertRaisesRegex(identity.IdentityMismatch, "ancestry_unverified"):
                self.ancestry.return_value = unknown
                self.verify()

    def test_missing_malformed_or_unknown_identity_metadata_fails_closed(self):
        expected, observed = copy.deepcopy(self.expected), copy.deepcopy(self.observed)
        for target, field, value in (
            ("expected", "source_sha", SOURCE[:7]), ("observed", "source_sha", None),
            ("expected", "image_id", "synthetic:latest"), ("observed", "image_id", ""),
            ("expected", "asset_sha256", ""), ("expected", "asset_sha256", 123),
            ("expected", "required_fixes", []), ("expected", "required_fixes", [FIX[:7]]),
            ("expected", "required_fixes", [FIX, FIX]), ("observed", "contains_fixes", [FIX]),
        ):
            with self.subTest(target=target, field=field, value=value):
                self.expected, self.observed = copy.deepcopy(expected), copy.deepcopy(observed)
                getattr(self, target)[field] = value
                with self.assertRaises(identity.IdentityMismatch):
                    self.verify()
        for target, document in (("expected", expected), ("observed", observed)):
            for field in document:
                with self.subTest(target=target, missing=field):
                    self.expected, self.observed = copy.deepcopy(expected), copy.deepcopy(observed)
                    del getattr(self, target)[field]
                    with self.assertRaises(identity.IdentityMismatch):
                        self.verify()
        for value in (None, [], "unknown"):
            with self.subTest(document=value), self.assertRaises(identity.IdentityMismatch):
                identity.verify_release_identity(value, observed, ASSET, is_ancestor=self.ancestry)

    def test_cherry_pick_requires_explicit_scoped_nonblank_review(self):
        for reviews in (
            None, [CHERRY_PICK], [{}],
            [{"fix_sha": FIX, "cherry_pick_sha": CHERRY_PICK}],
            [{"fix_sha": FIX, "cherry_pick_sha": CHERRY_PICK, "review": " "}],
            [{"fix_sha": STALE, "cherry_pick_sha": CHERRY_PICK, "review": "review-1"}],
            [{"fix_sha": FIX, "cherry_pick_sha": FIX, "review": "review-1"}],
            [{"fix_sha": FIX, "cherry_pick_sha": CHERRY_PICK[:7], "review": "review-1"}],
        ):
            with self.subTest(reviews=reviews), self.assertRaises(identity.IdentityMismatch):
                self.expected["reviewed_cherry_picks"] = reviews
                self.verify()
        self.review()
        self.expected["reviewed_cherry_picks"] *= 2
        with self.assertRaisesRegex(identity.IdentityMismatch, "invalid_cherry_pick_binding"):
            self.verify()


class ReleaseIdentityGitTest(unittest.TestCase):
    def test_git_commands_are_bounded_local_reads_with_full_commit_ids(self):
        for code, answer in ((0, True), (1, False)):
            with self.subTest(code=code), mock.patch.object(identity.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, b"commit\n"),
                subprocess.CompletedProcess([], 0, b"commit\n"),
                subprocess.CompletedProcess([], code, b""),
            ]) as run:
                self.assertIs(identity.git_is_ancestor(ROOT, FIX, SOURCE), answer)
                prefix = ["git", "-c", f"safe.directory={ROOT.resolve().as_posix()}", "-C", str(ROOT)]
                options = {"capture_output": True, "timeout": 10, "check": False, "env": mock.ANY}
                self.assertEqual(run.call_args_list, [
                    mock.call(prefix + ["cat-file", "-t", FIX], **options),
                    mock.call(prefix + ["cat-file", "-t", SOURCE], **options),
                    mock.call(prefix + ["merge-base", "--is-ancestor", FIX, SOURCE], **options),
                ])
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
                    self.assertEqual(call.kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
                    self.assertEqual(call.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_missing_objects_noncommit_objects_git_errors_and_timeouts_hold_acceptance(self):
        for response in (
            subprocess.CompletedProcess([], 128, b""),
            subprocess.CompletedProcess([], 0, b"blob\n"),
            FileNotFoundError("synthetic unavailable git"),
            subprocess.TimeoutExpired("git", 10),
        ):
            with self.subTest(response=type(response).__name__), mock.patch.object(identity.subprocess, "run") as run:
                if isinstance(response, Exception):
                    run.side_effect = response
                else:
                    run.return_value = response
                with self.assertRaisesRegex(identity.IdentityMismatch, "ancestry_unverified"):
                    identity.git_is_ancestor(ROOT, FIX, SOURCE)
        with mock.patch.object(identity.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, b"commit\n"),
            subprocess.CompletedProcess([], 0, b"commit\n"),
            subprocess.CompletedProcess([], 128, b""),
        ]), self.assertRaisesRegex(identity.IdentityMismatch, "ancestry_unverified"):
            identity.git_is_ancestor(ROOT, FIX, SOURCE)


class ReleaseIdentityCliTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "_tmp" / "tests"
        self.created_scratch = not scratch.exists()
        self.created_parent = not scratch.parent.exists()
        scratch.mkdir(parents=True, exist_ok=True)
        if self.created_scratch:
            self.addCleanup(self.remove_empty_scratch, scratch)
        temporary = tempfile.TemporaryDirectory(prefix="release-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        head = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, timeout=10, check=True,
        ).stdout.decode("ascii").strip()
        self.expected = {
            "source_sha": head, "image_id": "sha256:" + hashlib.sha256(b"synthetic image").hexdigest(),
            "asset_sha256": hashlib.sha256(ASSET).hexdigest(), "required_fixes": [head],
        }
        (self.folder / "expected.json").write_text(json.dumps(self.expected), encoding="utf-8")
        (self.folder / "observed.json").write_text(json.dumps({key: self.expected[key] for key in ("source_sha", "image_id")}), encoding="utf-8")
        (self.folder / "served.js").write_bytes(ASSET)
        self.argv = ["--expected", str(self.folder / "expected.json"), "--observed", str(self.folder / "observed.json"),
                     "--served-asset", str(self.folder / "served.js"), "--repository", str(ROOT)]

    def remove_empty_scratch(self, scratch):
        scratch.rmdir()
        # Only remove the parent if this test's files were its only contents.
        if self.created_parent and not any(scratch.parent.iterdir()):
            scratch.parent.rmdir()

    def run_cli(self):
        output = io.StringIO()
        with mock.patch("socket.socket.connect", side_effect=AssertionError("outbound network forbidden")), contextlib.redirect_stdout(output):
            status = identity.main(self.argv)
        report = json.loads(output.getvalue())
        self.assertNotIn(str(self.folder), output.getvalue())
        self.assertNotIn(self.expected["source_sha"], output.getvalue())
        return status, report

    def test_operator_cli_checks_real_local_git_ancestry_and_raw_asset_without_writes(self):
        before = {path.name: path.read_bytes() for path in self.folder.iterdir()}
        self.assertEqual(self.run_cli(), (0, {"status": "passed", "evidence_scope": "offline_release_identity_comparison"}))
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "verify_release_identity.py"), *self.argv],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "passed")
        self.assertEqual({path.name: path.read_bytes() for path in self.folder.iterdir()}, before)

    def test_stale_cli_asset_returns_nonzero_without_repairing_it(self):
        asset = self.folder / "served.js"
        asset.write_bytes(b"stale")
        self.assertEqual(self.run_cli(), (1, {"status": "failed", "reason": "asset_mismatch"}))
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "verify_release_identity.py"), *self.argv],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "failed", "reason": "asset_mismatch"})
        self.assertEqual(asset.read_bytes(), b"stale")

    def test_invalid_missing_duplicate_and_oversized_json_fail_without_echoing_content(self):
        observed = self.folder / "observed.json"
        for content in (
            b"not-json", b"\xff", b'{"source_sha":"one","source_sha":"two"}',
            b" " * 65537, b"[" * 1100 + b"0" + b"]" * 1100,
        ):
            with self.subTest(content_length=len(content)):
                observed.write_bytes(content)
                status, report = self.run_cli()
                self.assertEqual(status, 1)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(set(report), {"status", "reason"})
        observed.unlink()
        self.assertEqual(self.run_cli(), (1, {"status": "failed", "reason": "identity_input_unreadable"}))


if __name__ == "__main__":
    unittest.main()
