"""FastAPI router for BEN core memory management (Phase 3).

Endpoints
---------
GET    /api/memory/                       – List all core memories
POST   /api/memory/                       – Create a new core memory
GET    /api/memory/{id}                   – Get a single memory
PATCH  /api/memory/{id}                   – Edit a memory
DELETE /api/memory/{id}                   – Delete a memory
POST   /api/memory/{id}/toggle            – Toggle active/inactive

GET    /api/memory/conversations/          – List past conversation sessions
GET    /api/memory/conversations/{id}      – Get conversation + messages
DELETE /api/memory/conversations/{id}      – Delete a conversation
"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ben.memory.models import CoreMemoryStore, ConversationStore, VALID_CATEGORIES
from ben.utils.logger import logger


router = APIRouter(prefix="/api/memory", tags=["memory"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class MemoryCreate(BaseModel):
    category: str = Field(default="note", description="One of: preference, goal, project, context, note")
    content: str = Field(..., min_length=1, max_length=2000)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category. Must be one of: {sorted(VALID_CATEGORIES)}")
        return v

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Content cannot be empty.")
        return stripped


class MemoryUpdate(BaseModel):
    content: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = None
    active: Optional[bool] = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip().lower()
            if v not in VALID_CATEGORIES:
                raise ValueError(f"Invalid category. Must be one of: {sorted(VALID_CATEGORIES)}")
        return v

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            stripped = v.strip()
            if not stripped:
                raise ValueError("Content cannot be empty.")
            return stripped
        return v


# ---------------------------------------------------------------------------
# Core Memory Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[Dict[str, Any]])
async def list_memories(active_only: bool = False, category: Optional[str] = None):
    """List all core memories, optionally filtered."""
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"Invalid category. Choose from: {sorted(VALID_CATEGORIES)}")
    return await asyncio.to_thread(CoreMemoryStore.list_all, active_only, category)


@router.post("/", response_model=Dict[str, Any], status_code=201)
async def create_memory(body: MemoryCreate):
    """Create a new user-approved core memory."""
    try:
        return await asyncio.to_thread(CoreMemoryStore.create, body.category, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{memory_id}", response_model=Dict[str, Any])
async def get_memory(memory_id: str):
    """Retrieve a single core memory by ID."""
    mem = await asyncio.to_thread(CoreMemoryStore.get, memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return mem


@router.patch("/{memory_id}", response_model=Dict[str, Any])
async def update_memory(memory_id: str, body: MemoryUpdate):
    """Partially update a core memory's content, category, or active status."""
    try:
        mem = await asyncio.to_thread(
            CoreMemoryStore.update,
            memory_id, body.content, body.category, body.active
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if mem is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return mem


@router.delete("/{memory_id}", status_code=200)
async def delete_memory(memory_id: str):
    """Permanently delete a core memory."""
    deleted = await asyncio.to_thread(CoreMemoryStore.delete, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"deleted": True, "id": memory_id}


@router.post("/{memory_id}/toggle", response_model=Dict[str, Any])
async def toggle_memory(memory_id: str):
    """Toggle a core memory between active and inactive."""
    mem = await asyncio.to_thread(CoreMemoryStore.toggle_active, memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return mem


# ---------------------------------------------------------------------------
# Conversation History Endpoints
# ---------------------------------------------------------------------------

@router.get("/conversations/", response_model=List[Dict[str, Any]])
async def list_conversations(limit: int = 50):
    """List past conversation sessions (most recent first)."""
    return await asyncio.to_thread(ConversationStore.list_conversations, limit)


@router.get("/conversations/{session_id}", response_model=Dict[str, Any])
async def get_conversation(session_id: str):
    """Get a conversation record and all its messages."""
    conv = await asyncio.to_thread(ConversationStore.get_conversation, session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = await asyncio.to_thread(ConversationStore.load_messages, session_id)
    conv["messages"] = [
        {
            "id": m.id,
            "role": m.role.value,
            "content": m.content,
            "is_error": m.is_error,
            "timestamp": m.timestamp.isoformat(),
        }
        for m in messages
    ]
    conv["message_count"] = len(messages)
    return conv


@router.delete("/conversations/{session_id}", status_code=200)
async def delete_conversation(session_id: str):
    """Delete a conversation and all its messages."""
    deleted = await asyncio.to_thread(ConversationStore.delete_conversation, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True, "session_id": session_id}
