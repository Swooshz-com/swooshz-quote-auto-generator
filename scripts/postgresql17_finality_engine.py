"""Shared PostgreSQL 17 finality engine for the SQAG A23 proof.

The engine deliberately separates three concerns:

* live catalogue discovery and typed observation execution;
* semantic identity/reference resolution and executable field policies; and
* public-safe comparison receipts.

Synthetic tests use the same ``Observation``/``PolicyRegistry``/comparator
interfaces as the live collector.  They never provide an alternate expected
schema or a second comparison implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ENGINE_VERSION = "a23-g3-real-reference-v1"
POSTGRES_MAJOR = 17
POLICY_CLASSES = (
    "exact_semantic_value",
    "stable_normalized_identity_reference_edge",
    "canonicalized_collection_set_order",
    "postgresql_maintained_dynamic_estimate_maintenance_state",
    "provider_managed_normalized_state",
    "secret_redacted_shape_capability_state",
    "explicit_deferred_external_boundary",
)

CATALOGUE_SCHEMAS = ("pg_catalog",)
CATALOGUE_RELKINDS = ("r", "t", "p")
OID_TYPE_MARKERS = (
    "oid",
    "regclass",
    "regcollation",
    "regconfig",
    "regdictionary",
    "regnamespace",
    "regoper",
    "regoperator",
    "regproc",
    "regprocedure",
    "regrole",
    "regtype",
)
REFERENCE_FIELD_SUFFIXES = (
    "relid",
    "namespace",
    "owner",
    "roleid",
    "foid",
    "procoid",
    "typeid",
    "typid",
    "basetype",
    "elem",
    "indexrelid",
    "indrelid",
    "conrelid",
    "confrelid",
    "conindid",
    "contypid",
    "amopfamily",
    "amopopr",
    "amproc",
    "opclass",
    "collation",
    "classid",
    "refclassid",
    "classoid",
    "refclassoid",
    "objid",
    "refobjid",
    "objoid",
    "refobjoid",
)
AUTHORITY_CATALOGUE_MARKERS = (
    "auth",
    "acl",
    "default_acl",
    "parameter_acl",
    "role_setting",
    "publication",
    "trigger",
    "policy",
)
SECRET_FIELD_MARKERS = (
    "password",
    "secret",
    "token",
    "private_key",
    "key_material",
)
DYNAMIC_FIELD_MARKERS = (
    "page",
    "pages",
    "tupl",
    "visible",
    "frozenxid",
    "minmxid",
    "vacuum",
    "analy",
    "estimate",
    "counter",
    "scan",
    "blks",
    "blocks",
    "dead",
    "live",
    "mod_since",
    "stats",
    "reset",
    "backend_start",
    "xact_start",
    "query_start",
    "state_change",
    "wal_",
    "stadistinct",
    "stanullfrac",
    "stawidth",
    "stakind",
    "staop",
    "stacoll",
    "stanumbers",
    "stavalues",
    "stxdndistinct",
    "stxddependencies",
    "stxdmcv",
    "stxdexpr",
)
PROVIDER_CATALOGUE_MARKERS = (
    "replication",
    "subscription",
    "foreign",
    "statistic_ext",
    "file_node",
)


class FinalityError(RuntimeError):
    """Fail-closed A23 error with a stable public-safe category."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = code if not detail else f"{code}:{detail}"
        super().__init__(message)


class UnresolvedReferenceError(FinalityError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("unresolved_nonzero_oid", detail)


class AmbiguousReferenceError(FinalityError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("ambiguous_nonzero_oid", detail)


def _canonical(value: Any) -> Any:
    """Convert driver values to deterministic JSON-safe values."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return {"byte_length": len(value), "sha256": hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_canonical(item) for item in value]
    return str(value)


def _digest(value: Any) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null", "present": False}
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return {"type": "bytes", "present": True, "length": len(value)}
    if isinstance(value, str):
        return {"type": "text", "present": True, "length": len(value)}
    if isinstance(value, Mapping):
        return {"type": "object", "present": True, "keys": len(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"type": "collection", "present": True, "items": len(value)}
    return {"type": type(value).__name__, "present": True}


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
        raise FinalityError("unsafe_catalogue_identifier")
    return '"' + value.replace('"', '""') + '"'


def _type_is_oid(type_name: str) -> bool:
    lowered = type_name.lower()
    return any(marker in lowered for marker in OID_TYPE_MARKERS)


def _field_is_reference(field_name: str, type_name: str) -> bool:
    lowered = field_name.lower()
    if not _type_is_oid(type_name):
        return False
    if lowered == "oid":
        return True
    return lowered.endswith(REFERENCE_FIELD_SUFFIXES) or lowered in {"classid", "refclassid", "objid", "refobjid"}


@dataclass(frozen=True)
class FieldDescriptor:
    schema: str
    catalogue: str
    relation_kind: str
    field_name: str
    postgres_type: str
    typmod: int | None = None
    collation: str | None = None
    attnum: int | None = None
    not_null: bool = False
    storage: str | None = None
    generated: str | None = None

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.catalogue}.{self.field_name}"

    @property
    def authority_class(self) -> str:
        material = f"{self.schema}.{self.catalogue}.{self.field_name}".lower()
        if any(marker in material for marker in SECRET_FIELD_MARKERS):
            return "secret"
        if any(marker in material for marker in AUTHORITY_CATALOGUE_MARKERS):
            return "authority"
        if "publication" in material:
            return "publication_membership"
        if "trigger" in material:
            return "trigger_dependency"
        if "constraint" in material or "depend" in material:
            return "dependency"
        if self.schema == "pg_toast":
            return "toast_system"
        if self.schema == "pg_catalog" and self.catalogue.startswith("pg_stat"):
            return "provider_system"
        return "semantic"


@dataclass(frozen=True)
class FieldPolicy:
    field_key: str
    policy_id: str
    policy_class: str
    executable: bool = True

    def __post_init__(self) -> None:
        if self.policy_class not in POLICY_CLASSES:
            raise FinalityError("unknown_policy_class", self.policy_class)
        if not self.executable:
            raise FinalityError("unexecutable_policy", self.field_key)


def classify_field_policy(descriptor: FieldDescriptor) -> FieldPolicy:
    """Derive exactly one policy from live field metadata and generic semantics."""

    field_name = descriptor.field_name.lower()
    material = f"{descriptor.schema}.{descriptor.catalogue}.{field_name}"
    if any(marker in material for marker in SECRET_FIELD_MARKERS):
        policy_class = "secret_redacted_shape_capability_state"
    elif _field_is_reference(field_name, descriptor.postgres_type):
        policy_class = "stable_normalized_identity_reference_edge"
    elif any(token in field_name for token in ("acl", "options", "key", "vector", "array")) or descriptor.postgres_type.endswith("[]"):
        policy_class = "canonicalized_collection_set_order"
    elif any(marker in field_name for marker in DYNAMIC_FIELD_MARKERS) and descriptor.postgres_type.lower() not in {"bool", "boolean"}:
        policy_class = "postgresql_maintained_dynamic_estimate_maintenance_state"
    elif any(marker in material for marker in PROVIDER_CATALOGUE_MARKERS):
        policy_class = "provider_managed_normalized_state"
    elif descriptor.schema == "pg_toast" and descriptor.catalogue.startswith("pg_toast"):
        policy_class = "explicit_deferred_external_boundary"
    else:
        policy_class = "exact_semantic_value"
    policy_id = f"a23.{policy_class}.v1"
    return FieldPolicy(descriptor.key, policy_id, policy_class)


class PolicyRegistry:
    """Bidirectional executable field-policy registry."""

    def __init__(self, descriptors: Iterable[FieldDescriptor] = ()) -> None:
        self._descriptors: dict[str, FieldDescriptor] = {}
        self._policies: dict[str, FieldPolicy] = {}
        for descriptor in descriptors:
            self.register(descriptor, classify_field_policy(descriptor))

    def register(self, descriptor: FieldDescriptor, policy: FieldPolicy) -> None:
        if descriptor.key in self._descriptors or descriptor.key in self._policies:
            raise FinalityError("duplicate_policy", descriptor.key)
        if policy.field_key != descriptor.key:
            raise FinalityError("ambiguous_policy", descriptor.key)
        if not policy.executable:
            raise FinalityError("unexecutable_policy", descriptor.key)
        self._descriptors[descriptor.key] = descriptor
        self._policies[descriptor.key] = policy

    def policy_for(self, field_key: str) -> FieldPolicy:
        try:
            return self._policies[field_key]
        except KeyError as exc:
            raise FinalityError("missing_policy", field_key) from exc

    def descriptor_for(self, field_key: str) -> FieldDescriptor:
        try:
            return self._descriptors[field_key]
        except KeyError as exc:
            raise FinalityError("unknown_field", field_key) from exc

    @property
    def field_keys(self) -> frozenset[str]:
        return frozenset(self._policies)

    @property
    def size(self) -> int:
        return len(self._policies)

    def public_dict(self) -> dict[str, Any]:
        return {
            key: {
                "policy_id": self._policies[key].policy_id,
                "policy_class": self._policies[key].policy_class,
                "schema": self._descriptors[key].schema,
                "catalogue": self._descriptors[key].catalogue,
                "relation_kind": self._descriptors[key].relation_kind,
                "field_name": self._descriptors[key].field_name,
                "postgres_type": self._descriptors[key].postgres_type,
                "typmod": self._descriptors[key].typmod,
                "collation": self._descriptors[key].collation,
                "attnum": self._descriptors[key].attnum,
                "not_null": self._descriptors[key].not_null,
                "storage": self._descriptors[key].storage,
                "generated": self._descriptors[key].generated,
            }
            for key in sorted(self._policies)
        }

    @property
    def digest(self) -> str:
        return _digest(self.public_dict())


@dataclass(frozen=True)
class Observation:
    reference_id: str
    field_key: str
    row_identity: str
    object_kind: str
    postgres_type: str
    typmod: int | None
    collation: str | None
    presence: bool
    applicability: str
    policy_id: str
    policy_class: str
    value_digest: str
    raw_shape: Mapping[str, Any]
    raw_digest: str
    normalized_edge: str | None = None
    capability: str = "semantic"
    classification: str = "semantic"
    boundary: bool = False
    attnum: int | None = None
    not_null: bool = False
    storage: str | None = None
    generated: str | None = None

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return self.field_key, self.row_identity, self.object_kind

    @classmethod
    def from_value(
        cls,
        *,
        reference_id: str,
        descriptor: FieldDescriptor,
        policy: FieldPolicy,
        row_identity: str,
        object_kind: str,
        value: Any,
        value_digest: str | None = None,
        raw_digest: str | None = None,
        normalized_edge: str | None = None,
        applicability: str = "applicable",
        boundary: bool = False,
    ) -> "Observation":
        present = value is not None and not boundary
        safe = _safe_shape(value)
        return cls(
            reference_id=reference_id,
            field_key=descriptor.key,
            row_identity=row_identity,
            object_kind=object_kind,
            postgres_type=descriptor.postgres_type,
            typmod=descriptor.typmod,
            collation=descriptor.collation,
            presence=present,
            applicability=applicability,
            policy_id=policy.policy_id,
            policy_class=policy.policy_class,
            value_digest=value_digest or _digest(value),
            raw_shape=safe,
            raw_digest=raw_digest or _digest(value),
            normalized_edge=normalized_edge,
            capability=descriptor.authority_class,
            classification=descriptor.authority_class,
            boundary=boundary,
            attnum=descriptor.attnum,
            not_null=descriptor.not_null,
            storage=descriptor.storage,
            generated=descriptor.generated,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "field": self.field_key,
            "row_identity": self.row_identity,
            "object_kind": self.object_kind,
            "postgres_type": self.postgres_type,
            "typmod": self.typmod,
            "collation": self.collation,
            "presence": self.presence,
            "applicability": self.applicability,
            "policy_id": self.policy_id,
            "policy_class": self.policy_class,
            "value_digest": self.value_digest,
            "raw_shape": dict(self.raw_shape),
            "normalized_edge": self.normalized_edge,
            "capability": self.capability,
            "classification": self.classification,
            "boundary": self.boundary,
            "attnum": self.attnum,
            "not_null": self.not_null,
            "storage": self.storage,
            "generated": self.generated,
        }


@dataclass(frozen=True)
class ExecutionTrace:
    executed_fields: frozenset[str]
    field_values: int
    live_connection: bool
    synthetic: bool

    @classmethod
    def live(cls, executed_fields: Iterable[str], field_values: int) -> "ExecutionTrace":
        return cls(frozenset(executed_fields), int(field_values), True, False)

    @classmethod
    def synthetic(cls, executed_fields: Iterable[str], field_values: int) -> "ExecutionTrace":
        return cls(frozenset(executed_fields), int(field_values), False, True)


@dataclass(frozen=True)
class FinalitySnapshot:
    reference_id: str
    observations: tuple[Observation, ...]
    trace: ExecutionTrace
    coverage_universe_digest: str
    policy_registry_digest: str
    semantic_node_digest: str
    relationship_digest: str
    authority_digest: str
    provider_boundary_digest: str
    dynamic_variances: tuple[dict[str, Any], ...] = ()
    boundary_receipts: tuple[dict[str, Any], ...] = ()

    @classmethod
    def build(
        cls,
        *,
        reference_id: str,
        observations: Sequence[Observation],
        registry: PolicyRegistry,
        trace: ExecutionTrace,
        boundary_receipts: Sequence[Mapping[str, Any]] = (),
    ) -> "FinalitySnapshot":
        if not observations:
            raise FinalityError("empty_observation_set")
        if not trace.executed_fields:
            raise FinalityError("real_collector_returned_zero_executed_fields")
        if trace.field_values <= 0:
            raise FinalityError("real_collector_returned_zero_field_values")
        normalized_boundaries: list[dict[str, Any]] = []
        for boundary in boundary_receipts:
            if not isinstance(boundary, Mapping):
                raise FinalityError("invalid_authority_boundary_receipt")
            kind = boundary.get("kind")
            identity = boundary.get("identity")
            digest = boundary.get("digest")
            if not all(isinstance(item, str) and item for item in (kind, identity, digest)):
                raise FinalityError("invalid_authority_boundary_receipt")
            normalized_boundaries.append({"kind": kind, "identity": identity, "digest": digest})
        normalized_boundaries.sort(key=lambda item: (item["kind"], item["identity"]))

        seen: set[tuple[str, str, str]] = set()
        observed_fields: set[str] = set()
        for observation in observations:
            if observation.reference_id != reference_id:
                raise FinalityError("reference_provenance_mismatch")
            if observation.identity_key in seen:
                raise FinalityError("duplicate_observation", observation.field_key)
            seen.add(observation.identity_key)
            observed_fields.add(observation.field_key)
            policy = registry.policy_for(observation.field_key)
            if policy.policy_id != observation.policy_id or policy.policy_class != observation.policy_class:
                raise FinalityError("ambiguous_policy", observation.field_key)
            descriptor = registry.descriptor_for(observation.field_key)
            if descriptor.postgres_type != observation.postgres_type:
                raise FinalityError("field_type_drift", observation.field_key)
            if descriptor.typmod != observation.typmod or descriptor.collation != observation.collation:
                raise FinalityError("field_metadata_drift", observation.field_key)
            if (
                descriptor.attnum != observation.attnum
                or descriptor.not_null != observation.not_null
                or descriptor.storage != observation.storage
                or descriptor.generated != observation.generated
            ):
                raise FinalityError("field_metadata_drift", observation.field_key)
        missing_execution = registry.field_keys - trace.executed_fields
        if missing_execution:
            raise FinalityError("listed_field_not_executed", sorted(missing_execution)[0])
        missing_observation = registry.field_keys - observed_fields
        if missing_observation:
            raise FinalityError("observed_field_missing", sorted(missing_observation)[0])
        extra_fields = observed_fields - registry.field_keys
        if extra_fields:
            raise FinalityError("observed_unregistered_field", sorted(extra_fields)[0])

        ordered = sorted(observations, key=lambda item: item.identity_key)
        coverage = [
            (item.field_key, item.row_identity, item.object_kind, item.presence, item.applicability)
            for item in ordered
        ]
        semantic = [
            (
                item.field_key,
                item.row_identity,
                item.object_kind,
                item.postgres_type,
                item.typmod,
                item.collation,
                item.presence,
                item.applicability,
                item.policy_id,
                item.capability,
                item.classification,
                item.value_digest if item.policy_class != "postgresql_maintained_dynamic_estimate_maintenance_state" else "<dynamic>",
            )
            for item in ordered
        ]
        relationships = [
            (item.field_key, item.row_identity, item.normalized_edge, item.value_digest)
            for item in ordered
            if item.policy_class == "stable_normalized_identity_reference_edge" or item.normalized_edge
        ]
        authority = [
            (item.field_key, item.row_identity, item.capability, item.classification, item.value_digest)
            for item in ordered
            if item.capability != "semantic"
        ]
        authority.extend(
            ("boundary", item["kind"], item["identity"], item["digest"])
            for item in normalized_boundaries
        )
        provider_boundary = [
            (item.field_key, item.row_identity, item.policy_class, item.presence, item.applicability, item.value_digest)
            for item in ordered
            if item.policy_class in {"provider_managed_normalized_state", "secret_redacted_shape_capability_state", "explicit_deferred_external_boundary"}
        ]
        provider_boundary.extend(
            ("boundary", item["kind"], item["identity"], item["digest"])
            for item in normalized_boundaries
        )
        dynamic_variances = tuple(
            {
                "field": item.field_key,
                "row_identity": item.row_identity,
                "raw_shape": dict(item.raw_shape),
                "raw_digest": item.raw_digest,
            }
            for item in ordered
            if item.policy_class == "postgresql_maintained_dynamic_estimate_maintenance_state"
        )
        return cls(
            reference_id=reference_id,
            observations=tuple(ordered),
            trace=trace,
            coverage_universe_digest=_digest(coverage),
            policy_registry_digest=registry.digest,
            semantic_node_digest=_digest(semantic),
            relationship_digest=_digest(relationships),
            authority_digest=_digest(authority),
            provider_boundary_digest=_digest(provider_boundary),
            dynamic_variances=dynamic_variances,
            boundary_receipts=tuple(normalized_boundaries),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "coverage_universe_digest": self.coverage_universe_digest,
            "policy_registry_digest": self.policy_registry_digest,
            "semantic_node_digest": self.semantic_node_digest,
            "relationship_digest": self.relationship_digest,
            "authority_digest": self.authority_digest,
            "provider_boundary_digest": self.provider_boundary_digest,
            "executed_fields_count": len(self.trace.executed_fields),
            "field_values_count": self.trace.field_values,
            "dynamic_variance_count": len(self.dynamic_variances),
            "boundary_receipt_count": len(self.boundary_receipts),
            "synthetic": self.trace.synthetic,
            "real_connection": self.trace.live_connection,
        }


@dataclass(frozen=True)
class ReferenceReceipt:
    reference_id: str
    collector_mode: str
    synthetic: bool
    real_connection: bool
    executed_fields_count: int
    field_values_count: int
    reference_independence_verified: bool
    cleanup_verified: bool
    postgres_major: int
    cluster_system_identity: str
    container_identity: str
    volume_identity: str
    database_name: str
    migration_manifest_digest: str
    migration_checksums: tuple[tuple[str, str], ...]
    exact_replay_verified: bool
    maintenance_executed: bool = False
    maintenance_variance_witness_count: int = 0
    semantic_residue_free: bool = True

    def validate_live(self) -> None:
        if self.collector_mode != "live_postgresql17":
            raise FinalityError("collector_mode_not_live", self.reference_id)
        if self.synthetic:
            raise FinalityError("synthetic_receipt_cannot_satisfy_real_gate", self.reference_id)
        if not self.real_connection:
            raise FinalityError("real_connection_not_verified", self.reference_id)
        if self.executed_fields_count <= 0:
            raise FinalityError("executed_fields_count_zero", self.reference_id)
        if self.field_values_count <= 0:
            raise FinalityError("field_values_count_zero", self.reference_id)
        if not self.reference_independence_verified:
            raise FinalityError("reference_independence_not_verified", self.reference_id)
        if not self.cleanup_verified:
            raise FinalityError("cleanup_not_verified", self.reference_id)
        if self.postgres_major != POSTGRES_MAJOR:
            raise FinalityError("postgres_major_mismatch", self.reference_id)
        if not self.exact_replay_verified:
            raise FinalityError("exact_replay_not_verified", self.reference_id)
        if not self.semantic_residue_free:
            raise FinalityError("maintenance_semantic_residue", self.reference_id)

    def public_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "collector_mode": self.collector_mode,
            "synthetic": self.synthetic,
            "real_connection": self.real_connection,
            "executed_fields_count": self.executed_fields_count,
            "field_values_count": self.field_values_count,
            "reference_independence_verified": self.reference_independence_verified,
            "cleanup_verified": self.cleanup_verified,
            "postgres_major": self.postgres_major,
            "cluster_system_identity": self.cluster_system_identity,
            "container_identity": self.container_identity,
            "volume_identity": self.volume_identity,
            "database_name": self.database_name,
            "migration_manifest_digest": self.migration_manifest_digest,
            "migration_checksums": list(self.migration_checksums),
            "exact_replay_verified": self.exact_replay_verified,
            "maintenance_executed": self.maintenance_executed,
            "maintenance_variance_witness_count": self.maintenance_variance_witness_count,
            "semantic_residue_free": self.semantic_residue_free,
        }


def _row_identity_seed(
    schema: str,
    catalogue: str,
    row: Mapping[str, Any],
    *,
    stable_oids: Mapping[tuple[str, int], str] | None = None,
    relation_oids: Mapping[int, str] | None = None,
) -> str:
    """Return a stable natural seed; raw OIDs are resolver inputs only."""

    stable_oids = stable_oids or {}
    relation_oids = relation_oids or {}

    def integer(value: Any) -> int | None:
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None

    def reference(target_catalogue: str, value: Any) -> str:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return "<null>"
            if text in {"-", "0"}:
                return "<zero>"
            return f"symbol:{target_catalogue}:{text}"
        raw = integer(value)
        if raw is None:
            return "<null>"
        if raw == 0:
            return "<zero>"
        return stable_oids.get((target_catalogue, raw), f"<unresolved:{target_catalogue}>")

    def class_catalogue(value: Any) -> str:
        raw = integer(value)
        return relation_oids.get(raw, "<unknown_catalogue>") if raw is not None else "<unknown_catalogue>"

    def dependency_object(class_value: Any, object_value: Any) -> str:
        return reference(class_catalogue(class_value), object_value)

    def type_vector(value: Any) -> str:
        if value is None:
            return "<null>"
        if isinstance(value, (list, tuple)):
            return ",".join(reference("pg_type", item) for item in value)
        tokens = str(value).replace("{", "").replace("}", "").replace(",", " ").split()
        if not tokens:
            return "<empty>"
        result: list[str] = []
        for token in tokens:
            raw = integer(token)
            result.append(reference("pg_type", raw) if raw is not None else "<type_token>")
        return ",".join(result)

    def target_catalogue(field_name: str, value: Any) -> str:
        lowered = field_name.lower()
        if lowered == "oid":
            return catalogue
        if lowered in {"classid", "refclassid", "classoid", "refclassoid"}:
            return "pg_class"
        if lowered in {"objid", "refobjid", "objoid", "refobjoid"}:
            is_object = lowered in {"objid", "objoid"}
            class_field = (
                "classoid" if is_object and "classoid" in row
                else "classid" if is_object
                else "refclassoid" if "refclassoid" in row
                else "refclassid"
            )
            return class_catalogue(row.get(class_field))
        if lowered.endswith(("relid", "indexrelid", "indrelid", "conindid")):
            return "pg_class"
        if lowered.endswith(("namespace",)):
            return "pg_namespace"
        if lowered.endswith(("owner", "roleid", "defaclrole", "grantee", "member", "grantor")):
            return "pg_authid"
        if lowered.endswith(("typeid", "typid", "basetype", "elem", "array", "keytype", "intype", "lefttype", "righttype")):
            return "pg_type"
        if lowered.endswith(("foid", "procoid", "fnoid")):
            return "pg_proc"
        if lowered.endswith("amopfamily") or lowered.endswith("family"):
            return "pg_opfamily"
        if lowered.endswith("amproc"):
            return "pg_proc"
        if lowered.endswith("amopopr") or lowered.endswith(("eqop", "exclop", "opno")):
            return "pg_operator"
        if lowered in {"indclass"} or lowered.endswith("opclass"):
            return "pg_opclass"
        if lowered in {"indcollation"} or lowered.endswith("collation"):
            return "pg_collation"
        if lowered.endswith("method"):
            return "pg_am"
        if lowered == "stxoid" or lowered.startswith("stx"):
            return "pg_statistic_ext"
        if lowered.endswith("argtypes") or lowered.endswith("typevector"):
            return "pg_type"
        if lowered.endswith("oid"):
            return catalogue
        if lowered.endswith("id"):
            return catalogue
        return "<unresolved_catalogue>"

    if catalogue == "pg_namespace" and row.get("nspname") is not None:
        return f"schema:{row['nspname']}"
    if catalogue == "pg_authid" and row.get("rolname") is not None:
        return f"role:{row['rolname']}"
    if catalogue == "pg_database" and row.get("datname") is not None:
        return f"database:{row['datname']}"
    if catalogue == "pg_class" and row.get("relname") is not None:
        namespace = reference("pg_namespace", row.get("relnamespace"))
        return f"relation:{namespace}:{row['relname']}:{row.get('relkind')}"
    if catalogue == "pg_type" and row.get("typname") is not None:
        namespace = reference("pg_namespace", row.get("typnamespace"))
        return f"type:{namespace}:{row['typname']}:{row.get('typtype')}"
    if catalogue == "pg_proc" and row.get("proname") is not None:
        namespace = reference("pg_namespace", row.get("pronamespace"))
        return f"routine:{namespace}:{row['proname']}:{row.get('prokind')}:{type_vector(row.get('proargtypes'))}"
    if catalogue == "pg_attribute" and row.get("attname") is not None:
        relation = reference("pg_class", row.get("attrelid"))
        return f"column:{relation}:{row['attname']}:{row.get('attnum')}"
    if catalogue == "pg_index" and row.get("indexrelid") is not None:
        index = reference("pg_class", row.get("indexrelid"))
        table = reference("pg_class", row.get("indrelid"))
        return f"index:{index}:{table}"
    if catalogue == "pg_constraint" and row.get("conname") is not None:
        relation = reference("pg_class", row.get("conrelid"))
        type_identity = reference("pg_type", row.get("contypid"))
        return f"constraint:{relation}:{type_identity}:{row['conname']}:{row.get('contype')}"
    if catalogue == "pg_trigger" and row.get("tgname") is not None:
        relation = reference("pg_class", row.get("tgrelid"))
        return f"trigger:{relation}:{row['tgname']}"
    if catalogue == "pg_rewrite" and row.get("rulename") is not None:
        relation = reference("pg_class", row.get("ev_class"))
        return f"rewrite:{relation}:{row['rulename']}"
    if catalogue in {"pg_depend", "pg_shdepend"} and row.get("objid") is not None:
        return (
            f"dependency:{class_catalogue(row.get('classid'))}:{dependency_object(row.get('classid'), row.get('objid'))}:"
            f"{row.get('objsubid')}:{class_catalogue(row.get('refclassid'))}:"
            f"{dependency_object(row.get('refclassid'), row.get('refobjid'))}:{row.get('refobjsubid')}:{row.get('deptype')}"
        )
    if "starelid" in row and "staattnum" in row:
        return (
            f"statistics:{reference('pg_class', row.get('starelid'))}:"
            f"{row.get('staattnum')}:{row.get('stainherit')}"
        )
    if "stxoid" in row and "stxdinherit" in row:
        return (
            f"extended-statistics:{reference('pg_statistic_ext', row.get('stxoid'))}:"
            f"{row.get('stxdinherit')}"
        )

    if catalogue == "pg_default_acl" and row.get("defaclobjtype") is not None:
        role = reference("pg_authid", row.get("defaclrole"))
        namespace = reference("pg_namespace", row.get("defaclnamespace"))
        return f"default-acl:{role}:{namespace}:{row['defaclobjtype']}"

    for name in ("name", "slot_name", "subscription_name", "pubname", "extname", "fdwname", "srvname", "rolname"):
        if row.get(name) is not None:
            return f"{catalogue}:{name}:{row[name]}"

    # Fallback preserves resolver-derived reference material and scrubs
    # transaction/maintenance counters instead of using raw internal IDs.
    stable: dict[str, Any] = {}
    for key, value in row.items():
        lowered = str(key).lower()
        if lowered.endswith(("xid", "mxid")) or any(marker in lowered for marker in DYNAMIC_FIELD_MARKERS):
            stable[str(key)] = "<dynamic>"
        elif (
            lowered == "oid"
            or (lowered.endswith(("oid", "id")) and lowered not in {"objsubid"})
            or lowered in {"classid", "refclassid", "objid", "refobjid"}
        ):
            target = target_catalogue(str(key), value)
            stable[str(key)] = (
                reference(target, value)
                if target != "<unresolved_catalogue>"
                else "<unresolved_reference>"
            )
        else:
            stable[str(key)] = _canonical(value)
    if not stable or all(value in {"<reference>", "<dynamic>", "<unresolved_reference>"} for value in stable.values()):
        return ""
    return f"row:{schema}.{catalogue}:{_digest(stable)}"


class SemanticIdentityResolver:
    """Resolve catalog OIDs to stable names before normalizing observations."""

    def __init__(self, relation_oids: Mapping[int, str], object_oids: Mapping[tuple[str, int], str]) -> None:
        self.relation_oids = dict(relation_oids)
        self.object_oids = dict(object_oids)

    def resolve(self, *, field_name: str, value: Any, context: Mapping[str, Any] | None = None) -> str:
        if value is None:
            return "<null>"
        if isinstance(value, (list, tuple)):
            return "[" + ",".join(self.resolve(field_name=field_name, value=item, context=context) for item in value) + "]"
        try:
            integer = int(value)
        except (TypeError, ValueError):
            text = str(value)
            if text.startswith("<") or text.startswith("pg_") or "." in text:
                return text
            if isinstance(value, str) and text:
                return f"symbol:{field_name.lower()}:{text}"
            raise UnresolvedReferenceError(field_name)
        if integer == 0:
            return "<zero>"
        lowered = field_name.lower()
        if lowered == "oid":
            candidates = [identity for (catalogue, oid), identity in self.object_oids.items() if oid == integer]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise AmbiguousReferenceError(field_name)
            raise UnresolvedReferenceError(field_name)
        if lowered in {"classid", "refclassid", "classoid", "refclassoid"}:
            try:
                return self.relation_oids[integer]
            except KeyError as exc:
                raise UnresolvedReferenceError(field_name) from exc
        target_catalogues: list[str] = []
        if lowered in {"objid", "refobjid", "objoid", "refobjoid"} and context:
            is_object = lowered in {"objid", "objoid"}
            class_field = (
                "classoid" if is_object and context.get("classoid") is not None
                else "classid" if is_object
                else "refclassoid" if context.get("refclassoid") is not None
                else "refclassid"
            )
            class_value = context.get(class_field)
            if class_value is not None:
                class_name = self.relation_oids.get(int(class_value))
                if class_name:
                    target_catalogues.append(class_name)
        if lowered.endswith("relid") or lowered in {"indexrelid", "indrelid", "conindid"}:
            target_catalogues.append("pg_class")
        elif lowered.endswith("contypid") or lowered.endswith("typeid"):
            target_catalogues.append("pg_type")
        elif lowered.endswith("namespace"):
            target_catalogues.append("pg_namespace")
        elif lowered.endswith(("owner", "roleid", "defaclrole", "grantee", "member", "grantor")):
            target_catalogues.append("pg_authid")
        elif lowered.endswith(("typeid", "typid", "basetype", "elem", "array", "keytype", "intype", "lefttype", "righttype")):
            target_catalogues.append("pg_type")
        elif lowered.endswith(("family",)):
            target_catalogues.append("pg_opfamily")
        elif lowered.endswith(("method",)):
            target_catalogues.append("pg_am")
        elif lowered.endswith(("foid", "procoid")):
            target_catalogues.append("pg_proc")
        elif lowered.endswith(("argtypes", "typevector")):
            target_catalogues.append("pg_type")
        elif lowered in {"indclass"} or lowered.endswith("opclass"):
            target_catalogues.append("pg_opclass")
        elif lowered in {"indcollation"} or lowered.endswith("collation"):
            target_catalogues.append("pg_collation")
        elif lowered.endswith(("eqop", "exclop", "opno")):
            target_catalogues.append("pg_operator")
        elif lowered.endswith("amopfamily"):
            target_catalogues.append("pg_opfamily")
        elif lowered.endswith("amopopr"):
            target_catalogues.append("pg_operator")
        elif lowered.endswith("amproc"):
            target_catalogues.append("pg_proc")
        if not target_catalogues:
            raise UnresolvedReferenceError(field_name)
        matches = [self.object_oids.get((catalogue, integer)) for catalogue in target_catalogues]
        matches = [match for match in matches if match]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousReferenceError(field_name)
        raise UnresolvedReferenceError(field_name)


def _public_value_digest(
    *,
    descriptor: FieldDescriptor,
    policy: FieldPolicy,
    value: Any,
    resolver: SemanticIdentityResolver | None = None,
    context: Mapping[str, Any] | None = None,
) -> tuple[str, str | None]:
    """Normalize a value without exposing its raw content in receipts."""

    if policy.policy_class == "secret_redacted_shape_capability_state":
        return _digest({"shape": _safe_shape(value), "type": descriptor.postgres_type}), None
    if policy.policy_class == "explicit_deferred_external_boundary":
        return _digest({"boundary": True, "type": descriptor.postgres_type, "shape": _safe_shape(value)}), None
    if policy.policy_class == "postgresql_maintained_dynamic_estimate_maintenance_state":
        return _digest({"dynamic": True, "type": descriptor.postgres_type, "presence": value is not None}), None
    if policy.policy_class == "provider_managed_normalized_state":
        return _digest({"provider": True, "type": descriptor.postgres_type, "shape": _safe_shape(value), "value": _canonical(value)}), None
    if policy.policy_class == "stable_normalized_identity_reference_edge":
        if resolver is None:
            raise FinalityError("missing_identity_resolver", descriptor.key)
        if descriptor.field_name.lower() == "oid" and context and context.get("__row_identity"):
            edge = str(context["__row_identity"])
        else:
            edge = resolver.resolve(field_name=descriptor.field_name, value=value, context=context)
        return _digest({"edge": edge, "type": descriptor.postgres_type}), edge
    if policy.policy_class == "canonicalized_collection_set_order":
        values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        normalized: list[Any] = []
        for item in values:
            if resolver is not None and _type_is_oid(descriptor.postgres_type):
                normalized.append(resolver.resolve(field_name=descriptor.field_name, value=item, context=context))
            else:
                normalized.append(_canonical(item))
        normalized.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True))
        return _digest({"collection": normalized, "type": descriptor.postgres_type}), None
    if descriptor.postgres_type.lower() == "pg_node_tree":
        # Internal node trees contain implementation OIDs.  They are retained
        # as a type/shape receipt after numeric OID tokens are scrubbed; no raw
        # OID can enter the semantic digest.
        scrubbed = re.sub(r"\b\d+\b", "<oid>", str(value))
        return _digest({"node_tree": scrubbed, "type": descriptor.postgres_type}), None
    return _digest({"value": _canonical(value), "type": descriptor.postgres_type}), None


class LiveCatalogueCollector:
    """Derive and execute the whole live base-catalogue field universe."""

    DISCOVERY_SQL = """
select n.nspname as schema_name,
       c.relname as catalogue_name,
       c.relkind::text as relation_kind,
       c.oid as relation_oid,
       a.attname as field_name,
       format_type(a.atttypid, a.atttypmod) as postgres_type,
       a.atttypmod as typmod,
       case when a.attcollation = 0 then null else a.attcollation::regcollation::text end as collation,
       a.attnum,
       a.attnotnull as not_null,
       a.attstorage::text as storage,
       a.attgenerated::text as generated
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_attribute a on a.attrelid = c.oid
where n.nspname = 'pg_catalog'
  and c.relkind in ('r', 't', 'p')
  and a.attnum > 0
  and not a.attisdropped
order by n.nspname, c.relname, a.attnum
"""

    def __init__(self, connection: Any, reference_id: str) -> None:
        self.connection = connection
        self.reference_id = reference_id
        self.descriptors: tuple[FieldDescriptor, ...] = ()
        self.registry = PolicyRegistry()
        self.boundary_receipts: tuple[dict[str, Any], ...] = ()
        self._stable_oids: dict[tuple[str, int], str] = {}

    def discover(self) -> tuple[FieldDescriptor, ...]:
        rows = self.connection.execute(self.DISCOVERY_SQL).fetchall()
        descriptors = []
        relation_oids: dict[int, str] = {}
        for index, row in enumerate(rows):
            schema = str(_row_value(row, "schema_name", 0))
            catalogue = str(_row_value(row, "catalogue_name", 1))
            relation_kind = str(_row_value(row, "relation_kind", 2))
            relation_oid = int(_row_value(row, "relation_oid", 3))
            relation_oids[relation_oid] = catalogue
            descriptor = FieldDescriptor(
                schema=schema,
                catalogue=catalogue,
                relation_kind=relation_kind,
                field_name=str(_row_value(row, "field_name", 4)),
                postgres_type=str(_row_value(row, "postgres_type", 5)),
                typmod=_row_value(row, "typmod", 6),
                collation=_row_value(row, "collation", 7),
                attnum=int(_row_value(row, "attnum", 8)),
                not_null=bool(_row_value(row, "not_null", 9)),
                storage=str(_row_value(row, "storage", 10) or ""),
                generated=str(_row_value(row, "generated", 11) or ""),
            )
            descriptors.append(descriptor)
        if not descriptors:
            raise FinalityError("catalogue_discovery_returned_zero_fields")
        self.descriptors = tuple(descriptors)
        self.registry = PolicyRegistry(self.descriptors)
        self._relation_oids = relation_oids
        return self.descriptors

    def _fetch_rows(self, descriptor_group: Sequence[FieldDescriptor]) -> tuple[list[str], list[tuple[Any, ...]]]:
        first = descriptor_group[0]
        query = f"select * from {_quote_identifier(first.schema)}.{_quote_identifier(first.catalogue)}"
        cursor = self.connection.execute(query)
        names = []
        for item in cursor.description or ():
            names.append(str(getattr(item, "name", item[0])))
        return names, [tuple(row) for row in cursor.fetchall()]

    def _build_identity_maps(
        self,
        groups: Mapping[tuple[str, str], Sequence[FieldDescriptor]],
        rows_by_relation: Mapping[tuple[str, str], tuple[list[str], list[tuple[Any, ...]]]],
    ) -> tuple[SemanticIdentityResolver, dict[tuple[str, str, str], str]]:
        relation_oids = getattr(self, "_relation_oids", {})
        entries: list[tuple[str, str, list[str], tuple[Any, ...]]] = []
        for (schema, catalogue), (names, rows) in rows_by_relation.items():
            for row in rows:
                values = dict(zip(names, row))
                if catalogue == "pg_largeobject_metadata" and values.get("oid") is not None:
                    raise FinalityError("large_object_identity_requires_manifest")
                entries.append((schema, catalogue, names, tuple(row)))

        priority = {
            "pg_namespace": 0,
            "pg_authid": 1,
            "pg_database": 2,
            "pg_class": 3,
            "pg_type": 4,
            "pg_proc": 5,
            "pg_attribute": 6,
        }
        ordered_entries = sorted(entries, key=lambda item: (priority.get(item[1], 50), item[0], item[1], _digest(item[3])))
        stable_oids: dict[tuple[str, int], str] = {}
        row_identities: dict[tuple[str, str, str], str] = {}
        for _pass in range(2):
            next_objects: dict[tuple[str, int], str] = {}
            row_identities = {}
            for schema, catalogue, names, raw_row in ordered_entries:
                values = dict(zip(names, raw_row))
                seed = _row_identity_seed(
                    schema,
                    catalogue,
                    values,
                    stable_oids=stable_oids,
                    relation_oids=relation_oids,
                )
                if not seed:
                    raise FinalityError("catalogue_row_identity_unproven", f"{schema}.{catalogue}")
                identity = f"{schema}.{catalogue}:{seed}"
                row_identities[(schema, catalogue, _digest(values))] = identity
                raw_oid = values.get("oid")
                try:
                    raw_integer = int(raw_oid) if raw_oid is not None else 0
                except (TypeError, ValueError):
                    raw_integer = 0
                if raw_integer:
                    key = (catalogue, raw_integer)
                    prior = next_objects.get(key)
                    if prior is not None and prior != identity:
                        raise AmbiguousReferenceError(catalogue)
                    next_objects[key] = identity
            stable_oids.update(next_objects)
        self._stable_oids = dict(stable_oids)
        resolver = SemanticIdentityResolver(relation_oids, stable_oids)
        return resolver, row_identities

    def _collect_boundary_receipts(self) -> tuple[dict[str, Any], ...]:
        """Collect public-safe TOAST/system authority evidence without page rows."""

        relation_rows = self.connection.execute(
            """
select n.nspname as schema_name,
       c.relname as relation_name,
       c.relkind::text as relation_kind,
       c.relacl::text as relation_acl
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'pg_toast'
  and c.relkind in ('r', 't', 'p')
order by c.relname
"""
        ).fetchall()
        namespace_rows = self.connection.execute(
            """
select nspname as schema_name,
       nspacl::text as schema_acl
from pg_catalog.pg_namespace
where nspname = 'pg_toast'
"""
        ).fetchall()
        if len(namespace_rows) != 1:
            raise FinalityError("toast_authority_boundary_failed")
        receipts: list[dict[str, Any]] = []
        namespace = namespace_rows[0]
        schema_name = str(_row_value(namespace, "schema_name", 0))
        schema_acl = _row_value(namespace, "schema_acl", 1)
        receipts.append(
            {
                "kind": "toast_schema_acl_boundary",
                "identity": f"schema:{schema_name}",
                "digest": _digest({"schema": schema_name, "acl": _canonical(schema_acl)}),
            }
        )
        for row in relation_rows:
            relation_name = str(_row_value(row, "relation_name", 1))
            relation_kind = str(_row_value(row, "relation_kind", 2))
            relation_acl = _row_value(row, "relation_acl", 3)
            if not relation_name or not relation_kind:
                raise FinalityError("toast_authority_boundary_failed")
            receipts.append(
                {
                    "kind": "toast_relation_acl_boundary",
                    "identity": f"relation:{schema_name}.{relation_name}:{relation_kind}",
                    "digest": _digest(
                        {
                            "schema": schema_name,
                            "relation": relation_name,
                            "relation_kind": relation_kind,
                            "acl": _canonical(relation_acl),
                        }
                    ),
                }
            )
        return tuple(sorted(receipts, key=lambda item: (item["kind"], item["identity"])))

    def collect(self) -> tuple[tuple[Observation, ...], ExecutionTrace]:
        if not self.descriptors:
            self.discover()
        self.boundary_receipts = self._collect_boundary_receipts()
        groups: dict[tuple[str, str], list[FieldDescriptor]] = defaultdict(list)
        for descriptor in self.descriptors:
            groups[(descriptor.schema, descriptor.catalogue)].append(descriptor)
        rows_by_relation: dict[tuple[str, str], tuple[list[str], list[tuple[Any, ...]]]] = {}
        for key, descriptor_group in sorted(groups.items()):
            rows_by_relation[key] = self._fetch_rows(descriptor_group)
        resolver, identity_map = self._build_identity_maps(groups, rows_by_relation)
        observations: list[Observation] = []
        field_values = 0
        executed_fields: set[str] = set()
        for key, descriptor_group in sorted(groups.items()):
            schema, catalogue = key
            names, rows = rows_by_relation[key]
            descriptor_by_name = {descriptor.field_name: descriptor for descriptor in descriptor_group}
            if not rows:
                for descriptor in descriptor_group:
                    policy = self.registry.policy_for(descriptor.key)
                    observations.append(
                        Observation.from_value(
                            reference_id=self.reference_id,
                            descriptor=descriptor,
                            policy=policy,
                            row_identity=f"{schema}.{catalogue}:<empty>",
                            object_kind=f"catalogue:{descriptor.relation_kind}",
                            value=None,
                            applicability="authorized_empty_catalogue_boundary",
                            boundary=True,
                        )
                    )
                    executed_fields.add(descriptor.key)
                continue
            canonicalized_dependency_rows: set[str] = set()
            for raw_row in rows:
                if catalogue in {"pg_depend", "pg_shdepend"}:
                    duplicate_key = _digest(raw_row)
                    if duplicate_key in canonicalized_dependency_rows:
                        continue
                    canonicalized_dependency_rows.add(duplicate_key)
                values = dict(zip(names, raw_row))
                seed = _row_identity_seed(
                    schema,
                    catalogue,
                    values,
                    stable_oids=self._stable_oids,
                    relation_oids=getattr(self, "_relation_oids", {}),
                )
                if not seed:
                    raise FinalityError("catalogue_row_identity_unproven", f"{schema}.{catalogue}")
                row_identity = f"{schema}.{catalogue}:{seed}"
                object_kind = f"catalogue:{descriptor_group[0].relation_kind}"
                for field_name, value in zip(names, raw_row):
                    descriptor = descriptor_by_name.get(field_name)
                    if descriptor is None:
                        raise FinalityError("unknown_catalogue_field", f"{schema}.{catalogue}.{field_name}")
                    policy = self.registry.policy_for(descriptor.key)
                    value_digest, edge = _public_value_digest(
                        descriptor=descriptor,
                        policy=policy,
                        value=value,
                        resolver=resolver,
                        context={**values, "__row_identity": row_identity},
                    )
                    observations.append(
                        Observation.from_value(
                            reference_id=self.reference_id,
                            descriptor=descriptor,
                            policy=policy,
                            row_identity=row_identity,
                            object_kind=object_kind,
                            value=value,
                            value_digest=value_digest,
                            raw_digest=_digest(value),
                            normalized_edge=edge,
                        )
                    )
                    executed_fields.add(descriptor.key)
                    if value is not None:
                        field_values += 1
        trace = ExecutionTrace.live(executed_fields, field_values)
        return tuple(observations), trace


def compare_snapshots(
    left: FinalitySnapshot,
    right: FinalitySnapshot,
    *,
    allow_dynamic_variance: bool = True,
) -> dict[str, Any]:
    """Compare shared-engine snapshots and return a public-safe receipt."""

    if left.policy_registry_digest != right.policy_registry_digest:
        raise FinalityError("policy_registry_drift")
    if left.coverage_universe_digest != right.coverage_universe_digest:
        raise FinalityError("coverage_universe_drift")
    left_by_key = {item.identity_key: item for item in left.observations}
    right_by_key = {item.identity_key: item for item in right.observations}
    if set(left_by_key) != set(right_by_key):
        raise FinalityError("semantic_object_or_field_universe_drift")
    variance: list[dict[str, Any]] = []
    for key in sorted(left_by_key):
        before = left_by_key[key]
        after = right_by_key[key]
        for attr, code in (
            ("postgres_type", "field_type_drift"),
            ("typmod", "field_typmod_drift"),
            ("collation", "field_collation_drift"),
            ("attnum", "field_metadata_drift"),
            ("not_null", "field_metadata_drift"),
            ("storage", "field_metadata_drift"),
            ("generated", "field_metadata_drift"),
            ("presence", "field_presence_drift"),
            ("applicability", "field_applicability_drift"),
            ("policy_id", "policy_registry_drift"),
            ("policy_class", "policy_registry_drift"),
            ("capability", "authority_drift"),
            ("classification", "authority_drift"),
        ):
            if getattr(before, attr) != getattr(after, attr):
                raise FinalityError(code, before.field_key)
        if before.policy_class == "postgresql_maintained_dynamic_estimate_maintenance_state":
            if before.raw_digest != after.raw_digest:
                if not allow_dynamic_variance:
                    raise FinalityError("dynamic_variance_not_allowed", before.field_key)
                variance.append(
                    {
                        "field": before.field_key,
                        "row_identity": before.row_identity,
                        "before_raw_digest": before.raw_digest,
                        "after_raw_digest": after.raw_digest,
                        "receipt": "registered_postgresql_maintenance_variance",
                    }
                )
            continue
        if before.value_digest != after.value_digest:
            if before.capability in {"authority", "publication_membership"}:
                raise FinalityError("authority_drift", before.field_key)
            if before.capability in {"dependency", "trigger_dependency"} or before.normalized_edge != after.normalized_edge:
                raise FinalityError("relationship_dependency_drift", before.field_key)
            raise FinalityError("semantic_value_drift", before.field_key)
        if before.normalized_edge != after.normalized_edge:
            raise FinalityError("relationship_dependency_drift", before.field_key)
    if left.semantic_node_digest != right.semantic_node_digest:
        raise FinalityError("semantic_node_digest_drift")
    if left.relationship_digest != right.relationship_digest:
        raise FinalityError("relationship_digest_drift")
    if left.authority_digest != right.authority_digest:
        raise FinalityError("authority_digest_drift")
    if left.provider_boundary_digest != right.provider_boundary_digest:
        raise FinalityError("provider_boundary_digest_drift")
    return {
        "left": left.reference_id,
        "right": right.reference_id,
        "coverage_universe_digest": left.coverage_universe_digest,
        "policy_registry_digest": left.policy_registry_digest,
        "semantic_node_digest": left.semantic_node_digest,
        "relationship_digest": left.relationship_digest,
        "authority_digest": left.authority_digest,
        "provider_boundary_digest": left.provider_boundary_digest,
        "dynamic_variance_receipts": variance,
        "converged": True,
    }


def compare_real_references(
    references: Sequence[tuple[ReferenceReceipt, FinalitySnapshot]],
    *,
    require_maintenance: bool = False,
) -> dict[str, Any]:
    if len(references) != 4:
        raise FinalityError("four_reference_requirement_not_met")
    ids = [receipt.reference_id for receipt, _ in references]
    if ids != ["A", "B", "C", "P"]:
        raise FinalityError("reference_labels_not_exact")
    cluster_ids = [receipt.cluster_system_identity for receipt, _ in references]
    if len(set(cluster_ids)) != 4:
        raise FinalityError("same_physical_reference_reused")
    for receipt, snapshot in references:
        receipt.validate_live()
        if snapshot.reference_id != receipt.reference_id:
            raise FinalityError("receipt_snapshot_reference_mismatch")
        if snapshot.trace.synthetic or not snapshot.trace.live_connection:
            raise FinalityError("synthetic_snapshot_cannot_satisfy_real_gate")
    p_receipt = references[-1][0]
    if require_maintenance and (not p_receipt.maintenance_executed or p_receipt.maintenance_variance_witness_count <= 0):
        raise FinalityError("maintenance_operation_not_executed")
    convergence: list[dict[str, Any]] = []
    baseline = references[0][1]
    for receipt, snapshot in references[1:]:
        convergence.append(compare_snapshots(baseline, snapshot))
    return {
        "collector_mode": "live_postgresql17",
        "synthetic": False,
        "real_connection": True,
        "reference_ids": ids,
        "references": [receipt.public_dict() for receipt, _ in references],
        "snapshots": [snapshot.public_dict() for _, snapshot in references],
        "convergence": convergence,
        "converged": True,
        "cleanup_verified": all(receipt.cleanup_verified for receipt, _ in references),
        "maintenance_verified": p_receipt.maintenance_executed and p_receipt.maintenance_variance_witness_count > 0,
    }


def load_coverage_manifest(root: Path | None = None) -> dict[str, Any]:
    root = root or Path(__file__).resolve().parents[1]
    path = root / "docs" / "postgresql17-finality-coverage.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalityError("coverage_manifest_unreadable") from exc
    if payload.get("postgres_major") != POSTGRES_MAJOR:
        raise FinalityError("coverage_manifest_postgres_major_mismatch")
    if payload.get("engine_version") != ENGINE_VERSION:
        raise FinalityError("coverage_manifest_engine_version_mismatch")
    if payload.get("catalogue_universe", {}).get("derivation") != "live_pg_class_pg_attribute":
        raise FinalityError("coverage_manifest_not_live_derived")
    return payload
