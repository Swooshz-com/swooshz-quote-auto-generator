-- SQAG platform-scoped file and generated quote artifact storage.
-- Reviewed migration for local/operator-applied database setup only.
-- Do not run against production without the platform storage runbook approval.

create table if not exists sqag_quote_artifacts (
  workspace_id text not null,
  session_id text not null,
  artifact_kind text not null,
  filename text not null,
  content_type text not null,
  size_bytes integer not null,
  content_blob blob not null,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, session_id, artifact_kind)
);

create table if not exists sqag_file_artifacts (
  workspace_id text not null,
  owner_type text not null,
  owner_id text not null,
  artifact_kind text not null,
  filename text not null,
  content_type text not null,
  size_bytes integer not null,
  content_blob blob not null,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, owner_type, owner_id, artifact_kind)
);