"""Object-storage artifact backend contract for KQAG production readiness.

This module defines the provider-neutral contract only. It intentionally does
not configure AWS, GCP, Azure, R2, MinIO, or any credentialed backend.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Protocol


SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
ALLOWED_OWNER_TYPES = {
    "generated_quote",
    "uploaded_reference",
    "profile",
    "pricing_reference",
}


class ObjectStorageContractError(Exception):
    """Raised when the provider-neutral object storage contract is violated."""


@dataclass(frozen=True)
class ObjectArtifactMetadata:
    workspace_id: str
    owner_type: str
    owner_id: str
    artifact_kind: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    created_at: str
    updated_at: str

    def public_metadata(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("storage_key", None)
        return payload


class ObjectStorageBackend(Protocol):
    backend_name: str

    def store_artifact(
        self,
        *,
        workspace_id: str,
        owner_type: str,
        owner_id: str,
        artifact_kind: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> ObjectArtifactMetadata:
        ...

    def retrieve_artifact(self, metadata: ObjectArtifactMetadata, *, workspace_id: str) -> bytes:
        ...

    def delete_artifact(self, metadata: ObjectArtifactMetadata, *, workspace_id: str) -> bool:
        ...

    def verify_metadata(self, metadata: ObjectArtifactMetadata, *, workspace_id: str) -> bool:
        ...


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_segment(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    text = SAFE_SEGMENT_RE.sub("-", text).strip(".-_")
    return text[:120] if text else fallback


def normalize_owner_type(value: object) -> str:
    owner_type = safe_segment(value, "")
    if owner_type not in ALLOWED_OWNER_TYPES:
        raise ObjectStorageContractError("Artifact owner type is not supported.")
    return owner_type


def artifact_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def object_artifact_key(
    *,
    workspace_id: str,
    owner_type: str,
    owner_id: str,
    artifact_kind: str,
    filename: str,
    checksum_sha256: str,
) -> str:
    safe_workspace = safe_segment(workspace_id, "")
    safe_owner_type = normalize_owner_type(owner_type)
    safe_owner_id = safe_segment(owner_id, "")
    safe_kind = safe_segment(artifact_kind, "")
    safe_filename = safe_segment(filename, "artifact.bin")
    safe_checksum = checksum_sha256 if re.fullmatch(r"[a-f0-9]{64}", checksum_sha256) else ""
    if not all((safe_workspace, safe_owner_id, safe_kind, safe_checksum)):
        raise ObjectStorageContractError("Artifact metadata is incomplete.")
    return "/".join(
        (
            "workspaces",
            safe_workspace,
            safe_owner_type,
            safe_owner_id,
            safe_kind,
            f"{safe_checksum[:16]}-{safe_filename}",
        )
    )


class InMemoryObjectStorageBackend:
    """Synthetic backend for tests and readiness evidence only."""

    backend_name = "synthetic-in-memory"

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._metadata: dict[str, ObjectArtifactMetadata] = {}

    def store_artifact(
        self,
        *,
        workspace_id: str,
        owner_type: str,
        owner_id: str,
        artifact_kind: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> ObjectArtifactMetadata:
        if not content:
            raise ObjectStorageContractError("Artifact content is required.")
        checksum = artifact_checksum(content)
        key = object_artifact_key(
            workspace_id=workspace_id,
            owner_type=owner_type,
            owner_id=owner_id,
            artifact_kind=artifact_kind,
            filename=filename,
            checksum_sha256=checksum,
        )
        now = utc_timestamp()
        metadata = ObjectArtifactMetadata(
            workspace_id=safe_segment(workspace_id, ""),
            owner_type=normalize_owner_type(owner_type),
            owner_id=safe_segment(owner_id, ""),
            artifact_kind=safe_segment(artifact_kind, ""),
            filename=safe_segment(filename, "artifact.bin"),
            content_type=str(content_type or "application/octet-stream").strip() or "application/octet-stream",
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=key,
            created_at=now,
            updated_at=now,
        )
        self._objects[key] = bytes(content)
        self._metadata[key] = metadata
        return metadata

    def _require_workspace(self, metadata: ObjectArtifactMetadata, workspace_id: str) -> None:
        if metadata.workspace_id != safe_segment(workspace_id, ""):
            raise ObjectStorageContractError("Artifact is not available for this workspace.")

    def retrieve_artifact(self, metadata: ObjectArtifactMetadata, *, workspace_id: str) -> bytes:
        self._require_workspace(metadata, workspace_id)
        content = self._objects.get(metadata.storage_key)
        if content is None:
            raise ObjectStorageContractError("Artifact is not available.")
        if len(content) != metadata.size_bytes or artifact_checksum(content) != metadata.checksum_sha256:
            raise ObjectStorageContractError("Artifact integrity check failed.")
        return bytes(content)

    def delete_artifact(self, metadata: ObjectArtifactMetadata, *, workspace_id: str) -> bool:
        self._require_workspace(metadata, workspace_id)
        existed = metadata.storage_key in self._objects
        self._objects.pop(metadata.storage_key, None)
        self._metadata.pop(metadata.storage_key, None)
        return existed

    def verify_metadata(self, metadata: ObjectArtifactMetadata, *, workspace_id: str) -> bool:
        try:
            self.retrieve_artifact(metadata, workspace_id=workspace_id)
        except ObjectStorageContractError:
            return False
        return True
