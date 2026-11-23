"""Phase 4 tests — Session switching, desktop imports, and memory panel integration."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from ben.core.conversation import ConversationHistory
from ben.memory.models import ConversationStore, CoreMemoryStore


@pytest.fixture(autouse=True)
def temp_database(tmp_path):
    """Ensure tests run against a fresh temporary database."""
    import ben.memory.database as db_module
    orig_path = db_module.DB_PATH
    db_module.DB_PATH = tmp_path / "test_ben_phase4.db"
    db_module.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(db_module._local, "conn") and db_module._local.conn:
        db_module._local.conn.close()
        db_module._local.conn = None

    db_module.init_db()
    yield

    if hasattr(db_module._local, "conn") and db_module._local.conn:
        db_module._local.conn.close()
        db_module._local.conn = None
    db_module.DB_PATH = orig_path


@pytest.fixture
def client():
    from ben.app import app
    import ben.api.chat as chat_module

    mock_mgr = MagicMock()
    mock_mgr.is_configured = True
    mock_mgr.default_model = "gemini-3.8-flash"
    mock_mgr.key_char_count = 53

    original_mgr = chat_module._client_mgr
    original_history = chat_module._history

    chat_module._client_mgr = mock_mgr
    chat_module._history = ConversationHistory()

    with TestClient(app) as c:
        yield c

    chat_module._client_mgr = original_mgr
    chat_module._history = original_history


class TestSessionSwitchingEndpoint:
    def test_switch_session_success(self, client):
        # Create an existing session in SQLite
        sid = "pre-existing-session-abc"
        ConversationStore.ensure_conversation(sid, title="Study Session")
        hist = ConversationHistory(session_id=sid)
        msg1 = hist.add_user("What is quantum computing?")
        msg2 = hist.add_assistant("It uses qubits.")
        ConversationStore.save_message(sid, msg1)
        ConversationStore.save_message(sid, msg2)

        # Switch to it via API
        res = client.post(f"/api/chat/switch/{sid}")
        assert res.status_code == 200
        data = res.json()
        assert data["switched"] is True
        assert data["session_id"] == sid
        assert data["message_count"] == 2

        # Verify active history in chat router now matches the switched session
        hist_res = client.get("/api/chat/history")
        assert hist_res.status_code == 200
        hist_data = hist_res.json()
        assert hist_data["session_id"] == sid
        assert hist_data["message_count"] == 2
        assert hist_data["messages"][0]["content"] == "What is quantum computing?"

    def test_switch_nonexistent_session_returns_404(self, client):
        res = client.post("/api/chat/switch/does-not-exist-999")
        assert res.status_code == 404


class TestDesktopModuleImports:
    def test_desktop_app_imports_cleanly(self):
        """Verify desktop.app can be imported and constants are valid."""
        import desktop.app as desktop_app
        assert hasattr(desktop_app, "BENDesktopWindow")
        assert hasattr(desktop_app, "BACKEND_URL")
        assert desktop_app.BACKEND_URL == "http://127.0.0.1:8000"
        assert callable(desktop_app.create_ben_icon)
