"""Vercel Serverless Function entry point for BEN FastAPI application."""

import sys
from pathlib import Path

# Add project root directory to sys.path so 'ben' imports reliably
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ben.app import app
