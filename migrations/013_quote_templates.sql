create table if not exists sqag_quote_templates (
  workspace_id text not null,
  template_id text not null,
  name text not null,
  definition_json text not null,
  fingerprint text not null,
  save_number integer not null,
  is_default integer not null default 0,
  created_at text not null,
  updated_at text not null,
  primary key (workspace_id, template_id)
);

create unique index if not exists sqag_quote_templates_default_uidx on sqag_quote_templates (workspace_id) where is_default = 1;
