create table public.sqag_quote_ai_spend (
  workspace_id text not null,
  job_id text not null,
  session_id text not null,
  actor_id text not null,
  incarnation text not null,
  claim_token text not null,
  category text not null,
  attempts_json text not null,
  primary key (workspace_id, job_id),
  foreign key (workspace_id, session_id) references public.sqag_quote_sessions (workspace_id, session_id) on delete cascade
);
create index sqag_quote_ai_spend_session_idx on public.sqag_quote_ai_spend (workspace_id, session_id);
revoke all privileges on table public.sqag_quote_ai_spend from public, sqag_runtime, sqag_maintenance;
grant select, insert, update on table public.sqag_quote_ai_spend to sqag_runtime;
