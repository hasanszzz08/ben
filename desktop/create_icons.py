"""Generate BEN extension icons using PySide6 QPainter.

Run with:
    .venv\\Scripts\\python desktop\\create_icons.py

Outputs:
    extension/icons/icon16.png
    extension/icons/icon32.png
    extension/icons/icon48.png
    extension/icons/icon128.png
"""

import sys
from pathlib import Path

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import (
    QPainter, QColor, QLinearGradient, QBrush, QPen, QFont, QImage, QRadialGradient
)
from PySide6.QtCore import Qt, QRectF, QPointF

SIZES = [16, 32, 48, 128]
OUT_DIR = PROJECT_ROOT / "extension" / "icons"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def draw_icon(size: int) -> QImage:
    """Draw a single BEN icon at the given size."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    rect = QRectF(0, 0, size, size)
    margin = size * 0.04
    inner = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

    # ── Background: radial glow (dark navy center → deep blue edge)
    bg = QRadialGradient(size / 2, size / 2, size * 0.55)
    bg.setColorAt(0.0, QColor("#0f1e3d"))
    bg.setColorAt(1.0, QColor("#060b17"))
    p.setBrush(QBrush(bg))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(inner)

    # ── Outer glow ring
    if size >= 32:
        glow_pen = QPen(QColor(26, 127, 232, 80), size * 0.04)
        p.setPen(glow_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        shrink = size * 0.06
        p.drawEllipse(QRectF(shrink, shrink, size - 2 * shrink, size - 2 * shrink))

    # ── Blue accent ring
    ring_pen = QPen(QColor("#1a7fe8"), max(1, size * 0.025))
    p.setPen(ring_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(inner)

    # ── Letter "B"
    font_size = size * 0.48
    font = QFont("Segoe UI", int(font_size))
    font.setBold(True)
    p.setFont(font)

    # Gradient text (white → electric blue)
    text_grad = QLinearGradient(0, size * 0.2, 0, size * 0.85)
    text_grad.setColorAt(0.0, QColor("#e2f0ff"))
    text_grad.setColorAt(1.0, QColor("#1a7fe8"))
    p.setPen(QPen(QBrush(text_grad), 1))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "B")

    p.end()
    return img


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    for size in SIZES:
        img = draw_icon(size)
        path = OUT_DIR / f"icon{size}.png"
        img.save(str(path))
        print(f"  OK  {path}")
    print("\nAll icons generated successfully.")


if __name__ == "__main__":
    main()
