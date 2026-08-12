# Database migrations

Database changes are applied manually through the Supabase SQL editor. Run migration
files in numeric order and do not run them against production before reviewing their
preflight queries.

## Company alias enrichment

Before running `migrations/001_company_alias_enrichment.sql`, check whether the same
company has aliases that differ only by capitalization or surrounding spaces:

```sql
select
    company_id,
    lower(btrim(alias)) as normalized_alias,
    array_agg(alias_id order by alias_id) as alias_ids,
    array_agg(alias order by alias_id) as aliases
from public.company_aliases
group by company_id, lower(btrim(alias))
having count(*) > 1
order by company_id, normalized_alias;
```

If this returns rows, choose which alias record to retain before running the migration.
Do not delete a row until you have compared its type, active state, and any downstream
references. The migration will stop and roll back when these duplicates exist.

After the preflight returns no rows, run the complete contents of:

```text
database/migrations/001_company_alias_enrichment.sql
```

Verify the result:

```sql
select
    verification_status,
    generated_by,
    is_active,
    count(*)
from public.company_aliases
group by verification_status, generated_by, is_active
order by verification_status, generated_by, is_active;
```

Existing rows should appear as `VERIFIED`, `LEGACY`, and retain their previous
`is_active` value. Future AI suggestions must be inserted with all of the following:

```text
verification_status = PENDING
generated_by         = AI
is_active            = false
```

The database constraint prevents pending or rejected aliases from being activated.
The current relevance filter remains compatible because it only reads active aliases.

## Alias-enrichment run tracking

Before using the Stage 6A batch command, run the complete contents of:

```text
database/migrations/002_company_alias_enrichment_runs.sql
```

Verify the new table:

```sql
select to_regclass('public.company_alias_enrichment_runs');
```

The result should be `company_alias_enrichment_runs`. Successful runs prevent a
company from being researched again until the configured refresh interval elapses;
failed attempts remain eligible for a later run.
