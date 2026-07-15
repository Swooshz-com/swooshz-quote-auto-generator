-- SQAG Postgres-compatible forensic schema. Statements are separated by the marker below.
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_generation_runs (
  run_id text primary key, workspace_id text not null, actor_tracking_id text not null, actor_key_version text not null,
  job_id text, idempotency_key text, parent_run_id text references sqag_generation_runs(run_id), attempt_number integer not null default 1 check (attempt_number >= 1),
  job_type text not null, status text not null check (status in ('received','queued','running','blocked','completed','needs_confirmation','needs_review','completed_with_review_required','degraded','failed','cancelled','timed_out','abandoned','superseded')),
  error_category text, quote_session_id text, started_at text not null, completed_at text, app_revision text, evidence_schema_version text not null,
  retention_expires_at text not null, original_retention_expires_at text not null, legal_hold integer not null default 0,
  deletion_state text not null default 'active', deletion_error_code text, deletion_claimed_at text, unique (run_id, workspace_id)
)
-- SQAG_STATEMENT_BOUNDARY
create unique index if not exists sqag_generation_runs_workspace_job_uidx on sqag_generation_runs (workspace_id, job_id) where job_id is not null
-- SQAG_STATEMENT_BOUNDARY
create unique index if not exists sqag_generation_runs_workspace_idempotency_uidx on sqag_generation_runs (workspace_id, idempotency_key) where idempotency_key is not null
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_generation_evidence (
  evidence_id text primary key, run_id text not null, workspace_id text not null, evidence_type text not null, evidence_schema_version text not null,
  evidence_json text not null, evidence_sha256 text not null check (length(evidence_sha256) = 64), created_at text not null,
  retention_expires_at text not null, original_retention_expires_at text not null, legal_hold integer not null default 0,
  foreign key (run_id, workspace_id) references sqag_generation_runs(run_id, workspace_id)
)
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_audit_events (
  event_id text primary key, run_id text, workspace_id text not null, actor_tracking_id text not null, actor_key_version text not null,
  event_type text not null, event_json text not null, event_sha256 text not null check (length(event_sha256) = 64), created_at text not null,
  retention_expires_at text not null, original_retention_expires_at text not null, legal_hold integer not null default 0,
  foreign key (run_id, workspace_id) references sqag_generation_runs(run_id, workspace_id)
)
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_feedback (
  feedback_id text primary key, support_reference text not null unique, workspace_id text not null, reporter_tracking_id text not null, reporter_key_version text not null,
  run_id text, session_id text, category text not null, title text not null, message text not null, expected_result text, actual_result text, reproduction_steps text,
  impact text, link_choice text not null, manual_reference_text text, manual_reference_status text not null, resolved_reference_type text, resolved_reference_id text,
  diagnostic_metadata_json text not null, status text not null, created_at text not null, updated_at text not null, closed_at text,
  retention_expires_at text not null, original_retention_expires_at text not null, submission_retention_expires_at text not null, retention_policy_version text not null,
  legal_hold integer not null default 0, deletion_state text not null default 'active', deletion_error_code text, deletion_claimed_at text,
  unique (feedback_id, workspace_id), foreign key (run_id, workspace_id) references sqag_generation_runs(run_id, workspace_id)
)
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_feedback_status_history (
  history_id text primary key, feedback_id text not null, workspace_id text not null, from_status text, to_status text not null,
  actor_tracking_id text not null, actor_key_version text not null, resolution_note text, created_at text not null,
  retention_expires_at text not null, original_retention_expires_at text not null, legal_hold integer not null default 0,
  foreign key (feedback_id, workspace_id) references sqag_feedback(feedback_id, workspace_id)
)
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_legal_holds (
  hold_id text primary key, workspace_id text not null, target_type text not null, target_id text not null, enabled integer not null default 1,
  reason_code text not null, case_reference text, actor_tracking_id text not null, actor_key_version text not null, created_at text not null,
  released_by_tracking_id text, released_by_key_version text, released_at text
)
-- SQAG_STATEMENT_BOUNDARY
create unique index if not exists sqag_legal_holds_active_target_uidx on sqag_legal_holds (workspace_id, target_type, target_id) where enabled = 1
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_retention_delete_authorizations (
  authorization_id text primary key, workspace_id text not null, record_type text not null, record_id text not null, created_at text not null,
  unique (workspace_id, record_type, record_id)
)
-- SQAG_STATEMENT_BOUNDARY
create table if not exists sqag_deletion_receipts (
  receipt_id text primary key, workspace_id text not null, record_type text not null, record_id text not null, reason text not null,
  deleted_at text not null, original_retention_expires_at text not null, created_at text not null, retention_expires_at text not null,
  unique (workspace_id, record_type, record_id)
)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_generation_runs_workspace_started_idx on sqag_generation_runs (workspace_id, started_at)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_generation_runs_retention_idx on sqag_generation_runs (workspace_id, deletion_state, retention_expires_at, run_id)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_generation_runs_actor_idx on sqag_generation_runs (workspace_id, actor_tracking_id, started_at)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_generation_evidence_run_idx on sqag_generation_evidence (workspace_id, run_id, created_at)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_audit_events_run_idx on sqag_audit_events (workspace_id, run_id, created_at)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_feedback_workspace_status_idx on sqag_feedback (workspace_id, status, created_at)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_feedback_support_idx on sqag_feedback (workspace_id, support_reference)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_feedback_retention_idx on sqag_feedback (workspace_id, deletion_state, retention_expires_at, feedback_id)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_feedback_history_parent_idx on sqag_feedback_status_history (workspace_id, feedback_id, created_at)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_legal_holds_state_idx on sqag_legal_holds (workspace_id, enabled, target_type, target_id)
-- SQAG_STATEMENT_BOUNDARY
create or replace function sqag_reject_immutable_change() returns trigger language plpgsql as $$ begin raise exception 'SQAG immutable record cannot be changed'; end $$
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_generation_evidence_no_update on sqag_generation_evidence
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_generation_evidence_no_update before update on sqag_generation_evidence for each row execute function sqag_reject_immutable_change()
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_audit_events_no_update on sqag_audit_events
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_audit_events_no_update before update on sqag_audit_events for each row execute function sqag_reject_immutable_change()
