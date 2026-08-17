#!/usr/bin/env python3
"""Fail-closed A24 PostgreSQL 17 discovery, projection, and comparison engine."""

from __future__ import annotations

import collections
import datetime as _datetime
import decimal
import hashlib
import json
import math
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postgresql17_finality_authority import (  # noqa: E402
    AuthorityError,
    descriptor_digest,
    descriptor_key,
    digest_json,
    load_authority_package,
    re_forbidden_sql,
    validate_closed_world,
)


class FinalityRedError(RuntimeError):
    """A public-safe fail-closed observation or semantic comparison failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


@dataclass(frozen=True)
class TypedObservation:
    field_key: str
    mode: str
    semantic_policy: str
    receipt_type: str
    descriptor_digest: str
    value_type: str | None = None
    value: Any = None

    def public_dict(self) -> dict[str, Any]:
        result = {
            "field_key": self.field_key,
            "mode": self.mode,
            "semantic_policy": self.semantic_policy,
            "receipt_type": self.receipt_type,
            "descriptor_digest": self.descriptor_digest,
        }
        if self.value_type is not None:
            result["value_type"] = self.value_type
        if self.receipt_type not in {"METADATA_ONLY", "DERIVED_ONLY", "FORBIDDEN_RAW"}:
            result["value"] = safe_json_value(self.value, self.value_type)
        return result


@dataclass(frozen=True)
class CompiledProjection:
    plan_id: str
    relation_semantic_id: str
    mode: str
    sql: str | None
    semantic_policy: str
    value_fields: tuple[str, ...]
    digest: str


DISCOVERY_SQL = """
select
    n.nspname as namespace,
    c.relname as relation_name,
    c.relkind::text as relation_kind,
    c.relispartition,
    c.relispartition as is_partition,
    c.relpersistence::text as persistence,
    c.relreplident::text as replica_identity,
    c.relrowsecurity,
    c.relforcerowsecurity,
    owner_role.rolname as relowner_role,
    am.amname as relam_name,
    c.relisshared,
    toast_n.nspname as toast_namespace,
    toast_c.relname as toast_relation_name,
    toast_c.relkind::text as toast_relation_kind,
    coalesce(parent_n.nspname, toast_index_parent_n.nspname) as toast_parent_namespace,
    coalesce(parent_c.relname, toast_index_parent_c.relname) as toast_parent_relation_name,
    coalesce(parent_c.relkind::text, toast_index_parent_c.relkind::text) as toast_parent_relation_kind,
    a.attnum,
    case when a.attisdropped then null else a.attname end as attname,
    a.attisdropped,
    tns.nspname as type_namespace,
    t.typname as type_name,
    t.typtype::text as type_kind,
    a.atttypmod,
    a.attlen,
    a.attalign::text,
    a.attstorage::text,
    a.attcompression::text,
    a.attbyval,
    a.attgenerated::text,
    a.attidentity::text,
    case when a.attcollation = 0 then null else cns.nspname end as collation_namespace,
    case when a.attcollation = 0 then null else coll.collname end as collation_name
from pg_catalog.pg_class as c
join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
left join pg_catalog.pg_roles as owner_role on owner_role.oid = c.relowner
left join pg_catalog.pg_am as am on am.oid = c.relam
left join pg_catalog.pg_class as toast_c on toast_c.oid = c.reltoastrelid
left join pg_catalog.pg_namespace as toast_n on toast_n.oid = toast_c.relnamespace
left join pg_catalog.pg_class as parent_c on parent_c.reltoastrelid = c.oid
left join pg_catalog.pg_namespace as parent_n on parent_n.oid = parent_c.relnamespace
left join pg_catalog.pg_index as toast_index on toast_index.indexrelid = c.oid
left join pg_catalog.pg_class as toast_index_table on toast_index_table.oid = toast_index.indrelid
left join pg_catalog.pg_class as toast_index_parent_c on toast_index_parent_c.reltoastrelid = toast_index_table.oid
left join pg_catalog.pg_namespace as toast_index_parent_n on toast_index_parent_n.oid = toast_index_parent_c.relnamespace
left join pg_catalog.pg_attribute as a on a.attrelid = c.oid
left join pg_catalog.pg_type as t on t.oid = a.atttypid
left join pg_catalog.pg_namespace as tns on tns.oid = t.typnamespace
left join pg_catalog.pg_collation as coll on coll.oid = a.attcollation
left join pg_catalog.pg_namespace as cns on cns.oid = coll.collnamespace
where n.nspname in ('pg_catalog', 'pg_toast')
  and c.relpersistence <> 't'
order by n.nspname, c.relname, a.attnum
"""


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (KeyError, IndexError, TypeError):
        return row[key]


def _fetchall(connection: Any, sql: str, params: Sequence[Any] = ()) -> list[Any]:
    try:
        cursor = connection.execute(sql, params)
    except TypeError:
        cursor = connection.execute(sql)
    return list(cursor.fetchall())


def assert_postgresql17(connection: Any) -> str:
    rows = _fetchall(connection, "show server_version_num")
    if len(rows) != 1:
        raise FinalityRedError("postgresql_version_receipt_missing")
    raw = _row_value(rows[0], "server_version_num", 0)
    text = str(raw)
    if raw is None or not text.isdigit() or len(text) < 5:
        raise FinalityRedError("postgresql_version_receipt_invalid")
    if int(text[:2]) != 17:
        raise FinalityRedError("unsupported_postgresql_major")
    return text


def _relation_semantic_id(namespace: str, relation_name: str, relation_kind: str, parent_namespace: Any, parent_name: Any) -> str:
    if namespace == "pg_toast":
        if parent_namespace and parent_name:
            prefix = "toast_index" if relation_kind == "i" else "toast"
            return f"{prefix}:{parent_namespace}.{parent_name}"
        return f"toast_orphan:{namespace}.{relation_name}"
    return f"{namespace}.{relation_name}"


def _descriptor_from_row(row: Any) -> list[dict[str, Any]]:
    namespace = str(_row_value(row, "namespace", 0))
    relation_name = str(_row_value(row, "relation_name", 1))
    relation_kind = str(_row_value(row, "relation_kind", 2))
    physical_relation_name = relation_name
    if namespace == "pg_toast" and relation_kind == "i":
        relation_name = "__toast_index__"
    elif namespace == "pg_toast":
        relation_name = "__toast__"
    parent_namespace = _row_value(row, "toast_parent_namespace", 15)
    parent_name = _row_value(row, "toast_parent_relation_name", 16)
    relation_id = _relation_semantic_id(namespace, physical_relation_name, relation_kind, parent_namespace, parent_name)
    shared = bool(_row_value(row, "relisshared", 11))
    toast_namespace = _row_value(row, "toast_namespace", 12)
    toast_name = _row_value(row, "toast_relation_name", 13)
    if namespace == "pg_toast" and parent_namespace and parent_name:
        toast_attachment: dict[str, Any] | None = {"mode": "attached_to_index" if relation_kind == "i" else "attached_to", "parent_relation_semantic_id": f"{("toast_index" if relation_kind == "i" else "toast")}:{parent_namespace}.{parent_name}"}
    elif namespace == "pg_toast":
        toast_attachment = {"mode": "orphaned", "attachment_receipt": "METADATA_ONLY"}
    elif toast_namespace and toast_name:
        toast_attachment = {"mode": "has_toast", "toast_relation_semantic_id": f"toast:{namespace}.{physical_relation_name}"}
    else:
        toast_attachment = None
    common = {
        "relation_semantic_id": relation_id, "namespace": namespace, "relation_name": relation_name,
        "relation_kind": relation_kind, "catalogue_scope": "shared" if shared else "local", "shared": shared,
        "length": None, "alignment": None, "storage": None, "compression": None, "by_value": None,
        "generated": None, "identity": None, "toast_attachment": toast_attachment,
    }
    relation_descriptor = dict(common)
    relation_descriptor.update({
        "attnum": 0, "attname": "__relation__", "attname_state": "relation", "attisdropped": False,
        "type_identity": {"namespace": None, "name": None, "kind": "relation"}, "typmod": None,
        "collation_identity": None, "applicability": "RELATION", "descriptor_kind": "relation",
    })
    descriptors = [relation_descriptor]
    attnum = _row_value(row, "attnum", 18)
    if attnum is None:
        return descriptors
    attnum = int(attnum)
    dropped = bool(_row_value(row, "attisdropped", 20))
    attname = _row_value(row, "attname", 19)
    type_namespace = _row_value(row, "type_namespace", 21)
    type_name = _row_value(row, "type_name", 22)
    type_kind = _row_value(row, "type_kind", 23)
    collation_namespace = _row_value(row, "collation_namespace", 32)
    collation_name = _row_value(row, "collation_name", 33)
    descriptor = dict(common)
    descriptor.update({
        "attnum": attnum, "attname": None if attname is None else str(attname),
        "attname_state": "dropped" if dropped else "non_dropped", "attisdropped": dropped,
        "type_identity": {"namespace": None if type_namespace is None else str(type_namespace), "name": None if type_name is None else str(type_name), "kind": None if type_kind is None else str(type_kind)},
        "typmod": _row_value(row, "atttypmod", 24),
        "collation_identity": None if collation_namespace is None or collation_name is None else f"{collation_namespace}.{collation_name}",
        "length": _row_value(row, "attlen", 25), "alignment": _row_value(row, "attalign", 26),
        "storage": _row_value(row, "attstorage", 27), "compression": _row_value(row, "attcompression", 28),
        "by_value": _row_value(row, "attbyval", 29), "generated": _row_value(row, "attgenerated", 30),
        "identity": _row_value(row, "attidentity", 31),
        "applicability": "DROPPED" if dropped else ("SYSTEM" if attnum < 0 else "VALUE"),
        "descriptor_kind": "attribute",
    })
    descriptors.append(descriptor)
    return descriptors


def discover_descriptors(connection: Any) -> list[dict[str, Any]]:
    """Discover the mechanical pg_catalog/pg_toast universe only."""
    assert_postgresql17(connection)
    by_key: dict[str, dict[str, Any]] = {}
    for row in _fetchall(connection, DISCOVERY_SQL):
        for descriptor in _descriptor_from_row(row):
            key = descriptor_key(descriptor)
            if key in by_key and by_key[key] != descriptor:
                raise FinalityRedError("descriptor_duplicate_conflict", key)
            by_key[key] = descriptor
    descriptors = sorted(by_key.values(), key=descriptor_key)
    if not descriptors:
        raise FinalityRedError("catalogue_descriptor_discovery_empty")
    if not any(item["attnum"] < 0 for item in descriptors):
        raise FinalityRedError("negative_attribute_discovery_missing")
    return descriptors


def _validate_projection_sql(sql: str, plan_id: str) -> None:
    if not isinstance(sql, str) or not sql.strip() or not sql.lstrip().lower().startswith("select"):
        raise FinalityRedError("safe_projection_sql_missing", plan_id)
    lowered = sql.lower()
    if ";" in lowered.rstrip().rstrip(";") or re_forbidden_sql(lowered):
        raise FinalityRedError("unsafe_projection_sql", plan_id)


def compile_safe_projection(policy: dict[str, Any], descriptors: Iterable[dict[str, Any]]) -> dict[str, CompiledProjection]:
    relation_ids = {item["relation_semantic_id"] for item in descriptors}
    compiled: dict[str, CompiledProjection] = {}
    for plan_id, plan in policy["safe_projection_plans"].items():
        relation = plan["relation_semantic_id"]
        if relation not in relation_ids and relation != "__global__":
            raise FinalityRedError("projection_relation_not_in_universe", plan_id)
        if plan["mode"] != "METADATA_ONLY":
            _validate_projection_sql(plan["sql"], plan_id)
        value_fields = tuple(plan["value_fields"])
        for field_key in value_fields:
            if field_key not in policy["safety_bindings"]:
                raise FinalityRedError("projection_field_not_in_universe", plan_id)
            mode = policy["safety_bindings"][field_key]["mode"]
            if mode in {"FORBIDDEN_RAW", "METADATA_ONLY"} and plan["mode"] == "SAFE_SQL":
                raise FinalityRedError("projection_reads_unadmitted_field", plan_id)
        if plan["semantic_policy"] == "residual_exact" and (not value_fields or any(policy["safety_bindings"][field]["mode"] != "RAW_ALLOWED" for field in value_fields)):
            raise FinalityRedError("residual_exact_requires_locked_raw_allowed", plan_id)
        payload = {"plan_id": plan_id, "relation_semantic_id": relation, "mode": plan["mode"], "sql": plan["sql"], "semantic_policy": plan["semantic_policy"], "value_fields": value_fields}
        compiled[plan_id] = CompiledProjection(plan_id, relation, plan["mode"], plan["sql"], plan["semantic_policy"], value_fields, digest_json(payload))
    return compiled


def safe_json_value(value: Any, declared_type: str | None = None) -> Any:
    """Serialize only an explicitly supported value type; never stringify unknowns."""
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FinalityRedError("unsupported_nonfinite_numeric_value")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        if declared_type not in {"bytea_digest", "public_safe_bytes"}:
            raise FinalityRedError("unsupported_raw_bytes_serializer")
        return {"sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, (list, tuple)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise FinalityRedError("unsupported_mapping_key_type")
        return {key: safe_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, decimal.Decimal):
        return {"decimal": format(value, "f")}
    if isinstance(value, uuid.UUID):
        return {"uuid": value.hex}
    if isinstance(value, (_datetime.datetime, _datetime.date, _datetime.time)):
        return {"iso": value.isoformat()}
    raise FinalityRedError("unsupported_typed_serializer", declared_type or type(value).__name__)


def _execute_projection(connection: Any, projection: CompiledProjection) -> dict[str, Any]:
    if projection.mode == "METADATA_ONLY":
        return {"plan_id": projection.plan_id, "mode": projection.mode, "receipt_type": "METADATA_ONLY", "row_count": None, "value_digest": None, "projection_digest": projection.digest}
    if projection.sql is None:
        raise FinalityRedError("compiled_projection_sql_missing", projection.plan_id)
    rows = _fetchall(connection, projection.sql)
    safe_rows: list[Any] = []
    for row in rows:
        if isinstance(row, Mapping):
            safe_rows.append({str(key): safe_json_value(value) for key, value in row.items()})
        else:
            safe_rows.append([safe_json_value(value) for value in row])
    if projection.mode == "LARGE_OBJECT_GUARD":
        if len(safe_rows) != 1:
            raise FinalityRedError("large_object_guard_result_invalid")
        first = safe_rows[0]
        count = next(iter(first.values())) if isinstance(first, dict) else first[0]
        if not isinstance(count, int) or count < 0:
            raise FinalityRedError("large_object_guard_result_invalid")
        if count != 0:
            raise FinalityRedError("unexpected_large_object_metadata")
        return {"plan_id": projection.plan_id, "mode": projection.mode, "receipt_type": "LARGE_OBJECT_ABSENCE", "row_count": count, "value_digest": digest_json({"count": count}), "projection_digest": projection.digest}
    return {"plan_id": projection.plan_id, "mode": projection.mode, "receipt_type": "SAFE_DERIVED_OBSERVATION", "row_count": len(safe_rows), "value_digest": digest_json(safe_rows), "projection_digest": projection.digest}


def _descriptor_observations(descriptors: Sequence[dict[str, Any]], policy: dict[str, Any]) -> list[TypedObservation]:
    observations: list[TypedObservation] = []
    for descriptor in descriptors:
        key = descriptor_key(descriptor)
        mode = policy["safety_bindings"][key]["mode"]
        semantic_policy = policy["semantic_bindings"][key]["policy"]
        receipt_type = mode if mode != "RAW_ALLOWED" else "DERIVED_ONLY"
        observations.append(TypedObservation(key, mode, semantic_policy, receipt_type, descriptor_digest([descriptor])))
    return observations


def collect_reference(connection: Any, *, reference_id: str = "reference", package: tuple[dict[str, Any], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Collect one reference in the locked A24 phase order."""
    # Authority is loaded before the first database read. Discovery is the
    # first read; all value-bearing plans follow safety/policy closure.
    if package is None:
        package = load_authority_package()
    coverage, policy = package
    descriptors = discover_descriptors(connection)
    validate_closed_world(descriptors, coverage, policy)
    compiled = compile_safe_projection(policy, descriptors)
    plan_receipts = [_execute_projection(connection, compiled[plan_id]) for plan_id in sorted(compiled)]
    observations = _descriptor_observations(descriptors, policy)
    validate_closed_world(descriptors, coverage, policy, [item.field_key for item in observations])
    counts = collections.Counter(item.receipt_type for item in observations)
    large_object_count = next((item["row_count"] for item in plan_receipts if item["receipt_type"] == "LARGE_OBJECT_ABSENCE"), 0)
    return {
        "status": "PASS", "reference_id": reference_id, "phase_order": ["metadata_discovery", "safety_binding", "semantic_binding", "safe_projection_compilation", "database_read", "typed_observation", "semantic_comparison", "public_safe_receipt"],
        "server_version_num": assert_postgresql17(connection), "postgres_major": 17,
        "descriptor_count": len(descriptors), "descriptor_digest": descriptor_digest(descriptors),
        "safety_binding_digest": digest_json(policy["safety_bindings"]), "semantic_binding_digest": digest_json(policy["semantic_bindings"]),
        "binding_index_digest": digest_json(policy["binding_index"]), "query_plan_digest": policy["query_plan_digest"],
        "executed_plan_count": len(plan_receipts), "safe_plan_digest": digest_json(plan_receipts),
        "observation_count": len(observations), "typed_receipt_counts": dict(sorted(counts.items())),
        "observation_digest": digest_json([item.public_dict() for item in observations]), "package_digest": policy["package_digest"],
        "large_object_count": large_object_count, "forbidden_raw_values_read": False, "canary_status": "absent",
    }


def semantic_identity(row: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[Any, ...] | dict[str, Any]:
    """Resolve only explicit semantic identities; OID/ordinal fallback is red."""
    mode = contract.get("mode")
    if mode == "metadata_boundary":
        return {"receipt_type": contract.get("typed_receipt", "METADATA_ONLY")}
    if mode == "semantic_key":
        fields = contract.get("fields")
        if not isinstance(fields, list) or not fields:
            raise FinalityRedError("semantic_key_contract_missing")
        if any(not isinstance(field, str) or field not in row or row[field] is None for field in fields):
            raise FinalityRedError("semantic_identity_component_missing")
        return tuple(safe_json_value(row[field]) for field in fields)
    if mode == "object_address":
        address = contract.get("address")
        if not isinstance(address, dict) or set(address) != {"class", "identity"}:
            raise FinalityRedError("object_address_contract_invalid")
        if any(token in json.dumps(address, sort_keys=True).lower() for token in ("oid", "ordinal")):
            raise FinalityRedError("object_address_raw_identity_forbidden")
        return {"class": address["class"], "identity": safe_json_value(row.get(address["identity"]))}
    if mode == "edge_tuple":
        fields = contract.get("fields")
        if not isinstance(fields, list) or not fields or any(field not in row for field in fields):
            raise FinalityRedError("edge_tuple_contract_invalid")
        return tuple(safe_json_value(row[field]) for field in fields)
    raise FinalityRedError("identity_contract_unknown")


def canonicalize_collection(values: Sequence[Any], policy: str) -> Any:
    canonical = [safe_json_value(value) for value in values]
    if policy in {"ordered_sequence", "ordered_reference_sequence", "paired_positional"}:
        return canonical
    if policy == "unordered_set":
        encoded = [json.dumps(value, sort_keys=True, separators=(",", ":")) for value in canonical]
        if len(encoded) != len(set(encoded)):
            raise FinalityRedError("unordered_set_duplicate")
        return sorted(encoded)
    if policy == "multiset":
        return sorted(json.dumps(value, sort_keys=True, separators=(",", ":")) for value in canonical)
    raise FinalityRedError("collection_policy_not_explicit")


def compare_paired_vectors(left: Sequence[Sequence[Any]], right: Sequence[Sequence[Any]]) -> None:
    if any(not isinstance(item, (list, tuple)) for item in (*left, *right)):
        raise FinalityRedError("paired_vector_row_invalid")
    if canonicalize_collection(left, "paired_positional") != canonicalize_collection(right, "paired_positional"):
        raise FinalityRedError("paired_vector_correspondence_drift")


def compare_acl_tuples(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> None:
    fields = ("target", "object_class", "scope", "grantor", "grantee", "public", "privilege", "grant_option", "default_owner")
    def encoded(items: Sequence[Mapping[str, Any]]) -> list[str]:
        rows: list[str] = []
        for item in items:
            if any(field not in item for field in fields):
                raise FinalityRedError("acl_semantic_tuple_incomplete")
            rows.append(json.dumps({field: safe_json_value(item[field]) for field in fields}, sort_keys=True, separators=(",", ":")))
        return sorted(rows)
    if encoded(left) != encoded(right):
        raise FinalityRedError("acl_semantic_drift")


def compare_observations(left: Any, right: Any, policy: str, *, dynamic_authorized: bool = False, raw_allowed: bool = False) -> dict[str, Any]:
    if policy == "acl_semantic_tuples":
        compare_acl_tuples(left, right)
    elif policy == "paired_positional":
        compare_paired_vectors(left, right)
    elif policy in {"ordered_sequence", "ordered_reference_sequence", "unordered_set", "multiset"}:
        if canonicalize_collection(left, policy) != canonicalize_collection(right, policy):
            raise FinalityRedError("collection_semantic_drift")
    elif policy == "residual_exact":
        if not raw_allowed:
            raise FinalityRedError("residual_exact_not_locked_raw_allowed")
        if safe_json_value(left) != safe_json_value(right):
            raise FinalityRedError("residual_exact_semantic_drift")
    elif policy == "dynamic_scalar":
        if not dynamic_authorized:
            raise FinalityRedError("dynamic_policy_not_authorized")
        return {"equal": left == right, "receipt_type": "SEMANTIC_EQUAL" if left == right else "PERMITTED_DYNAMIC_VARIANCE", "variance": left != right}
    elif policy in {"metadata_descriptor", "exact_typed", "secret_shape", "statistics_shape", "large_object_absence", "deferred_boundary"}:
        if safe_json_value(left) != safe_json_value(right):
            raise FinalityRedError("exact_typed_semantic_drift")
    else:
        raise FinalityRedError("comparison_policy_unknown")
    return {"equal": True, "receipt_type": "SEMANTIC_EQUAL"}


def assert_forbidden_raw_is_not_materialized(field_key: str, value: Any, safety_binding: Mapping[str, Any]) -> None:
    if safety_binding.get("mode") == "FORBIDDEN_RAW" and value is not None:
        raise FinalityRedError("forbidden_raw_value_materialization", field_key)


def compare_reference_receipts(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(receipts) < 3:
        raise FinalityRedError("reference_count_below_minimum")
    required = {"descriptor_digest", "safety_binding_digest", "semantic_binding_digest", "query_plan_digest", "package_digest"}
    if any(not required.issubset(receipt) for receipt in receipts):
        raise FinalityRedError("reference_receipt_authority_incomplete")
    for key in required:
        if len({receipt[key] for receipt in receipts}) != 1:
            raise FinalityRedError("reference_authority_digest_drift", key)
    if any(receipt.get("observation_count", 0) <= 0 for receipt in receipts) or any(receipt.get("forbidden_raw_values_read") is not False for receipt in receipts):
        raise FinalityRedError("reference_receipt_observation_invalid")
    return {"status": "PASS", "reference_count": len(receipts), "descriptor_digest": receipts[0]["descriptor_digest"], "authority_equal": True, "semantic_equality_restored": True}


def public_safe_receipt(receipt: Mapping[str, Any]) -> str:
    text = json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    forbidden = ("password", "credential", "raw_oid", "row_ordinal", "secret")
    lowered = text.lower()
    if any(token in lowered for token in forbidden) or "\"raw_value\":" in lowered:
        raise FinalityRedError("public_receipt_contains_forbidden_material")
    return text
