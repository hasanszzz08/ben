"""Tool execution engine with validation, 5s timeout, and security audit logging — Phase 6.

Implements:
- execute_tool(tool_name, args, user_id)
- 5s strict timeout enforcement
- Security argument validation
- Sanitized audit logging (NO secrets, NO file contents)
"""

import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ben.tools.registry import registry
from ben.utils.logger import logger

TOOLS_LOG_FILE = Path(__file__).resolve().parent / "tools.log"


def log_tool_call(tool_name: str, status: str, user_id: str) -> None:
    """Append sanitized audit record to tools.log: timestamp, tool_name, status, user_id."""
    now_iso = datetime.now(timezone.utc).isoformat()
    record = f"{now_iso} | tool={tool_name} | status={status} | user={user_id}\n"
    try:
        with open(TOOLS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(record)
    except Exception as exc:
        logger.warning(f"Could not write to tools.log: {exc}")


async def execute_tool(
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    user_id: str = "hasan",
    approval_callback: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """Execute a registered tool safely with 5-second timeout and logging.

    Args:
        tool_name: Name of registered tool (e.g., 'open_app', 'list_files', 'read_file')
        args: Input arguments dictionary for the tool
        user_id: ID of the user requesting execution
        approval_callback: Optional callable for sensitive/destructive tool approvals

    Returns:
        Dict containing success status, tool_name, result, or error message.
    """
    if args is None:
        args = {}

    if not isinstance(args, dict):
        log_tool_call(tool_name, "FAILED:INVALID_ARGS", user_id)
        return {
            "success": False,
            "tool_name": tool_name,
            "error": "Tool arguments must be a dictionary.",
        }

    # 1. Look up tool from registry
    tool = registry.get(tool_name)
    if not tool:
        log_tool_call(tool_name, "FAILED:TOOL_NOT_FOUND", user_id)
        return {
            "success": False,
            "tool_name": tool_name,
            "error": f"Tool '{tool_name}' not found in registry.",
        }

    # 2. Validate arguments (block path traversal, check unapproved paths)
    for k, v in args.items():
        if isinstance(v, str):
            # Check for illegal path traversal attempts
            if ".." in v and ("\\" in v or "/" in v):
                log_tool_call(tool.name, "BLOCKED:PATH_TRAVERSAL", user_id)
                return {
                    "success": False,
                    "tool_name": tool.name,
                    "error": "Path traversal ('..') is strictly prohibited.",
                }

    # 3. Check approval if required
    if tool.requires_approval:
        approved = False
        if approval_callback:
            try:
                if inspect.iscoroutinefunction(approval_callback):
                    approved = await approval_callback(tool.name, args)
                else:
                    approved = approval_callback(tool.name, args)
            except Exception as e:
                logger.warning(f"Approval callback failed: {e}")
                approved = False

        if not approved:
            log_tool_call(tool.name, "DENIED:REQUIRES_APPROVAL", user_id)
            return {
                "success": False,
                "tool_name": tool.name,
                "error": f"Tool '{tool.name}' requires explicit user approval.",
                "requires_approval": True,
            }

    # 4. Execute tool with 5-second strict timeout
    try:
        if inspect.iscoroutinefunction(tool.execute):
            result = await asyncio.wait_for(tool.execute(args), timeout=5.0)
        else:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, tool.execute, args),
                timeout=5.0,
            )

        log_tool_call(tool.name, "SUCCESS", user_id)
        logger.info(f"[TOOL SUCCESS] user='{user_id}' tool='{tool.name}' completed.")
        return {
            "success": True,
            "tool_name": tool.name,
            "result": result,
        }

    except asyncio.TimeoutError:
        log_tool_call(tool.name, "TIMEOUT", user_id)
        logger.error(f"[TOOL TIMEOUT] Tool '{tool.name}' timed out after 5.0 seconds.")
        return {
            "success": False,
            "tool_name": tool.name,
            "error": f"Tool '{tool.name}' timed out after 5 seconds.",
        }

    except Exception as exc:
        err_msg = str(exc)
        log_tool_call(tool.name, f"ERROR:{type(exc).__name__}", user_id)
        logger.error(f"[TOOL ERROR] Tool '{tool.name}' failed: {err_msg}")
        return {
            "success": False,
            "tool_name": tool.name,
            "error": err_msg,
        }
