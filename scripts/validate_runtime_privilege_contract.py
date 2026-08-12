#!/usr/bin/env python3
"""Validate the runtime privilege contract manifest against repository authority."""

from __future__ import annotations

import json
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

VIEWS_KEYS = frozenset({"count", "runtime_accessible", "legacy_optional", "materialized_view_rule", "direct_runtime_grants"})
ACCESSIBLE_VIEW_KEYS = frozenset({"schema", "class", "privileges", "production_source", "bound", "definition"})
VIEW_PRIVILEGE_KEYS = frozenset({"select"})
VIEW_AUTHORITY_ROW_KEYS = frozenset(
    {
        "relation_name",
        "relation_kind",
        "owner",
        "relation_acl",
        "acl_entries",
        "column_acl_entries",
        "view_definition",
        "view_dependencies",
        "view_columns",
        "relation_options",
        "view_security",
        "runtime_privileges",
        "runtime_select",
        "runtime_select_grantable",
    }
)
VIEW_ACL_ENTRY_KEYS = frozenset({"grantee", "grantor", "privilege_type", "is_grantable"})
VIEW_RUNTIME_PRIVILEGE_KEYS = frozenset({"privilege_type", "effective", "is_grantable"})
VIEW_COLUMN_ACL_ROW_KEYS = frozenset(
    {"relation_name", "relation_kind", "column_number", "column_name", "acl_entries", "runtime_privileges"}
)
VIEW_DEFINITION_KEYS = frozenset({"canonical_definition", "dependencies", "columns", "relation_options", "security"})
VIEW_DEPENDENCY_KEYS = frozenset({"schema", "relation_name", "relation_kind", "dependency_type"})
VIEW_COLUMN_KEYS = frozenset(
    {"ordinal", "name", "type_oid", "type_schema", "type_name", "type_modifier", "type_sql"}
)
VIEW_SECURITY_KEYS = frozenset({"security_barrier", "security_invoker", "check_option"})
VIEW_RELATION_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
VIEW_COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
BOUND_SOURCE_SCHEMA = "public"
BOUND_SOURCE_RELATION = "legacy_quote_artifacts_source"
BOUND_SOURCE_RELKIND = "r"
BOUND_SOURCE_VIEW_OWNER = "sqag_migrator"
RUNTIME_TABLE_PRIVILEGE_ROW_KEYS = frozenset(
    {
        "schema_name",
        "table_name",
        "relation_kind",
        "owner",
        "owner_select",
        "row_security_enabled",
        "row_security_forced",
        "privilege_type",
        "effective",
        "is_grantable",
    }
)
RUNTIME_COLUMN_PRIVILEGE_ROW_KEYS = frozenset(
    {"schema_name", "table_name", "column_name", "privilege_type", "effective", "is_grantable"}
)

MATERIALIZED_VIEW_RULE = (
    "No public materialized view is classified. Any public materialized view fails "
    "closed: none may be owned by, effectively readable by, or grantable to "
    "sqag_runtime, and the later live preflight must classify any before activation."
)

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
          and (
              c.relname like 'sqag_' || chr(37)
              or c.relname = 'legacy_quote_artifacts_source'
          )
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
               c.relkind as relation_kind,
               r.rolname as owner,
               has_table_privilege('sqag_migrator', c.oid, 'SELECT') as owner_select,
               c.relrowsecurity as row_security_enabled,
               c.relforcerowsecurity as row_security_forced,
               p.privilege_type,
               has_table_privilege('sqag_runtime', c.oid, p.privilege_type) as effective,
               has_table_privilege(
                   'sqag_runtime',
                   c.oid,
                   p.privilege_type || ' WITH GRANT OPTION'
               ) as is_grantable
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        join pg_catalog.pg_roles r on r.oid = c.relowner
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
          and (
              c.relname like 'sqag_' || chr(37)
              or c.relname = 'legacy_quote_artifacts_source'
          )
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
          and (
              c.relname like 'sqag_' || chr(37)
              or c.relname = 'legacy_quote_artifacts_source'
          )
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
        select c.relname as relation_name,
               c.relkind as relation_kind,
               r.rolname as owner,
               c.relacl::text as relation_acl,
               acl.acl_entries,
               runtime.runtime_privileges,
               runtime.runtime_select,
               runtime.runtime_select_grantable
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        join pg_catalog.pg_roles r on r.oid = c.relowner
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'grantee', case when expanded.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text) end,
                               'grantor', coalesce(grantor_role.rolname, 'OID:' || expanded.grantor::text),
                               'privilege_type', expanded.privilege_type,
                               'is_grantable', expanded.is_grantable
                           )
                           order by expanded.grantee, expanded.grantor, expanded.privilege_type, expanded.is_grantable
                       ) filter (where expanded.grantee is not null),
                       '[]'::jsonb
                   ) as acl_entries
            from pg_catalog.aclexplode(coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))) expanded
            left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee and expanded.grantee <> 0
            left join pg_catalog.pg_roles grantor_role on grantor_role.oid = expanded.grantor
        ) acl
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'privilege_type', p.privilege_type,
                               'effective', has_table_privilege('sqag_runtime', c.oid, p.privilege_type),
                               'is_grantable', has_table_privilege('sqag_runtime', c.oid, p.privilege_type || ' WITH GRANT OPTION')
                           )
                           order by p.privilege_type
                       ),
                       '[]'::jsonb
                   ) as runtime_privileges,
                   has_table_privilege('sqag_runtime', c.oid, 'SELECT') as runtime_select,
                   has_table_privilege('sqag_runtime', c.oid, 'SELECT WITH GRANT OPTION') as runtime_select_grantable
            from (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('REFERENCES'), ('TRIGGER'), ('MAINTAIN')) p(privilege_type)
        ) runtime
        where n.nspname = 'public'
          and c.relkind in ('v', 'm')
        order by c.relkind, c.relname
    """,
}

CANONICAL_VERIFICATION_QUERY_SQL['view_acl'] = '''
        select c.relname as relation_name,
               c.relkind as relation_kind,
               r.rolname as owner,
               c.relacl::text as relation_acl,
               acl.acl_entries,
               column_acl.column_acl_entries,
               pg_catalog.pg_get_viewdef(c.oid, true) as view_definition,
               dependency.view_dependencies,
               shape.view_columns,
               options.relation_options,
               jsonb_build_object(
                   'security_barrier', options.security_barrier,
                   'security_invoker', options.security_invoker,
                   'check_option', options.check_option
               ) as view_security,
               runtime.runtime_privileges,
               runtime.runtime_select,
               runtime.runtime_select_grantable
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        join pg_catalog.pg_roles r on r.oid = c.relowner
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'grantee', case when expanded.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text) end,
                               'grantor', coalesce(grantor_role.rolname, 'OID:' || expanded.grantor::text),
                               'privilege_type', expanded.privilege_type,
                               'is_grantable', expanded.is_grantable
                           )
                           order by expanded.grantee, expanded.grantor, expanded.privilege_type, expanded.is_grantable
                       ) filter (where expanded.grantee is not null),
                       '[]'::jsonb
                   ) as acl_entries
            from pg_catalog.aclexplode(coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))) expanded
            left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee and expanded.grantee <> 0
            left join pg_catalog.pg_roles grantor_role on grantor_role.oid = expanded.grantor
        ) acl
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'relation_name', c.relname,
                               'relation_kind', c.relkind,
                               'column_number', a.attnum,
                               'column_name', a.attname,
                               'acl_entries', coalesce((
                                   select jsonb_agg(
                                       jsonb_build_object(
                                           'grantee', case when expanded.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text) end,
                                           'grantor', coalesce(grantor_role.rolname, 'OID:' || expanded.grantor::text),
                                           'privilege_type', expanded.privilege_type,
                                           'is_grantable', expanded.is_grantable
                                       )
                                       order by expanded.grantee, expanded.grantor, expanded.privilege_type, expanded.is_grantable
                                   )
                                   from pg_catalog.aclexplode(a.attacl) expanded
                                   left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee and expanded.grantee <> 0
                                   left join pg_catalog.pg_roles grantor_role on grantor_role.oid = expanded.grantor
                               ), '[]'::jsonb),
                               'runtime_privileges', (
                                   select coalesce(
                                              jsonb_agg(
                                                  jsonb_build_object(
                                                      'privilege_type', p.privilege_type,
                                                      'effective', has_column_privilege('sqag_runtime', c.oid, a.attname, p.privilege_type),
                                                      'is_grantable', has_column_privilege('sqag_runtime', c.oid, a.attname, p.privilege_type || ' WITH GRANT OPTION')
                                                  )
                                                  order by p.privilege_type
                                              ),
                                              '[]'::jsonb
                                          )
                                   from (values ('SELECT'), ('INSERT'), ('UPDATE'), ('REFERENCES')) p(privilege_type)
                               )
                           )
                           order by a.attnum
                       ),
                       '[]'::jsonb
                   ) as column_acl_entries
            from pg_catalog.pg_attribute a
            where a.attrelid = c.oid
              and a.attnum > 0
              and not a.attisdropped
        ) column_acl
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'schema', dependency_rows.nspname,
                               'relation_name', dependency_rows.relname,
                               'relation_kind', dependency_rows.relkind,
                               'dependency_type', dependency_rows.deptype
                           )
                           order by dependency_rows.nspname, dependency_rows.relname, dependency_rows.relkind, dependency_rows.deptype
                       ),
                       '[]'::jsonb
                   ) as view_dependencies
            from (
                select distinct dep_n.nspname, dep_c.relname, dep_c.relkind, d.deptype
                from pg_catalog.pg_rewrite rw
                join pg_catalog.pg_depend d on d.classid = 'pg_rewrite'::regclass and d.objid = rw.oid
                join pg_catalog.pg_class dep_c on dep_c.oid = d.refobjid
                join pg_catalog.pg_namespace dep_n on dep_n.oid = dep_c.relnamespace
                where d.refclassid = 'pg_class'::regclass
                  and rw.ev_class = c.oid
                  and d.refobjid <> c.oid
            ) dependency_rows
        ) dependency
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'ordinal', a.attnum,
                               'name', a.attname,
                               'type_oid', a.atttypid::int,
                               'type_schema', type_n.nspname,
                               'type_name', type_t.typname,
                               'type_modifier', a.atttypmod,
                               'type_sql', pg_catalog.format_type(a.atttypid, a.atttypmod)
                           )
                           order by a.attnum
                       ),
                       '[]'::jsonb
                   ) as view_columns
            from pg_catalog.pg_attribute a
            join pg_catalog.pg_type type_t on type_t.oid = a.atttypid
            join pg_catalog.pg_namespace type_n on type_n.oid = type_t.typnamespace
            where a.attrelid = c.oid
              and a.attnum > 0
              and not a.attisdropped
        ) shape
        cross join lateral (
            select coalesce(jsonb_object_agg(option_name, option_value order by option_name), '{}'::jsonb) as relation_options,
                   coalesce(bool_or(option_name = 'security_barrier' and option_value = 'true'), false) as security_barrier,
                   coalesce(bool_or(option_name = 'security_invoker' and option_value = 'true'), false) as security_invoker,
                   max(option_value) filter (where option_name = 'check_option') as check_option
            from pg_catalog.pg_options_to_table(coalesce(c.reloptions, array[]::text[]))
        ) options
        cross join lateral (
            select coalesce(
                       jsonb_agg(
                           jsonb_build_object(
                               'privilege_type', p.privilege_type,
                               'effective', has_table_privilege('sqag_runtime', c.oid, p.privilege_type),
                               'is_grantable', has_table_privilege('sqag_runtime', c.oid, p.privilege_type || ' WITH GRANT OPTION')
                           )
                           order by p.privilege_type
                       ),
                       '[]'::jsonb
                   ) as runtime_privileges,
                   has_table_privilege('sqag_runtime', c.oid, 'SELECT') as runtime_select,
                   has_table_privilege('sqag_runtime', c.oid, 'SELECT WITH GRANT OPTION') as runtime_select_grantable
            from (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('REFERENCES'), ('TRIGGER'), ('MAINTAIN')) p(privilege_type)
        ) runtime
        where n.nspname = 'public'
          and c.relkind in ('v', 'm')
        order by c.relkind, c.relname
'''

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
LEGACY_VIEW_DIRECT_GRANT_COUNTS = {"legacy_absent": 37, "legacy_present": 38}
LEGACY_VIEW_DEFINITION = {
    "canonical_definition": "select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, content_blob, created_at, updated_at from legacy_quote_artifacts_source",
    "dependencies": [
        {
            "schema": "public",
            "relation_name": "legacy_quote_artifacts_source",
            "relation_kind": "r",
            "dependency_type": "n",
        }
    ],
    "columns": [
        {"ordinal": 1, "name": "workspace_id", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
        {"ordinal": 2, "name": "session_id", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
        {"ordinal": 3, "name": "artifact_kind", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
        {"ordinal": 4, "name": "filename", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
        {"ordinal": 5, "name": "content_type", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
        {"ordinal": 6, "name": "size_bytes", "type_oid": 20, "type_schema": "pg_catalog", "type_name": "int8", "type_modifier": -1, "type_sql": "bigint"},
        {"ordinal": 7, "name": "content_blob", "type_oid": 17, "type_schema": "pg_catalog", "type_name": "bytea", "type_modifier": -1, "type_sql": "bytea"},
        {"ordinal": 8, "name": "created_at", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
        {"ordinal": 9, "name": "updated_at", "type_oid": 25, "type_schema": "pg_catalog", "type_name": "text", "type_modifier": -1, "type_sql": "text"},
    ],
    "relation_options": {},
    "security": {"security_barrier": False, "security_invoker": False, "check_option": None},
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
    "table_acl": (
        "pg_catalog.pg_class",
        "pg_catalog.pg_namespace",
        "relacl",
        "relkind",
        "'public'",
        "'legacy_quote_artifacts_source'",
        "order by",
    ),
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
        "pg_catalog.pg_roles",
        "c.relname",
        "relation_kind",
        "owner",
        "is_grantable",
        "relrowsecurity",
        "relforcerowsecurity",
        "row_security_enabled",
        "row_security_forced",
        "' WITH GRANT OPTION'",
        "'select'",
        "'insert'",
        "'update'",
        "'delete'",
        "'truncate'",
        "'references'",
        "'trigger'",
        "'maintain'",
        "'legacy_quote_artifacts_source'",
        "'sqag_migrator'",
        "owner_select",
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
        "'legacy_quote_artifacts_source'",
    ),
    "effective_runtime_schema_privileges": ("has_schema_privilege", "'public'", "'usage'", "'create'"),
    "effective_runtime_routine_privileges": ("has_function_privilege", "pg_catalog.pg_proc", "'public'", "'execute'"),
    "view_acl": (
        "pg_catalog.pg_class",
        "pg_catalog.pg_namespace",
        "pg_catalog.pg_roles",
        "relname",
        "relkind",
        "relacl",
        "'public'",
        "'sqag_runtime'",
        "'v'",
        "'m'",
        "has_table_privilege",
        "runtime_select",
        "runtime_select_grantable",
        "order by",
    ),
}


REQUIRED_QUERY_FEATURES['view_acl'] = REQUIRED_QUERY_FEATURES['view_acl'] + (
    'pg_catalog.aclexplode',
    'pg_catalog.acldefault',
    'jsonb_agg',
    'jsonb_build_object',
    'cross join lateral',
    'acl_entries',
    'runtime_privileges',
    'grantee',
    'grantor',
    'privilege_type',
    'is_grantable',
    'values',
    'filter',
    chr(39) + 'SELECT' + chr(39),
    chr(39) + 'INSERT' + chr(39),
    chr(39) + 'UPDATE' + chr(39),
    chr(39) + 'DELETE' + chr(39),
    chr(39) + 'TRUNCATE' + chr(39),
    chr(39) + 'REFERENCES' + chr(39),
    chr(39) + 'TRIGGER' + chr(39),
    chr(39) + 'MAINTAIN' + chr(39),
    'pg_catalog.pg_attribute',
    'pg_catalog.pg_depend',
    'pg_catalog.pg_rewrite',
    'pg_catalog.pg_type',
    'pg_catalog.pg_options_to_table',
    'pg_catalog.pg_get_viewdef',
    'pg_catalog.format_type',
    'has_column_privilege',
    'jsonb_object_agg',
    'column_acl_entries',
    'view_definition',
    'view_dependencies',
    'view_columns',
    'relation_options',
    'view_security',
    'column_number',
    'column_name',
    'type_oid',
    'type_schema',
    'type_name',
    'type_modifier',
    'type_sql',
    'dependency_type',
    'security_barrier',
    'security_invoker',
    'check_option',
    'refclassid',
    'ev_class',
    'deptype',
)


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
    _exact_value(views.get("legacy_optional"), True, "views_legacy_optional", errors)
    _exact_value(
        views.get("materialized_view_rule"),
        MATERIALIZED_VIEW_RULE,
        "views_materialized_view_rule",
        errors,
    )
    direct_runtime_grants = views.get("direct_runtime_grants")
    if _exact_keys(direct_runtime_grants, frozenset(LEGACY_VIEW_DIRECT_GRANT_COUNTS), "views_direct_runtime_grants", errors):
        tables = manifest.get("tables")
        table_accessible = tables.get("runtime_accessible") if isinstance(tables, dict) else None
        table_grants = sum(
            1
            for entry in table_accessible.values()
            if isinstance(entry, dict)
            for privilege in entry.get("privileges", {}).values()
            if type(privilege) is bool and privilege
        ) if isinstance(table_accessible, dict) else 0
        column_privileges = manifest.get("column_privileges")
        column_grants = sum(
            len(privileges)
            for privileges in column_privileges.values()
            if isinstance(privileges, dict)
            for privilege_columns in privileges.values()
            if isinstance(privilege_columns, list)
        ) if isinstance(column_privileges, dict) else 0
        database_runtime = manifest.get("database_acl", {}).get("sqag_runtime", {})
        schema_runtime = manifest.get("schema_acl", {}).get("sqag_runtime", {})
        database_grants = sum(1 for value in database_runtime.values() if type(value) is bool and value) if isinstance(database_runtime, dict) else 0
        schema_grants = sum(1 for value in schema_runtime.values() if type(value) is bool and value) if isinstance(schema_runtime, dict) else 0
        absent_total = table_grants + column_grants + database_grants + schema_grants
        _exact_value(direct_runtime_grants.get("legacy_absent"), absent_total, "views_direct_runtime_grants_legacy_absent", errors)
        _exact_value(direct_runtime_grants.get("legacy_present"), absent_total + 1, "views_direct_runtime_grants_legacy_present", errors)
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
        definition = entry.get("definition")
        if not _exact_keys(definition, VIEW_DEFINITION_KEYS, f"{label}_definition", errors):
            if not isinstance(definition, dict):
                continue
        expected_definition = LEGACY_VIEW_DEFINITION if view_name == "sqag_quote_artifacts" else None
        _exact_value(definition, expected_definition, f"{label}_definition", errors)
        privileges = entry.get("privileges")
        if not _exact_keys(privileges, VIEW_PRIVILEGE_KEYS, f"{label}_privileges", errors):
            if not isinstance(privileges, dict):
                continue
        _exact_value(privileges.get("select"), True, f"{label}_select", errors)


def _normalise_view_definition(value: str) -> tuple[tuple[str, str], ...]:
    """Canonicalize only the locked source relation's optional public qualifier."""

    tokens = [token for token in lex_sql(value) if token.kind != "COMMENT"]
    if tokens and tokens[-1].value == ";":
        tokens.pop()

    normalized: list[tuple[str, str]] = []
    for token in tokens:
        if token.kind == "WORD":
            normalized.append(("WORD", token.value.lower()))
        elif token.kind == "QUOTED_IDENTIFIER" and token.value == token.value.lower():
            # pg_get_viewdef() may quote an already-lowercase identifier.  Do
            # not make arbitrary quoted identifiers case-insensitive.
            normalized.append(("WORD", token.value))
        else:
            normalized.append((token.kind, token.value))

    source_qualification = (
        ("WORD", BOUND_SOURCE_SCHEMA),
        ("SYMBOL", "."),
        ("WORD", BOUND_SOURCE_RELATION),
    )
    canonical: list[tuple[str, str]] = []
    index = 0
    while index < len(normalized):
        if tuple(normalized[index:index + len(source_qualification)]) == source_qualification:
            canonical.append(("WORD", BOUND_SOURCE_RELATION))
            index += len(source_qualification)
        else:
            canonical.append(normalized[index])
            index += 1
    return tuple(canonical)


def _validate_view_columns(value: Any, label: str, errors: list[str]) -> list[tuple[int, str]]:
    if type(value) is not list:
        _add_error(errors, f'{label}_must_be_list')
        return []
    identities: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    seen_ordinals: set[int] = set()
    seen_names: set[str] = set()
    for index, column in enumerate(value):
        column_label = f'{label}_{index}'
        if not isinstance(column, dict):
            _add_error(errors, f'{column_label}_must_be_object')
            continue
        if not _exact_keys(column, VIEW_COLUMN_KEYS, column_label, errors):
            continue
        _require_type(column.get('ordinal'), int, f'{column_label}_ordinal', errors)
        _require_non_empty_string(column.get('name'), f'{column_label}_name', errors)
        _require_type(column.get('type_oid'), int, f'{column_label}_type_oid', errors)
        _require_non_empty_string(column.get('type_schema'), f'{column_label}_type_schema', errors)
        _require_non_empty_string(column.get('type_name'), f'{column_label}_type_name', errors)
        _require_type(column.get('type_modifier'), int, f'{column_label}_type_modifier', errors)
        _require_non_empty_string(column.get('type_sql'), f'{column_label}_type_sql', errors)
        if (
            type(column.get('ordinal')) is not int
            or type(column.get('name')) is not str
            or type(column.get('type_oid')) is not int
            or type(column.get('type_schema')) is not str
            or type(column.get('type_name')) is not str
            or type(column.get('type_modifier')) is not int
            or type(column.get('type_sql')) is not str
        ):
            continue
        ordinal = int(column['ordinal'])
        name = str(column['name'])
        if ordinal <= 0:
            _add_error(errors, f'{column_label}_ordinal_must_be_positive')
        if column['type_oid'] <= 0:
            _add_error(errors, f'{column_label}_type_oid_must_be_positive')
        identity = (ordinal, name)
        if identity in seen:
            _add_error(errors, f'{column_label}_duplicate_identity')
        if ordinal in seen_ordinals:
            _add_error(errors, f'{column_label}_duplicate_ordinal')
        if name in seen_names:
            _add_error(errors, f'{column_label}_duplicate_name')
        seen.add(identity)
        seen_ordinals.add(ordinal)
        seen_names.add(name)
        identities.append(identity)
    if not identities:
        _add_error(errors, f'{label}_must_not_be_empty')
    if identities != sorted(identities):
        _add_error(errors, f'{label}_must_be_ordered_by_ordinal')
    return identities


def _validate_view_dependencies(value: Any, label: str, errors: list[str]) -> list[dict[str, Any]]:
    if type(value) is not list:
        _add_error(errors, f'{label}_must_be_list')
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, dependency in enumerate(value):
        dependency_label = f'{label}_{index}'
        if not isinstance(dependency, dict):
            _add_error(errors, f'{dependency_label}_must_be_object')
            continue
        if not _exact_keys(dependency, VIEW_DEPENDENCY_KEYS, dependency_label, errors):
            continue
        for key in ('schema', 'relation_name', 'relation_kind', 'dependency_type'):
            _require_non_empty_string(dependency.get(key), f'{dependency_label}_{key}', errors)
        if any(type(dependency.get(key)) is not str for key in VIEW_DEPENDENCY_KEYS):
            continue
        dependency_type = str(dependency['dependency_type'])
        if dependency_type not in {'n', 'a', 'i', 'p', 'e'}:
            _add_error(errors, f'{dependency_label}_invalid_dependency_type_{dependency_type}')
        identity = (
            str(dependency['schema']),
            str(dependency['relation_name']),
            str(dependency['relation_kind']),
            dependency_type,
        )
        if identity in seen:
            _add_error(errors, f'{dependency_label}_duplicate_identity')
        seen.add(identity)
        result.append(dependency)
    return result


def _validate_view_options_and_security(
    relation_options: Any,
    view_security: Any,
    label: str,
    errors: list[str],
) -> tuple[dict[str, str], dict[str, Any]]:
    options: dict[str, str] = {}
    if type(relation_options) is not dict:
        _add_error(errors, f'{label}_relation_options_must_be_object')
    else:
        for key, value in relation_options.items():
            if type(key) is not str or not key.strip():
                _add_error(errors, f'{label}_relation_options_invalid_key')
            if type(value) is not str:
                _add_error(errors, f'{label}_relation_options_invalid_value_{key}')
            else:
                options[str(key)] = value
    if not _exact_keys(view_security, VIEW_SECURITY_KEYS, f'{label}_security', errors):
        if not isinstance(view_security, dict):
            return options, {}
    _require_type(view_security.get('security_barrier'), bool, f'{label}_security_barrier', errors)
    _require_type(view_security.get('security_invoker'), bool, f'{label}_security_invoker', errors)
    check_option = view_security.get('check_option')
    if check_option is not None and type(check_option) is not str:
        _add_error(errors, f'{label}_check_option_must_be_string_or_null')
    if type(check_option) is str and check_option.lower() not in {'local', 'cascaded', 'none'}:
        _add_error(errors, f'{label}_invalid_check_option_{check_option}')
    security = {
        'security_barrier': view_security.get('security_barrier'),
        'security_invoker': view_security.get('security_invoker'),
        'check_option': check_option,
    }
    return options, security


def _validate_view_column_acl(
    row: dict[str, Any],
    label: str,
    *,
    name: str,
    kind: str,
    owner: str,
    runtime_role: str,
    classified: bool,
    runtime_select: bool,
    runtime_select_grantable: bool,
    column_identities: list[tuple[int, str]],
    errors: list[str],
) -> set[str]:
    column_acl_entries = row.get('column_acl_entries')
    if type(column_acl_entries) is not list:
        _add_error(errors, f'{label}_column_acl_entries_must_be_list')
        return set()
    seen_columns: set[tuple[int, str]] = set()
    effective_runtime_columns: set[str] = set()
    for column_index, column in enumerate(column_acl_entries):
        column_label = f'{label}_column_acl_row_{column_index}'
        if not isinstance(column, dict):
            _add_error(errors, f'{column_label}_must_be_object')
            continue
        if not _exact_keys(column, VIEW_COLUMN_ACL_ROW_KEYS, column_label, errors):
            continue
        _require_non_empty_string(column.get('relation_name'), f'{column_label}_relation_name', errors)
        _require_non_empty_string(column.get('relation_kind'), f'{column_label}_relation_kind', errors)
        _require_type(column.get('column_number'), int, f'{column_label}_column_number', errors)
        _require_non_empty_string(column.get('column_name'), f'{column_label}_column_name', errors)
        if (
            type(column.get('relation_name')) is not str
            or type(column.get('relation_kind')) is not str
            or type(column.get('column_number')) is not int
            or type(column.get('column_name')) is not str
        ):
            continue
        identity = (int(column['column_number']), str(column['column_name']))
        if str(column['relation_name']) != name or str(column['relation_kind']) != kind:
            _add_error(errors, f'{column_label}_relation_identity_mismatch')
        if identity in seen_columns:
            _add_error(errors, f'{column_label}_duplicate_identity')
        seen_columns.add(identity)
        if identity not in set(column_identities):
            _add_error(errors, f'{column_label}_unknown_column_identity_{identity!r}')

        acl_entries = column.get('acl_entries')
        if type(acl_entries) is not list:
            _add_error(errors, f'{column_label}_acl_entries_must_be_list')
        else:
            seen_acl: set[tuple[str, str, str, bool]] = set()
            for acl_index, entry in enumerate(acl_entries):
                entry_label = f'{column_label}_acl_entry_{acl_index}'
                if not isinstance(entry, dict):
                    _add_error(errors, f'{entry_label}_must_be_object')
                    continue
                if not _exact_keys(entry, VIEW_ACL_ENTRY_KEYS, entry_label, errors):
                    continue
                for key in ('grantee', 'grantor', 'privilege_type'):
                    _require_non_empty_string(entry.get(key), f'{entry_label}_{key}', errors)
                _require_type(entry.get('is_grantable'), bool, f'{entry_label}_is_grantable', errors)
                if any(type(entry.get(key)) is not str for key in ('grantee', 'grantor', 'privilege_type')) or type(entry.get('is_grantable')) is not bool:
                    continue
                grantee = str(entry['grantee'])
                grantor = str(entry['grantor'])
                privilege = str(entry['privilege_type'])
                grantable = bool(entry['is_grantable'])
                if privilege not in VIEW_COLUMN_PRIVILEGES:
                    _add_error(errors, f'{entry_label}_invalid_privilege_type_{privilege}')
                    continue
                acl_identity = (grantee, grantor, privilege, grantable)
                if acl_identity in seen_acl:
                    _add_error(errors, f'{entry_label}_duplicate_acl_entry')
                seen_acl.add(acl_identity)
                if grantee == 'PUBLIC':
                    if grantable:
                        _add_error(errors, f'{entry_label}_column_acl_grant_option_forbidden')
                    _add_error(errors, f'{entry_label}_column_acl_public_authority_forbidden')
                elif grantee == runtime_role:
                    if grantable:
                        _add_error(errors, f'{entry_label}_column_acl_grant_option_forbidden')
                    _add_error(errors, f'{entry_label}_column_acl_runtime_authority_forbidden')
                    if grantable:
                        _add_error(errors, f'{entry_label}_column_acl_runtime_grant_option_forbidden')
                    if privilege != 'SELECT':
                        _add_error(errors, f'{entry_label}_column_acl_runtime_privilege_forbidden_{privilege}')
                    if grantor != owner:
                        _add_error(errors, f'{entry_label}_column_acl_runtime_grantor_invalid')
                elif classified:
                    if grantable:
                        _add_error(errors, f'{entry_label}_column_acl_grant_option_forbidden')
                    _add_error(errors, f'{entry_label}_column_acl_unexpected_participant_{grantee}_{grantor}')

        runtime_privileges = column.get('runtime_privileges')
        effective: dict[str, tuple[bool, bool]] = {}
        if type(runtime_privileges) is not list:
            _add_error(errors, f'{column_label}_runtime_privileges_must_be_list')
        else:
            seen_privileges: set[str] = set()
            for privilege_index, entry in enumerate(runtime_privileges):
                privilege_label = f'{label}_column_runtime_privilege_{privilege_index}'
                if not isinstance(entry, dict):
                    _add_error(errors, f'{privilege_label}_must_be_object')
                    continue
                if not _exact_keys(entry, VIEW_RUNTIME_PRIVILEGE_KEYS, privilege_label, errors):
                    continue
                _require_non_empty_string(entry.get('privilege_type'), f'{privilege_label}_privilege_type', errors)
                _require_type(entry.get('effective'), bool, f'{privilege_label}_effective', errors)
                _require_type(entry.get('is_grantable'), bool, f'{privilege_label}_is_grantable', errors)
                if type(entry.get('privilege_type')) is not str or type(entry.get('effective')) is not bool or type(entry.get('is_grantable')) is not bool:
                    continue
                privilege = str(entry['privilege_type'])
                if privilege not in VIEW_COLUMN_PRIVILEGES:
                    _add_error(errors, f'{privilege_label}_invalid_privilege_type_{privilege}')
                    continue
                if privilege in seen_privileges:
                    _add_error(errors, f'{label}_column_runtime_privilege_{privilege_index}_duplicate')
                seen_privileges.add(privilege)
                effective[privilege] = (bool(entry['effective']), bool(entry['is_grantable']))
            missing = set(VIEW_COLUMN_PRIVILEGES) - set(effective)
            if missing:
                _add_error(errors, f'{column_label}_runtime_privileges_missing_{sorted(missing)}')
        for privilege, (is_effective, is_grantable) in effective.items():
            if is_grantable:
                _add_error(errors, f'{column_label}_runtime_privilege_grant_option_forbidden')
            if is_effective:
                effective_runtime_columns.add(privilege)
                if kind == 'm':
                    _add_error(errors, f'{label}_materialized_view_column_authority_forbidden')
                elif not classified:
                    _add_error(errors, f'{label}_column_acl_runtime_authority_forbidden')
                elif privilege != 'SELECT' or not runtime_select:
                    _add_error(errors, f'{label}_classified_view_column_privilege_invalid_{privilege}')
        if classified and runtime_select_grantable:
            _add_error(errors, f'{label}_classified_view_column_relation_grant_option_forbidden')
    missing_columns = set(column_identities) - seen_columns
    if missing_columns:
        _add_error(errors, f'{label}_column_acl_entries_missing_{sorted(missing_columns)}')
    if set(column_identities) != seen_columns:
        _add_error(errors, f'{label}_column_acl_entries_identity_mismatch')
    return effective_runtime_columns


def evaluate_bound_source_authority(
    manifest: dict[str, Any],
    view_rows: list[dict[str, Any]],
    table_privilege_rows: list[dict[str, Any]],
    column_privilege_rows: list[dict[str, Any]],
    *,
    runtime_role: str = "sqag_runtime",
) -> tuple[str, ...]:
    """Require complete no-runtime-authority evidence for the locked source table."""

    errors: list[str] = []
    views = manifest.get("views") if isinstance(manifest, dict) else None
    accessible = views.get("runtime_accessible") if isinstance(views, dict) else None
    if not isinstance(accessible, dict):
        return ("runtime_accessible_views_must_be_object",)
    classified_names = set(accessible)
    classified_present = any(
        isinstance(row, dict) and row.get("relation_name") in classified_names
        for row in view_rows
    )

    if type(table_privilege_rows) is not list:
        errors.append("bound_source_table_evidence_must_be_list")
        table_privilege_rows = []
    source_table_rows = [
        (index, row)
        for index, row in enumerate(table_privilege_rows)
        if isinstance(row, dict)
        and row.get("schema_name") == BOUND_SOURCE_SCHEMA
        and row.get("table_name") == BOUND_SOURCE_RELATION
    ]

    if type(column_privilege_rows) is not list:
        errors.append("bound_source_column_evidence_must_be_list")
        column_privilege_rows = []
    source_column_rows = [
        (index, row)
        for index, row in enumerate(column_privilege_rows)
        if isinstance(row, dict)
        and row.get("schema_name") == BOUND_SOURCE_SCHEMA
        and row.get("table_name") == BOUND_SOURCE_RELATION
    ]

    source_evidence_present = bool(source_table_rows or source_column_rows)
    if not classified_present and not source_evidence_present:
        return tuple(errors)

    expected_table_privileges = set(VIEW_RELATION_PRIVILEGES)
    if not source_table_rows:
        if classified_present or source_column_rows:
            errors.append("bound_source_table_evidence_missing")
    if len(source_table_rows) != len(expected_table_privileges):
        if classified_present or source_table_rows:
            errors.append(
                "bound_source_table_evidence_cardinality_expected_"
                f"{len(expected_table_privileges)}_got_{len(source_table_rows)}"
            )

    seen_table_privileges: set[str] = set()
    for index, row in source_table_rows:
        label = f"bound_source_table_row_{index}"
        if not _exact_keys(row, RUNTIME_TABLE_PRIVILEGE_ROW_KEYS, label, errors):
            continue
        for key in ("schema_name", "table_name", "relation_kind", "owner", "privilege_type"):
            _require_non_empty_string(row.get(key), f"{label}_{key}", errors)
        _require_type(row.get("owner_select"), bool, f"{label}_owner_select", errors)
        _require_type(row.get("row_security_enabled"), bool, f"{label}_row_security_enabled", errors)
        _require_type(row.get("row_security_forced"), bool, f"{label}_row_security_forced", errors)
        _require_type(row.get("effective"), bool, f"{label}_effective", errors)
        _require_type(row.get("is_grantable"), bool, f"{label}_is_grantable", errors)
        if any(
            type(row.get(key)) is not str
            for key in ("schema_name", "table_name", "relation_kind", "owner", "privilege_type")
        ):
            continue
        if (
            type(row.get("owner_select")) is not bool
            or type(row.get("row_security_enabled")) is not bool
            or type(row.get("row_security_forced")) is not bool
            or type(row.get("effective")) is not bool
            or type(row.get("is_grantable")) is not bool
        ):
            continue
        privilege = str(row["privilege_type"])
        if privilege not in expected_table_privileges:
            _add_error(errors, f"{label}_invalid_privilege_type_{privilege}")
            continue
        if privilege in seen_table_privileges:
            _add_error(errors, f"{label}_duplicate_privilege")
        seen_table_privileges.add(privilege)
        if row["schema_name"] != BOUND_SOURCE_SCHEMA:
            _add_error(errors, f"{label}_schema_invalid")
        if row["table_name"] != BOUND_SOURCE_RELATION:
            _add_error(errors, f"{label}_relation_invalid")
        if row["relation_kind"] != BOUND_SOURCE_RELKIND:
            _add_error(errors, f"{label}_relation_kind_invalid")
        if row["owner"] == runtime_role:
            _add_error(errors, f"{label}_runtime_owner_forbidden")
        if classified_present and not row["owner_select"]:
            _add_error(
                errors,
                f"{label}_classified_view_owner_source_select_required_{BOUND_SOURCE_VIEW_OWNER}",
            )
        if classified_present and row["row_security_enabled"]:
            _add_error(errors, f"{label}_classified_view_source_row_security_enabled_forbidden")
        if classified_present and row["row_security_forced"]:
            _add_error(errors, f"{label}_classified_view_source_row_security_forced_forbidden")
        if row["effective"]:
            _add_error(errors, f"{label}_runtime_privilege_forbidden_{privilege}")
        if row["is_grantable"]:
            _add_error(errors, f"{label}_runtime_grant_option_forbidden_{privilege}")

    if classified_present or source_table_rows:
        if seen_table_privileges != expected_table_privileges:
            _add_error(
                errors,
                "bound_source_table_privileges_mismatch_expected_"
                f"{sorted(expected_table_privileges)}_got_{sorted(seen_table_privileges)}",
            )

    if not source_column_rows and (classified_present or source_table_rows):
        errors.append("bound_source_column_evidence_missing")

    expected_column_privileges = set(VIEW_COLUMN_PRIVILEGES)
    expected_columns = {
        str(column["name"])
        for column in LEGACY_VIEW_DEFINITION["columns"]
    }
    seen_columns: dict[str, set[str]] = {}
    for index, row in source_column_rows:
        label = f"bound_source_column_row_{index}"
        if not _exact_keys(row, RUNTIME_COLUMN_PRIVILEGE_ROW_KEYS, label, errors):
            continue
        for key in ("schema_name", "table_name", "column_name", "privilege_type"):
            _require_non_empty_string(row.get(key), f"{label}_{key}", errors)
        _require_type(row.get("effective"), bool, f"{label}_effective", errors)
        _require_type(row.get("is_grantable"), bool, f"{label}_is_grantable", errors)
        if any(
            type(row.get(key)) is not str
            for key in ("schema_name", "table_name", "column_name", "privilege_type")
        ):
            continue
        if type(row.get("effective")) is not bool or type(row.get("is_grantable")) is not bool:
            continue
        privilege = str(row["privilege_type"])
        column_name = str(row["column_name"])
        if privilege not in expected_column_privileges:
            _add_error(errors, f"{label}_invalid_privilege_type_{privilege}")
            continue
        privileges = seen_columns.setdefault(column_name, set())
        if privilege in privileges:
            _add_error(errors, f"{label}_duplicate_privilege")
        privileges.add(privilege)
        if row["effective"]:
            _add_error(errors, f"{label}_runtime_privilege_forbidden_{privilege}")
        if row["is_grantable"]:
            _add_error(errors, f"{label}_runtime_grant_option_forbidden_{privilege}")

    if classified_present or source_column_rows:
        for column_name in expected_columns:
            actual_privileges = seen_columns.get(column_name, set())
            if actual_privileges != expected_column_privileges:
                _add_error(
                    errors,
                    "bound_source_column_privileges_mismatch_"
                    f"{column_name}_expected_{sorted(expected_column_privileges)}_got_{sorted(actual_privileges)}",
                )
    return tuple(errors)


def evaluate_view_authority(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    runtime_role: str = "sqag_runtime",
) -> tuple[str, ...]:
    """Evaluate complete PostgreSQL-owned relation ACL and effective privilege evidence."""

    errors: list[str] = []
    if type(rows) is not list:
        return ("relation_view_rows_must_be_list",)

    views = manifest.get("views") if isinstance(manifest, dict) else None
    accessible = views.get("runtime_accessible") if isinstance(views, dict) else None
    if not isinstance(accessible, dict):
        return ("runtime_accessible_views_must_be_object",)
    classified = set(accessible)
    boundary = manifest.get("boundary_b") if isinstance(manifest, dict) else None
    expected_owner = boundary.get("object_owner") if isinstance(boundary, dict) else None
    expected_privileges = set(VIEW_RELATION_PRIVILEGES)

    legacy_rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, row in enumerate(rows):
        label = f"relation_view_row_{index}"
        if not isinstance(row, dict):
            _add_error(errors, f"{label}_must_be_object")
            continue
        if not _exact_keys(row, VIEW_AUTHORITY_ROW_KEYS, label, errors):
            continue

        for key in ("relation_name", "relation_kind", "owner"):
            _require_non_empty_string(row.get(key), f"{label}_{key}", errors)
        for key in ("runtime_select", "runtime_select_grantable"):
            _require_type(row.get(key), bool, f"{label}_{key}", errors)
        relation_acl = row.get("relation_acl")
        if relation_acl is not None and type(relation_acl) is not str:
            _add_error(errors, f"{label}_relation_acl_must_be_string_or_null")

        basic_types_valid = (
            all(type(row.get(key)) is str for key in ("relation_name", "relation_kind", "owner"))
            and all(type(row.get(key)) is bool for key in ("runtime_select", "runtime_select_grantable"))
        )
        if not basic_types_valid:
            continue

        name = str(row["relation_name"])
        kind = str(row["relation_kind"])
        owner = str(row["owner"])
        runtime_select = bool(row["runtime_select"])
        grantable = bool(row["runtime_select_grantable"])

        if name in seen_names:
            _add_error(errors, f"{label}_duplicate_relation_view_row_same_relation_name")
        seen_names.add(name)

        acl_entries = row.get("acl_entries")
        valid_acl_entries: list[dict[str, Any]] = []
        if type(acl_entries) is not list:
            _add_error(errors, f"{label}_acl_entries_must_be_list")
        else:
            seen_acl_entries: set[tuple[str, str, str, bool]] = set()
            for acl_index, entry in enumerate(acl_entries):
                entry_label = f"{label}_acl_entry_{acl_index}"
                if not isinstance(entry, dict):
                    _add_error(errors, f"{entry_label}_must_be_object")
                    continue
                if not _exact_keys(entry, VIEW_ACL_ENTRY_KEYS, entry_label, errors):
                    continue
                for key in ("grantee", "grantor", "privilege_type"):
                    _require_non_empty_string(entry.get(key), f"{entry_label}_{key}", errors)
                _require_type(entry.get("is_grantable"), bool, f"{entry_label}_is_grantable", errors)
                if (
                    any(type(entry.get(key)) is not str for key in ("grantee", "grantor", "privilege_type"))
                    or type(entry.get("is_grantable")) is not bool
                ):
                    continue
                grantee = str(entry["grantee"])
                grantor = str(entry["grantor"])
                privilege = str(entry["privilege_type"])
                is_grantable = bool(entry["is_grantable"])
                if privilege not in expected_privileges:
                    _add_error(errors, f"{entry_label}_invalid_privilege_type_{privilege}")
                    continue
                entry_key = (grantee, grantor, privilege, is_grantable)
                if entry_key in seen_acl_entries:
                    _add_error(errors, f"{entry_label}_duplicate_acl_entry")
                seen_acl_entries.add(entry_key)
                valid_acl_entries.append(entry)
                if grantee == "PUBLIC":
                    _add_error(errors, f"{entry_label}_public_acl_authority_forbidden")
                elif grantee == runtime_role:
                    if privilege != "SELECT":
                        _add_error(errors, f"{entry_label}_runtime_acl_privilege_forbidden_{privilege}")
                    if is_grantable:
                        _add_error(errors, f"{entry_label}_runtime_acl_grant_option_forbidden")
                    if grantor != owner:
                        _add_error(errors, f"{entry_label}_runtime_acl_grantor_invalid")
                elif grantee == owner:
                    if grantor != owner:
                        _add_error(errors, f"{entry_label}_owner_acl_grantor_invalid")
                    if is_grantable:
                        _add_error(errors, f"{entry_label}_owner_acl_grant_option_forbidden")
                elif name in classified:
                    _add_error(errors, f"{entry_label}_unexpected_acl_grantee_{grantee}")

        if name in classified:
            owner_acl_semantics = {
                (entry['grantee'], entry['grantor'], entry['privilege_type'], entry['is_grantable'])
                for entry in valid_acl_entries
                if entry.get('grantee') == owner
            }
            expected_owner_acl = {(owner, owner, privilege, False) for privilege in expected_privileges}
            if owner_acl_semantics != expected_owner_acl:
                _add_error(
                    errors,
                    f'{label}_owner_acl_completeness_expected_{sorted(expected_owner_acl)}_got_{sorted(owner_acl_semantics)}',
                )

        runtime_privileges = row.get("runtime_privileges")
        effective_privileges: dict[str, tuple[bool, bool]] = {}
        runtime_privileges_valid = True
        if type(runtime_privileges) is not list:
            _add_error(errors, f"{label}_runtime_privileges_must_be_list")
            runtime_privileges_valid = False
        else:
            seen_runtime_privileges: set[str] = set()
            for privilege_index, entry in enumerate(runtime_privileges):
                entry_label = f"{label}_runtime_privilege_{privilege_index}"
                if not isinstance(entry, dict):
                    _add_error(errors, f"{entry_label}_must_be_object")
                    runtime_privileges_valid = False
                    continue
                if not _exact_keys(entry, VIEW_RUNTIME_PRIVILEGE_KEYS, entry_label, errors):
                    runtime_privileges_valid = False
                    continue
                _require_non_empty_string(entry.get("privilege_type"), f"{entry_label}_privilege_type", errors)
                _require_type(entry.get("effective"), bool, f"{entry_label}_effective", errors)
                _require_type(entry.get("is_grantable"), bool, f"{entry_label}_is_grantable", errors)
                if (
                    type(entry.get("privilege_type")) is not str
                    or type(entry.get("effective")) is not bool
                    or type(entry.get("is_grantable")) is not bool
                ):
                    runtime_privileges_valid = False
                    continue
                privilege = str(entry["privilege_type"])
                if privilege not in expected_privileges:
                    _add_error(errors, f"{entry_label}_invalid_privilege_type_{privilege}")
                    runtime_privileges_valid = False
                    continue
                if privilege in seen_runtime_privileges:
                    _add_error(errors, f"{entry_label}_duplicate_runtime_privilege")
                    runtime_privileges_valid = False
                seen_runtime_privileges.add(privilege)
                effective_privileges[privilege] = (bool(entry["effective"]), bool(entry["is_grantable"]))
            missing_privileges = expected_privileges - set(effective_privileges)
            if missing_privileges:
                _add_error(errors, f"{label}_runtime_privileges_missing_{sorted(missing_privileges)}")
                runtime_privileges_valid = False

        if runtime_privileges_valid:
            select_effective, select_grantable = effective_privileges["SELECT"]
            if select_effective != runtime_select:
                _add_error(errors, f"{label}_runtime_select_evidence_mismatch")
            if select_grantable != grantable:
                _add_error(errors, f"{label}_runtime_select_grantable_evidence_mismatch")
            if any(is_grantable for _, is_grantable in effective_privileges.values()):
                _add_error(errors, f"{label}_runtime_privilege_grant_option_forbidden")

        view_columns = _validate_view_columns(row.get('view_columns'), f'{label}_view_columns', errors)
        view_dependencies = _validate_view_dependencies(row.get('view_dependencies'), f'{label}_view_dependencies', errors)
        relation_options, view_security = _validate_view_options_and_security(
            row.get('relation_options'),
            row.get('view_security'),
            label,
            errors,
        )
        view_definition = row.get('view_definition')
        if type(view_definition) is not str or not view_definition.strip():
            _add_error(errors, f'{label}_view_definition_must_be_non_empty_string')
        if name in classified:
            expected_definition = LEGACY_VIEW_DEFINITION
            if type(view_definition) is str:
                try:
                    definition_matches = _normalise_view_definition(view_definition) == _normalise_view_definition(expected_definition['canonical_definition'])
                except SQLLexError:
                    definition_matches = False
                if not definition_matches:
                    _add_error(errors, f'{label}_classified_view_definition_mismatch')
            if view_dependencies != expected_definition['dependencies']:
                _add_error(errors, f'{label}_classified_view_dependencies_mismatch')
            if row.get('view_columns') != expected_definition['columns']:
                _add_error(errors, f'{label}_classified_view_columns_mismatch')
            if relation_options != expected_definition['relation_options']:
                _add_error(errors, f'{label}_classified_view_options_mismatch')
            if view_security != expected_definition['security']:
                _add_error(errors, f'{label}_classified_view_security_mismatch')
        _validate_view_column_acl(
            row,
            label,
            name=name,
            kind=kind,
            owner=owner,
            runtime_role=runtime_role,
            classified=name in classified,
            runtime_select=runtime_select,
            runtime_select_grantable=grantable,
            column_identities=view_columns,
            errors=errors,
        )

        if owner == runtime_role:
            _add_error(errors, f"{label}_runtime_relation_ownership_forbidden")
        if kind not in ("v", "m"):
            _add_error(errors, f"{label}_unknown_relation_kind_{kind}")
            continue

        runtime_acl_entries = [entry for entry in valid_acl_entries if entry["grantee"] == runtime_role]
        effective_runtime = {
            privilege for privilege, (effective, _) in effective_privileges.items() if effective
        }
        if kind == "m":
            _add_error(errors, f"{label}_materialized_view_unclassified")
            if runtime_select or "SELECT" in effective_runtime:
                _add_error(errors, f"{label}_materialized_view_runtime_select_forbidden")
            if grantable or any(is_grantable for _, is_grantable in effective_privileges.values()):
                _add_error(errors, f"{label}_materialized_view_runtime_grant_option_forbidden")
            if effective_runtime - {"SELECT"}:
                _add_error(errors, f"{label}_materialized_view_runtime_privilege_forbidden_{sorted(effective_runtime - {'SELECT'})}")
            continue

        if name in classified:
            legacy_rows.append(row)
            if expected_owner is not None and owner != expected_owner:
                _add_error(errors, f"{label}_classified_view_owner_invalid_expected_{expected_owner}_got_{owner}")
            if not runtime_select:
                _add_error(errors, f"{label}_classified_view_missing_bounded_select")
            if grantable:
                _add_error(errors, f"{label}_classified_view_grant_option_forbidden")
            if kind != "v":
                _add_error(errors, f"{label}_classified_view_must_be_ordinary_view")
            if len(runtime_acl_entries) != 1:
                _add_error(errors, f"{label}_classified_view_requires_one_direct_runtime_select")
            else:
                direct_entry = runtime_acl_entries[0]
                if direct_entry["privilege_type"] != "SELECT":
                    _add_error(errors, f"{label}_classified_view_direct_runtime_privilege_invalid")
                if direct_entry["is_grantable"]:
                    _add_error(errors, f"{label}_classified_view_direct_runtime_grant_option_forbidden")
                if expected_owner is not None and direct_entry["grantor"] != expected_owner:
                    _add_error(errors, f"{label}_classified_view_direct_runtime_grantor_invalid")
            if runtime_privileges_valid:
                if effective_runtime != {"SELECT"}:
                    _add_error(errors, f"{label}_classified_view_runtime_privileges_invalid_{sorted(effective_runtime)}")
        elif runtime_select or grantable or effective_runtime or runtime_acl_entries:
            _add_error(errors, f"{label}_unclassified_ordinary_view_runtime_authority")

    if len(legacy_rows) > 1:
        names = {str(row["relation_name"]) for row in legacy_rows}
        _add_error(errors, f"classified_relation_view_rows_must_be_at_most_one_{sorted(names)}")

    return tuple(errors)


def evaluate_runtime_authority(
    manifest: dict[str, Any],
    view_rows: list[dict[str, Any]],
    table_privilege_rows: list[dict[str, Any]],
    column_privilege_rows: list[dict[str, Any]],
    *,
    runtime_role: str = "sqag_runtime",
) -> tuple[str, ...]:
    """Evaluate view ACLs plus the conditional bound-source table proof."""

    errors = list(evaluate_view_authority(manifest, view_rows, runtime_role=runtime_role))
    errors.extend(
        evaluate_bound_source_authority(
            manifest,
            view_rows,
            table_privilege_rows,
            column_privilege_rows,
            runtime_role=runtime_role,
        )
    )
    return tuple(errors)


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


def _validate_view_query(query: str, errors: list[str]) -> None:
    tokens = _read_only_query_tokens(query, 'view_acl', errors)
    if tokens is None:
        return
    parts = _projection_parts(tokens, 'view_acl', errors)
    if parts is not None:
        _require_projection_shape(
            parts,
            [
                'relation_name', 'relation_kind', 'owner', 'relation_acl', 'acl_entries',
                'column_acl_entries', 'view_definition', 'view_dependencies', 'view_columns',
                'relation_options', 'view_security', 'runtime_privileges', 'runtime_select',
                'runtime_select_grantable',
            ],
            'view_acl',
            errors,
        )
    required_patterns = (
        ['from', *_qualified_pattern('pg_catalog.pg_class'), 'c'],
        ['join', *_qualified_pattern('pg_catalog.pg_namespace'), 'n'],
        ['join', *_qualified_pattern('pg_catalog.pg_roles'), 'r'],
        ['pg_catalog', '.', 'pg_attribute'],
        ['pg_catalog', '.', 'pg_depend'],
        ['pg_catalog', '.', 'pg_type'],
        ['pg_catalog', '.', 'pg_options_to_table'],
        ['pg_catalog', '.', 'pg_get_viewdef'],
        ['pg_catalog', '.', 'format_type'],
        ['has_column_privilege'],
        ['pg_catalog', '.', 'aclexplode'],
        ['jsonb_agg'],
        ['jsonb_build_object'],
        ['jsonb_object_agg'],
        ['cross', 'join', 'lateral'],
        ['a', '.', 'attacl'],
        ['d', '.', 'refclassid'],
        ['d', '.', 'deptype'],
        ['where', 'n', '.', 'nspname', '=', ('STRING', 'public')],
        ['c', '.', 'relkind', 'in', '(', ('STRING_EXACT', 'v'), ',', ('STRING_EXACT', 'm'), ')'],
        ['order', 'by', 'c', '.', 'relkind', ',', 'c', '.', 'relname'],
    )
    for pattern in required_patterns:
        if not _find_token_pattern(tokens, pattern):
            _add_error(errors, f'verification_query_view_acl_missing_structural_pattern_{pattern}')
    if _count_token_pattern(tokens, ['pg_catalog', '.', 'aclexplode']) < 2:
        _add_error(errors, 'verification_query_view_acl_requires_relation_and_column_aclexplode')
    if _count_token_pattern(tokens, ['cross', 'join', 'lateral']) < 5:
        _add_error(errors, 'verification_query_view_acl_requires_structured_lateral_evidence')


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
        elif key == 'view_acl':
            _validate_view_query(value, errors)
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
                (
                    "has_table_privilege",
                    "pg_catalog.pg_class",
                    "is_grantable",
                    "owner_select",
                    "relrowsecurity",
                    "relforcerowsecurity",
                    "row_security_enabled",
                    "row_security_forced",
                    "'sqag_runtime'",
                    "'sqag_migrator'",
                    "'public'",
                ),
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


if __name__ == "__main__":
    raise SystemExit(validate_manifest_strictly(str(MANIFEST_PATH)))
