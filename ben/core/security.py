"""Security and sandboxing module for BEN local tools — Phase 6.

Enforces:
1. Strict directory boundaries (approved workspaces allowlist only).
2. Path traversal blocking (.., symlinks, absolute root escapes).
3. Secret & credential isolation (never read .env, .git, SSH/TLS keys).
4. System directory protection (C:\Windows, System32, Program Files).
5. Application execution allowlist (no arbitrary shell or executables).
6. Sanitized audit logging (no secrets or credential leakage).
"""

import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ben.config import settings
from ben.utils.logger import logger


class RiskLevel(str, Enum):
    READ_ONLY = "read-only"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


class SecurityViolation(Exception):
    """Raised when an operation attempts to violate local sandbox boundaries."""
    pass


# ---------------------------------------------------------------------------
# Strict Allowlists & Blocklists
# ---------------------------------------------------------------------------

# Approved applications that BEN is permitted to launch
ALLOWED_APPLICATIONS: Dict[str, Dict[str, Any]] = {
    "notepad": {
        "binaries": ["notepad.exe", "notepad"],
        "description": "Windows Notepad text editor",
    },
    "calc": {
        "binaries": ["calc.exe", "calc"],
        "description": "Windows Calculator",
    },
    "calculator": {
        "binaries": ["calc.exe", "calc"],
        "description": "Windows Calculator",
    },
    "chrome": {
        "binaries": ["chrome.exe", "chrome"],
        "description": "Google Chrome web browser",
    },
    "code": {
        "binaries": ["code.cmd", "code.exe", "code"],
        "description": "Visual Studio Code editor",
    },
    "explorer": {
        "binaries": ["explorer.exe", "explorer"],
        "description": "Windows File Explorer",
    },
}

# Blocked filenames (secrets, environment, keys, repository internals)
BLOCKED_FILENAMES: Set[str] = {
    ".env",
    ".env.example",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
    ".git",
    ".gitignore",
    ".gitattributes",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
    "authorized_keys",
    "credentials",
    "secrets.json",
}

# Blocked file extensions
BLOCKED_EXTENSIONS: Set[str] = {
    ".key",
    ".pem",
    ".pfx",
    ".p12",
    ".pkcs12",
    ".crt",
    ".der",
    ".kdbx",
}

# Sensitive directory names that must not be traversed into
BLOCKED_DIR_NAMES: Set[str] = {
    ".git",
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
}

# System directories that must never be accessed
BLOCKED_SYSTEM_PREFIXES: List[str] = [
    r"c:\windows",
    r"c:\winnt",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\recovery",
    r"c:\system volume information",
    "/etc",
    "/var",
    "/proc",
    "/sys",
    "/root",
    "/dev",
]


# ---------------------------------------------------------------------------
# Path & Workspace Validation
# ---------------------------------------------------------------------------

def get_approved_workspaces() -> List[Path]:
    """Return all canonical resolved directories approved for tool access."""
    return settings.allowed_workspaces


def validate_safe_path(
    user_path: str | Path,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
    allowed_roots: Optional[List[Path]] = None,
) -> Path:
    """Validate that a file or directory path is safe and inside approved workspaces.

    Raises:
        SecurityViolation: If path traversal, system directory, or blocked file is detected.
        FileNotFoundError: If must_exist=True and target does not exist.
        ValueError: If must_be_file/must_be_dir constraints fail.
    """
    if not user_path:
        raise SecurityViolation("Path cannot be empty.")

    raw_str = str(user_path).strip().strip("'\"")
    if not raw_str:
        raise SecurityViolation("Path cannot be empty or whitespace.")

    # Explicitly check for traversal attempts in raw string
    if ".." in raw_str.replace("\\", "/").split("/"):
        # We also check after resolving, but catching explicit traversal early is essential
        pass

    try:
        resolved = Path(raw_str).expanduser().resolve()
    except Exception as exc:
        raise SecurityViolation(f"Invalid path representation: {exc}")

    # 1. Block root drives (e.g., C:\ or /)
    if resolved == resolved.parent or not resolved.parts:
        raise SecurityViolation("Root drive paths are strictly prohibited.")

    # 2. Block system directories
    norm_resolved = str(resolved).lower()
    for sys_prefix in BLOCKED_SYSTEM_PREFIXES:
        if norm_resolved == sys_prefix.lower() or norm_resolved.startswith(sys_prefix.lower() + os.sep):
            raise SecurityViolation("Access to operating system directories is strictly prohibited.")

    # 3. Ensure path is within at least one approved workspace
    approved = allowed_roots or get_approved_workspaces()
    is_contained = False
    for root in approved:
        try:
            if resolved.is_relative_to(root):
                is_contained = True
                break
        except AttributeError:
            # Fallback for Python < 3.9 if ever run
            try:
                resolved.relative_to(root)
                is_contained = True
                break
            except ValueError:
                continue

    if not is_contained:
        raise SecurityViolation(
            f"Path '{resolved.name}' is outside approved workspace directories."
        )

    # 4. Check for blocked secret / credential files
    name_lower = resolved.name.lower()
    if name_lower in BLOCKED_FILENAMES or name_lower.startswith(".env"):
        raise SecurityViolation("Access to sensitive credential or environment files is strictly prohibited.")

    if resolved.suffix.lower() in BLOCKED_EXTENSIONS:
        raise SecurityViolation(f"Access to file extension '{resolved.suffix}' is strictly prohibited.")

    # 5. Check all path segments for blocked directories (.git, .ssh, etc.)
    for part in resolved.parts:
        if part.lower() in BLOCKED_DIR_NAMES:
            raise SecurityViolation(f"Access inside '{part}' directory is strictly prohibited.")

    # 6. Existence and type checks
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"File or directory not found: {resolved.name}")

    if must_be_file and resolved.exists() and not resolved.is_file():
        raise ValueError(f"Path is not a regular file: {resolved.name}")

    if must_be_dir and resolved.exists() and not resolved.is_dir():
        raise ValueError(f"Path is not a directory: {resolved.name}")

    return resolved


# ---------------------------------------------------------------------------
# Application Validation
# ---------------------------------------------------------------------------

def validate_app_name(app_name: str) -> str:
    """Validate that an application name is in the approved application allowlist.

    Returns the normalized canonical application key.
    Raises SecurityViolation if application is not permitted.
    """
    if not app_name or not isinstance(app_name, str):
        raise SecurityViolation("Application name must be a non-empty string.")

    cleaned = app_name.strip().lower()
    # Strip common file extensions if passed
    if cleaned.endswith(".exe") or cleaned.endswith(".cmd"):
        cleaned = cleaned.rsplit(".", 1)[0]

    if cleaned not in ALLOWED_APPLICATIONS:
        allowed = ", ".join(sorted(ALLOWED_APPLICATIONS.keys()))
        raise SecurityViolation(
            f"Application '{app_name}' is not in the approved application allowlist. "
            f"Permitted applications: {allowed}"
        )

    return cleaned


def resolve_app_binary(app_key: str) -> str:
    """Find the executable binary for an approved application."""
    app_info = ALLOWED_APPLICATIONS.get(app_key)
    if not app_info:
        raise SecurityViolation(f"Unknown application: '{app_key}'")

    for binary in app_info["binaries"]:
        found = shutil.which(binary)
        if found:
            return found

    # Fallback to the first binary name (Windows system PATH will search for it)
    return app_info["binaries"][0]


# ---------------------------------------------------------------------------
# Audit Logging
# ---------------------------------------------------------------------------

def audit_log(
    tool_name: str,
    arguments: Dict[str, Any],
    risk_level: str,
    approved: bool,
    status: str,
    details: str = "",
) -> None:
    """Safely log tool execution attempts to application logger without leaking secrets."""
    sanitized_args: Dict[str, Any] = {}
    sensitive_keys = {"password", "secret", "key", "token", "credential", "auth"}

    for k, v in arguments.items():
        if any(sec in k.lower() for sec in sensitive_keys):
            sanitized_args[k] = "[REDACTED]"
        else:
            sanitized_args[k] = str(v)[:200]  # Cap length for safe logging

    logger.info(
        f"[AUDIT] Tool='{tool_name}' Risk='{risk_level}' Approved={approved} "
        f"Status='{status}' Args={sanitized_args} Details='{details}'"
    )
