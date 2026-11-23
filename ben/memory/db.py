"""SQLite user profile and memory database interface — Phase 3.5.

Provides:
- load_user_profile(user_id): retrieve user profile dict
- save_user_profile(user_id, name, role, interests): persist user profile
- Database initialization and table definitions
"""

import sqlite3
from typing import Any, Dict, Optional

from ben.memory.database import get_db, init_db, DB_PATH
from ben.utils.logger import logger


def init_users_table() -> None:
    """Ensure the users table exists in SQLite database."""
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                name        TEXT,
                role        TEXT,
                interests   TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def load_user_profile(user_id: str = "hasan") -> Optional[Dict[str, Any]]:
    """Load user profile from SQLite database by user_id.

    Returns a dict with 'name', 'role', 'interests', and 'user_id', or None if not found.
    """
    init_users_table()
    with get_db() as db:
        row = db.execute(
            "SELECT user_id, name, role, interests, created_at FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not row and user_id != "hasan":
            # Fallback to 'hasan' if specific user_id not found
            row = db.execute(
                "SELECT user_id, name, role, interests, created_at FROM users WHERE user_id = 'hasan'"
            ).fetchone()

        if not row:
            # Fallback to first available user
            row = db.execute(
                "SELECT user_id, name, role, interests, created_at FROM users LIMIT 1"
            ).fetchone()

    if row:
        return {
            "user_id": row["user_id"],
            "name": row["name"],
            "role": row["role"],
            "interests": row["interests"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        }
    return None


def save_user_profile(
    user_id: str,
    name: str,
    role: str,
    interests: str,
) -> Dict[str, Any]:
    """Insert or update a user profile in the users table."""
    init_users_table()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO users (user_id, name, role, interests, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                interests = excluded.interests;
            """,
            (user_id, name, role, interests),
        )

    logger.info(f"User profile saved for '{user_id}': {name} ({role})")
    return {
        "user_id": user_id,
        "name": name,
        "role": role,
        "interests": interests,
    }
