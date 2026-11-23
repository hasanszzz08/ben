"""Vercel Serverless Function entry point for BEN FastAPI application."""

import sys
from pathlib import Path

# Add project root directory to sys.path so 'ben' imports reliably
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ben.app import app as fastapi_app


class VercelPathMiddleware:
    """Normalize Vercel rewrite paths so all routes match reliably."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/api/index.py"):
                new_path = path[len("/api/index.py"):]
                scope["path"] = new_path if new_path.startswith("/") else ("/" + new_path) if new_path else "/"
            elif path in ("/api", "/api/"):
                scope["path"] = "/"
        await self.app(scope, receive, send)


app = VercelPathMiddleware(fastapi_app)
