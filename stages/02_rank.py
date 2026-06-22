"""Stage 2 — Filtering & ranking (autonomous).

Scores each unranked job against the master profile via the Claude API, writes
fit_score + rationale, and sets status (pursue/maybe/skip) from config thresholds.

Idempotent: only scores jobs with fit_score IS NULL, so a re-run doesn't re-pay
for already-ranked jobs. Logs the run to run_log.

Run:
    .venv\\Scripts\\python stages\\02_rank.py
    .venv\\Scripts\\python stages\\02_rank.py --limit 5    # score only 5 (cheap test)
    .venv\\Scripts\\python stages\\02_rank.py --rescore     # re-score everything
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import anthropic  # noqa: E402

from lib import ranker  # noqa: E402
from lib.db import connect, query, execute  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config.yaml"
PROFILE_PATH = REPO_ROOT / "profile.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_env() -> None:
    """Load .env (KEY=value) into the environment — same minimal loader as Stage 1."""
    import os

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _status_for(score: int, thresholds: dict[str, int]) -> str:
    if score >= thresholds["pursue"]:
        return "pursue"
    if score >= thresholds["maybe"]:
        return "maybe"
    return "skip"


def run(limit: int | None = None, rescore: bool = False) -> int:
    _load_env()
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    thresholds = cfg["ranking"]["thresholds"]
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    # Select jobs to score. By default only unranked ones (idempotent/resumable).
    where = "WHERE fit_score IS NULL" if not rescore else ""
    sql = f"SELECT * FROM jobs {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"

    with connect() as conn:
        jobs = [dict(r) for r in query(conn, sql)]

    if not jobs:
        print("No jobs to rank (all already scored — use --rescore to redo).")
        return 0

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    system_prompt = ranker.build_system_prompt(profile, thresholds)

    started_at = _now()
    scored = 0
    errors = 0
    counts = {"pursue": 0, "maybe": 0, "skip": 0}

    print(f"Ranking {len(jobs)} job(s) with {ranker.MODEL}...")
    for job in jobs:
        try:
            result = ranker.score_job(client, system_prompt, job)
        except Exception as exc:
            errors += 1
            print(f"  [error] job {job['id']} ({job['title'][:40]}): "
                  f"{type(exc).__name__}: {exc}")
            continue

        score = int(result["fit_score"])
        status = _status_for(score, thresholds)
        track = result.get("track_assessment", job.get("track", "unknown"))
        counts[status] += 1
        scored += 1

        with connect() as conn:
            execute(
                conn,
                """UPDATE jobs
                   SET fit_score = ?, rationale = ?, track = ?, status = ?,
                       ranked_at = ?
                   WHERE id = ?""",
                (score, result["rationale"], track, status, _now(), job["id"]),
            )
        print(f"  [{status:6}] {score:3}  {job['title'][:50]}")

    status = "ok" if errors == 0 else "partial"
    with connect() as conn:
        execute(
            conn,
            """INSERT INTO run_log (stage, source, status, rows_updated, message, started_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("02_rank", None, status, scored,
             f"scored={scored} errors={errors} "
             f"pursue={counts['pursue']} maybe={counts['maybe']} skip={counts['skip']}",
             started_at),
        )

    print(f"\nStage 2 complete. Scored {scored} "
          f"(pursue={counts['pursue']}, maybe={counts['maybe']}, skip={counts['skip']})"
          + (f", {errors} error(s)" if errors else ""))
    return scored


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 2 — rank jobs against the profile.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only score the first N unranked jobs (cheap test).")
    parser.add_argument("--rescore", action="store_true",
                        help="Re-score ALL jobs, not just unranked ones.")
    args = parser.parse_args()
    run(limit=args.limit, rescore=args.rescore)
