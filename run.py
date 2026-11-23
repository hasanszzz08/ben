r"""BEN server entry-point.

Usage:
    .\.venv\Scripts\python run.py
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path (supports running from any directory)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from ben.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "ben.app:app",
        host=settings.ben_host,
        port=settings.ben_port,
        reload=(settings.ben_env == "development"),
        log_level="info",
    )
