#!/usr/bin/env python3
"""Closed-world, data-bound authority for SQAG A24 PostgreSQL 17.

The package is generated only by an explicit reviewed-universe command. Normal
validation never regenerates, infers, or admits a new catalogue descriptor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_PATH = ROOT / "docs" / "postgresql17-finality-coverage.json"
POLICY_PATH = ROOT / "docs" / "postgresql17-finality-policy-authority.json"
COVERAGE_SCHEMA = "postgresql17-finality-coverage-v1"
POLICY_SCHEMA = "postgresql17-finality-policy-authority-v1"
PACKAGE_VERSION = "a24.1.0"
SAFETY_MODES = frozenset({"RAW_ALLOWED", "DERIVED_ONLY", "METADATA_ONLY", "FORBIDDEN_RAW"})
SEMANTIC_POLICIES = frozenset(
    {
        "metadata_descriptor", "exact_typed", "ordered_sequence", "unordered_set",
        "multiset", "paired_positional", "ordered_reference_sequence",
        "acl_semantic_tuples", "dynamic_scalar", "secret_shape", "statistics_shape",
        "large_object_absence", "deferred_boundary", "residual_exact",
    }
)
IMPLEMENTATION_FILES = (
    "scripts/postgresql17_finality_authority.py",
    "scripts/postgresql17_finality_engine.py",
    "scripts/postgresql17_reference_harness.py",
    "scripts/validate_postgresql17_finality.py",
)


class AuthorityError(RuntimeError):
    """A deterministic, public-safe authority failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def canonical_source_bytes(raw: bytes) -> bytes:
    """Canonicalize source line endings without changing any other bytes."""
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def canonical_source_digest(raw: bytes) -> str:
    return sha256_bytes(canonical_source_bytes(raw))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityError("authority_file_unreadable", path.name) from exc
    if not isinstance(value, dict):
        raise AuthorityError("authority_file_not_object", path.name)
    return value


def descriptor_key(descriptor: dict[str, Any]) -> str:
    relation = descriptor.get("relation_semantic_id")
    attnum = descriptor.get("attnum")
    if not isinstance(relation, str) or not relation or type(attnum) is not int:
        raise AuthorityError("descriptor_locator_invalid")
    return f"{relation}|{attnum}"


def sorted_descriptors(descriptors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(descriptors, key=descriptor_key)


def descriptor_digest(descriptors: Iterable[dict[str, Any]]) -> str:
    return digest_json(sorted_descriptors(descriptors))


def _require_keys(value: Any, expected: set[str], code: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise AuthorityError(code)


def _check_descriptor(descriptor: dict[str, Any]) -> None:
    _require_keys(
        descriptor,
        {
            "relation_semantic_id", "namespace", "relation_name", "relation_kind", "catalogue_scope", "shared",
            "attnum", "attname", "attname_state", "attisdropped", "type_identity", "typmod",
            "collation_identity", "length", "alignment", "storage", "compression", "by_value", "generated",
            "identity", "applicability", "toast_attachment", "descriptor_kind",
        },
        "descriptor_shape_invalid",
    )
    if type(descriptor["attnum"]) is not int:
        raise AuthorityError("descriptor_attnum_invalid")
    dropped = descriptor["attisdropped"] is True
    if descriptor["attisdropped"] is not (descriptor["attname_state"] == "dropped"):
        raise AuthorityError("descriptor_dropped_state_inconsistent")
    if dropped and descriptor["attname"] is not None:
        raise AuthorityError("descriptor_dropped_name_must_be_typed")
    if not dropped and not isinstance(descriptor["attname"], str):
        raise AuthorityError("descriptor_name_missing")
    if descriptor["attnum"] == 0 and descriptor["descriptor_kind"] != "relation":
        raise AuthorityError("relation_descriptor_kind_missing")
    if descriptor["attnum"] != 0 and descriptor["descriptor_kind"] != "attribute":
        raise AuthorityError("attribute_descriptor_kind_missing")
    if descriptor["attnum"] == 0 and descriptor["applicability"] != "RELATION":
        raise AuthorityError("relation_attribute_not_typed")
    if descriptor["attnum"] < 0 and descriptor["applicability"] != "SYSTEM":
        raise AuthorityError("negative_attribute_not_system_typed")
    if dropped and descriptor["applicability"] != "DROPPED":
        raise AuthorityError("dropped_attribute_not_typed")
    if descriptor["applicability"] not in {"RELATION", "VALUE", "SYSTEM", "DROPPED"}:
        raise AuthorityError("descriptor_applicability_invalid")


def validate_coverage(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    _require_keys(
        coverage,
        {
            "$schema", "package_version", "proof_fixture_authority", "supported_compatibility_authority",
            "discovery_scope", "descriptors", "descriptor_count", "descriptor_digest",
        },
        "coverage_top_level_shape_invalid",
    )
    if coverage["$schema"] != COVERAGE_SCHEMA or coverage["package_version"] != PACKAGE_VERSION:
        raise AuthorityError("coverage_schema_or_version_invalid")
    if coverage["supported_compatibility_authority"].get("postgres_major") != 17:
        raise AuthorityError("unsupported_postgresql_major")
    scope = coverage["discovery_scope"]
    if not isinstance(scope, dict) or scope.get("namespaces") != ["pg_catalog", "pg_toast"]:
        raise AuthorityError("discovery_scope_invalid")
    for name in ("include_dropped_attributes", "include_negative_attributes", "include_toast_attachment_metadata"):
        if scope.get(name) is not True:
            raise AuthorityError("discovery_scope_incomplete", name)
    if scope.get("semantic_inference_during_discovery") is not False:
        raise AuthorityError("discovery_semantic_inference_forbidden")
    descriptors = coverage["descriptors"]
    if not isinstance(descriptors, list) or not descriptors:
        raise AuthorityError("coverage_universe_empty")
    if coverage["descriptor_count"] != len(descriptors):
        raise AuthorityError("coverage_descriptor_count_mismatch")
    keys: list[str] = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise AuthorityError("descriptor_not_object")
        _check_descriptor(descriptor)
        keys.append(descriptor_key(descriptor))
    if len(set(keys)) != len(keys) or keys != sorted(keys):
        raise AuthorityError("descriptor_order_or_uniqueness_invalid")
    if coverage["descriptor_digest"] != descriptor_digest(descriptors):
        raise AuthorityError("coverage_descriptor_digest_mismatch")
    return descriptors


def _source_digest(relative_path: str) -> str:
    try:
        return canonical_source_digest((ROOT / relative_path).read_bytes())
    except OSError as exc:
        raise AuthorityError("implementation_source_missing", relative_path) from exc


def _validate_provenance(policy: dict[str, Any]) -> None:
    provenance = policy.get("historical_provenance")
    if not isinstance(provenance, dict):
        raise AuthorityError("historical_provenance_missing")
    if provenance.get("pr169_review_inventory") != {
        "pull_request": 169, "inline_records": 67, "submitted_reviews": 27,
        "conversation_comments": 4, "complete_current_and_outdated": True,
    }:
        raise AuthorityError("pr169_inventory_not_complete")
    mappings = provenance.get("material_finding_mappings")
    if not isinstance(mappings, list):
        raise AuthorityError("historical_finding_mappings_missing")
    seen: set[str] = set()
    for item in mappings:
        if not isinstance(item, dict) or set(item) != {"class", "disposition", "mechanism", "scope"}:
            raise AuthorityError("historical_finding_mapping_shape_invalid")
        if item["class"] in seen:
            raise AuthorityError("duplicate_historical_finding_mapping")
        seen.add(item["class"])
        if item["disposition"] not in {"CLOSED_BY_A24_MECHANISM", "SUPERSEDED_BY_STRONGER_A24_MECHANISM", "OUTSIDE_A24_SCOPE"}:
            raise AuthorityError("historical_finding_disposition_invalid")
        if not isinstance(item["mechanism"], str) or not item["mechanism"] or not isinstance(item["scope"], str) or not item["scope"]:
            raise AuthorityError("historical_finding_mapping_empty")
    if not {"H59", "H60", "H61", "H62", "tgrelid"}.issubset(seen):
        raise AuthorityError("required_historical_classes_unmapped")


def _validate_projection_plan(plan_id: str, plan: Any) -> None:
    if not isinstance(plan, dict) or set(plan) != {"relation_semantic_id", "mode", "sql", "semantic_policy", "value_fields"}:
        raise AuthorityError("projection_plan_shape_invalid", plan_id)
    if plan["mode"] not in {"METADATA_ONLY", "SAFE_SQL", "LARGE_OBJECT_GUARD", "DYNAMIC_WITNESS"}:
        raise AuthorityError("projection_plan_mode_invalid", plan_id)
    if plan["mode"] != "METADATA_ONLY":
        sql = plan["sql"]
        if not isinstance(sql, str) or not sql.strip() or not sql.lstrip().lower().startswith("select"):
            raise AuthorityError("projection_plan_sql_missing", plan_id)
        lowered = sql.lower()
        if ";" in lowered.rstrip().rstrip(";") or re_forbidden_sql(lowered):
            raise AuthorityError("unsafe_projection_plan", plan_id)
    if not isinstance(plan["value_fields"], list) or any(not isinstance(item, str) for item in plan["value_fields"]):
        raise AuthorityError("projection_plan_value_fields_invalid", plan_id)
    if plan["semantic_policy"] not in SEMANTIC_POLICIES:
        raise AuthorityError("projection_plan_semantic_policy_invalid", plan_id)


def re_forbidden_sql(lowered: str) -> bool:
    # Shape predicates such as ``rolpassword IS NULL`` are allowed; selecting
    # the underlying value, wildcard columns, OIDs, or raw large-object data is not.
    if "select *" in lowered or "select\n*" in lowered or ".*" in lowered:
        return True
    if any(token in lowered for token in ("::oid", "::regclass", "pg_largeobject.data", "from pg_largeobject ")):
        return True
    for field in ("rolpassword", "subconninfo", "umoptions", "stavalues1", "stavalues2", "stavalues3", "stavalues4", "stavalues5", "stxdmcv"):
        if f"{field} from" in lowered or f"{field}," in lowered:
            return True
    return False


def _validate_implementation_registry(policy: dict[str, Any]) -> None:
    registry = policy.get("implementation_registry")
    if not isinstance(registry, dict) or set(registry) != {"authority", "engine", "reference_harness", "validator"}:
        raise AuthorityError("implementation_registry_ids_invalid")
    ids = {"authority": IMPLEMENTATION_FILES[0], "engine": IMPLEMENTATION_FILES[1], "reference_harness": IMPLEMENTATION_FILES[2], "validator": IMPLEMENTATION_FILES[3]}
    for implementation_id, entry in registry.items():
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "entrypoint"}:
            raise AuthorityError("implementation_registry_entry_invalid", implementation_id)
        if entry["path"] != ids[implementation_id] or entry["sha256"] != _source_digest(entry["path"]):
            raise AuthorityError("implementation_digest_mismatch", implementation_id)
        if not isinstance(entry["entrypoint"], str) or not entry["entrypoint"]:
            raise AuthorityError("implementation_entrypoint_invalid", implementation_id)


def validate_policy(coverage: dict[str, Any], policy: dict[str, Any], *, verify_sources: bool = True) -> None:
    descriptors = validate_coverage(coverage)
    _require_keys(
        policy,
        {
            "$schema", "package_version", "proof_fixture_authority", "supported_compatibility_authority",
            "production_boundary", "catalogue_universe_authority", "safety_bindings", "semantic_bindings",
            "binding_index", "row_identity_contracts", "safe_projection_plans", "serializer_registry",
            "comparator_registry", "anti_false_requirements", "historical_provenance", "implementation_registry",
            "query_plan_digest", "package_digest",
        },
        "policy_top_level_shape_invalid",
    )
    if policy["$schema"] != POLICY_SCHEMA or policy["package_version"] != PACKAGE_VERSION:
        raise AuthorityError("policy_schema_or_version_invalid")
    if policy["production_boundary"] != "DISPOSABLE_REFERENCE_CI_ONLY" or policy["supported_compatibility_authority"].get("postgres_major") != 17:
        raise AuthorityError("policy_boundary_invalid")
    if policy["proof_fixture_authority"] != coverage["proof_fixture_authority"]:
        raise AuthorityError("proof_fixture_authority_mismatch")
    if policy["catalogue_universe_authority"].get("descriptor_digest") != coverage["descriptor_digest"]:
        raise AuthorityError("policy_universe_digest_mismatch")
    keys = [descriptor_key(item) for item in descriptors]
    key_set = set(keys)
    safety = policy["safety_bindings"]
    semantic = policy["semantic_bindings"]
    index = policy["binding_index"]
    if not isinstance(safety, dict) or set(safety) != key_set or not isinstance(semantic, dict) or set(semantic) != key_set or not isinstance(index, dict) or set(index) != key_set:
        raise AuthorityError("binding_closure_mismatch")
    for key in keys:
        s = safety[key]
        a = semantic[key]
        if not isinstance(s, dict) or set(s) != {"mode", "reason", "raw_value_allowed"} or s["mode"] not in SAFETY_MODES or s["raw_value_allowed"] is not (s["mode"] == "RAW_ALLOWED") or not isinstance(s["reason"], str) or not s["reason"]:
            raise AuthorityError("safety_binding_invalid", key)
        if not isinstance(a, dict) or set(a) != {"policy", "binding_id", "selector"} or a["policy"] not in SEMANTIC_POLICIES or a["selector"] != {"field_key": key} or index[key] != a["binding_id"]:
            raise AuthorityError("semantic_binding_invalid", key)
    contracts = policy["row_identity_contracts"]
    relation_keys = {item["relation_semantic_id"] for item in descriptors}
    if not isinstance(contracts, dict) or set(contracts) != relation_keys:
        raise AuthorityError("row_identity_contract_closure_mismatch")
    for relation, contract in contracts.items():
        if not isinstance(contract, dict) or contract.get("mode") not in {"semantic_key", "object_address", "metadata_boundary", "edge_tuple"}:
            raise AuthorityError("row_identity_contract_invalid", relation)
        if contract["mode"] == "metadata_boundary" and not isinstance(contract.get("typed_receipt"), str):
            raise AuthorityError("metadata_boundary_receipt_missing", relation)
        identity_fields = {name: value for name, value in contract.items() if name != "reason"}
        if any(token in json.dumps(identity_fields, sort_keys=True).lower() for token in ("raw_oid", "ordinal", "definition_text", "value_hash", "content_fingerprint")):
            raise AuthorityError("forbidden_identity_fallback", relation)
    plans = policy["safe_projection_plans"]
    if not isinstance(plans, dict) or not plans:
        raise AuthorityError("safe_projection_plans_missing")
    for plan_id, plan in plans.items():
        _validate_projection_plan(plan_id, plan)
    if policy["query_plan_digest"] != digest_json(plans):
        raise AuthorityError("query_plan_digest_mismatch")
    for registry_name in ("serializer_registry", "comparator_registry"):
        if not isinstance(policy[registry_name], dict) or not policy[registry_name]:
            raise AuthorityError("registry_missing", registry_name)
    for name, entry in policy["serializer_registry"].items():
        if not isinstance(entry, dict) or set(entry) != {"supported_types", "reject_generic_fallback"} or entry["reject_generic_fallback"] is not True:
            raise AuthorityError("serializer_registry_invalid", name)
    for name, entry in policy["comparator_registry"].items():
        if not isinstance(entry, dict) or set(entry) != {"policy", "preserves_order", "preserves_multiplicity"} or entry["policy"] not in SEMANTIC_POLICIES:
            raise AuthorityError("comparator_registry_invalid", name)
    anti_false = policy["anti_false_requirements"]
    if not isinstance(anti_false, list) or len(anti_false) < 38 or len(set(anti_false)) != len(anti_false):
        raise AuthorityError("anti_false_matrix_incomplete")
    if verify_sources:
        _validate_implementation_registry(policy)
    _validate_provenance(policy)
    if policy["package_digest"] != digest_json({k: v for k, v in policy.items() if k != "package_digest"}):
        raise AuthorityError("package_digest_mismatch")


def load_authority_package(*, verify_sources: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage = load_json(COVERAGE_PATH)
    policy = load_json(POLICY_PATH)
    validate_policy(coverage, policy, verify_sources=verify_sources)
    return coverage, policy


def validate_closed_world(discovered: Iterable[dict[str, Any]], coverage: dict[str, Any], policy: dict[str, Any], observed_keys: Iterable[str] | None = None) -> None:
    discovered_keys = [descriptor_key(item) for item in sorted_descriptors(discovered)]
    locked_keys = [descriptor_key(item) for item in coverage["descriptors"]]
    if discovered_keys != locked_keys:
        raise AuthorityError("descriptor_universe_drift", f"missing={len(set(locked_keys) - set(discovered_keys))} unexpected={len(set(discovered_keys) - set(locked_keys))}")
    if set(policy["safety_bindings"]) != set(discovered_keys) or set(policy["semantic_bindings"]) != set(discovered_keys):
        raise AuthorityError("binding_closure_mismatch")
    if observed_keys is not None:
        observed = list(observed_keys)
        if len(observed) != len(set(observed)) or set(observed) != set(discovered_keys):
            raise AuthorityError("observation_closure_mismatch")


def _selector_matches(descriptor: dict[str, Any], selector: dict[str, Any]) -> bool:
    if not isinstance(selector, dict) or not selector:
        return False
    allowed = {"namespace", "relation_name", "attnum", "attname"}
    if set(selector) - allowed:
        raise AuthorityError("generation_selector_unknown_key")
    return all(descriptor.get(name) == value for name, value in selector.items())


def regenerate_universe(connection: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if os.environ.get("SQAG_A24_REVIEWED_UNIVERSE_REGENERATION") != "1":
        raise AuthorityError("universe_regeneration_requires_explicit_reviewed_flag")
    from postgresql17_finality_engine import discover_descriptors

    seed = load_json(POLICY_PATH)
    descriptors = sorted_descriptors(discover_descriptors(connection))
    proof = seed.get("proof_fixture_authority")
    coverage = {
        "$schema": COVERAGE_SCHEMA,
        "package_version": PACKAGE_VERSION,
        "proof_fixture_authority": proof,
        "supported_compatibility_authority": {"postgres_major": 17, "admitted_profile": "postgresql-17-disposable-ci"},
        "discovery_scope": {
            "namespaces": ["pg_catalog", "pg_toast"], "include_shared_catalogues": True, "include_local_catalogues": True,
            "include_dropped_attributes": True, "include_negative_attributes": True, "include_toast_attachment_metadata": True,
            "semantic_inference_during_discovery": False,
        },
        "descriptors": descriptors, "descriptor_count": len(descriptors), "descriptor_digest": descriptor_digest(descriptors),
    }
    seed_body = seed.get("generation_seed")
    if not isinstance(seed_body, dict):
        # A generated package is itself the reviewed seed for the next
        # explicitly approved package-evolution run. Normal validation never
        # enters this branch and never writes either authority file.
        previous_coverage = load_json(COVERAGE_PATH)
        previous_descriptors = previous_coverage.get("descriptors", [])
        previous_safety = seed.get("safety_bindings", {})
        previous_semantic = seed.get("semantic_bindings", {})
        recovered_overrides: list[dict[str, Any]] = []
        for previous_descriptor in previous_descriptors:
            previous_key = descriptor_key(previous_descriptor)
            safety_item = previous_safety.get(previous_key, {})
            semantic_item = previous_semantic.get(previous_key, {})
            if safety_item.get("mode") != "METADATA_ONLY" or semantic_item.get("policy") != "metadata_descriptor":
                recovered_overrides.append({
                    "selector": {"namespace": previous_descriptor["namespace"], "relation_name": previous_descriptor["relation_name"], "attnum": previous_descriptor["attnum"]},
                    "mode": safety_item.get("mode"), "policy": semantic_item.get("policy"), "reason": safety_item.get("reason", "reviewed binding"),
                })
        contracts = seed.get("row_identity_contracts", {})
        default_contract = next((value for value in contracts.values() if isinstance(value, dict)), None)
        if not isinstance(default_contract, dict):
            raise AuthorityError("default_relation_contract_missing")
        seed_body = {
            "field_overrides": recovered_overrides,
            "row_identity_contracts": {},
            "default_relation_contract": default_contract,
            "safe_projection_plans": seed.get("safe_projection_plans", {}),
            "serializer_registry": seed.get("serializer_registry", {}),
            "comparator_registry": seed.get("comparator_registry", {}),
            "anti_false_requirements": seed.get("anti_false_requirements", []),
            "historical_provenance": seed.get("historical_provenance", {}),
        }
    overrides = seed_body.get("field_overrides", [])
    if not isinstance(overrides, list):
        raise AuthorityError("generation_seed_overrides_invalid")
    safety: dict[str, Any] = {}
    semantic: dict[str, Any] = {}
    binding_index: dict[str, str] = {}
    for descriptor in descriptors:
        key = descriptor_key(descriptor)
        matches = [item for item in overrides if _selector_matches(descriptor, item.get("selector", {}))]
        if len(matches) > 1:
            raise AuthorityError("overlapping_generation_overrides", key)
        selected = matches[0] if matches else {}
        mode = selected.get("mode", "METADATA_ONLY")
        sem = selected.get("policy", "metadata_descriptor")
        if mode not in SAFETY_MODES or sem not in SEMANTIC_POLICIES:
            raise AuthorityError("generation_override_invalid", key)
        safety[key] = {"mode": mode, "reason": selected.get("reason", "reviewed metadata-only boundary; no raw catalogue value is collected"), "raw_value_allowed": mode == "RAW_ALLOWED"}
        binding_id = f"bind:{digest_json({'field_key': key, 'policy': sem, 'mode': mode})[:32]}"
        semantic[key] = {"policy": sem, "binding_id": binding_id, "selector": {"field_key": key}}
        binding_index[key] = binding_id
    relation_ids = {descriptor["relation_semantic_id"] for descriptor in descriptors}
    contracts = dict(seed_body.get("row_identity_contracts", {}))
    default_contract = seed_body.get("default_relation_contract")
    if not isinstance(default_contract, dict):
        raise AuthorityError("default_relation_contract_missing")
    for relation in relation_ids:
        contracts.setdefault(relation, dict(default_contract))
    generated = {
        "$schema": POLICY_SCHEMA, "package_version": PACKAGE_VERSION, "proof_fixture_authority": proof,
        "supported_compatibility_authority": {"postgres_major": 17, "admitted_profile": "postgresql-17-disposable-ci"},
        "production_boundary": "DISPOSABLE_REFERENCE_CI_ONLY",
        "catalogue_universe_authority": {"coverage_file": "docs/postgresql17-finality-coverage.json", "descriptor_digest": coverage["descriptor_digest"], "regeneration": "separate-reviewed-package-evolution-only"},
        "safety_bindings": safety, "semantic_bindings": semantic, "binding_index": binding_index,
        "row_identity_contracts": contracts, "safe_projection_plans": seed_body.get("safe_projection_plans", {}),
        "serializer_registry": seed_body.get("serializer_registry", {}), "comparator_registry": seed_body.get("comparator_registry", {}),
        "anti_false_requirements": seed_body.get("anti_false_requirements", []), "historical_provenance": seed_body.get("historical_provenance", {}),
        "implementation_registry": {
            "authority": {"path": IMPLEMENTATION_FILES[0], "sha256": _source_digest(IMPLEMENTATION_FILES[0]), "entrypoint": "load_authority_package"},
            "engine": {"path": IMPLEMENTATION_FILES[1], "sha256": _source_digest(IMPLEMENTATION_FILES[1]), "entrypoint": "collect_reference"},
            "reference_harness": {"path": IMPLEMENTATION_FILES[2], "sha256": _source_digest(IMPLEMENTATION_FILES[2]), "entrypoint": "run_real_references"},
            "validator": {"path": IMPLEMENTATION_FILES[3], "sha256": _source_digest(IMPLEMENTATION_FILES[3]), "entrypoint": "main"},
        },
        "query_plan_digest": digest_json(seed_body.get("safe_projection_plans", {})),
    }
    generated["package_digest"] = digest_json(generated)
    validate_policy(coverage, generated)
    COVERAGE_PATH.write_bytes(canonical_json(coverage))
    POLICY_PATH.write_bytes(canonical_json(generated))
    return coverage, generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate-universe", action="store_true")
    parser.add_argument("--dsn", default=os.environ.get("SQAG_A24_TEST_DSN", ""))
    args = parser.parse_args(argv)
    try:
        if args.regenerate_universe:
            if not args.dsn:
                raise AuthorityError("regeneration_dsn_missing")
            import psycopg
            with psycopg.connect(args.dsn, autocommit=True) as connection:
                regenerate_universe(connection)
            print("OK: reviewed PostgreSQL 17 authority package regenerated")
        else:
            load_authority_package()
            print("OK: PostgreSQL 17 authority package passes closed-world validation")
        return 0
    except AuthorityError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("FAIL: authority execution failed", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
