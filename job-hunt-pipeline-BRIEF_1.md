# Job-Hunt Automation Pipeline — Project Brief

> **Handoff document for Claude Code.** This is the design spec and starting context for building a personal, semi-autonomous job-hunt pipeline. Read it top to bottom before writing any code. Build incrementally, stage by stage, in the order given. Do **not** scaffold all five stages at once.

---

## 1. What this is

A personal pipeline that automates the *grunt work* of a job hunt — finding roles, scoring them, researching companies, drafting applications, and tracking state — while keeping a human (me) at the controls for every decision that carries my identity or judgment.

**Target roles are two-tiered, Paris-based** (corrected 2026-06-22 — an earlier draft overstated my readiness):

- **Core track — land today.** Integration & validation / embedded systems engineering. This is my real, demonstrable experience (~7 yrs, automotive, mostly RENAULT) and the anchor of the search.
- **Bridge track — realistic stretch.** AI-adjacent roles in or near my domain (ML/data engineering for testing, AI-augmented test automation, tooling/platform). I'm **at the start of an AI-engineering pivot, not finished with it**: actively learning AI foundations/fluency, with one AI-assisted Flutter/Supabase app and this pipeline as concrete — but early — evidence. I am **not** positioned to land a mid/senior pure-AI-engineering role today, and the pipeline must not pretend otherwise.

That context matters for two reasons:

1. It shapes what "a good match" means (stages 2 and 4 read this). Each job is scored on its track: core roles against proven experience, bridge roles against a stretch rubric — never blurred together. The profile splits skills into **proven** (defensible in an interview today) vs **developing** (evidenced learning, not expertise); drafts may only claim expertise from the proven set.
2. **This pipeline is itself a portfolio project.** It deliberately exercises agent orchestration, MCP integration, relational schema design, and a RAG-adjacent scoring step — exactly the competencies the bridge track wants, and the strongest single piece of evidence for the pivot. So engineering quality is not incidental; clean schema, clear separation of concerns, and a sensible repo structure are part of the deliverable.

---

## 2. The core design principle (read this twice)

**Agents do retrieval, drafting, and state management autonomously. Anything that sends a message, submits a form, or commits my identity passes through an explicit human approval gate.**

This is not a limitation to engineer away later. It is the correct boundary. Concretely:

- Stages 1–3 (sourcing, ranking, enrichment) run fully autonomously. They read and write the database without asking.
- Stages 4 and 5b (tailoring drafts, outreach drafts) produce **candidates flagged for review** — never finished actions. I edit, approve, and send.
- **Never** automate past a login wall, a CAPTCHA, or a final-submit button. Those are hard stops by design. Sites engineer against automation there, and those steps carry my real identity and work-authorization answers. Architect so the pipeline *degrades gracefully* when a gated source fails, rather than breaking or trying to force past the gate.

---

## 3. Architecture at a glance

A single SQLite database file is the **one authoritative store** for all pipeline state. Every stage reads from it and/or writes to it. Nothing holds state anywhere else. (This mirrors a backend principle I build by: state lives in one authoritative store, never split across clients.)

Two execution surfaces sit on top of that shared DB:

- **The engine — Claude Code (this surface).** Owns the code, the scripts, the `.db` file, and ideally a scheduled run. Runs stages 1–3 autonomously and *generates* the drafts for stages 4 and 5b.
- **The cockpit — a Claude Project / Claude Cowork layer (built later, not here).** A human-facing review surface that reads the same DB and writes back my decisions. Out of scope for this repo except that the schema must support it (clear status flags, reviewable draft rows).

The two meet only at the database. Code writes state; I review and write decisions back; the next engine run sees my decisions as new inputs.

```
  ┌─────────────────────────── Claude Code (engine) ───────────────────────────┐
  │  [1] Sourcing → [2] Ranking → [3] Enrichment → [4] Draft gen → [5b] Outreach gen │
  └──────────────────────────────────┬──────────────────────────────────────────┘
                                      │  reads / writes
                              ┌───────▼────────┐
                              │  SQLite  .db    │   ← the one authoritative store
                              └───────▲────────┘
                                      │  reads state / writes my decisions
  ┌───────────────────────────────────┴─────────────────────────────────────────┐
  │           Cockpit (Project / Cowork) — review, edit, approve, track            │
  └────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. The pipeline, stage by stage

Each stage below lists its **input**, **output**, whether it's **autonomous or gated**, and where it runs. The output of one stage is generally the input of the next — that's what makes this a clean pipeline.

### Stage 1 — Sourcing & aggregation  *(Claude Code · autonomous)*
- **Input:** run config (target titles, locations, keywords); list of sources; existing job keys from the DB (for dedup).
- **Does:** fetches postings from each source (API/RSS where available — cleanest; browser automation only where necessary). Parses messy HTML into structured fields. Deduplicates on a stable key. Inserts only genuinely new rows. Logs the run.
- **Output:** new rows in `jobs` with `status = 'new'` and a `date_scraped` timestamp.
- **Source reality:** treat API/RSS and public career pages as reliable. Treat login-gated sources (LinkedIn especially) as **best-effort** — do not architect as if they run unattended. If a source fails, log it and continue.

### Stage 2 — Filtering & ranking  *(Claude Code · autonomous)*
- **Input:** `new` jobs + my master profile (skills, must-haves, deal-breakers).
- **Does:** an LLM scoring call against a rubric derived from the profile. Produces a fit score and a short rationale per job.
- **Output:** each job updated with `fit_score` (0–100), `rationale`, and `status → 'pursue' | 'maybe' | 'skip'`.

### Stage 3 — Company enrichment  *(Claude Code · autonomous)*
- **Input:** the `pursue` set (company names + URLs).
- **Does:** web search / fetch for funding stage, size, tech stack, recent news, and any identifiable hiring manager / team members.
- **Output:** `companies` rows (FK-linked to jobs) holding the enrichment.

### Stage 4 — Tailoring drafts  *(Code generates → I review · GATED)*
- **Input:** master profile + specific JD + company enrichment.
- **Does:** generates a CV variant draft and a cover-letter draft per `pursue` role.
- **Output:** `drafts` rows flagged `status = 'needs_review'`. **Never auto-sent.** I edit and approve in the cockpit.

### Stage 5a — Pipeline tracking  *(Cockpit · human-driven)*
- **Input:** events from all stages + my manual stage updates.
- **Does:** surfaces a single source of truth — role, stage, date applied, contact, next action, follow-up due.
- **Output:** `applications` rows with a stage enum; my updates written back to the DB.

### Stage 5b — Follow-up & outreach drafting  *(Code drafts → I send · GATED)*
- **Input:** pipeline state (e.g. "applied 7 days ago, no reply") + contacts from enrichment.
- **Does:** drafts follow-up or cold-outreach messages when the state calls for one.
- **Output:** `outreach` rows flagged `status = 'needs_approval'`. I review and send.

---

## 5. Data model (the keystone — build this first)

A SQLite file. Six tables. The relationships `jobs → companies → applications → contacts` plus `drafts` and `outreach` are the spine every stage hangs off. Treat the schema as the first deliverable; nothing else works without it.

Suggested tables (refine as you build — these are the starting shape, not gospel):

- **`jobs`** — `id`, `dedup_key` (unique), `title`, `company_name`, `location`, `url`, `jd_text`, `salary`, `date_posted`, `date_scraped`, `fit_score`, `rationale`, `status` (`new`/`pursue`/`maybe`/`skip`/`applied`/`closed`), `company_id` (FK, nullable until enriched).
- **`companies`** — `id`, `name`, `funding_stage`, `size`, `tech_stack`, `recent_news`, `notes`.
- **`contacts`** — `id`, `company_id` (FK), `name`, `role`, `linkedin_url`, `email`, `notes`.
- **`applications`** — `id`, `job_id` (FK), `stage` (enum), `date_applied`, `next_action`, `follow_up_due`, `notes`.
- **`drafts`** — `id`, `job_id` (FK), `type` (`cv`/`cover_letter`), `content`, `status` (`needs_review`/`approved`/`sent`), `created_at`.
- **`outreach`** — `id`, `contact_id` (FK), `job_id` (FK), `content`, `status` (`needs_approval`/`approved`/`sent`), `created_at`.

**The dedup key** is the one detail to get right early: a normalized `company + title + location`, or a hash of the canonical URL. It's what stops the same repost appearing five times across runs.

---

## 6. The master profile (second deliverable)

A structured file (`profile.json` or `profile.md`) holding skills, must-haves, deal-breakers, and CV/cover-letter source material. It is the **keystone input** — stages 1 (criteria), 2 (ranking rubric), and 4 (tailoring) all read from it. Get it structured before building the stages that depend on it. Scaffold a template; I'll fill in the real content.

---

## 7. On-demand first, scheduled later

Claude does not run in the background between sessions. So:

- **Phase 1:** build each stage as a script I can run **on demand** in a Claude Code session ("source new jobs now"). Prove it works.
- **Phase 2:** once a stage's script is proven, wrap it in an OS scheduler (cron / launchd / Task Scheduler) for recurring runs.

Don't build the scheduling layer until the underlying scripts are solid.

---

## 8. Suggested repo structure

```
job-hunt-pipeline/
├── README.md
├── profile.json                # master profile (keystone input)
├── config.yaml                 # titles, locations, keywords, source list
├── db/
│   ├── schema.sql              # the six tables
│   └── pipeline.db             # the one authoritative store (gitignored)
├── stages/
│   ├── 01_source.py
│   ├── 02_rank.py
│   ├── 03_enrich.py
│   ├── 04_draft.py
│   └── 05_outreach.py
├── lib/
│   ├── db.py                   # connection + query helpers
│   └── dedup.py                # the stable-key logic
└── runs/                       # run logs (gitignored)
```

---

## 9. Build order (do not skip ahead)

1. **Schema** (`db/schema.sql`) + DB helpers (`lib/db.py`). Nothing works without the store.
2. **Master profile template** (`profile.json`) + **config** (`config.yaml`).
3. **Stage 1 — sourcing**, starting with *one* reliable source (an API/RSS or a public career page — not LinkedIn). Get dedup right here.
4. **Stage 2 — ranking.** Now you can score what stage 1 collects.
5. **Stage 3 — enrichment.**
6. **Stage 4 — draft generation** (with the `needs_review` gate).
7. **Stage 5b — outreach drafting** (with the `needs_approval` gate).
8. Only then: scheduling, and the cockpit/review layer.

Start small, prove each stage against real data before moving on, and keep the human gates intact at stages 4 and 5b.

---

## 10. Open questions to confirm before/while building

- Which MCP servers to wire in (SQLite, browser/Chrome) — confirm current options against Anthropic's docs, as these shift.
- Which specific sources expose APIs/RSS vs. require browser automation — verify per source rather than assuming.
- Whether the ranking and drafting LLM calls go through the API or stay in-session — a cost/convenience call to make once stage 1 is proven.

*(Anthropic's products and capabilities move fast — confirm tool/MCP specifics against current docs rather than treating any capability claim here as fixed.)*
