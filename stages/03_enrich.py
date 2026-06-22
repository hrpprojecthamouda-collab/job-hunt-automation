"""Stage 3 — Company enrichment (autonomous, web-research-assisted).

Takes the `pursue` set, identifies the real employer (France Travail often
anonymizes the company name to '(non précisé)'), and writes a `companies` row
with funding stage / size / tech stack / recent news, FK-linking each job to it.

At this phase the web research is done in-session (Claude Code WebSearch/WebFetch)
and the findings are passed in as a structured payload — this keeps a human in
the loop on what's relevant, which matters when employer names are hidden and
identification takes judgment. A later phase can swap in the API's server-side
web-search tool for full autonomy.

Run:
    .venv\\Scripts\\python stages\\03_enrich.py            # apply the ENRICHMENTS below
    .venv\\Scripts\\python stages\\03_enrich.py --show     # show current enrichment state
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.db import connect, query, query_one, execute, insert  # noqa: E402
from lib.dedup import normalize  # noqa: E402

# ---------------------------------------------------------------------------
# Enrichment findings — researched in-session for the current `pursue` set.
# Each entry maps one or more job IDs to the real company + enrichment fields.
# (job_ids lets one company cover multiple pursue jobs, e.g. CGI = 3 & 7.)
# ---------------------------------------------------------------------------
ENRICHMENTS = [
    {
        "job_ids": [3, 7],
        "name": "CGI",
        "funding_stage": "public (TSX/NYSE: GIB)",
        "size": "~94,000 worldwide; 10,000+ in France",
        "tech_stack": "Engineering services for automotive OEMs. Job 3 (Paris): "
                      "EMC, Process Validation, CANoe, hardware design. Job 7 (Cergy): "
                      "Matlab, Simulink, system architecture, control validation.",
        "recent_news": "FY2025 revenue CA$15.9B. CGI en France ~$2.2B revenue, HQ "
                       "Paris-La Défense. Automotive: 40-yr Michelin partnership; early "
                       "member of Catena-X automotive data ecosystem.",
        "website": "https://www.cgi.com/france",
        "notes": "Large IT/engineering consultancy (ESN). These are consultant roles "
                 "in their automotive engineering-services centre — directly matches "
                 "Mohamed's consultant-at-RENAULT background. CDI. Salary not stated in posting.",
    },
    {
        "job_ids": [2],
        "name": "SNCF Réseau",
        "funding_stage": "public-sector (SNCF Group / French State)",
        "size": "~4,000 engineering staff (SNCF Réseau ingénierie); SNCF Group very large",
        "tech_stack": "Railway signaling: ATS+/NExTEO (CBTC, SIL2), V-cycle homologation "
                      "& acceptance, requirements traceability. Project: RER E / EOLE.",
        "recent_news": "NExTEO is the next-gen automatic train supervision for the Paris "
                       "RER E (EOLE westward extension) — a flagship, innovation-heavy "
                       "signaling programme.",
        "website": "https://www.sncf-reseau.com",
        "notes": "Infrastructure manager within SNCF Group. CDI, €45-55k (matches "
                 "Mohamed's band). Domain is rail, not automotive — transferable V-cycle "
                 "validation discipline; railway signaling is the ramp-up gap.",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def show() -> None:
    with connect() as c:
        rows = query(c, """
            SELECT j.id, j.fit_score, j.title, j.status,
                   co.name AS company, co.size, co.funding_stage
            FROM jobs j LEFT JOIN companies co ON j.company_id = co.id
            WHERE j.status = 'pursue' ORDER BY j.fit_score DESC""")
    for r in rows:
        d = dict(r)
        comp = d["company"] or "(not enriched)"
        print(f"  job {d['id']} [{d['fit_score']}] {d['title'][:45]:45} -> {comp}")


def run() -> int:
    enriched = 0
    with connect() as conn:
        for e in ENRICHMENTS:
            name_norm = normalize(e["name"])
            # Upsert the company by normalized name.
            existing = query_one(conn, "SELECT id FROM companies WHERE name_norm = ?",
                                  (name_norm,))
            if existing:
                company_id = existing["id"]
                execute(conn, """UPDATE companies SET funding_stage=?, size=?,
                                 tech_stack=?, recent_news=?, website=?, notes=?,
                                 enriched_at=? WHERE id=?""",
                        (e["funding_stage"], e["size"], e["tech_stack"], e["recent_news"],
                         e["website"], e["notes"], _now(), company_id))
            else:
                company_id = insert(conn, "companies", {
                    "name": e["name"], "name_norm": name_norm,
                    "funding_stage": e["funding_stage"], "size": e["size"],
                    "tech_stack": e["tech_stack"], "recent_news": e["recent_news"],
                    "website": e["website"], "notes": e["notes"], "enriched_at": _now(),
                })
            # Link the jobs and de-anonymize their company_name.
            for jid in e["job_ids"]:
                execute(conn, "UPDATE jobs SET company_id=?, company_name=? WHERE id=?",
                        (company_id, e["name"], jid))
                enriched += 1

        execute(conn, """INSERT INTO run_log (stage, status, rows_updated, message, started_at)
                         VALUES (?, ?, ?, ?, ?)""",
                ("03_enrich", "ok", enriched,
                 f"enriched {len(ENRICHMENTS)} companies covering {enriched} jobs", _now()))

    print(f"Stage 3 complete. Enriched {len(ENRICHMENTS)} companies, linked {enriched} jobs.")
    show()
    return enriched


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 3 — company enrichment.")
    parser.add_argument("--show", action="store_true", help="Show enrichment state, don't write.")
    args = parser.parse_args()
    if args.show:
        show()
    else:
        run()
