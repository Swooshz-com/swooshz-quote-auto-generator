-- Postgres-only controlled DELETE guards for immutable forensic rows.
-- SQAG_STATEMENT_BOUNDARY
create or replace function sqag_require_retention_delete_authorization() returns trigger language plpgsql as $$
declare expected_type text; expected_id text; consumed_authorization_id text;
begin
  expected_type := TG_TABLE_NAME;
  if TG_TABLE_NAME = 'sqag_generation_evidence' then expected_id := OLD.evidence_id; else expected_id := OLD.event_id; end if;
  delete from sqag_retention_delete_authorizations
  where authorization_id = (
    select authorization_id from sqag_retention_delete_authorizations
    where workspace_id = OLD.workspace_id and record_type = expected_type and record_id = expected_id
    order by created_at, authorization_id
    limit 1
  )
  returning authorization_id into consumed_authorization_id;
  if consumed_authorization_id is null then
    raise exception 'SQAG forensic deletion is restricted';
  end if;
  return OLD;
end $$
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_generation_evidence_guard_delete on sqag_generation_evidence
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_generation_evidence_guard_delete before delete on sqag_generation_evidence for each row execute function sqag_require_retention_delete_authorization()
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_audit_events_guard_delete on sqag_audit_events
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_audit_events_guard_delete before delete on sqag_audit_events for each row execute function sqag_require_retention_delete_authorization()
