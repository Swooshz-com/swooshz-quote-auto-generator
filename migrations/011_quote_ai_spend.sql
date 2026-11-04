create table if not exists sqag_quote_ai_spend (
  workspace_id text not null,
  job_id text not null,
  session_id text not null,
  actor_id text not null,
  incarnation text not null,
  claim_token text not null,
  category text not null,
  attempts_json text not null,
  primary key (workspace_id, job_id),
  foreign key (workspace_id, session_id) references sqag_quote_sessions (workspace_id, session_id) on delete cascade
);

create index if not exists sqag_quote_ai_spend_session_idx on sqag_quote_ai_spend (workspace_id, session_id);
