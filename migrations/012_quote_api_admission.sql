-- Synthetic fixture only. Never included in runtime SQLite migrations.
pragma foreign_keys = on;
create table if not exists sqag_quote_api_tenants (
  tenant_id text not null check (length(tenant_id) between 1 and 128 and substr(tenant_id,1,1) glob '[A-Za-z0-9]' and tenant_id not glob '*[^A-Za-z0-9._:-]*') check (tenant_id is null or typeof(tenant_id) = 'text'),
  workspace_id text not null check (length(workspace_id) between 1 and 128 and workspace_id not glob '*[^A-Za-z0-9_-]*') check (workspace_id is null or typeof(workspace_id) = 'text'),
  state text not null default 'disabled' check (state in ('active','disabled','revoked')) check (state is null or typeof(state) = 'text'),
  authority_revision integer not null default 1 check (authority_revision between 1 and 9223372036854775806) check (authority_revision is null or typeof(authority_revision) = 'integer'),
  policy_json text not null check (length(cast(policy_json as blob)) between 2 and 65536) check (policy_json is null or typeof(policy_json) = 'text'),
  created_at integer not null check (created_at between 0 and 253402300799) check (created_at is null or typeof(created_at) = 'integer'),
  expires_at integer not null check (expires_at between 1 and 253402300799 and expires_at > created_at) check (expires_at is null or typeof(expires_at) = 'integer'),
  revoked_at integer check (revoked_at between 0 and 253402300799) check (revoked_at is null or typeof(revoked_at) = 'integer'),
  create_minute_start integer not null default 0 check (create_minute_start between 0 and 253402300799 and create_minute_start % 60 = 0) check (create_minute_start is null or typeof(create_minute_start) = 'integer'),
  create_minute_count integer not null default 0 check (create_minute_count between 0 and 15) check (create_minute_count is null or typeof(create_minute_count) = 'integer'),
  create_day_start integer not null default 0 check (create_day_start between 0 and 253402300799 and create_day_start % 86400 = 0) check (create_day_start is null or typeof(create_day_start) = 'integer'),
  create_day_count integer not null default 0 check (create_day_count between 0 and 100) check (create_day_count is null or typeof(create_day_count) = 'integer'),
  active_jobs integer not null default 0 check (active_jobs between 0 and 2) check (active_jobs is null or typeof(active_jobs) = 'integer'),
  counter_revision integer not null default 0 check (counter_revision between 0 and 9223372036854775806) check (counter_revision is null or typeof(counter_revision) = 'integer'),
  primary key (tenant_id),
  check ((state = 'revoked' and revoked_at is not null and revoked_at >= created_at) or (state <> 'revoked' and revoked_at is null))
);
create table if not exists sqag_quote_api_callers (
  tenant_id text not null check (tenant_id is null or typeof(tenant_id) = 'text'),
  caller_id text not null check (length(caller_id) between 1 and 128 and substr(caller_id,1,1) glob '[A-Za-z0-9]' and caller_id not glob '*[^A-Za-z0-9._:-]*') check (caller_id is null or typeof(caller_id) = 'text'),
  owner_subject text not null check (substr(owner_subject,1,8) = 'service:' and length(substr(owner_subject,9)) = 22 and substr(owner_subject,9) not glob '*[^A-Za-z0-9_-]*' and substr(substr(owner_subject,9),22,1) glob '[AQgw]') check (owner_subject is null or typeof(owner_subject) = 'text'),
  credential_id text not null check (length(credential_id) = 22 and credential_id not glob '*[^A-Za-z0-9_-]*' and substr(credential_id,22,1) glob '[AQgw]') check (credential_id is null or typeof(credential_id) = 'text'),
  verifier blob not null check (length(verifier) = 32) check (verifier is null or typeof(verifier) = 'blob'),
  state text not null default 'disabled' check (state in ('active','disabled','revoked')) check (state is null or typeof(state) = 'text'),
  credential_revision integer not null default 1 check (credential_revision between 1 and 9223372036854775806) check (credential_revision is null or typeof(credential_revision) = 'integer'),
  authority_revision integer not null default 1 check (authority_revision between 1 and 9223372036854775806) check (authority_revision is null or typeof(authority_revision) = 'integer'),
  operations_mask integer not null default 0 check (operations_mask between 0 and 255) check (operations_mask is null or typeof(operations_mask) = 'integer'),
  created_at integer not null check (created_at between 0 and 253402300799) check (created_at is null or typeof(created_at) = 'integer'),
  expires_at integer not null check (expires_at between 1 and 253402300799 and expires_at > created_at) check (expires_at is null or typeof(expires_at) = 'integer'),
  revoked_at integer check (revoked_at between 0 and 253402300799) check (revoked_at is null or typeof(revoked_at) = 'integer'),
  poll_minute_start integer not null default 0 check (poll_minute_start between 0 and 253402300799 and poll_minute_start % 60 = 0) check (poll_minute_start is null or typeof(poll_minute_start) = 'integer'),
  poll_minute_count integer not null default 0 check (poll_minute_count between 0 and 120) check (poll_minute_count is null or typeof(poll_minute_count) = 'integer'),
  counter_revision integer not null default 0 check (counter_revision between 0 and 9223372036854775806) check (counter_revision is null or typeof(counter_revision) = 'integer'),
  primary key (tenant_id, caller_id),
  foreign key (tenant_id) references sqag_quote_api_tenants (tenant_id) on update restrict on delete restrict,
  check ((state = 'revoked' and revoked_at is not null and revoked_at >= created_at) or (state <> 'revoked' and revoked_at is null))
);
create index if not exists sqag_quote_api_tenants_workspace_idx on sqag_quote_api_tenants (workspace_id, tenant_id);
create unique index if not exists sqag_quote_api_callers_credential_uidx on sqag_quote_api_callers (credential_id);
create unique index if not exists sqag_quote_api_callers_subject_uidx on sqag_quote_api_callers (owner_subject);
create trigger if not exists sqag_quote_api_tenants_binding_immutable before update of tenant_id, workspace_id on sqag_quote_api_tenants
begin select raise(abort, 'SQAG immutable record cannot be changed'); end;
create trigger if not exists sqag_quote_api_callers_binding_immutable before update of tenant_id, caller_id, owner_subject, credential_id on sqag_quote_api_callers
begin select raise(abort, 'SQAG immutable record cannot be changed'); end;
