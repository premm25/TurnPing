"""Design tokens, stylesheet and the programmatically drawn TurnPing icon."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    ok: str
    warn: str
    err: str
    info: str
    selection: str


LIGHT = Palette(
    bg="#F6F6F9",
    surface="#FFFFFF",
    surface2="#EFEFF4",
    border="#DEDEE7",
    text="#1A1A23",
    muted="#5C5D6E",
    accent="#5446D9",
    accent_hover="#4437C4",
    accent_text="#FFFFFF",
    ok="#1C8A4C",
    warn="#9A6206",
    err="#C93636",
    info="#2F62C9",
    selection="#E4E1FB",
)
DARK = Palette(
    bg="#131319",
    surface="#1C1C24",
    surface2="#25252F",
    border="#34343F",
    text="#ECECF3",
    muted="#A3A3B5",
    accent="#8F85FF",
    accent_hover="#A49CFF",
    accent_text="#14141A",
    ok="#43C985",
    warn="#E3AA4E",
    err="#F47272",
    info="#77A2FF",
    selection="#2F2B55",
)

BRAND_TOP = "#6A5CFF"
BRAND_BOTTOM = "#3F2FBF"


def is_dark() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def current() -> Palette:
    return DARK if is_dark() else LIGHT


def level_color(level: str, p: Palette | None = None) -> str:
    p = p or current()
    return {"ok": p.ok, "info": p.info, "warn": p.warn, "error": p.err}.get(level, p.muted)


def _arrow_files(p: Palette) -> tuple[str, str]:
    """Render chevrons for combo/spin boxes (Qt stylesheets need image files)."""
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.gettempdir()) / "turnping-ui"
    folder.mkdir(exist_ok=True)
    out = []
    for name, up in (("down", False), ("up", True)):
        path = folder / f"chevron-{name}-{p.muted.lstrip('#')}.png"
        if not path.exists():
            pm = QPixmap(20, 20)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(p.muted))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            y0, y1 = (12.5, 7.5) if up else (7.5, 12.5)
            painter.drawPolyline([QPointF(5, y0), QPointF(10, y1), QPointF(15, y0)])
            painter.end()
            pm.save(str(path))
        out.append(path.as_posix())
    return out[0], out[1]


def stylesheet(p: Palette) -> str:
    down, up = _arrow_files(p)
    return f"""
    QWidget {{ color: {p.text}; font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
               font-size: 10pt; }}
    QMainWindow, QDialog, #Page {{ background: {p.bg}; }}
    #Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
    #Sidebar QListWidget {{ background: transparent; border: none; outline: none; }}
    #Sidebar QListWidget::item {{ padding: 9px 14px; margin: 2px 8px; border-radius: 6px; color: {p.muted}; }}
    #Sidebar QListWidget::item:selected {{ background: {p.selection}; color: {p.text}; font-weight: 600; }}
    #Sidebar QListWidget::item:hover:!selected {{ background: {p.surface2}; }}
    #BrandName {{ font-size: 13pt; font-weight: 700; }}
    #Tagline, .muted, QLabel[muted="true"] {{ color: {p.muted}; }}
    #PageTitle {{ font-size: 16pt; font-weight: 650; }}
    #SectionTitle {{ font-size: 11pt; font-weight: 650; }}
    #Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
    #CardValue {{ font-size: 12pt; font-weight: 650; }}
    #Banner {{ background: {p.surface}; border: 1px solid {p.warn}; border-left: 4px solid {p.warn};
               border-radius: 8px; }}
    #BannerError {{ background: {p.surface}; border: 1px solid {p.err}; border-left: 4px solid {p.err};
               border-radius: 8px; }}
    QPushButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 6px;
                   padding: 6px 14px; }}
    QPushButton:hover {{ border-color: {p.accent}; }}
    QPushButton:focus {{ border: 2px solid {p.accent}; padding: 5px 13px; }}
    QPushButton:disabled {{ color: {p.muted}; }}
    QPushButton[primary="true"] {{ background: {p.accent}; color: {p.accent_text}; border: none;
                                   font-weight: 600; }}
    QPushButton[primary="true"]:hover {{ background: {p.accent_hover}; }}
    QPushButton[primary="true"]:focus {{ border: 2px solid {p.text}; padding: 4px 12px; }}
    QLineEdit, QComboBox, QSpinBox, QTimeEdit {{ background: {p.surface}; border: 1px solid {p.border};
                   border-radius: 6px; padding: 5px 8px; min-height: 20px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {{ border: 2px solid {p.accent};
                   padding: 4px 7px; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; width: 18px; background: transparent; }}
    QAbstractSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
    QAbstractSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
    QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border};
                   selection-background-color: {p.selection}; selection-color: {p.text}; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox:focus {{ color: {p.accent}; }}
    QGroupBox {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px;
                 margin-top: 14px; padding: 14px 12px 10px 12px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
    QTableView, QTreeView, QListView#Plain {{ background: {p.surface}; alternate-background-color: {p.surface2};
                 border: 1px solid {p.border}; border-radius: 8px; gridline-color: {p.border};
                 selection-background-color: {p.selection}; selection-color: {p.text}; }}
    QTableView::item {{ padding: 4px 10px; }}
    QHeaderView::section {{ background: {p.surface2}; color: {p.muted}; border: none;
                 border-bottom: 1px solid {p.border}; padding: 6px 8px; font-weight: 600; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QToolTip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; }}
    QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {p.selection}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
    #Pill {{ border-radius: 10px; padding: 3px 10px; font-weight: 600; }}
    #Popup {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
    #PopupTitle {{ font-weight: 700; font-size: 10.5pt; }}
    #PopupAccent {{ border-radius: 2px; }}
    """


def draw_logo(size: int, *, paused: bool = False, badge: str | None = None) -> QPixmap:
    """The TurnPing mark: a ping dot with two signal arcs on a rounded square."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0, 0, size, size).adjusted(size * 0.04, size * 0.04, -size * 0.04, -size * 0.04)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    if paused:
        gradient.setColorAt(0, QColor("#9A9AA8"))
        gradient.setColorAt(1, QColor("#5E5E6B"))
    else:
        gradient.setColorAt(0, QColor(BRAND_TOP))
        gradient.setColorAt(1, QColor(BRAND_BOTTOM))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(rect, size * 0.24, size * 0.24)

    cx, cy = rect.left() + rect.width() * 0.33, rect.bottom() - rect.height() * 0.33
    white = QColor("#FFFFFF")
    painter.setBrush(white)
    r = size * 0.095
    painter.drawEllipse(QPointF(cx, cy), r, r)
    pen = QPen(white)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setWidthF(max(1.5, size * 0.085))
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for radius in (size * 0.26, size * 0.43):
        arc = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawArc(arc, 5 * 16, 80 * 16)
    if badge:
        br = size * 0.17
        center = QPointF(rect.right() - br * 0.9, rect.top() + br * 0.9)
        painter.setPen(QPen(QColor("#FFFFFF"), max(1.0, size * 0.04)))
        painter.setBrush(QColor(badge))
        painter.drawEllipse(center, br, br)
    painter.end()
    return pm


def app_icon(*, paused: bool = False, badge: str | None = None) -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(draw_logo(s, paused=paused, badge=badge))
    return icon
