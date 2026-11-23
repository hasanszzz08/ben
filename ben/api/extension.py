"""FastAPI extension router for BEN — Phase 5 (Chrome Extension Backend).

Endpoints
---------
GET  /api/extension/status        – Health check for the extension to ping
POST /api/extension/summarize     – Summarize extracted webpage text
POST /api/extension/explain       – Explain selected text from a webpage
POST /api/extension/save-note     – Save a note/fact directly to core_memories
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ben.config import settings
from ben.core.client import AnthropicClientManager
from ben.memory.models import CoreMemoryStore, VALID_CATEGORIES, build_memory_context
from ben.utils.logger import logger

router = APIRouter(prefix="/api/extension", tags=["extension"])

_client_mgr = AnthropicClientManager()

# ---------------------------------------------------------------------------
# Security constants — treat all webpage/user-submitted text as untrusted
# ---------------------------------------------------------------------------
MAX_WEBPAGE_CHARS = 12_000   # ~3k tokens; well within Gemini context limits
MAX_TEXT_CHARS = 4_000       # explain selection limit
MAX_NOTE_CHARS = 2_000       # same as CoreMemoryStore limit
MAX_URL_CHARS = 2_048        # standard URL length limit

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SummarizeRequest(BaseModel):
    """Summarize the readable text of a webpage."""
    url: str = Field(..., max_length=MAX_URL_CHARS, description="Source URL (for context only, not fetched)")
    content: str = Field(..., min_length=1, max_length=MAX_WEBPAGE_CHARS,
                         description="Extracted readable text from document.body.innerText")
    max_length: Optional[int] = Field(default=None, ge=50, le=2000,
                                      description="Requested word count for the summary")

    @field_validator("content")
    @classmethod
    def strip_and_validate_content(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Webpage content cannot be empty or whitespace-only.")
        # Truncate silently at the byte boundary (already enforced by max_length,
        # but sanitize any residual whitespace bursts that inflate token count)
        return " ".join(stripped.split())

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("URL cannot be empty.")
        return stripped


class SummarizeResponse(BaseModel):
    summary: str
    url: str
    word_count: int
    model: str


class ExplainRequest(BaseModel):
    """Explain selected/highlighted text from a webpage."""
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS,
                      description="Selected text to explain")
    context_url: Optional[str] = Field(default=None, max_length=MAX_URL_CHARS,
                                        description="URL the selection came from (optional context)")

    @field_validator("text")
    @classmethod
    def strip_and_validate_text(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Text cannot be empty or whitespace-only.")
        return stripped


class ExplainResponse(BaseModel):
    explanation: str
    original_text_length: int
    model: str


class SaveNoteRequest(BaseModel):
    """Save a highlighted fact or note to BEN's core memory."""
    content: str = Field(..., min_length=1, max_length=MAX_NOTE_CHARS,
                         description="Content to save as a core memory")
    category: str = Field(default="note",
                          description=f"Memory category: {sorted(VALID_CATEGORIES)}")
    source_url: Optional[str] = Field(default=None, max_length=MAX_URL_CHARS,
                                       description="Source URL (appended to content for traceability)")

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Note content cannot be empty.")
        return stripped

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{v}'. Must be one of: {sorted(VALID_CATEGORIES)}"
            )
        return v


class SaveNoteResponse(BaseModel):
    saved: bool
    memory_id: str
    category: str
    content_preview: str   # first 80 chars — never the full key/secret


class StatusResponse(BaseModel):
    status: str
    model: str
    key_configured: bool
    version: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_error(exc: Exception) -> str:
    """Return error string with API key redacted."""
    msg = str(exc)
    key = settings.api_key or ""
    if key and key in msg:
        msg = msg.replace(key, "[REDACTED]")
    return msg


def _build_summarize_prompt(url: str, content: str, max_length: Optional[int]) -> str:
    length_hint = f" Target length: approximately {max_length} words." if max_length else ""
    return (
        f"Summarize the following webpage content in a clear, structured way.{length_hint}\n"
        f"Use bullet points where appropriate. Start with a one-sentence overview.\n\n"
        f"Source URL: {url}\n\n"
        f"--- WEBPAGE CONTENT (extracted text) ---\n{content}\n--- END CONTENT ---"
    )


def _build_explain_prompt(text: str, context_url: Optional[str]) -> str:
    ctx = f"\n(From: {context_url})" if context_url else ""
    return (
        f"Explain the following text clearly and concisely.{ctx}\n"
        f"Provide context, define terms, and highlight why it's significant if relevant.\n\n"
        f"--- SELECTED TEXT ---\n{text}\n--- END TEXT ---"
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=StatusResponse)
async def extension_status():
    """Lightweight health-check for the Chrome extension to verify connectivity."""
    return StatusResponse(
        status="online" if _client_mgr.is_configured else "unconfigured",
        model=_client_mgr.default_model,
        key_configured=_client_mgr.is_configured,
        version="0.5.0",
    )


@router.post("/summarize", response_model=SummarizeResponse)
async def summarize_webpage(req: SummarizeRequest):
    """Accept extracted webpage text and return a structured AI summary.

    The API key never leaves the backend.  Webpage content is treated as
    untrusted input and is already length-limited by the Pydantic model.
    """
    if not _client_mgr.is_configured:
        raise HTTPException(status_code=503, detail="BEN is not configured. Set ANTHROPIC_API_KEY in .env.")

    prompt = _build_summarize_prompt(req.url, req.content, req.max_length)

    try:
        sys_instruction = await asyncio.to_thread(
            build_memory_context, settings.ben_system_instruction
        )
        summary = await asyncio.to_thread(
            _client_mgr.generate,
            prompt,
            None,          # use default model
            sys_instruction,
            500,           # max_tokens for summary
            0.4,           # lower temperature for factual summaries
        )
        summary = summary.strip() or "(no summary generated)"
    except Exception as exc:
        error_detail = _safe_error(exc)
        logger.error(f"Summarize failed: {error_detail}")
        raise HTTPException(status_code=502, detail=f"Anthropic Claude API error: {error_detail}")

    word_count = len(summary.split())
    logger.info(f"Summarized '{req.url}' → {word_count} words")

    return SummarizeResponse(
        summary=summary,
        url=req.url,
        word_count=word_count,
        model=_client_mgr.default_model,
    )


@router.post("/explain", response_model=ExplainResponse)
async def explain_text(req: ExplainRequest):
    """Accept highlighted text and return a plain-language explanation.

    The API key never leaves the backend.  Selected text is treated as
    untrusted input and is already length-limited by the Pydantic model.
    """
    if not _client_mgr.is_configured:
        raise HTTPException(status_code=503, detail="BEN is not configured. Set ANTHROPIC_API_KEY in .env.")

    prompt = _build_explain_prompt(req.text, req.context_url)

    try:
        sys_instruction = await asyncio.to_thread(
            build_memory_context, settings.ben_system_instruction
        )
        explanation = await asyncio.to_thread(
            _client_mgr.generate,
            prompt,
            None,
            sys_instruction,
            0.5,
        )
        explanation = explanation.strip() or "(no explanation generated)"
    except Exception as exc:
        error_detail = _safe_error(exc)
        logger.error(f"Explain failed: {error_detail}")
        raise HTTPException(status_code=502, detail=f"AI explanation error: {error_detail}")

    logger.info(f"Explained {len(req.text)}-char selection from {req.context_url or 'unknown'}")

    return ExplainResponse(
        explanation=explanation,
        original_text_length=len(req.text),
        model=_client_mgr.default_model,
    )


@router.post("/save-note", response_model=SaveNoteResponse)
async def save_note(req: SaveNoteRequest):
    """Save highlighted text or a quick fact to BEN's SQLite core memory.

    Optionally appends the source URL to make the memory traceable.
    """
    # Build the content string, optionally tagging the source URL
    content = req.content
    if req.source_url:
        source_tag = f" [source: {req.source_url}]"
        # Truncate to stay within the 2000-char DB limit after tagging
        max_base = MAX_NOTE_CHARS - len(source_tag)
        if len(content) > max_base:
            content = content[:max_base].rstrip()
        content = content + source_tag

    try:
        record = await asyncio.to_thread(
            CoreMemoryStore.create, req.category, content
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        error_detail = _safe_error(exc)
        logger.error(f"Save-note DB error: {error_detail}")
        raise HTTPException(status_code=500, detail="Failed to save note to memory.")

    preview = record["content"][:80] + ("…" if len(record["content"]) > 80 else "")
    logger.info(f"Saved {req.category} memory from extension: {preview}")

    return SaveNoteResponse(
        saved=True,
        memory_id=record["id"],
        category=record["category"],
        content_preview=preview,
    )
