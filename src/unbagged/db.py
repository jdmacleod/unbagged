"""SQLite connection and migration handling.

Four-ish tables do not justify an ORM (docs/handoff.md §3), so this is plain
``sqlite3`` with a numbered-migration runner. Migrations are ``.sql`` files in
``migrations/``, applied in filename order and recorded in ``schema_migration``.
Adding a migration means adding a file; there is no downgrade path, because a
single-user local database is better restored from a backup than un-migrated.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

DEFAULT_DB_PATH = Path("data/db/unbagged.sqlite")
DB_PATH_ENV = "UNBAGGED_DB"


def db_path() -> Path:
    """Where the database lives. The container sets UNBAGGED_DB to a path on the
    bind-mounted volume, so a user can back the whole thing up by copying a file."""
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with the pragmas this project depends on.

    Foreign keys are off by default in SQLite, which would silently turn every
    ``ON DELETE CASCADE`` in the schema into a no-op and leave orphaned baskets
    behind when a request is deleted.
    """
    target = Path(path) if path is not None else db_path()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False, deliberately. FastAPI resolves a sync dependency
    # and calls a sync endpoint on different threadpool workers, so a connection
    # created while resolving would be unusable by the handler. The guard it
    # removes is not protecting anything here: api.py hands out one connection
    # per request and never shares it, so no two threads ever touch the same
    # connection at once.
    #
    # The timeout is a short wait rather than an immediate "database is locked":
    # one user with two browser tabs is a realistic amount of concurrency.
    conn = sqlite3.connect(
        target, isolation_level=None, timeout=10.0, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def available_migrations() -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = MIGRATION_NAME.match(path.name)
        if not m:
            raise ValueError(
                f"migration {path.name!r} must be named NNN_lower_snake_case.sql"
            )
        found.append((int(m.group(1)), path))
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration version among {versions}")
    return found


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration ("
        "  version INTEGER PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
        ")"
    )
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migration")}


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply every pending migration. Returns the versions applied, newest last."""
    done = applied_versions(conn)
    applied: list[int] = []
    for version, path in available_migrations():
        if version in done:
            continue
        # Each migration is one transaction: a half-applied schema is worse than
        # an unapplied one, because the next run would skip the missing half.
        # BEGIN/COMMIT have to live inside the script itself — executescript()
        # commits any transaction that is already open before it runs.
        # The filename is interpolated rather than bound because executescript()
        # takes no parameters; available_migrations() has already checked it
        # against MIGRATION_NAME, so it cannot carry a quote.
        script = "\n".join([
            "BEGIN;",
            path.read_text(encoding="utf-8"),
            f"INSERT INTO schema_migration (version, name) "  # noqa: S608
            f"VALUES ({version}, '{path.name}');",
            "COMMIT;",
        ])
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


@contextmanager
def open_db(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a migrated connection, and close it afterwards."""
    conn = connect(path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a unit of work. A parse that fails halfway must leave no partial
    request behind — half a report is indistinguishable from a truncated one."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
