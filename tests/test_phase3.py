"""Phase 3 tests — SQLite memory, CoreMemoryStore, ConversationStore, API endpoints."""

import os
import tempfile
import threading
import pytest
from unittest.mock import MagicMock, patch

# Use a temporary DB for all tests so we never touch the real one
@pytest.fixture(autouse=True)
def temp_database(tmp_path):
    """Redirect the database to a fresh temp file for every test."""
    import ben.memory.database as db_module
    orig_path = db_module.DB_PATH
    db_module.DB_PATH = tmp_path / "test_ben.db"
    db_module.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Reset thread-local connection so we get a fresh one
    import ben.memory.database as db_mod
    if hasattr(db_mod._local, "conn") and db_mod._local.conn:
        db_mod._local.conn.close()
        db_mod._local.conn = None

    db_module.init_db()
    yield

    # Cleanup
    if hasattr(db_mod._local, "conn") and db_mod._local.conn:
        db_mod._local.conn.close()
        db_mod._local.conn = None
    db_module.DB_PATH = orig_path


# ============================================================
# Database initialisation
# ============================================================

class TestDatabaseInit:
    def test_tables_created(self):
        from ben.memory.database import get_db
        with get_db() as db:
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "conversations" in tables
        assert "messages" in tables
        assert "core_memories" in tables
        assert "schema_version" in tables

    def test_migration_tracked(self):
        from ben.memory.database import get_db
        with get_db() as db:
            row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == 1

    def test_init_db_idempotent(self):
        """Calling init_db() twice must not raise."""
        from ben.memory.database import init_db
        init_db()
        init_db()


# ============================================================
# ConversationStore
# ============================================================

class TestConversationStore:
    def test_create_conversation(self):
        from ben.memory.models import ConversationStore
        cid = ConversationStore.create_conversation(title="Test Session")
        row = ConversationStore.get_conversation(cid)
        assert row is not None
        assert row["title"] == "Test Session"

    def test_ensure_conversation_idempotent(self):
        from ben.memory.models import ConversationStore
        sid = "fixed-session-123"
        ConversationStore.ensure_conversation(sid)
        ConversationStore.ensure_conversation(sid)  # must not raise or duplicate
        assert ConversationStore.get_conversation(sid) is not None

    def test_save_and_load_messages(self):
        from ben.memory.models import ConversationStore
        from ben.core.conversation import ConversationHistory
        hist = ConversationHistory()
        ConversationStore.ensure_conversation(hist.session_id)
        u = hist.add_user("Hello BEN")
        a = hist.add_assistant("Hello back!")
        ConversationStore.save_message(hist.session_id, u)
        ConversationStore.save_message(hist.session_id, a)

        loaded = ConversationStore.load_messages(hist.session_id)
        assert len(loaded) == 2
        assert loaded[0].content == "Hello BEN"
        assert loaded[1].content == "Hello back!"

    def test_load_history_reconstructs(self):
        from ben.memory.models import ConversationStore
        from ben.core.conversation import ConversationHistory
        hist = ConversationHistory()
        ConversationStore.ensure_conversation(hist.session_id)
        ConversationStore.save_message(hist.session_id, hist.add_user("Q1"))
        ConversationStore.save_message(hist.session_id, hist.add_assistant("A1"))

        restored = ConversationStore.load_history(hist.session_id)
        assert restored.message_count == 2
        assert restored.session_id == hist.session_id

    def test_update_message_content(self):
        from ben.memory.models import ConversationStore
        from ben.core.conversation import ConversationHistory
        hist = ConversationHistory()
        ConversationStore.ensure_conversation(hist.session_id)
        msg = hist.add_assistant("Draft")
        ConversationStore.save_message(hist.session_id, msg)
        ConversationStore.update_message_content(msg.id, "Final text")
        loaded = ConversationStore.load_messages(hist.session_id)
        assert loaded[0].content == "Final text"

    def test_list_conversations(self):
        from ben.memory.models import ConversationStore
        ConversationStore.create_conversation(title="Alpha")
        ConversationStore.create_conversation(title="Beta")
        all_convs = ConversationStore.list_conversations()
        assert len(all_convs) >= 2

    def test_delete_conversation_cascades(self):
        from ben.memory.models import ConversationStore
        from ben.core.conversation import ConversationHistory
        hist = ConversationHistory()
        ConversationStore.ensure_conversation(hist.session_id)
        ConversationStore.save_message(hist.session_id, hist.add_user("hi"))
        deleted = ConversationStore.delete_conversation(hist.session_id)
        assert deleted is True
        msgs = ConversationStore.load_messages(hist.session_id)
        assert msgs == []

    def test_get_message_count(self):
        from ben.memory.models import ConversationStore
        from ben.core.conversation import ConversationHistory
        hist = ConversationHistory()
        ConversationStore.ensure_conversation(hist.session_id)
        ConversationStore.save_message(hist.session_id, hist.add_user("one"))
        ConversationStore.save_message(hist.session_id, hist.add_assistant("two"))
        assert ConversationStore.get_message_count(hist.session_id) == 2


# ============================================================
# CoreMemoryStore
# ============================================================

class TestCoreMemoryStore:
    def test_create_memory(self):
        from ben.memory.models import CoreMemoryStore
        mem = CoreMemoryStore.create("goal", "Learn Rust in 3 months")
        assert mem["id"]
        assert mem["category"] == "goal"
        assert mem["content"] == "Learn Rust in 3 months"
        assert mem["active"] == 1

    def test_create_invalid_category(self):
        from ben.memory.models import CoreMemoryStore
        with pytest.raises(ValueError, match="Invalid category"):
            CoreMemoryStore.create("habit", "Exercise daily")

    def test_create_empty_content(self):
        from ben.memory.models import CoreMemoryStore
        with pytest.raises(ValueError, match="empty"):
            CoreMemoryStore.create("note", "   ")

    def test_create_content_too_long(self):
        from ben.memory.models import CoreMemoryStore
        with pytest.raises(ValueError, match="too long"):
            CoreMemoryStore.create("note", "x" * 2001)

    def test_get_memory(self):
        from ben.memory.models import CoreMemoryStore
        mem = CoreMemoryStore.create("preference", "Dark mode always")
        fetched = CoreMemoryStore.get(mem["id"])
        assert fetched["content"] == "Dark mode always"

    def test_get_nonexistent(self):
        from ben.memory.models import CoreMemoryStore
        assert CoreMemoryStore.get("nonexistent-id-xyz") is None

    def test_list_all(self):
        from ben.memory.models import CoreMemoryStore
        CoreMemoryStore.create("goal", "Goal A")
        CoreMemoryStore.create("note", "Note B")
        all_mem = CoreMemoryStore.list_all()
        assert len(all_mem) >= 2

    def test_list_active_only(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("context", "Active item")
        CoreMemoryStore.update(m["id"], active=False)
        CoreMemoryStore.create("context", "Another active")
        active = CoreMemoryStore.list_all(active_only=True)
        ids = [a["id"] for a in active]
        assert m["id"] not in ids

    def test_list_by_category(self):
        from ben.memory.models import CoreMemoryStore
        CoreMemoryStore.create("goal", "Goal X")
        CoreMemoryStore.create("note", "Note Y")
        goals = CoreMemoryStore.list_all(category="goal")
        assert all(g["category"] == "goal" for g in goals)

    def test_update_content(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("note", "Old content")
        updated = CoreMemoryStore.update(m["id"], content="New content")
        assert updated["content"] == "New content"

    def test_update_category(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("note", "Some text")
        updated = CoreMemoryStore.update(m["id"], category="goal")
        assert updated["category"] == "goal"

    def test_update_active_flag(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("note", "A note")
        updated = CoreMemoryStore.update(m["id"], active=False)
        assert updated["active"] == 0

    def test_update_invalid_category(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("note", "Text")
        with pytest.raises(ValueError, match="Invalid category"):
            CoreMemoryStore.update(m["id"], category="habit")

    def test_delete_memory(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("note", "To delete")
        assert CoreMemoryStore.delete(m["id"]) is True
        assert CoreMemoryStore.get(m["id"]) is None

    def test_delete_nonexistent(self):
        from ben.memory.models import CoreMemoryStore
        assert CoreMemoryStore.delete("does-not-exist") is False

    def test_toggle_active(self):
        from ben.memory.models import CoreMemoryStore
        m = CoreMemoryStore.create("note", "Toggle me")
        assert m["active"] == 1
        toggled = CoreMemoryStore.toggle_active(m["id"])
        assert toggled["active"] == 0
        toggled2 = CoreMemoryStore.toggle_active(m["id"])
        assert toggled2["active"] == 1

    def test_toggle_nonexistent(self):
        from ben.memory.models import CoreMemoryStore
        assert CoreMemoryStore.toggle_active("no-such-id") is None


# ============================================================
# build_memory_context
# ============================================================

class TestMemoryContext:
    def test_no_memories_returns_base(self):
        from ben.memory.models import build_memory_context
        base = "You are BEN."
        result = build_memory_context(base)
        assert result == base

    def test_injects_active_memories(self):
        from ben.memory.models import CoreMemoryStore, build_memory_context
        CoreMemoryStore.create("goal", "Pass the AWS exam")
        CoreMemoryStore.create("preference", "Concise answers")
        result = build_memory_context("You are BEN.")
        assert "Pass the AWS exam" in result
        assert "Concise answers" in result
        assert "LONG-TERM MEMORY" in result

    def test_excludes_inactive_memories(self):
        from ben.memory.models import CoreMemoryStore, build_memory_context
        m = CoreMemoryStore.create("note", "Inactive note")
        CoreMemoryStore.update(m["id"], active=False)
        result = build_memory_context("You are BEN.")
        assert "Inactive note" not in result


# ============================================================
# Memory API endpoints (FastAPI TestClient)
# ============================================================

@pytest.fixture
def api_client():
    from ben.app import app
    from fastapi.testclient import TestClient

    # Patch DB init to use already-initialised temp DB
    with patch("ben.app._init_db", return_value=None):
        with TestClient(app) as c:
            yield c


class TestMemoryAPIEndpoints:
    def test_create_and_list(self, api_client):
        res = api_client.post("/api/memory/", json={"category": "goal", "content": "Ship BEN v1"})
        assert res.status_code == 201
        data = res.json()
        assert data["content"] == "Ship BEN v1"

        lst = api_client.get("/api/memory/")
        assert any(m["content"] == "Ship BEN v1" for m in lst.json())

    def test_create_invalid_category(self, api_client):
        res = api_client.post("/api/memory/", json={"category": "habit", "content": "Run daily"})
        assert res.status_code == 422

    def test_get_memory(self, api_client):
        created = api_client.post("/api/memory/", json={"category": "note", "content": "A note"}).json()
        fetched = api_client.get(f"/api/memory/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["content"] == "A note"

    def test_get_nonexistent(self, api_client):
        res = api_client.get("/api/memory/does-not-exist")
        assert res.status_code == 404

    def test_update_memory(self, api_client):
        created = api_client.post("/api/memory/", json={"category": "note", "content": "Old"}).json()
        updated = api_client.patch(f"/api/memory/{created['id']}", json={"content": "New"})
        assert updated.status_code == 200
        assert updated.json()["content"] == "New"

    def test_delete_memory(self, api_client):
        created = api_client.post("/api/memory/", json={"category": "note", "content": "Bye"}).json()
        res = api_client.delete(f"/api/memory/{created['id']}")
        assert res.status_code == 200
        assert res.json()["deleted"] is True

    def test_toggle_memory(self, api_client):
        created = api_client.post("/api/memory/", json={"category": "goal", "content": "Toggle"}).json()
        toggled = api_client.post(f"/api/memory/{created['id']}/toggle")
        assert toggled.status_code == 200
        assert toggled.json()["active"] == 0

    def test_list_active_only(self, api_client):
        m = api_client.post("/api/memory/", json={"category": "note", "content": "inactive"}).json()
        api_client.patch(f"/api/memory/{m['id']}", json={"active": False})
        api_client.post("/api/memory/", json={"category": "note", "content": "active"})
        lst = api_client.get("/api/memory/?active_only=true")
        assert all(x["active"] == 1 for x in lst.json())

    def test_list_conversation_history(self, api_client):
        res = api_client.get("/api/memory/conversations/")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_delete_nonexistent_conversation(self, api_client):
        res = api_client.delete("/api/memory/conversations/no-such-session")
        assert res.status_code == 404


# ============================================================
# Thread-safety: concurrent writes
# ============================================================

class TestConcurrentAccess:
    def test_concurrent_memory_creates(self):
        """Multiple threads can write core_memories without corruption."""
        from ben.memory.models import CoreMemoryStore
        from ben.memory.database import get_db, _local

        errors = []

        def write(i):
            try:
                # Each thread needs its own connection
                if hasattr(_local, "conn") and _local.conn:
                    _local.conn.close()
                    _local.conn = None
                CoreMemoryStore.create("note", f"Thread {i} note")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        all_mem = CoreMemoryStore.list_all()
        assert len(all_mem) >= 8
