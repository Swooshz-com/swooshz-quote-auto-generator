create table public.sqag_quote_templates (
  workspace_id text not null,
  template_id text not null,
  name text not null,
  definition_json text not null,
  fingerprint text not null,
  save_number bigint not null,
  is_default integer not null default 0,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, template_id)
);
create unique index sqag_quote_templates_default_uidx on public.sqag_quote_templates (workspace_id) where is_default = 1;
revoke all privileges on table public.sqag_quote_templates from public, sqag_runtime, sqag_maintenance;
grant select, insert, update, delete on table public.sqag_quote_templates to sqag_runtime;
