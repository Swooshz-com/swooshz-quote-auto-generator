#!/usr/bin/env python3
"""Validate the runtime privilege contract manifest against repository authority."""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (
    EXPECTED_TABLES,
    EXPECTED_ROUTINES,
    MIGRATION_FILE_NAMES,
    canonical_migration_payload,
    migration_manifest,
)

MANIFEST_PATH = ROOT / "docs" / "runtime-privilege-contract.json"

PERMITTED_KEYS = frozenset(
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

RUNTIME_ROLE_ATTR_KEYS = frozenset(
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

DATABASE_ACL_ROLES = frozenset({"public", "sqag_migrator", "sqag_app", "sqag_runtime"})

SCHEMA_ACL_ROLES = frozenset({"public", "pg_database_owner", "sqag_app", "sqag_runtime"})

PREFERRED_PRIVILEGES = frozenset({"select", "insert", "update", "delete"})

DEFAULT_PRIV_ROLES = frozenset({"sqag_runtime", "sqag_migrator_to_sqag_app", "provider_controlled", "verification_rule"})

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

MUTABLE_ALL_PRIVS = {"select": True, "insert": True, "update": True, "delete": True}

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


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 2


def _ok(message: str) -> None:
    print(f"OK: {message}")


def _check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_top_level_keys(manifest: dict[str, Any], errors: list[str]) -> None:
    unknown = set(manifest) - PERMITTED_KEYS
    if unknown:
        errors.append(f"unknown_top_level_keys: {','.join(sorted(unknown))}")
    missing = PERMITTED_KEYS - set(manifest)
    if missing:
        errors.append(f"missing_top_level_keys: {','.join(sorted(missing))}")


def validate_schema_version(manifest: dict[str, Any], errors: list[str]) -> None:
    _check(manifest.get("schema_version") == 1, "schema_version_must_be_1", errors)
    _check(
        manifest.get("$schema") == "runtime-privilege-contract-schema-v1",
        "schema_identifier_invalid",
        errors,
    )
    _check(
        manifest.get("contract_type") == "runtime_privilege_contract",
        "contract_type_invalid",
        errors,
    )


def validate_source_binding(manifest: dict[str, Any], errors: list[str]) -> None:
    _check(
        manifest.get("repository") == "Swooshz-com/swooshz-quote-auto-generator",
        "repository_invalid",
        errors,
    )
    _check(
        manifest.get("canonical_source_revision")
        == "cc53c685ff617aaa5bf1eb24e8a62c1273570779",
        "source_revision_mismatch",
        errors,
    )
    _check(
        manifest.get("canonical_source_tree")
        == "68d67a9a08c4c3d9e86460e24060f31fdc0eaa27",
        "source_tree_mismatch",
        errors,
    )


def validate_roles(manifest: dict[str, Any], errors: list[str]) -> None:
    roles: dict[str, Any] | None = manifest.get("roles")
    _check(isinstance(roles, dict), "roles_must_be_object", errors)
    if not isinstance(roles, dict):
        return

    unknown = set(roles) - ROLE_KEYS
    if unknown:
        errors.append(f"unknown_role_sections: {','.join(sorted(unknown))}")

    runtime: dict[str, Any] | None = roles.get("runtime")
    _check(isinstance(runtime, dict), "runtime_role_must_be_object", errors)
    if isinstance(runtime, dict):
        _check(runtime.get("name") == "sqag_runtime", "runtime_role_name_invalid", errors)
        attrs: dict[str, Any] | None = runtime.get("attributes")
        _check(isinstance(attrs, dict), "runtime_attributes_must_be_object", errors)
        if isinstance(attrs, dict):
            unknown_attrs = set(attrs) - RUNTIME_ROLE_ATTR_KEYS
            if unknown_attrs:
                errors.append(f"unknown_runtime_attributes: {','.join(sorted(unknown_attrs))}")
            _check(attrs.get("login") is False, "runtime_must_be_nologin", errors)
            _check(attrs.get("password") is None, "runtime_password_must_be_null", errors)
            _check(attrs.get("superuser") is False, "runtime_must_be_nosuperuser", errors)
            _check(attrs.get("createdb") is False, "runtime_must_be_nocreatedb", errors)
            _check(attrs.get("createrole") is False, "runtime_must_be_nocreaterole", errors)
            _check(attrs.get("replication") is False, "runtime_must_be_noreplication", errors)
            _check(attrs.get("bypassrls") is False, "runtime_must_be_nobypassrls", errors)
            _check(attrs.get("inherit") is True, "runtime_must_be_inherit", errors)
        _check(runtime.get("memberships") == [], "runtime_must_have_no_memberships", errors)
        _check(runtime.get("ownership") == [], "runtime_must_have_no_ownership", errors)
        _check(runtime.get("grant_options") == [], "runtime_must_have_no_grant_options", errors)

    migrator: dict[str, Any] | None = roles.get("migrator")
    _check(isinstance(migrator, dict), "migrator_role_must_be_object", errors)
    if isinstance(migrator, dict):
        _check(migrator.get("name") == "sqag_migrator", "migrator_role_name_invalid", errors)
        _check(migrator.get("can_create_roles") is False, "migrator_cannot_create_roles", errors)

    legacy: dict[str, Any] | None = roles.get("legacy")
    _check(isinstance(legacy, dict), "legacy_role_must_be_object", errors)
    if isinstance(legacy, dict):
        _check(legacy.get("name") == "sqag_app", "legacy_role_name_invalid", errors)

    provider: dict[str, Any] | None = roles.get("provider")
    _check(isinstance(provider, dict), "provider_role_must_be_object", errors)
    if isinstance(provider, dict):
        _check(provider.get("name") == "neondb_owner", "provider_role_name_invalid", errors)

    forbidden: Any = roles.get("forbidden")
    _check(isinstance(forbidden, list), "forbidden_roles_must_be_list", errors)
    if isinstance(forbidden, list):
        _check("sqag_maintenance" in forbidden, "sqag_maintenance_must_be_forbidden", errors)


def validate_production_migrations(manifest: dict[str, Any], errors: list[str]) -> None:
    migrations_manifest: Any = manifest.get("production_migrations")
    _check(isinstance(migrations_manifest, list), "production_migrations_must_be_list", errors)
    if not isinstance(migrations_manifest, list):
        return

    repo_manifest = migration_manifest(ROOT / "migrations")

    expected_count = len(MIGRATION_FILE_NAMES)
    _check(
        len(migrations_manifest) == expected_count,
        f"production_migrations_count_mismatch_expected_{expected_count}_got_{len(migrations_manifest)}",
        errors,
    )

    if len(migrations_manifest) != expected_count:
        return

    for index, migration_entry in enumerate(migrations_manifest):
        _check(isinstance(migration_entry, dict), f"migration_{index}_must_be_object", errors)
        if not isinstance(migration_entry, dict):
            continue

        unknown_keys = set(migration_entry) - MIGRATION_KEYS
        if unknown_keys:
            errors.append(f"migration_{index}_unknown_keys: {','.join(sorted(unknown_keys))}")

        manifest_path = migration_entry.get("path")
        expected_name = MIGRATION_FILE_NAMES[index]
        expected_path = f"migrations/{expected_name}"

        _check(
            manifest_path == expected_path,
            f"migration_{index}_path_mismatch_expected_{expected_path}_got_{manifest_path}",
            errors,
        )

        _check(
            migration_entry.get("sequence_no") == index + 1,
            f"migration_{index}_sequence_no_mismatch_expected_{index + 1}",
            errors,
        )

        repo_migration = repo_manifest[index]
        actual_digest = repo_migration.checksum_sha256
        manifest_digest = migration_entry.get("sha256")

        _check(
            manifest_digest == actual_digest,
            f"migration_{index}_sha256_mismatch_{expected_name}",
            errors,
        )

        table_set = set(migration_entry.get("tables", []))
        repo_table_set = repo_migration.path.read_bytes()
        all_known = table_set.issubset(ALL_TABLES)
        _check(
            all_known,
            f"migration_{index}_unknown_tables: {table_set - ALL_TABLES}",
            errors,
        )


def validate_table_matrix(manifest: dict[str, Any], errors: list[str]) -> None:
    tables_section: Any = manifest.get("tables")
    _check(isinstance(tables_section, dict), "tables_section_must_be_object", errors)
    if not isinstance(tables_section, dict):
        return

    _check(tables_section.get("total_count") == 16, "total_table_count_must_be_16", errors)
    _check(tables_section.get("rw_count") == 11, "runtime_accessible_count_must_be_11", errors)
    _check(tables_section.get("forbidden_count") == 5, "forbidden_count_must_be_5", errors)

    runtime_accessible: dict[str, Any] | None = tables_section.get("runtime_accessible")
    runtime_forbidden: dict[str, Any] | None = tables_section.get("runtime_forbidden")

    _check(isinstance(runtime_accessible, dict), "runtime_accessible_must_be_object", errors)
    _check(isinstance(runtime_forbidden, dict), "runtime_forbidden_must_be_object", errors)

    if isinstance(runtime_accessible, dict):
        actual_rw = set(runtime_accessible)
        _check(
            actual_rw == RUNTIME_TABLES,
            f"runtime_accessible_table_set_mismatch_extra_{actual_rw - RUNTIME_TABLES}_missing_{RUNTIME_TABLES - actual_rw}",
            errors,
        )
        for table_name, entry in runtime_accessible.items():
            _check(isinstance(entry, dict), f"table_{table_name}_entry_must_be_object", errors)
            if isinstance(entry, dict):
                privs: dict[str, Any] | None = entry.get("privileges")
                _check(isinstance(privs, dict), f"table_{table_name}_privileges_must_be_object", errors)
                if isinstance(privs, dict):
                    priv_keys = set(privs)
                    _check(
                        priv_keys.issubset(PREFERRED_PRIVILEGES),
                        f"table_{table_name}_unknown_privileges: {priv_keys - PREFERRED_PRIVILEGES}",
                        errors,
                    )
                    for p in PREFERRED_PRIVILEGES:
                        _check(
                            p in privs,
                            f"table_{table_name}_missing_privilege: {p}",
                            errors,
                        )
                    locked = LOCKED_PRIVILEGE_MATRIX.get(table_name)
                    if locked is not None:
                        for p in PREFERRED_PRIVILEGES:
                            _check(
                                privs.get(p) is locked.get(p),
                                f"table_{table_name}_privilege_{p}_mismatch_expected_{locked.get(p)}_got_{privs.get(p)}",
                                errors,
                            )
                    _check(
                        not entry.get("grant_option", False),
                        f"table_{table_name}_must_not_have_grant_option",
                        errors,
                    )

    if isinstance(runtime_forbidden, dict):
        actual_fb = set(runtime_forbidden)
        _check(
            actual_fb == FORBIDDEN_TABLES,
            f"forbidden_table_set_mismatch_extra_{actual_fb - FORBIDDEN_TABLES}_missing_{FORBIDDEN_TABLES - actual_fb}",
            errors,
        )
        for table_name, entry in runtime_forbidden.items():
            _check(isinstance(entry, dict), f"table_{table_name}_entry_must_be_object", errors)
            if isinstance(entry, dict):
                cls: str | None = entry.get("class")
                _check(
                    cls is not None and cls != "",
                    f"table_{table_name}_must_have_non_empty_class",
                    errors,
                )


def validate_sequences(manifest: dict[str, Any], errors: list[str]) -> None:
    seq: Any = manifest.get("sequences")
    _check(isinstance(seq, dict), "sequences_must_be_object", errors)
    if isinstance(seq, dict):
        _check(seq.get("user_defined_public_count") == 0, "sequence_count_must_be_0", errors)
        _check(seq.get("runtime_privileges") == "none", "runtime_sequence_privileges_must_be_none", errors)


def validate_routines(manifest: dict[str, Any], errors: list[str]) -> None:
    routines: Any = manifest.get("routines")
    _check(isinstance(routines, dict), "routines_must_be_object", errors)
    if not isinstance(routines, dict):
        return

    _check(routines.get("total_count") == 3, "total_routine_count_must_be_3", errors)
    _check(routines.get("sqag_owned_count") == 2, "sqag_owned_routine_count_must_be_2", errors)

    sqag_triggers: dict[str, Any] | None = routines.get("sqag_owned_triggers")
    _check(isinstance(sqag_triggers, dict), "sqag_owned_triggers_must_be_object", errors)
    if isinstance(sqag_triggers, dict):
        actual_triggers = set(sqag_triggers)
        _check(
            actual_triggers == EXPECTED_ROUTINES,
            f"sqag_trigger_routine_set_mismatch_extra_{actual_triggers - EXPECTED_ROUTINES}_missing_{EXPECTED_ROUTINES - actual_triggers}",
            errors,
        )
        for name, entry in sqag_triggers.items():
            _check(isinstance(entry, dict), f"routine_{name}_entry_must_be_object", errors)
            if isinstance(entry, dict):
                _check(entry.get("owner") == "sqag_migrator", f"routine_{name}_owner_must_be_sqag_migrator", errors)
                _check(entry.get("security_mode") == "invoker", f"routine_{name}_must_be_invoker", errors)
                _check(entry.get("class") == "trigger_only", f"routine_{name}_must_be_trigger_only", errors)
                _check(entry.get("direct_runtime_execute") is False, f"routine_{name}_direct_runtime_execute_must_be_false", errors)
                _check(entry.get("public_execute_after_boundary_b") is False, f"routine_{name}_public_execute_after_boundary_b_must_be_false", errors)

    provider_exceptions: dict[str, Any] | None = routines.get("provider_owned_exceptions")
    _check(isinstance(provider_exceptions, dict), "provider_owned_exceptions_must_be_object", errors)
    if isinstance(provider_exceptions, dict):
        _check("show_db_tree" in provider_exceptions, "show_db_tree_provider_exception_missing", errors)
        if "show_db_tree" in provider_exceptions:
            entry = provider_exceptions["show_db_tree"]
            _check(isinstance(entry, dict), "show_db_tree_entry_must_be_object", errors)
            if isinstance(entry, dict):
                _check(entry.get("owner") == "neondb_owner", "show_db_tree_owner_must_be_neondb_owner", errors)
                _check(entry.get("class") == "provider_diagnostic_exception", "show_db_tree_class_must_be_provider_diagnostic_exception", errors)
                _check(entry.get("direct_runtime_grant") is False, "show_db_tree_must_have_no_direct_runtime_grant", errors)
                _check(entry.get("public_execute") == "unchanged", "show_db_tree_public_execute_must_be_unchanged", errors)
                _check(entry.get("effective_runtime_execution") == "bounded_public_exception", "show_db_tree_effective_runtime_must_be_bounded_public_exception", errors)


def validate_database_acl(manifest: dict[str, Any], errors: list[str]) -> None:
    acl: Any = manifest.get("database_acl")
    _check(isinstance(acl, dict), "database_acl_must_be_object", errors)
    if not isinstance(acl, dict):
        return

    public: dict[str, Any] | None = acl.get("public")
    if isinstance(public, dict):
        _check(public.get("connect") is True, "public_database_connect_must_be_true", errors)
        _check(public.get("create") is False, "public_database_create_must_be_false", errors)

    runtime: dict[str, Any] | None = acl.get("sqag_runtime")
    if isinstance(runtime, dict):
        _check(runtime.get("connect") is True, "runtime_database_connect_must_be_true", errors)
        _check(runtime.get("create") is False, "runtime_database_create_must_be_false", errors)
        _check(runtime.get("temporary") is False, "runtime_database_temporary_must_be_false", errors)

    migrator: dict[str, Any] | None = acl.get("sqag_migrator")
    if isinstance(migrator, dict):
        _check(migrator.get("connect") is True, "migrator_database_connect_must_be_true", errors)
        _check(migrator.get("create") is True, "migrator_database_create_must_be_true", errors)
        _check(migrator.get("temporary") is True, "migrator_database_temporary_must_be_true", errors)


def validate_schema_acl(manifest: dict[str, Any], errors: list[str]) -> None:
    acl: Any = manifest.get("schema_acl")
    _check(isinstance(acl, dict), "schema_acl_must_be_object", errors)
    if not isinstance(acl, dict):
        return

    _check(acl.get("schema_name") == "public", "schema_name_must_be_public", errors)

    public: dict[str, Any] | None = acl.get("public")
    if isinstance(public, dict):
        _check(public.get("usage") is True, "public_schema_usage_must_be_true", errors)

    pg_owner: dict[str, Any] | None = acl.get("pg_database_owner")
    if isinstance(pg_owner, dict):
        _check(pg_owner.get("create") is True, "pg_database_owner_schema_create_must_be_true", errors)
        _check(pg_owner.get("usage") is True, "pg_database_owner_schema_usage_must_be_true", errors)

    runtime: dict[str, Any] | None = acl.get("sqag_runtime")
    if isinstance(runtime, dict):
        _check(runtime.get("usage") is True, "runtime_schema_usage_must_be_true", errors)
        _check(runtime.get("create") is False, "runtime_schema_create_must_be_false", errors)


def validate_default_privileges(manifest: dict[str, Any], errors: list[str]) -> None:
    defpriv: Any = manifest.get("default_privileges")
    _check(isinstance(defpriv, dict), "default_privileges_must_be_object", errors)
    if not isinstance(defpriv, dict):
        return

    runtime_defaults: dict[str, Any] | None = defpriv.get("sqag_runtime")
    if isinstance(runtime_defaults, dict):
        _check(runtime_defaults.get("tables") == "none", "runtime_default_table_privileges_must_be_none", errors)
        _check(runtime_defaults.get("sequences") == "none", "runtime_default_sequence_privileges_must_be_none", errors)
        _check(runtime_defaults.get("routines") == "none", "runtime_default_routine_privileges_must_be_none", errors)

    _check(
        defpriv.get("verification_rule") is not None,
        "default_privileges_verification_rule_required",
        errors,
    )


def validate_verification_queries(manifest: dict[str, Any], errors: list[str]) -> None:
    queries: Any = manifest.get("verification_queries")
    _check(isinstance(queries, dict), "verification_queries_must_be_object", errors)
    if isinstance(queries, dict):
        required = frozenset(
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
        missing_queries = required - set(queries)
        if missing_queries:
            errors.append(f"missing_verification_queries: {','.join(sorted(missing_queries))}")


def validate_manifest_strictly(manifest_path: str) -> int:
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return _fail(f"cannot_read_manifest: {exc}")

    try:
        manifest: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail(f"manifest_invalid_json: {exc}")

    if not isinstance(manifest, dict):
        return _fail("manifest_must_be_JSON_object")

    errors: list[str] = []

    validate_top_level_keys(manifest, errors)
    if "schema_version" not in manifest:
        return _fail("schema_version_required")
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
