"""Built-in Local Tools for BEN — Phase 6 & 7.

Tools:
1. OpenApp (open_app): Launch approved desktop applications (notepad, calc)
2. ListFiles (list_files): List .txt, .py, .md files inside approved folders
3. ReadFile (read_file): Read first 2000 characters of approved text files
4. DetectDevices (detect_devices): Network device detection for Phase 7
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ben.config import PROJECT_ROOT
from ben.tools.registry import Tool, registry
from ben.utils.logger import logger

USER_HOME = Path(os.environ.get("USERPROFILE", "C:/Users/user")).resolve()

# Approved Folders: C:\Users\Hasan paths and user profile fallbacks
APPROVED_FOLDERS: List[Path] = [
    Path("C:/Users/Hasan/Documents"),
    Path("C:/Users/Hasan/Downloads"),
    USER_HOME / "Documents",
    USER_HOME / "Downloads",
    PROJECT_ROOT.resolve(),
]

APPROVED_APPS: Dict[str, str] = {
    "notepad": r"C:\Windows\System32\notepad.exe" if Path(r"C:\Windows\System32\notepad.exe").exists() else "notepad.exe",
    "calc": r"C:\Windows\System32\calc.exe" if Path(r"C:\Windows\System32\calc.exe").exists() else "calc.exe",
}

APPROVED_EXTENSIONS = {".txt", ".py", ".md"}

BLOCKED_FILES = {
    ".env", ".env.example", ".git", ".gitignore", "id_rsa", "id_ed25519", "credentials"
}


def map_hasan_path(path: Path) -> Path:
    """If C:/Users/Hasan does not exist on disk, transparently map to current user profile."""
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    path_str = str(resolved)
    if "c:\\users\\hasan" in path_str.lower() and not Path("C:/Users/Hasan").exists():
        rel = path_str[len("C:\\Users\\Hasan"):].lstrip("\\/")
        return (USER_HOME / rel).resolve() if rel else USER_HOME.resolve()
    return resolved


def is_path_approved(path: Path, allow_project_root: bool = True) -> bool:
    """Check whether a path resides within APPROVED_FOLDERS and avoids traversal."""
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path

    mapped = map_hasan_path(resolved)

    for approved in APPROVED_FOLDERS:
        if not allow_project_root and approved == PROJECT_ROOT.resolve():
            continue
        try:
            appr_res = approved.resolve()
            appr_map = map_hasan_path(appr_res)
            if resolved == appr_res or resolved.is_relative_to(appr_res):
                return True
            if mapped == appr_map or mapped.is_relative_to(appr_map):
                return True
        except (ValueError, TypeError):
            continue
    return False


# ---------------------------------------------------------------------------
# Tool 1: OpenApp
# ---------------------------------------------------------------------------

class OpenApp(Tool):
    """Open an approved desktop application."""

    def __init__(self):
        super().__init__(
            name="open_app",
            description="Open an approved application",
            risk_level="read_only",
            requires_approval=False,
        )
        self.approved_apps = APPROVED_APPS

    async def execute(self, args: Dict[str, Any]) -> str:
        app_name = str(args.get("app_name") or args.get("app") or args.get("name") or "").strip().lower()
        if not app_name:
            raise ValueError("app_name is required (e.g. 'notepad', 'calc').")

        # Exact check against approved applications (e.g., 'calculator' is unapproved, only 'calc' is approved)
        if app_name not in self.approved_apps:
            allowed = ", ".join(self.approved_apps.keys())
            raise PermissionError(
                f"Application '{app_name}' is not approved. Permitted apps: {allowed}"
            )

        app_path = self.approved_apps[app_name]

        try:
            subprocess.Popen([app_path], shell=False)
            logger.info(f"OpenApp tool successfully launched '{app_name}' via '{app_path}'")
            return "opened"
        except Exception as exc:
            logger.error(f"Failed to launch app '{app_name}': {exc}")
            try:
                subprocess.Popen([f"{app_name}.exe"], shell=False)
                return "opened"
            except Exception as e2:
                raise RuntimeError(f"Could not open {app_name}: {e2}")


# ---------------------------------------------------------------------------
# Tool 2: ListFiles
# ---------------------------------------------------------------------------

class ListFiles(Tool):
    """List files in approved folder."""

    def __init__(self):
        super().__init__(
            name="list_files",
            description="List files in approved folder",
            risk_level="read_only",
            requires_approval=False,
        )
        self.approved_folders = [
            r"C:\Users\Hasan\Documents",
            r"C:\Users\Hasan\Downloads",
        ]

    async def execute(self, args: Dict[str, Any]) -> List[str]:
        raw_path = str(args.get("folder_path") or args.get("folder") or args.get("directory") or args.get("path") or "").strip().strip("'\"")

        if not raw_path:
            target_path = Path(r"C:\Users\Hasan\Documents")
        else:
            low = raw_path.lower()
            if "download" in low and ("c:\\" not in low and "/" not in low):
                target_path = Path(r"C:\Users\Hasan\Downloads")
            elif "document" in low and ("c:\\" not in low and "/" not in low):
                target_path = Path(r"C:\Users\Hasan\Documents")
            elif low in [".", "./", "ben", "project"]:
                target_path = PROJECT_ROOT.resolve()
            else:
                target_path = Path(raw_path).expanduser().resolve()

        if not is_path_approved(target_path):
            raise PermissionError(f"Folder '{target_path}' is outside approved folders.")

        fs_path = map_hasan_path(target_path)
        if not fs_path.exists():
            try:
                fs_path.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

        if not fs_path.exists():
            return []

        # List .txt, .py, .md files (and directories) in folder
        matched_files = []
        for entry in fs_path.iterdir():
            if entry.name.startswith(".") or entry.name in BLOCKED_FILES:
                continue
            if entry.is_dir():
                matched_files.append(f"{entry.name}/")
            elif entry.suffix.lower() in APPROVED_EXTENSIONS:
                matched_files.append(entry.name)

        return sorted(matched_files)


# ---------------------------------------------------------------------------
# Tool 3: ReadFile
# ---------------------------------------------------------------------------

class ReadFile(Tool):
    """Read text file content."""

    def __init__(self):
        super().__init__(
            name="read_file",
            description="Read text file content",
            risk_level="read_only",
            requires_approval=False,
        )
        self.approved_extensions = [".txt", ".py", ".md"]
        self.approved_folders = [
            r"C:\Users\Hasan\Documents",
            r"C:\Users\Hasan\Downloads",
        ]

    async def execute(self, args: Dict[str, Any]) -> str:
        raw_path = str(args.get("file_path") or args.get("path") or args.get("file") or "").strip().strip("'\"")
        if not raw_path:
            raise ValueError("file_path is required.")

        candidate = Path(raw_path)
        if not candidate.is_absolute():
            if (PROJECT_ROOT / raw_path).exists():
                target_path = (PROJECT_ROOT / raw_path).resolve()
            elif (USER_HOME / "Documents" / raw_path).exists():
                target_path = (USER_HOME / "Documents" / raw_path).resolve()
            elif (USER_HOME / "Downloads" / raw_path).exists():
                target_path = (USER_HOME / "Downloads" / raw_path).resolve()
            else:
                target_path = (PROJECT_ROOT / raw_path).resolve()
        else:
            target_path = candidate.resolve()

        # Block sensitive files
        if target_path.name in BLOCKED_FILES or target_path.name.startswith(".env"):
            raise PermissionError("Access to sensitive credential or environment files is strictly prohibited.")

        # Check extension
        if target_path.suffix.lower() not in self.approved_extensions:
            allowed = ", ".join(self.approved_extensions)
            raise ValueError(f"File extension '{target_path.suffix}' is not permitted. Allowed: {allowed}")

        # Check approved folders
        if not is_path_approved(target_path):
            raise PermissionError(f"File '{target_path}' is outside approved folders.")

        fs_path = map_hasan_path(target_path)
        if not fs_path.exists():
            raise FileNotFoundError(f"File not found: {target_path.name}")

        if not fs_path.is_file():
            raise ValueError(f"Path is not a regular file: {target_path.name}")

        # Read first 2000 characters
        with open(fs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(2000)

        return content


# ---------------------------------------------------------------------------
# Tool 4: DetectDevices (Preserved for Phase 7 Dashboard & /devices)
# ---------------------------------------------------------------------------

class DetectDevices(Tool):
    """Scan local network for connected devices (mock version)."""

    def __init__(self):
        super().__init__(
            name="detect_devices",
            description="Scan local network for connected devices on 192.168.x.x subnet.",
            risk_level="read_only",
            requires_approval=False,
        )

    async def execute(self, args: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        return [
            {"ip": "192.168.1.10", "hostname": "Laptop", "mac": "AA:BB:CC:DD:EE:FF", "status": "online"},
            {"ip": "192.168.1.20", "hostname": "Phone", "mac": "11:22:33:44:55:66", "status": "online"},
            {"ip": "192.168.1.30", "hostname": "Smart TV", "mac": "99:88:77:66:55:44", "status": "offline"},
        ]


# Backward compatibility aliases
OpenAppTool = OpenApp
ListFilesTool = ListFiles
ReadFileTool = ReadFile
DetectDevicesTool = DetectDevices

# Register built-in tools
if not registry.get("open_app"):
    registry.add(OpenApp())
if not registry.get("list_files"):
    registry.add(ListFiles())
if not registry.get("read_file"):
    registry.add(ReadFile())
if not registry.get("detect_devices"):
    registry.add(DetectDevices())
