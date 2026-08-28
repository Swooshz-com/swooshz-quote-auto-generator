#!/usr/bin/env python3
"""Fail-closed A25 runtime capability and PostgreSQL provenance verifier.

The contract binds only the reviewed SQAG application namespace. Git commit
and tree admission belongs to CI/deployment; this module does not copy source
revisions or maintain an implementation digest registry.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (  # noqa: E402
    EXPECTED_CALLABLE_ROUTINE_KEYS,
    EXPECTED_INDEXES,
    EXPECTED_ROUTINES,
    EXPECTED_TABLES,
    EXPECTED_TRIGGER_ROUTINE_KEYS,
    EXPECTED_TRIGGER_ROUTINE_LINKS,
    LEDGER_TABLE,
    MIGRATION_TABLES,
    migration_manifest,
)


CONTRACT_PATH = ROOT / "docs" / "runtime-privilege-contract.json"
SOURCE_SQL_FILES = (
    "webapp/server.py",
    "webapp/forensics.py",
    "scripts/enforce_forensic_retention.py",
)
UNSUPPORTED_SOURCE_RELATIONS = frozenset({"sqag_file_artifacts", "sqag_quote_artifacts"})
SQL_RELATION_RE = re.compile(
    r'\b(?:delete\s+from|from|join|into|update)\s+(?:public\.)?([a-z_][a-z0-9_]*)',
    re.IGNORECASE,
)
DYNAMIC_RELATION_RE = re.compile(
    r'\b(?:delete\s+from|from|join|into|update)\s+\{([a-z_][a-z0-9_]*)\}',
    re.IGNORECASE,
)
UNQUALIFIED_SQL_RELATION_RE = re.compile(
    r'\b(?:delete\s+from|from|join|into|update)\s+(?!public\.)(sqag_[a-z_][a-z0-9_]*)',
    re.IGNORECASE,
)
DATABASE_PRIVILEGES = ("CONNECT", "CREATE", "TEMPORARY")
SCHEMA_PRIVILEGES = ("USAGE", "CREATE")
TABLE_PRIVILEGES = (
    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN",
)
RUNTIME_TABLE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "sqag_profiles": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sqag_pricing_references": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sqag_quote_sessions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sqag_generation_runs": ("SELECT", "INSERT", "UPDATE"),
    "sqag_generation_evidence": ("SELECT", "INSERT"),
    "sqag_audit_events": ("SELECT", "INSERT"),
    "sqag_feedback": ("SELECT", "INSERT", "UPDATE"),
    "sqag_feedback_status_history": ("SELECT", "INSERT"),
    "sqag_object_artifacts": ("SELECT", "INSERT", "UPDATE"),
    "sqag_quote_publication_versions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sqag_quote_publication_artifacts": ("SELECT", "INSERT", "DELETE"),
}
MAINTENANCE_TABLE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "sqag_quote_sessions": ("SELECT", "DELETE"),
    "sqag_object_artifacts": ("SELECT", "UPDATE"),
    "sqag_generation_runs": ("SELECT", "UPDATE", "DELETE"),
    "sqag_generation_evidence": ("SELECT", "DELETE"),
    "sqag_audit_events": ("SELECT", "INSERT", "DELETE"),
    "sqag_feedback": ("SELECT", "UPDATE", "DELETE"),
    "sqag_feedback_status_history": ("SELECT", "DELETE"),
    "sqag_legal_holds": ("SELECT", "INSERT", "UPDATE"),
    "sqag_retention_delete_authorizations": ("SELECT", "INSERT", "DELETE"),
    "sqag_deletion_receipts": ("SELECT", "INSERT", "DELETE"),
    "sqag_retention_scan_cursors": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sqag_quote_publication_versions": ("SELECT", "DELETE"),
    "sqag_quote_publication_artifacts": ("SELECT", "DELETE"),
}
RUNTIME_FORBIDDEN_TABLES = frozenset({
    "sqag_legal_holds", "sqag_retention_delete_authorizations", "sqag_deletion_receipts",
    "sqag_retention_scan_cursors", LEDGER_TABLE,
})
DECLARED_ROLES = ("sqag_runtime", "sqag_migrator", "sqag_maintenance")
PROVIDER_CONTROLLED_MEMBERSHIP_EDGE_KEYS = frozenset({
    "role", "member", "grantor", "admin_option", "inherit_option", "set_option",
})
EXPECTED_PROVIDER_CONTROLLED_PROTECTED_ROLES = (
    "sqag_runtime", "sqag_migrator", "sqag_maintenance",
)
EXPECTED_PROVIDER_CONTROLLED_ALLOWED_EDGES = (
    {
        "role": "sqag_runtime",
        "member": "neondb_owner",
        "grantor": "cloud_admin",
        "admin_option": True,
        "inherit_option": False,
        "set_option": False,
    },
    {
        "role": "sqag_migrator",
        "member": "neondb_owner",
        "grantor": "cloud_admin",
        "admin_option": True,
        "inherit_option": False,
        "set_option": False,
    },
    {
        "role": "sqag_maintenance",
        "member": "neondb_owner",
        "grantor": "cloud_admin",
        "admin_option": True,
        "inherit_option": False,
        "set_option": False,
    },
)
EXPECTED_OWNERSHIP = {
    "database_owner": "neondb_owner",
    "public_schema_owner": "pg_database_owner",
}
RUNTIME_ROLES = ("sqag_runtime", "sqag_maintenance")
EXPECTED_TRIGGER_ROUTINE_KEYS = set(EXPECTED_TRIGGER_ROUTINE_KEYS)
EXPECTED_CALLABLE_ROUTINE_KEYS = set(EXPECTED_CALLABLE_ROUTINE_KEYS)
EXPECTED_ROUTINE_KEYS = EXPECTED_TRIGGER_ROUTINE_KEYS | EXPECTED_CALLABLE_ROUTINE_KEYS
CALLABLE_ROUTINE_NAME = "sqag_quote_session_deletion_hold_blocked"
CALLABLE_ROUTINE_IDENTITY_ARGUMENTS = "text, text"
CALLABLE_ROUTINE_MIGRATION_PATH = "migrations/008_quote_session_deletion_hold_authority_postgres.sql"
CALLABLE_ROUTINE_REFERENCED_RELATIONS = (
    "sqag_audit_events",
    "sqag_feedback",
    "sqag_feedback_status_history",
    "sqag_generation_evidence",
    "sqag_generation_runs",
    "sqag_legal_holds",
    "sqag_quote_publication_versions",
    "sqag_quote_sessions",
)
EXPECTED_CALLABLE_ROUTINE_DOCUMENT = {
    "name": CALLABLE_ROUTINE_NAME,
    "identity_arguments": CALLABLE_ROUTINE_IDENTITY_ARGUMENTS,
    "argument_types": ["text", "text"],
    "result_type": "boolean",
    "language": "sql",
    "owner": "sqag_migrator",
    "security_mode": "definer",
    "volatility": "stable",
    "parallel": "unsafe",
    "leakproof": False,
    "direct_execute": True,
    "trigger_only": False,
    "function_search_path": ["pg_catalog", "public"],
    "schema_qualified_relations": True,
    "referenced_relations": list(CALLABLE_ROUTINE_REFERENCED_RELATIONS),
    "explicit_execute_roles": ["sqag_runtime"],
    "owner_execute": True,
    "public_execute": False,
    "maintenance_execute": False,
    "default_public_execute": False,
    "grant_options": False,
    "fail_closed": True,
}
PRODUCTION_POSTGRES_CALLER_SPECS = {
    "scripts/verify_production_database_provider.py": {
        "storage_roles": ("SQAG_RUNTIME_DATABASE_ROLE",),
        "connection_roles": ("SQAG_RUNTIME_DATABASE_ROLE",),
        "required_source_tokens": (),
        "forbidden_source_tokens": (),
    },
    "scripts/verify_live_retention_delete.py": {
        "storage_roles": ("SQAG_RUNTIME_DATABASE_ROLE", "SQAG_MAINTENANCE_DATABASE_ROLE"),
        "connection_roles": ("SQAG_MIGRATOR_DATABASE_ROLE",),
        "required_source_tokens": (
            "SQAG_DATABASE_URL_ENV_NAME",
            "SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME",
            "SQAG_MIGRATOR_DATABASE_URL_ENV_NAME",
        ),
        "forbidden_source_tokens": ("configured_database_url",),
    },
    "scripts/verify_live_db_object_backup_restore.py": {
        "storage_roles": ("SQAG_RUNTIME_DATABASE_ROLE", "SQAG_MAINTENANCE_DATABASE_ROLE"),
        "connection_roles": (),
        "required_source_tokens": (
            "apply_sqag_storage_migrations",
            "SQAG_MIGRATOR_DATABASE_URL_ENV_NAME",
            "SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME",
            "RESTORE_MIGRATOR_DATABASE_URL_ENV_NAME",
            "RESTORE_MAINTENANCE_DATABASE_URL_ENV_NAME",
        ),
        "forbidden_source_tokens": (),
    },
    "scripts/migrate_inline_draft_files_to_object_storage.py": {
        "storage_roles": ("SQAG_RUNTIME_DATABASE_ROLE",),
        "connection_roles": (),
        "required_source_tokens": (),
        "forbidden_source_tokens": (),
    },
}




class RuntimePrivilegeContractError(RuntimeError):
    """A static or runtime contract observation failed closed."""


ContractError = RuntimePrivilegeContractError


class DuplicateKeyError(RuntimePrivilegeContractError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _rows(connection: Any, sql: str, params: Sequence[Any] = ()) -> list[Any]:
    return list(connection.execute(sql, tuple(params)).fetchall())


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _exact_keys(value: Any, expected: set[str], label: str, errors: list[str]) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{label}:object_required")
        return
    actual = {str(key) for key in value}
    if expected - actual:
        errors.append(f"{label}:missing:{','.join(sorted(expected - actual))}")
    if actual - expected:
        errors.append(f"{label}:unexpected:{','.join(sorted(actual - expected))}")


def _string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{label}:string_list_required")
        return []
    result = list(value)
    if len(result) != len(set(result)):
        errors.append(f"{label}:duplicates")
    return result


def _validate_provider_controlled_memberships(value: Any, errors: list[str]) -> None:
    _exact_keys(
        value,
        {"protected_roles", "allowed_edges"},
        "provider_controlled_memberships",
        errors,
    )
    if not isinstance(value, Mapping):
        return
    protected_roles = _string_list(
        value.get("protected_roles"),
        "provider_controlled_memberships.protected_roles",
        errors,
    )
    if tuple(protected_roles) != EXPECTED_PROVIDER_CONTROLLED_PROTECTED_ROLES:
        errors.append("provider_controlled_memberships.protected_roles:unexpected")
    allowed_edges = value.get("allowed_edges")
    if not isinstance(allowed_edges, list):
        errors.append("provider_controlled_memberships.allowed_edges:list_required")
        return
    actual_edges: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, edge in enumerate(allowed_edges):
        label = f"provider_controlled_memberships.allowed_edges[{index}]"
        _exact_keys(edge, set(PROVIDER_CONTROLLED_MEMBERSHIP_EDGE_KEYS), label, errors)
        if not isinstance(edge, Mapping):
            continue
        for option in ("admin_option", "inherit_option", "set_option"):
            if not isinstance(edge.get(option), bool):
                errors.append(f"{label}.{option}:boolean_required")
        tuple_value = tuple(edge.get(key) for key in (
            "role", "member", "grantor",
            "admin_option", "inherit_option", "set_option",
        ))
        if tuple_value in seen:
            errors.append("provider_controlled_memberships.allowed_edges:duplicates")
        seen.add(tuple_value)
        actual_edges.append(dict(edge))
    if actual_edges != [dict(edge) for edge in EXPECTED_PROVIDER_CONTROLLED_ALLOWED_EDGES]:
        errors.append("provider_controlled_memberships.allowed_edges:unexpected")


def _validate_ownership(value: Any, errors: list[str]) -> None:
    _exact_keys(value, set(EXPECTED_OWNERSHIP), "ownership", errors)
    if isinstance(value, Mapping) and dict(value) != EXPECTED_OWNERSHIP:
        errors.append("ownership:unexpected")


def load_manifest(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePrivilegeContractError(f"contract_unreadable:{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimePrivilegeContractError("contract_root_object_required")
    return value


def _validate_roles(roles: Any, errors: list[str]) -> None:
    _exact_keys(roles, {"runtime", "migrator", "maintenance"}, "roles", errors)
    if not isinstance(roles, Mapping):
        return
    attributes = {
        key: {
            "login": True, "superuser": False, "createdb": False, "createrole": False,
            "replication": False, "bypassrls": False, "inherit": False, "connection_limit": -1,
        }
        for key in ("runtime", "migrator", "maintenance")
    }
    names = {"runtime": "sqag_runtime", "migrator": "sqag_migrator", "maintenance": "sqag_maintenance"}
    role_keys = {"name", "attributes", "memberships_as_member", "owned_objects", "grant_options"}
    for key, name in names.items():
        role = roles.get(key)
        _exact_keys(role, role_keys, f"roles.{key}", errors)
        if not isinstance(role, Mapping):
            continue
        if role.get("name") != name:
            errors.append(f"roles.{key}.name:unexpected")
        attrs = role.get("attributes")
        _exact_keys(attrs, set(attributes[key]), f"roles.{key}.attributes", errors)
        if isinstance(attrs, Mapping) and dict(attrs) != attributes[key]:
            errors.append(f"roles.{key}.attributes:unexpected")
        for field in ("memberships_as_member", "grant_options"):
            items = _string_list(role.get(field), f"roles.{key}.{field}", errors)
            if items:
                errors.append(f"roles.{key}.{field}:must_be_empty")
        if key == "migrator":
            if role.get("owned_objects") != "all_declared_namespace_objects":
                errors.append("roles.migrator.owned_objects:unexpected")
        elif role.get("owned_objects") != []:
            errors.append(f"roles.{key}.owned_objects:must_be_empty")


def _validate_migrations(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("production_migrations:list_required")
        return
    try:
        canonical = migration_manifest(ROOT / "migrations")
    except Exception as exc:
        errors.append(f"production_migrations:source_unreadable:{type(exc).__name__}")
        return
    actual: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            errors.append("production_migrations:item_object_required")
            continue
        _exact_keys(item, {"path", "sequence_no", "sha256", "tables"}, "production_migrations.item", errors)
        actual.append(dict(item))
    expected = [
        {
            "path": f"migrations/{item.migration_id}",
            "sequence_no": item.sequence_no,
            "sha256": item.checksum_sha256,
            "tables": sorted(MIGRATION_TABLES[item.migration_id]),
        }
        for item in canonical
    ]
    if actual != expected:
        errors.append("production_migrations:does_not_match_canonical_manifest")


def _validate_privilege_maps(manifest: Mapping[str, Any], errors: list[str]) -> None:
    expected_db = {
        "public": {"connect": True, "create": False, "temporary": False},
        "runtime": {"connect": True, "create": False, "temporary": False},
        "migrator": {"connect": True, "create": False, "temporary": False},
        "maintenance": {"connect": True, "create": False, "temporary": False},
    }
    expected_schema = {
        "public": {"usage": True, "create": False},
        "runtime": {"usage": True, "create": False},
        "migrator": {"usage": True, "create": True},
        "maintenance": {"usage": True, "create": False},
    }
    for field, expected in (("database_privileges", expected_db), ("schema_privileges", expected_schema)):
        value = manifest.get(field)
        _exact_keys(value, set(expected), field, errors)
        if not isinstance(value, Mapping):
            continue
        keys = set(expected["public"])
        for role, role_expected in expected.items():
            item = value.get(role)
            _exact_keys(item, keys, f"{field}.{role}", errors)
            if isinstance(item, Mapping) and dict(item) != role_expected:
                errors.append(f"{field}.{role}:unexpected")


def _validate_table_map(value: Any, expected: Mapping[str, tuple[str, ...]], label: str, errors: list[str]) -> None:
    _exact_keys(value, set(expected), label, errors)
    if not isinstance(value, Mapping):
        return
    for table, privileges in expected.items():
        item = value.get(table)
        _exact_keys(item, {"privileges"}, f"{label}.{table}", errors)
        if isinstance(item, Mapping) and tuple(_string_list(item.get("privileges"), f"{label}.{table}.privileges", errors)) != privileges:
            errors.append(f"{label}.{table}.privileges:unexpected")


def _validate_callable_routine_source(
    binding: Mapping[str, Any],
    *,
    source_texts: Mapping[str, str] | None,
    errors: list[str],
) -> None:
    source = binding.get("callable_routine_source")
    _exact_keys(
        source,
        {"path", "routine", "allowed_relations"},
        "source_binding.callable_routine_source",
        errors,
    )
    if not isinstance(source, Mapping):
        return
    if source.get("path") != CALLABLE_ROUTINE_MIGRATION_PATH:
        errors.append("source_binding.callable_routine_source.path:unexpected")
    expected_routine = f"public.{CALLABLE_ROUTINE_NAME}({CALLABLE_ROUTINE_IDENTITY_ARGUMENTS})"
    if source.get("routine") != expected_routine:
        errors.append("source_binding.callable_routine_source.routine:unexpected")
    declared_relations = set(_string_list(
        source.get("allowed_relations"),
        "source_binding.callable_routine_source.allowed_relations",
        errors,
    ))
    if declared_relations != set(CALLABLE_ROUTINE_REFERENCED_RELATIONS):
        errors.append("source_binding.callable_routine_source.allowed_relations:unexpected")
    path = str(source.get("path") or "")
    if not path:
        return
    texts = dict(source_texts or {})
    if path not in texts:
        try:
            texts[path] = (ROOT / path).read_text(encoding="utf-8")
        except OSError:
            errors.append(f"source_binding.file_missing:{path}")
            return
    text = texts[path]
    literal = {
        match.group(1).lower()
        for match in SQL_RELATION_RE.finditer(text)
        if match.group(1).lower().startswith("sqag_")
    }
    if literal != set(CALLABLE_ROUTINE_REFERENCED_RELATIONS):
        errors.append("source_binding.callable_routine_source.relations:unexpected")
    if UNQUALIFIED_SQL_RELATION_RE.search(text):
        errors.append("source_binding.callable_routine_source.unqualified_relation")


def validate_source_bindings(manifest: Mapping[str, Any], *, source_texts: Mapping[str, str] | None = None) -> list[str]:
    errors: list[str] = []
    binding = manifest.get("source_binding")
    _exact_keys(binding, {"files", "allowed_sql_relations", "unsupported_sql_relations", "dynamic_sql_variables", "callable_routine_source"}, "source_binding", errors)
    if not isinstance(binding, Mapping):
        return errors
    if tuple(_string_list(binding.get("files"), "source_binding.files", errors)) != SOURCE_SQL_FILES:
        errors.append("source_binding.files:unexpected")
    if set(_string_list(binding.get("allowed_sql_relations"), "source_binding.allowed_sql_relations", errors)) != set(EXPECTED_TABLES):
        errors.append("source_binding.allowed_sql_relations:unexpected")
    if set(_string_list(binding.get("unsupported_sql_relations"), "source_binding.unsupported_sql_relations", errors)) != set(UNSUPPORTED_SOURCE_RELATIONS):
        errors.append("source_binding.unsupported_sql_relations:unexpected")
    dynamic = binding.get("dynamic_sql_variables")
    if not isinstance(dynamic, Mapping):
        errors.append("source_binding.dynamic_sql_variables:object_required")
        dynamic = {}
    texts = dict(source_texts or {})
    for path in SOURCE_SQL_FILES:
        if path not in texts:
            try:
                texts[path] = (ROOT / path).read_text(encoding="utf-8")
            except OSError:
                errors.append(f"source_binding.file_missing:{path}")
                continue
        literal = {m.group(1).lower() for m in SQL_RELATION_RE.finditer(texts[path]) if m.group(1).lower().startswith("sqag_")}
        allowed = set(EXPECTED_TABLES) | set(UNSUPPORTED_SOURCE_RELATIONS)
        unknown = sorted(literal - allowed)
        if unknown:
            errors.append(f"source_binding.unknown_sql_relation:{path}:{','.join(unknown)}")
        found_dynamic = {m.group(1) for m in DYNAMIC_RELATION_RE.finditer(texts[path])}
        declared_dynamic = set(_string_list(dynamic.get(path, []), f"source_binding.dynamic_sql_variables.{path}", errors))
        if found_dynamic - declared_dynamic:
            errors.append(f"source_binding.unbounded_dynamic_sql:{path}")
    _validate_callable_routine_source(binding, source_texts=source_texts, errors=errors)
    return errors


def _validate_manifest_document(manifest: Mapping[str, Any]) -> None:
    errors: list[str] = []
    top = {
        "$schema", "schema_version", "contract_type", "repository", "namespace", "session_authority", "roles", "provider_controlled_memberships", "ownership",
        "production_migrations", "database_privileges", "schema_privileges", "runtime_tables",
        "maintenance_tables", "runtime_forbidden_tables", "source_binding", "observation", "policy",
    }
    _exact_keys(manifest, top, "contract", errors)
    if {"canonical_source_revision", "canonical_source_tree", "implementation_registry", "source_digest", "source_sha256"}.intersection(manifest):
        errors.append("contract:source_identity_or_digest_registry_forbidden")
    if manifest.get("$schema") != "runtime-privilege-contract-schema-v4" or manifest.get("schema_version") != 4:
        errors.append("contract:schema_version_unexpected")
    if manifest.get("contract_type") != "runtime_privilege_contract" or manifest.get("repository") != "Swooshz-com/swooshz-quote-auto-generator":
        errors.append("contract:identity_unexpected")
    namespace = manifest.get("namespace")
    _exact_keys(namespace, {"schema", "search_path", "tables", "indexes", "routines", "callable_routines", "sequences", "views", "materialized_views"}, "namespace", errors)
    if isinstance(namespace, Mapping):
        if namespace.get("schema") != "public" or namespace.get("search_path") != ["public", "pg_catalog"]:
            errors.append("namespace:fixed_public_search_path_required")
        if set(_string_list(namespace.get("tables"), "namespace.tables", errors)) != set(EXPECTED_TABLES) | {LEDGER_TABLE}:
            errors.append("namespace.tables:unexpected")
        if set(_string_list(namespace.get("indexes"), "namespace.indexes", errors)) != set(EXPECTED_INDEXES):
            errors.append("namespace.indexes:unexpected")
        routines = namespace.get("routines")
        if not isinstance(routines, list):
            errors.append("namespace.routines:list_required")
        else:
            keys: set[tuple[str, str]] = set()
            for item in routines:
                if not isinstance(item, Mapping):
                    errors.append("namespace.routines:item_required")
                    continue
                _exact_keys(item, {"name", "identity_arguments", "owner", "security_mode", "direct_execute", "trigger_only"}, "namespace.routines.item", errors)
                keys.add((_clean(item.get("name")), _clean(item.get("identity_arguments"))))
                if item.get("owner") != "sqag_migrator" or item.get("security_mode") != "invoker" or item.get("direct_execute") is not False or item.get("trigger_only") is not True:
                    errors.append("namespace.routines:unsafe_properties")
            if keys != EXPECTED_TRIGGER_ROUTINE_KEYS:
                errors.append("namespace.routines:unexpected")
        callable_routines = namespace.get("callable_routines")
        if not isinstance(callable_routines, list):
            errors.append("namespace.callable_routines:list_required")
        else:
            if len(callable_routines) != 1 or any(
                not isinstance(item, Mapping)
                or dict(item) != EXPECTED_CALLABLE_ROUTINE_DOCUMENT
                for item in callable_routines
            ):
                for item in callable_routines:
                    if isinstance(item, Mapping):
                        _exact_keys(
                            item,
                            set(EXPECTED_CALLABLE_ROUTINE_DOCUMENT),
                            "namespace.callable_routines.item",
                            errors,
                        )
                errors.append("namespace.callable_routines:unexpected")
        for field in ("sequences", "views", "materialized_views"):
            if namespace.get(field) != []:
                errors.append(f"namespace.{field}:must_be_empty")
    session_authority = manifest.get("session_authority")
    _exact_keys(session_authority, {"session_identity_query", "runtime_role", "maintenance_role", "migration_role", "required_before_sql", "expected_role_overrides", "url_username_inference", "set_role_substitution"}, "session_authority", errors)
    if isinstance(session_authority, Mapping) and session_authority != {
        "session_identity_query": "select session_user as session_role, current_user as active_role",
        "runtime_role": "sqag_runtime",
        "maintenance_role": "sqag_maintenance",
        "migration_role": "sqag_migrator",
        "required_before_sql": True,
        "expected_role_overrides": False,
        "url_username_inference": False,
        "set_role_substitution": False,
    }:
        errors.append("session_authority:unsafe")
    _validate_roles(manifest.get("roles"), errors)
    _validate_provider_controlled_memberships(manifest.get("provider_controlled_memberships"), errors)
    _validate_ownership(manifest.get("ownership"), errors)
    _validate_migrations(manifest.get("production_migrations"), errors)
    _validate_privilege_maps(manifest, errors)
    _validate_table_map(manifest.get("runtime_tables"), RUNTIME_TABLE_PRIVILEGES, "runtime_tables", errors)
    _validate_table_map(manifest.get("maintenance_tables"), MAINTENANCE_TABLE_PRIVILEGES, "maintenance_tables", errors)
    forbidden = manifest.get("runtime_forbidden_tables")
    _exact_keys(forbidden, set(RUNTIME_FORBIDDEN_TABLES), "runtime_forbidden_tables", errors)
    if isinstance(forbidden, Mapping):
        for table, item in forbidden.items():
            _exact_keys(item, {"reason"}, f"runtime_forbidden_tables.{table}", errors)
            if not isinstance(item, Mapping) or not isinstance(item.get("reason"), str) or not item["reason"].strip():
                errors.append(f"runtime_forbidden_tables.{table}:reason_required")
    errors.extend(validate_source_bindings(manifest))
    errors.extend(validate_session_authority_source())
    observation = manifest.get("observation")
    _exact_keys(observation, {"allowed", "forbidden"}, "observation", errors)
    if isinstance(observation, Mapping):
        allowed = set(_string_list(observation.get("allowed"), "observation.allowed", errors))
        required = {"server_major", "database_identity", "search_path", "migration_ledger", "named_namespace_metadata", "normalized_acl_provenance", "role_attributes", "bounded_memberships", "effective_privileges", "sanitized_counts"}
        if not required.issubset(allowed):
            errors.append("observation.allowed:missing")
        forbidden_words = ("password", "credential", "token", "customer", "raw_acl")
        if any(any(word in item.lower() for word in forbidden_words) for item in _string_list(observation.get("forbidden"), "observation.forbidden", errors)):
            errors.append("observation.forbidden:unsafe_terms")
    policy = manifest.get("policy")
    _exact_keys(policy, {"fail_closed", "no_source_digest_registry", "unknown_sqag_object_red", "dynamic_sql_requires_allowlist", "supported_server_major"}, "policy", errors)
    if isinstance(policy, Mapping) and (policy.get("fail_closed") is not True or policy.get("no_source_digest_registry") is not True or policy.get("unknown_sqag_object_red") is not True or policy.get("dynamic_sql_requires_allowlist") is not True or policy.get("supported_server_major") != 17):
        errors.append("policy:unsafe")
    if errors:
        raise RuntimePrivilegeContractError(";".join(errors[:60]))



def _ast_call_sites(source_path: Path, callee: str) -> tuple[list[ast.Call], list[str]]:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except (OSError, SyntaxError) as exc:
        return [], [f"source_unreadable:{source_path.name}:{type(exc).__name__}"]
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == callee:
            calls.append(node)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == callee:
            calls.append(node)
    return calls, []


def _fixed_keyword(call: ast.Call, keyword: str, expected_name: str) -> bool:
    value = next((item.value for item in call.keywords if item.arg == keyword), None)
    if isinstance(value, ast.Name):
        return value.id == expected_name
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "webapp"
        and value.attr == expected_name
    )

def _constructor_default_is_required(tree: ast.Module, function_name: str, argument_name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if argument.arg == argument_name:
                    return isinstance(default, ast.Name) and default.id == "_POSTGRES_SESSION_ROLE_REQUIRED"
    return False


def _validate_production_postgres_caller(path: Path, spec: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"production_postgres_source_unreadable:{path.as_posix()}:{type(exc).__name__}"]
    storage_calls, storage_errors = _ast_call_sites(path, "DatabaseSqagStorage")
    connection_calls, connection_errors = _ast_call_sites(path, "postgres_storage_connection")
    errors.extend(storage_errors)
    errors.extend(connection_errors)
    storage_roles = tuple(str(item) for item in spec.get("storage_roles", ()))
    connection_roles = tuple(str(item) for item in spec.get("connection_roles", ()))
    if not storage_calls:
        errors.append(f"production_postgres_storage_call_sites_missing:{path.as_posix()}")
    for call in storage_calls:
        if not any(_fixed_keyword(call, "expected_session_role", role) for role in storage_roles):
            errors.append(f"production_postgres_storage_call_site_unbound:{path.as_posix()}:{call.lineno}")
    if connection_roles and not connection_calls:
        errors.append(f"production_postgres_connection_call_sites_missing:{path.as_posix()}")
    if not connection_roles and connection_calls:
        errors.append(f"production_postgres_connection_call_sites_unexpected:{path.as_posix()}")
    for call in connection_calls:
        if not any(_fixed_keyword(call, "expected_role", role) for role in connection_roles):
            errors.append(f"production_postgres_connection_call_site_unbound:{path.as_posix()}:{call.lineno}")
    for token in tuple(str(item) for item in spec.get("required_source_tokens", ())):
        if token not in source:
            errors.append(f"production_postgres_source_token_missing:{path.as_posix()}:{token}")
    for token in tuple(str(item) for item in spec.get("forbidden_source_tokens", ())):
        if token in source:
            errors.append(f"production_postgres_source_token_forbidden:{path.as_posix()}:{token}")
    return errors


def validate_session_authority_source() -> list[str]:
    """Fail closed if a current PostgreSQL storage call site loses fixed authority."""
    errors: list[str] = []
    for relative_path, spec in PRODUCTION_POSTGRES_CALLER_SPECS.items():
        errors.extend(_validate_production_postgres_caller(ROOT / relative_path, spec))

    server_path = ROOT / "webapp" / "server.py"
    retention_path = ROOT / "scripts" / "enforce_forensic_retention.py"
    server_calls, server_errors = _ast_call_sites(server_path, "DatabaseSqagStorage")
    retention_calls, retention_errors = _ast_call_sites(retention_path, "DatabaseSqagStorage")
    errors.extend(server_errors)
    errors.extend(retention_errors)
    if not server_calls:
        errors.append("runtime_database_storage_call_sites_missing")
    for call in server_calls:
        if not _fixed_keyword(call, "expected_session_role", "SQAG_RUNTIME_DATABASE_ROLE"):
            errors.append(f"runtime_database_storage_call_site_unbound:{call.lineno}")
    if not retention_calls:
        errors.append("maintenance_database_storage_call_site_missing")
    for call in retention_calls:
        if not _fixed_keyword(call, "expected_session_role", "SQAG_MAINTENANCE_DATABASE_ROLE"):
            errors.append(f"maintenance_database_storage_call_site_unbound:{call.lineno}")
    try:
        tree = ast.parse(server_path.read_text(encoding="utf-8"), filename=str(server_path))
    except (OSError, SyntaxError):
        tree = None
    if tree is None or not _constructor_default_is_required(tree, "__init__", "expected_session_role"):
        errors.append("database_storage_constructor_role_default_not_fail_closed")
    if tree is not None:
        connection_calls, connection_errors = _ast_call_sites(server_path, "postgres_storage_connection")
        errors.extend(connection_errors)
        for call in connection_calls:
            if not any(item.arg == "expected_role" for item in call.keywords):
                errors.append(f"postgres_connection_call_site_role_omitted:{call.lineno}")
    return errors

def validate_manifest(manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = dict(manifest) if manifest is not None else load_manifest()
    _validate_manifest_document(value)
    return value


def validate_manifest_strictly(manifest_path: str | Path = CONTRACT_PATH) -> int:
    try:
        validate_manifest(load_manifest(Path(manifest_path)))
    except RuntimePrivilegeContractError:
        return 1
    return 0


def validate_runtime_membership_edges(
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    provider = manifest.get("provider_controlled_memberships")
    if not isinstance(provider, Mapping):
        errors.append("membership:manifest_missing")
        protected_roles = set(DECLARED_ROLES)
        expected_edges: list[Mapping[str, Any]] = []
    else:
        protected_roles = {
            value for value in provider.get("protected_roles", [])
            if isinstance(value, str)
        }
        expected_edges = [
            edge for edge in provider.get("allowed_edges", [])
            if isinstance(edge, Mapping)
        ]
    allowed_participants = protected_roles | {"neondb_owner", "cloud_admin"}
    expected: list[tuple[Any, ...]] = []
    expected_seen: set[tuple[Any, ...]] = set()
    for index, edge in enumerate(expected_edges):
        key = tuple(edge.get(field) for field in (
            "role", "member", "grantor",
            "admin_option", "inherit_option", "set_option",
        ))
        if key in expected_seen:
            errors.append("membership:manifest_duplicate")
        expected_seen.add(key)
        expected.append(key)
        for option in ("admin_option", "inherit_option", "set_option"):
            if not isinstance(edge.get(option), bool):
                errors.append(f"membership:manifest_option_not_boolean:{index}")
    observed: list[tuple[Any, ...]] = []
    seen: set[tuple[Any, ...]] = set()
    fields = ("role", "member", "grantor")
    option_fields = ("admin_option", "inherit_option", "set_option")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append(f"membership:row_object_required:{index}")
            continue
        names: list[str] = []
        for field in fields:
            value = row.get(field)
            if not isinstance(value, str) or not value:
                errors.append(f"membership:field_required:{index}:{field}")
            names.append(_clean(value))
        options: list[bool] = []
        for field in option_fields:
            value = row.get(field)
            if not isinstance(value, bool):
                errors.append(f"membership:option_not_boolean:{index}:{field}")
                value = False
            options.append(value)
        key = (*names, *options)
        if key in seen:
            errors.append("membership:duplicate")
        seen.add(key)
        observed.append(key)
        if any(name not in allowed_participants for name in names):
            errors.append(f"membership:unknown_participant:{names[0]}:{names[1]}")
    expected_set = set(expected)
    observed_set = set(observed)
    for key in sorted(expected_set - observed_set, key=str):
        errors.append(f"membership:missing:{key[0]}:{key[1]}")
    for key in sorted(observed_set - expected_set, key=str):
        errors.append(f"membership:unexpected:{key[0]}:{key[1]}:{key[2]}")
    if len(observed) != len(expected):
        errors.append("membership:count_mismatch")
    return errors


def validate_server_version(server_version_num: int) -> None:
    if not 170000 <= int(server_version_num) < 180000:
        raise RuntimePrivilegeContractError("postgresql_major_mismatch")


def _normalize_search_path(value: Any) -> list[str]:
    return [part.strip().strip('"') for part in str(value or "").split(",") if part.strip()]


def _acl_grantee(row: Any) -> str:
    return _clean(_row_value(row, "grantee")) or "UNKNOWN"


def _acl_privilege(row: Any) -> str:
    return _clean(_row_value(row, "privilege_type")).upper()


def _grantable(row: Any) -> bool:
    return bool(_row_value(row, "is_grantable"))


def _check_role_attributes(connection: Any, manifest: Mapping[str, Any], errors: list[str]) -> None:
    rows = _rows(connection, "select rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls, rolconnlimit from pg_catalog.pg_roles where rolname in (?, ?, ?) order by rolname", DECLARED_ROLES)
    actual = {_clean(_row_value(row, "rolname")): row for row in rows}
    for key, role_name in (("runtime", "sqag_runtime"), ("migrator", "sqag_migrator"), ("maintenance", "sqag_maintenance")):
        row = actual.get(role_name)
        if row is None:
            errors.append(f"role_missing:{role_name}")
            continue
        observed = {
            "login": bool(_row_value(row, "rolcanlogin")), "superuser": bool(_row_value(row, "rolsuper")),
            "createdb": bool(_row_value(row, "rolcreatedb")), "createrole": bool(_row_value(row, "rolcreaterole")),
            "replication": bool(_row_value(row, "rolreplication")), "bypassrls": bool(_row_value(row, "rolbypassrls")),
            "inherit": bool(_row_value(row, "rolinherit")), "connection_limit": int(_row_value(row, "rolconnlimit")),
        }
        if observed != manifest["roles"][key]["attributes"]:
            errors.append(f"role_attributes_mismatch:{role_name}")


def _check_memberships(
    connection: Any,
    manifest: Mapping[str, Any],
    errors: list[str],
) -> None:
    protected_roles = tuple(
        manifest["provider_controlled_memberships"]["protected_roles"]
    )
    placeholders = ", ".join("?" for _ in protected_roles)
    rows = _rows(
        connection,
        "select parent.rolname as role, member.rolname as member, "
        "grantor.rolname as grantor, am.admin_option, am.inherit_option, am.set_option "
        "from pg_catalog.pg_auth_members am "
        "join pg_catalog.pg_roles parent on parent.oid = am.roleid "
        "join pg_catalog.pg_roles member on member.oid = am.member "
        "join pg_catalog.pg_roles grantor on grantor.oid = am.grantor "
        f"where parent.rolname in ({placeholders}) "
        f"or member.rolname in ({placeholders}) "
        f"or grantor.rolname in ({placeholders}) "
        "order by parent.rolname, member.rolname, grantor.rolname",
        protected_roles * 3,
    )
    errors.extend(validate_runtime_membership_edges(manifest, [
        {
            key: _row_value(row, key)
            for key in (
                "role", "member", "grantor",
                "admin_option", "inherit_option", "set_option",
            )
        }
        for row in rows
    ]))


def _check_relation_inventory(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select c.relname, c.relkind, owner.rolname as owner, (c.relkind = 'i' and con.oid is not null) as generated_index from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace join pg_catalog.pg_roles owner on owner.oid = c.relowner left join pg_catalog.pg_index idx on idx.indexrelid = c.oid left join pg_catalog.pg_constraint con on con.conindid = c.oid where n.nspname = 'public' and c.relname like 'sqag_' || chr(37) and c.relkind in ('r', 'i', 'S', 'v', 'm', 'f', 'p') order by c.relname")
    expected = {name: "r" for name in set(EXPECTED_TABLES) | {LEDGER_TABLE}}
    expected.update({name: "i" for name in EXPECTED_INDEXES})
    actual = {_clean(_row_value(row, "relname")): (_clean(_row_value(row, "relkind")), _clean(_row_value(row, "owner"))) for row in rows if not (_clean(_row_value(row, "relkind")) == "i" and bool(_row_value(row, "generated_index")))}
    if set(actual) != set(expected):
        errors.append("namespace_relation_inventory_mismatch")
    for name, kind in expected.items():
        observed = actual.get(name)
        if observed is None:
            errors.append(f"namespace_relation_missing:{name}")
        elif observed != (kind, "sqag_migrator"):
            errors.append(f"namespace_relation_properties_mismatch:{name}")


def _check_container_ownership(
    connection: Any,
    manifest: Mapping[str, Any],
    errors: list[str],
) -> None:
    rows = _rows(
        connection,
        "select 'database' as object_type, d.datname as object_name, "
        "owner.rolname as owner from pg_catalog.pg_database d "
        "join pg_catalog.pg_roles owner on owner.oid = d.datdba "
        "where d.datname = current_database() "
        "union all "
        "select 'schema' as object_type, n.nspname as object_name, "
        "owner.rolname as owner from pg_catalog.pg_namespace n "
        "join pg_catalog.pg_roles owner on owner.oid = n.nspowner "
        "where n.nspname = 'public'",
    )
    expected = {
        "database": manifest["ownership"]["database_owner"],
        "schema": manifest["ownership"]["public_schema_owner"],
    }
    seen: set[str] = set()
    for row in rows:
        object_type = _clean(_row_value(row, "object_type"))
        object_name = _clean(_row_value(row, "object_name"))
        owner = _clean(_row_value(row, "owner"))
        seen.add(object_type)
        if owner != expected.get(object_type):
            errors.append(f"{object_type}_owner_mismatch:{object_name}")
    for object_type in expected:
        if object_type not in seen:
            errors.append(f"{object_type}_owner_missing")


def _check_runtime_maintenance_ownership(connection: Any, errors: list[str]) -> None:
    rows = _rows(
        connection,
        "select 'relation' as object_type, c.relname as object_name, "
        "owner.rolname as owner from pg_catalog.pg_class c "
        "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
        "join pg_catalog.pg_roles owner on owner.oid = c.relowner "
        "where n.nspname = 'public' and owner.rolname in (?, ?) "
        "union all "
        "select 'routine' as object_type, p.proname as object_name, "
        "owner.rolname as owner from pg_catalog.pg_proc p "
        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
        "join pg_catalog.pg_roles owner on owner.oid = p.proowner "
        "where n.nspname = 'public' and owner.rolname in (?, ?)",
        RUNTIME_ROLES * 2,
    )
    for row in rows:
        errors.append(
            "runtime_or_maintenance_object_owned:"
            f"{_clean(_row_value(row, 'object_type'))}:"
            f"{_clean(_row_value(row, 'object_name'))}:"
            f"{_clean(_row_value(row, 'owner'))}"
        )


def _check_complete_public_sqag_routine_inventory(connection: Any, errors: list[str]) -> None:
    routine_rows = _rows(
        connection,
        "select p.oid as function_oid, p.proname, "
        "pg_get_function_identity_arguments(p.oid) as identity_arguments "
        "from pg_catalog.pg_proc p "
        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and left(p.proname, 5) = 'sqag_' "
        "order by p.proname, identity_arguments, p.oid",
    )
    grouped: dict[tuple[str, str], list[Any]] = {}
    for row in routine_rows:
        grouped.setdefault(
            (_clean(_row_value(row, "proname")), _clean(_row_value(row, "identity_arguments"))),
            [],
        ).append(row)
    if set(grouped) != EXPECTED_ROUTINE_KEYS:
        errors.append("routine_inventory_mismatch")

    acl_rows = _rows(
        connection,
        "select p.proname, pg_get_function_identity_arguments(p.oid) as identity_arguments, "
        "case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, "
        "acl.privilege_type, acl.is_grantable "
        "from pg_catalog.pg_proc p "
        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
        "left join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl on true "
        "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 "
        "where n.nspname = 'public' and left(p.proname, 5) = 'sqag_' "
        "order by p.proname, identity_arguments, grantee, acl.privilege_type",
    )
    for row in acl_rows:
        key = (_clean(_row_value(row, "proname")), _clean(_row_value(row, "identity_arguments")))
        expected_grantees = {"sqag_migrator"}
        if key in EXPECTED_CALLABLE_ROUTINE_KEYS:
            expected_grantees.add("sqag_runtime")
        if _acl_grantee(row) not in expected_grantees or _acl_privilege(row) != "EXECUTE":
            errors.append(f"routine_acl_mismatch:{key[0]}")
        if (
            _acl_grantee(row) in {"PUBLIC", "sqag_maintenance"}
            or (_acl_grantee(row) == "sqag_runtime" and key not in EXPECTED_CALLABLE_ROUTINE_KEYS)
            or _grantable(row)
        ):
            errors.append(f"routine_escalation:{key[0]}")

    for role in RUNTIME_ROLES:
        for row in routine_rows:
            routine_name = _clean(_row_value(row, "proname"))
            function_oid = _row_value(row, "function_oid")
            effective_rows = _rows(
                connection,
                "select has_function_privilege(?, ?, 'EXECUTE') as effective",
                (role, function_oid),
            )
            expected = role == "sqag_runtime" and key in EXPECTED_CALLABLE_ROUTINE_KEYS
            observed = any(bool(_row_value(effective_row, "effective")) for effective_row in effective_rows)
            if observed != expected:
                errors.append(f"effective_routine_privilege:{role}:{routine_name}")


def _routine_proconfig(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _check_callable_routine_definition(row: Any, errors: list[str]) -> None:
    definition = _clean(_row_value(row, "function_definition"))
    relations = {
        match.group(1).lower()
        for match in SQL_RELATION_RE.finditer(definition)
        if match.group(1).lower().startswith("sqag_")
    }
    if relations != set(CALLABLE_ROUTINE_REFERENCED_RELATIONS):
        errors.append("callable_routine_relation_inventory_mismatch")
    if UNQUALIFIED_SQL_RELATION_RE.search(definition):
        errors.append("callable_routine_unqualified_relation")


def _check_routines(connection: Any, errors: list[str]) -> None:
    routine_names = sorted({name for name, _identity_arguments in EXPECTED_ROUTINE_KEYS})
    placeholders = ", ".join("?" for _ in routine_names)
    routine_rows = _rows(
        connection,
        "select p.oid as function_oid, n.nspname as schema_name, p.proname, "
        "p.prokind, pg_get_function_identity_arguments(p.oid) as identity_arguments, "
        "pg_get_function_result(p.oid) as result_type, p.prosecdef, "
        "p.provolatile, p.proparallel, p.proleakproof, p.proconfig, "
        "pg_get_functiondef(p.oid) as function_definition, l.lanname as language, "
        "owner.rolname as owner "
        "from pg_catalog.pg_proc p "
        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
        "join pg_catalog.pg_language l on l.oid = p.prolang "
        "join pg_catalog.pg_roles owner on owner.oid = p.proowner "
        f"where n.nspname = 'public' and p.proname in ({placeholders}) "
        "order by p.proname, identity_arguments, p.oid",
        routine_names,
    )
    grouped: dict[tuple[str, str], list[Any]] = {}
    for row in routine_rows:
        grouped.setdefault(
            (_clean(_row_value(row, "proname")), _clean(_row_value(row, "identity_arguments"))),
            [],
        ).append(row)
    if set(grouped) != EXPECTED_ROUTINE_KEYS:
        errors.append("routine_inventory_mismatch")
    routine_oids: dict[tuple[str, str], Any] = {}
    for key in EXPECTED_TRIGGER_ROUTINE_KEYS:
        candidates = grouped.get(key, [])
        if len(candidates) != 1:
            errors.append(f"routine_identity_ambiguous:{key[0]}")
            continue
        row = candidates[0]
        if (
            _clean(_row_value(row, "schema_name")) != "public"
            or _clean(_row_value(row, "prokind")) != "f"
            or _clean(_row_value(row, "identity_arguments"))
            or _clean(_row_value(row, "result_type")).lower() != "trigger"
            or _clean(_row_value(row, "owner")) != "sqag_migrator"
            or bool(_row_value(row, "prosecdef"))
        ):
            errors.append(f"routine_properties_mismatch:{key[0]}")
        routine_oids[key] = _row_value(row, "function_oid")
    for key in EXPECTED_CALLABLE_ROUTINE_KEYS:
        candidates = grouped.get(key, [])
        if len(candidates) != 1:
            errors.append(f"routine_identity_ambiguous:{key[0]}")
            continue
        row = candidates[0]
        if (
            _clean(_row_value(row, "schema_name")) != "public"
            or _clean(_row_value(row, "prokind")) != "f"
            or _clean(_row_value(row, "identity_arguments")) != CALLABLE_ROUTINE_IDENTITY_ARGUMENTS
            or _clean(_row_value(row, "result_type")).lower() != "boolean"
            or _clean(_row_value(row, "language")) != "sql"
            or not bool(_row_value(row, "prosecdef"))
            or _clean(_row_value(row, "owner")) != "sqag_migrator"
            or _clean(_row_value(row, "provolatile")) != "s"
            or _clean(_row_value(row, "proparallel")) != "u"
            or bool(_row_value(row, "proleakproof"))
            or _routine_proconfig(_row_value(row, "proconfig")) != ["search_path=pg_catalog, public"]
        ):
            errors.append(f"callable_routine_properties_mismatch:{key[0]}")
        _check_callable_routine_definition(row, errors)
        routine_oids[key] = _row_value(row, "function_oid")

    trigger_rows = _rows(
        connection,
        "select t.tgname as trigger_name, n.nspname as table_schema, "
        "c.relname as table_name, p.oid as function_oid, p.proname as function_name "
        "from pg_catalog.pg_trigger t "
        "join pg_catalog.pg_class c on c.oid = t.tgrelid "
        "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
        "join pg_catalog.pg_proc p on p.oid = t.tgfoid "
        f"where n.nspname = 'public' and not t.tgisinternal and p.proname in ({placeholders})",
        routine_names,
    )
    expected_links = {
        (routine_name, trigger_name, table_name, routine_oids[(routine_name, "")])
        for routine_name, links in EXPECTED_TRIGGER_ROUTINE_LINKS.items()
        for trigger_name, table_name in links
        if (routine_name, "") in routine_oids
    }
    actual_links = {
        (
            _clean(_row_value(row, "function_name")),
            _clean(_row_value(row, "trigger_name")),
            _clean(_row_value(row, "table_name")),
            _row_value(row, "function_oid"),
        )
        for row in trigger_rows
    }
    if actual_links != expected_links:
        errors.append("routine_trigger_linkage_mismatch")

    acl_rows = _rows(
        connection,
        "select p.proname, pg_get_function_identity_arguments(p.oid) as identity_arguments, "
        "case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, "
        "acl.privilege_type, acl.is_grantable "
        "from pg_catalog.pg_proc p "
        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
        "left join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl on true "
        "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 "
        f"where n.nspname = 'public' and p.proname in ({placeholders}) "
        "order by p.proname, identity_arguments, grantee, acl.privilege_type",
        routine_names,
    )
    for row in acl_rows:
        key = (_clean(_row_value(row, "proname")), _clean(_row_value(row, "identity_arguments")))
        if key not in EXPECTED_ROUTINE_KEYS:
            continue
        expected_grantees = {"sqag_migrator"}
        if key in EXPECTED_CALLABLE_ROUTINE_KEYS:
            expected_grantees.add("sqag_runtime")
        grantee = _acl_grantee(row)
        if grantee not in expected_grantees or _acl_privilege(row) != "EXECUTE":
            errors.append(f"routine_acl_mismatch:{key[0]}")
        if grantee in {"PUBLIC", "sqag_maintenance"} or (
            grantee == "sqag_runtime" and key not in EXPECTED_CALLABLE_ROUTINE_KEYS
        ) or _grantable(row):
            errors.append(f"routine_escalation:{key[0]}")


def _check_table_acls(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select c.relname, owner.rolname as owner, case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace join pg_catalog.pg_roles owner on owner.oid = c.relowner left join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'sqag_' || chr(37) order by c.relname, grantee, acl.privilege_type")
    grouped: dict[str, dict[str, set[str]]] = {}
    owners: dict[str, str] = {}
    grantable: set[tuple[str, str, str]] = set()
    for row in rows:
        table, grantee, privilege = _clean(_row_value(row, "relname")), _acl_grantee(row), _acl_privilege(row)
        owners[table] = _clean(_row_value(row, "owner"))
        grouped.setdefault(table, {}).setdefault(grantee, set()).add(privilege)
        if _grantable(row):
            grantable.add((table, grantee, privilege))
    expected_tables = set(RUNTIME_TABLE_PRIVILEGES) | set(MAINTENANCE_TABLE_PRIVILEGES) | RUNTIME_FORBIDDEN_TABLES
    for table in expected_tables:
        if owners.get(table) != "sqag_migrator":
            errors.append(f"table_acl_owner_mismatch:{table}")
        table_grants = grouped.get(table, {})
        if table_grants.get("PUBLIC"):
            errors.append(f"table_acl_public:{table}")
        if any(grantee not in {"sqag_migrator", "sqag_runtime", "sqag_maintenance", "PUBLIC"} for grantee in table_grants):
            errors.append(f"table_acl_unknown_grantee:{table}")
        if table_grants.get("sqag_runtime", set()) != set(RUNTIME_TABLE_PRIVILEGES.get(table, ())):
            errors.append(f"runtime_direct_privilege_mismatch:{table}")
        if table_grants.get("sqag_maintenance", set()) != set(MAINTENANCE_TABLE_PRIVILEGES.get(table, ())):
            errors.append(f"maintenance_direct_privilege_mismatch:{table}")
        if any(table_name == table and grantee in {"sqag_runtime", "sqag_maintenance"} for table_name, grantee, _ in grantable):
            errors.append(f"table_grant_option:{table}")


def _check_database_schema_acls(
    connection: Any,
    manifest: Mapping[str, Any],
    errors: list[str],
) -> None:
    db_rows = _rows(
        connection,
        "select case when acl.grantee = 0 then 'PUBLIC' else "
        "coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, "
        "acl.privilege_type, acl.is_grantable from pg_catalog.pg_database d "
        "left join lateral aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) acl on true "
        "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee "
        "and acl.grantee <> 0 where d.datname = current_database() "
        "order by grantee, acl.privilege_type",
    )
    schema_rows = _rows(
        connection,
        "select case when acl.grantee = 0 then 'PUBLIC' else "
        "coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, "
        "acl.privilege_type, acl.is_grantable from pg_catalog.pg_namespace n "
        "left join lateral aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) acl on true "
        "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee "
        "and acl.grantee <> 0 where n.nspname = 'public' "
        "order by grantee, acl.privilege_type",
    )
    role_names = {
        key: manifest["roles"][key]["name"]
        for key in ("runtime", "migrator", "maintenance")
    }
    expected = {
        "database": {
            "PUBLIC": {
                privilege
                for privilege in DATABASE_PRIVILEGES
                if manifest["database_privileges"]["public"][privilege.lower()]
            },
            **{
                role_names[key]: {
                    privilege
                    for privilege in DATABASE_PRIVILEGES
                    if manifest["database_privileges"][key][privilege.lower()]
                }
                for key in role_names
            },
        },
        "schema": {
            "PUBLIC": {
                privilege
                for privilege in SCHEMA_PRIVILEGES
                if manifest["schema_privileges"]["public"][privilege.lower()]
            },
            **{
                role_names[key]: {
                    privilege
                    for privilege in SCHEMA_PRIVILEGES
                    if manifest["schema_privileges"][key][privilege.lower()]
                }
                for key in role_names
            },
        },
    }
    owners = {
        "database": manifest["ownership"]["database_owner"],
        "schema": manifest["ownership"]["public_schema_owner"],
    }
    declared = set(role_names.values())
    for label, rows, required in (
        ("database", db_rows, expected["database"]),
        ("schema", schema_rows, expected["schema"]),
    ):
        observed: dict[str, set[str]] = {}
        for row in rows:
            grantee = _acl_grantee(row)
            observed.setdefault(grantee, set()).add(_acl_privilege(row))
            if grantee in declared and _grantable(row):
                errors.append(f"{label}_grant_option:{grantee}")
        for grantee, privileges in required.items():
            if observed.get(grantee, set()) != privileges:
                errors.append(f"{label}_direct_privilege_mismatch:{grantee}")
        allowed_grantees = set(required) | {owners[label]}
        if any(grantee not in allowed_grantees for grantee in observed):
            errors.append(f"{label}_unknown_grantee")


def _check_default_acls(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_default_acl d left join lateral aclexplode(d.defaclacl) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where d.defaclrole in (select oid from pg_catalog.pg_roles where rolname in (?, ?, ?))", DECLARED_ROLES)
    for row in rows:
        grantee = _acl_grantee(row)
        if grantee in {"PUBLIC", "sqag_runtime", "sqag_maintenance"} or (_grantable(row) and grantee != "sqag_migrator"):
            errors.append(f"default_acl_escalation:{grantee}")


def _check_columns(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select n.nspname as schema_name, c.relname as table_name, a.attname as column_name, case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_attribute a join pg_catalog.pg_class c on c.oid = a.attrelid join pg_catalog.pg_namespace n on n.oid = c.relnamespace left join lateral pg_catalog.aclexplode(a.attacl) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' and c.relkind = 'r' and c.relname like 'sqag_' || chr(37) and a.attnum > 0 and not a.attisdropped and a.attacl is not null order by c.relname, a.attname, grantee, acl.privilege_type")
    for row in rows:
        table = _clean(_row_value(row, "table_name"))
        column = _clean(_row_value(row, "column_name"))
        grantee = _acl_grantee(row)
        # The migrator owns the declared namespace. Any explicit column ACL
        # outside that owner authority is outside the locked runtime model.
        if grantee != "sqag_migrator":
            errors.append(f"column_privilege:{table}:{column}:{grantee}")
        if _grantable(row) and grantee != "sqag_migrator":
            errors.append(f"column_grant_option:{table}:{column}:{grantee}")


def _check_effective(
    connection: Any,
    manifest: Mapping[str, Any],
    errors: list[str],
) -> None:
    role_bindings = (
        ("runtime", manifest["roles"]["runtime"]["name"]),
        ("migrator", manifest["roles"]["migrator"]["name"]),
        ("maintenance", manifest["roles"]["maintenance"]["name"]),
    )
    for role_key, role in role_bindings:
        expected_database = manifest["database_privileges"][role_key]
        for privilege in DATABASE_PRIVILEGES:
            expected = bool(expected_database[privilege.lower()])
            row = _rows(
                connection,
                "select has_database_privilege(?, current_database(), ?) as effective, "
                "has_database_privilege(?, current_database(), ?) as grantable",
                (
                    role, privilege,
                    role, f"{privilege} WITH GRANT OPTION",
                ),
            )[0]
            if bool(_row_value(row, "effective")) != expected or bool(_row_value(row, "grantable")):
                errors.append(f"effective_database_privilege:{role}:{privilege}")
    for role in RUNTIME_ROLES:
        for privilege, expected in (("USAGE", True), ("CREATE", False)):
            row = _rows(
                connection,
                "select has_schema_privilege(?, 'public', ?) as effective, "
                "has_schema_privilege(?, 'public', ?) as grantable",
                (role, privilege, role, f"{privilege} WITH GRANT OPTION"),
            )[0]
            if bool(_row_value(row, "effective")) != expected or bool(_row_value(row, "grantable")):
                errors.append(f"effective_schema_privilege:{role}:{privilege}")
        for table in sorted(set(EXPECTED_TABLES) | {LEDGER_TABLE}):
            allowed = (
                set(RUNTIME_TABLE_PRIVILEGES.get(table, ()))
                if role == "sqag_runtime"
                else set(MAINTENANCE_TABLE_PRIVILEGES.get(table, ()))
            )
            for privilege in TABLE_PRIVILEGES:
                qualified = f"public.{table}"
                row = _rows(
                    connection,
                    "select has_table_privilege(?, ?, ?) as effective, "
                    "has_table_privilege(?, ?, ?) as grantable",
                    (
                        role, qualified, privilege,
                        role, qualified, f"{privilege} WITH GRANT OPTION",
                    ),
                )[0]
                if bool(_row_value(row, "effective")) != (privilege in allowed) or bool(_row_value(row, "grantable")):
                    errors.append(f"effective_table_privilege:{role}:{table}:{privilege}")
        for routine_name, identity_arguments in sorted(EXPECTED_ROUTINE_KEYS):
            rows = _rows(
                connection,
                "select has_function_privilege(?, p.oid, 'EXECUTE') as effective "
                "from pg_catalog.pg_proc p "
                "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                "where n.nspname = 'public' and p.proname = ? "
                "and pg_get_function_identity_arguments(p.oid) = ?",
                (role, routine_name, identity_arguments),
            )
            observed = any(bool(_row_value(row, "effective")) for row in rows)
            expected = role == "sqag_runtime" and (
                routine_name,
                identity_arguments,
            ) in EXPECTED_CALLABLE_ROUTINE_KEYS
            if observed != expected:
                errors.append(f"effective_routine_privilege:{role}:{routine_name}")


def verify_postgres_privilege_contract(connection: Any, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    contract = validate_manifest(manifest)
    errors: list[str] = []
    try:
        validate_server_version(int(_row_value(_rows(connection, "select current_setting('server_version_num') as server_version_num")[0], "server_version_num")))
    except (TypeError, ValueError, RuntimePrivilegeContractError):
        errors.append("postgresql_major_mismatch")
    if not _rows(connection, "show search_path") or _normalize_search_path(_row_value(_rows(connection, "show search_path")[0], "search_path", 0)) != ["public", "pg_catalog"]:
        errors.append("search_path_not_fixed")
    _check_role_attributes(connection, contract, errors)
    _check_memberships(connection, contract, errors)
    _check_relation_inventory(connection, errors)
    _check_complete_public_sqag_routine_inventory(connection, errors)
    _check_routines(connection, errors)
    _check_table_acls(connection, errors)
    _check_database_schema_acls(connection, contract, errors)
    _check_default_acls(connection, errors)
    _check_container_ownership(connection, contract, errors)
    _check_runtime_maintenance_ownership(connection, errors)
    _check_columns(connection, errors)
    _check_effective(connection, contract, errors)
    if errors:
        raise RuntimePrivilegeContractError(";".join(errors[:80]))
    return {"status": "verified", "postgres_major": 17, "search_path": ["public", "pg_catalog"], "declared_roles": 3, "declared_tables": len(EXPECTED_TABLES) + 1, "declared_indexes": len(EXPECTED_INDEXES), "declared_routines": len(EXPECTED_ROUTINES), "raw_values_materialized": False}


def validate_migration_report(report: Mapping[str, Any]) -> None:
    if report.get("safeToApply") is not True or report.get("status") != "ready" or report.get("pendingMigrationIds") or report.get("blockers"):
        raise RuntimePrivilegeContractError("migration_state_not_final")


def main() -> int:
    try:
        validate_manifest()
    except RuntimePrivilegeContractError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("OK: A25 runtime privilege contract is structurally valid and source-bound without a digest registry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
