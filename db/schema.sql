-- ============================================================================
-- Job-Hunt Automation Pipeline — database schema
-- ----------------------------------------------------------------------------
-- The ONE authoritative store for all pipeline state. Every stage reads from
-- and/or writes to this file; nothing holds state anywhere else.
--
-- Six core tables form the spine:  jobs -> companies -> applications -> contacts
-- plus drafts and outreach hanging off jobs/contacts.
--
-- Conventions:
--   * Every table has an integer primary key `id`.
--   * Timestamps are ISO-8601 TEXT (UTC), e.g. '2026-06-22T18:56:00Z'.
--   * `status` / `stage` / `track` columns are constrained with CHECK enums so
--     a typo fails loudly instead of silently corrupting pipeline state.
--   * Foreign keys are declared AND enforced (PRAGMA foreign_keys = ON, set by
--     lib/db.py on every connection — SQLite does not enforce FKs by default).
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- companies — enrichment target (Stage 3). One row per distinct company.
-- Created/updated by enrichment; jobs link to it once known.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    name_norm     TEXT NOT NULL UNIQUE,   -- normalized name for dedup (lowercased, trimmed)
    funding_stage TEXT,                   -- e.g. 'seed', 'series_a', 'public', 'bootstrapped'
    size          TEXT,                   -- e.g. '11-50', '201-500'
    tech_stack    TEXT,                   -- free text / comma list captured by enrichment
    recent_news   TEXT,
    website       TEXT,
    notes         TEXT,
    enriched_at   TEXT,                   -- set when Stage 3 last filled this row
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ----------------------------------------------------------------------------
-- jobs — the heart of Stage 1 (sourcing) and Stage 2 (ranking).
-- A row starts as 'new'; ranking moves it to pursue/maybe/skip; later lifecycle
-- moves it to applied/closed.
--
-- `track` ('core' | 'bridge') is set at sourcing/ranking time so Stage 2 can
-- score embedded/validation roles (core, landable today) against a different
-- rubric than AI-adjacent stretch roles (bridge). 'unknown' until classified.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    dedup_key     TEXT NOT NULL UNIQUE,   -- HARD key: 'source:native_id'. Stops exact re-inserts within a source across runs.
    fingerprint   TEXT,                   -- SOFT key: normalized 'company|title|location'. Lets a later stage spot the SAME job reposted on another source (not UNIQUE — collisions are flagged, not blocked).
    title         TEXT NOT NULL,
    company_name  TEXT NOT NULL,          -- raw, as scraped (before company is enriched/linked)
    location      TEXT,
    url           TEXT,
    jd_text       TEXT,                   -- full job description, parsed to plain text
    salary        TEXT,
    source        TEXT,                   -- which source this came from (e.g. 'wttj', 'company_page')
    date_posted   TEXT,                   -- as reported by the source, if available
    date_scraped  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

    -- Stage 2 ranking output:
    track         TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (track IN ('core','bridge','unknown')),
    fit_score     INTEGER CHECK (fit_score BETWEEN 0 AND 100),
    rationale     TEXT,
    ranked_at     TEXT,

    status        TEXT NOT NULL DEFAULT 'new'
                      CHECK (status IN ('new','pursue','maybe','skip','applied','closed')),

    company_id    INTEGER REFERENCES companies(id) ON DELETE SET NULL  -- nullable until enriched
);

-- ----------------------------------------------------------------------------
-- contacts — people at a company (Stage 3 enrichment / used by Stage 5b).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name          TEXT,
    role          TEXT,
    linkedin_url  TEXT,
    email         TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ----------------------------------------------------------------------------
-- applications — pipeline tracking (Stage 5a, cockpit-driven).
-- One row per job actually applied to; `stage` is the human-tracked funnel.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS applications (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage         TEXT NOT NULL DEFAULT 'applied'
                      CHECK (stage IN ('applied','screening','interview','offer','rejected','withdrawn')),
    date_applied  TEXT,
    next_action   TEXT,
    follow_up_due TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ----------------------------------------------------------------------------
-- drafts — Stage 4 output (GATED). CV variants and cover letters.
-- ALWAYS created as 'needs_review'. The human edits/approves in the cockpit;
-- nothing here is ever auto-sent.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    type          TEXT NOT NULL CHECK (type IN ('cv','cover_letter')),
    content       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'needs_review'
                      CHECK (status IN ('needs_review','approved','sent','rejected')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ----------------------------------------------------------------------------
-- outreach — Stage 5b output (GATED). Follow-up / cold-outreach messages.
-- ALWAYS created as 'needs_approval'. The human reviews and sends.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outreach (
    id            INTEGER PRIMARY KEY,
    contact_id    INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    content       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'needs_approval'
                      CHECK (status IN ('needs_approval','approved','sent','rejected')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ----------------------------------------------------------------------------
-- run_log — one row per stage run, for observability and graceful degradation.
-- Stage 1 especially logs source successes/failures here and continues.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_log (
    id            INTEGER PRIMARY KEY,
    stage         TEXT NOT NULL,          -- e.g. '01_source', '02_rank'
    source        TEXT,                   -- which source, when applicable
    status        TEXT NOT NULL CHECK (status IN ('ok','partial','error')),
    rows_added    INTEGER DEFAULT 0,
    rows_updated  INTEGER DEFAULT 0,
    message       TEXT,                   -- error text / summary
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ----------------------------------------------------------------------------
-- Indexes for the read patterns each stage actually uses.
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint  ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_track        ON jobs(track);
CREATE INDEX IF NOT EXISTS idx_jobs_company_id   ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_company  ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_applications_job  ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_drafts_job        ON drafts(job_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status     ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_outreach_job      ON outreach(job_id);
CREATE INDEX IF NOT EXISTS idx_outreach_status   ON outreach(status);
