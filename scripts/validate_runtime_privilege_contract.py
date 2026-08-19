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
    EXPECTED_INDEXES,
    EXPECTED_ROUTINES,
    EXPECTED_TABLES,
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
RUNTIME_ROLES = ("sqag_runtime", "sqag_maintenance")
EXPECTED_ROUTINE_KEYS = {
    ("sqag_reject_immutable_change", ""),
    ("sqag_require_retention_delete_authorization", ""),
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


def validate_source_bindings(manifest: Mapping[str, Any], *, source_texts: Mapping[str, str] | None = None) -> list[str]:
    errors: list[str] = []
    binding = manifest.get("source_binding")
    _exact_keys(binding, {"files", "allowed_sql_relations", "unsupported_sql_relations", "dynamic_sql_variables"}, "source_binding", errors)
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
    return errors


def _validate_manifest_document(manifest: Mapping[str, Any]) -> None:
    errors: list[str] = []
    top = {
        "$schema", "schema_version", "contract_type", "repository", "namespace", "session_authority", "roles",
        "production_migrations", "database_privileges", "schema_privileges", "runtime_tables",
        "maintenance_tables", "runtime_forbidden_tables", "source_binding", "observation", "policy",
    }
    _exact_keys(manifest, top, "contract", errors)
    if {"canonical_source_revision", "canonical_source_tree", "implementation_registry", "source_digest", "source_sha256"}.intersection(manifest):
        errors.append("contract:source_identity_or_digest_registry_forbidden")
    if manifest.get("$schema") != "runtime-privilege-contract-schema-v2" or manifest.get("schema_version") != 2:
        errors.append("contract:schema_version_unexpected")
    if manifest.get("contract_type") != "runtime_privilege_contract" or manifest.get("repository") != "Swooshz-com/swooshz-quote-auto-generator":
        errors.append("contract:identity_unexpected")
    namespace = manifest.get("namespace")
    _exact_keys(namespace, {"schema", "search_path", "tables", "indexes", "routines", "sequences", "views", "materialized_views"}, "namespace", errors)
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
            if keys != EXPECTED_ROUTINE_KEYS:
                errors.append("namespace.routines:unexpected")
        for field in ("sequences", "views", "materialized_views"):
            if namespace.get(field) != []:
                errors.append(f"namespace.{field}:must_be_empty")
    session_authority = manifest.get("session_authority")
    _exact_keys(session_authority, {"current_user_query", "runtime_role", "maintenance_role", "migration_role", "required_before_sql", "expected_role_overrides", "url_username_inference", "set_role_substitution"}, "session_authority", errors)
    if isinstance(session_authority, Mapping) and session_authority != {
        "current_user_query": "select current_user as role",
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


def validate_session_authority_source() -> list[str]:
    """Fail closed if a current PostgreSQL storage call site loses fixed authority."""
    errors: list[str] = []
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


def validate_runtime_membership_edges(manifest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[str]:
    declared = set(DECLARED_ROLES)
    errors: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        role, member, grantor = (_clean(row.get(key)) for key in ("role", "member", "grantor"))
        key = (role, member, grantor, bool(row.get("admin_option")), bool(row.get("inherit_option")), bool(row.get("set_option")))
        if key in seen:
            errors.append("membership:duplicate")
        seen.add(key)
        if role in declared or member in declared or grantor in declared:
            errors.append(f"membership:unexpected:{role}:{member}")
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


def _check_memberships(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select parent.rolname as role, member.rolname as member, grantor.rolname as grantor, am.admin_option, am.inherit_option, am.set_option from pg_catalog.pg_auth_members am join pg_catalog.pg_roles parent on parent.oid = am.roleid join pg_catalog.pg_roles member on member.oid = am.member join pg_catalog.pg_roles grantor on grantor.oid = am.grantor where parent.rolname in (?, ?, ?) or member.rolname in (?, ?, ?) or grantor.rolname in (?, ?, ?) order by parent.rolname, member.rolname, grantor.rolname", DECLARED_ROLES * 3)
    errors.extend(validate_runtime_membership_edges({}, [
        {key: _row_value(row, key) for key in ("role", "member", "grantor", "admin_option", "inherit_option", "set_option")}
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


def _check_container_ownership(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select 'database' as object_type, d.datname as object_name, owner.rolname as owner from pg_catalog.pg_database d join pg_catalog.pg_roles owner on owner.oid = d.datdba where d.datname = current_database() union all select 'schema' as object_type, n.nspname as object_name, owner.rolname as owner from pg_catalog.pg_namespace n join pg_catalog.pg_roles owner on owner.oid = n.nspowner where n.nspname = 'public'")
    for row in rows:
        object_type = _clean(_row_value(row, "object_type"))
        object_name = _clean(_row_value(row, "object_name"))
        owner = _clean(_row_value(row, "owner"))
        if owner in {"sqag_runtime", "sqag_maintenance", "sqag_migrator"}:
            errors.append(f"{object_type}_owner_is_declared_runtime_role:{object_name}")


def _check_routines(connection: Any, errors: list[str]) -> None:
    rows = _rows(connection, "select p.proname, pg_get_function_identity_arguments(p.oid) as identity_arguments, p.prosecdef, owner.rolname as owner, exists (select 1 from pg_catalog.pg_trigger t where t.tgfoid = p.oid and not t.tgisinternal) as has_trigger_dependency, case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid = p.pronamespace join pg_catalog.pg_roles owner on owner.oid = p.proowner left join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' and p.prokind in ('f', 'p', 'a', 'w') order by p.proname, identity_arguments, grantee, acl.privilege_type")
    grouped: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        grouped.setdefault((_clean(_row_value(row, "proname")), _clean(_row_value(row, "identity_arguments"))), []).append(row)
    if set(grouped) != EXPECTED_ROUTINE_KEYS:
        errors.append("routine_inventory_mismatch")
    for key in EXPECTED_ROUTINE_KEYS:
        for row in grouped.get(key, []):
            if _clean(_row_value(row, "owner")) != "sqag_migrator" or bool(_row_value(row, "prosecdef")) or not bool(_row_value(row, "has_trigger_dependency")):
                errors.append(f"routine_properties_mismatch:{key[0]}")
            if _acl_grantee(row) != "sqag_migrator" or _acl_privilege(row) != "EXECUTE":
                errors.append(f"routine_acl_mismatch:{key[0]}")
            if _acl_grantee(row) in {"PUBLIC", "sqag_runtime", "sqag_maintenance"} or (_grantable(row) and _acl_grantee(row) != "sqag_migrator"):
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


def _check_database_schema_acls(connection: Any, errors: list[str]) -> None:
    db_rows = _rows(connection, "select case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_database d left join lateral aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where d.datname = current_database() order by grantee, acl.privilege_type")
    schema_rows = _rows(connection, "select case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, acl.privilege_type, acl.is_grantable from pg_catalog.pg_namespace n left join lateral aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) acl on true left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 where n.nspname = 'public' order by grantee, acl.privilege_type")
    expected = {
        "database": {"PUBLIC": {"CONNECT"}, "sqag_runtime": {"CONNECT"}, "sqag_migrator": {"CONNECT"}, "sqag_maintenance": {"CONNECT"}},
        "schema": {"PUBLIC": {"USAGE"}, "sqag_runtime": {"USAGE"}, "sqag_migrator": {"USAGE", "CREATE"}, "sqag_maintenance": {"USAGE"}},
    }
    for label, rows, required in (("database", db_rows, expected["database"]), ("schema", schema_rows, expected["schema"])):
        observed: dict[str, set[str]] = {}
        for row in rows:
            grantee = _acl_grantee(row)
            observed.setdefault(grantee, set()).add(_acl_privilege(row))
            if grantee in {"sqag_runtime", "sqag_maintenance"} and _grantable(row):
                errors.append(f"{label}_grant_option:{grantee}")
        for grantee, privileges in required.items():
            if observed.get(grantee, set()) != privileges:
                errors.append(f"{label}_direct_privilege_mismatch:{grantee}")
        if any(grantee not in set(required) | {"postgres", "pg_database_owner", "sqag_database_owner", "UNKNOWN"} for grantee in observed):
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


def _check_effective(connection: Any, errors: list[str]) -> None:
    for role in RUNTIME_ROLES:
        for privilege, expected in (("CONNECT", True), ("CREATE", False), ("TEMPORARY", False)):
            row = _rows(connection, "select has_database_privilege(?, current_database(), ?) as effective, has_database_privilege(?, current_database(), ?) as grantable", (role, privilege, role, f"{privilege} WITH GRANT OPTION"))[0]
            if bool(_row_value(row, "effective")) != expected or bool(_row_value(row, "grantable")):
                errors.append(f"effective_database_privilege:{role}:{privilege}")
        for privilege, expected in (("USAGE", True), ("CREATE", False)):
            row = _rows(connection, "select has_schema_privilege(?, 'public', ?) as effective, has_schema_privilege(?, 'public', ?) as grantable", (role, privilege, role, f"{privilege} WITH GRANT OPTION"))[0]
            if bool(_row_value(row, "effective")) != expected or bool(_row_value(row, "grantable")):
                errors.append(f"effective_schema_privilege:{role}:{privilege}")
        for table in sorted(set(EXPECTED_TABLES) | {LEDGER_TABLE}):
            allowed = set(RUNTIME_TABLE_PRIVILEGES.get(table, ())) if role == "sqag_runtime" else set(MAINTENANCE_TABLE_PRIVILEGES.get(table, ()))
            for privilege in TABLE_PRIVILEGES:
                qualified = f"public.{table}"
                row = _rows(connection, "select has_table_privilege(?, ?, ?) as effective, has_table_privilege(?, ?, ?) as grantable", (role, qualified, privilege, role, qualified, f"{privilege} WITH GRANT OPTION"))[0]
                if bool(_row_value(row, "effective")) != (privilege in allowed) or bool(_row_value(row, "grantable")):
                    errors.append(f"effective_table_privilege:{role}:{table}:{privilege}")
        for routine_name, identity_arguments in sorted(EXPECTED_ROUTINE_KEYS):
            rows = _rows(connection, "select has_function_privilege(?, p.oid, 'EXECUTE') as effective from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = ? and pg_get_function_identity_arguments(p.oid) = ?", (role, routine_name, identity_arguments))
            if any(bool(_row_value(row, "effective")) for row in rows):
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
    _check_memberships(connection, errors)
    _check_relation_inventory(connection, errors)
    _check_routines(connection, errors)
    _check_table_acls(connection, errors)
    _check_database_schema_acls(connection, errors)
    _check_default_acls(connection, errors)
    _check_container_ownership(connection, errors)
    _check_columns(connection, errors)
    _check_effective(connection, errors)
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
