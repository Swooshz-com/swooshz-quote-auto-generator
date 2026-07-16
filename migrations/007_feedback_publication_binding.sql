-- Additive feedback-to-publication binding indexes and immutability for SQLite.
-- Existing SQLite databases receive the three nullable columns through the
-- guarded legacy-schema upgrader before this idempotent migration is executed.

create index if not exists sqag_feedback_publication_idx
  on sqag_feedback (workspace_id, publication_version_id, run_id);

create trigger if not exists sqag_feedback_linkage_no_update
before update of run_id, session_id, publication_version_id,
  link_resolution_source, link_resolved_at on sqag_feedback begin
  select raise(abort, 'feedback evidence linkage is immutable');
end;
