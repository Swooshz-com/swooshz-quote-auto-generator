-- SQAG platform-scoped app storage boundary.
-- Reviewed migration for local/operator-applied database setup only.
-- Do not run against production without the platform storage runbook approval.

create table if not exists sqag_profiles (
  workspace_id text not null,
  profile_id text not null,
  payload_json text not null,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, profile_id)
);

create table if not exists sqag_pricing_references (
  workspace_id text not null,
  reference_id text not null,
  payload_json text not null,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, reference_id)
);

create table if not exists sqag_quote_sessions (
  workspace_id text not null,
  session_id text not null,
  metadata_json text not null,
  draft_files_json text not null default '[]',
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, session_id)
);
