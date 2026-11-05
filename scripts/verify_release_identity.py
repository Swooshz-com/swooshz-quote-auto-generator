"""Compare trusted release expectations with independently captured evidence.

Offline and read-only: this command never contacts a host, fetches Git objects,
deploys, or repairs a mismatch. Capture installed metadata and the raw served
app.js bytes separately under the same operator-approved target/candidate.

Expected JSON (trusted acceptance policy, not copied from the installation):
  source_sha: full lowercase Git commit SHA
  image_id: immutable sha256:<64 lowercase hex> image digest
  asset_sha256: SHA256 of the accepted app.js bytes
  required_fixes: nonempty list of full commit SHAs
  reviewed_cherry_picks: optional list of {fix_sha, cherry_pick_sha, review}
    where review identifies an independent equivalence review. This command
    verifies ancestry, not semantic equivalence or the review's authenticity.

Observed JSON contains exactly source_sha and image_id, from operator-side
installed metadata. --served-asset is an independent capture of the served body,
not its reported hash or a file taken from the source checkout. Hashes are of
raw bytes, including line endings. Inputs must all describe one candidate.
The repository must contain the commits needed for ancestry checks; missing
objects hold acceptance. A reviewed cherry-pick never waives identity/hash checks.
Passing this comparison alone does not establish deployment or product readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from collections.abc import Callable


class IdentityMismatch(ValueError):
    """A closed error code; never include input contents in diagnostics."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise IdentityMismatch(code)


def _sha(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _build_identity(document: object, keys: set[str], optional: set[str]) -> dict:
    _require(isinstance(document, dict), "invalid_identity_document")
    _require(keys <= document.keys() <= keys | optional, "invalid_identity_fields")
    _require(_sha(document["source_sha"], 40), "invalid_source_sha")
    image = document["image_id"]
    _require(isinstance(image, str) and image.startswith("sha256:") and _sha(image[7:], 64), "invalid_image_id")
    return document


def verify_release_identity(
    expected: object,
    observed: object,
    served_asset: bytes,
    *,
    is_ancestor: Callable[[str, str], bool],
) -> dict[str, str]:
    """Return only closed metadata, or raise IdentityMismatch on any mismatch.

    is_ancestor must return a boolean from independently read Git ancestry and
    raise IdentityMismatch if it cannot verify it. It must not trust a list of
    fixes claimed by the installed metadata.
    """
    expected = _build_identity(
        expected,
        {"source_sha", "image_id", "asset_sha256", "required_fixes"},
        {"reviewed_cherry_picks"},
    )
    observed = _build_identity(observed, {"source_sha", "image_id"}, set())
    _require(_sha(expected["asset_sha256"], 64), "invalid_asset_sha256")
    fixes = expected["required_fixes"]
    _require(isinstance(fixes, list) and bool(fixes) and all(_sha(fix, 40) for fix in fixes), "invalid_required_fixes")
    _require(len(set(fixes)) == len(fixes), "duplicate_required_fix")
    reviews = expected.get("reviewed_cherry_picks", [])
    _require(isinstance(reviews, list), "invalid_cherry_pick_reviews")
    equivalents = {}
    for entry in reviews:
        _require(isinstance(entry, dict) and set(entry) == {"fix_sha", "cherry_pick_sha", "review"}, "invalid_cherry_pick_review")
        fix, replacement, review = entry["fix_sha"], entry["cherry_pick_sha"], entry["review"]
        _require(_sha(fix, 40) and _sha(replacement, 40), "invalid_cherry_pick_sha")
        _require(fix in fixes and fix != replacement and fix not in equivalents, "invalid_cherry_pick_binding")
        _require(isinstance(review, str) and bool(review.strip()) and len(review) <= 200, "missing_cherry_pick_review")
        equivalents[fix] = replacement

    _require(expected["source_sha"] == observed["source_sha"], "source_mismatch")
    _require(expected["image_id"] == observed["image_id"], "image_mismatch")
    _require(isinstance(served_asset, bytes) and bool(served_asset), "missing_served_asset")
    _require(hashlib.sha256(served_asset).hexdigest() == expected["asset_sha256"], "asset_mismatch")
    for fix in fixes:
        present = is_ancestor(fix, expected["source_sha"])
        _require(type(present) is bool, "ancestry_unverified")
        if not present and fix in equivalents:
            present = is_ancestor(equivalents[fix], expected["source_sha"])
            _require(type(present) is bool, "ancestry_unverified")
        _require(present, "required_fix_missing")
    return {"status": "passed", "evidence_scope": "offline_release_identity_comparison"}


def git_is_ancestor(repository: Path, ancestor: str, source: str) -> bool:
    """Read existing commit objects only; unavailable history is not a pass."""
    _require(_sha(ancestor, 40) and _sha(source, 40), "invalid_ancestry_sha")
    prefix = ["git", "-c", f"safe.directory={repository.resolve().as_posix()}", "-C", str(repository)]
    # Missing objects in a partial clone must not trigger Git's implicit fetch.
    environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        for commit in (ancestor, source):
            result = subprocess.run(prefix + ["cat-file", "-t", commit], capture_output=True, timeout=10, check=False, env=environment)
            _require(result.returncode == 0 and result.stdout.strip() == b"commit", "ancestry_unverified")
        result = subprocess.run(
            prefix + ["merge-base", "--is-ancestor", ancestor, source],
            capture_output=True, timeout=10, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise IdentityMismatch("ancestry_unverified") from None
    _require(result.returncode in (0, 1), "ancestry_unverified")
    return result.returncode == 0


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    document = {}
    for key, value in pairs:
        _require(key not in document, "duplicate_json_field")
        document[key] = value
    return document


def _read_json(path: Path) -> object:
    # These are tiny identity documents, never environment exports or app data.
    with path.open("rb") as handle:
        content = handle.read(65537)
    _require(len(content) <= 65536, "identity_document_too_large")
    return json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expected", required=True, type=Path, help="Trusted accepted-build JSON, independent of observed metadata.")
    parser.add_argument("--observed", required=True, type=Path, help="Captured operator-side installed source/image metadata JSON.")
    parser.add_argument("--served-asset", required=True, type=Path, help="Separately captured raw served app.js bytes.")
    parser.add_argument("--repository", required=True, type=Path, help="Local Git repository with required commit history; no fetch is performed.")
    args = parser.parse_args(argv)
    try:
        report = verify_release_identity(
            _read_json(args.expected), _read_json(args.observed), args.served_asset.read_bytes(),
            is_ancestor=lambda ancestor, source: git_is_ancestor(args.repository, ancestor, source),
        )
    except IdentityMismatch as error:
        report = {"status": "failed", "reason": str(error)}
    except (OSError, UnicodeError, ValueError, RecursionError):
        report = {"status": "failed", "reason": "identity_input_unreadable"}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
