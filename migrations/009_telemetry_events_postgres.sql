-- SQAG metadata-only telemetry producer schema for PostgreSQL-compatible storage.
-- Statements are separated by the repository migration boundary marker.
-- SQAG_STATEMENT_BOUNDARY
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
)
-- SQAG_STATEMENT_BOUNDARY
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
)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_telemetry_source_state_workspace_idx
  on sqag_telemetry_source_state (workspace_id, source_product, high_watermark)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_telemetry_events_feed_idx
  on sqag_telemetry_events (workspace_id, source_sequence, event_id)
-- SQAG_STATEMENT_BOUNDARY
create unique index if not exists sqag_telemetry_events_source_sequence_uidx
  on sqag_telemetry_events (workspace_id, source_sequence)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_telemetry_events_retention_idx
  on sqag_telemetry_events (workspace_id, deletion_state, retention_expires_at, event_id)
-- SQAG_STATEMENT_BOUNDARY
create index if not exists sqag_telemetry_events_actor_idx
  on sqag_telemetry_events (workspace_id, actor_tracking_id, occurred_at)
-- SQAG_STATEMENT_BOUNDARY
create unique index if not exists sqag_telemetry_events_retry_uidx
  on sqag_telemetry_events (workspace_id, retry_lineage_id, attempt_number)
  where retry_lineage_id is not null and attempt_number is not null
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_telemetry_source_state_no_delete on sqag_telemetry_source_state
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_telemetry_source_state_no_delete
before delete on sqag_telemetry_source_state for each row execute function sqag_reject_immutable_change()
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_telemetry_events_no_update on sqag_telemetry_events
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_telemetry_events_no_update
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
on sqag_telemetry_events for each row execute function sqag_reject_immutable_change()
-- SQAG_STATEMENT_BOUNDARY
drop trigger if exists sqag_telemetry_events_guard_delete on sqag_telemetry_events
-- SQAG_STATEMENT_BOUNDARY
create trigger sqag_telemetry_events_guard_delete
before delete on sqag_telemetry_events for each row execute function sqag_require_retention_delete_authorization()
-- SQAG_STATEMENT_BOUNDARY
create function public.sqag_quote_session_deletion_hold_blocked_v2(
  text,
  text
) returns boolean
language sql
stable
parallel unsafe
security definer
set search_path = pg_catalog, public
as $sqag_v2$
with
input_state as (
  select (
    $1 is not null
    and $2 is not null
    and $1 = btrim($1)
    and $2 = btrim($2)
    and $1 ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    and $2 ~ '^quote-[A-Za-z0-9_-]{3,64}$'
  ) as valid
),
session_rows as (
  select s.workspace_id, s.session_id
  from public.sqag_quote_sessions s
  where (select valid from input_state)
    and s.workspace_id = $1
    and s.session_id = $2
),
publication_versions as (
  select v.workspace_id, v.session_id, v.run_id, v.legal_hold
  from public.sqag_quote_publication_versions v
  where (select valid from input_state)
    and v.workspace_id = $1
    and v.session_id = $2
),
publication_version_state as (
  select coalesce(bool_or(
    v.run_id is null
    or v.run_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    or v.legal_hold is null
    or v.legal_hold not in (0, 1)
    or v.legal_hold = 1
    or not exists (
      select 1
      from public.sqag_generation_runs r
      where r.workspace_id = v.workspace_id
        and r.run_id = v.run_id
    )
    or exists (
      select 1
      from public.sqag_legal_holds h
      where h.workspace_id = v.workspace_id
        and h.target_type = 'publication_version'
        and h.target_id = v.run_id
        and h.enabled = 1
    )
  ), false) as blocked
  from publication_versions v
),
session_runs as (
  select distinct r.run_id, r.legal_hold
  from public.sqag_generation_runs r
  where (select valid from input_state)
    and r.workspace_id = $1
    and (
      r.quote_session_id = $2
      or exists (
        select 1
        from publication_versions v
        where v.workspace_id = r.workspace_id
          and v.run_id = r.run_id
      )
    )
),
run_candidates as (
  select r.run_id
  from session_runs r
),
feedback_rows as (
  select distinct f.workspace_id, f.feedback_id, f.legal_hold, f.run_id, f.session_id,
    f.publication_version_id
  from public.sqag_feedback f
  where (select valid from input_state)
    and f.workspace_id = $1
    and (
      f.session_id = $2
      or f.run_id in (select r.run_id from session_runs r)
      or f.publication_version_id in (select v.run_id from publication_versions v)
    )
),
session_audits as (
  select a.workspace_id, a.event_id, a.legal_hold, a.run_id, a.feedback_id, a.session_id
  from public.sqag_audit_events a
  where (select valid from input_state)
    and a.workspace_id = $1
    and a.session_id = $2
),
standalone_audits as (
  select a.event_id, a.legal_hold
  from session_audits a
  where a.run_id is null
    and a.feedback_id is null
),
feedback_session_runs as (
  select f.feedback_id, r.run_id
  from feedback_rows f
  join public.sqag_generation_runs r
    on r.workspace_id = $1
   and r.quote_session_id = f.session_id
  where f.session_id is not null
),
feedback_target_state as (
  select f.feedback_id,
    (
      (f.session_id is not null and (
        f.session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
        or not exists (
          select 1
          from session_rows s
          where s.workspace_id = f.workspace_id
            and s.session_id = f.session_id
        )
      ))
      or (f.run_id is not null and (
        f.run_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
        or not exists (
          select 1
          from run_candidates c
          where c.run_id = f.run_id
        )
      ))
      or (f.publication_version_id is not null and (
        f.publication_version_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
        or not exists (
          select 1
          from publication_versions v
          where v.workspace_id = f.workspace_id
            and v.session_id = $2
            and v.run_id = f.publication_version_id
        )
      ))
    ) as blocked
  from feedback_rows f
),
run_graph_detail as (
  select c.run_id,
    (
      c.run_id is null
      or c.run_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      or not exists (
        select 1
        from public.sqag_generation_runs r
        where r.workspace_id = $1
          and r.run_id = c.run_id
      )
      or exists (
        select 1
        from public.sqag_generation_runs r
        where r.workspace_id = $1
          and r.run_id = c.run_id
          and (
            r.legal_hold is null
            or r.legal_hold not in (0, 1)
            or r.legal_hold = 1
            or (r.quote_session_id is not null and (
              r.quote_session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
              or not exists (
                select 1
                from public.sqag_quote_sessions s
                where s.workspace_id = r.workspace_id
                  and s.session_id = r.quote_session_id
              )
            ))
            or exists (
              select 1
              from public.sqag_legal_holds h
              where h.workspace_id = r.workspace_id
                and h.target_type = 'generation_run'
                and h.target_id = r.run_id
                and h.enabled = 1
            )
          )
      )
      or exists (
        select 1
        from public.sqag_generation_evidence e
        where e.workspace_id = $1
          and e.run_id = c.run_id
          and (
            e.evidence_id is null
            or e.evidence_id !~ '^evidence-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            or e.legal_hold is null
            or e.legal_hold not in (0, 1)
            or e.legal_hold = 1
            or exists (
              select 1
              from public.sqag_legal_holds h
              where h.workspace_id = e.workspace_id
                and h.target_type = 'generation_evidence'
                and h.target_id = e.evidence_id
                and h.enabled = 1
            )
          )
      )
      or exists (
        select 1
        from public.sqag_audit_events a
        where a.workspace_id = $1
          and a.run_id = c.run_id
          and (
            a.event_id is null
            or a.event_id !~ '^audit-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            or a.legal_hold is null
            or a.legal_hold not in (0, 1)
            or a.legal_hold = 1
            or (a.feedback_id is not null and (
              a.feedback_id !~ '^feedback-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
              or not exists (
                select 1
                from feedback_target_state f
                where f.feedback_id = a.feedback_id
                  and not f.blocked
              )
            ))
            or (a.session_id is not null and (
              a.session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
              or not exists (
                select 1
                from session_rows s
                where s.workspace_id = a.workspace_id
                  and s.session_id = a.session_id
              )
            ))
            or exists (
              select 1
              from public.sqag_legal_holds h
              where h.workspace_id = a.workspace_id
                and h.target_type = 'audit_event'
                and h.target_id = a.event_id
                and h.enabled = 1
            )
          )
      )
    ) as blocked
  from run_candidates c
),
session_audit_state as (
  select coalesce(bool_or(
    a.event_id is null
    or a.event_id !~ '^audit-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    or a.legal_hold is null
    or a.legal_hold not in (0, 1)
    or a.legal_hold = 1
    or (a.session_id is not null and (
      a.session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
      or not exists (
        select 1
        from session_rows s
        where s.workspace_id = a.workspace_id
          and s.session_id = a.session_id
      )
    ))
    or (a.run_id is not null and (
      a.run_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      or not exists (
        select 1
        from run_candidates c
        where c.run_id = a.run_id
      )
      or exists (
        select 1
        from run_graph_detail g
        where g.run_id = a.run_id
          and g.blocked
      )
    ))
    or (a.feedback_id is not null and (
      a.feedback_id !~ '^feedback-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      or not exists (
        select 1
        from feedback_target_state f
        where f.feedback_id = a.feedback_id
          and not f.blocked
      )
    ))
    or exists (
      select 1
      from public.sqag_legal_holds h
      where h.workspace_id = a.workspace_id
        and h.target_type = 'audit_event'
        and h.target_id = a.event_id
        and h.enabled = 1
    )
  ), false) as blocked
  from session_audits a
),
feedback_session_run_state as (
  select f.feedback_id,
    count(r.run_id) > 500 as too_many
  from feedback_rows f
  join feedback_session_runs r on r.feedback_id = f.feedback_id
  group by f.feedback_id
),
feedback_state as (
  select coalesce(bool_or(
    f.feedback_id is null
    or f.feedback_id !~ '^feedback-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    or f.legal_hold is null
    or f.legal_hold not in (0, 1)
    or f.legal_hold = 1
    or exists (
      select 1
      from feedback_target_state target
      where target.feedback_id = f.feedback_id
        and target.blocked
    )
    or exists (
      select 1
      from public.sqag_legal_holds h
      where h.workspace_id = $1
        and h.target_type = 'feedback'
        and h.target_id = f.feedback_id
        and h.enabled = 1
    )
    or (f.run_id is not null and exists (
      select 1
      from run_graph_detail g
      where g.run_id = f.run_id
        and g.blocked
    ))
    or (f.session_id is not null and (
      exists (
        select 1
        from feedback_session_run_state rs
        where rs.feedback_id = f.feedback_id
          and rs.too_many
      )
      or exists (
        select 1
        from feedback_session_runs fsr
        join run_graph_detail g on g.run_id = fsr.run_id
        where fsr.feedback_id = f.feedback_id
          and g.blocked
      )
    ))
    or exists (
      select 1
      from public.sqag_feedback_status_history h
      where h.workspace_id = f.workspace_id
        and h.feedback_id = f.feedback_id
        and (
          h.history_id is null
          or h.history_id !~ '^feedback-history-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
          or h.legal_hold is null
          or h.legal_hold not in (0, 1)
          or h.legal_hold = 1
          or exists (
            select 1
            from public.sqag_legal_holds hld
            where hld.workspace_id = h.workspace_id
              and hld.target_type = 'feedback_status_history'
              and hld.target_id = h.history_id
              and hld.enabled = 1
          )
        )
    )
    or exists (
      select 1
      from public.sqag_audit_events a
      where a.workspace_id = f.workspace_id
        and a.feedback_id = f.feedback_id
        and (
          a.event_id is null
          or a.event_id !~ '^audit-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
          or a.legal_hold is null
          or a.legal_hold not in (0, 1)
          or a.legal_hold = 1
          or (a.feedback_id is not null and (
            a.feedback_id !~ '^feedback-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            or not exists (
              select 1
              from feedback_target_state target
              where target.feedback_id = a.feedback_id
                and not target.blocked
            )
          ))
          or (a.run_id is not null and (
            a.run_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            or not exists (
              select 1
              from run_candidates c
              where c.run_id = a.run_id
            )
            or exists (
              select 1
              from run_graph_detail g
              where g.run_id = a.run_id
                and g.blocked
            )
          ))
          or (a.session_id is not null and (
            a.session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
            or not exists (
              select 1
              from session_rows s
              where s.workspace_id = a.workspace_id
                and s.session_id = a.session_id
            )
          ))
          or exists (
            select 1
            from public.sqag_legal_holds h
            where h.workspace_id = a.workspace_id
              and h.target_type = 'audit_event'
              and h.target_id = a.event_id
              and h.enabled = 1
          )
        )
    )
  ), false) as blocked
  from feedback_rows f
),
telemetry_rows as (
  select e.workspace_id, e.event_id, e.source_product, e.source_sequence,
    e.session_reference, e.run_reference, e.support_reference, e.legal_hold
  from public.sqag_telemetry_events e
  where (select valid from input_state)
    and e.workspace_id = $1
    and (
      e.session_reference = $2
      or e.run_reference in (select run_id from run_candidates)
      or e.run_reference in (select run_id from publication_versions)
      or e.support_reference in (select feedback_id from feedback_rows)
    )
),
telemetry_state as (
  select coalesce(bool_or(
    e.event_id is null
    or e.event_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    or e.source_product is null
    or e.source_product <> 'sqag'
    or e.source_sequence is null
    or e.source_sequence < 1
    or e.legal_hold is null
    or e.legal_hold not in (0, 1)
    or e.legal_hold = 1
    or (e.session_reference is not null and (
      e.session_reference !~ '^quote-[A-Za-z0-9_-]{3,64}$'
      or not exists (
        select 1
        from session_rows s
        where s.workspace_id = e.workspace_id
          and s.session_id = e.session_reference
      )
    ))
    or (e.run_reference is not null and (
      e.run_reference !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      or not exists (
        select 1
        from run_candidates c
        where c.run_id = e.run_reference
      )
    ))
    or (e.support_reference is not null and (
      e.support_reference !~ '^feedback-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      or not exists (
        select 1
        from feedback_rows f
        where f.feedback_id = e.support_reference
      )
    ))
    or exists (
      select 1
      from public.sqag_legal_holds h
      where h.workspace_id = e.workspace_id
        and h.target_type = 'telemetry_event'
        and h.target_id = e.event_id
        and h.enabled = 1
    )
  ), false) as blocked
  from telemetry_rows e
),
standalone_audit_state as (
  select coalesce(bool_or(
    a.event_id is null
    or a.event_id !~ '^audit-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    or a.legal_hold is null
    or a.legal_hold not in (0, 1)
    or a.legal_hold = 1
    or exists (
      select 1
      from public.sqag_legal_holds h
      where h.workspace_id = $1
        and h.target_type = 'audit_event'
        and h.target_id = a.event_id
        and h.enabled = 1
    )
  ), false) as blocked
  from standalone_audits a
),
hold_state as (
  select coalesce(bool_or(
    h.enabled is null
    or h.enabled not in (0, 1)
    or (h.enabled = 1 and (
      h.target_type is null
      or h.target_type not in (
        'generation_run', 'generation_evidence', 'audit_event',
        'feedback', 'feedback_status_history', 'publication_version',
        'telemetry_event'
      )
      or h.target_id is null
      or h.target_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      or (h.target_type = 'generation_run' and not exists (
        select 1
        from public.sqag_generation_runs r
        where r.workspace_id = h.workspace_id
          and r.run_id = h.target_id
      ))
      or (h.target_type = 'generation_evidence' and not exists (
        select 1
        from public.sqag_generation_evidence e
        where e.workspace_id = h.workspace_id
          and e.evidence_id = h.target_id
      ))
      or (h.target_type = 'audit_event' and not exists (
        select 1
        from public.sqag_audit_events a
        where a.workspace_id = h.workspace_id
          and a.event_id = h.target_id
      ))
      or (h.target_type = 'feedback' and not exists (
        select 1
        from public.sqag_feedback f
        where f.workspace_id = h.workspace_id
          and f.feedback_id = h.target_id
      ))
      or (h.target_type = 'feedback_status_history' and not exists (
        select 1
        from public.sqag_feedback_status_history hs
        where hs.workspace_id = h.workspace_id
          and hs.history_id = h.target_id
      ))
      or (h.target_type = 'publication_version' and not exists (
        select 1
        from public.sqag_quote_publication_versions v
        where v.workspace_id = h.workspace_id
          and v.run_id = h.target_id
      ))
      or (h.target_type = 'telemetry_event' and not exists (
        select 1
        from public.sqag_telemetry_events e
        where e.workspace_id = h.workspace_id
          and e.event_id = h.target_id
      ))
    ))
  ), false) as blocked
  from public.sqag_legal_holds h
  where h.workspace_id = $1
)
select (
  not (select valid from input_state)
  or not exists (select 1 from session_rows)
  or (select blocked from publication_version_state)
  or exists (select 1 from run_graph_detail where blocked)
  or (select blocked from feedback_state)
  or (select blocked from session_audit_state)
  or (select blocked from standalone_audit_state)
  or (select blocked from telemetry_state)
  or (select blocked from hold_state)
) as hold_blocked
$sqag_v2$
-- SQAG_STATEMENT_BOUNDARY
alter function public.sqag_quote_session_deletion_hold_blocked_v2(text, text) owner to sqag_migrator
-- SQAG_STATEMENT_BOUNDARY
revoke all privileges on function public.sqag_quote_session_deletion_hold_blocked_v2(text, text) from public, sqag_runtime, sqag_maintenance
-- SQAG_STATEMENT_BOUNDARY
grant execute on function public.sqag_quote_session_deletion_hold_blocked_v2(text, text) to sqag_runtime
