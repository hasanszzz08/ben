"""FastAPI chat router for BEN — Phase 3 (SQLite-backed, memory-enriched).

Endpoints (unchanged from Phase 2)
-----------------------------------
POST /api/chat/message       – Single-shot request/response
POST /api/chat/stream        – Streaming Server-Sent Events response
POST /api/chat/clear         – Clear conversation history (persists old session)
GET  /api/chat/history       – Retrieve current session history
GET  /api/chat/status        – Backend and model health status
POST /api/chat/stop          – Cancel active generation
"""

import asyncio
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ben.config import settings
from ben.core.client import AnthropicClientManager
from ben.core.conversation import ConversationHistory, Role
from ben.memory.database import get_db
from ben.memory.models import ConversationStore, build_memory_context
from ben.memory.service import (
    build_personalized_instruction,
    get_user_context,
    load_user_profile,
    save_user_profile,
)
from ben.tools.executor import execute_tool
from ben.tools.registry import ToolRegistry, registry
from ben.tools.tools import OpenApp, ListFiles, ReadFile, DetectDevices
from ben.voice.listener import listen_for_codeword, speak
from ben.utils.logger import logger

# Auto-register Phase 6 & Phase 7 tools into ToolRegistry
if not registry.get("open_app"):
    registry.add(OpenApp())
if not registry.get("list_files"):
    registry.add(ListFiles())
if not registry.get("read_file"):
    registry.add(ReadFile())
if not registry.get("detect_devices"):
    registry.add(DetectDevices())


router = APIRouter(prefix="/api/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# Shared session state (one active session per server process)
# Phase 3: hydrated from / persisted to SQLite
# ---------------------------------------------------------------------------
_history = ConversationHistory()
_client_mgr = AnthropicClientManager()

_cancel_event: threading.Event = threading.Event()
_request_lock = asyncio.Lock()

MAX_INPUT_CHARS = 32_000


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_INPUT_CHARS)
    session_id: Optional[str] = None

    @field_validator("message")
    @classmethod
    def strip_message(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message cannot be empty or whitespace.")
        return stripped


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    message_count: int
    model: str


class ChatHistoryResponse(BaseModel):
    session_id: str
    message_count: int
    messages: list[Dict[str, Any]]


class StatusResponse(BaseModel):
    status: str
    model: str
    key_configured: bool
    session_id: str
    message_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_error(exc: Exception) -> str:
    """Return error text with API key redacted."""
    msg = str(exc)
    key = settings.api_key or ""
    if key and key in msg:
        msg = msg.replace(key, "[REDACTED]")
    return msg


def _persist_message(session_id: str, message) -> None:
    """Write a single message to SQLite (called from thread pool)."""
    ConversationStore.ensure_conversation(session_id)
    ConversationStore.save_message(session_id, message)


def _persist_update(message_id: str, content: str) -> None:
    """Update message content in SQLite (streaming finalize)."""
    ConversationStore.update_message_content(message_id, content)


def _enriched_system_instruction(user_id: str = "hasan") -> str:
    """Build system instruction enriched with user profile, tools, and active core_memories."""
    profile = load_user_profile(user_id)
    personalized_base = build_personalized_instruction(settings.ben_system_instruction, profile)
    tools_desc = (
        "\n\nYou have access to local tools for Boss Hasan:\n"
        "- open_app: {\"app_name\": \"notepad\"} (Approved: 'notepad', 'calc')\n"
        "- list_files: {\"folder_path\": \"C:\\\\Users\\\\Hasan\\\\Documents\"} (Approved: C:\\Users\\Hasan\\Documents, C:\\Users\\Hasan\\Downloads)\n"
        "- read_file: {\"file_path\": \"run.py\"} (Approved: .txt, .py, .md inside approved folders)\n"
        "If you need to invoke a tool, reply in the exact format:\n"
        "TOOL_CALL: <tool_name> <json_args>\n"
        "Example: TOOL_CALL: open_app {\"app_name\": \"notepad\"}\n"
    )
    return build_memory_context(personalized_base + tools_desc)


class UserProfilePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role: str = Field(default="boss", max_length=100)
    interests: str = Field(..., min_length=1, max_length=500)
    user_id: str = Field(default="hasan", max_length=50)


@router.get("/profile")
async def get_profile(user_id: str = "hasan"):
    """Retrieve the current user profile from SQLite."""
    profile = await get_user_context(user_id)
    if not profile:
        return {"configured": False, "user_id": user_id, "profile": None}
    return {"configured": True, "user_id": user_id, "profile": profile}


@router.post("/profile")
async def set_profile(payload: UserProfilePayload):
    """Save or update the user profile in SQLite."""
    saved = await asyncio.to_thread(
        save_user_profile,
        user_id=payload.user_id,
        name=payload.name,
        role=payload.role,
        interests=payload.interests,
    )
    return {"saved": True, "profile": saved}


# ---------------------------------------------------------------------------
# Dynamic AI Provider / Model & Key Configuration
# ---------------------------------------------------------------------------

class AIConfigPayload(BaseModel):
    provider: str = Field(default="gemini")
    model: str = Field(default="gemini-3.5-flash-lite")
    api_key: Optional[str] = None


def _update_env_var(key: str, val: str) -> None:
    from pathlib import Path
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_file.exists():
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        found = False
        for line in lines:
            if line.strip().startswith(f"{key}="):
                new_lines.append(f"{key}={val}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={val}\n")
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning(f"Could not write {key} to .env: {e}")


@router.get("/config")
async def get_ai_config():
    """Return active AI provider, model, and masked key status."""
    return {
        "provider": "gemini" if _client_mgr.is_gemini else "claude",
        "model": _client_mgr.default_model,
        "is_configured": _client_mgr.is_configured,
        "key_char_count": _client_mgr.key_char_count,
        "available_models": [
            {"id": "gemini-3.5-flash-lite", "name": "Google Gemini 3.5 Flash Lite (Fast)", "provider": "gemini"},
            {"id": "gemini-flash-latest", "name": "Google Gemini Flash (Latest)", "provider": "gemini"},
            {"id": "gemini-2.5-pro", "name": "Google Gemini 2.5 Pro", "provider": "gemini"},
            {"id": "claude-3-5-sonnet-20241022", "name": "Anthropic Claude 3.5 Sonnet", "provider": "claude"},
            {"id": "claude-3-opus-20240229", "name": "Anthropic Claude 3 Opus", "provider": "claude"},
            {"id": "claude-3-7-sonnet-20250219", "name": "Anthropic Claude 3.7 Sonnet", "provider": "claude"},
        ]
    }


@router.post("/config")
async def update_ai_config(payload: AIConfigPayload):
    """Dynamically switch AI provider, model, or API key in 1 click."""
    global _client_mgr
    new_key = payload.api_key.strip() if payload.api_key and payload.api_key.strip() else _client_mgr._api_key
    selected_model = payload.model.strip() if payload.model else "gemini-3.5-flash-lite"

    # Persist to .env
    if payload.api_key and payload.api_key.strip():
        if payload.provider == "claude" or new_key.startswith("sk-ant"):
            _update_env_var("ANTHROPIC_API_KEY", new_key)
        else:
            _update_env_var("GEMINI_API_KEY", new_key)
            _update_env_var("ANTHROPIC_API_KEY", new_key)

    if selected_model.startswith("gemini"):
        _update_env_var("GEMINI_MODEL", selected_model)
    else:
        _update_env_var("ANTHROPIC_MODEL", selected_model)

    _client_mgr = AnthropicClientManager(api_key=new_key, model=selected_model)
    conn = await asyncio.to_thread(_client_mgr.test_connection)

    return {
        "success": conn.success,
        "provider": "gemini" if _client_mgr.is_gemini else "claude",
        "model": _client_mgr.default_model,
        "message": conn.response if conn.success else conn.error,
    }


# ---------------------------------------------------------------------------
# Phase 6 & 7: Tool Dispatcher & Devices / Voice Endpoints
# ---------------------------------------------------------------------------

async def try_dispatch_tool(text: str) -> Optional[str]:
    """Check if user prompt directly invokes one of the registered tools."""
    import re
    clean = text.strip()
    clean_lower = clean.lower()

    # 1. OpenApp: "open <app>", "launch <app>", "start <app>"
    open_match = re.match(r"^(?:open|launch|start)\s+([a-zA-Z0-9_\-\.]+)\s*$", clean, re.IGNORECASE)
    if open_match:
        app_name = open_match.group(1).strip()
        if app_name.endswith(".exe"):
            app_name = app_name[:-4]
        res = await execute_tool("open_app", {"app_name": app_name})
        if res.get("success"):
            return res.get("result", "opened")
        return f"Failed to open {app_name}: {res.get('error')}"

    # 2. ListFiles: "list my documents", "list documents", "show my documents", "list files in documents"
    if clean_lower in ["list my documents", "list documents", "show my documents", "list files in documents"]:
        res = await execute_tool("list_files", {"folder_path": r"C:\Users\Hasan\Documents"})
        if res.get("success"):
            files = res.get("result", [])
            file_list = "\n".join(f"• {f}" for f in files) if files else "(folder is empty)"
            return f"Files in Documents:\n{file_list}"
        return f"Error: {res.get('error')}"

    if clean_lower in ["list my downloads", "list downloads", "show my downloads", "list files in downloads"]:
        res = await execute_tool("list_files", {"folder_path": r"C:\Users\Hasan\Downloads"})
        if res.get("success"):
            files = res.get("result", [])
            file_list = "\n".join(f"• {f}" for f in files) if files else "(folder is empty)"
            return f"Files in Downloads:\n{file_list}"
        return f"Error: {res.get('error')}"

    # "list C:\Windows" or "list <path>" or "list files in <path>"
    list_path_match = re.match(r"^(?:list|list files in|show files in)\s+([a-zA-Z]:[\\/][a-zA-Z0-9_\-\./\\]+|[a-zA-Z0-9_\-\./\\]+)\s*$", clean, re.IGNORECASE)
    if list_path_match:
        target_path = list_path_match.group(1).strip()
        res = await execute_tool("list_files", {"folder_path": target_path})
        if res.get("success"):
            files = res.get("result", [])
            file_list = "\n".join(f"• {f}" for f in files) if files else "(folder is empty)"
            return f"Files in {target_path}:\n{file_list}"
        return f"Error: {res.get('error')}"

    if clean_lower in ["list files", "list my files", "list all files"]:
        res = await execute_tool("list_files", {"folder_path": r"C:\Users\Hasan\Documents"})
        if res.get("success"):
            files = res.get("result", [])
            file_list = "\n".join(f"• {f}" for f in files) if files else "(folder is empty)"
            return f"Files in Documents:\n{file_list}"
        return f"Error: {res.get('error')}"

    # 3. ReadFile: "read run.py", "read C:\password.txt", "read file <path>"
    read_match = re.match(r"^read(?:\s+file)?\s+([a-zA-Z]:[\\/][a-zA-Z0-9_\-\./\\]+|[a-zA-Z0-9_\-\./\\]+)\s*$", clean, re.IGNORECASE)
    if read_match:
        target_file = read_match.group(1).strip()
        res = await execute_tool("read_file", {"file_path": target_file})
        if res.get("success"):
            return res.get("result", "")
        return f"Error: {res.get('error')}"

    # 4. DetectDevices
    if any(k in clean_lower for k in ["find devices", "detect devices", "scan devices", "show devices", "scan network", "my devices"]):
        res = await execute_tool("detect_devices", {})
        if res.get("success"):
            devices = res.get("result", [])
            lines = [f"• {d.get('hostname', 'Device')} — IP: {d.get('ip')} | MAC: {d.get('mac')} | Status: {d.get('status')}" for d in devices]
            return "Detected devices on your network:\n" + "\n".join(lines)
        return f"Error scanning devices: {res.get('error')}"

    return None


def _parse_claude_tool_call(text: str):
    """Parse tool use from Claude's response text if present."""
    import json
    import re
    m = re.search(r"(?:TOOL_CALL|TOOL):\s*([a-zA-Z0-9_]+)\s*(\{.*?\})", text, re.DOTALL | re.IGNORECASE)
    if m:
        tname = m.group(1).strip()
        try:
            targs = json.loads(m.group(2).strip())
            return tname, targs
        except Exception:
            pass

    m2 = re.search(r"```(?:tool_call|json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m2:
        try:
            data = json.loads(m2.group(1).strip())
            tname = data.get("tool") or data.get("name") or data.get("tool_name")
            targs = data.get("args") or data.get("inputs") or data.get("parameters") or {}
            if tname:
                return tname, targs
        except Exception:
            pass

    return None


class OpenAppPayload(BaseModel):
    app: str = Field(..., min_length=1)


class VoiceModePayload(BaseModel):
    codeword: str = "aslamlyekum"
    simulate: bool = False
    command: Optional[str] = None


@router.get("/devices")
async def get_devices_endpoint():
    """Scan and list network devices on 192.168.x.x."""
    res = await execute_tool("DetectDevices", {})
    return {"devices": res.get("result", [])}


@router.post("/open_app")
async def open_app_endpoint(payload: OpenAppPayload):
    """Launch an approved application (notepad, calc)."""
    res = await execute_tool("OpenApp", {"app": payload.app})
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to open app."))
    return {"status": "success", "result": res.get("result")}


@router.post("/start_voice_mode")
async def start_voice_mode(payload: Optional[VoiceModePayload] = None):
    """Listen for codeword 'aslamlyekum' and trigger voice session."""
    codeword = payload.codeword if payload else "aslamlyekum"
    simulate = payload.simulate if payload else False
    command = payload.command if payload else None

    # Listen or simulate detection
    if simulate:
        detected = True
    else:
        detected = await listen_for_codeword(codeword=codeword, timeout_seconds=4)
        if not detected and simulate:
            detected = True

    if not detected:
        return {
            "status": "not_detected",
            "codeword_detected": False,
            "message": f"Codeword '{codeword}' not heard. Please say 'hello ben aslamlyekum'.",
        }

    greeting = "Voice mode activated. How can I help?"
    speak(greeting)

    command_output = None
    if command:
        tool_out = await try_dispatch_tool(command)
        if tool_out:
            command_output = tool_out
            speak(tool_out)

    return {
        "status": "activated",
        "codeword_detected": True,
        "message": greeting,
        "command_result": command_output,
    }


@router.get("/tools")
async def list_tools_endpoint():
    """List all registered tools."""
    return {"tools": registry.list_all()}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=StatusResponse)
async def get_status():
    """Health and configuration status — safe for browser consumption."""
    return StatusResponse(
        status="online" if _client_mgr.is_configured else "unconfigured",
        model=_client_mgr.default_model,
        key_configured=_client_mgr.is_configured,
        session_id=_history.session_id,
        message_count=_history.message_count,
    )


@router.get("/history", response_model=ChatHistoryResponse)
async def get_history():
    """Return the current conversation history (safe — no API key data)."""
    return ChatHistoryResponse(
        session_id=_history.session_id,
        message_count=_history.message_count,
        messages=[
            {
                "id": m.id,
                "role": m.role.value,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
                "is_error": m.is_error,
            }
            for m in _history.messages
        ],
    )


@router.post("/clear")
async def clear_conversation():
    """Archive current session to SQLite and start a fresh conversation."""
    async with _request_lock:
        _cancel_event.set()
        old_id = _history.session_id
        _history.clear()
        logger.info(f"Conversation cleared. Old session: {old_id}, New: {_history.session_id}")
    # Ensure new session exists in DB
    await asyncio.to_thread(ConversationStore.ensure_conversation, _history.session_id)
    return {"cleared": True, "session_id": _history.session_id}


@router.post("/switch/{session_id}")
async def switch_session(session_id: str):
    """Switch active conversation to an existing session from SQLite."""
    global _history
    async with _request_lock:
        _cancel_event.set()
        conv = await asyncio.to_thread(ConversationStore.get_conversation, session_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation session not found.")
        _history = await asyncio.to_thread(ConversationStore.load_history, session_id)
        logger.info(f"Switched to session: {session_id} ({len(_history.messages)} messages)")
    return {
        "switched": True,
        "session_id": _history.session_id,
        "message_count": _history.message_count,
        "title": conv.get("title", "Untitled"),
    }


@router.post("/stop")
async def stop_generation():
    """Request cancellation of the active streaming generation."""
    _cancel_event.set()
    return {"stopped": True}


@router.post("/message", response_model=ChatResponse)
async def send_message(req: ChatRequest):
    """Single-turn chat: send a message, receive a complete response."""
    if not _client_mgr.is_configured:
        raise HTTPException(status_code=503, detail="BEN is not configured. Set ANTHROPIC_API_KEY in .env.")

    async with _request_lock:
        _cancel_event.clear()

        # Record user message in-memory
        try:
            user_msg = _history.add_user(req.message)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        # Persist user message
        await asyncio.to_thread(ConversationStore.ensure_conversation, _history.session_id)
        await asyncio.to_thread(ConversationStore.save_message, _history.session_id, user_msg)

        # Direct Tool Execution Dispatch
        tool_reply = await try_dispatch_tool(req.message)
        if tool_reply:
            assistant_msg = _history.add_assistant(tool_reply)
            await asyncio.to_thread(ConversationStore.save_message, _history.session_id, assistant_msg)
            return ChatResponse(
                reply=tool_reply,
                session_id=_history.session_id,
                message_count=_history.message_count,
                model=_client_mgr.default_model,
            )

        # Build Claude conversation history with prior context and enriched system instruction
        claude_hist = _history.to_anthropic_messages(exclude_last_user=True)
        sys_instruction = await asyncio.to_thread(_enriched_system_instruction)

        try:
            reply_text = await asyncio.to_thread(
                _client_mgr.generate,
                prompt=req.message,
                system_instruction=sys_instruction,
                history_override=claude_hist,
            )
            reply_text = reply_text.strip() if reply_text else "(no response)"
        except Exception as exc:
            error_detail = _safe_error(exc)
            err_msg = _history.add_assistant(f"[Error] {error_detail}", is_error=True)
            await asyncio.to_thread(ConversationStore.save_message, _history.session_id, err_msg)
            logger.error(f"Chat generation failed: {error_detail}")
            raise HTTPException(status_code=502, detail=f"Anthropic Claude API error: {error_detail}")

        # Check if Claude requested tool use
        tool_call = _parse_claude_tool_call(reply_text)
        if tool_call:
            tool_name, tool_args = tool_call
            logger.info(f"Claude requested tool execution: {tool_name} with {tool_args}")
            tool_exec_res = await execute_tool(tool_name, tool_args, user_id="hasan")
            tool_output_str = str(tool_exec_res.get("result") if tool_exec_res.get("success") else f"Error: {tool_exec_res.get('error')}")

            followup_prompt = (
                f"Tool '{tool_name}' execution result: {tool_output_str}\n"
                "Please deliver the final response to Boss Hasan based on this tool result."
            )
            try:
                second_reply = await asyncio.to_thread(
                    _client_mgr.generate,
                    prompt=followup_prompt,
                    system_instruction=sys_instruction,
                    history_override=claude_hist + [
                        {"role": "assistant", "content": reply_text},
                        {"role": "user", "content": followup_prompt},
                    ],
                )
                if second_reply and second_reply.strip():
                    reply_text = second_reply.strip()
            except Exception as exc:
                logger.warning(f"Followup Claude generation after tool use failed: {exc}")
                reply_text = f"Tool '{tool_name}' executed: {tool_output_str}"

        assistant_msg = _history.add_assistant(reply_text)
        await asyncio.to_thread(ConversationStore.save_message, _history.session_id, assistant_msg)

        return ChatResponse(
            reply=reply_text,
            session_id=_history.session_id,
            message_count=_history.message_count,
            model=_client_mgr.default_model,
        )


@router.post("/stream")
async def stream_message(req: ChatRequest):
    """Streaming chat: send a message, receive tokens via Server-Sent Events."""
    if not _client_mgr.is_configured:
        raise HTTPException(status_code=503, detail="BEN is not configured. Set ANTHROPIC_API_KEY in .env.")

    if _request_lock.locked():
        try:
            await asyncio.wait_for(_request_lock.acquire(), timeout=1.5)
            _request_lock.release()
        except asyncio.TimeoutError:
            raise HTTPException(status_code=409, detail="Another request is already in progress. Stop it first.")

    try:
        user_msg = _history.add_user(req.message.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Persist user message immediately
    await asyncio.to_thread(ConversationStore.ensure_conversation, _history.session_id)
    await asyncio.to_thread(ConversationStore.save_message, _history.session_id, user_msg)

    _cancel_event.clear()
    # Placeholder assistant message (empty, updated as stream arrives)
    assistant_msg = _history.add_assistant("")
    # Check tool intent first
    tool_reply = await try_dispatch_tool(req.message)
    if tool_reply:
        _history.replace_last_assistant(tool_reply)
        await asyncio.to_thread(ConversationStore.update_message_content, assistant_msg.id, tool_reply)

        async def tool_event_generator():
            import json
            yield f"data: {json.dumps({'type': 'token', 'text': tool_reply})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'session_id': _history.session_id, 'model': _client_mgr.default_model, 'message_count': _history.message_count})}\n\n"

        return StreamingResponse(
            tool_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def event_generator():
        import json
        accumulated = []

        async with _request_lock:
            _cancel_event.clear()
            try:
                claude_hist = _history.to_anthropic_messages(exclude_last_user=True)
                sys_instruction = await asyncio.to_thread(_enriched_system_instruction)

                loop = asyncio.get_event_loop()
                gen_queue: asyncio.Queue = asyncio.Queue()

                def producer():
                    try:
                        for chunk in _client_mgr.generate_stream(
                            prompt=req.message,
                            history_override=claude_hist,
                            system_instruction=sys_instruction,
                        ):
                            loop.call_soon_threadsafe(gen_queue.put_nowait, ("token", chunk))
                            if _cancel_event.is_set():
                                break
                    except Exception as exc:
                        loop.call_soon_threadsafe(gen_queue.put_nowait, ("error", _safe_error(exc)))
                    finally:
                        loop.call_soon_threadsafe(gen_queue.put_nowait, ("__end__", None))

                thread = threading.Thread(target=producer, daemon=True)
                thread.start()

                while True:
                    if _cancel_event.is_set():
                        full = "".join(accumulated)
                        _history.replace_last_assistant(full)
                        await asyncio.to_thread(
                            ConversationStore.update_message_content, assistant_msg.id, full
                        )
                        yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                        break

                    try:
                        event_type, payload = await asyncio.wait_for(gen_queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        yield f"data: {json.dumps({'type': 'error', 'detail': 'Response timed out.'})}\n\n"
                        break

                    if event_type == "__end__":
                        full_text = "".join(accumulated).strip() or "(no response)"
                        _history.replace_last_assistant(full_text)
                        await asyncio.to_thread(
                            ConversationStore.update_message_content, assistant_msg.id, full_text
                        )
                        yield f"data: {json.dumps({'type': 'done', 'session_id': _history.session_id, 'model': _client_mgr.default_model, 'message_count': _history.message_count})}\n\n"
                        break

                    elif event_type == "token":
                        accumulated.append(payload)
                        _history.replace_last_assistant("".join(accumulated))
                        yield f"data: {json.dumps({'type': 'token', 'text': payload})}\n\n"

                    elif event_type == "error":
                        err_content = f"[Error] {payload}"
                        _history.replace_last_assistant(err_content)
                        await asyncio.to_thread(
                            ConversationStore.update_message_content, assistant_msg.id, err_content
                        )
                        yield f"data: {json.dumps({'type': 'error', 'detail': payload})}\n\n"
                        break

            except Exception as exc:
                err = _safe_error(exc)
                logger.error(f"Stream error: {err}")
                yield f"data: {json.dumps({'type': 'error', 'detail': err})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
