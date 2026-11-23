import os
from pathlib import Path
import subprocess
import sys


def start_backend():
    base_dir = Path(__file__).resolve().parent
    # If run from subfolder, locate project root containing run.py
    if not (base_dir / "run.py").exists() and (base_dir.parent / "run.py").exists():
        base_dir = base_dir.parent

    os.chdir(base_dir)

    # Prefer virtual environment python if available, fallback to sys.executable
    venv_py = base_dir / ".venv" / "Scripts" / "python.exe"
    executable = str(venv_py) if venv_py.exists() else sys.executable

    subprocess.run([executable, "run.py"])


if __name__ == "__main__":
    start_backend()
