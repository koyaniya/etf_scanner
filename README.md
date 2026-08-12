# UFO Space News Intelligence Agent

An automated research pipeline for monitoring news, companies, technologies, and
industry developments related to the **Procure Space ETF (UFO)**.

The goal of the project is not simply to summarize news, but to build a system that
can identify:

- which events are relevant to UFO holdings;
- which broader space-industry developments may affect the ETF;
- whether industry momentum is becoming more positive or negative;
- which themes are gaining importance over time;
- and eventually whether news signals have measurable relationships with ETF price
  movements.

## Table of Contents

- [Project Goal](#project-goal)
- [Current Architecture](#current-architecture)
- [Project Structure](#project-structure)
- [Current Features](#current-features)
- [Automation](#automation)
- [Roadmap](#roadmap)
- [Proposed Final Architecture](#proposed-final-architecture)
- [Technology Stack](#technology-stack)
- [Research Principles](#research-principles)

---

## Project Goal

The agent is designed to answer questions such as:

- What important developments occurred in the space industry today?
- Which UFO holdings were affected?
- Which technologies or themes are becoming more important?
- Is the overall direction of the sector improving or deteriorating?
- Which news events historically had the strongest relationship with UFO price
  movements?

The long-term goal is to build a continuously improving **space-sector intelligence
and forecasting system**.

---

## Current Architecture

```text
UFO Holdings Source
        │
        ▼
Holdings Collector
        │
        ▼
Holding Normalizer
        │
        ├── COMPANY ── continues to resolution
        ├── FUND
        ├── CASH
        ├── CURRENCY
        └── OTHER
        │
        ▼
Company Resolver
        │
        ├── Existing company found
        │       └── assign company_id
        │
        └── Unresolved company
                │
                ▼
        LLM + Web Verification
                │
                ├── verified non-company ──── correct holding_type
                ├── verified existing ─────── link company_id
                ├── verified new company ──── create company master record
                └── uncertain / conflicting ─ keep unresolved
        │
        ▼
Historical ETF Holdings
        │
        ▼
Company Master / Aliases
        │
        ├───────────────┐
        ▼               ▼
RSS Sources      Industry Topics
        │               │
        ▼               ▼
News Collection  Topic Keywords
        │               │
        └───────┬───────┘
                ▼
        Relevance Filter
                │
                ▼
        Article Analysis
                │
                ▼
        Event / Trend Data
                │
                ▼
     Daily / Weekly Reports
```

---

## Project Structure

```text
etf_scanner/
│
├── collectors/
│   ├── __init__.py
│   ├── holdings_collector.py
│   └── rss_collector.py
│
├── processing/
│   ├── __init__.py
│   ├── holdings_normalizer.py
│   ├── company_resolver.py
│   └── relevance_filter.py
│
├── agents/
│   ├── __init__.py
│   ├── company_enrichment_agent.py
│   └── company_alias_agent.py
│
├── scripts/
│   ├── enrich_company_aliases.py
│   ├── generate_deterministic_aliases.py
│   ├── review_company_aliases.py
│   ├── suggest_company_aliases.py
│   └── test_company_enrichment.py
│
├── .github/
│   └── workflows/
│       ├── update-ufo-holdings.yml
│       └── daily-rss-collector.yml
│
├── requirements.txt
├── .env                    # local secrets — git-ignored
├── .gitignore
└── README.md
```

> **Note:** `.env` holds Supabase credentials and is listed in `.gitignore`. It is
> never committed — CI reads the same values from GitHub Actions secrets.

### Planned Additions

| Directory   | Purpose                                      |
| ----------- | -------------------------------------------- |
| `analysis/` | LLM classification, event clustering, trends |
| `reports/`  | Daily and weekly report generation           |
| `database/` | Schema migrations and manual run instructions |
| `prompts/`  | Versioned LLM prompts                        |
| `tests/`    | Unit and integration tests                   |

---

## Current Features

### 1. UFO Holdings Collection

The system retrieves the latest UFO ETF holdings from the official Procure ETF
source. Holdings are stored as historical snapshots rather than overwritten, which
allows analysis of additions and removals, weight changes, portfolio composition over
time, and which companies were held when a given article was published.

The holdings workflow is executed automatically through GitHub Actions.

### 2. Holding Classification and Company Resolution

Raw ETF holdings do not always represent operating companies — a holdings file may
also contain cash, currencies, money-market funds, and other instruments. The
`holdings_normalizer` classifies every holding **before** company matching:

| Type       | Meaning                                                         |
| ---------- | --------------------------------------------------------------- |
| `COMPANY`  | Operating company / equity holding                              |
| `FUND`     | Mutual fund, money-market fund, or similar                      |
| `CASH`     | Cash or cash-equivalent position                                |
| `CURRENCY` | Foreign-currency position                                       |
| `OTHER`    | Cannot be classified confidently by deterministic rules         |

Only `COMPANY` holdings participate in company-master resolution. Non-company
holdings remain in `etf_holdings` — they are still valid parts of the historical
portfolio.

Each company holding is then linked to the `companies` master via `company_id`, using
a deterministic resolver that attempts ticker matching followed by canonical
company-name matching. This lets multiple historical security identifiers resolve to
the same underlying business — ETF source data may change a ticker or identifier
after an exchange convention change or corporate action while still referring to the
same company.

Companies carry multiple aliases (official name, short name, ticker, product, brand,
subsidiary, and manual entries), which the relevance filter later matches against
article text.

### 3. LLM-Based Company Enrichment

When a holding is classified `COMPANY` but cannot be matched to the existing master,
it is passed to an OpenAI-based enrichment agent that combines structured model output
with web verification. The agent treats `stock_ticker`, `cusip`, and `security_name`
as identifiers of a single security, and determines whether it is a company, fund,
ETF, bond, warrant, right, currency, cash position, other instrument, or unknown.

It returns `entity_type`, `confidence`, `canonical_name`, `verified`,
`identifier_conflict`, `corporate_action_detected`, `exchange`, `country_code`,
`website_url`, `verification_summary`, and `reason`.

A classification is **accepted automatically only when** all four hold:

```text
verified              = true
identifier_conflict   = false
confidence           >= configured threshold
entity_type          != UNKNOWN
```

**Identifier conflict protection.** A ticker match alone is not sufficient. If the
ticker points to Security A, the CUSIP to an unrelated Security B, and the name is
unverified, the agent returns `verified = false`, `identifier_conflict = true`,
`entity_type = UNKNOWN`, and no company is created. This stops unrelated securities
that share or reuse identifiers from contaminating the company master.

**Corporate action handling.** Not every mismatch is an error — reorganizations,
holding-company formations, ticker and CUSIP changes, renames, and mergers all
produce legitimate identifier drift. Where reliable public evidence shows identifiers
belong to the same company lineage, the agent returns `verified = true`,
`identifier_conflict = false`, `corporate_action_detected = true`, preserving the
historical source data while still linking to the correct master entity.

**Company master maintenance.** For a verified unresolved company, fields are
normalized and validated, the master is re-checked, and the holding is either linked
to an existing company or inserted as a new record (`canonical_name`,
`primary_ticker`, `exchange`, `country_code`, `website_url`, `is_active`) with a
database-generated `company_id`.

```text
unresolved holding
        ↓
LLM + web verification
        ↓
verified COMPANY
        ↓
normalize + validate candidate
        ↓
re-check company master
        │
        ├── exists → link company_id
        └── new    → create company → assign company_id
```

The agent runs **only** for unresolved holdings, keeping scheduled runs deterministic
and minimizing LLM usage.

### 4. AI-Assisted Company Alias Suggestions

The alias suggestion agent researches one existing company at a time using structured
OpenAI output and web search. Every suggestion includes an alias type, confidence,
evidence summary, and one or more supporting URLs. Suggestions are never activated
automatically by the research model. A separate deterministic validator rejects
malformed, generic, duplicated, and low-confidence suggestions. Products, brands,
subsidiaries, former names, alternative tickers, and ambiguous cross-company aliases
remain `PENDING` and inactive. Only a high-confidence short name that is derived from
the canonical name and supported by the company's own website can be automatically
marked `VERIFIED` and active.

Preview suggestions without writing to the database:

```bash
python -m scripts.suggest_company_aliases --company-id 123
```

After inspecting the validation decisions, save reviewable candidates and any narrowly
auto-approved short names:

```bash
python -m scripts.suggest_company_aliases --company-id 123 --save
```

Both commands make an OpenAI API request and may perform web searches. The model
defaults to `gpt-5-mini` and can be overridden with `OPENAI_ALIAS_MODEL`. Existing
aliases are compared case- and whitespace-insensitively and are not inserted again.

List aliases waiting for review:

```bash
python -m scripts.review_company_aliases list
```

Approve or reject a pending alias:

```bash
python -m scripts.review_company_aliases decide \
  --alias-id 456 --action approve --reviewed-by anna \
  --note "Confirmed on the official company website."

python -m scripts.review_company_aliases decide \
  --alias-id 456 --action reject --reviewed-by anna \
  --note "Generic product term."
```

The review command also supports `deactivate` for an active verified alias and
`reopen` for a rejected or deactivated alias that needs another review. Decisions use
an optimistic state check so one reviewer cannot silently overwrite another review
made at the same time. Each action records `reviewed_at`, `reviewed_by`, and an
appended audit note.

Run a bounded local enrichment batch in preview mode:

```bash
python -m scripts.enrich_company_aliases --limit 5
```

Preview mode performs OpenAI research but does not write aliases or run history. Once
the output has been inspected, enable writes explicitly:

```bash
python -m scripts.enrich_company_aliases --limit 5 --save
```

The save command requires migration
`database/migrations/002_company_alias_enrichment_runs.sql`. A successful run is
recorded even when no new alias is found. Successful companies become eligible again
after `ALIAS_RESEARCH_REFRESH_DAYS` (default: `180`), while failed companies remain
eligible for the next run. Use `--force` only when intentionally researching a
company before its refresh date.

Generate all missing deterministic aliases without using OpenAI:

```bash
python -m scripts.generate_deterministic_aliases --save
```

The holdings workflow also runs this deterministic step after company resolution.
AI alias enrichment remains a separate workflow so OpenAI or web-search failures do
not block holdings collection. The daily alias workflow runs at 06:00 Korea time,
generates deterministic aliases first, and then researches at most five eligible
companies. At five companies per day, an initial backlog of 64 companies takes about
13 daily runs.

### 5. Industry Topic Taxonomy

The project contains a topic taxonomy for major space-industry areas. Each topic can
contain multiple weighted keywords.

| Category               | Topics                                                                |
| ---------------------- | --------------------------------------------------------------------- |
| Launch                 | launch services, reusable launch systems, launch failures and delays   |
| Satellites             | satellite manufacturing, satellite communications, direct-to-device    |
| Data                   | Earth observation, space data and analytics                            |
| Government             | defense and national security, government contracts                    |
| Regulation             | space regulation, spectrum regulation                                  |
| Infrastructure         | propulsion, orbital infrastructure, lunar exploration                  |
| Corporate              | funding and IPOs, mergers and acquisitions                             |

### 6. RSS News Collection

The RSS collector reads active sources from Supabase and collects recent articles.
Current example sources include **NASA News** and **SpaceNews**.

The collector normalizes:

- title;
- URL;
- publication date;
- author;
- summary;
- source;
- content hash.

Articles are deduplicated and stored in Supabase. The RSS collector runs
automatically every day using GitHub Actions.

### 7. Rule-Based Relevance Filtering

New articles are evaluated using:

- company aliases;
- ETF holdings;
- industry-topic keywords;
- weighted keyword matching.

Articles are classified into one of four labels:

| Label               | Meaning                                              |
| ------------------- | ---------------------------------------------------- |
| `DIRECT_HOLDING`    | Article concerns a company currently held by UFO     |
| `INDUSTRY_RELEVANT` | Article concerns the broader space industry          |
| `WEAK_MATCH`        | Partial or ambiguous match, may need LLM review      |
| `IRRELEVANT`        | No meaningful relationship to UFO or the sector      |

The filter also stores:

- relevance score;
- company match score;
- topic match score;
- matched companies;
- matched aliases;
- matched topics;
- matched keywords;
- whether LLM review is required.

The scoring system is versioned so it can be improved and re-run later.

---

## Automation

### UFO Holdings

The holdings pipeline runs automatically through GitHub Actions and checks whether
enough time has passed since the latest stored snapshot.

```text
GitHub Actions
      ↓
UFO Holdings Collector
      ↓
Source Validation
      ↓
Holding Classification
      ↓
Company Resolver
      ↓
unresolved COMPANY?
      │
      ├── no ───────────────────────────┐
      │                                │
      └── yes                          │
            ↓                          │
       OpenAI + Web Verification       │
            ↓                          │
       Company Enrichment              │
            ↓                          │
       Company Master Update           │
            │                          │
            └──────────────┬───────────┘
                           ↓
                     Supabase
```

OpenAI is therefore used as a fallback rather than for every ETF holding.

### Daily News Collection

Runs daily.

```text
GitHub Actions
    ↓
RSS collector
    ↓
Article normalization
    ↓
Deduplication
    ↓
Supabase
```

---

## Roadmap

### Phase 1 — Data Collection, Entity Resolution and Relevance Engine

**Completed**

- [x] Create UFO holdings database
- [x] Automatically update UFO holdings
- [x] Preserve historical holding snapshots
- [x] Classify holdings by entity type
- [x] Separate company holdings from cash, currencies and funds
- [x] Link ETF holdings to company master with `company_id`
- [x] Build deterministic company resolver
- [x] Build LLM-based company enrichment fallback
- [x] Add web verification for unresolved holdings
- [x] Add identifier-conflict detection
- [x] Add corporate-action-aware company resolution
- [x] Automatically create verified new company records
- [x] Create company master
- [x] Create company aliases
- [x] Design alias-enrichment metadata and safety constraints
- [x] Build AI-assisted alias suggestion agent
- [x] Add deterministic alias validation and ambiguity controls
- [x] Add pending-alias review and lifecycle commands
- [x] Add bounded local alias-enrichment orchestration
- [x] Add separate daily alias-enrichment automation
- [x] Create industry topic taxonomy
- [x] Create weighted topic keywords
- [x] Create news source registry
- [x] Build RSS collector
- [x] Store collected articles in Supabase
- [x] Remove exact duplicate articles
- [x] Build first rule-based relevance filter
- [x] Automate holdings collection
- [x] Automate daily RSS collection

**Next**

- [ ] Connect relevance filtering to the daily GitHub Actions pipeline
- [ ] Manually evaluate relevance-filter precision and recall
- [x] Build deterministic alias normalization and generation
- [x] Build AI-assisted alias suggestions
- [x] Add alias validation and ambiguous ticker handling
- [x] Add a pending-alias review workflow
- [ ] Add event detection
- [ ] Add more trusted RSS and official sources
- [ ] Add SEC EDGAR collector
- [ ] Add broad search / news discovery collector
- [ ] Add article-body extraction

### Phase 2 — LLM Article Intelligence

LLM-based analysis is applied **only after** rule-based filtering.

For relevant articles, extract structured fields such as:

| Field                  | Description                               |
| ---------------------- | ----------------------------------------- |
| `event_type`           | Category of the underlying event          |
| `affected_companies`   | Companies referenced or impacted          |
| `affected_topics`      | Industry topics referenced                |
| `impact_direction`     | Positive / negative / neutral             |
| `impact_strength`      | Magnitude of expected impact              |
| `importance_score`     | Overall significance                      |
| `confidence_score`     | Model confidence in the extraction        |
| `time_horizon`         | Short / medium / long term                |
| `summary`              | Condensed factual summary                 |
| `industry_implication` | Interpretation at sector level            |
| `risk_factors`         | Identified risks                          |

**Planned tasks**

- [ ] Build LLM classifier
- [ ] Use structured JSON output
- [ ] Separate facts from interpretation
- [ ] Add prompt versioning
- [ ] Store LLM analysis separately from raw article data
- [ ] Track model and prompt versions

### Phase 3 — Event Detection and Deduplication

Multiple news articles may describe the same underlying event. For example:

> **Article 1:** Rocket Lab wins government contract
>
> **Article 2:** Space Force awards new launch contract to Rocket Lab
>
> **Article 3:** RKLB shares rise following contract announcement

These should eventually become one event.

**Planned tasks**

- [ ] Semantic duplicate detection
- [ ] Event clustering
- [ ] Event fingerprints
- [ ] Identify updates to existing events
- [ ] Distinguish confirmation from genuinely new information
- [ ] Track event history

### Phase 4 — Automatic Keyword and Topic Discovery

The initial topic and keyword system is manually curated. A future component should
automatically detect emerging terminology.

Possible workflow:

```text
Recent relevant articles
        ↓
Extract frequently appearing phrases
        ↓
Compare with known topic keywords
        ↓
Identify new candidate keywords
        ↓
Score usefulness / frequency
        ↓
Human or LLM validation
        ↓
Add approved keywords
```

Examples of possible discoveries:

- new launch vehicle name;
- new satellite architecture;
- new regulatory terminology;
- new company product;
- new defense program;
- new space-industry acronym.

The system may eventually create temporary monitoring queries for rapidly developing
topics.

**Planned tasks**

- [ ] Candidate keyword extraction
- [ ] Keyword frequency analysis
- [ ] Phrase embeddings / semantic similarity
- [ ] Detect emerging topics
- [ ] Automatically suggest new aliases
- [ ] Automatically suggest new monitoring queries
- [ ] Add human approval before permanent insertion

### Phase 5 — Daily and Weekly Intelligence Reports

**Daily report**

- Top developments
- Direct UFO holding news
- Industry-level developments
- Government / regulation signals
- Positive signals
- Negative signals
- Important events to watch

**Weekly report**

- Overall industry direction
- Topic momentum
- Most affected UFO holdings
- Largest new risks
- Funding activity
- Government contract activity
- Technology trends
- Changes versus previous week

**Planned tasks**

- [ ] Daily Markdown report
- [ ] Weekly sector report
- [ ] Company-specific report
- [ ] Topic-specific report
- [ ] Slack or email delivery

### Phase 6 — Market Data Integration

Connect news intelligence to financial-market data.

Potential data:

- UFO daily price;
- returns;
- trading volume;
- volatility;
- individual holding prices;
- interest rates;
- market indices;
- sector ETFs.

This allows research into questions such as:

- Do high-impact news events precede abnormal UFO returns?
- Which topics have historically had the strongest market impact?
- Are negative launch events more important than positive contract announcements?

### Phase 7 — Time-Series Forecasting

Once sufficient historical data exists, build forecasting models using both market
data and news-derived features.

**News-derived features**

- `daily_relevance_score`
- `positive_event_count`
- `negative_event_count`
- `weighted_news_impact`
- `topic_momentum`
- `company_weighted_news_score`
- `government_contract_score`
- `regulatory_risk_score`
- `news_volume`
- `novelty_score`

**Market features**

- UFO returns
- Volume
- Volatility
- Moving averages
- Holding-level returns
- Interest rates
- NASDAQ performance

**Potential model families**

- linear / regularized regression;
- ARIMAX / dynamic regression;
- gradient boosting;
- XGBoost / LightGBM;
- temporal convolution models;
- LSTM / GRU;
- Transformers for time series.

**Initial goal:** estimate whether news-derived information improves forecasting
relative to a market-data-only baseline.

> **Important:** forecasting must be evaluated with proper walk-forward validation to
> avoid look-ahead bias.

### Phase 8 — Adaptive News Scoring

The current relevance scoring system is manually defined:

```text
Rocket Lab company match  +50
launch contract           +20
Space Force               +20
```

Eventually the system should learn which signals historically mattered more. For
example, historical data might show:

| Signal                | Observed relationship with returns |
| --------------------- | ---------------------------------- |
| Government contract   | Strong                             |
| Launch delay          | Strong negative                    |
| Executive interview   | Little measurable relationship     |
| Generic NASA news     | Almost none                        |

The system could then adjust feature weights based on observed outcomes. A safer
first approach is:

- supervised learning;
- online learning;
- Bayesian updating;
- contextual bandit approaches.

This should be implemented before attempting full reinforcement learning.

### Phase 9 — Reinforcement Learning Research

A long-term experimental direction is to treat the news-scoring policy as an
adaptive system.

```text
Article / Event
      ↓
Agent assigns importance score
      ↓
Future market reaction observed
      ↓
Reward signal
      ↓
Scoring policy updated
```

Possible reward signals:

- future abnormal ETF return;
- future volatility;
- trading-volume change;
- holding-level price reaction;
- directional correctness.

However, price movement cannot automatically be interpreted as proof that an article
caused the movement. The system would need to account for:

- overall market movement;
- NASDAQ movement;
- macroeconomic news;
- interest rates;
- overlapping events;
- earnings;
- ETF holding weights;
- different event time horizons.

Reinforcement learning is therefore a research-stage feature, not part of the initial
production pipeline. Before RL, the project should establish reliable event labels
and market-impact datasets.

---

## Proposed Final Architecture

```text
                    ┌─────────────────────┐
                    │  UFO Holdings       │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Companies / Aliases │
                    └──────────┬──────────┘
                               │
       ┌───────────────────────┼───────────────────────┐
       │                       │                       │
       ▼                       ▼                       ▼
 RSS / APIs              Web Search              SEC / Official
       │                       │                       │
       └───────────────────────┼───────────────────────┘
                               ▼
                       Article Database
                               │
                               ▼
                      Relevance Filtering
                               │
                               ▼
                       LLM Classification
                               │
                               ▼
                        Event Clustering
                               │
                   ┌───────────┴───────────┐
                   ▼                       ▼
            Trend Analytics          Market Data
                   │                       │
                   └───────────┬───────────┘
                               ▼
                      Time-Series Dataset
                               │
                       ┌───────┴────────┐
                       ▼                ▼
                  Forecasting     Adaptive Scoring
                                        │
                                        ▼
                              RL / Online Learning
```

---

## Technology Stack

| Status      | Tools                                                                                                                  |
| ----------- | ---------------------------------------------------------------------------------------------------------------------- |
| **Current** | Python · pandas · Supabase / PostgreSQL · GitHub Actions · OpenAI API · Responses API · Structured Outputs · Web Search · Pydantic · RSS / Atom · feedparser · requests · BeautifulSoup         |
| **Planned** | Claude API or OpenAI API · embeddings · SEC APIs · search APIs · scikit-learn · XGBoost / LightGBM · statsmodels · PyTorch · time-series forecasting libraries |

---

## Research Principles

This system should always distinguish between:

1. **Relevance** — is the article about UFO or the space sector?
2. **Importance** — does the event matter?
3. **Sentiment** — is the tone positive or negative?
4. **Market impact** — did prices actually react?
5. **Prediction** — can future movement be estimated?
6. **Causality** — did the event cause the movement?
