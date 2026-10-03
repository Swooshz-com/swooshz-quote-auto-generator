do $sqag_migration$
declare
  target_oid oid;
  attempt_check_count integer;
  historical_constraint_name text;
  historical_constraint_validated boolean;
  historical_definition text;
begin
  target_oid := pg_catalog.to_regclass('public.sqag_telemetry_events');
  if target_oid is null or not exists (
    select 1
    from pg_catalog.pg_class relation
    join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace
    where relation.oid = target_oid
      and namespace.nspname = 'public'
      and relation.relname = 'sqag_telemetry_events'
      and relation.relkind = 'r'
      and not relation.relispartition
  ) then
    raise exception 'SQAG telemetry attempt migration requires public.sqag_telemetry_events';
  end if;

  select count(*)
    into attempt_check_count
  from pg_catalog.pg_constraint constraint_row
  join pg_catalog.pg_attribute attempt_attribute
    on attempt_attribute.attrelid = constraint_row.conrelid
   and attempt_attribute.attname = 'attempt_number'
   and not attempt_attribute.attisdropped
  where constraint_row.conrelid = target_oid
    and constraint_row.contype = 'c'
    and constraint_row.conkey @> array[attempt_attribute.attnum]::smallint[];

  if attempt_check_count <> 1 then
    raise exception 'SQAG telemetry attempt migration requires exactly one historical attempt constraint';
  end if;

  select constraint_row.conname,
         constraint_row.convalidated,
         pg_catalog.regexp_replace(
           pg_catalog.lower(pg_catalog.pg_get_constraintdef(constraint_row.oid)),
           '[[:space:]()]',
           '',
           'g'
         )
    into historical_constraint_name,
         historical_constraint_validated,
         historical_definition
  from pg_catalog.pg_constraint constraint_row
  join pg_catalog.pg_attribute attempt_attribute
    on attempt_attribute.attrelid = constraint_row.conrelid
   and attempt_attribute.attname = 'attempt_number'
   and not attempt_attribute.attisdropped
  where constraint_row.conrelid = target_oid
    and constraint_row.contype = 'c'
    and constraint_row.conkey @> array[attempt_attribute.attnum]::smallint[];

  if not historical_constraint_validated
     or historical_definition <> 'checkattempt_numberisnullorattempt_number>=1' then
    raise exception 'SQAG telemetry historical attempt constraint is unvalidated or drifted';
  end if;

  execute pg_catalog.format(
    'alter table public.sqag_telemetry_events drop constraint %%I',
    historical_constraint_name
  );
end
$sqag_migration$;

alter table public.sqag_telemetry_events
  add constraint sqag_telemetry_events_attempt_semantics_ck
  check (
    attempt_number is null
    or attempt_number >= 1
    or (
      attempt_number = 0
      and event_type = 'validation'
      and event_status = 'blocked'
      and purpose is not null and purpose = 'request_validation'
      and failure_class is not null and failure_class = 'configuration'
      and model is null
      and usage_available is not null and usage_available = 0
      and cost_available is not null and cost_available = 0
      and input_tokens is null
      and output_tokens is null
      and total_tokens is null
      and cache_read_tokens is null
      and cache_write_tokens is null
      and estimated_cost is null
      and actual_cost is null
      and currency is null
      and cost_version is null
    )
  );

alter table public.sqag_telemetry_events
  validate constraint sqag_telemetry_events_attempt_semantics_ck;
