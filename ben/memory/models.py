"""Data-access layer for BEN memory tables.

All functions are synchronous (safe to call from asyncio.to_thread).
They use get_db() for connection management.

Modules
-------
ConversationStore   – persist and retrieve conversation sessions + messages
CoreMemoryStore     – CRUD for user-approved long-term facts
build_memory_context – compose the system prompt memory injection block
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ben.memory.database import get_db
from ben.core.conversation import ConversationHistory, Message, Role


# ============================================================
# Conversation persistence
# ============================================================

class ConversationStore:
    """Persist chat sessions and messages to SQLite."""

    # ── conversations table ───────────────────────────────────

    @staticmethod
    def create_conversation(session_id: Optional[str] = None, title: str = "Untitled") -> str:
        """Insert a new conversation row. Returns the session_id."""
        cid = session_id or str(uuid.uuid4())
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)",
                (cid, title)
            )
        return cid

    @staticmethod
    def ensure_conversation(session_id: str, title: str = "Untitled") -> str:
        """Create the conversation row if it doesn't already exist. Idempotent."""
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO conversations (id, title) VALUES (?, ?)",
                (session_id, title)
            )
        return session_id

    @staticmethod
    def update_conversation_title(session_id: str, title: str) -> None:
        with get_db() as db:
            db.execute(
                "UPDATE conversations SET title=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?",
                (title, session_id)
            )

    @staticmethod
    def get_conversation(session_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as db:
            row = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id=?",
                (session_id,)
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_conversations(limit: int = 50) -> List[Dict[str, Any]]:
        with get_db() as db:
            rows = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def delete_conversation(session_id: str) -> bool:
        """Delete a conversation and all its messages (CASCADE)."""
        with get_db() as db:
            cur = db.execute("DELETE FROM conversations WHERE id=?", (session_id,))
        return cur.rowcount > 0

    # ── messages table ────────────────────────────────────────

    @staticmethod
    def save_message(session_id: str, message: Message) -> None:
        """Persist a single Message object to the messages table."""
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO messages "
                "(id, conversation_id, role, content, is_error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message.id,
                    session_id,
                    message.role.value,
                    message.content,
                    int(message.is_error),
                    message.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
                )
            )
            # Touch parent's updated_at
            db.execute(
                "UPDATE conversations SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?",
                (session_id,)
            )

    @staticmethod
    def update_message_content(message_id: str, content: str) -> None:
        """Update the content of an existing message (used during streaming)."""
        with get_db() as db:
            db.execute(
                "UPDATE messages SET content=? WHERE id=?",
                (content, message_id)
            )

    @staticmethod
    def load_messages(session_id: str) -> List[Message]:
        """Load all messages for a session as Message objects."""
        with get_db() as db:
            rows = db.execute(
                "SELECT id, role, content, is_error, created_at "
                "FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
                (session_id,)
            ).fetchall()

        result = []
        for r in rows:
            try:
                ts_str = r["created_at"].rstrip("Z")
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            except Exception:
                ts = datetime.now(timezone.utc)

            result.append(Message(
                id=r["id"],
                role=Role(r["role"]),
                content=r["content"],
                is_error=bool(r["is_error"]),
                timestamp=ts,
            ))
        return result

    @staticmethod
    def load_history(session_id: str) -> ConversationHistory:
        """Reconstruct a full ConversationHistory from the database."""
        history = ConversationHistory(session_id=session_id)
        messages = ConversationStore.load_messages(session_id)
        history.messages = messages
        return history

    @staticmethod
    def get_message_count(session_id: str) -> int:
        with get_db() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                (session_id,)
            ).fetchone()
        return row[0] if row else 0


# ============================================================
# Core Memory CRUD
# ============================================================

VALID_CATEGORIES = {"preference", "goal", "project", "context", "note"}


class CoreMemoryStore:
    """Manage user-approved long-term facts in the core_memories table."""

    @staticmethod
    def create(category: str, content: str) -> Dict[str, Any]:
        """Insert a new core memory and return it as a dict."""
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of: {sorted(VALID_CATEGORIES)}")
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty.")
        if len(content) > 2000:
            raise ValueError("Memory content too long (max 2000 characters).")

        mid = str(uuid.uuid4())
        with get_db() as db:
            db.execute(
                "INSERT INTO core_memories (id, category, content) VALUES (?, ?, ?)",
                (mid, category, content)
            )
            row = db.execute("SELECT * FROM core_memories WHERE id=?", (mid,)).fetchone()
        return dict(row)

    @staticmethod
    def get(memory_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as db:
            row = db.execute("SELECT * FROM core_memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_all(active_only: bool = False, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return memories, optionally filtered by active status and category."""
        conditions, params = [], []
        if active_only:
            conditions.append("active = 1")
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with get_db() as db:
            rows = db.execute(
                f"SELECT * FROM core_memories {where} ORDER BY category, created_at ASC",
                params
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def update(memory_id: str, content: Optional[str] = None,
               category: Optional[str] = None, active: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        """Partially update a core memory. Returns updated row or None if not found."""
        if category is not None and category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'.")

        sets, params = [], []
        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError("Memory content cannot be empty.")
            sets.append("content = ?"); params.append(content)
        if category is not None:
            sets.append("category = ?"); params.append(category)
        if active is not None:
            sets.append("active = ?"); params.append(int(active))

        if not sets:
            return CoreMemoryStore.get(memory_id)

        sets.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        params.append(memory_id)

        with get_db() as db:
            db.execute(
                f"UPDATE core_memories SET {', '.join(sets)} WHERE id=?",
                params
            )
            row = db.execute("SELECT * FROM core_memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def delete(memory_id: str) -> bool:
        with get_db() as db:
            cur = db.execute("DELETE FROM core_memories WHERE id=?", (memory_id,))
        return cur.rowcount > 0

    @staticmethod
    def toggle_active(memory_id: str) -> Optional[Dict[str, Any]]:
        """Flip the active flag on a memory."""
        with get_db() as db:
            db.execute(
                "UPDATE core_memories SET active = 1 - active, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (memory_id,)
            )
            row = db.execute("SELECT * FROM core_memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None


# ============================================================
# System prompt context builder
# ============================================================

def build_memory_context(base_instruction: str) -> str:
    """Inject active core_memories into BEN's system instruction.

    Memories are appended as a structured block so BEN always has
    current goals, preferences, and project context in-scope without
    requiring them to be repeated in every message.

    Returns the enriched system instruction string.
    """
    memories = CoreMemoryStore.list_all(active_only=True)
    if not memories:
        return base_instruction

    # Group by category
    grouped: Dict[str, List[str]] = {}
    for m in memories:
        grouped.setdefault(m["category"], []).append(m["content"])

    sections = []
    category_labels = {
        "preference": "Preferences",
        "goal":       "Current Goals",
        "project":    "Active Projects",
        "context":    "Context",
        "note":       "Notes",
    }

    for cat in ["goal", "project", "preference", "context", "note"]:
        if cat in grouped:
            label = category_labels[cat]
            items = "\n".join(f"  • {c}" for c in grouped[cat])
            sections.append(f"{label}:\n{items}")

    memory_block = "\n\n".join(sections)

    return (
        f"{base_instruction}\n\n"
        f"--- LONG-TERM MEMORY (user-approved) ---\n"
        f"{memory_block}\n"
        f"--- END MEMORY ---"
    )
