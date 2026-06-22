"""Stage 1 — Sourcing & aggregation (autonomous).

Reads config.yaml, runs each ENABLED source, normalizes + dedups results, and
inserts only genuinely new rows into `jobs` with status='new'. Every source is
logged to `run_log`; a failing source is logged and SKIPPED, never fatal
(graceful degradation, per the brief).

Run:
    .venv\\Scripts\\python stages\\01_source.py
    .venv\\Scripts\\python stages\\01_source.py --dry-run   # fetch+report, no DB writes
"""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Make the repo root importable when run as a script from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib import dedup  # noqa: E402
from lib.db import connect, init_db, insert_ignore, execute  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config.yaml"

# Map a config source `name` to the module that implements its adapter.
# (Only france_travail is wired up for now; others get added as built.)
ADAPTERS = {
    "france_travail": "lib.sources.france_travail",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_env() -> None:
    """Minimal .env loader (no extra dependency). Lines: KEY=value, # comments."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    import os

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _log_run(conn, stage, source, status, added, message, started_at) -> None:
    execute(
        conn,
        """INSERT INTO run_log (stage, source, status, rows_added, message, started_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (stage, source, status, added, message, started_at),
    )


def run(dry_run: bool = False) -> int:
    _load_env()
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    search_cfg = cfg.get("search", {})
    sources = [s for s in cfg.get("sources", []) if s.get("enabled")]

    if not sources:
        print("No enabled sources in config.yaml. Set `enabled: true` on one "
              "(and add its credentials to .env) to fetch.")
        return 0

    if not dry_run:
        init_db()

    total_added = 0
    for src in sources:
        name = src["name"]
        started_at = _now()
        module_path = ADAPTERS.get(name)
        if not module_path:
            print(f"[skip] {name}: no adapter wired up yet.")
            continue

        try:
            adapter = importlib.import_module(module_path)
            jobs = adapter.fetch(search_cfg)
        except Exception as exc:  # graceful degradation: log & continue
            msg = f"{type(exc).__name__}: {exc}"
            print(f"[error] {name}: {msg}")
            if not dry_run:
                with connect() as conn:
                    _log_run(conn, "01_source", name, "error", 0, msg, started_at)
            continue

        added = _persist(jobs, dry_run)
        total_added += added
        status = "ok" if jobs else "partial"
        print(f"[ok] {name}: fetched {len(jobs)}, new {added}"
              + (" (dry-run, nothing written)" if dry_run else ""))
        if not dry_run:
            with connect() as conn:
                _log_run(conn, "01_source", name, status, added,
                         f"fetched={len(jobs)} new={added}", started_at)

    print(f"\nStage 1 complete. New jobs added: {total_added}")
    return total_added


def _persist(jobs: list[dict], dry_run: bool) -> int:
    """Dedup-insert normalized jobs. Returns count of genuinely new rows."""
    if dry_run:
        # Report what WOULD be inserted, touch nothing.
        return len(jobs)

    added = 0
    with connect() as conn:
        for j in jobs:
            key = dedup.dedup_key(j["source"], j["native_id"])
            fp = dedup.fingerprint(j["company_name"], j["title"], j.get("location"))
            row = {
                "dedup_key": key,
                "fingerprint": fp,
                "title": j["title"],
                "company_name": j["company_name"],
                "location": j.get("location"),
                "url": j.get("url"),
                "jd_text": j.get("jd_text"),
                "salary": j.get("salary"),
                "source": j["source"],
                "date_posted": j.get("date_posted"),
                "track": j.get("track", "unknown"),
                "status": "new",
            }
            if insert_ignore(conn, "jobs", row) is not None:
                added += 1
    return added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 1 — source new jobs.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and report, but write nothing to the DB.")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
