-- Durable, portable journal for object artifact replacement and cleanup.
-- This migration is applied only by the explicit SQLite/PostgreSQL migration flow.

create table if not exists sqag_object_artifact_operations (
  workspace_id text not null,
  owner_type text not null,
  owner_id text not null,
  operation_seq integer not null check (operation_seq > 0),
  operation_id text not null,
  request_sha256 text not null check (length(request_sha256) = 64),
  plan_json text not null,
  state text not null check (state in ('prepared', 'published', 'aborted')),
  cleanup_json text not null,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, owner_type, owner_id, operation_seq),
  unique (workspace_id, operation_id)
);

-- SQLite installs equivalent guards in the storage migration runner.
-- SQAG_POSTGRES_ONLY_BEGIN
-- SQAG_STATEMENT_BOUNDARY
create function public.sqag_object_artifact_operations_guard()
returns trigger
language plpgsql
volatile
parallel unsafe
security invoker
set search_path = pg_catalog, public
as $sqag$
begin
  if tg_op = 'DELETE' then
    raise exception using errcode = '23514', message = 'SQAG object artifact operation history is retained';
  end if;
  if row(
    new.workspace_id, new.owner_type, new.owner_id, new.operation_seq,
    new.operation_id, new.request_sha256, new.plan_json, new.created_at
  ) is distinct from row(
    old.workspace_id, old.owner_type, old.owner_id, old.operation_seq,
    old.operation_id, old.request_sha256, old.plan_json, old.created_at
  ) then
    raise exception using errcode = '23514', message = 'SQAG object artifact operation is immutable';
  end if;
  if not (
    new.state = old.state
    or (old.state = 'prepared' and new.state in ('published', 'aborted'))
  ) then
    raise exception using errcode = '23514', message = 'SQAG object artifact operation state transition is invalid';
  end if;
  return new;
end
$sqag$
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_object_artifact_operations_guard_update
before update on public.sqag_object_artifact_operations
for each row execute function public.sqag_object_artifact_operations_guard()
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_object_artifact_operations_guard_delete
before delete on public.sqag_object_artifact_operations
for each row execute function public.sqag_object_artifact_operations_guard()
-- SQAG_STATEMENT_BOUNDARY
create function public.sqag_object_artifact_cleanup_policy_lock()
returns trigger
language plpgsql
volatile
parallel unsafe
security invoker
set search_path = pg_catalog, public
as $sqag$
declare
  target_workspace_id text;
begin
  if tg_op = 'DELETE' then
    target_workspace_id := old.workspace_id;
  else
    target_workspace_id := new.workspace_id;
  end if;
  perform pg_advisory_xact_lock(
    hashtextextended(target_workspace_id || ':sqag_object_artifact_cleanup_policy_v1', 0)
  );
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$sqag$
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_object_artifact_cleanup_version_lock
before insert or update or delete on public.sqag_quote_publication_versions
for each row execute function public.sqag_object_artifact_cleanup_policy_lock()
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_object_artifact_cleanup_hold_lock
before insert or update or delete on public.sqag_legal_holds
for each row execute function public.sqag_object_artifact_cleanup_policy_lock()
-- SQAG_POSTGRES_ONLY_END
