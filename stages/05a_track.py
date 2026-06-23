"""Stage 5a — Pipeline tracking (human-driven).

A thin CLI over the `applications` table: record that you applied to a job, and
advance its stage as the process moves (applied -> screening -> interview ->
offer / rejected / withdrawn). This is the single source of truth for "where is
each application", which Stage 5b (outreach) later reads to decide follow-ups.

Run:
    .venv\\Scripts\\python stages\\05a_track.py --show
    .venv\\Scripts\\python stages\\05a_track.py --applied 3 --date 2026-06-22
    .venv\\Scripts\\python stages\\05a_track.py --advance 3 --stage screening \\
        --next "Appel tel. RH CGI" --follow-up 2026-06-30 --note "RH a contacté le 23/06"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.db import connect, query, query_one, execute, insert  # noqa: E402

VALID_STAGES = ("applied", "screening", "interview", "offer", "rejected", "withdrawn")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def show() -> None:
    with connect() as c:
        rows = query(c, """
            SELECT a.id, a.job_id, a.stage, a.date_applied, a.next_action,
                   a.follow_up_due, a.notes, j.title, j.company_name, j.fit_score
            FROM applications a JOIN jobs j ON a.job_id = j.id
            ORDER BY a.follow_up_due IS NULL, a.follow_up_due, j.fit_score DESC""")
    if not rows:
        print("No applications tracked yet. Use --applied <job_id>.")
        return
    for r in rows:
        d = dict(r)
        print(f"job {d['job_id']} [{d['fit_score']}] {d['company_name']} — {d['title'][:40]}")
        print(f"  stage: {d['stage']}  | applied: {d['date_applied']}  "
              f"| follow-up due: {d['follow_up_due'] or '-'}")
        if d['next_action']:
            print(f"  next: {d['next_action']}")
        if d['notes']:
            print(f"  notes: {d['notes']}")
        print()


def applied(job_id: int, date: str | None) -> None:
    with connect() as c:
        job = query_one(c, "SELECT id, title, company_name FROM jobs WHERE id = ?", (job_id,))
        if not job:
            print(f"No job with id {job_id}.")
            return
        existing = query_one(c, "SELECT id FROM applications WHERE job_id = ?", (job_id,))
        if existing:
            print(f"Application for job {job_id} already exists (id {existing['id']}). "
                  f"Use --advance to update it.")
            return
        insert(c, "applications", {
            "job_id": job_id, "stage": "applied",
            "date_applied": date or _now()[:10], "created_at": _now(),
        })
        # reflect lifecycle on the job row too
        execute(c, "UPDATE jobs SET status = 'applied' WHERE id = ?", (job_id,))
    print(f"Recorded application: job {job_id} ({dict(job)['company_name']} — "
          f"{dict(job)['title']}) as 'applied' on {date or _now()[:10]}.")


def advance(job_id: int, stage: str, next_action: str | None,
            follow_up: str | None, note: str | None) -> None:
    if stage not in VALID_STAGES:
        print(f"Invalid stage '{stage}'. Use one of: {', '.join(VALID_STAGES)}")
        return
    with connect() as c:
        appn = query_one(c, "SELECT id, notes FROM applications WHERE job_id = ?", (job_id,))
        if not appn:
            print(f"No application for job {job_id}. Record it first with --applied {job_id}.")
            return
        # append note rather than overwrite
        notes = dict(appn).get("notes") or ""
        if note:
            stamp = _now()[:10]
            notes = (notes + f"\n[{stamp}] {note}").strip()
        execute(c, """UPDATE applications
                      SET stage = ?, next_action = COALESCE(?, next_action),
                          follow_up_due = COALESCE(?, follow_up_due), notes = ?
                      WHERE job_id = ?""",
                (stage, next_action, follow_up, notes, job_id))
        if stage in ("rejected", "withdrawn"):
            execute(c, "UPDATE jobs SET status = 'closed' WHERE id = ?", (job_id,))
    print(f"Updated job {job_id} -> stage '{stage}'"
          + (f", next: {next_action}" if next_action else "")
          + (f", follow-up: {follow_up}" if follow_up else ""))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Stage 5a — track applications.")
    p.add_argument("--show", action="store_true", help="Show all tracked applications.")
    p.add_argument("--applied", type=int, metavar="JOB_ID", help="Record an application as applied.")
    p.add_argument("--date", help="Date applied (YYYY-MM-DD); defaults to today.")
    p.add_argument("--advance", type=int, metavar="JOB_ID", help="Advance an application's stage.")
    p.add_argument("--stage", help=f"New stage: {', '.join(VALID_STAGES)}")
    p.add_argument("--next", dest="next_action", help="Next action text.")
    p.add_argument("--follow-up", dest="follow_up", help="Follow-up due date (YYYY-MM-DD).")
    p.add_argument("--note", help="Append a timestamped note.")
    args = p.parse_args()

    if args.show:
        show()
    elif args.applied is not None:
        applied(args.applied, args.date)
    elif args.advance is not None:
        if not args.stage:
            print("--advance requires --stage.")
        else:
            advance(args.advance, args.stage, args.next_action, args.follow_up, args.note)
    else:
        show()
