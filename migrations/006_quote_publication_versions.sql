-- Versioned quote publication staging for SQLite/local and SQLite-backed UAT.

create table if not exists sqag_quote_publication_versions (
  workspace_id text not null,
  session_id text not null,
  run_id text not null,
  job_id text,
  state text not null check (state in ('staged','published','superseded','failed')),
  artifact_storage_mode text not null check (artifact_storage_mode in ('database','object')),
  artifact_source text not null default 'version' check (artifact_source in ('version','legacy_current')),
  metadata_json text not null,
  error_code text,
  created_at text not null,
  updated_at text not null,
  promoted_at text,
  failed_at text,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  deletion_state text not null default 'active' check (deletion_state in ('active','deleting','delete_failed')),
  deletion_error_code text,
  deletion_claimed_at text,
  primary key (workspace_id, run_id),
  unique (workspace_id, session_id, run_id)
);

create index if not exists sqag_quote_publication_versions_session_idx
  on sqag_quote_publication_versions (workspace_id, session_id, state, updated_at, run_id);
create index if not exists sqag_quote_publication_versions_retention_idx
  on sqag_quote_publication_versions (workspace_id, deletion_state, retention_expires_at, run_id);

create table if not exists sqag_quote_publication_artifacts (
  workspace_id text not null,
  session_id text not null,
  run_id text not null,
  artifact_kind text not null,
  filename text not null,
  content_type text not null,
  size_bytes integer not null,
  checksum_sha256 text not null check (length(checksum_sha256) = 64),
  content_blob blob not null,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, run_id, artifact_kind),
  foreign key (workspace_id, run_id)
    references sqag_quote_publication_versions(workspace_id, run_id)
    on delete cascade
);

create index if not exists sqag_quote_publication_artifacts_session_idx
  on sqag_quote_publication_artifacts (workspace_id, session_id, run_id, artifact_kind);

-- Staging rows are bounded by the linked generation-run retention graph. A
-- current publication is never deleted independently of its session/run graph.
