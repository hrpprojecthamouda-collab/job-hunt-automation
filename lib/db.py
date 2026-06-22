"""Database connection + query helpers for the job-hunt pipeline.

The SQLite file at db/pipeline.db is the one authoritative store. Every stage
goes through this module rather than opening its own connection, so that:

  * foreign keys are enforced on EVERY connection (SQLite defaults them OFF),
  * rows come back as dict-like sqlite3.Row objects, not positional tuples,
  * the schema is applied idempotently (safe to call init_db repeatedly),
  * writes happen inside a transaction context manager.

Usage:
    from lib.db import connect, init_db, query, execute

    init_db()                       # create the file + tables if missing
    with connect() as conn:
        rows = query(conn, "SELECT * FROM jobs WHERE status = ?", ("new",))
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# Resolve paths relative to the repo root (this file lives in lib/).
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "pipeline.db"
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"


@contextmanager
def connect(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection, committing on success / rolling back on error.

    Foreign-key enforcement is turned on here because SQLite ignores FK
    constraints unless `PRAGMA foreign_keys = ON` is set per-connection.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # safer concurrent reads (cockpit + engine)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create the database file and apply schema.sql idempotently."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(schema_sql)


def query(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> list[sqlite3.Row]:
    """Run a SELECT and return all rows as a list of sqlite3.Row."""
    return conn.execute(sql, params).fetchall()


def query_one(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> sqlite3.Row | None:
    """Run a SELECT and return the first row, or None."""
    return conn.execute(sql, params).fetchone()


def execute(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()
) -> sqlite3.Cursor:
    """Run a single INSERT/UPDATE/DELETE. Returns the cursor (for lastrowid)."""
    return conn.execute(sql, params)


def execute_many(
    conn: sqlite3.Connection, sql: str, seq_of_params: Iterable[Sequence[Any]]
) -> sqlite3.Cursor:
    """Run the same INSERT/UPDATE/DELETE over many parameter sets."""
    return conn.executemany(sql, seq_of_params)


def insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> int:
    """Insert a dict as a row; return the new rowid.

    Column names come from the dict keys, so callers stay readable and we don't
    hand-write column lists at each call site.
    """
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    cur = conn.execute(sql, tuple(row.values()))
    return int(cur.lastrowid)


def insert_ignore(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> int | None:
    """Insert, skipping silently on a UNIQUE conflict (used for dedup in Stage 1).

    Returns the new rowid, or None if the row already existed (conflict).
    """
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    sql = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
    cur = conn.execute(sql, tuple(row.values()))
    return int(cur.lastrowid) if cur.rowcount else None


if __name__ == "__main__":
    # `python lib/db.py` initializes (or migrates) the database in place.
    init_db()
    with connect() as c:
        tables = query(
            c,
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        )
    print(f"Initialized {DB_PATH}")
    print("Tables:", ", ".join(r["name"] for r in tables))
