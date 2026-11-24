"""Vercel Serverless Function entry point for BEN FastAPI application.

@vercel/python automatically detects the ASGI `app` object and wraps it.
The `handler` alias ensures maximum compatibility across Vercel runtime versions.
"""

import os
import sys
from pathlib import Path

# Signal Vercel environment for config adjustments (/tmp writable, etc.)
os.environ.setdefault("VERCEL", "1")

# Add project root directory to sys.path so 'ben' imports reliably
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ben.app import app  # noqa: E402

# Vercel Python runtime looks for `app` (ASGI) or `handler`
handler = app
