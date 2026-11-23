"""Service layer for user profile and memory context — Phase 3.5.

Exposes:
- get_user_context(user_id): asynchronous context retrieval
- save_user_profile(user_id, name, role, interests): profile persistence
- load_user_profile(user_id): synchronous profile loader
- build_personalized_instruction(base_instruction, profile): injects user context
"""

import asyncio
from typing import Any, Dict, Optional

from ben.memory.db import (
    load_user_profile as _db_load_profile,
    save_user_profile as _db_save_profile,
)


def save_user_profile(
    user_id: str = "hasan",
    name: str = "Sayed Hasan",
    role: str = "boss",
    interests: str = "AI, entrepreneurship, programming, automation, productivity",
) -> Dict[str, Any]:
    """Save user profile to SQLite database (synchronous)."""
    return _db_save_profile(user_id=user_id, name=name, role=role, interests=interests)


def load_user_profile(user_id: str = "hasan") -> Optional[Dict[str, Any]]:
    """Load user profile from SQLite database (synchronous)."""
    return _db_load_profile(user_id=user_id)


async def get_user_context(user_id: str = "hasan") -> Optional[Dict[str, Any]]:
    """Asynchronously load user profile context for chat and prompt enrichment."""
    return await asyncio.to_thread(load_user_profile, user_id)


def build_personalized_instruction(
    base_instruction: str,
    profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Enrich the system instruction with the user's profile, role, and interests."""
    if not profile:
        profile = load_user_profile()

    if not profile:
        return base_instruction

    name = profile.get("name", "Sayed Hasan")
    role = profile.get("role", "boss")
    interests = profile.get("interests", "AI, entrepreneurship, programming, automation, productivity")

    user_block = (
        f"You are BEN, personal AI assistant for {name} (first-year BCA student "
        f"interested in {interests}). Greet him by name and acknowledge his role as your {role} "
        f"(e.g., 'Hello boss Hasan! Ready to help with your AI projects?'). "
        f"He uses you for studying, programming, and building projects. "
        f"You have access to these tools: ListFiles, ReadFile, OpenApp, DetectDevices. "
        f"When the user asks to open an app, list files, read a file, or find devices, "
        f"choose the appropriate tool. You are voice-activated with the codeword 'aslamlyekum'. "
        f"Greet Hasan by name when he starts voice mode."
    )

    return f"{user_block}\n\n{base_instruction}"
