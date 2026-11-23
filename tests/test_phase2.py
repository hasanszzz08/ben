"""Tests for BEN Phase 2: Conversation history, chat API, validation, and streaming."""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from ben.core.conversation import ConversationHistory, Role, Message


# ============================================================
# ConversationHistory unit tests
# ============================================================

class TestConversationHistory:
    def test_empty_on_init(self):
        h = ConversationHistory()
        assert h.is_empty
        assert h.message_count == 0
        assert isinstance(h.session_id, str)
        assert len(h.session_id) == 36  # UUID format

    def test_add_user_message(self):
        h = ConversationHistory()
        msg = h.add_user("Hello BEN!")
        assert msg.role == Role.USER
        assert msg.content == "Hello BEN!"
        assert h.message_count == 1

    def test_add_assistant_message(self):
        h = ConversationHistory()
        h.add_user("Hi")
        msg = h.add_assistant("Hello, I'm BEN.")
        assert msg.role == Role.ASSISTANT
        assert not msg.is_error

    def test_whitespace_stripped(self):
        h = ConversationHistory()
        msg = h.add_user("  Hello  ")
        assert msg.content == "Hello"

    def test_empty_message_raises(self):
        h = ConversationHistory()
        with pytest.raises(ValueError, match="empty"):
            h.add_user("   ")

    def test_message_too_long_raises(self):
        h = ConversationHistory()
        too_long = "x" * (ConversationHistory.MAX_MESSAGE_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            h.add_user(too_long)

    def test_clear_resets_session(self):
        h = ConversationHistory()
        old_id = h.session_id
        h.add_user("Hello")
        h.add_assistant("Hi")
        h.clear()
        assert h.is_empty
        assert h.session_id != old_id

    def test_replace_last_assistant(self):
        h = ConversationHistory()
        h.add_user("Tell me something")
        h.add_assistant("Draft text")
        h.replace_last_assistant("Final text")
        assert h.messages[-1].content == "Final text"

    def test_anthropic_messages_format(self):
        h = ConversationHistory()
        h.add_user("What is 2+2?")
        h.add_assistant("It's 4.")
        msgs = h.to_anthropic_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[0]["content"] == "What is 2+2?"

    def test_anthropic_messages_excludes_errors(self):
        h = ConversationHistory()
        h.add_user("Hello")
        h.add_assistant("Error response", is_error=True)
        msgs = h.to_anthropic_messages(exclude_last_user=True)
        # Error assistant message excluded, lone user excluded
        assert len(msgs) == 0

    def test_anthropic_messages_ends_with_assistant_when_excluded(self):
        """When exclude_last_user=True, lone trailing user messages are dropped."""
        h = ConversationHistory()
        h.add_user("Hello")  # lone user message
        msgs = h.to_anthropic_messages(exclude_last_user=True)
        assert len(msgs) == 0

    def test_anthropic_messages_with_multiple_turns(self):
        h = ConversationHistory()
        h.add_user("Q1")
        h.add_assistant("A1")
        h.add_user("Q2")
        h.add_assistant("A2")
        msgs = h.to_anthropic_messages()
        assert len(msgs) == 4
        assert msgs[-1]["role"] == "assistant"

    def test_gemini_history_backward_compatibility(self):
        h = ConversationHistory()
        h.add_user("Q1")
        h.add_assistant("A1")
        hist = h.to_gemini_history()
        assert len(hist) == 2

    def test_prune_on_overflow(self):
        h = ConversationHistory()
        h.MAX_HISTORY_MESSAGES = 4
        h.add_user("u1"); h.add_assistant("a1")
        h.add_user("u2"); h.add_assistant("a2")
        h.add_user("u3")  # this triggers pruning
        contents = [m.content for m in h.messages]
        assert "u1" not in contents


# ============================================================
# FastAPI chat endpoint tests (with mocked Claude)
# ============================================================

@pytest.fixture
def client():
    """Create a TestClient with mocked AnthropicClientManager."""
    from ben.app import app
    import ben.api.chat as chat_module

    mock_mgr = MagicMock()
    mock_mgr.is_configured = True
    mock_mgr.default_model = "claude-3-5-sonnet-20241022"
    mock_mgr.key_char_count = 53
    mock_mgr.generate.return_value = "Hello! I'm BEN, ready to assist."

    original_mgr = chat_module._client_mgr
    original_history = chat_module._history

    chat_module._client_mgr = mock_mgr
    chat_module._history = ConversationHistory()

    with TestClient(app) as c:
        yield c

    # Restore
    chat_module._client_mgr = original_mgr
    chat_module._history = original_history


class TestChatStatusEndpoint:
    def test_status_online(self, client):
        res = client.get("/api/chat/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "online"
        assert data["model"] == "claude-3-5-sonnet-20241022"
        assert data["key_configured"] is True

    def test_status_has_session_id(self, client):
        res = client.get("/api/chat/status")
        assert "session_id" in res.json()


class TestChatHistoryEndpoint:
    def test_empty_history(self, client):
        res = client.get("/api/chat/history")
        assert res.status_code == 200
        data = res.json()
        assert data["message_count"] == 0
        assert isinstance(data["messages"], list)

    def test_history_after_message(self, client):
        client.post("/api/chat/message", json={"message": "Hello"})
        res = client.get("/api/chat/history")
        data = res.json()
        assert data["message_count"] >= 2  # user + assistant

    def test_history_has_no_api_key(self, client):
        """History endpoint must never leak the API key."""
        client.post("/api/chat/message", json={"message": "Hello"})
        res = client.get("/api/chat/history")
        raw = res.text
        assert "ANTHROPIC_API_KEY" not in raw
        assert "GEMINI_API_KEY" not in raw


class TestChatMessageEndpoint:
    def test_send_message_success(self, client):
        res = client.post("/api/chat/message", json={"message": "Hello BEN!"})
        assert res.status_code == 200
        data = res.json()
        assert "reply" in data
        assert data["reply"] == "Hello! I'm BEN, ready to assist."
        assert data["model"] == "claude-3-5-sonnet-20241022"
        assert "session_id" in data

    def test_empty_message_rejected(self, client):
        res = client.post("/api/chat/message", json={"message": "   "})
        assert res.status_code == 422

    def test_missing_message_rejected(self, client):
        res = client.post("/api/chat/message", json={})
        assert res.status_code == 422

    def test_message_too_long_rejected(self, client):
        res = client.post("/api/chat/message", json={"message": "x" * 32001})
        assert res.status_code == 422

    def test_history_grows_with_turns(self, client):
        client.post("/api/chat/message", json={"message": "Hello"})
        client.post("/api/chat/message", json={"message": "What is Python?"})
        res = client.get("/api/chat/history")
        assert res.json()["message_count"] == 4  # 2 user + 2 assistant

    def test_claude_error_returns_502(self, client):
        import ben.api.chat as chat_module
        chat_module._client_mgr.generate.side_effect = Exception("Claude connection error")
        res = client.post("/api/chat/message", json={"message": "Hello"})
        assert res.status_code == 502
        # Restore
        chat_module._client_mgr.generate.side_effect = None
        chat_module._client_mgr.generate.return_value = "Hello! I'm BEN, ready to assist."


class TestChatClearEndpoint:
    def test_clear_resets_history(self, client):
        client.post("/api/chat/message", json={"message": "Hello"})
        client.post("/api/chat/clear", json={})
        res = client.get("/api/chat/history")
        assert res.json()["message_count"] == 0

    def test_clear_returns_new_session_id(self, client):
        status_before = client.get("/api/chat/status").json()["session_id"]
        client.post("/api/chat/clear", json={})
        status_after = client.get("/api/chat/status").json()["session_id"]
        assert status_before != status_after


class TestUIServing:
    def test_root_serves_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
        assert b"BEN" in res.content

    def test_api_docs_available(self, client):
        res = client.get("/api/docs")
        assert res.status_code == 200
