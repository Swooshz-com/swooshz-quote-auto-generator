-- Additive feedback-to-publication binding for existing Postgres databases.
-- SQAG_STATEMENT_BOUNDARY
alter table sqag_feedback add column if not exists publication_version_id text
-- SQAG_STATEMENT_BOUNDARY
alter table sqag_feedback add column if not exists link_resolution_source text
-- SQAG_STATEMENT_BOUNDARY
alter table sqag_feedback add column if not exists link_resolved_at text
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_feedback_publication_idx on sqag_feedback (workspace_id, publication_version_id, run_id)
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_feedback_linkage_no_update on sqag_feedback
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_feedback_linkage_no_update
before update of run_id, session_id, publication_version_id,
  link_resolution_source, link_resolved_at on sqag_feedback
for each row execute function sqag_reject_immutable_change()
-- SQAG_STATEMENT_BOUNDARY
