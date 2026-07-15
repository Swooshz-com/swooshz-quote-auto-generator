-- SQAG immutable generation evidence, append-only audit, feedback, and retention.
-- Reviewed migration for local/operator-applied database setup only.
-- Do not run against production without the platform storage runbook approval.

create table if not exists sqag_generation_runs (
  run_id text not null primary key,
  workspace_id text not null,
  actor_tracking_id text not null,
  job_type text not null,
  status text not null,
  error_category text,
  quote_session_id text,
  started_at text not null,
  completed_at text,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0
);

create table if not exists sqag_generation_evidence (
  evidence_id text not null primary key,
  run_id text not null,
  workspace_id text not null,
  evidence_type text not null,
  evidence_json text not null,
  evidence_sha256 text not null,
  created_at text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0
);

create table if not exists sqag_audit_events (
  event_id text not null primary key,
  run_id text,
  workspace_id text not null,
  event_type text not null,
  event_json text not null,
  created_at text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0
);

create table if not exists sqag_feedback (
  feedback_id text not null primary key,
  support_reference text not null unique,
  workspace_id text not null,
  reporter_tracking_id text not null,
  run_id text,
  session_id text,
  category text not null,
  title text not null,
  message text not null,
  expected_result text,
  actual_result text,
  reproduction_steps text,
  impact text,
  link_choice text not null,
  manual_reference_text text,
  manual_reference_status text not null,
  resolved_reference_type text,
  resolved_reference_id text,
  diagnostic_metadata_json text not null,
  status text not null,
  created_at text not null,
  updated_at text not null,
  closed_at text,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0
);

create table if not exists sqag_feedback_status_history (
  history_id text not null primary key,
  feedback_id text not null,
  workspace_id text not null,
  from_status text,
  to_status text not null,
  actor_tracking_id text not null,
  created_at text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0
);

create table if not exists sqag_deletion_receipts (
  receipt_id text not null primary key,
  workspace_id text not null,
  record_type text not null,
  record_id text not null,
  reason text not null,
  deleted_at text not null,
  original_retention_expires_at text not null,
  created_at text not null
);

create index if not exists sqag_generation_runs_workspace_started_idx
  on sqag_generation_runs (workspace_id, started_at);
create index if not exists sqag_generation_evidence_run_idx
  on sqag_generation_evidence (workspace_id, run_id);
create index if not exists sqag_audit_events_run_idx
  on sqag_audit_events (workspace_id, run_id, created_at);
create index if not exists sqag_feedback_workspace_status_idx
  on sqag_feedback (workspace_id, status, created_at);
