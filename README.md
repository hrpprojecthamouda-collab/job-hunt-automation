# Job-Hunt Automation Pipeline

A personal, semi-autonomous pipeline that automates the *grunt work* of a job
hunt — sourcing roles, scoring them against a profile, enriching company data,
and drafting tailored applications — while keeping a human approval gate on
anything that commits identity.

It is also, deliberately, a **portfolio project**: it exercises agent
orchestration, relational schema design, source integration, and a
RAG-adjacent scoring step, with engineering quality treated as part of the
deliverable.

## Core design principle

> **Agents do retrieval, drafting, and state management autonomously. Anything
> that sends a message, submits a form, or commits identity passes through an
> explicit human approval gate.**

- Stages 1–3 (source → rank → enrich) run autonomously against the database.
- Stages 4 & 5b (CV/cover-letter drafts, outreach) only ever produce
  `needs_review` / `needs_approval` candidates — never auto-sent.
- The pipeline **never** automates past a login wall, CAPTCHA, or final-submit
  button. Gated sources are best-effort and degrade gracefully.

## Architecture

A single SQLite file (`db/pipeline.db`) is the one authoritative store; every
stage reads from and/or writes to it. Nothing holds state anywhere else.

```
[1] Sourcing → [2] Ranking → [3] Enrichment → [4] Draft gen → [5b] Outreach gen
                              │ reads / writes
                        ┌─────▼─────┐
                        │ SQLite .db │   ← the one authoritative store
                        └───────────┘
```

## Targeting

Roles are scored on **two tracks** (the `jobs.track` column):

- **core** — integration & validation / embedded systems (land-today, on proven
  experience).
- **bridge** — AI-adjacent stretch roles in or near that domain.

The master profile (`profile.json`) splits skills into **proven** (defensible
today) vs **developing** (evidenced learning, not expertise). Drafts may only
claim expertise from the proven set.

## Layout

```
├── profile.json            # master profile — the keystone input (stages 1,2,4 read it)
├── config.yaml             # search criteria, sources, ranking thresholds
├── db/schema.sql           # 7 tables (jobs, companies, contacts, applications, drafts, outreach, run_log)
├── lib/
│   ├── db.py               # connection + query helpers (FK enforcement, WAL)
│   ├── dedup.py            # two-part dedup: hard key (source:id) + soft fingerprint
│   └── sources/            # one adapter module per source
└── stages/
    └── 01_source.py        # Stage 1 — sourcing & dedup (more stages added incrementally)
```

## Setup

```bash
# 1. Create a venv and install deps
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2. Initialize the database
.venv\Scripts\python lib/db.py

# 3. Add API credentials (France Travail — register at https://francetravail.io/inscription)
copy .env.example .env        # then fill in the credentials

# 4. Run Stage 1
.venv\Scripts\python stages\01_source.py --dry-run   # fetch + report, no writes
.venv\Scripts\python stages\01_source.py             # fetch + persist
```

## Status

Built and verified: schema + DB helpers, master profile template, config, and
Stage 1 (sourcing) with the France Travail adapter, two-part dedup, and
graceful per-source degradation. Stages 2–5 are in progress, built in order.

## Build order

Schema → profile/config → Stage 1 (sourcing) → 2 (ranking) → 3 (enrichment) →
4 (drafts, gated) → 5b (outreach, gated) → then scheduling and the review
cockpit. Each stage is proven against real data before the next is started.
