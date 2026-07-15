-- SQAG forensic, feedback, legal-hold, and retention schema for SQLite/local evidence.
-- PR #140 is unmerged, so migration 004 is amended in-place before canonical history.
-- The Postgres-compatible variant is maintained separately to avoid mixed-dialect DDL.

pragma foreign_keys = on;

create table if not exists sqag_generation_runs (
  run_id text not null primary key,
  workspace_id text not null,
  actor_tracking_id text not null,
  actor_key_version text not null,
  job_id text,
  idempotency_key text,
  parent_run_id text,
  attempt_number integer not null default 1 check (attempt_number >= 1),
  job_type text not null,
  status text not null check (status in ('received','queued','running','blocked','completed','needs_confirmation','needs_review','completed_with_review_required','degraded','failed','cancelled','timed_out','abandoned','superseded')),
  error_category text,
  quote_session_id text,
  started_at text not null,
  completed_at text,
  app_revision text,
  evidence_schema_version text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  deletion_state text not null default 'active' check (deletion_state in ('active','review_required','deleting','delete_failed')),
  deletion_error_code text,
  deletion_claimed_at text,
  unique (run_id, workspace_id),
  foreign key (parent_run_id) references sqag_generation_runs(run_id)
);

create unique index if not exists sqag_generation_runs_workspace_job_uidx
  on sqag_generation_runs (workspace_id, job_id) where job_id is not null;
create unique index if not exists sqag_generation_runs_workspace_idempotency_uidx
  on sqag_generation_runs (workspace_id, idempotency_key) where idempotency_key is not null;

create table if not exists sqag_generation_evidence (
  evidence_id text not null primary key,
  run_id text not null,
  workspace_id text not null,
  evidence_type text not null,
  evidence_schema_version text not null,
  evidence_json text not null,
  evidence_sha256 text not null check (length(evidence_sha256) = 64),
  created_at text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  unique (evidence_id, workspace_id),
  foreign key (run_id, workspace_id) references sqag_generation_runs(run_id, workspace_id)
);

create table if not exists sqag_audit_events (
  event_id text not null primary key,
  run_id text,
  feedback_id text,
  session_id text,
  workspace_id text not null,
  actor_tracking_id text not null,
  actor_key_version text not null,
  event_type text not null,
  event_json text not null,
  event_sha256 text not null check (length(event_sha256) = 64),
  created_at text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  unique (event_id, workspace_id),
  foreign key (run_id, workspace_id) references sqag_generation_runs(run_id, workspace_id)
);

create table if not exists sqag_feedback (
  feedback_id text not null primary key,
  support_reference text not null unique,
  workspace_id text not null,
  reporter_tracking_id text not null,
  reporter_key_version text not null,
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
  status text not null check (status in ('open','triaged','in_progress','resolved','closed','rejected','duplicate')),
  created_at text not null,
  updated_at text not null,
  closed_at text,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  submission_retention_expires_at text not null,
  retention_policy_version text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  deletion_state text not null default 'active' check (deletion_state in ('active','review_required','deleting','delete_failed')),
  deletion_error_code text,
  deletion_claimed_at text,
  unique (feedback_id, workspace_id),
  foreign key (run_id, workspace_id) references sqag_generation_runs(run_id, workspace_id)
);

create table if not exists sqag_feedback_status_history (
  history_id text not null primary key,
  feedback_id text not null,
  workspace_id text not null,
  from_status text,
  to_status text not null,
  actor_tracking_id text not null,
  actor_key_version text not null,
  resolution_note text,
  created_at text not null,
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  unique (history_id, workspace_id),
  foreign key (feedback_id, workspace_id) references sqag_feedback(feedback_id, workspace_id)
);

create table if not exists sqag_legal_holds (
  hold_id text not null primary key,
  workspace_id text not null,
  target_type text not null check (target_type in ('generation_run','generation_evidence','audit_event','feedback','feedback_status_history')),
  target_id text not null,
  enabled integer not null default 1 check (enabled in (0, 1)),
  reason_code text not null,
  case_reference text,
  actor_tracking_id text not null,
  actor_key_version text not null,
  created_at text not null,
  released_by_tracking_id text,
  released_by_key_version text,
  released_at text
);
create unique index if not exists sqag_legal_holds_active_target_uidx
  on sqag_legal_holds (workspace_id, target_type, target_id) where enabled = 1;

create table if not exists sqag_retention_delete_authorizations (
  authorization_id text not null primary key,
  workspace_id text not null,
  record_type text not null,
  record_id text not null,
  created_at text not null,
  unique (workspace_id, record_type, record_id)
);

create table if not exists sqag_deletion_receipts (
  receipt_id text not null primary key,
  workspace_id text not null,
  record_type text not null,
  record_id text not null,
  reason text not null,
  deleted_at text not null,
  original_retention_expires_at text not null,
  created_at text not null,
  retention_expires_at text not null,
  unique (workspace_id, record_type, record_id)
);

create index if not exists sqag_generation_runs_workspace_started_idx on sqag_generation_runs (workspace_id, started_at);
create index if not exists sqag_generation_runs_retention_idx on sqag_generation_runs (workspace_id, deletion_state, retention_expires_at, run_id);
create index if not exists sqag_generation_runs_actor_idx on sqag_generation_runs (workspace_id, actor_tracking_id, started_at);
create index if not exists sqag_generation_evidence_run_idx on sqag_generation_evidence (workspace_id, run_id, created_at);
create index if not exists sqag_generation_evidence_retention_idx on sqag_generation_evidence (workspace_id, retention_expires_at);
create index if not exists sqag_audit_events_run_idx on sqag_audit_events (workspace_id, run_id, created_at);
create index if not exists sqag_audit_events_actor_idx on sqag_audit_events (workspace_id, actor_tracking_id, created_at);
create index if not exists sqag_audit_events_feedback_idx on sqag_audit_events (workspace_id, feedback_id, created_at);
create index if not exists sqag_audit_events_retention_idx on sqag_audit_events (workspace_id, retention_expires_at, event_id);
create index if not exists sqag_feedback_workspace_status_idx on sqag_feedback (workspace_id, status, created_at);
create index if not exists sqag_feedback_support_idx on sqag_feedback (workspace_id, support_reference);
create index if not exists sqag_feedback_retention_idx on sqag_feedback (workspace_id, deletion_state, retention_expires_at, feedback_id);
create index if not exists sqag_feedback_history_parent_idx on sqag_feedback_status_history (workspace_id, feedback_id, created_at);
create index if not exists sqag_legal_holds_state_idx on sqag_legal_holds (workspace_id, enabled, target_type, target_id);
create index if not exists sqag_deletion_receipts_retention_idx on sqag_deletion_receipts (workspace_id, retention_expires_at);

create trigger if not exists sqag_generation_evidence_no_update
before update of evidence_json, evidence_sha256, evidence_type, evidence_schema_version, run_id, workspace_id, created_at on sqag_generation_evidence begin
  select raise(abort, 'generation evidence is immutable');
end;
create trigger if not exists sqag_audit_events_no_update
before update of event_json, event_sha256, event_type, run_id, feedback_id, session_id, workspace_id, actor_tracking_id, actor_key_version, created_at on sqag_audit_events begin
  select raise(abort, 'audit events are immutable');
end;
create trigger if not exists sqag_generation_evidence_guard_delete
before delete on sqag_generation_evidence when not exists (
  select 1 from sqag_retention_delete_authorizations where workspace_id = old.workspace_id and record_type = 'sqag_generation_evidence' and record_id = old.evidence_id
) begin
  select raise(abort, 'generation evidence deletion is restricted');
end;
create trigger if not exists sqag_generation_evidence_cleanup_delete_auth
after delete on sqag_generation_evidence begin
  delete from sqag_retention_delete_authorizations where workspace_id = old.workspace_id and record_type = 'sqag_generation_evidence' and record_id = old.evidence_id;
end;
create trigger if not exists sqag_audit_events_guard_delete
before delete on sqag_audit_events when not exists (
  select 1 from sqag_retention_delete_authorizations where workspace_id = old.workspace_id and record_type = 'sqag_audit_events' and record_id = old.event_id
) begin
  select raise(abort, 'audit event deletion is restricted');
end;
create trigger if not exists sqag_audit_events_cleanup_delete_auth
after delete on sqag_audit_events begin
  delete from sqag_retention_delete_authorizations where workspace_id = old.workspace_id and record_type = 'sqag_audit_events' and record_id = old.event_id;
end;
