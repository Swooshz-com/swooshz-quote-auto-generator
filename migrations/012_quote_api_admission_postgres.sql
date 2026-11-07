-- Explicit operator migration source; no startup application or seed data.
create table public.sqag_quote_api_tenants (
  tenant_id text not null check (tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  workspace_id text not null check (workspace_id ~ '^[A-Za-z0-9_-]{1,128}$'),
  state text not null default 'disabled' check (state in ('active','disabled','revoked')),
  authority_revision bigint not null default 1 check (authority_revision between 1 and 9223372036854775806),
  policy_json text not null check (octet_length(policy_json) between 2 and 65536),
  created_at bigint not null check (created_at between 0 and 253402300799),
  expires_at bigint not null check (expires_at between 1 and 253402300799 and expires_at > created_at),
  revoked_at bigint check (revoked_at between 0 and 253402300799),
  create_minute_start bigint not null default 0 check (create_minute_start between 0 and 253402300799 and mod(create_minute_start, 60) = 0),
  create_minute_count integer not null default 0 check (create_minute_count between 0 and 15),
  create_day_start bigint not null default 0 check (create_day_start between 0 and 253402300799 and mod(create_day_start, 86400) = 0),
  create_day_count integer not null default 0 check (create_day_count between 0 and 100),
  active_jobs integer not null default 0 check (active_jobs between 0 and 2),
  counter_revision bigint not null default 0 check (counter_revision between 0 and 9223372036854775806),
  primary key (tenant_id),
  check ((state = 'revoked' and revoked_at is not null and revoked_at >= created_at) or (state <> 'revoked' and revoked_at is null))
);
create table public.sqag_quote_api_callers (
  tenant_id text not null,
  caller_id text not null check (caller_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  owner_subject text not null check (owner_subject ~ '^service:[A-Za-z0-9_-]{21}[AQgw]$'),
  credential_id text not null check (credential_id ~ '^[A-Za-z0-9_-]{21}[AQgw]$'),
  verifier bytea not null check (octet_length(verifier) = 32),
  state text not null default 'disabled' check (state in ('active','disabled','revoked')),
  credential_revision bigint not null default 1 check (credential_revision between 1 and 9223372036854775806),
  authority_revision bigint not null default 1 check (authority_revision between 1 and 9223372036854775806),
  operations_mask smallint not null default 0 check (operations_mask between 0 and 255),
  created_at bigint not null check (created_at between 0 and 253402300799),
  expires_at bigint not null check (expires_at between 1 and 253402300799 and expires_at > created_at),
  revoked_at bigint check (revoked_at between 0 and 253402300799),
  poll_minute_start bigint not null default 0 check (poll_minute_start between 0 and 253402300799 and mod(poll_minute_start, 60) = 0),
  poll_minute_count integer not null default 0 check (poll_minute_count between 0 and 120),
  counter_revision bigint not null default 0 check (counter_revision between 0 and 9223372036854775806),
  primary key (tenant_id, caller_id),
  foreign key (tenant_id) references public.sqag_quote_api_tenants (tenant_id) on update restrict on delete restrict,
  check ((state = 'revoked' and revoked_at is not null and revoked_at >= created_at) or (state <> 'revoked' and revoked_at is null))
);
alter table public.sqag_quote_api_tenants owner to sqag_migrator;
alter table public.sqag_quote_api_callers owner to sqag_migrator;
create index sqag_quote_api_tenants_workspace_idx on public.sqag_quote_api_tenants (workspace_id, tenant_id);
create unique index sqag_quote_api_callers_credential_uidx on public.sqag_quote_api_callers (credential_id);
create unique index sqag_quote_api_callers_subject_uidx on public.sqag_quote_api_callers (owner_subject);
create trigger sqag_quote_api_tenants_binding_immutable before update of tenant_id, workspace_id
  on public.sqag_quote_api_tenants for each row execute function public.sqag_reject_immutable_change();
create trigger sqag_quote_api_callers_binding_immutable before update of tenant_id, caller_id, owner_subject, credential_id
  on public.sqag_quote_api_callers for each row execute function public.sqag_reject_immutable_change();

-- SQAG_STATEMENT_BOUNDARY
create function public.sqag_quote_api_admission_lookup(text)
returns table (
  credential_id text, verifier bytea, tenant_id text, workspace_id text,
  caller_id text, owner_subject text, credential_revision bigint,
  tenant_revision bigint, caller_revision bigint, operations_mask smallint,
  expires_at bigint, caller_state text, tenant_state text, policy_json text
)
language sql stable parallel unsafe security definer
set search_path = pg_catalog, public
as $sqag$
select c.credential_id, c.verifier, c.tenant_id, t.workspace_id,
       c.caller_id, c.owner_subject, c.credential_revision,
       t.authority_revision, c.authority_revision, c.operations_mask,
       least(c.expires_at, t.expires_at), c.state, t.state, t.policy_json
from public.sqag_quote_api_callers c
join public.sqag_quote_api_tenants t on t.tenant_id = c.tenant_id
where $1 ~ '^[A-Za-z0-9_-]{21}[AQgw]$' and c.credential_id = $1
$sqag$;
alter function public.sqag_quote_api_admission_lookup(text) owner to sqag_migrator;
revoke all privileges on table public.sqag_quote_api_tenants, public.sqag_quote_api_callers from public, sqag_runtime, sqag_maintenance;
revoke all privileges on function public.sqag_quote_api_admission_lookup(text) from public, sqag_runtime, sqag_maintenance;
grant execute on function public.sqag_quote_api_admission_lookup(text) to sqag_runtime;
grant select (tenant_id, workspace_id, state, authority_revision, policy_json, created_at, expires_at, revoked_at, create_minute_start, create_minute_count, create_day_start, create_day_count, active_jobs, counter_revision) on public.sqag_quote_api_tenants to sqag_runtime;
grant select (tenant_id, caller_id, owner_subject, credential_id, state, credential_revision, authority_revision, operations_mask, created_at, expires_at, revoked_at, poll_minute_start, poll_minute_count, counter_revision) on public.sqag_quote_api_callers to sqag_runtime;
grant update (create_minute_start, create_minute_count, create_day_start, create_day_count, active_jobs, counter_revision) on public.sqag_quote_api_tenants to sqag_runtime;
grant update (poll_minute_start, poll_minute_count, counter_revision) on public.sqag_quote_api_callers to sqag_runtime;
