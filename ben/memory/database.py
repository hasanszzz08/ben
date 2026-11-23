"""SQLite database layer for BEN memory.

Uses stdlib sqlite3 only — no ORM dependency.

Connection strategy
-------------------
FastAPI runs sync route handlers in a thread pool and async handlers
in the event loop. To be safe we use a threading.local() pool so each
thread gets its own connection (SQLite connections are not thread-safe
to share across threads).

The module exposes:
  get_db()          → context manager returning a sqlite3.Connection
  init_db()         → create tables and apply schema migrations
  DB_PATH           → pathlib.Path to the database file
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from ben.config import PROJECT_ROOT, settings
from ben.utils.logger import logger


# ---------------------------------------------------------------------------
# Database location
# ---------------------------------------------------------------------------

DB_PATH: Path = settings.ben_data_dir / "ben_memory.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Thread-local storage for connections
_local = threading.local()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────
-- conversations: one row per chat session
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT 'Untitled',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ─────────────────────────────────────────────────
-- messages: individual chat turns
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role                TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content             TEXT NOT NULL DEFAULT '',
    is_error            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages(conversation_id, created_at);

-- ─────────────────────────────────────────────────
-- core_memories: user-approved long-term facts
-- Categories: preference | goal | project | context | note
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core_memories (
    id          TEXT PRIMARY KEY,
    category    TEXT NOT NULL DEFAULT 'note'
                CHECK(category IN ('preference','goal','project','context','note')),
    content     TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ─────────────────────────────────────────────────
-- users: user profile information (Phase 3.5)
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    name        TEXT,
    role        TEXT,
    interests   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Migration list: (version, sql). Applied once, tracked in schema_version table.
MIGRATIONS: list[tuple[int, str]] = [
    (1, SCHEMA_SQL),
]

MIGRATION_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _make_connection() -> sqlite3.Connection:
    """Open a new SQLite connection with sensible defaults."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_thread_connection() -> sqlite3.Connection:
    """Return (or create) a per-thread SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _make_connection()
    return _local.conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a sqlite3 connection.

    The caller must NOT close the connection; it is reused across calls
    within the same thread.  Rolls back on exception.
    """
    conn = _get_thread_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Initialisation & migrations
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables and apply any pending schema migrations.

    Safe to call multiple times (idempotent).
    """
    conn = _get_thread_connection()
    try:
        # Bootstrap migration tracker
        conn.executescript(MIGRATION_BOOTSTRAP)
        conn.commit()

        # Determine current version
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0

        # Ensure users table is present across all schema versions
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                name        TEXT,
                role        TEXT,
                interests   TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
        for version, sql in sorted(pending, key=lambda x: x[0]):
            logger.info(f"Applying DB migration v{version}…")
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.commit()

        if pending:
            logger.info(f"Database initialised at {DB_PATH}")
        else:
            logger.debug(f"Database schema up to date (v{current}) at {DB_PATH}")

    except Exception as exc:
        conn.rollback()
        logger.error(f"Database initialisation failed: {exc}")
        raise


def close_thread_connection() -> None:
    """Explicitly close this thread's connection (call on thread teardown)."""
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None
