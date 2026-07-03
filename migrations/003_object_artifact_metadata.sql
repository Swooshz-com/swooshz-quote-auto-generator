-- KQAG object-storage generated artifact metadata groundwork.
-- Reviewed migration for local/operator-applied database setup only.
-- Do not run against production without the platform storage runbook approval.

create table if not exists kqag_object_artifacts (
  artifact_id text not null primary key,
  workspace_id text not null,
  owner_type text not null,
  owner_id text not null,
  platform_user_id text,
  session_id text,
  job_id text,
  artifact_kind text not null,
  filename text not null,
  content_type text not null,
  size_bytes integer not null,
  checksum_sha256 text not null,
  object_provider_type text not null,
  object_key_ref text not null,
  status text not null default 'active',
  retention_status text not null default 'active',
  created_at text not null,
  updated_at text not null,
  deleted_at text,
  unique (workspace_id, owner_type, owner_id, artifact_kind)
);
