#!/usr/bin/env python3
"""Opt-in live S3-compatible object-storage evidence for generated artifacts.

The verifier only runs against a real provider when explicit live-evidence
environment variables are present. Output is metadata-only: no endpoint,
bucket, object key, credential, artifact byte, DB URL, private path, or tenant
payload values are printed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.object_storage import (
    OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME,
    OBJECT_STORAGE_BUCKET_ENV_NAME,
    OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME,
    OBJECT_STORAGE_PROVIDER_ENV_NAME,
    OBJECT_STORAGE_REGION_ENV_NAME,
    OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME,
    ObjectStorageBackend,
    ObjectStorageConfigurationError,
    ObjectStorageContractError,
    S3CompatibleObjectStorageBackend,
    object_storage_provider_status,
)


LIVE_OBJECT_STORAGE_EVIDENCE_ENV_NAME = "KQAG_LIVE_OBJECT_STORAGE_EVIDENCE"
REQUIRED_ENV_NAMES = [
    LIVE_OBJECT_STORAGE_EVIDENCE_ENV_NAME,
    OBJECT_STORAGE_PROVIDER_ENV_NAME,
    OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME,
    OBJECT_STORAGE_BUCKET_ENV_NAME,
    OBJECT_STORAGE_REGION_ENV_NAME,
    OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME,
    OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME,
]
TRUE_VALUES = {"1", "true", "yes", "on", "run", "enabled"}
WORKSPACE_ID = "workspace-live-provider-evidence"
OTHER_WORKSPACE_ID = "workspace-live-provider-other"
SYNTHETIC_ARTIFACTS = [
    {
        "artifact_kind": "xlsx",
        "filename": "live-provider-synthetic-quotation.xlsx",
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content": b"synthetic-live-provider-xlsx-bytes",
    },
    {
        "artifact_kind": "pdf",
        "filename": "live-provider-synthetic-quotation.pdf",
        "content_type": "application/pdf",
        "content": b"synthetic-live-provider-pdf-bytes",
    },
]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _live_opt_in_enabled(env: Mapping[str, str]) -> bool:
    return _clean(env.get(LIVE_OBJECT_STORAGE_EVIDENCE_ENV_NAME)).lower() in TRUE_VALUES


def _missing_env_names(env: Mapping[str, str], provider_status: Mapping[str, Any]) -> list[str]:
    missing = []
    if not _live_opt_in_enabled(env):
        missing.append(LIVE_OBJECT_STORAGE_EVIDENCE_ENV_NAME)
    if not _clean(env.get(OBJECT_STORAGE_PROVIDER_ENV_NAME)):
        missing.append(OBJECT_STORAGE_PROVIDER_ENV_NAME)
    for name in provider_status.get("missing_fields") or []:
        if name not in missing:
            missing.append(str(name))
    return missing


def build_s3_backend_for_test(*, bucket: str, client: object) -> S3CompatibleObjectStorageBackend:
    return S3CompatibleObjectStorageBackend(bucket=bucket, client=client)


def _build_live_s3_backend(env: Mapping[str, str], provider_status: Mapping[str, Any]) -> S3CompatibleObjectStorageBackend:
    if provider_status.get("provider") != "s3_compatible" or not provider_status.get("configured"):
        raise ObjectStorageConfigurationError("Object storage backend is not available.")
    try:
        boto3 = importlib.import_module("boto3")
        client = boto3.client(
            "s3",
            endpoint_url=env.get(OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME),
            region_name=env.get(OBJECT_STORAGE_REGION_ENV_NAME),
            aws_access_key_id=env.get(OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME),
            aws_secret_access_key=env.get(OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME),
        )
    except Exception as exc:
        raise ObjectStorageConfigurationError("Object storage backend is not available.") from exc
    return S3CompatibleObjectStorageBackend(
        bucket=_clean(env.get(OBJECT_STORAGE_BUCKET_ENV_NAME)),
        client=client,
        status=provider_status,
    )


def _empty_checks() -> dict[str, bool]:
    return {
        "store": False,
        "retrieve": False,
        "checksum": False,
        "content_type": False,
        "byte_size": False,
        "wrong_workspace_denied": False,
        "delete": False,
        "tombstone": False,
        "missing_object_failed_closed": False,
    }


def _provider_blockers(provider_status: Mapping[str, Any], *, live_provider_evidence_supported: bool) -> list[str]:
    blockers = [str(item) for item in provider_status.get("blockers") or []]
    if live_provider_evidence_supported:
        return [item for item in blockers if item != "live_provider_evidence_missing"]
    return blockers


def _metadata_report(provider_status: Mapping[str, Any], *, status: str, checks: Mapping[str, bool], missing_env_names: list[str], test_injected_backend: bool = False) -> dict[str, Any]:
    supported = bool(status == "passed" and not test_injected_backend)
    return {
        "schema": "swooshz.kqag.live-object-storage-provider-evidence.v1",
        "status": status,
        "live_provider_evidence_supported": supported,
        "test_injected_backend": bool(test_injected_backend),
        "provider": {
            "family": provider_status.get("provider", "disabled"),
            "configured": bool(provider_status.get("configured")),
            "runtime_backend_available": bool(provider_status.get("runtime_backend_available")),
            "synthetic_only": bool(provider_status.get("synthetic_only")),
            "blockers": _provider_blockers(provider_status, live_provider_evidence_supported=supported),
        },
        "checks": dict(checks),
        "required_env_names": list(REQUIRED_ENV_NAMES),
        "missing_env_names": list(missing_env_names),
        "privacy": {
            "output": "metadata-only",
            "provider_values_printed": False,
            "object_keys_printed": False,
            "artifact_bytes_printed": False,
            "private_values_omitted": True,
        },
        "notes": [
            "This verifier uses safe synthetic generated XLSX/PDF bytes.",
            "Provider values, object keys, credentials, artifact bytes, DB URLs, private paths, and tenant data are not reported.",
            "A test-injected backend exercises verifier logic only and is not live-provider evidence.",
        ],
    }


def _head_checks(backend: ObjectStorageBackend, metadata: Any) -> tuple[bool, bool]:
    client = getattr(backend, "client", None)
    bucket = getattr(backend, "bucket", "")
    if client is None or not bucket:
        return False, False
    response = client.head_object(Bucket=bucket, Key=metadata.storage_key)
    content_type_ok = _clean(response.get("ContentType")) == metadata.content_type
    byte_size_ok = int(response.get("ContentLength") or 0) == metadata.size_bytes
    return content_type_ok, byte_size_ok


def _exercise_backend(backend: ObjectStorageBackend) -> dict[str, bool]:
    checks = _empty_checks()
    stored_metadata = []
    store_ok = True
    retrieve_ok = True
    checksum_ok = True
    content_type_ok = True
    byte_size_ok = True
    for artifact in SYNTHETIC_ARTIFACTS:
        metadata = backend.store_artifact(
            workspace_id=WORKSPACE_ID,
            owner_type="generated_quote",
            owner_id="quote-session-live-provider-evidence",
            artifact_kind=artifact["artifact_kind"],
            filename=artifact["filename"],
            content_type=artifact["content_type"],
            content=artifact["content"],
        )
        stored_metadata.append(metadata)
        retrieved = backend.retrieve_artifact(metadata, workspace_id=WORKSPACE_ID)
        artifact_content_type_ok, artifact_byte_size_ok = _head_checks(backend, metadata)
        store_ok = store_ok and bool(metadata.storage_key)
        retrieve_ok = retrieve_ok and retrieved == artifact["content"]
        checksum_ok = checksum_ok and len(metadata.checksum_sha256) == 64
        content_type_ok = content_type_ok and artifact_content_type_ok
        byte_size_ok = byte_size_ok and artifact_byte_size_ok

    checks["store"] = store_ok
    checks["retrieve"] = retrieve_ok
    checks["checksum"] = checksum_ok
    checks["content_type"] = content_type_ok
    checks["byte_size"] = byte_size_ok

    protected = stored_metadata[0]
    try:
        backend.retrieve_artifact(protected, workspace_id=OTHER_WORKSPACE_ID)
    except ObjectStorageContractError:
        checks["wrong_workspace_denied"] = True

    deleted = backend.delete_artifact(protected, workspace_id=WORKSPACE_ID)
    checks["delete"] = bool(deleted)
    checks["tombstone"] = bool(deleted)
    try:
        backend.retrieve_artifact(protected, workspace_id=WORKSPACE_ID)
    except ObjectStorageContractError:
        checks["missing_object_failed_closed"] = True

    # Leave no synthetic PDF object behind after a successful verifier run.
    if len(stored_metadata) > 1:
        backend.delete_artifact(stored_metadata[1], workspace_id=WORKSPACE_ID)
    return checks


def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    backend_factory: Callable[[Mapping[str, str]], ObjectStorageBackend] | None = None,
) -> dict[str, Any]:
    current_env = dict(os.environ if env is None else env)
    provider_status = object_storage_provider_status(current_env)
    missing_env_names = _missing_env_names(current_env, provider_status)
    if missing_env_names or provider_status.get("provider") != "s3_compatible":
        return _metadata_report(provider_status, status="failed", checks=_empty_checks(), missing_env_names=missing_env_names)
    if not provider_status.get("runtime_backend_available") and backend_factory is None:
        return _metadata_report(provider_status, status="failed", checks=_empty_checks(), missing_env_names=missing_env_names)

    test_injected_backend = backend_factory is not None
    try:
        backend = backend_factory(current_env) if backend_factory else _build_live_s3_backend(current_env, provider_status)
        checks = _exercise_backend(backend)
    except Exception:
        return _metadata_report(
            provider_status,
            status="failed",
            checks=_empty_checks(),
            missing_env_names=missing_env_names,
            test_injected_backend=test_injected_backend,
        )
    return _metadata_report(
        provider_status,
        status="passed" if all(checks.values()) else "failed",
        checks=checks,
        missing_env_names=missing_env_names,
        test_injected_backend=test_injected_backend,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Verify live S3-compatible object-storage evidence with metadata-only output.")


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = run_verification()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report.get("status") == "passed" and report.get("live_provider_evidence_supported") else 1


if __name__ == "__main__":
    raise SystemExit(main())
