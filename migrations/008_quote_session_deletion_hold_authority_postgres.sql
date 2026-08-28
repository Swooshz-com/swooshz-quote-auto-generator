-- PostgreSQL runtime authority for hosted quote-session deletion hold checks.
-- The callable is deliberately read-only and returns only a boolean decision.
-- SQAG_STATEMENT_BOUNDARY
create function public.sqag_quote_session_deletion_hold_blocked(
  text,
  text
) returns boolean
language sql
stable
parallel unsafe
security definer
set search_path = pg_catalog, public
as $sqag$
with
input_state as (
  select (
    $1 is not null
    and $2 is not null
    and $1 = btrim($1)
    and $1 ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    and $2 = btrim($2)
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
feedback_rows as (
  select distinct f.workspace_id, f.feedback_id, f.legal_hold, f.run_id, f.session_id,
    f.publication_version_id
  from public.sqag_feedback f
  where (select valid from input_state)
    and f.workspace_id = $1
    and (
      f.session_id = $2
      or f.run_id in (
        select r.run_id
        from session_runs r
      )
      or f.publication_version_id in (
        select v.run_id
        from publication_versions v
      )
    )
),
session_audits as (
  select a.workspace_id, a.event_id, a.legal_hold, a.run_id, a.feedback_id
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
run_candidates as (
  select r.run_id
  from session_runs r
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
                from public.sqag_feedback f
                where f.workspace_id = a.workspace_id
                  and f.feedback_id = a.feedback_id
              )
            ))
            or (a.session_id is not null and (
              a.session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
              or not exists (
                select 1
                from public.sqag_quote_sessions s
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
session_audit_state as (
  select coalesce(bool_or(
    a.event_id is null
    or a.event_id !~ '^audit-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    or a.legal_hold is null
    or a.legal_hold not in (0, 1)
    or a.legal_hold = 1
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
          or (a.run_id is not null and (
            a.run_id !~ '^run-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            or not exists (
              select 1
              from public.sqag_generation_runs r
              where r.workspace_id = a.workspace_id
                and r.run_id = a.run_id
            )
          ))
          or (a.session_id is not null and (
            a.session_id !~ '^quote-[A-Za-z0-9_-]{3,64}$'
            or not exists (
              select 1
              from public.sqag_quote_sessions s
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
        'feedback', 'feedback_status_history', 'publication_version'
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
  or (select blocked from hold_state)
) as hold_blocked
$sqag$
-- SQAG_STATEMENT_BOUNDARY
alter function public.sqag_quote_session_deletion_hold_blocked(text, text) owner to sqag_migrator
-- SQAG_STATEMENT_BOUNDARY
revoke all privileges on function public.sqag_quote_session_deletion_hold_blocked(text, text) from public, sqag_runtime, sqag_maintenance
-- SQAG_STATEMENT_BOUNDARY
grant execute on function public.sqag_quote_session_deletion_hold_blocked(text, text) to sqag_runtime
