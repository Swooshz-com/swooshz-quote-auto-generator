-- SQAG metadata-only telemetry producer schema for SQLite/local evidence.
-- This migration is append-only for event identity and source ordering. The
-- source state is intentionally retained after event deletion.

pragma foreign_keys = on;

-- Migration 004 predates telemetry_event legal holds. Rebuild this small
-- authority table so the target enumeration remains strict without changing
-- the immutable migration 004 source.
drop index if exists sqag_legal_holds_active_target_uidx;
drop index if exists sqag_legal_holds_state_idx;
alter table sqag_legal_holds rename to sqag_legal_holds_legacy;
create table sqag_legal_holds (
  hold_id text not null primary key,
  workspace_id text not null,
  target_type text not null check (target_type in ('generation_run','generation_evidence','audit_event','feedback','feedback_status_history','publication_version','telemetry_event')),
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
insert into sqag_legal_holds (
  hold_id, workspace_id, target_type, target_id, enabled, reason_code,
  case_reference, actor_tracking_id, actor_key_version, created_at,
  released_by_tracking_id, released_by_key_version, released_at
)
select
  hold_id, workspace_id, target_type, target_id, enabled, reason_code,
  case_reference, actor_tracking_id, actor_key_version, created_at,
  released_by_tracking_id, released_by_key_version, released_at
from sqag_legal_holds_legacy;
drop table sqag_legal_holds_legacy;
create unique index if not exists sqag_legal_holds_active_target_uidx
  on sqag_legal_holds (workspace_id, target_type, target_id) where enabled = 1;
create index if not exists sqag_legal_holds_state_idx
  on sqag_legal_holds (workspace_id, enabled, target_type, target_id);

create table if not exists sqag_telemetry_source_state (
  workspace_id text not null,
  source_product text not null check (source_product = 'sqag'),
  next_source_sequence integer not null default 1 check (next_source_sequence >= 1),
  high_watermark integer not null default 0 check (high_watermark >= 0 and high_watermark < next_source_sequence),
  reconciliation_state text not null default 'healthy' check (reconciliation_state in ('healthy','reconciling','inconsistent')),
  last_reconciled_at text,
  reconciliation_reference text,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, source_product)
);

create table if not exists sqag_telemetry_events (
  workspace_id text not null,
  event_id text not null,
  source_product text not null check (source_product = 'sqag'),
  source_sequence integer not null check (source_sequence >= 1),
  event_type text not null check (event_type in ('generation','validation','ai_provider_attempt','pricing_change','profile_change','publication','download','feedback','security','rate_limit','abuse','cancellation','timeout','abandonment','supersession','storage_staging','storage_finalization','storage_compensation','configuration','operator_action','reconciliation','retention','legal_hold','deletion','backup','restore')),
  event_status text not null check (event_status in ('started','queued','running','success','failed','blocked','denied','completed','needs_confirmation','needs_review','completed_with_review_required','degraded','cancelled','timed_out','abandoned','superseded','available','unavailable','held','deleted','reconciled','staged','finalized','compensated','requested','updated','restored','rate_limited')),
  actor_tracking_id text not null,
  actor_key_version text not null,
  action_reference text,
  run_reference text,
  session_reference text,
  support_reference text,
  retry_lineage_id text,
  attempt_number integer check (attempt_number is null or attempt_number >= 1),
  provider text check (provider is null or provider in ('openai','deepseek')),
  model text,
  reasoning_level text check (reasoning_level is null or reasoning_level in ('none','minimal','low','medium','high','xhigh','max','ultra','standard')),
  operation_route text,
  purpose text,
  failure_class text check (failure_class is null or failure_class in ('missing_api_key','timeout','rate_limited','upstream_unavailable','http_error','network_error','invalid_json','schema_validation_failed','model_output_invalid','provider_error','generator_error','configuration','storage','authorization','unknown')),
  duration_ms integer check (duration_ms is null or duration_ms >= 0),
  usage_available integer check (usage_available is null or usage_available in (0, 1)),
  input_tokens integer check (input_tokens is null or input_tokens >= 0),
  output_tokens integer check (output_tokens is null or output_tokens >= 0),
  total_tokens integer check (total_tokens is null or total_tokens >= 0),
  cache_read_tokens integer check (cache_read_tokens is null or cache_read_tokens >= 0),
  cache_write_tokens integer check (cache_write_tokens is null or cache_write_tokens >= 0),
  cost_available integer check (cost_available is null or cost_available in (0, 1)),
  estimated_cost numeric check (estimated_cost is null or estimated_cost >= 0),
  actual_cost numeric check (actual_cost is null or actual_cost >= 0),
  currency text,
  cost_version text,
  quota_decision text check (quota_decision is null or quota_decision in ('allowed','denied','not_evaluated')),
  rate_limit_decision text check (rate_limit_decision is null or rate_limit_decision in ('allowed','denied','not_evaluated')),
  abuse_decision text check (abuse_decision is null or abuse_decision in ('allowed','denied','not_evaluated')),
  deployment_revision text,
  occurred_at text not null,
  immutable_metadata_digest text not null check (length(immutable_metadata_digest) = 64),
  retention_expires_at text not null,
  original_retention_expires_at text not null,
  legal_hold integer not null default 0 check (legal_hold in (0, 1)),
  deletion_state text not null default 'active' check (deletion_state in ('active','review_required','deleting','delete_failed')),
  deletion_error_code text,
  deletion_claimed_at text,
  primary key (workspace_id, event_id),
  foreign key (workspace_id, source_product) references sqag_telemetry_source_state(workspace_id, source_product)
);

create index if not exists sqag_telemetry_source_state_workspace_idx
  on sqag_telemetry_source_state (workspace_id, source_product, high_watermark);
create index if not exists sqag_telemetry_events_feed_idx
  on sqag_telemetry_events (workspace_id, source_sequence, event_id);
create unique index if not exists sqag_telemetry_events_source_sequence_uidx
  on sqag_telemetry_events (workspace_id, source_sequence);
create index if not exists sqag_telemetry_events_retention_idx
  on sqag_telemetry_events (workspace_id, deletion_state, retention_expires_at, event_id);
create index if not exists sqag_telemetry_events_actor_idx
  on sqag_telemetry_events (workspace_id, actor_tracking_id, occurred_at);
create unique index if not exists sqag_telemetry_events_retry_uidx
  on sqag_telemetry_events (workspace_id, retry_lineage_id, attempt_number)
  where retry_lineage_id is not null and attempt_number is not null;

create trigger if not exists sqag_telemetry_source_state_no_delete
before delete on sqag_telemetry_source_state begin
  select raise(abort, 'telemetry source state is retained');
end;
create trigger if not exists sqag_telemetry_events_no_update
before update of workspace_id, event_id, source_product, source_sequence,
  event_type, event_status, actor_tracking_id, actor_key_version,
  action_reference, run_reference, session_reference, support_reference,
  retry_lineage_id, attempt_number, provider, model, reasoning_level,
  operation_route, purpose, failure_class, duration_ms, usage_available,
  input_tokens, output_tokens, total_tokens, cache_read_tokens,
  cache_write_tokens, cost_available, estimated_cost, actual_cost, currency,
  cost_version, quota_decision, rate_limit_decision, abuse_decision,
  deployment_revision, occurred_at, immutable_metadata_digest,
  retention_expires_at, original_retention_expires_at
on sqag_telemetry_events begin
  select raise(abort, 'telemetry events are immutable');
end;
create trigger if not exists sqag_telemetry_events_guard_delete
before delete on sqag_telemetry_events when not exists (
  select 1 from sqag_retention_delete_authorizations
  where workspace_id = old.workspace_id
    and record_type = 'sqag_telemetry_events'
    and record_id = old.event_id
) begin
  select raise(abort, 'telemetry event deletion is restricted');
end;
create trigger if not exists sqag_telemetry_events_cleanup_delete_auth
after delete on sqag_telemetry_events begin
  delete from sqag_retention_delete_authorizations
  where workspace_id = old.workspace_id
    and record_type = 'sqag_telemetry_events'
    and record_id = old.event_id;
end;
