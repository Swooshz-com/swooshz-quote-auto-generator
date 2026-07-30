#!/usr/bin/env python3
"""Validate the runtime privilege contract manifest against repository authority."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (  # noqa: E402
    EXPECTED_ROUTINES,
    EXPECTED_TABLES,
    MIGRATION_FILE_NAMES,
    MIGRATION_TABLES,
    migration_manifest,
)

MANIFEST_PATH = ROOT / "docs" / "runtime-privilege-contract.json"

TOP_LEVEL_KEYS = frozenset(
    {
        "$schema",
        "schema_version",
        "contract_type",
        "repository",
        "canonical_source_revision",
        "canonical_source_tree",
        "roles",
        "production_migrations",
        "database_acl",
        "schema_acl",
        "tables",
        "sequences",
        "routines",
        "default_privileges",
        "verification_queries",
    }
)

ROLE_KEYS = frozenset({"runtime", "migrator", "legacy", "provider", "forbidden"})
RUNTIME_ROLE_KEYS = frozenset(
    {"name", "description", "attributes", "memberships", "ownership", "grant_options"}
)
MIGRATOR_ROLE_KEYS = frozenset({"name", "description", "can_create_roles"})
LEGACY_ROLE_KEYS = frozenset({"name", "description", "status"})
PROVIDER_ROLE_KEYS = frozenset({"name", "description", "status"})
RUNTIME_ROLE_ATTRIBUTE_KEYS = frozenset(
    {
        "login",
        "password",
        "superuser",
        "createdb",
        "createrole",
        "replication",
        "bypassrls",
        "inherit",
        "connection_limit",
    }
)

MIGRATION_KEYS = frozenset({"path", "sequence_no", "sha256", "tables"})
DATABASE_ACL_KEYS = frozenset({"public", "sqag_migrator", "sqag_app", "sqag_runtime"})
DATABASE_PUBLIC_KEYS = frozenset({"connect", "temporary", "create"})
DATABASE_MIGRATOR_KEYS = frozenset({"connect", "create", "temporary"})
DATABASE_APP_KEYS = frozenset({"connect"})
DATABASE_RUNTIME_KEYS = frozenset({"connect", "create", "temporary"})
SCHEMA_ACL_KEYS = frozenset(
    {"schema_name", "public", "pg_database_owner", "sqag_app", "sqag_runtime"}
)
SCHEMA_PUBLIC_KEYS = frozenset({"usage"})
SCHEMA_OWNER_KEYS = frozenset({"create", "usage"})
SCHEMA_APP_KEYS = frozenset({"usage"})
SCHEMA_RUNTIME_KEYS = frozenset({"usage", "create"})

TABLES_KEYS = frozenset(
    {"rw_count", "forbidden_count", "total_count", "runtime_accessible", "runtime_forbidden"}
)
ACCESSIBLE_TABLE_KEYS = frozenset({"class", "schema", "privileges"})
FORBIDDEN_TABLE_KEYS = frozenset({"class", "schema", "reason"})
PRIVILEGE_KEYS = frozenset({"select", "insert", "update", "delete"})

SEQUENCE_KEYS = frozenset({"user_defined_public_count", "runtime_privileges", "rule"})
ROUTINES_KEYS = frozenset(
    {"sqag_owned_triggers", "sqag_owned_count", "provider_owned_exceptions", "total_count", "rule"}
)
TRIGGER_ROUTINE_KEYS = frozenset(
    {
        "schema",
        "owner",
        "security_mode",
        "class",
        "direct_runtime_execute",
        "public_execute_after_boundary_b",
    }
)
PROVIDER_EXCEPTION_KEYS = frozenset(
    {
        "schema",
        "owner",
        "class",
        "direct_runtime_grant",
        "public_execute",
        "effective_runtime_execution",
    }
)
DEFAULT_PRIVILEGES_KEYS = frozenset(
    {"sqag_runtime", "sqag_migrator_to_sqag_app", "provider_controlled", "verification_rule"}
)
RUNTIME_DEFAULT_KEYS = frozenset({"tables", "sequences", "routines"})
VERIFICATION_QUERY_KEYS = frozenset(
    {
        "database_acl",
        "schema_acl",
        "table_acl",
        "routine_acl",
        "default_acl",
        "role_attributes",
        "role_memberships",
        "sequence_acl",
        "effective_runtime_table_privileges",
        "effective_runtime_schema_privileges",
        "effective_runtime_routine_privileges",
    }
)

RUNTIME_TABLES = frozenset(
    {
        "sqag_profiles",
        "sqag_pricing_references",
        "sqag_quote_sessions",
        "sqag_generation_runs",
        "sqag_generation_evidence",
        "sqag_audit_events",
        "sqag_feedback",
        "sqag_feedback_status_history",
        "sqag_object_artifacts",
        "sqag_quote_publication_versions",
        "sqag_quote_publication_artifacts",
    }
)
FORBIDDEN_TABLES = frozenset(
    {
        "sqag_legal_holds",
        "sqag_retention_delete_authorizations",
        "sqag_deletion_receipts",
        "sqag_retention_scan_cursors",
        "sqag_schema_migrations",
    }
)
ALL_TABLES = RUNTIME_TABLES | FORBIDDEN_TABLES

LOCKED_PRIVILEGE_MATRIX: dict[str, dict[str, bool]] = {
    "sqag_profiles": {"select": True, "insert": True, "update": True, "delete": True},
    "sqag_pricing_references": {"select": True, "insert": True, "update": True, "delete": True},
    "sqag_quote_sessions": {"select": True, "insert": True, "update": True, "delete": True},
    "sqag_generation_runs": {"select": True, "insert": True, "update": True, "delete": False},
    "sqag_generation_evidence": {"select": True, "insert": True, "update": False, "delete": False},
    "sqag_audit_events": {"select": True, "insert": True, "update": False, "delete": False},
    "sqag_feedback": {"select": True, "insert": True, "update": True, "delete": False},
    "sqag_feedback_status_history": {"select": True, "insert": True, "update": False, "delete": False},
    "sqag_object_artifacts": {"select": True, "insert": True, "update": True, "delete": False},
    "sqag_quote_publication_versions": {"select": True, "insert": True, "update": True, "delete": True},
    "sqag_quote_publication_artifacts": {"select": True, "insert": True, "update": False, "delete": True},
}

ACCESSIBLE_TABLE_CLASSES = {
    "sqag_profiles": "mutable",
    "sqag_pricing_references": "mutable",
    "sqag_quote_sessions": "mutable",
    "sqag_generation_runs": "append_and_status",
    "sqag_generation_evidence": "immutable_evidence",
    "sqag_audit_events": "immutable_audit",
    "sqag_feedback": "mutable_feedback",
    "sqag_feedback_status_history": "append_only_history",
    "sqag_object_artifacts": "mutable_metadata",
    "sqag_quote_publication_versions": "mutable",
    "sqag_quote_publication_artifacts": "mutable",
}
FORBIDDEN_TABLE_DETAILS = {
    "sqag_legal_holds": ("operator_only", "No runtime privilege. Operator/retention-only."),
    "sqag_retention_delete_authorizations": (
        "retention_only",
        "No runtime privilege. Retention-only internal mechanism.",
    ),
    "sqag_deletion_receipts": (
        "retention_only",
        "No runtime privilege. Retention-only internal mechanism.",
    ),
    "sqag_retention_scan_cursors": (
        "retention_only",
        "No runtime privilege. Retention-only internal mechanism.",
    ),
    "sqag_schema_migrations": (
        "migration_only",
        "No runtime privilege. Migration ledger owned by sqag_migrator.",
    ),
}

ROLE_DESCRIPTIONS = {
    "runtime": "Restricted application runtime role. Dormant NOLOGIN during Boundary A/B. Activated with LOGIN in #160 only after independent verification.",
    "migrator": "Owner/operator authority. Owns all application database objects. Applies ACL changes. Cannot create roles.",
    "legacy": "Legacy active rollback role. Retained until separately gated retirement after #160 switch and observation window.",
    "provider": "Provider/control-plane role. Creates/alters roles through separately authorised provider authority. Unchanged by SQAG.",
}

REQUIRED_QUERY_FEATURES: dict[str, tuple[str, ...]] = {
    "database_acl": ("pg_catalog.pg_database", "datname", "datacl", "current_database"),
    "schema_acl": ("pg_catalog.pg_namespace", "nspname", "nspacl", "public"),
    "table_acl": ("pg_catalog.pg_class", "pg_catalog.pg_namespace", "relacl", "relkind", "public", "order by"),
    "role_attributes": (
        "pg_catalog.pg_roles",
        "rolname",
        "rolsuper",
        "rolcanlogin",
        "rolconnlimit",
        "rolpassword",
    ),
    "role_memberships": ("pg_catalog.pg_auth_members", "admin_option", "pg_catalog.pg_roles"),
    "sequence_acl": ("pg_catalog.pg_class", "relkind", "'s'", "pg_catalog.pg_namespace", "relacl"),
    "effective_runtime_schema_privileges": ("has_schema_privilege", "public", "usage", "create"),
    "effective_runtime_routine_privileges": ("has_function_privilege", "pg_catalog.pg_proc", "public", "execute"),
}


class DuplicateKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 2


def _ok(message: str) -> None:
    print(f"OK: {message}")


def _add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def _exact_keys(value: Any, expected: frozenset[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        _add_error(errors, f"{label}_must_be_object")
        return False
    actual = set(value)
    unknown = actual - expected
    missing = expected - actual
    if unknown:
        _add_error(errors, f"{label}_unknown_keys: {','.join(sorted(unknown))}")
    if missing:
        _add_error(errors, f"{label}_missing_keys: {','.join(sorted(missing))}")
    return not unknown and not missing


def _exact_value(value: Any, expected: Any, label: str, errors: list[str]) -> None:
    if expected is None:
        valid = value is None
    else:
        valid = type(value) is type(expected) and value == expected
    if not valid:
        _add_error(errors, f"{label}_invalid_expected_{expected!r}_got_{value!r}")


def _require_type(value: Any, expected_type: type, label: str, errors: list[str]) -> None:
    if type(value) is not expected_type:
        _add_error(errors, f"{label}_must_be_{expected_type.__name__}")


def _require_non_empty_string(value: Any, label: str, errors: list[str]) -> None:
    if type(value) is not str or not value.strip():
        _add_error(errors, f"{label}_must_be_non_empty_string")


def _require_string_list(value: Any, label: str, errors: list[str], *, unique: bool = True) -> None:
    if type(value) is not list:
        _add_error(errors, f"{label}_must_be_list")
        return
    for index, item in enumerate(value):
        if type(item) is not str:
            _add_error(errors, f"{label}_{index}_must_be_string")
    if unique and len(value) != len(set(value)):
        _add_error(errors, f"{label}_must_not_contain_duplicates")


def _check_exact_string_list(
    value: Any, expected: list[str], label: str, errors: list[str]
) -> None:
    _require_string_list(value, label, errors)
    if type(value) is list and value != expected:
        _add_error(errors, f"{label}_invalid_expected_{expected!r}_got_{value!r}")


def validate_top_level_keys(manifest: dict[str, Any], errors: list[str]) -> None:
    _exact_keys(manifest, TOP_LEVEL_KEYS, "top_level", errors)


def validate_schema_version(manifest: dict[str, Any], errors: list[str]) -> None:
    _exact_value(manifest.get("schema_version"), 1, "schema_version", errors)
    _exact_value(manifest.get("$schema"), "runtime-privilege-contract-schema-v1", "schema_identifier", errors)
    _exact_value(manifest.get("contract_type"), "runtime_privilege_contract", "contract_type", errors)


def validate_source_binding(manifest: dict[str, Any], errors: list[str]) -> None:
    _exact_value(manifest.get("repository"), "Swooshz-com/swooshz-quote-auto-generator", "repository", errors)
    _exact_value(
        manifest.get("canonical_source_revision"),
        "cc53c685ff617aaa5bf1eb24e8a62c1273570779",
        "source_revision",
        errors,
    )
    _exact_value(
        manifest.get("canonical_source_tree"),
        "68d67a9a08c4c3d9e86460e24060f31fdc0eaa27",
        "source_tree",
        errors,
    )


def validate_roles(manifest: dict[str, Any], errors: list[str]) -> None:
    roles = manifest.get("roles")
    if not _exact_keys(roles, ROLE_KEYS, "roles", errors):
        if not isinstance(roles, dict):
            return

    runtime = roles.get("runtime") if isinstance(roles, dict) else None
    if _exact_keys(runtime, RUNTIME_ROLE_KEYS, "runtime_role", errors):
        _exact_value(runtime.get("name"), "sqag_runtime", "runtime_role_name", errors)
        _exact_value(runtime.get("description"), ROLE_DESCRIPTIONS["runtime"], "runtime_role_description", errors)
        attributes = runtime.get("attributes")
        if _exact_keys(attributes, RUNTIME_ROLE_ATTRIBUTE_KEYS, "runtime_attributes", errors):
            expected_attributes = {
                "login": False,
                "password": None,
                "superuser": False,
                "createdb": False,
                "createrole": False,
                "replication": False,
                "bypassrls": False,
                "inherit": True,
                "connection_limit": -1,
            }
            for key, expected in expected_attributes.items():
                _exact_value(attributes.get(key), expected, f"runtime_attribute_{key}", errors)
        _check_exact_string_list(runtime.get("memberships"), [], "runtime_memberships", errors)
        _check_exact_string_list(runtime.get("ownership"), [], "runtime_ownership", errors)
        _check_exact_string_list(runtime.get("grant_options"), [], "runtime_grant_options", errors)

    migrator = roles.get("migrator") if isinstance(roles, dict) else None
    if _exact_keys(migrator, MIGRATOR_ROLE_KEYS, "migrator_role", errors):
        _exact_value(migrator.get("name"), "sqag_migrator", "migrator_role_name", errors)
        _exact_value(migrator.get("description"), ROLE_DESCRIPTIONS["migrator"], "migrator_role_description", errors)
        _exact_value(migrator.get("can_create_roles"), False, "migrator_can_create_roles", errors)

    legacy = roles.get("legacy") if isinstance(roles, dict) else None
    if _exact_keys(legacy, LEGACY_ROLE_KEYS, "legacy_role", errors):
        _exact_value(legacy.get("name"), "sqag_app", "legacy_role_name", errors)
        _exact_value(legacy.get("description"), ROLE_DESCRIPTIONS["legacy"], "legacy_role_description", errors)
        _exact_value(legacy.get("status"), "retained_until_retirement", "legacy_role_status", errors)

    provider = roles.get("provider") if isinstance(roles, dict) else None
    if _exact_keys(provider, PROVIDER_ROLE_KEYS, "provider_role", errors):
        _exact_value(provider.get("name"), "neondb_owner", "provider_role_name", errors)
        _exact_value(provider.get("description"), ROLE_DESCRIPTIONS["provider"], "provider_role_description", errors)
        _exact_value(provider.get("status"), "provider_unchanged", "provider_role_status", errors)

    forbidden = roles.get("forbidden") if isinstance(roles, dict) else None
    _check_exact_string_list(forbidden, ["sqag_maintenance"], "forbidden_roles", errors)


def validate_production_migrations(manifest: dict[str, Any], errors: list[str]) -> None:
    migrations = manifest.get("production_migrations")
    if type(migrations) is not list:
        _add_error(errors, "production_migrations_must_be_list")
        return

    repo_manifest = migration_manifest(ROOT / "migrations")
    expected_count = len(MIGRATION_FILE_NAMES)
    if len(migrations) != expected_count:
        _add_error(errors, f"production_migrations_count_mismatch_expected_{expected_count}_got_{len(migrations)}")

    for index, entry in enumerate(migrations):
        label = f"migration_{index}"
        if not _exact_keys(entry, MIGRATION_KEYS, label, errors):
            if not isinstance(entry, dict):
                continue

        if index >= expected_count or index >= len(repo_manifest):
            continue
        expected_name = MIGRATION_FILE_NAMES[index]
        expected_path = f"migrations/{expected_name}"
        _exact_value(entry.get("path"), expected_path, f"{label}_path", errors)
        _exact_value(entry.get("sequence_no"), index + 1, f"{label}_sequence_no", errors)
        digest = entry.get("sha256")
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _add_error(errors, f"{label}_sha256_must_be_lowercase_hex_64")
        else:
            _exact_value(digest, repo_manifest[index].checksum_sha256, f"{label}_sha256", errors)

        tables = entry.get("tables")
        _require_string_list(tables, f"{label}_tables", errors)
        if type(tables) is list:
            actual_tables = set(tables)
            expected_tables = set(MIGRATION_TABLES[expected_name])
            if actual_tables != expected_tables:
                _add_error(
                    errors,
                    f"{label}_table_binding_mismatch_expected_{sorted(expected_tables)}_got_{sorted(actual_tables)}",
                )


def validate_table_matrix(manifest: dict[str, Any], errors: list[str]) -> None:
    tables = manifest.get("tables")
    if not _exact_keys(tables, TABLES_KEYS, "tables", errors):
        if not isinstance(tables, dict):
            return

    _exact_value(tables.get("total_count"), 16, "total_table_count", errors)
    _exact_value(tables.get("rw_count"), 11, "runtime_accessible_count", errors)
    _exact_value(tables.get("forbidden_count"), 5, "forbidden_count", errors)

    accessible = tables.get("runtime_accessible")
    forbidden = tables.get("runtime_forbidden")
    if not isinstance(accessible, dict):
        _add_error(errors, "runtime_accessible_must_be_object")
    else:
        actual_accessible = set(accessible)
        if actual_accessible != RUNTIME_TABLES:
            _add_error(
                errors,
                f"runtime_accessible_table_set_mismatch_extra_{actual_accessible - RUNTIME_TABLES}_missing_{RUNTIME_TABLES - actual_accessible}",
            )
        for table_name, entry in accessible.items():
            label = f"accessible_table_{table_name}"
            if not _exact_keys(entry, ACCESSIBLE_TABLE_KEYS, label, errors):
                if not isinstance(entry, dict):
                    continue
            _exact_value(entry.get("class"), ACCESSIBLE_TABLE_CLASSES.get(table_name), f"{label}_class", errors)
            _exact_value(entry.get("schema"), "public", f"{label}_schema", errors)
            privileges = entry.get("privileges")
            if not _exact_keys(privileges, PRIVILEGE_KEYS, f"{label}_privileges", errors):
                if not isinstance(privileges, dict):
                    continue
            locked = LOCKED_PRIVILEGE_MATRIX.get(table_name)
            for privilege_name in sorted(PRIVILEGE_KEYS):
                expected = locked.get(privilege_name) if locked is not None else None
                _exact_value(privileges.get(privilege_name), expected, f"{label}_{privilege_name}", errors)

    if not isinstance(forbidden, dict):
        _add_error(errors, "runtime_forbidden_must_be_object")
    else:
        actual_forbidden = set(forbidden)
        if actual_forbidden != FORBIDDEN_TABLES:
            _add_error(
                errors,
                f"forbidden_table_set_mismatch_extra_{actual_forbidden - FORBIDDEN_TABLES}_missing_{FORBIDDEN_TABLES - actual_forbidden}",
            )
        for table_name, entry in forbidden.items():
            label = f"forbidden_table_{table_name}"
            if not _exact_keys(entry, FORBIDDEN_TABLE_KEYS, label, errors):
                if not isinstance(entry, dict):
                    continue
            expected_class, expected_reason = FORBIDDEN_TABLE_DETAILS.get(table_name, (None, None))
            _exact_value(entry.get("class"), expected_class, f"{label}_class", errors)
            _exact_value(entry.get("schema"), "public", f"{label}_schema", errors)
            _require_non_empty_string(entry.get("reason"), f"{label}_reason", errors)
            _exact_value(entry.get("reason"), expected_reason, f"{label}_reason", errors)


def validate_sequences(manifest: dict[str, Any], errors: list[str]) -> None:
    sequence = manifest.get("sequences")
    if not _exact_keys(sequence, SEQUENCE_KEYS, "sequences", errors):
        if not isinstance(sequence, dict):
            return
    _exact_value(sequence.get("user_defined_public_count"), 0, "sequence_count", errors)
    _exact_value(sequence.get("runtime_privileges"), "none", "runtime_sequence_privileges", errors)
    _exact_value(
        sequence.get("rule"),
        "Any future production sequence must be explicitly classified before CI passes.",
        "sequence_rule",
        errors,
    )


def validate_routines(manifest: dict[str, Any], errors: list[str]) -> None:
    routines = manifest.get("routines")
    if not _exact_keys(routines, ROUTINES_KEYS, "routines", errors):
        if not isinstance(routines, dict):
            return
    _exact_value(routines.get("total_count"), 3, "total_routine_count", errors)
    _exact_value(routines.get("sqag_owned_count"), 2, "sqag_owned_routine_count", errors)
    _exact_value(
        routines.get("rule"),
        "No direct runtime routine grants. Every future routine must be explicitly classified before CI passes.",
        "routine_rule",
        errors,
    )

    triggers = routines.get("sqag_owned_triggers")
    if not isinstance(triggers, dict):
        _add_error(errors, "sqag_owned_triggers_must_be_object")
    else:
        if set(triggers) != set(EXPECTED_ROUTINES):
            _add_error(errors, "sqag_trigger_routine_set_mismatch")
        for name, entry in triggers.items():
            label = f"trigger_routine_{name}"
            if not _exact_keys(entry, TRIGGER_ROUTINE_KEYS, label, errors):
                if not isinstance(entry, dict):
                    continue
            _exact_value(entry.get("schema"), "public", f"{label}_schema", errors)
            _exact_value(entry.get("owner"), "sqag_migrator", f"{label}_owner", errors)
            _exact_value(entry.get("security_mode"), "invoker", f"{label}_security_mode", errors)
            _exact_value(entry.get("class"), "trigger_only", f"{label}_class", errors)
            _exact_value(entry.get("direct_runtime_execute"), False, f"{label}_direct_runtime_execute", errors)
            _exact_value(
                entry.get("public_execute_after_boundary_b"),
                False,
                f"{label}_public_execute_after_boundary_b",
                errors,
            )

    provider_exceptions = routines.get("provider_owned_exceptions")
    if not isinstance(provider_exceptions, dict):
        _add_error(errors, "provider_owned_exceptions_must_be_object")
    else:
        if set(provider_exceptions) != {"show_db_tree"}:
            _add_error(errors, "provider_exception_set_must_be_exactly_show_db_tree")
        entry = provider_exceptions.get("show_db_tree")
        label = "provider_exception_show_db_tree"
        if not _exact_keys(entry, PROVIDER_EXCEPTION_KEYS, label, errors):
            if not isinstance(entry, dict):
                return
        _exact_value(entry.get("schema"), "public", f"{label}_schema", errors)
        _exact_value(entry.get("owner"), "neondb_owner", f"{label}_owner", errors)
        _exact_value(entry.get("class"), "provider_diagnostic_exception", f"{label}_class", errors)
        _exact_value(entry.get("direct_runtime_grant"), False, f"{label}_direct_runtime_grant", errors)
        _exact_value(entry.get("public_execute"), "unchanged", f"{label}_public_execute", errors)
        _exact_value(
            entry.get("effective_runtime_execution"),
            "bounded_public_exception",
            f"{label}_effective_runtime_execution",
            errors,
        )


def validate_database_acl(manifest: dict[str, Any], errors: list[str]) -> None:
    acl = manifest.get("database_acl")
    if not _exact_keys(acl, DATABASE_ACL_KEYS, "database_acl", errors):
        if not isinstance(acl, dict):
            return
    expected = {
        "public": (DATABASE_PUBLIC_KEYS, {"connect": True, "temporary": "forbidden_after_boundary_b", "create": False}),
        "sqag_migrator": (DATABASE_MIGRATOR_KEYS, {"connect": True, "create": True, "temporary": True}),
        "sqag_app": (DATABASE_APP_KEYS, {"connect": "retained_until_retirement"}),
        "sqag_runtime": (DATABASE_RUNTIME_KEYS, {"connect": True, "create": False, "temporary": False}),
    }
    for actor, (keys, values) in expected.items():
        actor_acl = acl.get(actor)
        if _exact_keys(actor_acl, keys, f"database_acl_{actor}", errors):
            for key, value in values.items():
                _exact_value(actor_acl.get(key), value, f"database_acl_{actor}_{key}", errors)


def validate_schema_acl(manifest: dict[str, Any], errors: list[str]) -> None:
    acl = manifest.get("schema_acl")
    if not _exact_keys(acl, SCHEMA_ACL_KEYS, "schema_acl", errors):
        if not isinstance(acl, dict):
            return
    _exact_value(acl.get("schema_name"), "public", "schema_name", errors)
    expected = {
        "public": (SCHEMA_PUBLIC_KEYS, {"usage": True}),
        "pg_database_owner": (SCHEMA_OWNER_KEYS, {"create": True, "usage": True}),
        "sqag_app": (SCHEMA_APP_KEYS, {"usage": "retained_until_retirement"}),
        "sqag_runtime": (SCHEMA_RUNTIME_KEYS, {"usage": True, "create": False}),
    }
    for actor, (keys, values) in expected.items():
        actor_acl = acl.get(actor)
        if _exact_keys(actor_acl, keys, f"schema_acl_{actor}", errors):
            for key, value in values.items():
                _exact_value(actor_acl.get(key), value, f"schema_acl_{actor}_{key}", errors)


def validate_default_privileges(manifest: dict[str, Any], errors: list[str]) -> None:
    default_privileges = manifest.get("default_privileges")
    if not _exact_keys(default_privileges, DEFAULT_PRIVILEGES_KEYS, "default_privileges", errors):
        if not isinstance(default_privileges, dict):
            return
    runtime = default_privileges.get("sqag_runtime")
    if _exact_keys(runtime, RUNTIME_DEFAULT_KEYS, "runtime_default_privileges", errors):
        for key in sorted(RUNTIME_DEFAULT_KEYS):
            _exact_value(runtime.get(key), "none", f"runtime_default_{key}", errors)
    _exact_value(
        default_privileges.get("sqag_migrator_to_sqag_app"),
        "preserved_during_boundary_a_and_initial_boundary_b",
        "migrator_default_privileges",
        errors,
    )
    _exact_value(
        default_privileges.get("provider_controlled"),
        "unchanged",
        "provider_default_privileges",
        errors,
    )
    _exact_value(
        default_privileges.get("verification_rule"),
        "Must expand defaclacl through aclexplode() or equivalent grantee-aware operation. Checking defaclrole alone is insufficient.",
        "default_privileges_verification_rule",
        errors,
    )


def _normalized_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _require_sql_features(
    query: str, key: str, features: tuple[str, ...], errors: list[str]
) -> None:
    normalized = _normalized_sql(query)
    for feature in features:
        if feature.lower() not in normalized:
            _add_error(errors, f"verification_query_{key}_missing_semantic_feature_{feature}")


def _validate_default_acl_query(query: str, errors: list[str]) -> None:
    normalized = _normalized_sql(query)
    required = (
        "pg_catalog.pg_default_acl",
        "defaclrole",
        "defaclnamespace",
        "defaclobjtype",
        "defaclacl",
        "privilege_type",
        "is_grantable",
        "grantee",
        "owner",
        "namespace",
        "order by",
    )
    _require_sql_features(query, "default_acl", required, errors)
    lateral_explode_count = len(
        re.findall(r"cross\s+join\s+lateral\s+(?:pg_catalog\.)?aclexplode\s*\(", normalized)
    )
    if lateral_explode_count != 1:
        _add_error(errors, "verification_query_default_acl_requires_exactly_one_cross_join_lateral_aclexplode")
    if not re.search(r"defaclobjtype\s+in\s*\(\s*'r'\s*,\s*'s'\s*,\s*'f'\s*\)", normalized):
        _add_error(errors, "verification_query_default_acl_must_cover_r_s_f_object_types")
    if not re.search(r"case\s+when\s+[^;]*grantee\s*=\s*0[^;]*public", normalized):
        _add_error(errors, "verification_query_default_acl_must_map_grantee_zero_to_public")
    if "::regrole" in normalized or "::name" in normalized:
        _add_error(errors, "verification_query_default_acl_must_not_cast_absent_role_names")
    if not re.search(r"left\s+join\s+(?:pg_catalog\.)?pg_roles", normalized):
        _add_error(errors, "verification_query_default_acl_must_left_join_named_grantees")


def _validate_routine_query(query: str, errors: list[str]) -> None:
    normalized = _normalized_sql(query)
    _require_sql_features(
        query,
        "routine_acl",
        (
            "pg_catalog.pg_proc",
            "pg_catalog.pg_namespace",
            "pg_catalog.pg_roles",
            "pg_catalog.pg_trigger",
            "pg_get_function_identity_arguments",
            "proname",
            "proacl",
            "proowner",
            "prosecdef",
            "prokind",
            "tgfoid",
            "tgisinternal",
            "order by",
        ),
        errors,
    )
    if "nspname = 'public'" not in normalized:
        _add_error(errors, "verification_query_routine_acl_must_define_public_schema_boundary")
    if not re.search(r"p\.prokind\s+in\s*\(\s*'f'\s*,\s*'p'\s*,\s*'a'\s*,\s*'w'\s*\)", normalized):
        _add_error(errors, "verification_query_routine_acl_must_cover_all_user_defined_routine_kinds")
    if re.search(r"proname\s+like", normalized):
        _add_error(errors, "verification_query_routine_acl_must_not_prefix_filter_routines")


def validate_verification_queries(manifest: dict[str, Any], errors: list[str]) -> None:
    queries = manifest.get("verification_queries")
    if not _exact_keys(queries, VERIFICATION_QUERY_KEYS, "verification_queries", errors):
        if not isinstance(queries, dict):
            return
    for key in VERIFICATION_QUERY_KEYS:
        value = queries.get(key)
        _require_non_empty_string(value, f"verification_query_{key}", errors)
        if type(value) is not str or not value.strip():
            continue
        if key == "default_acl":
            _validate_default_acl_query(value, errors)
        elif key == "routine_acl":
            _validate_routine_query(value, errors)
        elif key in REQUIRED_QUERY_FEATURES:
            _require_sql_features(value, key, REQUIRED_QUERY_FEATURES[key], errors)
    table_query = queries.get("effective_runtime_table_privileges")
    if type(table_query) is str and table_query.strip():
        _require_sql_features(
            table_query,
            "effective_runtime_table_privileges",
            ("has_table_privilege", "pg_catalog.pg_class", "is_grantable", "sqag_runtime", "public"),
            errors,
        )


def validate_manifest_strictly(manifest_path: str) -> int:
    try:
        raw = Path(manifest_path).read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(f"cannot_read_manifest: {exc}")

    try:
        manifest = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except DuplicateKeyError as exc:
        return _fail(f"manifest_duplicate_json_key: {exc}")
    except json.JSONDecodeError as exc:
        return _fail(f"manifest_invalid_json: {exc}")
    except ValueError as exc:
        return _fail(f"manifest_invalid_json: {exc}")

    if not isinstance(manifest, dict):
        return _fail("manifest_must_be_JSON_object")

    errors: list[str] = []
    validate_top_level_keys(manifest, errors)
    validate_schema_version(manifest, errors)
    validate_source_binding(manifest, errors)
    validate_roles(manifest, errors)
    validate_production_migrations(manifest, errors)
    validate_table_matrix(manifest, errors)
    validate_sequences(manifest, errors)
    validate_routines(manifest, errors)
    validate_database_acl(manifest, errors)
    validate_schema_acl(manifest, errors)
    validate_default_privileges(manifest, errors)
    validate_verification_queries(manifest, errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 2

    _ok("Runtime privilege contract manifest passes strict validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate_manifest_strictly(str(MANIFEST_PATH)))
