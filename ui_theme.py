# -*- coding: utf-8 -*-
"""
Cyberpunk 主題：配色、字型、圖示、QSS，以及切角卡片等自繪元件。
"""
from __future__ import annotations
from i18n import LANG, tr

from string import Template

from PySide6.QtCore import QByteArray, QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

import ytdl_core as core

# ======================================================================= 配色
DARK = dict(
    name="dark",
    bg="#060A13", nav="#080D18", panel="#0B1220", panel2="#0F1A2E", field="#060A13",
    line="#1C2A44", line_hi="#24406B", hover="#101B30",
    text="#E4EEFF", text2="#B4C3DD", muted="#8C9DBB", disabled="#4A5B78",
    accent="#22E4FF", accent_text="#04121A",
    primary_bg="#22E4FF", primary_fg="#04121A",
    blue="#2E7BFF", green="#3BE8B0", amber="#FFB547", red="#FF5C7A", red_text="#FF8DA2",
    pink="#FB7299", yt="#FF8DA2", purple="#B07CFF",
    term_bg="#03060C", footer="#04070E", footer_text="#8C9DBB",
    grid_alpha=0.035, glow_alpha=0.32,
)
LIGHT = dict(
    name="light",
    bg="#EDF2F9", nav="#FFFFFF", panel="#FFFFFF", panel2="#F3F7FC", field="#F7FAFD",
    line="#D3DDEA", line_hi="#AFC2DA", hover="#EEF3FA",
    text="#0A1426", text2="#33415C", muted="#4A5B78", disabled="#9AA8BE",
    accent="#0077A8", accent_text="#FFFFFF",
    primary_bg="#0A1426", primary_fg="#22E4FF",
    blue="#1F5FE0", green="#0B6E4B", amber="#A86200", red="#C8293F", red_text="#C8293F",
    pink="#B0174F", yt="#C8293F", purple="#7A3FD0",
    term_bg="#0A1426", footer="#0A1426", footer_text="#B4C3DD",
    grid_alpha=0.06, glow_alpha=0.10,
)
# 終端機永遠用深色字色
TERM = dict(text="#C9D6EE", dim="#6F81A3", cyan="#22E4FF", blue="#5B9BFF", purple="#B07CFF",
            green="#3BE8B0", amber="#FFB547", red="#FF5C7A", pink="#FB7299", sep="#33415C")

T: dict = dict(DARK)          # 目前使用中的配色（全域）
EFFECTS = {"glow": True, "grid": True}


def set_palette(name: str):
    T.clear()
    T.update(LIGHT if name == "light" else DARK)


def qc(hex_: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def ah(hex_: str, alpha: float) -> str:
    """#RRGGBB + 透明度 → QSS 用的 #AARRGGBB"""
    h = hex_.lstrip("#")
    return f"#{int(alpha * 255):02X}{h.upper()}"


# ======================================================================= 字型
# 中日文字型依介面語言挑（同一個漢字在繁中 / 簡中 / 日文的字形不同）；英文介面以 Segoe UI 為主
_CJK_FAMILIES = {
    "zh_CN": ["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "PingFang SC"],
    "ja": ["Yu Gothic UI", "Meiryo UI", "Meiryo", "Noto Sans JP", "Hiragino Sans"],
}.get(LANG, ["Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans TC", "PingFang TC"])
UI_FAMILIES = ["Segoe UI"] + _CJK_FAMILIES if LANG == "en" else _CJK_FAMILIES + ["Segoe UI"]
MONO_FAMILIES = ["JetBrains Mono", "Consolas", _CJK_FAMILIES[0], "monospace"]
DISPLAY_FAMILIES = ["Orbitron", "Segoe UI", _CJK_FAMILIES[0]]


def load_fonts():
    if core.FONTS_DIR.is_dir():
        for f in core.FONTS_DIR.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(f))


def _font(families, px: int, weight=QFont.Weight.Normal, spacing: float | None = None) -> QFont:
    f = QFont()
    f.setFamilies(families)
    f.setPixelSize(px)
    f.setWeight(weight)
    if spacing is not None:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def f_ui(px=14, bold=False):
    return _font(UI_FAMILIES, px, QFont.Weight.Bold if bold else QFont.Weight.Normal)


def f_mono(px=12, bold=False):
    return _font(MONO_FAMILIES, px, QFont.Weight.DemiBold if bold else QFont.Weight.Normal)


def f_disp(px=11, bold=False, spacing=130):
    """spacing：舊的百分比寫法，換算成固定字距（最多 0.32em），避免 Orbitron 在 Windows 上字距過寬被截掉"""
    f = _font(DISPLAY_FAMILIES, px, QFont.Weight.Bold if bold else QFont.Weight.Medium)
    em = min(0.32, max(0.0, (spacing - 100) / 100 * 0.5))
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, round(px * em, 1))
    return f


# ======================================================================= 圖示
ICONS = {
    "download": '<path d="M12 3v13"/><path d="M7 11l5 5 5-5"/><path d="M4 21h16"/>',
    "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    "terminal": '<path d="M4 17l6-6-6-6"/><path d="M12 19h8"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2L20 3"/><path d="M17 6l3 3"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3"/><path d="M12 19v3"/><path d="M4.9 4.9l2.1 2.1"/><path d="M17 17l2.1 2.1"/><path d="M2 12h3"/><path d="M19 12h3"/><path d="M4.9 19.1L7 17"/><path d="M17 7l2.1-2.1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "check": '<path d="M5 12l5 5L20 7"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "play": '<path d="M7 4l13 8-13 8z" fill="{c}" stroke="none"/>',
    "stop": '<path d="M6 6h12v12H6z" fill="{c}" stroke="none"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5"/><path d="M12 18h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16h.01"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.9 4.9l1.4 1.4"/><path d="M17.7 17.7l1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M4.9 19.1l1.4-1.4"/><path d="M17.7 6.3l1.4-1.4"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "chev_down": '<path d="M6 9l6 6 6-6"/>',
    "chev_up": '<path d="M18 15l-6-6-6 6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "clip": '<rect x="6" y="4" width="12" height="17"/><path d="M9 4V2h6v2"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7"/><path d="M8 14h8"/>',
    "qr": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3z"/><path d="M20 14v7"/><path d="M14 20h3"/>',
    "logout": '<path d="M9 21H4V3h5"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "exe": '<rect x="3" y="4" width="18" height="16"/><path d="M3 9h18"/><path d="M7 14l2 2-2 2"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
    "bell": '<path d="M6 10a6 6 0 1 1 12 0c0 3 1 4.5 2 6H4c1-1.5 2-3 2-6z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
}


def svg_pixmap(name: str, color: str, size: int = 18, stroke: float = 2.0) -> QPixmap:
    body = ICONS.get(name, "").replace("{c}", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(pm)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def make_icon(name: str, role="text2", on_role=None, size=18, stroke=2.0, disabled_role="disabled") -> QIcon:
    """role 等是配色名稱（T 的 key）或直接的色碼。on_role：按下 / 選取時的顏色。"""
    def col(r):
        if r == "transparent":
            return "none"
        return r if r.startswith("#") else T[r]
    ic = QIcon()
    ic.addPixmap(svg_pixmap(name, col(role), size, stroke), QIcon.Mode.Normal, QIcon.State.Off)
    dis = "transparent" if role == "transparent" else disabled_role
    ic.addPixmap(svg_pixmap(name, col(dis), size, stroke), QIcon.Mode.Disabled, QIcon.State.Off)
    if on_role:
        ic.addPixmap(svg_pixmap(name, col(on_role), size, stroke), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(svg_pixmap(name, col(disabled_role), size, stroke), QIcon.Mode.Disabled, QIcon.State.On)
    return ic


def write_assets() -> dict:
    """QSS 用的小圖（下拉箭頭、勾勾），依主題寫到 cache 資料夾。"""
    core.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, name, color in (("chev", "chev_down", T["accent"]), ("chev_dis", "chev_down", T["disabled"]),
                             ("check", "check", T["accent_text"])):
        body = ICONS[name]
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
               f'stroke="{color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
        path = core.CACHE_DIR / f"{key}_{T['name']}.svg"
        path.write_text(svg, encoding="utf-8")
        out[key] = path.as_posix()
    return out


# ======================================================================= QSS
QSS = Template(r"""
* { outline: 0; }
QWidget { color: $text; }
QMainWindow { background: $bg; }
QToolTip { background: $panel2; color: $text; border: 1px solid $line_hi; padding: 6px 8px; }

/* ---------- 導覽列 ---------- */
QFrame#Sidebar { background: $nav; border: none; border-right: 1px solid $line; }
QPushButton#Nav { text-align: left; padding: 0 12px; min-height: 44px; border: 1px solid transparent;
                  background: transparent; color: $text2; }
QPushButton#Nav:hover { background: $hover; color: $text; border: 1px solid transparent; }
QPushButton#Nav:checked { color: $accent; background: $accent_soft; border: 1px solid $accent_line; }
QLabel#NavBadge { background: $blue; color: #FFFFFF; padding: 1px 7px; }
QFrame#SysBox { background: $panel; border: 1px solid $line; }
QLabel#Version { color: $muted; }

/* ---------- 頁尾 ---------- */
QFrame#Footer { background: $footer; border: none; border-top: 1px solid $line; }
QLabel#FooterText, QLabel#FooterRight { color: $footer_text; }
QLabel#FooterPrompt { color: #22E4FF; }

/* ---------- 文字 ---------- */
QLabel#Eyebrow { color: $accent; }
QLabel#Muted { color: $muted; }
QLabel#Text2 { color: $text2; }
QLabel#Hint { color: $muted; }
QLabel#Title { color: $text; }
QLabel#Tag { color: $accent; }
QLabel[tone="ok"] { color: $green; }
QLabel[tone="warn"] { color: $amber; }
QLabel[tone="bad"] { color: $red_text; }
QLabel[tone="accent"] { color: $accent; }
QLabel[tone="muted"] { color: $muted; }

/* ---------- 卡片 ---------- */
QFrame#Card { background: $panel; border: 1px solid $line; }
QFrame#Inset { background: $panel2; border: 1px dashed $line_hi; }
QFrame#PathRow { background: $field; border: 1px solid $line; }
QFrame#LiveRow { background: transparent; border: 1px solid $line; }
QFrame#JobCard { background: $panel; border: 1px solid $line; }
QFrame#JobCard[state="failed"] { border: 1px solid $red_line; }
QFrame#TipBox { background: $amber_soft; border: 1px solid $amber_line; }
QFrame#Seg { background: $field; border: 1px solid $line; }

/* ---------- 標籤 Chip ---------- */
QLabel#Chip { border: 1px solid $line; padding: 3px 9px; color: $text2; background: transparent; }
QLabel#Chip[tone="accent"] { border: 1px solid $accent_line; color: $accent; }
QLabel#Chip[tone="ok"] { border: 1px solid $green_line; color: $green; background: $green_soft; }
QLabel#Chip[tone="warn"] { border: 1px solid $amber_line; color: $amber; }
QLabel#Chip[tone="bad"] { border: 1px solid $red_line; color: $red_text; }
QLabel#Chip[tone="pink"] { border: 1px solid $pink_line; color: $pink; }
QLabel#Chip[tone="yt"] { border: 1px solid $yt_line; color: $yt; }

/* ---------- 按鈕 ---------- */
QPushButton { background: transparent; color: $text; border: 1px solid $line_hi; padding: 0 14px; min-height: 38px; }
QPushButton:hover { border: 1px solid $accent; color: $accent; }
QPushButton:pressed { background: $accent_soft; }
QPushButton:disabled { color: $disabled; border: 1px solid $line; }
QPushButton[variant="secondary"] { border: 1px solid $blue; background: $blue_soft; }
QPushButton[variant="danger"] { border: 1px solid $red_line; color: $red_text; }
QPushButton[variant="warn"] { border: 1px solid $amber; color: $amber; }
QPushButton[variant="link"] { border: none; color: $accent; padding: 0 4px; min-height: 24px; }
QPushButton[variant="link"]:hover { text-decoration: underline; }
QPushButton[variant="icon"] { padding: 0; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; }
QPushButton[variant="small"] { min-height: 30px; padding: 0 10px; }
QPushButton#Primary { border: none; background: transparent; }

QPushButton[seg="true"] { border: none; background: transparent; color: $text2; min-height: 36px; padding: 0 16px; }
QPushButton[seg="true"]:hover { color: $text; border: none; }
QPushButton[seg="true"]:checked { background: $blue; color: #FFFFFF; }

QPushButton[chip="true"] { min-height: 34px; padding: 0 14px; color: $text2; border: 1px solid $line_hi; }
QPushButton[chip="true"]:checked { color: $accent; border: 1px solid $accent_line; background: $accent_soft; }

QPushButton[tab="true"] { border: none; border-bottom: 2px solid transparent; min-height: 42px; padding: 0 16px; color: $text2; }
QPushButton[tab="true"]:hover { color: $text; border: none; border-bottom: 2px solid $line_hi; }
QPushButton[tab="true"]:checked { color: $accent; border-bottom: 2px solid $accent; }

QPushButton#AdvToggle { border: none; text-align: left; padding: 0; min-height: 28px; color: $text2; }
QPushButton#AdvToggle:hover { color: $accent; border: none; }

/* ---------- 輸入 ---------- */
QComboBox, QLineEdit, QSpinBox { background: $field; border: 1px solid $line_hi; color: $text; padding: 0 10px;
    min-height: 40px; selection-background-color: $accent; selection-color: $accent_text; }
QPlainTextEdit#UrlEdit { background: $field; border: 1px solid $line; color: $text; padding: 8px 10px;
    selection-background-color: $accent; selection-color: $accent_text; }
QSpinBox QLineEdit, QComboBox QLineEdit { border: none; background: transparent; padding: 0; margin: 0;
    min-height: 0; color: $text; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border: 1px solid $accent_line; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit#UrlEdit:focus { border: 1px solid $accent; }
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled { color: $disabled; border: 1px solid $line; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { image: url("$chev"); width: 14px; height: 14px; }
QComboBox::down-arrow:disabled { image: url("$chev_dis"); }
QComboBox QAbstractItemView { background: $panel2; border: 1px solid $line_hi; color: $text; padding: 4px;
    selection-background-color: $accent_soft; selection-color: $accent; outline: 0; }
QLineEdit[mono="true"] { color: $accent; }

QCheckBox { color: $text2; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $line_hi; background: $field; }
QCheckBox::indicator:hover { border: 1px solid $accent; }
QCheckBox::indicator:checked { background: $accent; border: 1px solid $accent; image: url("$check"); }

QProgressBar { background: $field; border: 1px solid $line; min-height: 8px; max-height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { background: $accent; }
QProgressBar[thin="true"] { min-height: 4px; max-height: 4px; border: none; }

/* ---------- 捲動 ---------- */
QScrollArea#PageScroll { background: transparent; border: none; }
QWidget#PageBody, QWidget#PageViewport { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $line_hi; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $accent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $line_hi; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: $accent; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ---------- 串流清單 ---------- */
QTableWidget#StreamTable { background: $field; border: 1px solid $line; color: $text2; outline: 0;
    gridline-color: transparent; selection-background-color: $accent_soft; selection-color: $accent; }
QTableWidget#StreamTable::item { padding: 0 10px; border-bottom: 1px solid $line; }
QTableWidget#StreamTable::item:hover { background: $hover; }
QTableWidget#StreamTable::item:selected { background: $accent_soft; color: $accent; }
QTableWidget#StreamTable QHeaderView::section { background: $panel2; color: $muted; border: none;
    border-bottom: 1px solid $line_hi; padding: 6px 10px; }
QTableWidget#StreamTable QTableCornerButton::section { background: $panel2; border: none; }

/* ---------- 終端機 ---------- */
QFrame#TermFrame { background: $term_bg; border: 1px solid $line_hi; }
QFrame#TermHead { background: transparent; border: none; border-bottom: 1px solid #1C2A44; }
QLabel#TermHeadText { color: #8C9DBB; }
QPlainTextEdit#Terminal { background: $term_bg; border: none; color: #C9D6EE; padding: 8px 10px;
    selection-background-color: #24406B; selection-color: #FFFFFF; }

QMessageBox { background: $panel; }
QMessageBox QLabel { color: $text; }
""")


def build_qss(assets: dict) -> str:
    v = dict(T)
    v.update(assets)
    v.update(
        accent_soft=ah(T["accent"], 0.10), accent_line=ah(T["accent"], 0.45),
        blue_soft=ah(T["blue"], 0.10),
        green_soft=ah(T["green"], 0.08), green_line=ah(T["green"], 0.40),
        amber_soft=ah(T["amber"], 0.07), amber_line=ah(T["amber"], 0.35),
        red_line=ah(T["red"], 0.50), pink_line=ah(T["pink"], 0.50), yt_line=ah(T["yt"], 0.50),
    )
    return QSS.safe_substitute(v)


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# ======================================================================= 自繪元件
def cut_path(r: QRectF, tr: float = 0, bl: float = 0, br: float = 0) -> QPainterPath:
    """右上 / 左下 / 右下切角的多邊形。"""
    x0, y0, x1, y1 = r.left(), r.top(), r.right(), r.bottom()
    pts = [QPointF(x0, y0)]
    if tr:
        pts += [QPointF(x1 - tr, y0), QPointF(x1, y0 + tr)]
    else:
        pts.append(QPointF(x1, y0))
    if br:
        pts += [QPointF(x1, y1 - br), QPointF(x1 - br, y1)]
    else:
        pts.append(QPointF(x1, y1))
    if bl:
        pts += [QPointF(x0 + bl, y1), QPointF(x0, y1 - bl)]
    else:
        pts.append(QPointF(x0, y1))
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    return path


class CutFrame(QFrame):
    """切角卡片。border/fill 是配色名稱；glow=True 時加上霓虹光暈。"""

    def __init__(self, tr=18, bl=0, border="line_hi", fill="panel", glow=False, glow_color="accent", parent=None):
        super().__init__(parent)
        self.cut_tr, self.cut_bl = tr, bl
        self.border_role, self.fill_role = border, fill
        self.glow, self.glow_color = glow, glow_color
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.refresh_effects()

    def set_roles(self, border=None, fill=None, glow=None):
        if border:
            self.border_role = border
        if fill:
            self.fill_role = fill
        if glow is not None:
            self.glow = glow
        self.refresh_effects()
        self.update()

    def refresh_effects(self):
        if self.glow and EFFECTS["glow"]:
            eff = self.graphicsEffect()
            if not isinstance(eff, QGraphicsDropShadowEffect):
                eff = QGraphicsDropShadowEffect(self)
                self.setGraphicsEffect(eff)
            eff.setBlurRadius(32)
            eff.setOffset(0, 0)
            eff.setColor(qc(T[self.glow_color], T["glow_alpha"]))
        else:
            self.setGraphicsEffect(None)

    def _col(self, role):
        """'accent'、'#RRGGBB' 或 'accent@0.45'（帶透明度）"""
        if "@" in role:
            name, a = role.split("@", 1)
            return qc(T.get(name, name), float(a))
        if role.startswith("#"):
            return QColor(role)
        return QColor(T[role])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = cut_path(r, self.cut_tr, self.cut_bl)
        p.fillPath(path, QBrush(QColor(T[self.fill_role])))
        p.setPen(QPen(self._col(self.border_role), 1))
        p.drawPath(path)
        p.end()


class PrimaryButton(QPushButton):
    """主要動作按鈕：實心電光青 + 雙切角。"""

    def __init__(self, text, icon_name=None, parent=None, cut=12):
        super().__init__(text, parent)
        self.setObjectName("Primary")
        self.icon_name = icon_name
        self.cut = cut
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)
        f = f_ui(14, bold=True)
        self.setFont(f)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) + 44 + (24 if self.icon_name else 0)
        return QSize(w, 44)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        path = cut_path(r, self.cut, self.cut)
        if not self.isEnabled():
            bg, fg = qc(T["line"]), QColor(T["disabled"])
        else:
            bg, fg = QColor(T["primary_bg"]), QColor(T["primary_fg"])
            if self.isDown():
                bg = bg.darker(115)
            elif self.underMouse():
                bg = bg.lighter(112) if T["name"] == "dark" else bg.lighter(140)
        p.fillPath(path, bg)
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self.text())
        iw = 24 if self.icon_name else 0
        x = (self.width() - tw - iw) / 2
        if self.icon_name:
            pm = svg_pixmap(self.icon_name, fg.name(), 16, 2.4)
            p.drawPixmap(int(x), int((self.height() - 16) / 2), pm)
        p.setPen(fg)
        p.setFont(self.font())
        p.drawText(QRectF(x + iw, 0, tw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class GridRoot(QWidget):
    """深藍黑背景 + 淡網格。"""

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T["bg"]))
        if EFFECTS["grid"]:
            p.setPen(QPen(qc(T["accent"], T["grid_alpha"]), 1))
            step = 32
            for x in range(0, self.width(), step):
                p.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), step):
                p.drawLine(0, y, self.width(), y)
        p.end()


class LogoMark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(0, 0), QPointF(r.width() * 0.7, 0), QPointF(r.width(), r.height() * 0.3),
                                   QPointF(r.width(), r.height()), QPointF(r.width() * 0.3, r.height()),
                                   QPointF(0, r.height() * 0.7)]))
        bg = T["accent"] if T["name"] == "dark" else T["primary_bg"]
        fg = T["accent_text"] if T["name"] == "dark" else T["primary_fg"]
        p.fillPath(path, QColor(bg))
        p.drawPixmap(7, 7, svg_pixmap("download", fg, 16, 2.6))
        p.end()


def app_icon() -> QIcon:
    """程式圖示：深藍切角方塊 + 青色下載箭頭。用 Qt 直接畫，不依賴 .ico 檔或圖片外掛。"""
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pad = 0 if size <= 24 else size * 0.04
        r = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
        w, h = r.width(), r.height()
        path = QPainterPath()
        path.addPolygon(QPolygonF([
            QPointF(r.left(), r.top()), QPointF(r.left() + w * 0.7, r.top()), QPointF(r.right(), r.top() + h * 0.3),
            QPointF(r.right(), r.bottom()), QPointF(r.left() + w * 0.3, r.bottom()), QPointF(r.left(), r.top() + h * 0.7)]))
        path.closeSubpath()
        p.fillPath(path, QColor("#0A1426"))
        if size >= 32:   # 淡青色描邊：放在深色工作列上也看得到輪廓
            p.setPen(QPen(qc("#22E4FF", 0.35), max(1.0, size * 0.012)))
            p.drawPath(path)
        g = w * 0.54
        k = g / 24
        stroke = max(2.6, (1.7 if size <= 24 else 1.2) / k)   # 小尺寸加粗
        body = ICONS["download"]
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#22E4FF" '
               f'stroke-width="{stroke:.2f}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(
            p, QRectF(r.left() + (w - g) / 2, r.top() + (h - g) / 2, g, g))
        p.end()
        icon.addPixmap(pm)
    return icon


class Dot(QWidget):
    def __init__(self, role="muted", size=6, glow=False, parent=None):
        super().__init__(parent)
        self.role, self.glow = role, glow
        self.setFixedSize(size + (6 if glow else 0), size + (6 if glow else 0))
        self.size_ = size

    def set_role(self, role):
        self.role = role
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        c = QColor(T[self.role]) if not self.role.startswith("#") else QColor(self.role)
        off = (self.width() - self.size_) / 2
        if self.glow:
            p.fillRect(QRectF(off - 2, off - 2, self.size_ + 4, self.size_ + 4), qc(c.name(), 0.25))
        p.fillRect(QRectF(off, off, self.size_, self.size_), c)
        p.end()


class ElideLabel(QLabel):
    """寬度不夠時自動以「…」截斷，完整文字放在 tooltip。"""

    def __init__(self, text="", parent=None, mode=Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)
        self.setText(text)

    def setText(self, text):  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full if len(self._full) > 30 else "")
        self._refresh()

    def fullText(self):  # noqa: N802
        return self._full

    def _refresh(self):
        w = max(20, self.width())
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, w))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.FontChange:
            self._refresh()


class Thumb(QWidget):
    """影片縮圖：沒圖時畫斜紋佔位；右下角顯示長度。"""

    def __init__(self, w=224, h=126, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.pix: QPixmap | None = None
        self.duration = ""
        self.placeholder = tr("封面縮圖")

    def set_pixmap(self, pm: QPixmap | None):
        self.pix = pm if (pm and not pm.isNull()) else None
        self.update()

    def set_duration(self, d: str):
        self.duration = d or ""
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = self.rect()
        if self.pix:
            scaled = self.pix.scaled(r.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
            sx = (scaled.width() - r.width()) // 2
            sy = (scaled.height() - r.height()) // 2
            p.drawPixmap(0, 0, scaled, sx, sy, r.width(), r.height())
        else:
            p.fillRect(r, QColor(T["panel2"]))
            p.setPen(QPen(QColor(T["line"]), 6))
            for i in range(-r.height(), r.width(), 20):
                p.drawLine(i, r.height(), i + r.height(), 0)
            p.setPen(QColor(T["muted"]))
            p.setFont(f_ui(12))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.placeholder)
        p.setPen(QPen(QColor(T["line_hi"]), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        if self.duration:
            p.setFont(f_mono(11))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(self.duration) + 12
            box = QRectF(r.width() - tw - 6, r.height() - 24, tw, 18)
            p.fillRect(box, qc("#060A13", 0.88))
            p.setPen(QColor("#E4EEFF"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.duration)
        p.end()


class QRView(QWidget):
    """B站 QR Code：白底 + 青色角框。matrix 為 None 時顯示佔位。"""

    def __init__(self, size=196, parent=None):
        super().__init__(parent)
        self.setFixedSize(size + 12, size + 12)
        self.box = size
        self.matrix = None
        self.message = "QR CODE"

    def set_matrix(self, m):
        self.matrix = m
        self.update()

    def set_message(self, text):
        self.matrix = None
        self.message = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        o = 6
        box = QRectF(o, o, self.box, self.box)
        p.fillRect(box, QColor("#FFFFFF"))
        if self.matrix:
            n = len(self.matrix)
            inner = self.box - 16
            cell = inner / n
            for y, row in enumerate(self.matrix):
                for x, v in enumerate(row):
                    if v:
                        p.fillRect(QRectF(o + 8 + x * cell, o + 8 + y * cell, cell + 0.6, cell + 0.6), QColor("#000000"))
        else:
            p.setPen(QColor("#0B1220"))
            p.setFont(f_disp(12, spacing=140))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        pen = QPen(QColor(T["accent"]), 3)
        p.setPen(pen)
        L = 22
        p.drawLine(1, 1, L, 1)
        p.drawLine(1, 1, 1, L)
        w, h = self.width() - 2, self.height() - 2
        p.drawLine(w, h, w - L, h)
        p.drawLine(w, h, w, h - L)
        p.end()


class ThemeOption(QPushButton):
    """外觀設定的主題卡：迷你預覽 + 名稱。"""

    def __init__(self, key, label, parent=None):
        super().__init__(parent)
        self.key, self.label = key, label
        self.setCheckable(True)
        self.setMinimumSize(96, 84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(110, 84)

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        p.fillRect(r, qc(T["accent"], 0.06) if on else QColor(T["panel"]))
        p.setPen(QPen(QColor(T["accent"] if on else T["line_hi"]), 1))
        p.drawRect(r)
        pv = QRectF(10, 10, r.width() - 20, 40)
        if self.key == "system":
            p.fillRect(QRectF(pv.left(), pv.top(), pv.width() / 2, pv.height()), QColor(DARK["bg"]))
            p.fillRect(QRectF(pv.center().x(), pv.top(), pv.width() / 2, pv.height()), QColor(LIGHT["bg"]))
        else:
            pal = DARK if self.key == "dark" else LIGHT
            p.fillRect(pv, QColor(pal["bg"]))
            p.fillRect(QRectF(pv.left() + 5, pv.top() + 5, 10, pv.height() - 10), QColor(pal["panel"]))
            p.fillRect(QRectF(pv.left() + 19, pv.top() + 5, pv.width() - 24, pv.height() - 10), QColor(pal["panel2"]))
            p.fillRect(QRectF(pv.left() + 19, pv.top() + 5, pv.width() - 24, 2), QColor(pal["accent"]))
        p.setPen(QPen(QColor(T["line_hi"]), 1))
        p.drawRect(pv)
        p.setPen(QColor(T["accent"] if on else T["text2"]))
        p.setFont(f_ui(13))
        p.drawText(QRectF(0, 54, r.width(), 24), Qt.AlignmentFlag.AlignCenter, self.label)
        p.end()


class Segmented(QFrame):
    """分段切換：一列互斥按鈕。changed(key)"""
    changed = Signal(str)

    def __init__(self, items, parent=None, stretch=False):
        super().__init__(parent)
        self.setObjectName("Seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(0)
        self.buttons = {}
        for key, text in items:
            b = QPushButton(text)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.buttons[key] = b
            lay.addWidget(b, 1 if stretch else 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding if stretch else QSizePolicy.Policy.Maximum,
                           QSizePolicy.Policy.Fixed)

    def set(self, key):
        b = self.buttons.get(key)
        if b:
            b.setChecked(True)

    def value(self):
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return None
