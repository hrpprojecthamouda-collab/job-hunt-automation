"""Stage 4 — Tailoring drafts (GATED: code generates, human reviews).

For each `pursue` job, generates a tailored French CV variant + cover letter
and writes them as `drafts` rows with status='needs_review'. NEVER auto-sent.

Idempotent: skips a job that already has drafts (use --redraft to regenerate).
Reads company enrichment (Stage 3) so the cover letter speaks to the real employer.

Run:
    .venv\\Scripts\\python stages\\04_draft.py             # draft for all pursue jobs lacking drafts
    .venv\\Scripts\\python stages\\04_draft.py --job 2     # just one job id
    .venv\\Scripts\\python stages\\04_draft.py --redraft   # regenerate even if drafts exist
    .venv\\Scripts\\python stages\\04_draft.py --export     # write existing drafts to runs/drafts/*.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import anthropic  # noqa: E402

from lib import drafter  # noqa: E402
from lib.db import connect, query, query_one, execute, insert  # noqa: E402

PROFILE_PATH = REPO_ROOT / "profile.json"
EXPORT_DIR = REPO_ROOT / "runs" / "drafts"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_env() -> None:
    import os
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _has_drafts(conn, job_id: int) -> bool:
    row = query_one(conn, "SELECT COUNT(*) n FROM drafts WHERE job_id = ?", (job_id,))
    return row["n"] > 0


def run(job_id: int | None = None, redraft: bool = False) -> int:
    _load_env()
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    where = "WHERE status = 'pursue'"
    params: tuple = ()
    if job_id is not None:
        where = "WHERE id = ?"
        params = (job_id,)

    with connect() as conn:
        jobs = [dict(r) for r in query(conn, f"SELECT * FROM jobs {where} ORDER BY fit_score DESC", params)]

    if not jobs:
        print("No matching jobs to draft.")
        return 0

    client = anthropic.Anthropic()
    system_prompt = drafter.build_system_prompt(profile)
    started_at = _now()
    made = 0

    for job in jobs:
        with connect() as conn:
            if not redraft and _has_drafts(conn, job["id"]):
                print(f"  [skip] job {job['id']} already has drafts (--redraft to regenerate).")
                continue
            company = None
            if job.get("company_id"):
                company = query_one(conn, "SELECT * FROM companies WHERE id = ?",
                                    (job["company_id"],))
                company = dict(company) if company else None

        print(f"  drafting job {job['id']} [{job['fit_score']}] {job['title'][:45]} "
              f"@ {job.get('company_name')} ...")
        try:
            result = drafter.draft_for_job(client, system_prompt, job, company)
        except Exception as exc:
            print(f"    [error] {type(exc).__name__}: {exc}")
            continue

        with connect() as conn:
            if redraft:
                execute(conn, "DELETE FROM drafts WHERE job_id = ?", (job["id"],))
            insert(conn, "drafts", {"job_id": job["id"], "type": "cv",
                                    "content": result["cv_markdown"],
                                    "status": "needs_review", "created_at": _now()})
            insert(conn, "drafts", {"job_id": job["id"], "type": "cover_letter",
                                    "content": result["cover_letter_markdown"],
                                    "status": "needs_review", "created_at": _now()})
            made += 2
        print(f"    -> CV + cover letter written (needs_review). "
              f"Notes: {result.get('tailoring_notes', '')[:90]}")

    with connect() as conn:
        execute(conn, """INSERT INTO run_log (stage, status, rows_updated, message, started_at)
                         VALUES (?, ?, ?, ?, ?)""",
                ("04_draft", "ok", made, f"created {made} draft rows (needs_review)", started_at))

    print(f"\nStage 4 complete. Created {made} draft(s), all status=needs_review (GATED).")
    print("Review them: .venv\\Scripts\\python stages\\04_draft.py --export")
    return made


def export() -> None:
    """Write existing drafts to runs/drafts/*.md for human review."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        rows = query(conn, """
            SELECT d.id, d.job_id, d.type, d.status, d.content,
                   j.title, j.company_name, j.fit_score
            FROM drafts d JOIN jobs j ON d.job_id = j.id
            ORDER BY j.fit_score DESC, d.type""")
    if not rows:
        print("No drafts to export. Run Stage 4 first.")
        return
    for r in rows:
        d = dict(r)
        fname = f"job{d['job_id']}_{d['company_name'].replace(' ', '')}_{d['type']}.md"
        path = EXPORT_DIR / fname
        header = (f"<!-- job {d['job_id']} | {d['title']} | {d['company_name']} "
                  f"| score {d['fit_score']} | status: {d['status']} -->\n\n")
        path.write_text(header + d["content"], encoding="utf-8")
        print(f"  wrote {path}")
    print(f"\nExported {len(rows)} draft(s) to {EXPORT_DIR}. Review, edit, then approve.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 4 — generate tailored drafts (GATED).")
    parser.add_argument("--job", type=int, default=None, help="Draft only this job id.")
    parser.add_argument("--redraft", action="store_true", help="Regenerate even if drafts exist.")
    parser.add_argument("--export", action="store_true", help="Export existing drafts to runs/drafts/.")
    args = parser.parse_args()
    if args.export:
        export()
    else:
        run(job_id=args.job, redraft=args.redraft)
