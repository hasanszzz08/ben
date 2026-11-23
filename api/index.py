"""Vercel Serverless Function entry point for BEN FastAPI application."""

import sys
from pathlib import Path

# Add project root directory to sys.path so 'ben' imports reliably
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ben.app import app as fastapi_app


class VercelPathMiddleware:
    """Restore real requested path from Vercel routing headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            real_path = (
                headers.get(b"x-matched-path", b"")
                or headers.get(b"x-forwarded-uri", b"")
                or headers.get(b"x-vercel-matched-path", b"")
            ).decode("utf-8", "ignore")

            if real_path and real_path not in ("/api/index.py", ""):
                # Strip query parameters if present in the matched path
                scope["path"] = real_path.split("?")[0]
            else:
                path = scope.get("path", "")
                if path.startswith("/api/index.py"):
                    new_path = path[len("/api/index.py"):]
                    scope["path"] = new_path if new_path.startswith("/") else ("/" + new_path) if new_path else "/"
                elif path in ("/api", "/api/"):
                    scope["path"] = "/"

        await self.app(scope, receive, send)


app = VercelPathMiddleware(fastapi_app)
