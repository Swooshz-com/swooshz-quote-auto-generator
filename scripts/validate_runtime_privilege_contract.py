#!/usr/bin/env python3
"""Validate the runtime privilege contract manifest against repository authority."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
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
        "column_privileges",
        "views",
        "sequences",
        "routines",
        "default_privileges",
        "boundary_b",
        "verification_queries",
    }
)

ROLE_KEYS = frozenset({"runtime", "migrator", "legacy", "provider", "forbidden"})
RUNTIME_ROLE_KEYS = frozenset(
    {
        "name",
        "description",
        "attributes",
        "memberships_as_member",
        "inherited_roles",
        "set_assumable_roles",
        "membership_derived_privileges",
        "provider_control_edges",
        "ownership",
        "grant_options",
    }
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
PROVIDER_CONTROL_EDGE_KEYS = frozenset(
    {
        "parent_role",
        "member_role",
        "grantor",
        "admin_option",
        "inherit_option",
        "set_option",
        "classification",
        "security_rationale",
    }
)
MEMBERSHIP_ROW_KEYS = frozenset(
    {"role", "member", "grantor", "admin_option", "inherit_option", "set_option"}
)
PROTECTED_PRODUCTION_ROLES = frozenset(
    {
        "sqag_runtime",
        "sqag_migrator",
        "sqag_app",
        "neondb_owner",
        "neon_superuser",
        "cloud_admin",
    }
)
PROVIDER_CONTROL_CLASSIFICATION = "postgresql17_creator_admin_control"
PROVIDER_CONTROL_SECURITY_RATIONALE = (
    "PostgreSQL 17 system-generated creator-admin control for the provider administrator; "
    "it grants no privilege, inheritance, or SET-role path to sqag_runtime."
)
PRODUCTION_PROVIDER_CONTROL_EDGE = {
    "parent_role": "sqag_runtime",
    "member_role": "neondb_owner",
    "grantor": "cloud_admin",
    "admin_option": True,
    "inherit_option": False,
    "set_option": False,
    "classification": PROVIDER_CONTROL_CLASSIFICATION,
    "security_rationale": PROVIDER_CONTROL_SECURITY_RATIONALE,
}

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

VIEWS_KEYS = frozenset({"count", "runtime_accessible"})
ACCESSIBLE_VIEW_KEYS = frozenset({"schema", "class", "privileges", "production_source", "bound"})
VIEW_PRIVILEGE_KEYS = frozenset({"select"})

BOUNDARY_B_KEYS = frozenset(
    {
        "requires_postgresql17",
        "runtime_role",
        "object_owner",
        "database_owner_authority",
        "authority_input_model",
        "fail_closed",
        "idempotent_rerun",
        "operations",
    }
)
BOUNDARY_B_OPERATION_KEYS = frozenset(
    {
        "database_acl_grant",
        "schema_acl_grant",
        "public_temporary_revoke",
        "object_privilege_grants",
        "public_trigger_execute_revoke",
    }
)

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
    {"object_classes", "sqag_runtime", "sqag_migrator_to_sqag_app", "provider_controlled", "verification_rule"}
)
RUNTIME_DEFAULT_KEYS = frozenset({"tables", "sequences", "routines"})
COLUMN_PRIVILEGES_TABLE_KEYS = frozenset({"sqag_quote_publication_artifacts"})
COLUMN_PRIVILEGE_ENTRY_KEYS = frozenset({"update"})
DEFAULT_ACL_OBJECT_CLASSES = ["r", "S", "f", "n", "T"]
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
        "effective_runtime_database_privileges",
        "effective_runtime_table_privileges",
        "effective_runtime_column_privileges",
        "effective_runtime_schema_privileges",
        "effective_runtime_routine_privileges",
        "view_acl",
    }
)

# These SQL strings are the reviewed executable-query contracts.  They are
# intentionally kept separate from the manifest: deriving the expected
# sequence from a candidate manifest would allow a query mutation to redefine
# its own admission contract.
CANONICAL_VERIFICATION_QUERY_SQL: dict[str, str] = {
    "database_acl": """
        select datacl
        from pg_catalog.pg_database
        where datname = current_database()
    """,
    "schema_acl": """
        select nspacl
        from pg_catalog.pg_namespace
        where nspname = 'public'
    """,
    "table_acl": """
        select relname, relacl
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where c.relkind = 'r'
          and n.nspname = 'public'
          and c.relname like 'sqag_' || chr(37)
        order by relname
    """,
    "routine_acl": """
        select p.proname,
               pg_get_function_identity_arguments(p.oid) as identity_arguments,
               p.prokind,
               p.prosecdef,
               p.proacl,
               p.proowner,
               r.rolname as owner,
               exists (
                   select 1
                   from pg_catalog.pg_trigger t
                   where t.tgfoid = p.oid
                     and not t.tgisinternal
               ) as has_trigger_dependency
        from pg_catalog.pg_proc p
        join pg_catalog.pg_namespace n on n.oid = p.pronamespace
        join pg_catalog.pg_roles r on r.oid = p.proowner
        where n.nspname = 'public'
          and p.prokind in ('f', 'p', 'a', 'w')
        order by p.proname, identity_arguments
    """,
    "default_acl": """
        select owner.rolname as owner,
               coalesce(ns.nspname, '<global>') as namespace,
               d.defaclobjtype as object_type,
               case
                   when expanded.grantee = 0 then 'PUBLIC'
                   else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text)
               end as grantee,
               expanded.privilege_type,
               expanded.is_grantable
        from pg_catalog.pg_default_acl d
        join pg_catalog.pg_roles owner on owner.oid = d.defaclrole
        left join pg_catalog.pg_namespace ns on ns.oid = d.defaclnamespace
        cross join lateral pg_catalog.aclexplode(d.defaclacl) expanded
        left join pg_catalog.pg_roles grantee_role
          on grantee_role.oid = expanded.grantee and expanded.grantee <> 0
        where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T')
        order by owner, namespace, object_type, grantee, expanded.privilege_type, expanded.is_grantable
    """,
    "role_attributes": """
        select r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb,
               r.rolcanlogin, r.rolreplication, r.rolbypassrls, r.rolconnlimit,
               a.rolpassword is null as password_is_null
        from pg_catalog.pg_roles r
        join pg_catalog.pg_authid a on a.oid = r.oid
        where r.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner')
        order by r.rolname
    """,
    "role_memberships": """
        select r.rolname as role,
               m.rolname as member,
               grantor.rolname as grantor,
               am.admin_option,
               am.inherit_option,
               am.set_option
        from pg_catalog.pg_auth_members am
        join pg_catalog.pg_roles r on r.oid = am.roleid
        join pg_catalog.pg_roles m on m.oid = am.member
        join pg_catalog.pg_roles grantor on grantor.oid = am.grantor
        order by role, member, grantor
    """,
    "sequence_acl": """
        select relname, relacl
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where c.relkind = 'S' and n.nspname = 'public'
        order by relname
    """,
    "effective_runtime_database_privileges": """
        select p.privilege_type,
               has_database_privilege('sqag_runtime', current_database(), p.privilege_type) as effective,
               has_database_privilege(
                   'sqag_runtime',
                   current_database(),
                   p.privilege_type || ' WITH GRANT OPTION'
               ) as is_grantable
        from (values ('CONNECT'), ('CREATE'), ('TEMPORARY')) p(privilege_type)
        order by p.privilege_type
    """,
    "effective_runtime_table_privileges": """
        select n.nspname as schema_name,
               c.relname as table_name,
               p.privilege_type,
               has_table_privilege('sqag_runtime', c.oid, p.privilege_type) as effective,
               has_table_privilege(
                   'sqag_runtime',
                   c.oid,
                   p.privilege_type || ' WITH GRANT OPTION'
               ) as is_grantable
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        cross join (
            values
                ('SELECT'),
                ('INSERT'),
                ('UPDATE'),
                ('DELETE'),
                ('TRUNCATE'),
                ('REFERENCES'),
                ('TRIGGER'),
                ('MAINTAIN')
        ) p(privilege_type)
        where n.nspname = 'public'
          and c.relkind = 'r'
          and c.relname like 'sqag_' || chr(37)
        order by n.nspname, c.relname, p.privilege_type
    """,
    "effective_runtime_column_privileges": """
        select n.nspname as schema_name,
               c.relname as table_name,
               a.attname as column_name,
               p.privilege_type,
               has_column_privilege('sqag_runtime', c.oid, a.attname, p.privilege_type) as effective,
               has_column_privilege(
                   'sqag_runtime',
                   c.oid,
                   a.attname,
                   p.privilege_type || ' WITH GRANT OPTION'
               ) as is_grantable
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        join pg_catalog.pg_attribute a on a.attrelid = c.oid
        cross join (
            values
                ('SELECT'),
                ('INSERT'),
                ('UPDATE'),
                ('REFERENCES')
        ) p(privilege_type)
        where n.nspname = 'public'
          and c.relkind = 'r'
          and c.relname like 'sqag_' || chr(37)
          and a.attnum > 0
          and not a.attisdropped
        order by n.nspname, c.relname, a.attname, p.privilege_type
    """,
    "effective_runtime_schema_privileges": """
        select p.privilege_type,
               has_schema_privilege('sqag_runtime', 'public', p.privilege_type) as effective,
               has_schema_privilege(
                   'sqag_runtime',
                   'public',
                   p.privilege_type || ' WITH GRANT OPTION'
               ) as is_grantable
        from (values ('USAGE'), ('CREATE')) p(privilege_type)
        order by p.privilege_type
    """,
    "effective_runtime_routine_privileges": """
        select p.proname as routine_name,
               has_function_privilege('sqag_runtime', p.oid, 'EXECUTE') as effective
        from pg_catalog.pg_proc p
        join pg_catalog.pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
        order by p.proname
    """,
    "view_acl": """
        select c.relname as view_name,
               c.relacl as view_acl
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where c.relkind = 'v'
          and n.nspname = 'public'
        order by c.relname
    """,
}

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

LEGACY_VIEWS = frozenset({"sqag_quote_artifacts"})
LEGACY_VIEW_DETAILS = {
    "sqag_quote_artifacts": (
        "legacy_publication_backfill",
        "webapp.server.DatabaseSqagStorage.publish_quote_session_forensic_transaction",
    ),
}

BOUNDARY_B_OPERATION_AUTHORITY = {
    "database_acl_grant": "database_owner_authority",
    "schema_acl_grant": "database_owner_authority",
    "public_temporary_revoke": "database_owner_authority",
    "object_privilege_grants": "object_owner",
    "public_trigger_execute_revoke": "object_owner",
}

ROLE_DESCRIPTIONS = {
    "runtime": "Restricted application runtime role. Dormant NOLOGIN during Boundary A/B. Activated with LOGIN in #160 only after independent verification.",
    "migrator": "Owner/operator authority. Owns all application database objects. Applies ACL changes. Cannot create roles.",
    "legacy": "Legacy active rollback role. Retained until separately gated retirement after #160 switch and observation window.",
    "provider": "Provider/control-plane role. Creates/alters roles through separately authorised provider authority. Unchanged by SQAG.",
}

REQUIRED_QUERY_FEATURES: dict[str, tuple[str, ...]] = {
    "database_acl": ("pg_catalog.pg_database", "datname", "datacl", "current_database"),
    "schema_acl": ("pg_catalog.pg_namespace", "nspname", "nspacl", "'public'"),
    "table_acl": ("pg_catalog.pg_class", "pg_catalog.pg_namespace", "relacl", "relkind", "'public'", "order by"),
    "role_attributes": (
        "pg_catalog.pg_roles",
        "pg_catalog.pg_authid",
        "rolname",
        "rolsuper",
        "rolcanlogin",
        "rolconnlimit",
        "rolpassword",
        "password_is_null",
        "is",
        "null",
    ),
    "role_memberships": (
        "pg_catalog.pg_auth_members",
        "pg_catalog.pg_roles",
        "grantor",
        "admin_option",
        "inherit_option",
        "set_option",
    ),
    "sequence_acl": ("pg_catalog.pg_class", "relkind", "'s'", "pg_catalog.pg_namespace", "relacl"),
    "effective_runtime_database_privileges": (
        "has_database_privilege",
        "current_database",
        "privilege_type",
        "is_grantable",
        "' WITH GRANT OPTION'",
        "'connect'",
        "'create'",
        "'temporary'",
    ),
    "effective_runtime_table_privileges": (
        "has_table_privilege",
        "pg_catalog.pg_class",
        "pg_catalog.pg_namespace",
        "c.relname",
        "is_grantable",
        "' WITH GRANT OPTION'",
        "'select'",
        "'insert'",
        "'update'",
        "'delete'",
        "'truncate'",
        "'references'",
        "'trigger'",
        "'maintain'",
    ),
    "effective_runtime_column_privileges": (
        "has_column_privilege",
        "pg_catalog.pg_attribute",
        "attname",
        "attnum",
        "attisdropped",
        "is_grantable",
        "' WITH GRANT OPTION'",
        "'select'",
        "'insert'",
        "'update'",
        "'references'",
    ),
    "effective_runtime_schema_privileges": ("has_schema_privilege", "'public'", "'usage'", "'create'"),
    "effective_runtime_routine_privileges": ("has_function_privilege", "pg_catalog.pg_proc", "'public'", "'execute'"),
    "view_acl": ("pg_catalog.pg_class", "pg_catalog.pg_namespace", "relname", "relacl", "relkind", "'public'", "order by"),
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


def _validate_provider_control_edge_policy(
    runtime: Any,
    errors: list[str],
    *,
    enforce_production_identity: bool = True,
) -> dict[str, Any] | None:
    if not isinstance(runtime, dict):
        _add_error(errors, "runtime_role_must_be_object")
        return None
    edges = runtime.get("provider_control_edges")
    if type(edges) is not list:
        _add_error(errors, "provider_control_edges_must_be_list")
        return None
    if len(edges) != 1:
        _add_error(errors, f"provider_control_edges_count_invalid_expected_1_got_{len(edges)}")
    if not edges:
        return None
    edge = edges[0]
    if not _exact_keys(edge, PROVIDER_CONTROL_EDGE_KEYS, "provider_control_edge_0", errors):
        return None
    for key in ("parent_role", "member_role", "grantor"):
        _require_non_empty_string(edge.get(key), f"provider_control_edge_0_{key}", errors)
    for key, expected in {
        "admin_option": True,
        "inherit_option": False,
        "set_option": False,
        "classification": PROVIDER_CONTROL_CLASSIFICATION,
        "security_rationale": PROVIDER_CONTROL_SECURITY_RATIONALE,
    }.items():
        _exact_value(edge.get(key), expected, f"provider_control_edge_0_{key}", errors)
    if enforce_production_identity:
        for key in ("parent_role", "member_role", "grantor"):
            _exact_value(
                edge.get(key),
                PRODUCTION_PROVIDER_CONTROL_EDGE[key],
                f"provider_control_edge_0_{key}",
                errors,
            )
    return edge


def validate_runtime_membership_edges(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    enforce_production_identity: bool = True,
) -> tuple[str, ...]:
    """Evaluate the complete membership result without pre-filtering rows.

    Unrelated cluster membership rows are retained during the scan. Every
    well-formed row is classified as the exact provider-control edge, a
    protected-role row, or a truly unrelated row before the complete graph is
    evaluated. A row with a protected participant is never ignored because the
    runtime is absent from its parent/member positions.
    """

    errors: list[str] = []
    roles = manifest.get("roles") if isinstance(manifest, dict) else None
    runtime = roles.get("runtime") if isinstance(roles, dict) else None
    edge = _validate_provider_control_edge_policy(
        runtime,
        errors,
        enforce_production_identity=enforce_production_identity,
    )
    if edge is None:
        return tuple(errors)

    expected_row = {
        "role": edge["parent_role"],
        "member": edge["member_role"],
        "grantor": edge["grantor"],
        "admin_option": edge["admin_option"],
        "inherit_option": edge["inherit_option"],
        "set_option": edge["set_option"],
    }
    if type(rows) is not list:
        return (*errors, "role_membership_rows_must_be_list")

    runtime_name = str(edge["parent_role"])
    identity_protected_roles = (
        PROTECTED_PRODUCTION_ROLES if enforce_production_identity else frozenset()
    )
    protected_roles = identity_protected_roles | {
        str(edge["parent_role"]),
        str(edge["member_role"]),
        str(edge["grantor"]),
    }
    runtime_rows: list[dict[str, Any]] = []
    protected_rows: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str, str, bool, bool, bool]] = set()
    graph: dict[str, set[str]] = {}
    for index, row in enumerate(rows):
        label = f"role_membership_row_{index}"
        if not _exact_keys(row, MEMBERSHIP_ROW_KEYS, label, errors):
            continue
        for key in ("role", "member", "grantor"):
            _require_non_empty_string(row.get(key), f"{label}_{key}", errors)
        for key in ("admin_option", "inherit_option", "set_option"):
            _require_type(row.get(key), bool, f"{label}_{key}", errors)
        if any(type(row.get(key)) is not str for key in ("role", "member", "grantor")):
            continue
        if any(type(row.get(key)) is not bool for key in ("admin_option", "inherit_option", "set_option")):
            continue

        parent = str(row["role"])
        member = str(row["member"])
        grantor = str(row["grantor"])
        row_tuple = (
            parent,
            member,
            grantor,
            row["admin_option"],
            row["inherit_option"],
            row["set_option"],
        )
        if row_tuple in seen_rows:
            _add_error(errors, f"{label}_duplicate_role_membership_row")
        seen_rows.add(row_tuple)

        graph.setdefault(member, set()).add(parent)
        participants = {parent, member, grantor}
        is_expected_edge = row == expected_row
        protected_participants = participants & protected_roles
        if is_expected_edge or protected_participants:
            protected_rows.append(row)
        if runtime_name in participants:
            runtime_rows.append(row)
        if is_expected_edge:
            continue

        if member == runtime_name:
            _add_error(errors, f"{label}_runtime_as_member_forbidden")
            _add_error(errors, f"{label}_runtime_privilege_membership_path_forbidden")
            if row["inherit_option"]:
                _add_error(errors, f"{label}_runtime_inherit_path_forbidden")
            if row["set_option"]:
                _add_error(errors, f"{label}_runtime_set_path_forbidden")

        if protected_participants:
            _add_error(errors, f"{label}_protected_role_edge_forbidden")
            if grantor in protected_roles:
                _add_error(errors, f"{label}_protected_grantor_forbidden")
            if row["admin_option"]:
                _add_error(errors, f"{label}_protected_admin_option_forbidden")
            if row["inherit_option"]:
                _add_error(errors, f"{label}_protected_inherit_option_forbidden")
            if row["set_option"]:
                _add_error(errors, f"{label}_protected_set_option_forbidden")
            for participant in sorted(participants - protected_roles):
                _add_error(
                    errors,
                    f"{label}_unknown_protected_edge_participant_{participant}",
                )
            for forbidden_role in ("neon_superuser", "sqag_app", "sqag_migrator"):
                if forbidden_role in {parent, member}:
                    _add_error(errors, f"{label}_forbidden_provider_control_role_{forbidden_role}")

    if len(runtime_rows) != 1:
        _add_error(errors, f"runtime_edge_count_invalid_expected_1_got_{len(runtime_rows)}")
    if len(runtime_rows) == 1 and runtime_rows[0] != expected_row:
        _add_error(errors, "provider_control_edge_tuple_mismatch")
    elif len(runtime_rows) > 1:
        for index, row in enumerate(runtime_rows):
            if row != expected_row:
                _add_error(errors, f"provider_control_edge_tuple_mismatch_at_runtime_edge_{index}")

    if len(protected_rows) != 1:
        _add_error(
            errors,
            f"protected_role_row_count_invalid_expected_1_got_{len(protected_rows)}",
        )

    def reachable_cycle(start: str) -> bool:
        visited: set[str] = set()
        active: set[str] = set()

        def visit(current: str) -> bool:
            if current in active:
                return True
            if current in visited:
                return False
            visited.add(current)
            active.add(current)
            try:
                return any(visit(parent) for parent in graph.get(current, set()))
            finally:
                active.remove(current)

        return visit(start)

    for protected_role in sorted(protected_roles):
        if reachable_cycle(protected_role):
            _add_error(
                errors,
                f"recursive_protected_role_membership_path_forbidden_{protected_role}",
            )
            if protected_role == runtime_name:
                _add_error(errors, "recursive_runtime_membership_path_forbidden")

    return tuple(errors)


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
        _check_exact_string_list(
            runtime.get("memberships_as_member"), [], "runtime_memberships_as_member", errors
        )
        _check_exact_string_list(runtime.get("inherited_roles"), [], "runtime_inherited_roles", errors)
        _check_exact_string_list(
            runtime.get("set_assumable_roles"), [], "runtime_set_assumable_roles", errors
        )
        _check_exact_string_list(
            runtime.get("membership_derived_privileges"),
            [],
            "runtime_membership_derived_privileges",
            errors,
        )
        _validate_provider_control_edge_policy(runtime, errors)
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


def validate_column_privileges(manifest: dict[str, Any], errors: list[str]) -> None:
    column_privileges = manifest.get("column_privileges")
    if not _exact_keys(column_privileges, COLUMN_PRIVILEGES_TABLE_KEYS, "column_privileges", errors):
        if not isinstance(column_privileges, dict):
            return
    entry = column_privileges.get("sqag_quote_publication_artifacts")
    label = "column_privileges_sqag_quote_publication_artifacts"
    if not _exact_keys(entry, COLUMN_PRIVILEGE_ENTRY_KEYS, label, errors):
        if not isinstance(entry, dict):
            return
    _check_exact_string_list(
        entry.get("update"),
        ["checksum_sha256"],
        f"{label}_update",
        errors,
    )
    tables = manifest.get("tables")
    runtime_accessible = tables.get("runtime_accessible") if isinstance(tables, dict) else None
    table_entry = (
        runtime_accessible.get("sqag_quote_publication_artifacts", {})
        if isinstance(runtime_accessible, dict)
        else {}
    )
    table_privileges = table_entry.get("privileges", {}) if isinstance(table_entry, dict) else {}
    _exact_value(
        table_privileges.get("update"),
        False,
        "column_privileges_publication_artifacts_table_update",
        errors,
    )


def validate_views(manifest: dict[str, Any], errors: list[str]) -> None:
    views = manifest.get("views")
    if not _exact_keys(views, VIEWS_KEYS, "views", errors):
        if not isinstance(views, dict):
            return
    _exact_value(views.get("count"), 1, "view_count", errors)
    accessible = views.get("runtime_accessible")
    if not isinstance(accessible, dict):
        _add_error(errors, "runtime_accessible_views_must_be_object")
        return
    actual = set(accessible)
    if actual != LEGACY_VIEWS:
        _add_error(
            errors,
            f"view_set_mismatch_extra_{sorted(actual - LEGACY_VIEWS)}_missing_{sorted(LEGACY_VIEWS - actual)}",
        )
    for view_name, entry in accessible.items():
        label = f"accessible_view_{view_name}"
        if not _exact_keys(entry, ACCESSIBLE_VIEW_KEYS, label, errors):
            if not isinstance(entry, dict):
                continue
        expected_class, expected_source = LEGACY_VIEW_DETAILS.get(view_name, (None, None))
        _exact_value(entry.get("schema"), "public", f"{label}_schema", errors)
        _exact_value(entry.get("class"), expected_class, f"{label}_class", errors)
        _exact_value(
            entry.get("production_source"),
            expected_source,
            f"{label}_production_source",
            errors,
        )
        _exact_value(entry.get("bound"), True, f"{label}_bound", errors)
        privileges = entry.get("privileges")
        if not _exact_keys(privileges, VIEW_PRIVILEGE_KEYS, f"{label}_privileges", errors):
            if not isinstance(privileges, dict):
                continue
        _exact_value(privileges.get("select"), True, f"{label}_select", errors)


def validate_boundary_b(manifest: dict[str, Any], errors: list[str]) -> None:
    boundary = manifest.get("boundary_b")
    if not _exact_keys(boundary, BOUNDARY_B_KEYS, "boundary_b", errors):
        if not isinstance(boundary, dict):
            return
    _exact_value(boundary.get("requires_postgresql17"), True, "boundary_b_requires_postgresql17", errors)
    _exact_value(boundary.get("runtime_role"), "sqag_runtime", "boundary_b_runtime_role", errors)
    _exact_value(boundary.get("object_owner"), "sqag_migrator", "boundary_b_object_owner", errors)
    _exact_value(
        boundary.get("database_owner_authority"),
        "database_owner",
        "boundary_b_database_owner_authority",
        errors,
    )
    _exact_value(
        boundary.get("authority_input_model"),
        "variable_reference_only",
        "boundary_b_authority_input_model",
        errors,
    )
    _exact_value(boundary.get("fail_closed"), True, "boundary_b_fail_closed", errors)
    _exact_value(boundary.get("idempotent_rerun"), True, "boundary_b_idempotent_rerun", errors)
    operations = boundary.get("operations")
    if not _exact_keys(operations, BOUNDARY_B_OPERATION_KEYS, "boundary_b_operations", errors):
        if not isinstance(operations, dict):
            return
    for operation, authority in BOUNDARY_B_OPERATION_AUTHORITY.items():
        _exact_value(operations.get(operation), authority, f"boundary_b_{operation}", errors)


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
    _check_exact_string_list(
        default_privileges.get("object_classes"),
        DEFAULT_ACL_OBJECT_CLASSES,
        "default_privilege_object_classes",
        errors,
    )
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


class SQLLexError(ValueError):
    """Raised when a verification query cannot be lexed deterministically."""


@dataclass(frozen=True)
class SQLToken:
    kind: str
    value: str
    position: int


_DOLLAR_QUOTE_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_SQL_MULTI_SYMBOLS = ("::", "||", "<=", ">=", "<>", "!=", "=>", "->", "#>")
_READ_ONLY_FORBIDDEN_WORDS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "truncate",
        "create",
        "alter",
        "drop",
        "grant",
        "revoke",
        "comment",
        "call",
        "copy",
        "begin",
        "start",
        "commit",
        "rollback",
        "abort",
        "savepoint",
        "release",
        "set",
        "set_config",
        "reset",
        "show",
        "prepare",
        "execute",
        "do",
        "vacuum",
        "analyze",
        "refresh",
        "lock",
        "listen",
        "notify",
        "discard",
    }
)


def lex_sql(query: str) -> tuple[SQLToken, ...]:
    """Tokenize only the bounded SQL needed by the read-only contract.

    Comments and quoted bodies are represented as non-code tokens, so words
    inside them cannot satisfy a catalog/query-shape assertion. This is a
    lexer, not a general SQL parser.
    """

    if type(query) is not str:
        raise SQLLexError("query_not_string")
    tokens: list[SQLToken] = []
    index = 0
    length = len(query)
    while index < length:
        char = query[index]
        if char.isspace():
            index += 1
            continue
        if query.startswith("--", index):
            newline = query.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if query.startswith("/*", index):
            start = index
            index += 2
            depth = 1
            while index < length and depth:
                if query.startswith("/*", index):
                    depth += 1
                    index += 2
                elif query.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                raise SQLLexError(f"unterminated_block_comment_at_{start}")
            tokens.append(SQLToken("COMMENT", "", start))
            continue
        if char == "'":
            start = index
            index += 1
            value: list[str] = []
            closed = False
            while index < length:
                if query[index] == "\\" and index + 1 < length:
                    value.extend((query[index], query[index + 1]))
                    index += 2
                elif query[index] == "'":
                    if index + 1 < length and query[index + 1] == "'":
                        value.append("'")
                        index += 2
                    else:
                        index += 1
                        closed = True
                        break
                else:
                    value.append(query[index])
                    index += 1
            if not closed:
                raise SQLLexError(f"unterminated_single_quote_at_{start}")
            tokens.append(SQLToken("STRING", "".join(value), start))
            continue
        if char == '"':
            start = index
            index += 1
            value = []
            closed = False
            while index < length:
                if query[index] == '"':
                    if index + 1 < length and query[index + 1] == '"':
                        value.append('"')
                        index += 2
                    else:
                        index += 1
                        closed = True
                        break
                else:
                    value.append(query[index])
                    index += 1
            if not closed:
                raise SQLLexError(f"unterminated_double_quote_at_{start}")
            tokens.append(SQLToken("QUOTED_IDENTIFIER", "".join(value), start))
            continue
        if char == "$":
            match = _DOLLAR_QUOTE_TAG.match(query, index)
            if match:
                delimiter = match.group(0)
                start = index
                body_start = index + len(delimiter)
                body_end = query.find(delimiter, body_start)
                if body_end < 0:
                    raise SQLLexError(f"unterminated_dollar_quote_at_{start}")
                tokens.append(SQLToken("DOLLAR_QUOTE", query[body_start:body_end], start))
                index = body_end + len(delimiter)
                continue
        if char.isalpha() or char == "_":
            start = index
            index += 1
            while index < length and (query[index].isalnum() or query[index] in {"_", "$"}):
                index += 1
            tokens.append(SQLToken("WORD", query[start:index], start))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < length and (query[index].isdigit() or query[index] == "."):
                index += 1
            tokens.append(SQLToken("NUMBER", query[start:index], start))
            continue
        matched_symbol = next((symbol for symbol in _SQL_MULTI_SYMBOLS if query.startswith(symbol, index)), None)
        if matched_symbol is not None:
            tokens.append(SQLToken("SYMBOL", matched_symbol, index))
            index += len(matched_symbol)
            continue
        tokens.append(SQLToken("SYMBOL", char, index))
        index += 1
    return tuple(tokens)


def _token_is_word(token: SQLToken, value: str) -> bool:
    return token.kind == "WORD" and token.value.lower() == value.lower()


def _token_matches(token: SQLToken, expected: str | tuple[str, str]) -> bool:
    if isinstance(expected, tuple):
        kind, value = expected
        if kind == "STRING_EXACT":
            return token.kind == "STRING" and token.value == value
        if kind == "STRING":
            return token.kind == "STRING" and token.value.lower() == value.lower()
        return token.kind == kind and token.value.lower() == value.lower()
    if token.kind == "WORD":
        return token.value.lower() == expected.lower()
    return token.value == expected


def _normalise_executable_tokens(tokens: tuple[SQLToken, ...] | list[SQLToken]) -> tuple[tuple[str, str], ...]:
    """Return the executable structure with presentation-only differences removed."""
    return tuple(
        (
            token.kind,
            token.value.lower() if token.kind == "WORD" else token.value,
        )
        for token in tokens
        if token.kind != "COMMENT"
    )


def _find_token_pattern(
    tokens: tuple[SQLToken, ...] | list[SQLToken], pattern: list[str | tuple[str, str]]
) -> bool:
    if not pattern or len(tokens) < len(pattern):
        return False
    return any(
        all(_token_matches(tokens[index + offset], expected) for offset, expected in enumerate(pattern))
        for index in range(len(tokens) - len(pattern) + 1)
    )


def _count_token_pattern(
    tokens: tuple[SQLToken, ...] | list[SQLToken], pattern: list[str | tuple[str, str]]
) -> int:
    if not pattern or len(tokens) < len(pattern):
        return 0
    return sum(
        all(_token_matches(tokens[index + offset], expected) for offset, expected in enumerate(pattern))
        for index in range(len(tokens) - len(pattern) + 1)
    )


def _qualified_pattern(value: str) -> list[str]:
    return [part for index, part in enumerate(value.split(".")) for part in (([".", part] if index else [part]))]


def _feature_pattern(feature: str) -> list[str | tuple[str, str]]:
    if len(feature) >= 2 and feature[0] == "'" and feature[-1] == "'":
        return [("STRING", feature[1:-1])]
    if " " in feature:
        return feature.lower().split()
    if "." in feature:
        return _qualified_pattern(feature.lower())
    return [feature.lower()]


def _require_sql_features(
    tokens: tuple[SQLToken, ...], key: str, features: tuple[str, ...], errors: list[str]
) -> None:
    for feature in features:
        if not _find_token_pattern(tokens, _feature_pattern(feature)):
            _add_error(errors, f"verification_query_{key}_missing_semantic_feature_{feature}")


def _read_only_query_tokens(query: str, key: str, errors: list[str]) -> tuple[SQLToken, ...] | None:
    try:
        tokens = [token for token in lex_sql(query) if token.kind != "COMMENT"]
    except SQLLexError as exc:
        _add_error(errors, f"verification_query_{key}_lexical_error_{exc}")
        return None
    semicolons = [index for index, token in enumerate(tokens) if token.value == ";"]
    if semicolons:
        if len(semicolons) != 1 or semicolons[0] != len(tokens) - 1:
            _add_error(errors, f"verification_query_{key}_must_be_single_executable_statement")
            return None
        tokens.pop()
    if not tokens:
        _add_error(errors, f"verification_query_{key}_must_contain_one_read_only_query")
        return None
    if not _token_is_word(tokens[0], "select"):
        _add_error(errors, f"verification_query_{key}_must_be_single_read_only_select")
    forbidden = sorted(
        {
            token.value.lower()
            for token in tokens
            if token.kind in {"WORD", "QUOTED_IDENTIFIER"} and token.value.lower() in _READ_ONLY_FORBIDDEN_WORDS
        }
    )
    if forbidden:
        _add_error(errors, f"verification_query_{key}_contains_forbidden_executable_words_{','.join(forbidden)}")
    return tuple(tokens)


EXPECTED_VERIFICATION_QUERY_TOKENS: dict[str, tuple[tuple[str, str], ...]] = {
    key: _normalise_executable_tokens(lex_sql(query))
    for key, query in CANONICAL_VERIFICATION_QUERY_SQL.items()
}


def _validate_exact_query_contract(query: str, key: str, errors: list[str]) -> None:
    """Require the complete reviewed executable token sequence for one key."""
    tokens = _read_only_query_tokens(query, key, errors)
    if tokens is None:
        return
    expected = EXPECTED_VERIFICATION_QUERY_TOKENS.get(key)
    if expected is None or _normalise_executable_tokens(tokens) != expected:
        _add_error(errors, f"verification_query_{key}_executable_structure_mismatch")


def _top_level_index(tokens: tuple[SQLToken, ...], word: str) -> int | None:
    depth = 0
    for index, token in enumerate(tokens):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and _token_is_word(token, word):
            return index
    return None


def _split_top_level(tokens: tuple[SQLToken, ...]) -> list[tuple[SQLToken, ...]]:
    parts: list[tuple[SQLToken, ...]] = []
    current: list[SQLToken] = []
    depth = 0
    for token in tokens:
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        if token.value == "," and depth == 0:
            parts.append(tuple(current))
            current = []
        else:
            current.append(token)
    parts.append(tuple(current))
    return parts


def _projection_alias(part: tuple[SQLToken, ...]) -> str | None:
    for index in range(len(part) - 2, -1, -1):
        if _token_is_word(part[index], "as") and part[index + 1].kind in {"WORD", "QUOTED_IDENTIFIER"}:
            return part[index + 1].value.lower()
    for token in reversed(part):
        if token.kind in {"WORD", "QUOTED_IDENTIFIER"}:
            return token.value.lower()
    return None


def _projection_parts(tokens: tuple[SQLToken, ...], key: str, errors: list[str]) -> list[tuple[SQLToken, ...]] | None:
    from_index = _top_level_index(tokens, "from")
    if from_index is None or from_index <= 1:
        _add_error(errors, f"verification_query_{key}_must_have_top_level_from")
        return None
    parts = _split_top_level(tokens[1:from_index])
    if any(not part for part in parts):
        _add_error(errors, f"verification_query_{key}_projection_contains_empty_column")
        return None
    return parts


def _require_projection_shape(
    parts: list[tuple[SQLToken, ...]],
    expected_aliases: list[str],
    key: str,
    errors: list[str],
) -> None:
    aliases = [_projection_alias(part) for part in parts]
    if aliases != expected_aliases:
        _add_error(errors, f"verification_query_{key}_projected_columns_invalid_expected_{expected_aliases}_got_{aliases}")


def _validate_default_acl_query(query: str, errors: list[str]) -> None:
    tokens = _read_only_query_tokens(query, "default_acl", errors)
    if tokens is None:
        return
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
    _require_sql_features(tokens, "default_acl", required, errors)
    parts = _projection_parts(tokens, "default_acl", errors)
    if parts is not None:
        _require_projection_shape(parts, ["owner", "namespace", "object_type", "grantee", "privilege_type", "is_grantable"], "default_acl", errors)
        for index, pattern in {
            0: _qualified_pattern("owner.rolname"),
            1: _qualified_pattern("ns.nspname"),
            2: _qualified_pattern("d.defaclobjtype"),
            4: _qualified_pattern("expanded.privilege_type"),
            5: _qualified_pattern("expanded.is_grantable"),
        }.items():
            if index < len(parts) and not _find_token_pattern(parts[index], pattern):
                _add_error(errors, f"verification_query_default_acl_projection_{index}_missing_expected_expression")
        if len(parts) > 3 and (
            not _find_token_pattern(
                parts[3],
                ["case", "when", *_qualified_pattern("expanded.grantee"), "=", "0", "then", ("STRING", "public")],
            )
            or not _find_token_pattern(parts[3], ["end", "as", "grantee"])
        ):
            _add_error(errors, "verification_query_default_acl_projection_grantee_missing_public_mapping")
    if not _find_token_pattern(tokens, ["from", *_qualified_pattern("pg_catalog.pg_default_acl"), "d"]):
        _add_error(errors, "verification_query_default_acl_must_read_pg_default_acl")
    if not _find_token_pattern(tokens, ["join", *_qualified_pattern("pg_catalog.pg_roles"), "owner"]):
        _add_error(errors, "verification_query_default_acl_must_join_owner_role")
    if not _find_token_pattern(tokens, ["left", "join", *_qualified_pattern("pg_catalog.pg_namespace"), "ns"]):
        _add_error(errors, "verification_query_default_acl_must_left_join_namespace")
    lateral_pattern = ["cross", "join", "lateral", *_qualified_pattern("pg_catalog.aclexplode"), "("]
    if _count_token_pattern(tokens, lateral_pattern) != 1:
        _add_error(errors, "verification_query_default_acl_requires_exactly_one_cross_join_lateral_aclexplode")
    if not _find_token_pattern(tokens, ["left", "join", *_qualified_pattern("pg_catalog.pg_roles"), "grantee_role"]):
        _add_error(errors, "verification_query_default_acl_must_left_join_named_grantees")
    if not _find_token_pattern(
        tokens,
        [
            "where", "d", ".", "defaclobjtype", "in", "(",
            ("STRING_EXACT", "r"), ",", ("STRING_EXACT", "S"), ",",
            ("STRING_EXACT", "f"), ",", ("STRING_EXACT", "n"), ",",
            ("STRING_EXACT", "T"), ")",
        ],
    ):
        _add_error(errors, "verification_query_default_acl_must_cover_r_s_f_n_T_object_types")
    if _find_token_pattern(tokens, ["::", "regrole"]) or _find_token_pattern(tokens, ["::", "name"]):
        _add_error(errors, "verification_query_default_acl_must_not_cast_absent_role_names")
    if not _find_token_pattern(tokens, ["order", "by", "owner", ",", "namespace", ",", "object_type", ",", "grantee", ",", "expanded", ".", "privilege_type", ",", "expanded", ".", "is_grantable"]):
        _add_error(errors, "verification_query_default_acl_must_order_deterministically")


def _validate_role_attributes_query(query: str, errors: list[str]) -> None:
    tokens = _read_only_query_tokens(query, "role_attributes", errors)
    if tokens is None:
        return
    required = (
        "pg_catalog.pg_roles",
        "pg_catalog.pg_authid",
        "rolname",
        "rolsuper",
        "rolinherit",
        "rolcreaterole",
        "rolcreatedb",
        "rolcanlogin",
        "rolreplication",
        "rolbypassrls",
        "rolconnlimit",
        "rolpassword",
        "password_is_null",
        "order by",
    )
    _require_sql_features(tokens, "role_attributes", required, errors)
    parts = _projection_parts(tokens, "role_attributes", errors)
    if parts is not None:
        _require_projection_shape(
            parts,
            [
                "rolname", "rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb",
                "rolcanlogin", "rolreplication", "rolbypassrls", "rolconnlimit",
                "password_is_null",
            ],
            "role_attributes",
            errors,
        )
        if parts and not _find_token_pattern(parts[0], ["r", ".", "rolname"]):
            _add_error(errors, "verification_query_role_attributes_projection_role_name_invalid")
        if len(parts) > 9 and not _find_token_pattern(
            parts[9], ["a", ".", "rolpassword", "is", "null", "as", "password_is_null"]
        ):
            _add_error(errors, "verification_query_role_attributes_password_state_must_be_boolean_null_assertion")
    if not _find_token_pattern(tokens, ["from", *_qualified_pattern("pg_catalog.pg_roles"), "r"]):
        _add_error(errors, "verification_query_role_attributes_must_read_pg_roles")
    if not _find_token_pattern(tokens, ["join", *_qualified_pattern("pg_catalog.pg_authid"), "a"]):
        _add_error(errors, "verification_query_role_attributes_must_read_pg_authid")
    if not _find_token_pattern(tokens, ["on", "a", ".", "oid", "=", "r", ".", "oid"]):
        _add_error(errors, "verification_query_role_attributes_must_join_authid_by_oid")
    if not _find_token_pattern(tokens, ["where", "r", ".", "rolname", "in"]):
        _add_error(errors, "verification_query_role_attributes_must_filter_declared_roles")
    if not _find_token_pattern(tokens, ["order", "by", "r", ".", "rolname"]):
        _add_error(errors, "verification_query_role_attributes_must_order_by_role_name")


def _validate_routine_query(query: str, errors: list[str]) -> None:
    tokens = _read_only_query_tokens(query, "routine_acl", errors)
    if tokens is None:
        return
    required = (
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
    )
    _require_sql_features(tokens, "routine_acl", required, errors)
    parts = _projection_parts(tokens, "routine_acl", errors)
    if parts is not None:
        expected_aliases = ["proname", "identity_arguments", "prokind", "prosecdef", "proacl", "proowner", "owner", "has_trigger_dependency"]
        _require_projection_shape(parts, expected_aliases, "routine_acl", errors)
        projection_patterns = {
            0: _qualified_pattern("p.proname"),
            1: ["pg_get_function_identity_arguments", "(", "p", ".", "oid", ")", "as", "identity_arguments"],
            2: _qualified_pattern("p.prokind"),
            3: _qualified_pattern("p.prosecdef"),
            4: _qualified_pattern("p.proacl"),
            5: _qualified_pattern("p.proowner"),
            6: ["r", ".", "rolname", "as", "owner"],
            7: ["exists", "(", "select", "1", "from", *_qualified_pattern("pg_catalog.pg_trigger"), "t"],
        }
        for index, pattern in projection_patterns.items():
            if index < len(parts) and not _find_token_pattern(parts[index], pattern):
                _add_error(errors, f"verification_query_routine_acl_projection_{index}_missing_expected_expression")
    if not _find_token_pattern(tokens, ["from", *_qualified_pattern("pg_catalog.pg_proc"), "p"]):
        _add_error(errors, "verification_query_routine_acl_must_read_pg_proc")
    if not _find_token_pattern(tokens, ["join", *_qualified_pattern("pg_catalog.pg_namespace"), "n"]):
        _add_error(errors, "verification_query_routine_acl_must_join_namespace")
    if not _find_token_pattern(tokens, ["join", *_qualified_pattern("pg_catalog.pg_roles"), "r"]):
        _add_error(errors, "verification_query_routine_acl_must_join_owner_roles")
    if not _find_token_pattern(tokens, ["from", *_qualified_pattern("pg_catalog.pg_trigger"), "t"]):
        _add_error(errors, "verification_query_routine_acl_must_read_trigger_catalog")
    if not _find_token_pattern(tokens, ["where", "n", ".", "nspname", "=", ("STRING", "public")]):
        _add_error(errors, "verification_query_routine_acl_must_define_public_schema_boundary")
    if not _find_token_pattern(tokens, ["p", ".", "prokind", "in", "(", ("STRING_EXACT", "f"), ",", ("STRING_EXACT", "p"), ",", ("STRING_EXACT", "a"), ",", ("STRING_EXACT", "w"), ")"]):
        _add_error(errors, "verification_query_routine_acl_must_cover_all_user_defined_routine_kinds")
    if _find_token_pattern(tokens, ["proname", "like"]):
        _add_error(errors, "verification_query_routine_acl_must_not_prefix_filter_routines")
    if not _find_token_pattern(tokens, ["order", "by", "p", ".", "proname", ",", "identity_arguments"]):
        _add_error(errors, "verification_query_routine_acl_must_order_deterministically")


def validate_verification_queries(manifest: dict[str, Any], errors: list[str]) -> None:
    queries = manifest.get("verification_queries")
    if set(CANONICAL_VERIFICATION_QUERY_SQL) != set(VERIFICATION_QUERY_KEYS):
        _add_error(errors, "repository_expected_verification_query_contract_keys_mismatch")
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
        elif key == "role_attributes":
            _validate_role_attributes_query(value, errors)
        elif key == "routine_acl":
            _validate_routine_query(value, errors)
        elif key in REQUIRED_QUERY_FEATURES:
            tokens = _read_only_query_tokens(value, key, errors)
            if tokens is not None:
                _require_sql_features(tokens, key, REQUIRED_QUERY_FEATURES[key], errors)
        _validate_exact_query_contract(value, key, errors)
    table_query = queries.get("effective_runtime_table_privileges")
    if type(table_query) is str and table_query.strip():
        tokens = _read_only_query_tokens(table_query, "effective_runtime_table_privileges", errors)
        if tokens is not None:
            _require_sql_features(
                tokens,
                "effective_runtime_table_privileges",
                ("has_table_privilege", "pg_catalog.pg_class", "is_grantable", "'sqag_runtime'", "'public'"),
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
    validate_column_privileges(manifest, errors)
    validate_views(manifest, errors)
    validate_sequences(manifest, errors)
    validate_routines(manifest, errors)
    validate_database_acl(manifest, errors)
    validate_schema_acl(manifest, errors)
    validate_default_privileges(manifest, errors)
    validate_boundary_b(manifest, errors)
    validate_verification_queries(manifest, errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 2

    _ok("Runtime privilege contract manifest passes strict validation.")
    return 0

# A22R2 finality coverage and comparison-policy closure
FINALITY_MANIFEST_PATH = ROOT / "docs" / "postgresql17-finality-coverage.json"
FINALITY_TOP_LEVEL_KEYS = frozenset(
    {
        "$schema",
        "schema_version",
        "contract_type",
        "repository",
        "design_lock",
        "postgresql_major",
        "source",
        "queries",
        "handling_classes",
        "comparison_policies",
        "field_registry",
        "reference_proof",
        "historical_review_mapping",
        "class_level_red_receipts",
    }
)
FINALITY_CLASS_IDS = (
    "exact_semantic_value",
    "stable_normalized_identity_reference_edge",
    "canonicalized_collection_set_order",
    "postgresql_maintained_dynamic_estimate",
    "provider_managed_normalized_state",
    "secret_redacted_shape_capability",
    "deferred_external_boundary",
)
FINALITY_POLICY_IDS = (
    "exact-semantic-v1",
    "stable-reference-edge-v1",
    "canonical-collection-v1",
    "postgresql-maintained-dynamic-v1",
    "provider-normalized-v1",
    "secret-redacted-v1",
    "deferred-external-boundary-v1",
)
FINALITY_NORMALIZER_NAMES = frozenset(
    {
        "normalize_exact_value",
        "normalize_reference_edge",
        "normalize_canonical_collection",
        "normalize_system_dynamic",
        "normalize_provider_state",
        "normalize_secret_shape",
        "normalize_deferred_boundary",
    }
)
FINALITY_REVIEW_COMMENT_IDS = frozenset(
    {
        3775760589,
        3775760599,
        3775760605,
        3776937010,
        3776937028,
        3780236879,
        3780236883,
        3780236885,
        3780956750,
        3780956759,
        3780956762,
        3780956766,
        3780956774,
        3780956777,
        3780956781,
        3781482901,
        3782398217,
        3782398228,
        3782398237,
        3782398258,
        3782398242,
        3782398246,
        3782398256,
        3783697275,
        3783697278,
        3783697281,
        3783697283,
        3785318729,
        3785318741,
        3785318751,
        3786071378,
        3786071396,
        3786071404,
        3789189421,
        3789189424,
        3789189427,
        3789793670,
        3789793674,
        3789793678,
        3789793681,
    }
)


FINALITY_ALLOWED_DYNAMIC_FIELD_IDS = frozenset(
    {
        ("pg_catalog.pg_class", "relpages"),
        ("pg_catalog.pg_class", "reltuples"),
        ("pg_catalog.pg_class", "relallvisible"),
        ("pg_catalog.pg_class", "relfrozenxid"),
        ("pg_catalog.pg_class", "relminmxid"),
        ("pg_catalog.pg_class", "relfilenode"),
    }
)


def _finality_canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _finality_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"shape": "float", "value": "nan"}
        if math.isinf(value):
            return {"shape": "float", "value": "infinity" if value > 0 else "-infinity"}
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "shape": "binary",
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if isinstance(value, dict):
        return {
            str(key): _finality_safe_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_finality_safe_value(item) for item in value]
    if isinstance(value, set):
        return sorted(
            (_finality_safe_value(item) for item in value),
            key=_finality_canonical_json,
        )
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return {"shape": type(value).__name__, "value": isoformat()}
    return {
        "shape": type(value).__name__,
        "repr_sha256": hashlib.sha256(type(value).__name__.encode("utf-8")).hexdigest(),
    }


def _finality_digest(value: Any) -> str:
    return hashlib.sha256(
        _finality_canonical_json(_finality_safe_value(value)).encode("utf-8")
    ).hexdigest()


def _finality_error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _finality_exact_keys(
    value: Any,
    expected: frozenset[str],
    label: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        _finality_error(errors, f"{label}_must_be_object")
        return False
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        _finality_error(errors, f"{label}_missing_keys:{','.join(missing)}")
    if extra:
        _finality_error(errors, f"{label}_unknown_keys:{','.join(extra)}")
    return not missing and not extra


def _finality_non_empty_string(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        _finality_error(errors, f"{label}_must_be_non_empty_string")


def _finality_catalogue_id(value: dict[str, Any]) -> str:
    if "catalogue" in value:
        return str(value["catalogue"])
    return f"{value.get('schema_name', '')}.{value.get('catalogue_name', '')}"


def _finality_field_id(value: dict[str, Any]) -> tuple[str, str]:
    return _finality_catalogue_id(value), str(value.get("field_name", value.get("field")))


def _finality_policy_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(policy["id"]): policy
        for policy in manifest.get("comparison_policies", [])
        if isinstance(policy, dict) and "id" in policy
    }


def _finality_registry_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    registry = manifest.get("field_registry", {})
    entries: list[dict[str, Any]] = []
    for key in ("special_fields", "relationship_fields", "provider_fields", "deferred_fields"):
        values = registry.get(key, []) if isinstance(registry, dict) else []
        if isinstance(values, list):
            entries.extend(item for item in values if isinstance(item, dict))
    return entries


def _finality_forbidden_policy_form(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, str):
        lowered = value.lower()
        if value == "*" or "ignore pg_" in lowered or "ignore volatile" in lowered or "wildcard" in lowered:
            findings.append(path or "<root>")
    elif isinstance(value, dict):
        for key, item in value.items():
            findings.extend(_finality_forbidden_policy_form(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_finality_forbidden_policy_form(item, f"{path}[{index}]"))
    return findings

def validate_finality_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _finality_exact_keys(manifest, FINALITY_TOP_LEVEL_KEYS, "finality_manifest", errors)
    if manifest.get("$schema") != "postgresql17-finality-coverage-schema-v1":
        _finality_error(errors, "finality_schema_identifier_invalid")
    if manifest.get("schema_version") != 1:
        _finality_error(errors, "finality_schema_version_invalid")
    if manifest.get("contract_type") != "postgresql17_finality_coverage":
        _finality_error(errors, "finality_contract_type_invalid")
    if manifest.get("repository") != "Swooshz-com/swooshz-quote-auto-generator":
        _finality_error(errors, "finality_repository_invalid")
    if manifest.get("design_lock") != "DL-160-POSTMERGE-026-A22R2":
        _finality_error(errors, "finality_design_lock_invalid")
    if manifest.get("postgresql_major") != 17:
        _finality_error(errors, "finality_postgresql_major_must_be_17")

    source = manifest.get("source")
    if _finality_exact_keys(
        source,
        frozenset(
            {
                "runtime_contract",
                "migration_provenance",
                "extractor_id",
                "normalizer_id",
                "policy_registry_id",
                "reference_topology",
            }
        ),
        "finality_source",
        errors,
    ):
        if source.get("runtime_contract") != "docs/runtime-privilege-contract.json":
            _finality_error(errors, "finality_source_runtime_contract_invalid")
        if source.get("migration_provenance") != "docs/runtime-privilege-contract.json#/production_migrations":
            _finality_error(errors, "finality_source_migration_provenance_invalid")

    queries = manifest.get("queries")
    if _finality_exact_keys(
        queries,
        frozenset({"catalogue_universe", "field_universe"}),
        "finality_queries",
        errors,
    ):
        required_query_parts = {
            "catalogue_universe": (
                "select",
                "pg_catalog.pg_class",
                "pg_catalog.pg_namespace",
                "relkind",
                "relispartition",
            ),
            "field_universe": (
                "select",
                "pg_catalog.pg_attribute",
                "pg_catalog.pg_class",
                "pg_catalog.pg_namespace",
                "pg_catalog.pg_type",
                "attnum",
                "attisdropped",
                "attstorage",
            ),
        }
        for key, required in required_query_parts.items():
            query = queries.get(key)
            _finality_non_empty_string(query, f"finality_query_{key}", errors)
            if not isinstance(query, str):
                continue
            lowered = query.lower()
            if not lowered.lstrip().startswith("select"):
                _finality_error(errors, f"finality_query_{key}_must_be_select")
            if ";" in query:
                _finality_error(errors, f"finality_query_{key}_must_be_single_statement")
            if any(
                token in lowered
                for token in (
                    "insert ",
                    "update ",
                    "delete ",
                    "drop ",
                    "alter ",
                    "create ",
                    "copy ",
                    "call ",
                )
            ):
                _finality_error(errors, f"finality_query_{key}_contains_mutation_keyword")
            for required_part in required:
                if required_part not in lowered:
                    _finality_error(
                        errors,
                        f"finality_query_{key}_missing_{required_part.replace('.', '_')}",
                    )

    classes = manifest.get("handling_classes")
    class_ids = [
        item.get("id")
        for item in classes
        if isinstance(item, dict)
    ] if isinstance(classes, list) else []
    if tuple(class_ids) != FINALITY_CLASS_IDS:
        _finality_error(errors, "finality_handling_classes_must_match_closed_registry")

    policies = manifest.get("comparison_policies")
    policy_ids = [
        item.get("id")
        for item in policies
        if isinstance(item, dict)
    ] if isinstance(policies, list) else []
    if tuple(policy_ids) != FINALITY_POLICY_IDS:
        _finality_error(errors, "finality_comparison_policies_must_match_closed_registry")
    policy_map = _finality_policy_map(manifest)
    for policy_id in FINALITY_POLICY_IDS:
        policy = policy_map.get(policy_id)
        if not policy:
            continue
        _finality_exact_keys(
            policy,
            frozenset(
                {
                    "id",
                    "handling_class",
                    "normalizer",
                    "allow_raw_variance",
                    "shape_fields",
                    "public_receipt",
                }
            ),
            f"finality_policy_{policy_id}",
            errors,
        )
        if policy.get("normalizer") not in FINALITY_NORMALIZER_NAMES:
            _finality_error(errors, f"finality_policy_{policy_id}_normalizer_not_executable")
        if policy.get("handling_class") not in FINALITY_CLASS_IDS:
            _finality_error(errors, f"finality_policy_{policy_id}_handling_class_unknown")
        if not isinstance(policy.get("allow_raw_variance"), bool):
            _finality_error(errors, f"finality_policy_{policy_id}_variance_flag_invalid")
        if not isinstance(policy.get("shape_fields"), list) or not policy["shape_fields"]:
            _finality_error(errors, f"finality_policy_{policy_id}_shape_contract_missing")

    registry = manifest.get("field_registry")
    if _finality_exact_keys(
        registry,
        frozenset(
            {
                "mode",
                "binding_keys",
                "default_policy_id",
                "default_handling_class",
                "special_fields",
                "relationship_fields",
                "provider_fields",
                "deferred_fields",
            }
        ),
        "finality_field_registry",
        errors,
    ):
        if registry.get("mode") != "reference_derived_exact_tuple":
            _finality_error(errors, "finality_field_registry_mode_invalid")
        if registry.get("default_policy_id") != "exact-semantic-v1":
            _finality_error(errors, "finality_default_policy_must_be_exact_semantic")
        if registry.get("default_handling_class") != "exact_semantic_value":
            _finality_error(errors, "finality_default_handling_class_invalid")
        selector_keys: set[tuple[str, str, str]] = set()
        for entry in _finality_registry_entries(manifest):
            selector = (
                str(entry.get("schema_name", "")),
                str(entry.get("catalogue_name", "")),
                str(entry.get("field_name", "")),
            )
            if selector in selector_keys:
                _finality_error(errors, f"finality_duplicate_field_policy:{'.'.join(selector)}")
            selector_keys.add(selector)
            policy_id = entry.get("policy_id")
            if policy_id not in policy_map:
                _finality_error(errors, f"finality_field_policy_unknown:{selector}:{policy_id}")
            binding_kind = entry.get("binding_kind")
            if binding_kind == "system_dynamic":
                field_id = (f"{selector[0]}.{selector[1]}", selector[2])
                if field_id not in FINALITY_ALLOWED_DYNAMIC_FIELD_IDS:
                    _finality_error(errors, f"finality_dynamic_field_not_authorized:{selector}")
                if policy_id != "postgresql-maintained-dynamic-v1":
                    _finality_error(errors, f"finality_dynamic_field_policy_invalid:{selector}")
                if not isinstance(entry.get("semantic_family"), str) or not entry["semantic_family"]:
                    _finality_error(errors, f"finality_dynamic_field_family_missing:{selector}")
                if not isinstance(entry.get("allowed_postgres_types"), list) or not entry["allowed_postgres_types"]:
                    _finality_error(errors, f"finality_dynamic_field_types_missing:{selector}")
            elif binding_kind == "reference_edge":
                if policy_id != "stable-reference-edge-v1":
                    _finality_error(errors, f"finality_edge_field_policy_invalid:{selector}")
                if not entry.get("edge_kind") or not entry.get("target_catalogue"):
                    _finality_error(errors, f"finality_edge_field_shape_missing:{selector}")
            elif binding_kind == "secret_shape":
                if policy_id != "secret-redacted-v1":
                    _finality_error(errors, f"finality_secret_field_policy_invalid:{selector}")
            elif binding_kind not in {"provider_shape", "deferred_boundary"}:
                _finality_error(errors, f"finality_field_binding_kind_unknown:{selector}:{binding_kind}")

    proof = manifest.get("reference_proof")
    if _finality_exact_keys(
        proof,
        frozenset(
            {
                "minimum_clean_references",
                "raw_variance_receipt",
                "normalized_digest",
                "maintenance_perturbation",
            }
        ),
        "finality_reference_proof",
        errors,
    ):
        if proof.get("minimum_clean_references") != 3:
            _finality_error(errors, "finality_requires_three_clean_references")
        receipt = proof.get("raw_variance_receipt")
        if _finality_exact_keys(
            receipt,
            frozenset({"encoding", "required_fields", "raw_values_emitted"}),
            "finality_raw_variance_receipt",
            errors,
        ) and receipt.get("raw_values_emitted") is not False:
            _finality_error(errors, "finality_raw_variance_must_not_be_emitted")
        perturbation = proof.get("maintenance_perturbation")
        if _finality_exact_keys(
            perturbation,
            frozenset(
                {
                    "required",
                    "disposable_only",
                    "accepted_semantic_change",
                    "operation_family",
                    "expected_result",
                }
            ),
            "finality_maintenance_perturbation",
            errors,
        ):
            if perturbation.get("required") is not True or perturbation.get("disposable_only") is not True:
                _finality_error(errors, "finality_maintenance_perturbation_must_be_disposable_and_required")
            if perturbation.get("accepted_semantic_change") is not False:
                _finality_error(errors, "finality_maintenance_perturbation_semantic_change_forbidden")

    mapping = manifest.get("historical_review_mapping")
    if not isinstance(mapping, list):
        _finality_error(errors, "finality_historical_review_mapping_must_be_list")
    else:
        comment_ids: list[int] = []
        current = 0
        outdated = 0
        for item in mapping:
            if not isinstance(item, dict):
                _finality_error(errors, "finality_historical_review_mapping_entry_not_object")
                continue
            _finality_exact_keys(
                item,
                frozenset(
                    {
                        "thread_id",
                        "comment_id",
                        "is_outdated",
                        "mechanism_class",
                        "causal_red_receipt",
                        "disposition",
                    }
                ),
                "finality_historical_review_mapping_entry",
                errors,
            )
            comment_id = item.get("comment_id")
            if not isinstance(comment_id, int):
                _finality_error(errors, "finality_historical_review_mapping_comment_id_invalid")
            else:
                comment_ids.append(comment_id)
            if item.get("is_outdated") is True:
                outdated += 1
            elif item.get("is_outdated") is False:
                current += 1
            else:
                _finality_error(errors, "finality_historical_review_mapping_state_invalid")
            _finality_non_empty_string(item.get("mechanism_class"), "finality_historical_review_mapping_mechanism", errors)
            _finality_non_empty_string(item.get("causal_red_receipt"), "finality_historical_review_mapping_receipt", errors)
            if item.get("disposition") != "material_generic_mapping":
                _finality_error(errors, "finality_historical_review_mapping_disposition_invalid")
        if len(mapping) != 40 or set(comment_ids) != FINALITY_REVIEW_COMMENT_IDS:
            _finality_error(errors, "finality_historical_review_mapping_must_cover_live_40_thread_corpus")
        if current != 26 or outdated != 14:
            _finality_error(errors, "finality_historical_review_mapping_current_outdated_totals_invalid")

    red_receipts = manifest.get("class_level_red_receipts")
    red_ids = [
        item.get("id")
        for item in red_receipts
        if isinstance(item, dict)
    ] if isinstance(red_receipts, list) else []
    if red_ids != ["H59", "H60", "H61", "H62"]:
        _finality_error(errors, "finality_historical_class_red_receipts_incomplete")
    if isinstance(red_receipts, list):
        for item in red_receipts:
            if not isinstance(item, dict) or item.get("expected_result") != "RED":
                _finality_error(errors, "finality_historical_class_red_receipt_not_red")

    for finding in _finality_forbidden_policy_form(manifest):
        _finality_error(errors, f"finality_forbidden_broad_policy_form:{finding}")
    return errors


def load_finality_coverage_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or FINALITY_MANIFEST_PATH
    raw = manifest_path.read_text(encoding="utf-8")
    value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError("finality_manifest_must_be_object")
    return value


def validate_finality_manifest_strictly(path: str | None = None) -> int:
    manifest_path = Path(path) if path else FINALITY_MANIFEST_PATH
    try:
        manifest = load_finality_coverage_manifest(manifest_path)
    except DuplicateKeyError as exc:
        print(f"FAIL: finality_manifest_duplicate_json_key:{exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL: finality_manifest_invalid:{exc}", file=sys.stderr)
        return 2
    errors = validate_finality_manifest(manifest)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print("PASS: PostgreSQL 17 finality coverage and A22R2 policy registry pass strict validation.")
    return 0

def derive_finality_catalogue_universe(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    derived: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("catalogue_universe_row_not_object")
        required = ("schema_name", "catalogue_name", "relkind", "relpersistence", "relispartition")
        if any(key not in row for key in required):
            raise ValueError(f"catalogue_universe_row_missing_field:{required}")
        item = {
            "schema_name": str(row["schema_name"]),
            "catalogue_name": str(row["catalogue_name"]),
            "catalogue": f"{row['schema_name']}.{row['catalogue_name']}",
            "object_kind": str(row["relkind"]),
            "relpersistence": str(row["relpersistence"]),
            "relispartition": bool(row["relispartition"]),
        }
        key = tuple(item[key] for key in ("catalogue", "object_kind", "relpersistence", "relispartition"))
        if key in seen:
            raise ValueError(f"duplicate_catalogue_universe_entry:{key}")
        seen.add(key)
        derived.append(item)
    return tuple(sorted(derived, key=_finality_canonical_json))


def derive_finality_field_universe(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    derived: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("field_universe_row_not_object")
        required = (
            "schema_name",
            "catalogue_name",
            "field_name",
            "ordinal_position",
            "postgres_type",
            "relkind",
            "relpersistence",
            "relispartition",
            "attnotnull",
            "attgenerated",
            "attidentity",
            "attstorage",
        )
        if any(key not in row for key in required):
            raise ValueError(f"field_universe_row_missing_field:{required}")
        item = {
            "schema_name": str(row["schema_name"]),
            "catalogue_name": str(row["catalogue_name"]),
            "catalogue": f"{row['schema_name']}.{row['catalogue_name']}",
            "field_name": str(row["field_name"]),
            "ordinal_position": int(row["ordinal_position"]),
            "postgres_type": str(row["postgres_type"]),
            "object_kind": str(row["relkind"]),
            "relpersistence": str(row["relpersistence"]),
            "relispartition": bool(row["relispartition"]),
            "attnotnull": bool(row["attnotnull"]),
            "attgenerated": str(row["attgenerated"] or ""),
            "attidentity": str(row["attidentity"] or ""),
            "attstorage": str(row["attstorage"]),
            "applicability": "catalogue_relation",
        }
        key = (item["catalogue"], item["field_name"])
        if key in seen:
            raise ValueError(f"duplicate_field_universe_entry:{key[0]}.{key[1]}")
        seen.add(key)
        derived.append(item)
    return tuple(
        sorted(
            derived,
            key=lambda item: (item["catalogue"], item["ordinal_position"], item["field_name"]),
        )
    )


def _finality_special_entry(
    manifest: dict[str, Any],
    field_id: tuple[str, str],
) -> dict[str, Any] | None:
    matches = [
        item
        for item in _finality_registry_entries(manifest)
        if (
            f"{item.get('schema_name')}.{item.get('catalogue_name')}",
            str(item.get("field_name")),
        ) == field_id
    ]
    if len(matches) > 1:
        raise ValueError(f"ambiguous_field_policy:{field_id[0]}.{field_id[1]}")
    return matches[0] if matches else None


def build_finality_field_bindings(
    field_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    manifest: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    active_manifest = manifest or load_finality_coverage_manifest()
    fields = (
        tuple(field_rows)
        if field_rows and "catalogue" in field_rows[0] and "field_name" in field_rows[0]
        else derive_finality_field_universe(list(field_rows))
    )
    policy_map = _finality_policy_map(active_manifest)
    class_roles = {
        item["id"]: item["output_role"]
        for item in active_manifest["handling_classes"]
    }
    bindings: list[dict[str, Any]] = []
    for field in fields:
        field_id = _finality_field_id(field)
        special = _finality_special_entry(active_manifest, field_id)
        policy_id = special["policy_id"] if special else active_manifest["field_registry"]["default_policy_id"]
        policy = policy_map.get(policy_id)
        if policy is None:
            raise ValueError(f"field_policy_not_found:{field_id}:{policy_id}")
        binding_kind = str(special["binding_kind"]) if special else "exact"
        semantic_family = special.get("semantic_family") if special else None
        edge_kind = special.get("edge_kind") if special else None
        target_catalogue = special.get("target_catalogue") if special else None
        if special:
            allowed_types = special.get("allowed_postgres_types", [])
            if allowed_types and field["postgres_type"] not in allowed_types:
                raise ValueError(
                    f"field_policy_type_mismatch:{field_id[0]}.{field_id[1]}:"
                    f"{field['postgres_type']}:{allowed_types}"
                )
        bindings.append(
            {
                "binding_id": f"reference-field:{field_id[0]}:{field_id[1]}",
                "catalogue": field_id[0],
                "field": field_id[1],
                "postgres_type": field["postgres_type"],
                "object_kind": field["object_kind"],
                "applicability": field["applicability"],
                "handling_class": policy["handling_class"],
                "policy_id": policy_id,
                "normalizer": policy["normalizer"],
                "binding_kind": binding_kind,
                "semantic_family": semantic_family,
                "edge_kind": edge_kind,
                "target_catalogue": target_catalogue,
                "output_role": class_roles[policy["handling_class"]],
            }
        )
    return tuple(bindings)


def validate_finality_field_binding_closure(
    derived_fields: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    bindings: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    consumed_fields: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> list[str]:
    errors: list[str] = []
    field_ids = {_finality_field_id(field) for field in derived_fields}
    binding_ids = [_finality_field_id(binding) for binding in bindings]
    if len(binding_ids) != len(set(binding_ids)):
        _finality_error(errors, "field_binding_duplicate")
    binding_set = set(binding_ids)
    if field_ids - binding_set:
        _finality_error(errors, f"field_binding_missing:{sorted(field_ids - binding_set)}")
    if binding_set - field_ids:
        _finality_error(errors, f"field_binding_extra:{sorted(binding_set - field_ids)}")
    for binding in bindings:
        if binding.get("policy_id") not in FINALITY_POLICY_IDS:
            _finality_error(errors, f"field_binding_policy_unknown:{binding.get('binding_id')}")
        if binding.get("normalizer") not in FINALITY_NORMALIZER_NAMES:
            _finality_error(errors, f"field_binding_normalizer_unknown:{binding.get('binding_id')}")
        if not binding.get("binding_id") or not binding.get("output_role"):
            _finality_error(errors, f"field_binding_identity_or_output_missing:{binding}")
    if consumed_fields is not None:
        consumed_ids = {_finality_field_id(field) for field in consumed_fields}
        if consumed_ids - field_ids:
            _finality_error(errors, f"executed_but_unregistered:{sorted(consumed_ids - field_ids)}")
        if field_ids - consumed_ids:
            _finality_error(errors, f"listed_but_unused:{sorted(field_ids - consumed_ids)}")
    return errors


def _finality_observation_shape(binding: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "field_present": bool(observation.get("field_present", True)),
        "postgres_type": str(observation.get("postgres_type", binding["postgres_type"])),
        "object_kind": str(observation.get("object_kind", binding["object_kind"])),
        "applicability": str(observation.get("applicability", binding["applicability"])),
        "policy_id": binding["policy_id"],
        "semantic_family": binding.get("semantic_family"),
        "edge_kind": binding.get("edge_kind"),
        "capability_shape": _finality_safe_value(observation.get("capability_shape")),
        "secret_shape": _finality_safe_value(observation.get("secret_shape")),
        "boundary_id": observation.get("boundary_id"),
    }


def normalize_exact_value(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    return _finality_safe_value(observation.get("raw_value"))


def normalize_reference_edge(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    raw_value = observation.get("raw_value")
    if raw_value in (None, 0, "0"):
        return {"edge_state": "none"}
    target = observation.get("target_identity")
    if not isinstance(target, str) or not target:
        raise ValueError("reference_edge_target_identity_missing")
    return {
        "edge_kind": binding.get("edge_kind"),
        "target_catalogue": binding.get("target_catalogue"),
        "target_identity": target,
    }


def normalize_canonical_collection(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    value = observation.get("raw_value")
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("collection_policy_requires_collection_value")
    values = [_finality_safe_value(item) for item in value]
    return sorted(values, key=_finality_canonical_json)


def normalize_system_dynamic(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    return {
        "state": "postgresql_maintained",
        "semantic_family": binding.get("semantic_family"),
        "field_present": bool(observation.get("field_present", True)),
        "postgres_type": binding["postgres_type"],
        "object_kind": binding["object_kind"],
        "applicability": binding["applicability"],
        "policy_id": binding["policy_id"],
    }


def normalize_provider_state(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    return {
        "state": "provider_managed",
        "capability_shape": _finality_safe_value(observation.get("capability_shape")),
        "field_present": bool(observation.get("field_present", True)),
        "postgres_type": binding["postgres_type"],
        "object_kind": binding["object_kind"],
        "applicability": binding["applicability"],
        "policy_id": binding["policy_id"],
    }


def normalize_secret_shape(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    raw_value = observation.get("raw_value")
    return {
        "state": "secret_redacted",
        "field_present": bool(observation.get("field_present", True)),
        "postgres_type": binding["postgres_type"],
        "object_kind": binding["object_kind"],
        "applicability": binding["applicability"],
        "policy_id": binding["policy_id"],
        "secret_shape": _finality_safe_value(observation.get("secret_shape"))
        or {
            "present": raw_value is not None,
            "value_type": type(raw_value).__name__ if raw_value is not None else "null",
        },
    }


def normalize_deferred_boundary(binding: dict[str, Any], observation: dict[str, Any]) -> Any:
    return {
        "state": "deferred_external_boundary",
        "boundary_id": observation.get("boundary_id"),
        "field_present": bool(observation.get("field_present", True)),
        "postgres_type": binding["postgres_type"],
        "object_kind": binding["object_kind"],
        "applicability": binding["applicability"],
        "policy_id": binding["policy_id"],
    }

def _finality_normalizer(name: str) -> Any:
    function = globals().get(name)
    if not callable(function) or name not in FINALITY_NORMALIZER_NAMES:
        raise ValueError(f"normalizer_not_executable:{name}")
    return function


def normalize_finality_reference(
    snapshot: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    reference_id: str = "reference",
) -> dict[str, Any]:
    active_manifest = manifest or load_finality_coverage_manifest()
    errors: list[str] = []
    try:
        catalogue_universe = derive_finality_catalogue_universe(snapshot.get("catalogue_rows", []))
        field_universe = derive_finality_field_universe(snapshot.get("field_rows", []))
        bindings = build_finality_field_bindings(field_universe, active_manifest)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "reference_id": reference_id,
            "errors": [f"reference_derivation_failed:{exc}"],
            "graph_digest": None,
            "raw_observations": (),
        }

    consumed_fields = snapshot.get("executed_fields")
    if consumed_fields is None:
        consumed_fields = [
            {"catalogue": field["catalogue"], "field_name": field["field_name"]}
            for field in field_universe
        ]
    errors.extend(validate_finality_field_binding_closure(field_universe, bindings, consumed_fields))

    binding_map = {_finality_field_id(binding): binding for binding in bindings}
    observations = snapshot.get("field_values", [])
    if not isinstance(observations, list):
        errors.append("field_values_must_be_list")
        observations = []
    normalized_nodes: list[dict[str, Any]] = []
    raw_observations: list[dict[str, Any]] = []
    seen_observations: set[tuple[str, str, str]] = set()
    observed_field_ids: set[tuple[str, str]] = set()
    for observation in observations:
        if not isinstance(observation, dict):
            errors.append("field_observation_not_object")
            continue
        field_id = _finality_field_id(observation)
        observed_field_ids.add(field_id)
        binding = binding_map.get(field_id)
        if binding is None:
            errors.append(f"executed_but_unregistered:{field_id}")
            continue
        object_identity = str(observation.get("object_identity", "<catalogue>"))
        observation_key = (object_identity, field_id[0], field_id[1])
        if observation_key in seen_observations:
            errors.append(f"duplicate_field_observation:{observation_key}")
            continue
        seen_observations.add(observation_key)
        shape = _finality_observation_shape(binding, observation)
        if shape["postgres_type"] != binding["postgres_type"]:
            errors.append(
                f"field_shape_type_drift:{field_id}:{shape['postgres_type']}:{binding['postgres_type']}"
            )
        if shape["object_kind"] != binding["object_kind"]:
            errors.append(f"field_shape_object_kind_drift:{field_id}")
        if shape["applicability"] != binding["applicability"]:
            errors.append(f"field_shape_applicability_drift:{field_id}")
        try:
            normalized_value = _finality_normalizer(binding["normalizer"])(binding, observation)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"field_normalization_failed:{field_id}:{exc}")
            normalized_value = {"normalization_error": str(exc)}
        normalized_nodes.append(
            {
                "object_identity": object_identity,
                "catalogue": field_id[0],
                "field": field_id[1],
                "shape": shape,
                "normalized_value": normalized_value,
            }
        )
        raw_observations.append(
            {
                "observation_key": observation_key,
                "field_id": field_id,
                "policy_id": binding["policy_id"],
                "shape": shape,
                "value_digest": _finality_digest(observation.get("raw_value")),
            }
        )
    field_ids = set(binding_map)
    if observed_field_ids - field_ids:
        errors.append(f"executed_but_unregistered:{sorted(observed_field_ids - field_ids)}")
    if field_ids - observed_field_ids:
        errors.append(f"listed_but_unused:{sorted(field_ids - observed_field_ids)}")

    allowed_edge_kinds = {
        str(item.get("edge_kind"))
        for item in active_manifest["field_registry"].get("relationship_fields", [])
    }
    edges: list[dict[str, Any]] = []
    for edge in snapshot.get("edges", []):
        if not isinstance(edge, dict):
            errors.append("relationship_edge_not_object")
            continue
        if edge.get("edge_kind") not in allowed_edge_kinds:
            errors.append(f"unknown_relationship_edge:{edge.get('edge_kind')}")
            continue
        edges.append(_finality_safe_value(edge))
    graph = {
        "catalogue_universe": list(catalogue_universe),
        "field_universe": list(field_universe),
        "bindings": [
            {
                "binding_id": binding["binding_id"],
                "catalogue": binding["catalogue"],
                "field": binding["field"],
                "policy_id": binding["policy_id"],
                "normalizer": binding["normalizer"],
                "handling_class": binding["handling_class"],
                "output_role": binding["output_role"],
                "binding_kind": binding["binding_kind"],
                "semantic_family": binding["semantic_family"],
                "edge_kind": binding["edge_kind"],
                "target_catalogue": binding["target_catalogue"],
            }
            for binding in bindings
        ],
        "normalized_nodes": sorted(normalized_nodes, key=_finality_canonical_json),
        "normalized_edges": sorted(edges, key=_finality_canonical_json),
        "capability_graph": _finality_safe_value(snapshot.get("capability_graph", {})),
        "secret_safe_state": _finality_safe_value(snapshot.get("secret_safe_state", {})),
        "deferred_boundaries": _finality_safe_value(snapshot.get("deferred_boundaries", [])),
    }
    return {
        "reference_id": reference_id,
        "catalogue_count": len(catalogue_universe),
        "field_count": len(field_universe),
        "catalogue_universe": catalogue_universe,
        "field_universe": field_universe,
        "bindings": bindings,
        "graph": graph,
        "graph_digest": _finality_digest(graph),
        "raw_observations": tuple(raw_observations),
        "errors": tuple(sorted(set(errors))),
    }


def compare_finality_references(
    snapshots: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_manifest = manifest or load_finality_coverage_manifest()
    errors: list[str] = []
    minimum = int(active_manifest["reference_proof"]["minimum_clean_references"])
    if len(snapshots) < minimum:
        errors.append(f"clean_reference_count_below_minimum:{len(snapshots)}:{minimum}")
    normalized = [
        normalize_finality_reference(
            snapshot,
            active_manifest,
            str(snapshot.get("reference_id", f"reference-{index + 1}")),
        )
        for index, snapshot in enumerate(snapshots)
    ]
    for result in normalized:
        errors.extend(result.get("errors", ()))
    if normalized:
        base = normalized[0]
        for result in normalized[1:]:
            if result.get("catalogue_universe") != base.get("catalogue_universe"):
                errors.append(f"catalogue_universe_drift:{base['reference_id']}:{result['reference_id']}")
            if result.get("field_universe") != base.get("field_universe"):
                errors.append(f"field_universe_drift:{base['reference_id']}:{result['reference_id']}")
            if result.get("bindings") != base.get("bindings"):
                errors.append(f"binding_registry_drift:{base['reference_id']}:{result['reference_id']}")

    raw_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for result in normalized:
        for observation in result.get("raw_observations", ()):
            raw_by_key.setdefault(tuple(observation["observation_key"]), []).append(
                {**observation, "reference_id": result["reference_id"]}
            )
    expected_reference_count = len(normalized)
    for observation_key, values in sorted(raw_by_key.items()):
        if len(values) != expected_reference_count:
            errors.append(
                f"field_observation_presence_drift:{observation_key}:"
                f"{len(values)}:{expected_reference_count}"
            )
    variance_inventory: list[dict[str, Any]] = []
    policy_map = _finality_policy_map(active_manifest)
    for observation_key, values in sorted(raw_by_key.items()):
        shapes = {_finality_canonical_json(value["shape"]) for value in values}
        if len(shapes) > 1:
            errors.append(f"field_shape_drift:{observation_key}")
        value_digests = sorted({value["value_digest"] for value in values})
        if len(value_digests) <= 1:
            continue
        policy = policy_map.get(values[0]["policy_id"], {})
        receipt = {
            "reference_ids": sorted(value["reference_id"] for value in values),
            "object_identity": observation_key[0],
            "catalogue": observation_key[1],
            "field": observation_key[2],
            "policy_id": values[0]["policy_id"],
            "value_digests": value_digests,
            "distinct_value_count": len(value_digests),
        }
        variance_inventory.append(receipt)
        if policy.get("allow_raw_variance") is not True:
            errors.append(f"raw_variance_unclassified:{observation_key}:{values[0]['policy_id']}")
    normalized_digests = [result.get("graph_digest") for result in normalized]
    if len(set(normalized_digests)) > 1:
        errors.append("normalized_graph_digest_drift")
    return {
        "ok": not errors,
        "errors": tuple(sorted(set(errors))),
        "references": tuple(result["reference_id"] for result in normalized),
        "catalogue_count_receipt": tuple(result.get("catalogue_count") for result in normalized),
        "field_count_receipt": tuple(result.get("field_count") for result in normalized),
        "variance_inventory": tuple(variance_inventory),
        "normalized_digests": tuple(normalized_digests),
        "policy_registry": tuple(
            (
                policy["id"],
                policy["normalizer"],
                bool(policy["allow_raw_variance"]),
            )
            for policy in active_manifest["comparison_policies"]
        ),
    }

def collect_finality_reference_metadata(
    connection: Any,
    manifest: dict[str, Any] | None = None,
    reference_id: str = "postgresql17-reference",
) -> dict[str, Any]:
    active_manifest = manifest or load_finality_coverage_manifest()
    version_cursor = connection.execute(
        "select current_setting('server_version_num')::int as server_version_num"
    )
    version_row = version_cursor.fetchone()
    version_number = int(
        version_row[0]
        if not isinstance(version_row, dict)
        else version_row["server_version_num"]
    )
    if version_number // 10000 != 17:
        raise ValueError(f"postgresql_major_mismatch:{version_number}")
    query_rows: dict[str, list[dict[str, Any]]] = {}
    for key, query in active_manifest["queries"].items():
        cursor = connection.execute(query)
        rows = cursor.fetchall()
        description = getattr(cursor, "description", None) or ()
        columns = []
        for column in description:
            column_name = getattr(column, "name", None)
            if column_name is None:
                column_name = column[0] if isinstance(column, (tuple, list)) else str(column)
            columns.append(str(column_name))
        query_rows[key] = [
            dict(row)
            if isinstance(row, dict)
            else dict(zip(columns, row))
            for row in rows
        ]
    return {
        "reference_id": reference_id,
        "postgresql_major": 17,
        "catalogue_rows": query_rows["catalogue_universe"],
        "field_rows": query_rows["field_universe"],
        "executed_fields": [],
        "field_values": [],
        "capability_graph": {},
        "secret_safe_state": {},
        "deferred_boundaries": [],
    }


def validate_historical_review_mapping(manifest: dict[str, Any] | None = None) -> list[str]:
    active_manifest = manifest or load_finality_coverage_manifest()
    return [
        error
        for error in validate_finality_manifest(active_manifest)
        if "historical_review_mapping" in error
    ]


def validate_historical_class_red_receipts(manifest: dict[str, Any] | None = None) -> list[str]:
    active_manifest = manifest or load_finality_coverage_manifest()
    return [
        error
        for error in validate_finality_manifest(active_manifest)
        if "historical_class_red" in error
    ]


def validate_all_contracts() -> int:
    runtime_result = validate_manifest_strictly(str(MANIFEST_PATH))
    finality_result = validate_finality_manifest_strictly()
    return 0 if runtime_result == 0 and finality_result == 0 else 2

if __name__ == "__main__":
    raise SystemExit(validate_all_contracts())
