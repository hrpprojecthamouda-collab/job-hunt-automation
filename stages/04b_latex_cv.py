"""Stage 4b — Tailored LaTeX CV folders (GATED, for human review).

Generates, per pursue job, a full copy of the user's real cv/ LaTeX template
with only the headline, tagline, and experience ORDER tailored to the role.
Skills, projects, certifications, education, languages are the user's verbatim
files — so the 3 Flutter projects and 2 Anthropic certifications are always
included, unchanged.

Output: runs/cv/<role>/  (a compilable LaTeX project each). Gitignored.

Run:
    .venv\\Scripts\\python stages\\04b_latex_cv.py
    .venv\\Scripts\\python stages\\04b_latex_cv.py --job 2
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

from lib import latex_cv  # noqa: E402
from lib.db import connect, query, query_one, execute  # noqa: E402

PROFILE_PATH = REPO_ROOT / "profile.json"
CV_SRC = REPO_ROOT / "cv"
OUT_ROOT = REPO_ROOT / "runs" / "cv"


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


def _slug(job: dict) -> str:
    co = (job.get("company_name") or "co").replace(" ", "")
    return f"job{job['id']}_{co}"


def run(job_id: int | None = None) -> int:
    if not CV_SRC.exists():
        print(f"Source template not found: {CV_SRC}")
        return 0
    _load_env()
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    where = "WHERE status = 'pursue'"
    params: tuple = ()
    if job_id is not None:
        where, params = "WHERE id = ?", (job_id,)
    with connect() as conn:
        jobs = [dict(r) for r in query(conn, f"SELECT * FROM jobs {where} ORDER BY fit_score DESC", params)]
    if not jobs:
        print("No matching jobs.")
        return 0

    client = anthropic.Anthropic()
    system_prompt = latex_cv.build_system_prompt(profile)
    started_at = _now()
    made = 0

    for job in jobs:
        company = None
        if job.get("company_id"):
            with connect() as conn:
                row = query_one(conn, "SELECT * FROM companies WHERE id = ?", (job["company_id"],))
                company = dict(row) if row else None
        print(f"  tailoring LaTeX CV for job {job['id']} [{job['fit_score']}] "
              f"{job['title'][:42]} @ {job.get('company_name')} ...")
        try:
            plan = latex_cv.plan_for_job(client, system_prompt, job, company)
            out_dir = OUT_ROOT / _slug(job)
            latex_cv.render_cv_folder(CV_SRC, out_dir, plan, job)
        except Exception as exc:
            print(f"    [error] {type(exc).__name__}: {exc}")
            continue
        made += 1
        print(f"    -> {out_dir}  (order: {' > '.join(plan['experience_order'])})")

    with connect() as conn:
        execute(conn, """INSERT INTO run_log (stage, status, rows_updated, message, started_at)
                         VALUES (?, ?, ?, ?, ?)""",
                ("04b_latex_cv", "ok", made,
                 f"generated {made} tailored LaTeX CV folder(s)", started_at))

    print(f"\nStage 4b complete. {made} tailored LaTeX CV folder(s) in {OUT_ROOT}.")
    print("Each is a full cv/ copy — compile cv.tex with LuaLaTeX. Review before sending.")
    return made


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 4b — tailored LaTeX CV folders.")
    parser.add_argument("--job", type=int, default=None, help="Only this job id.")
    args = parser.parse_args()
    run(job_id=args.job)
