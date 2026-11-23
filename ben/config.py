"""Configuration settings for BEN — Anthropic Claude API Integration."""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root path is guaranteed relative to this file location regardless of CWD
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# Explicitly load .env from the project directory with override=True to prevent stale system env vars
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)


KNOWN_PLACEHOLDERS = {
    "your_anthropic_api_key_here",
    "your_claude_api_key_here",
    "your_gemini_api_key_here",
    "your_actual_key_here",
    "your_actual_gemini_api_key_here",
    "test_key",
    "placeholder",
}


def sanitize_key(raw_key: Optional[str]) -> Optional[str]:
    """Sanitize and validate an API key without exposing it.

    Strips whitespace and surrounding quotation marks.
    Returns None if the key is empty or matches known template placeholders.
    """
    if not raw_key:
        return None
    cleaned = raw_key.strip().strip("'\"")
    if not cleaned or cleaned.lower() in KNOWN_PLACEHOLDERS:
        return None
    return cleaned


class Settings(BaseSettings):
    """BEN Application Settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Primary Anthropic Claude API Credentials
    anthropic_api_key: Optional[str] = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic Claude API Key"
    )

    # Fallback to API_KEY if ANTHROPIC_API_KEY is not set
    api_key_fallback: Optional[str] = Field(
        default=None,
        validation_alias="API_KEY",
        description="General API Key fallback"
    )

    # Legacy Gemini fallback (for migration grace)
    gemini_api_key_legacy: Optional[str] = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description="Legacy fallback key"
    )

    # Optional Custom Base URL (e.g. for proxy, local gateway, or custom endpoint)
    anthropic_base_url: Optional[str] = Field(
        default=None,
        validation_alias="ANTHROPIC_BASE_URL",
        description="Optional Anthropic Base URL"
    )

    base_url_fallback: Optional[str] = Field(
        default=None,
        validation_alias="BASE_URL",
        description="Fallback Base URL"
    )

    # Anthropic Model Configuration (official model: claude-3-5-sonnet-20241022)
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        validation_alias="ANTHROPIC_MODEL",
        description="Claude Model Identifier"
    )

    ben_model_fallback: Optional[str] = Field(
        default=None,
        validation_alias="BEN_MODEL",
        description="Fallback Model Identifier"
    )

    # Agent Persona
    ben_system_instruction: str = Field(
        default=(
            "You are BEN, a personal AI agent assisting with personal productivity, "
            "self-improvement, programming, studying, project management, and computer automation. "
            "You are precise, proactive, helpful, and concise."
        ),
        validation_alias="BEN_SYSTEM_INSTRUCTION",
        description="System instruction for the model"
    )

    # Network / Backend Security (localhost only by default)
    ben_env: str = Field(default="development", validation_alias="BEN_ENV")
    ben_host: str = Field(default="127.0.0.1", validation_alias="BEN_HOST")
    ben_port: int = Field(default=8000, validation_alias="BEN_PORT")

    # Data directory for SQLite memory and logs
    ben_data_dir: Path = Field(
        default=PROJECT_ROOT / "data",
        description="Directory for local storage (memory DB, logs)"
    )

    # Approved workspaces for local tools (semicolon-delimited list of absolute paths)
    ben_allowed_workspaces: str = Field(
        default="",
        validation_alias="BEN_ALLOWED_WORKSPACES",
        description="Semicolon-delimited allowed workspace directories"
    )

    @property
    def allowed_workspaces(self) -> list[Path]:
        """Return list of canonical resolved allowed workspace directories."""
        workspaces = [PROJECT_ROOT.resolve()]
        if self.ben_allowed_workspaces:
            for item in self.ben_allowed_workspaces.split(";"):
                cleaned = item.strip()
                if cleaned:
                    p = Path(cleaned).resolve()
                    if p.exists() and p not in workspaces:
                        workspaces.append(p)
        return workspaces

    @property
    def api_key(self) -> Optional[str]:
        """Return the sanitized effective API key.

        Prioritizes ANTHROPIC_API_KEY, then API_KEY, then GEMINI_API_KEY.
        Discards placeholder template values automatically.
        """
        raw = (
            self.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or self.api_key_fallback
            or os.environ.get("API_KEY")
            or self.gemini_api_key_legacy
            or os.environ.get("GEMINI_API_KEY")
        )
        return sanitize_key(raw)

    @property
    def base_url(self) -> Optional[str]:
        """Return sanitized optional base URL for Anthropic client if configured."""
        raw = (
            self.anthropic_base_url
            or os.environ.get("ANTHROPIC_BASE_URL")
            or self.base_url_fallback
            or os.environ.get("BASE_URL")
        )
        if not raw:
            return None
        cleaned = raw.strip().rstrip("/")
        return cleaned if cleaned else None

    @property
    def ben_model(self) -> str:
        """Return the effective Claude model identifier."""
        raw = self.anthropic_model or os.environ.get("ANTHROPIC_MODEL") or self.ben_model_fallback or os.environ.get("BEN_MODEL")
        if raw and not raw.startswith("gemini"):
            return raw.strip()
        return "claude-3-5-sonnet-20241022"

    def get_api_key_diagnostics(self) -> Dict[str, Any]:
        """Provide safe diagnostic metadata about the API key without revealing any part of it.

        Guarantees that no substring, prefix, or character of the key is returned.
        """
        effective = self.api_key
        raw = (
            self.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or self.api_key_fallback
            or os.environ.get("API_KEY")
            or self.gemini_api_key_legacy
            or os.environ.get("GEMINI_API_KEY")
        )

        has_raw = bool(raw and raw.strip())
        is_placeholder = bool(has_raw and not effective)
        is_configured = bool(effective is not None)
        char_count = len(effective) if effective else 0

        source = "Not Found"
        if ENV_PATH.exists() and (self.anthropic_api_key or self.api_key_fallback or self.gemini_api_key_legacy):
            source = f".env file ({ENV_PATH})"
        elif "ANTHROPIC_API_KEY" in os.environ:
            source = "Environment variable (ANTHROPIC_API_KEY)"
        elif "API_KEY" in os.environ:
            source = "Environment variable (API_KEY)"
        elif "GEMINI_API_KEY" in os.environ:
            source = "Environment variable (GEMINI_API_KEY)"

        return {
            "key_exists": is_configured,
            "char_count": char_count,
            "is_placeholder": is_placeholder,
            "source": source,
            "project_root": str(PROJECT_ROOT),
            "env_file_path": str(ENV_PATH),
            "env_file_exists": ENV_PATH.exists(),
            "model": self.ben_model,
            "base_url": self.base_url or "https://api.anthropic.com (default)",
        }

    def ensure_directories(self) -> None:
        """Ensure necessary local data directories exist."""
        self.ben_data_dir.mkdir(parents=True, exist_ok=True)


# Global settings singleton
settings = Settings()
settings.ensure_directories()
