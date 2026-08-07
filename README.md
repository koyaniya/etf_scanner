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
UFO Holdings
     │
     ▼
Company Master / Aliases
     │
     ├───────────────┐
     │               │
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
│   └── relevance_filter.py
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
| `database/` | Schema definitions and migrations            |
| `prompts/`  | Versioned LLM prompts                        |
| `tests/`    | Unit and integration tests                   |

---

## Current Features

### 1. UFO Holdings Collection

The system retrieves the latest UFO ETF holdings from the official Procure ETF
source. Holdings are stored as historical snapshots rather than overwritten.

This allows future analysis of:

- additions and removals;
- changes in holding weights;
- portfolio composition over time;
- which companies were held at the time an article was published.

The holdings workflow is executed automatically through GitHub Actions.

### 2. Company Master and Aliases

ETF holdings are connected to a company master table. Each company may have multiple
aliases, including:

- official company name;
- shortened company name;
- ticker;
- product name;
- brand;
- subsidiary;
- manually defined aliases.

### 3. Industry Topic Taxonomy

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

### 4. RSS News Collection

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

### 5. Rule-Based Relevance Filtering

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

Runs automatically and checks whether a new snapshot should be collected.

```text
GitHub Actions
    ↓
UFO holdings collector
    ↓
Validation
    ↓
Supabase
```

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

### Phase 1 — Data Collection and Relevance Engine

**Completed**

- [x] Create UFO holdings database
- [x] Automatically update UFO holdings
- [x] Preserve historical holding snapshots
- [x] Create company master
- [x] Create company aliases
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
- [ ] Improve alias matching and ambiguous ticker handling
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
| **Current** | Python · pandas · Supabase / PostgreSQL · GitHub Actions · RSS / Atom · feedparser · requests · BeautifulSoup            |
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
