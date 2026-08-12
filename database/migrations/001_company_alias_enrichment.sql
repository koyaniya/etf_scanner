-- Stage 1: prepare company_aliases for safe automated enrichment.
--
-- Run this migration manually in the Supabase SQL editor. It is intentionally
-- backward-compatible with the existing company_sync and relevance_filter code.
-- Existing aliases remain active and are classified as verified legacy data.

begin;

alter table public.company_aliases
    add column if not exists confidence numeric(4, 3),
    add column if not exists verification_status text,
    add column if not exists source_urls text[],
    add column if not exists generated_by text,
    add column if not exists notes text,
    add column if not exists reviewed_at timestamp with time zone,
    add column if not exists reviewed_by text;

-- Backfill existing rows before applying NOT NULL constraints. Future AI-created
-- rows will explicitly use PENDING, is_active = false, and generated_by = 'AI'.
update public.company_aliases
set
    verification_status = coalesce(verification_status, 'VERIFIED'),
    source_urls = coalesce(source_urls, '{}'::text[]),
    generated_by = coalesce(generated_by, 'LEGACY')
where
    verification_status is null
    or source_urls is null
    or generated_by is null;

alter table public.company_aliases
    alter column verification_status set default 'VERIFIED',
    alter column verification_status set not null,
    alter column source_urls set default '{}'::text[],
    alter column source_urls set not null,
    alter column generated_by set default 'MANUAL',
    alter column generated_by set not null;

alter table public.company_aliases
    drop constraint if exists company_aliases_confidence_check,
    add constraint company_aliases_confidence_check
        check (confidence is null or confidence between 0 and 1),
    drop constraint if exists company_aliases_verification_status_check,
    add constraint company_aliases_verification_status_check
        check (verification_status in ('PENDING', 'VERIFIED', 'REJECTED')),
    drop constraint if exists company_aliases_generated_by_check,
    add constraint company_aliases_generated_by_check
        check (generated_by in ('LEGACY', 'MANUAL', 'DETERMINISTIC', 'AI')),
    drop constraint if exists company_aliases_nonempty_check,
    add constraint company_aliases_nonempty_check
        check (btrim(alias) <> ''),
    drop constraint if exists company_aliases_active_verified_check,
    add constraint company_aliases_active_verified_check
        check (not is_active or verification_status = 'VERIFIED');

-- Stop before index creation if case/whitespace variants already exist for the
-- same company. The transaction will roll back and the diagnostic query in
-- database/README.md can be used to inspect the conflicts.
do $$
begin
    if exists (
        select 1
        from public.company_aliases
        group by company_id, lower(btrim(alias))
        having count(*) > 1
    ) then
        raise exception using
            message = 'Duplicate company aliases exist after case/space normalization.',
            hint = 'Run the duplicate diagnostic in database/README.md and resolve those rows first.';
    end if;
end
$$;

-- Keep company_aliases_unique because the current Supabase upsert names its
-- columns via on_conflict="company_id,alias". This additional index catches
-- variants such as "Rocket Lab" and " rocket lab ".
create unique index if not exists company_aliases_normalized_unique
    on public.company_aliases (company_id, lower(btrim(alias)));

create index if not exists company_aliases_review_queue_idx
    on public.company_aliases (verification_status, created_at)
    where verification_status = 'PENDING';

comment on column public.company_aliases.confidence is
    'Generator confidence from 0 to 1; null for aliases without a model score.';
comment on column public.company_aliases.verification_status is
    'Review state. Only VERIFIED aliases may be active and used for matching.';
comment on column public.company_aliases.source_urls is
    'Public evidence URLs supporting the alias-to-company relationship.';
comment on column public.company_aliases.generated_by is
    'Alias origin: LEGACY, MANUAL, DETERMINISTIC, or AI.';
comment on column public.company_aliases.notes is
    'Short verification explanation or reviewer note.';

commit;
