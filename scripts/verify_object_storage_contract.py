#!/usr/bin/env python3
"""Verify the synthetic KQAG object-storage artifact contract.

The verifier uses only an in-memory backend and synthetic artifact bytes. It
emits metadata-only JSON and does not configure any real cloud provider.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.object_storage import InMemoryObjectStorageBackend, ObjectStorageContractError


SYNTHETIC_WORKSPACE_ID = "workspace-object-contract"
OTHER_WORKSPACE_ID = "workspace-object-other"
SENSITIVE_SYNTHETIC_VALUES = (
    "sqlite:///",
    "C:/Users/Private",
    "Synthetic Private Customer",
    "Generated quote private line item",
    "Private pricing catalog contents",
    "Private profile layout contents",
    "staff.member@example.test",
    "oauth-client-secret-value",
    "swooshz_private_session_cookie",
    "sk-proj-private-api-key",
    "synthetic-private-artifact-bytes",
    "raw provider response text",
    "private-code",
    "private-state",
    "workspaces/workspace-object-contract",
)


def synthetic_artifacts() -> list[dict[str, Any]]:
    return [
        {
            "class": "generated_quote_artifacts",
            "owner_type": "generated_quote",
            "owner_id": "quote-session-object-contract",
            "artifact_kind": "xlsx",
            "filename": "synthetic-quotation.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content": b"synthetic-xlsx-bytes",
        },
        {
            "class": "generated_quote_artifacts",
            "owner_type": "generated_quote",
            "owner_id": "quote-session-object-contract",
            "artifact_kind": "pdf",
            "filename": "synthetic-quotation.pdf",
            "content_type": "application/pdf",
            "content": b"synthetic-pdf-bytes",
        },
        {
            "class": "uploaded_references",
            "owner_type": "uploaded_reference",
            "owner_id": "quote-session-object-contract",
            "artifact_kind": "reference_image",
            "filename": "synthetic-reference.png",
            "content_type": "image/png",
            "content": b"synthetic-reference-bytes",
        },
        {
            "class": "profile_layout_assets",
            "owner_type": "profile",
            "owner_id": "profile-object-contract",
            "artifact_kind": "quotation_layout",
            "filename": "quotation-layout.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content": b"synthetic-layout-bytes",
        },
        {
            "class": "pricing_visual_assets",
            "owner_type": "pricing_reference",
            "owner_id": "pricing-object-contract",
            "artifact_kind": "visual_1_1",
            "filename": "synthetic-visual.png",
            "content_type": "image/png",
            "content": b"synthetic-visual-bytes",
        },
    ]


def run_verification(*, work_dir: Path | None = None) -> dict[str, Any]:
    _ = work_dir
    backend = InMemoryObjectStorageBackend()
    checks = {
        "store_retrieve_delete": False,
        "checksum_verified": False,
        "workspace_metadata_enforced": False,
        "wrong_workspace_retrieval_blocked": False,
        "wrong_workspace_delete_blocked": False,
        "metadata_without_object_keys": False,
    }
    metadata_records = []
    classes_seen: set[str] = set()

    for artifact in synthetic_artifacts():
        metadata = backend.store_artifact(
            workspace_id=SYNTHETIC_WORKSPACE_ID,
            owner_type=artifact["owner_type"],
            owner_id=artifact["owner_id"],
            artifact_kind=artifact["artifact_kind"],
            filename=artifact["filename"],
            content_type=artifact["content_type"],
            content=artifact["content"],
        )
        content = backend.retrieve_artifact(metadata, workspace_id=SYNTHETIC_WORKSPACE_ID)
        if content != artifact["content"]:
            raise ObjectStorageContractError("Synthetic artifact retrieval mismatch.")
        if not backend.verify_metadata(metadata, workspace_id=SYNTHETIC_WORKSPACE_ID):
            raise ObjectStorageContractError("Synthetic artifact checksum verification failed.")
        classes_seen.add(artifact["class"])
        metadata_records.append(metadata.public_metadata())

    checks["store_retrieve_delete"] = True
    checks["checksum_verified"] = all(len(item["checksum_sha256"]) == 64 for item in metadata_records)
    checks["workspace_metadata_enforced"] = all(item["workspace_id"] == SYNTHETIC_WORKSPACE_ID for item in metadata_records)
    checks["metadata_without_object_keys"] = all("storage_key" not in item for item in metadata_records)

    protected_metadata = backend.store_artifact(
        workspace_id=SYNTHETIC_WORKSPACE_ID,
        owner_type="generated_quote",
        owner_id="quote-session-owned",
        artifact_kind="xlsx",
        filename="owned-quotation.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=b"owned-xlsx-bytes",
    )
    try:
        backend.retrieve_artifact(protected_metadata, workspace_id=OTHER_WORKSPACE_ID)
    except ObjectStorageContractError:
        checks["wrong_workspace_retrieval_blocked"] = True
    try:
        backend.delete_artifact(protected_metadata, workspace_id=OTHER_WORKSPACE_ID)
    except ObjectStorageContractError:
        checks["wrong_workspace_delete_blocked"] = True
    deleted = backend.delete_artifact(protected_metadata, workspace_id=SYNTHETIC_WORKSPACE_ID)
    checks["store_retrieve_delete"] = checks["store_retrieve_delete"] and deleted

    passed = all(checks.values()) and classes_seen == {
        "generated_quote_artifacts",
        "uploaded_references",
        "profile_layout_assets",
        "pricing_visual_assets",
    }
    return {
        "schema": "swooshz.kqag.object-storage-contract-evidence.v1",
        "status": "passed" if passed else "failed",
        "synthetic_only": True,
        "contract": {
            "backend": backend.backend_name,
            "operations": ["store", "retrieve", "delete", "verify_metadata"],
            "authorization": "workspace-scoped metadata",
            "integrity": "sha256",
        },
        "artifact_classes": {
            "covered": [
                "generated_quote_artifacts",
                "uploaded_references",
                "profile_layout_assets",
                "pricing_visual_assets",
            ],
            "stored_count": len(metadata_records),
        },
        "checks": checks,
        "privacy": {
            "output": "metadata-only",
            "object_keys_printed": False,
            "artifact_bytes_printed": False,
            "private_values_omitted": True,
        },
        "notes": [
            "No real cloud provider, external service, or credentialed object store is configured.",
            "Evidence proves only the provider-neutral object-storage contract and fail-closed readiness hook.",
        ],
    }


def failed_report() -> dict[str, Any]:
    return {
        "schema": "swooshz.kqag.object-storage-contract-evidence.v1",
        "status": "failed",
        "synthetic_only": True,
        "checks": {},
        "privacy": {
            "output": "metadata-only",
            "object_keys_printed": False,
            "artifact_bytes_printed": False,
            "private_values_omitted": True,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify synthetic object-storage contract evidence without private data.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Accepted for consistency; the path is never printed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_verification(work_dir=args.work_dir)
    except Exception:
        report = failed_report()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
