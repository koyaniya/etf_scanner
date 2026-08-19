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
`is_active` value. AI suggestions that pass deterministic validation are inserted
with all of the following:

```text
verification_status = VERIFIED
generated_by         = AI
is_active            = true
```

Hard-rejected AI suggestions are not inserted. The database constraint prevents
pending or rejected aliases from being activated. The current relevance filter
remains compatible because it only reads active aliases.

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

## Article Analyzer storage

Before implementing or running the Article Analyzer, run the complete contents of:

```text
database/migrations/003_article_analyses.sql
```

This creates:

- `article_analyses` for one versioned analysis per article;
- `article_analysis_companies` for validated company relationships;
- `article_analysis_topics` for validated topic relationships.

The schema does not create the canonical `events` table yet. Several articles may
describe one underlying event, so event clustering will be implemented after article
analysis is reliable.

Verify the tables:

```sql
select
    to_regclass('public.article_analyses') as article_analyses,
    to_regclass('public.article_analysis_companies')
        as article_analysis_companies,
    to_regclass('public.article_analysis_topics')
        as article_analysis_topics;
```

All three result columns should contain their table names.

The analyzer selects only articles whose latest relevance class is
`DIRECT_HOLDING`, `INDUSTRY_RELEVANT`, or `WEAK_MATCH`. Preview one eligible article:

```bash
python3 -m scripts.analyze_article --article-id 123
```

Set `OPENAI_ARTICLE_ANALYZER_MODEL` to override the default `gpt-5-mini`, or pass
`--model` for one preview. Stage 3 is preview-only: it calls the model and prints the
structured analysis plus resolved company/topic IDs, but does not write analysis rows.

Run a bounded Stage 4 preview batch:

```bash
python3 -m scripts.analyze_articles --limit 5
```

After checking the output, persist the analyses and their resolved relationships:

```bash
python3 -m scripts.analyze_articles --limit 5 --save
```

Only a `SUCCESS` row for the current `analysis_version` prevents repeat processing.
A failed article remains eligible for retry. To process one specific eligible article,
add `--article-id 123`. The command never selects `IRRELEVANT` articles.

## Automated Article Analyzer

The daily RSS workflow runs these operations in order:

```text
RSS collection -> relevance filter -> Article Analyzer
```

Configure the following GitHub repository settings before enabling the analyzer:

- secret `OPENAI_API_KEY`;
- existing secrets `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`;
- optional variable `OPENAI_ARTICLE_ANALYZER_MODEL` (default `gpt-5-mini`);
- optional variable `ARTICLE_ANALYSIS_DAILY_LIMIT` (default `10`).

The workflow's manual **Run workflow** form accepts an `analysis_limit` override.
Scheduled runs use the repository variable or the default. The analyzer finishes the
bounded batch even if one article fails, prints the complete summary, and then marks
the GitHub Actions step failed so the problem is visible in the workflow logs.

Verify saved analyses and normalized relationships at any time:

```sql
select analysis_id, article_id, status, analysis_version, model, prompt_version
from public.article_analyses
order by analysis_id desc
limit 20;

select * from public.article_analysis_companies
order by analysis_id desc;

select * from public.article_analysis_topics
order by analysis_id desc;
```

## Canonical event storage

Before implementing the event candidate and clustering logic, run the complete
contents of:

```text
database/migrations/004_canonical_events.sql
```

This creates:

- `events` for one canonical real-world occurrence;
- `event_articles` for its supporting article analyses;
- `event_companies` and `event_topics` for normalized relationships;
- `event_clustering_runs` for versioned decisions, failures, and repeat prevention.

An `article_analysis` can belong to only one canonical event. Multiple analyses can
support the same event. Uncertain articles can safely become separate events and be
merged later using the `MERGED` status and `merged_into_event_id`.

Verify the tables after applying the migration:

```sql
select
    to_regclass('public.events') as events,
    to_regclass('public.event_articles') as event_articles,
    to_regclass('public.event_companies') as event_companies,
    to_regclass('public.event_topics') as event_topics,
    to_regclass('public.event_clustering_runs') as event_clustering_runs;
```

All five result columns should contain their corresponding table names. Stage 1 only
adds storage; no current command writes canonical events yet.

## Canonical event candidate preview

Stage 2 can rank existing active event candidates for one successful article analysis:

```bash
python3 -m scripts.find_event_candidates --analysis-id 1
```

The default candidate window is 30 days and can be configured with
`EVENT_CANDIDATE_WINDOW_DAYS` or overridden using `--window-days`. Candidate scoring
uses matching event type, affected-company overlap, topic overlap, and event/publication
date proximity. This command is read-only: it makes no LLM call and saves nothing.

## Deterministic event clustering

Before saving Stage 3 decisions, run:

```text
database/migrations/005_event_clustering_ambiguous.sql
```

This allows an ambiguous deterministic result to be recorded without incorrectly
linking it to an event. Preview a bounded batch:

```bash
python3 -m scripts.cluster_article_events --limit 10
```

After reviewing it, save deterministic decisions:

```bash
python3 -m scripts.cluster_article_events --limit 10 --save
```

No candidate or a clearly weak candidate creates a new canonical event. Only a strong,
company-backed candidate with a safe margin over the runner-up is linked automatically.
Intermediate cases are saved as `AMBIGUOUS` for the later LLM comparison stage. Use
`--analysis-id 1` to preview or process one analysis.

## LLM resolution for ambiguous event clusters

Stage 4 sends only ambiguous deterministic cases to a structured LLM comparison. It
compares the saved article analysis with the shortlisted canonical events and decides
`MATCHED` or `NEW_EVENT`:

```bash
python3 -m scripts.resolve_ambiguous_events --limit 5
python3 -m scripts.resolve_ambiguous_events --limit 5 --save
```

The command makes no web searches and does not call OpenAI when the ambiguous queue is
empty. Set `OPENAI_EVENT_CLUSTER_MODEL` to override the default `gpt-5-mini`. Failed
comparisons retain `decision = AMBIGUOUS` so they remain eligible for retry.

## Canonical event consolidation

Before saving Stage 5 event revisions, run:

```text
database/migrations/006_event_updates.sql
```

The migration creates `event_updates`, which stores the complete before, proposed, and
applied state for each evidence set. Preview or save pending multi-article events:

```sql
select to_regclass('public.event_updates') as event_updates;
```

The result should be `event_updates`. Then preview or save pending multi-article events:

```bash
python3 -m scripts.update_canonical_events --limit 5
python3 -m scripts.update_canonical_events --limit 5 --save
```

Only events with at least two linked analyses are eligible. A stable signature of all
linked analysis IDs prevents repeat processing until another article is attached.
`NO_CHANGE` decisions are also audited. Set `OPENAI_EVENT_UPDATE_MODEL` to override the
default `gpt-5-mini`.

## Automated canonical-event pipeline

The daily RSS workflow now runs the complete sequence:

```text
RSS collection
  -> relevance filtering
  -> article analysis
  -> deterministic event clustering
  -> ambiguous LLM resolution
  -> canonical event consolidation
```

Optional GitHub repository variables and defaults:

- `EVENT_CLUSTER_DAILY_LIMIT=10`;
- `EVENT_AMBIGUOUS_DAILY_LIMIT=5`;
- `EVENT_UPDATE_DAILY_LIMIT=5`;
- `OPENAI_EVENT_CLUSTER_MODEL=gpt-5-mini`;
- `OPENAI_EVENT_UPDATE_MODEL=gpt-5-mini`.

Manual workflow runs expose overrides for all three limits. Downstream stages continue
after an isolated failure so already-successful work is not stranded; a final workflow
step still reports the overall run as failed and leaves detailed logs for every stage.
