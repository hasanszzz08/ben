"""BEN FastAPI Application — Phase 3 (Memory-Persistent Chat Backend).

Serves:
- REST API at /api/chat/*     (chat interface)
- REST API at /api/memory/*   (core memory CRUD + conversation history)
- Static chat UI at /         (served from ben/static/)
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ben.config import settings
from ben.api.chat import router as chat_router
from ben.api.memory import router as memory_router
from ben.api.extension import router as extension_router
from ben.utils.logger import logger


# ---------------------------------------------------------------------------
# Lifespan: init DB and log startup status
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    # Initialise SQLite schema (idempotent)
    await asyncio.to_thread(_init_db)

    logger.info(f"BEN backend running on http://{settings.ben_host}:{settings.ben_port}")
    from ben.core.client import AnthropicClientManager
    mgr = AnthropicClientManager()
    if not mgr.is_configured:
        logger.warning("Anthropic API key not configured. Set ANTHROPIC_API_KEY in .env")
    else:
        logger.info(f"Anthropic Claude model: {mgr.default_model}")

    yield

    logger.info("BEN backend shutting down.")


def _init_db():
    from ben.memory.database import init_db
    init_db()


# ---------------------------------------------------------------------------
# App definition
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BEN",
    description="Personal AI Agent – Phase 5 Chrome Extension",
    version="0.5.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# CORS: allow localhost + Chrome extension
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        f"http://localhost:{settings.ben_port}",
        f"http://{settings.ben_host}:{settings.ben_port}",
        "chrome-extension://*",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Accept"],
)

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(extension_router)

# ---------------------------------------------------------------------------
# Static UI & Dashboard
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
FRONTEND_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_ui():
    """Serve the BEN chat interface."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return {"error": "UI not found."}
    return FileResponse(str(index))


@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    """Serve the BEN Devices & Control Dashboard."""
    dash = FRONTEND_DIR / "dashboard.html"
    if not dash.exists():
        return {"error": "Dashboard not found."}
    return FileResponse(str(dash))


# ---------------------------------------------------------------------------
# Root route aliases matching Phase 6 & 7 specs
# ---------------------------------------------------------------------------
from ben.tools.executor import execute_tool
from ben.voice.listener import listen_for_codeword, speak


@app.get("/devices")
async def root_get_devices():
    """Root alias for device detection."""
    res = await execute_tool("DetectDevices", {})
    return {"devices": res.get("result", [])}


@app.post("/open_app")
async def root_open_app(payload: dict):
    """Root alias to open approved desktop applications."""
    app_name = payload.get("app", "")
    res = await execute_tool("OpenApp", {"app": app_name})
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to open app."))
    return {"status": "success", "result": res.get("result")}


@app.post("/start_voice_mode")
async def root_start_voice_mode(payload: dict = None):
    """Root alias to start voice codeword detection."""
    codeword = payload.get("codeword", "aslamlyekum") if payload else "aslamlyekum"
    simulate = payload.get("simulate", False) if payload else False
    command = payload.get("command") if payload else None

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
        from ben.api.chat import try_dispatch_tool
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
