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
├── cv/                     # the user's real YAAC LaTeX CV (source of truth for 4b)
├── db/schema.sql           # 7 tables (jobs, companies, contacts, applications, drafts, outreach, run_log)
├── lib/
│   ├── db.py               # connection + query helpers (FK enforcement, WAL)
│   ├── dedup.py            # two-part dedup: hard key (source:id) + soft fingerprint
│   ├── ranker.py           # Stage 2 scoring (Haiku, per-track rubric, structured output)
│   ├── drafter.py          # Stage 4 CV/cover-letter drafting (Opus, no-invention rule)
│   ├── latex_cv.py         # Stage 4b LaTeX CV tailoring (headline/tagline only)
│   └── sources/            # one adapter module per source (france_travail.py)
└── stages/
    ├── 01_source.py        # Stage 1 — sourcing & dedup            (autonomous)
    ├── 02_rank.py          # Stage 2 — LLM ranking, per-track       (autonomous)
    ├── 03_enrich.py        # Stage 3 — company enrichment           (autonomous)
    ├── 04_draft.py         # Stage 4 — CV + cover-letter drafts     (GATED, needs_review)
    ├── 04b_latex_cv.py     # Stage 4b — tailored LaTeX CV per role  (GATED)
    └── 05a_track.py        # Stage 5a — application tracking        (human-driven)
```

Generated artifacts (gitignored, live in `runs/`):
- `runs/drafts/*.md` — cover letters + Markdown CV drafts (readable)
- `runs/cv/<role>/` — tailored LaTeX CV projects (compile `cv.tex` → PDF)

## Setup

```bash
# 1. Create a venv and install deps
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2. Initialize the database
.venv\Scripts\python lib/db.py

# 3. Add API credentials (France Travail — register at https://francetravail.io/inscription)
copy .env.example .env        # then fill in the credentials

# 4. Run the pipeline, stage by stage
.venv\Scripts\python stages\01_source.py             # source new jobs
.venv\Scripts\python stages\02_rank.py               # rank against the profile
.venv\Scripts\python stages\03_enrich.py             # enrich pursue companies
.venv\Scripts\python stages\04_draft.py              # draft CV + cover letter (gated)
.venv\Scripts\python stages\04_draft.py --export     # export drafts to runs/drafts/
.venv\Scripts\python stages\04b_latex_cv.py          # tailored LaTeX CVs to runs/cv/

# Track applications (Stage 5a)
.venv\Scripts\python stages\05a_track.py --applied 3 --date 2026-06-22
.venv\Scripts\python stages\05a_track.py --advance 3 --stage screening --follow-up 2026-06-30
.venv\Scripts\python stages\05a_track.py --show      # your job-hunt dashboard
```

## Status

Built and verified end-to-end against live data:

| Stage | What | Mode |
|------|------|------|
| 1 — Sourcing | France Travail API, two-part dedup, graceful degradation | autonomous |
| 2 — Ranking | LLM scoring (Haiku), per-track rubric, structured output | autonomous |
| 3 — Enrichment | company research for the pursue set | autonomous |
| 4 — Drafts | CV + cover letter (Opus), strict no-invention rule | **gated** (needs_review) |
| 4b — LaTeX CV | tailored copies of the real `cv/` template, per role | **gated** |
| 5a — Tracking | application stage tracker (the DB-backed dashboard) | human-driven |

Stage 5b (outreach drafting) is intentionally deferred until there are real
contacts and application state to trigger it. Scheduling (Phase 2) and the
review cockpit remain future work.

## Build order

Schema → profile/config → Stage 1 (sourcing) → 2 (ranking) → 3 (enrichment) →
4 (drafts, gated) → 5b (outreach, gated) → then scheduling and the review
cockpit. Each stage is proven against real data before the next is started.
