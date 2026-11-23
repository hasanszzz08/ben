"""BEN Desktop Companion — Phase 4 Windows Desktop App (PySide6).

Features:
- Floating futuristic interface (Navy/Black with glowing blue accents)
- Compact floating companion mode (440x680) + expandable full window mode (920x820)
- Always-on-Top pinning toggle
- Local backend health monitoring with 1-click auto-launcher
- Embedded live chat, SSE streaming, and Memory Control Panel
- Native Windows System Tray integration with background minimize
- Global summon hotkeys: Alt+B and Ctrl+Shift+Space
- Prominent Emergency Stop button (cancels active streaming / backend generation)
"""

import sys
import os
import subprocess
import threading
import urllib.request
import json
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import (
    Qt, QUrl, QTimer, Signal, QObject, QSize
)
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QAction, QKeySequence
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QSystemTrayIcon, QMenu,
    QStackedWidget, QMessageBox, QFrame
)
from PySide6.QtWebEngineWidgets import QWebEngineView

# Try to import global hotkey library
try:
    import keyboard
    HAS_KEYBOARD = True
except Exception:
    HAS_KEYBOARD = False

BACKEND_URL = "http://127.0.0.1:8000"
STATUS_ENDPOINT = f"{BACKEND_URL}/api/chat/status"
STOP_ENDPOINT = f"{BACKEND_URL}/api/chat/stop"


def create_ben_icon(size: int = 64) -> QIcon:
    """Generate a sharp, futuristic BEN brand icon programmatically."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # Blue rounded square
    painter.setBrush(QColor("#1d4ed8"))
    painter.setPen(QColor("#3b82f6"))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, size // 5, size // 5)

    # White 'B' letter
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", size // 2, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "B")
    painter.end()

    return QIcon(pixmap)


class HotkeyBridge(QObject):
    """Bridge for cross-thread global hotkey signal."""
    hotkey_triggered = Signal()


class BENDesktopWindow(QMainWindow):
    """BEN Windows Desktop Companion Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BEN — Personal AI Agent")
        self.setWindowIcon(create_ben_icon(64))

        # Window state
        self.is_compact = True
        self.is_always_on_top = False
        self.backend_process = None
        self.hotkey_bridge = HotkeyBridge()
        self.hotkey_bridge.hotkey_triggered.connect(self.toggle_summon)

        # Base Geometry: Compact floating side-panel by default
        self.compact_size = QSize(440, 680)
        self.expanded_size = QSize(940, 820)
        self.resize(self.compact_size)

        self._setup_ui()
        self._setup_tray()
        self._setup_global_hotkeys()

        # Start backend health polling
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_backend_status)
        self.poll_timer.start(3000)

        # Initial check
        QTimer.singleShot(500, self.check_backend_status)

    def _setup_ui(self):
        """Construct the futuristic dark UI layout."""
        # Main central container
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Futuristic Top Bar ────────────────────────────────────
        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")
        self.top_bar.setFixedHeight(48)
        self.top_bar.setStyleSheet("""
            #topBar {
                background: #0d1526;
                border-bottom: 1px solid rgba(59, 130, 246, 0.25);
            }
        """)

        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(12, 6, 12, 6)
        top_layout.setSpacing(8)

        # Logo + Title
        logo_label = QLabel("BEN")
        logo_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 15px;
                font-weight: 800;
                letter-spacing: 0.05em;
                background: #2563eb;
                padding: 3px 8px;
                border-radius: 6px;
            }
        """)
        top_layout.addWidget(logo_label)

        # Status badge
        self.status_dot = QLabel("● Connecting…")
        self.status_dot.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: 600;")
        top_layout.addWidget(self.status_dot)

        top_layout.addStretch()

        # Emergency Stop Button
        self.stop_btn = QPushButton("🛑 Stop")
        self.stop_btn.setToolTip("Emergency Stop: Immediately cancel active generation / task")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(248, 113, 113, 0.15);
                border: 1px solid rgba(248, 113, 113, 0.4);
                color: #f87171;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: rgba(248, 113, 113, 0.3);
                border-color: #f87171;
            }
        """)
        self.stop_btn.clicked.connect(self.emergency_stop)
        top_layout.addWidget(self.stop_btn)

        # Always on Top pin
        self.pin_btn = QPushButton("📌 Pin")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("Toggle Always on Top")
        self.pin_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(59, 130, 246, 0.2);
                color: #8a9bbf;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:checked {
                background: rgba(59, 130, 246, 0.2);
                color: #60a5fa;
                border-color: #3b82f6;
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """)
        self.pin_btn.toggled.connect(self.toggle_always_on_top)
        top_layout.addWidget(self.pin_btn)

        # Compact / Expanded Size Toggle
        self.size_toggle_btn = QPushButton("⤢ Expand")
        self.size_toggle_btn.setToolTip("Switch between Compact companion and Full view")
        self.size_toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(59, 130, 246, 0.2);
                color: #8a9bbf;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(59, 130, 246, 0.15);
                color: #ffffff;
            }
        """)
        self.size_toggle_btn.clicked.connect(self.toggle_size)
        top_layout.addWidget(self.size_toggle_btn)

        main_layout.addWidget(self.top_bar)

        # ── Stacked Central Content ───────────────────────────────
        self.stack = QStackedWidget()

        # Page 0: Offline Fallback Screen
        self.offline_widget = self._create_offline_screen()
        self.stack.addWidget(self.offline_widget)

        # Page 1: QWebEngineView (Live BEN Web Interface)
        self.web_view = QWebEngineView()
        self.web_view.setStyleSheet("background: #070b14;")
        self.stack.addWidget(self.web_view)

        main_layout.addWidget(self.stack)

        # ── Bottom Status Bar ─────────────────────────────────────
        self.bottom_bar = QFrame()
        self.bottom_bar.setFixedHeight(26)
        self.bottom_bar.setStyleSheet("""
            QFrame {
                background: #070b14;
                border-top: 1px solid rgba(59, 130, 246, 0.15);
            }
        """)
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(12, 0, 12, 0)

        self.model_info_label = QLabel("Model: gemini-3.8-flash")
        self.model_info_label.setStyleSheet("color: #4a5a7a; font-size: 10px; font-weight: 600;")
        bottom_layout.addWidget(self.model_info_label)

        bottom_layout.addStretch()

        shortcut_label = QLabel("Hotkeys: Alt+B  |  Ctrl+Shift+Space")
        shortcut_label.setStyleSheet("color: #4a5a7a; font-size: 10px;")
        bottom_layout.addWidget(shortcut_label)

        main_layout.addWidget(self.bottom_bar)

    def _create_offline_screen(self) -> QWidget:
        """Create the sleek offline splash screen when the backend isn't running."""
        w = QWidget()
        w.setStyleSheet("background: #070b14;")
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(14)

        icon_lbl = QLabel("⬡")
        icon_lbl.setStyleSheet("color: #3b82f6; font-size: 54px;")
        icon_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_lbl)

        title_lbl = QLabel("BEN Backend is Offline")
        title_lbl.setStyleSheet("color: #e8edf5; font-size: 18px; font-weight: 700;")
        title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_lbl)

        sub_lbl = QLabel("Start the local FastAPI service on 127.0.0.1:8000 to connect.")
        sub_lbl.setStyleSheet("color: #8a9bbf; font-size: 13px;")
        sub_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(sub_lbl)

        launch_btn = QPushButton("🚀 Launch Local BEN Backend")
        launch_btn.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                font-weight: 700;
                font-size: 13px;
                padding: 10px 20px;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: #3b82f6;
            }
        """)
        launch_btn.clicked.connect(self.launch_backend)
        layout.addWidget(launch_btn, alignment=Qt.AlignCenter)

        return w

    def _setup_tray(self):
        """Configure Windows System Tray icon and menu."""
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(create_ben_icon(32))
        self.tray.setToolTip("BEN — Personal AI Agent")

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background: #0d1526;
                color: #e8edf5;
                border: 1px solid rgba(59, 130, 246, 0.3);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item:selected {
                background: rgba(59, 130, 246, 0.2);
                color: #60a5fa;
            }
        """)

        toggle_action = menu.addAction("Show / Hide BEN (Alt+B)")
        toggle_action.triggered.connect(self.toggle_summon)

        menu.addSeparator()

        stop_action = menu.addAction("🛑 Emergency Stop")
        stop_action.triggered.connect(self.emergency_stop)

        browser_action = menu.addAction("🌐 Open in Web Browser")
        browser_action.triggered.connect(lambda: os.system(f"start {BACKEND_URL}"))

        menu.addSeparator()

        quit_action = menu.addAction("Quit BEN Companion")
        quit_action.triggered.connect(self.quit_application)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_summon()

    def _setup_global_hotkeys(self):
        """Register Alt+B and Ctrl+Shift+Space global summon shortcuts."""
        if not HAS_KEYBOARD:
            return

        def on_hotkey():
            self.hotkey_bridge.hotkey_triggered.emit()

        try:
            keyboard.add_hotkey("alt+b", on_hotkey, suppress=False)
            keyboard.add_hotkey("ctrl+shift+space", on_hotkey, suppress=False)
        except Exception as e:
            print(f"Warning: Could not register global keyboard hook: {e}")

    def toggle_summon(self):
        """Summon or hide the floating companion window."""
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def toggle_always_on_top(self, checked: bool):
        """Pin the window to stay on top of all other applications."""
        self.is_always_on_top = checked
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
            self.pin_btn.setText("📌 Pinned")
        else:
            flags &= ~Qt.WindowStaysOnTopHint
            self.pin_btn.setText("📌 Pin")
        self.setWindowFlags(flags)
        self.show()

    def toggle_size(self):
        """Toggle between compact companion and expanded view."""
        if self.is_compact:
            self.resize(self.expanded_size)
            self.size_toggle_btn.setText("⤡ Compact")
            self.is_compact = False
        else:
            self.resize(self.compact_size)
            self.size_toggle_btn.setText("⤢ Expand")
            self.is_compact = True

    def emergency_stop(self):
        """Trigger backend cancellation endpoint."""
        def call_stop():
            try:
                req = urllib.request.Request(STOP_ENDPOINT, data=b"{}", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    pass
            except Exception:
                pass

        threading.Thread(target=call_stop, daemon=True).start()
        self.status_dot.setText("● Stopped")
        self.status_dot.setStyleSheet("color: #f87171; font-size: 11px; font-weight: 600;")
        QTimer.singleShot(1500, self.check_backend_status)

    def check_backend_status(self):
        """Query /api/chat/status and update view state."""
        def poll():
            try:
                with urllib.request.urlopen(STATUS_ENDPOINT, timeout=1.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        return True, data
            except Exception:
                return False, None
            return False, None

        def handle_result(is_online, data):
            if is_online:
                self.status_dot.setText("● Online")
                self.status_dot.setStyleSheet("color: #34d399; font-size: 11px; font-weight: 600;")
                if data and "model" in data:
                    self.model_info_label.setText(f"Model: {data['model']}")

                # Switch to WebEngine view if currently on offline screen
                if self.stack.currentIndex() == 0:
                    self.web_view.setUrl(QUrl(BACKEND_URL))
                    self.stack.setCurrentIndex(1)
            else:
                self.status_dot.setText("● Offline")
                self.status_dot.setStyleSheet("color: #f87171; font-size: 11px; font-weight: 600;")
                self.stack.setCurrentIndex(0)

        threading.Thread(
            target=lambda: self.run_on_main(poll(), handle_result),
            daemon=True
        ).start()

    def run_on_main(self, result, handler):
        is_online, data = result
        QTimer.singleShot(0, lambda: handler(is_online, data))

    def launch_backend(self):
        """Launch python run.py as a detached process."""
        python_exe = sys.executable
        run_script = str(PROJECT_ROOT / "run.py")

        self.status_dot.setText("● Starting…")
        self.status_dot.setStyleSheet("color: #3b82f6; font-size: 11px; font-weight: 600;")

        try:
            self.backend_process = subprocess.Popen(
                [python_exe, run_script],
                cwd=str(PROJECT_ROOT),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            )
            QTimer.singleShot(1200, self.check_backend_status)
        except Exception as e:
            QMessageBox.critical(self, "Error Launching Backend", str(e))

    def closeEvent(self, event):
        """Minimize to system tray instead of exiting on window close."""
        if self.tray.isVisible():
            self.hide()
            self.tray.showMessage(
                "BEN is still running",
                "BEN is minimized to your system tray. Press Alt+B or click the tray icon to summon.",
                QSystemTrayIcon.Information,
                2500
            )
            event.ignore()
        else:
            event.accept()

    def quit_application(self):
        """Completely exit the desktop companion."""
        if HAS_KEYBOARD:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        self.tray.hide()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BEN")
    app.setOrganizationName("BEN Agent")
    app.setQuitOnLastWindowClosed(False)

    window = BENDesktopWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
