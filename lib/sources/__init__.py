"""Source adapters for Stage 1.

Each adapter exposes a `fetch(search_cfg) -> list[dict]` returning NORMALIZED
job dicts with this shape (the contract stages/01_source.py relies on):

    {
        "source":       str,    # adapter name, e.g. "france_travail"
        "native_id":    str,    # source's own stable id (NOT a url)
        "title":        str,
        "company_name": str,
        "location":     str | None,
        "url":          str | None,
        "jd_text":      str | None,
        "salary":       str | None,
        "date_posted":  str | None,   # ISO-8601 if available
        "track":        str,          # 'core' | 'bridge' — which query set found it
    }

Adapters must raise on hard failure; the orchestrator catches per-source so one
bad source doesn't sink the run (graceful degradation, per the brief).
"""
