"""Conversation history manager for BEN chat sessions."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional
from enum import Enum

from pydantic import BaseModel, Field


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    """A single message in BEN's conversation."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: Role
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_error: bool = False


class ConversationHistory:
    """In-memory conversation history for a chat session.

    Maintains the full ordered message list and provides utilities
    to build context payloads for the Gemini API.

    Designed to be replaced or extended with SQLite persistence in Phase 3
    without changing the calling interface.
    """

    MAX_MESSAGE_LENGTH = 32_000   # characters per message
    MAX_HISTORY_MESSAGES = 100    # cap before pruning oldest pairs

    def __init__(self, session_id: Optional[str] = None):
        self.session_id: str = session_id or str(uuid.uuid4())
        self.messages: List[Message] = []
        self.created_at: datetime = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    #  Mutation                                                            #
    # ------------------------------------------------------------------ #

    def add_user(self, content: str) -> Message:
        """Append a user message and return it."""
        content = self._validate_content(content)
        msg = Message(role=Role.USER, content=content)
        self.messages.append(msg)
        self._prune()
        return msg

    def add_assistant(self, content: str, is_error: bool = False) -> Message:
        """Append an assistant (BEN) message and return it."""
        msg = Message(role=Role.ASSISTANT, content=content, is_error=is_error)
        self.messages.append(msg)
        return msg

    def replace_last_assistant(self, content: str) -> None:
        """Update the final assistant message in-place (used during streaming)."""
        for msg in reversed(self.messages):
            if msg.role == Role.ASSISTANT:
                msg.content = content
                return

    def clear(self) -> None:
        """Reset conversation to empty (new session)."""
        self.messages.clear()
        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    #  Query                                                               #
    # ------------------------------------------------------------------ #

    def to_anthropic_messages(self, exclude_last_user: bool = False) -> List[dict]:
        """Build the message list expected by Anthropic Claude Messages API.

        Returns alternating user/assistant turns:
        [{'role': 'user', 'content': '...'}, {'role': 'assistant', 'content': '...'}]
        System messages and error messages are excluded.
        Consecutive identical roles are merged to ensure strict turn alternation.
        """
        claude_messages: List[dict] = []
        for msg in self.messages:
            if msg.role == Role.SYSTEM or msg.is_error:
                continue
            role_str = "user" if msg.role == Role.USER else "assistant"
            if claude_messages and claude_messages[-1]["role"] == role_str:
                claude_messages[-1]["content"] += "\n" + msg.content
            else:
                claude_messages.append({"role": role_str, "content": msg.content})

        if exclude_last_user:
            while claude_messages and claude_messages[-1]["role"] == "user":
                claude_messages.pop()

        # Anthropic requires the first message in the list to be a user message
        while claude_messages and claude_messages[0]["role"] != "user":
            claude_messages.pop(0)

        return claude_messages

    def to_gemini_history(self) -> List[dict]:
        """Backward-compatible alias for existing tests and endpoints."""
        return self.to_anthropic_messages(exclude_last_user=True)

    @property
    def is_empty(self) -> bool:
        return len(self.messages) == 0

    @property
    def message_count(self) -> int:
        return len(self.messages)

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _validate_content(self, content: str) -> str:
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be empty.")
        if len(content) > self.MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"Message too long ({len(content)} chars). "
                f"Maximum allowed is {self.MAX_MESSAGE_LENGTH} characters."
            )
        return content

    def _prune(self) -> None:
        """Drop oldest user/assistant pairs if history exceeds the cap."""
        non_sys = [m for m in self.messages if m.role != Role.SYSTEM]
        if len(non_sys) > self.MAX_HISTORY_MESSAGES:
            # Remove the two oldest non-system messages to keep pairs
            count = 0
            for i, msg in enumerate(self.messages):
                if msg.role != Role.SYSTEM:
                    del self.messages[i]
                    count += 1
                    if count >= 2:
                        break
