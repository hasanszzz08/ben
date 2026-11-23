"""Phase 5 tests — BEN Chrome Extension Backend Endpoints.

Tests:
  GET  /api/extension/status         (health check)
  POST /api/extension/summarize      (valid, too-long, empty)
  POST /api/extension/explain        (valid, too-long, empty)
  POST /api/extension/save-note      (valid, invalid category, empty content)
  Security: API key never exposed in any response
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from ben.app import app

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Constants matching extension.py limits
# ---------------------------------------------------------------------------
MAX_WEBPAGE   = 12_000
MAX_TEXT      = 4_000
MAX_NOTE      = 2_000
MAX_URL       = 2_048

VALID_URL     = "https://example.com/article"
SAMPLE_TEXT   = "Photosynthesis is the process by which plants convert sunlight into glucose."
SAMPLE_PAGE   = " ".join(["BEN is a personal AI agent. " * 50])     # ~1 350 chars
LONG_WEBPAGE  = "x " * (MAX_WEBPAGE // 2 + 10)                      # > 12 000 chars
LONG_TEXT     = "y " * (MAX_TEXT  // 2 + 10)                        # > 4 000 chars
LONG_NOTE     = "z " * (MAX_NOTE  // 2 + 10)                        # > 2 000 chars


from unittest.mock import patch
from ben.core.client import AnthropicClientManager

@pytest_asyncio.fixture
async def client():
    """Shared AsyncClient across all tests with mocked AI responses (0 API cost)."""
    from ben.api import extension as ext_module

    orig_key = ext_module._client_mgr._api_key
    ext_module._client_mgr._api_key = "sk-ant-test-key-12345"

    with patch.object(AnthropicClientManager, "generate", return_value="This is a structured AI response from Claude."):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as c:
            yield c

    ext_module._client_mgr._api_key = orig_key


# ===========================================================================
# GET /api/extension/status
# ===========================================================================

class TestExtensionStatus:

    @pytest.mark.asyncio
    async def test_status_returns_200(self, client):
        resp = await client.get("/api/extension/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_status_schema(self, client):
        data = (await client.get("/api/extension/status")).json()
        assert "status" in data
        assert "model" in data
        assert "key_configured" in data
        assert "version" in data

    @pytest.mark.asyncio
    async def test_status_no_api_key_in_response(self, client):
        """API key must never appear in any response body."""
        resp = await client.get("/api/extension/status")
        from ben.config import settings
        key = settings.api_key or ""
        if key:
            assert key not in resp.text

    @pytest.mark.asyncio
    async def test_status_version_is_string(self, client):
        data = (await client.get("/api/extension/status")).json()
        assert isinstance(data["version"], str)
        assert data["version"]  # non-empty


# ===========================================================================
# POST /api/extension/summarize
# ===========================================================================

class TestExtensionSummarize:

    @pytest.mark.asyncio
    async def test_summarize_valid_returns_200(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": SAMPLE_PAGE,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_summarize_response_schema(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": SAMPLE_PAGE,
        })
        if resp.status_code == 200:
            data = resp.json()
            assert "summary" in data
            assert "url" in data
            assert "word_count" in data
            assert "model" in data
            assert isinstance(data["word_count"], int)

    @pytest.mark.asyncio
    async def test_summarize_content_too_long_returns_422(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": LONG_WEBPAGE,
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_summarize_empty_content_returns_422(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": "",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_summarize_whitespace_only_content_returns_422(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": "   \n\t  ",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_summarize_empty_url_returns_422(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": "",
            "content": SAMPLE_PAGE,
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_summarize_url_too_long_returns_422(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": "https://example.com/" + "a" * MAX_URL,
            "content": SAMPLE_PAGE,
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_summarize_no_api_key_in_response(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": SAMPLE_PAGE,
        })
        from ben.config import settings
        key = settings.api_key or ""
        if key:
            assert key not in resp.text

    @pytest.mark.asyncio
    async def test_summarize_accepts_max_length_hint(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "url": VALID_URL,
            "content": SAMPLE_PAGE,
            "max_length": 200,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_summarize_missing_url_returns_422(self, client):
        resp = await client.post("/api/extension/summarize", json={
            "content": SAMPLE_PAGE,
        })
        assert resp.status_code == 422


# ===========================================================================
# POST /api/extension/explain
# ===========================================================================

class TestExtensionExplain:

    @pytest.mark.asyncio
    async def test_explain_valid_returns_200(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": SAMPLE_TEXT,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_explain_response_schema(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": SAMPLE_TEXT,
        })
        if resp.status_code == 200:
            data = resp.json()
            assert "explanation" in data
            assert "original_text_length" in data
            assert "model" in data
            assert data["original_text_length"] == len(SAMPLE_TEXT)

    @pytest.mark.asyncio
    async def test_explain_text_too_long_returns_422(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": LONG_TEXT,
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_explain_empty_text_returns_422(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": "",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_explain_whitespace_text_returns_422(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": "   ",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_explain_with_context_url(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": SAMPLE_TEXT,
            "context_url": VALID_URL,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_explain_no_api_key_in_response(self, client):
        resp = await client.post("/api/extension/explain", json={
            "text": SAMPLE_TEXT,
        })
        from ben.config import settings
        key = settings.api_key or ""
        if key:
            assert key not in resp.text

    @pytest.mark.asyncio
    async def test_explain_missing_text_returns_422(self, client):
        resp = await client.post("/api/extension/explain", json={
            "context_url": VALID_URL,
        })
        assert resp.status_code == 422


# ===========================================================================
# POST /api/extension/save-note
# ===========================================================================

class TestExtensionSaveNote:

    @pytest.mark.asyncio
    async def test_save_note_valid_note(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "BEN is a personal AI agent built with FastAPI.",
            "category": "note",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert "memory_id" in data
        assert data["category"] == "note"
        assert "content_preview" in data

    @pytest.mark.asyncio
    async def test_save_note_valid_goal(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "Complete Phase 5 Chrome Extension.",
            "category": "goal",
        })
        assert resp.status_code == 200
        assert resp.json()["category"] == "goal"

    @pytest.mark.asyncio
    async def test_save_note_valid_project(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "BEN project — multi-phase AI agent.",
            "category": "project",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_save_note_valid_preference(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "Prefer concise responses.",
            "category": "preference",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_save_note_valid_context(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "Currently studying FastAPI async patterns.",
            "category": "context",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_save_note_with_source_url(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "Important fact from the article.",
            "category": "note",
            "source_url": VALID_URL,
        })
        assert resp.status_code == 200
        # Source URL should be tagged in the preview
        data = resp.json()
        assert data["saved"] is True

    @pytest.mark.asyncio
    async def test_save_note_invalid_category_returns_422(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "Some content.",
            "category": "INVALID_CATEGORY",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_save_note_empty_content_returns_422(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "",
            "category": "note",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_save_note_whitespace_content_returns_422(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "   ",
            "category": "note",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_save_note_content_too_long_returns_422(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": LONG_NOTE,
            "category": "note",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_save_note_content_preview_max_80_chars(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "A" * 200,
            "category": "note",
        })
        assert resp.status_code == 200
        preview = resp.json()["content_preview"]
        # Preview is 80 chars + optional ellipsis "…" (1 char)
        assert len(preview) <= 82

    @pytest.mark.asyncio
    async def test_save_note_no_api_key_in_response(self, client):
        resp = await client.post("/api/extension/save-note", json={
            "content": "Test note for security check.",
            "category": "note",
        })
        from ben.config import settings
        key = settings.api_key or ""
        if key:
            assert key not in resp.text


# ===========================================================================
# Cross-cutting: API key never exposed
# ===========================================================================

class TestSecurityApiKeyNeverExposed:

    @pytest.mark.asyncio
    async def test_status_no_key(self, client):
        from ben.config import settings
        key = settings.api_key or ""
        resp = await client.get("/api/extension/status")
        if key:
            assert key not in resp.text

    @pytest.mark.asyncio
    async def test_save_note_no_key(self, client):
        from ben.config import settings
        key = settings.api_key or ""
        resp = await client.post("/api/extension/save-note", json={
            "content": "Security test note.",
            "category": "note",
        })
        if key:
            assert key not in resp.text
