# -*- coding: utf-8 -*-
"""
YT-DLP Downloader — Cyberpunk Edition
PySide6 介面：左側導覽列 + 右側工作區（下載 / 佇列 / 紀錄 / 帳號 / 工具）。
下載邏輯在 ytdl_core.py，外觀在 ui_theme.py。
"""
from __future__ import annotations

import copy
import datetime
import html
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QProcess, QProcessEnvironment, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIntValidator, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QStackedWidget, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

import ui_theme as ui
import ytdl_core as core
import i18n
from i18n import tr
from ui_theme import T

try:
    import qrcode  # 純 Python，產生 QR 矩陣用
except ImportError:
    qrcode = None

APP_TITLE = f"{core.APP_NAME} — {core.APP_EDITION}  v{core.APP_VERSION}"


# ======================================================================= 共用工具
class _Sig(QObject):
    done = Signal(object)
    fail = Signal(str)
    prog = Signal(object, object)


_alive: set = set()


def run_bg(fn, on_done=None, on_fail=None, on_prog=None):
    """在背景執行 fn(progress_cb)，結果透過 Qt signal 回到主執行緒。"""
    sig = _Sig()
    _alive.add(sig)
    if on_done:
        sig.done.connect(on_done)
    if on_fail:
        sig.fail.connect(on_fail)
    if on_prog:
        sig.prog.connect(on_prog)
    sig.done.connect(lambda *_: _alive.discard(sig))
    sig.fail.connect(lambda *_: _alive.discard(sig))

    def target():
        try:
            r = fn(lambda a, b: sig.prog.emit(a, b))
            sig.done.emit(r)
        except Exception as e:  # noqa: BLE001
            sig.fail.emit(f"{type(e).__name__}: {e}")

    threading.Thread(target=target, daemon=True).start()


def open_path(p: str):
    p = str(p)
    if not os.path.exists(p):
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            return
    if core.IS_WIN:
        os.startfile(p)  # noqa: S606
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))


def reveal_file(path: str, fallback_dir: str):
    if path and core.IS_WIN and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path) if path and os.path.exists(path) else fallback_dir)


def decode_out(b: bytes) -> str:
    """yt-dlp 輸出先當 UTF-8 解；不是的話改用系統編碼（Big5 / Shift-JIS）。"""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        enc = locale.getpreferredencoding(False) or "mbcs"
        try:
            return b.decode(enc, "replace")
        except LookupError:
            return b.decode("utf-8", "replace")


def proc_env(s: dict | None = None) -> QProcessEnvironment:
    env = QProcessEnvironment()
    for k, v in core.subprocess_env(s).items():
        env.insert(k, v)
    return env


def fetch_bytes(url: str, limit=4_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": core.UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(limit)


CUT_RE = re.compile(r"bytes read, \d+ more expected|Read timed out|Connection reset|IncompleteRead", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")   # 終端機顏色碼（aria2c 會輸出）

SITE_TONE = {"youtube": "yt", "bilibili": "pink", "other": None}
POST_NAMES = {"Merger": tr("合併影音"), "ExtractAudio": tr("轉換音訊"), "VideoRemuxer": tr("轉換容器"),
              "EmbedThumbnail": tr("嵌入封面"), "Metadata": tr("寫入資訊"), "FixupM3u8": tr("修正檔案"),
              "EmbedSubtitle": tr("嵌入字幕"), "SponsorBlock": "SponsorBlock", "ModifyChapters": tr("移除片段"),
              "ThumbnailsConvertor": tr("轉換封面"), "FFmpegConcat": tr("合併清單")}


def explain_error(err: str) -> list[str]:
    e = err.lower()
    tips = []
    if "ffmpeg" in e and ("not found" in e or "not installed" in e):
        tips.append(tr("找不到 ffmpeg：請到「工具 / 設定」下載 ffmpeg。"))
    if "sign in to confirm" in e or "not a bot" in e:
        tips.append(tr("YouTube 要求驗證：請到「帳號 / Cookie」設定 YouTube 的 Cookie（建議 Firefox 或 cookies.txt）。"))
    if "could not copy" in e and "cookie" in e:
        tips.append(tr("瀏覽器開著時無法讀取 Cookie：請完全關閉該瀏覽器，或改用 Firefox / cookies.txt。"))
    if "failed to decrypt" in e or "dpapi" in e:
        tips.append(tr("無法解密 Chrome / Edge 的 Cookie：請改用 Firefox，或匯出 cookies.txt。"))
    if "http error 567" in e:
        tips.append(tr("Bilibili CDN 擋下了未分段的請求，新版已自動分段下載，按「重試」即可。"))
    if ("more expected" in e or "read timed out" in e or "connection reset" in e
            or "incompleteread" in e) and ("bilivideo" in e or "bili" in e or "bytes read" in e):
        tips.append(tr("下載中途被 CDN 節點斷線（B站常見於 mirrorcosov 節點）。新版已自動改走較穩定的備用節點並加強重試，"
                    "按「重試」即可；若仍失敗，可到「工具 / 設定 › B站 CDN 節點」手動換一個節點。"))
    if "unreachable network" in e or "network is unreachable" in e:
        tips.append(tr("網路無法到達：通常是連到 IPv6 位址但這台電腦沒有 IPv6。新版已讓 aria2c 只用 IPv4，按「重試」即可。"))
    if "aria2c exited with code" in e:
        tips.append(tr("多線下載（aria2c）失敗。若重試仍失敗，可到「工具 / 設定 › 多線下載」取消勾選這個網站，改回一般下載。"))
    if "conversion failed" in e or "postprocessing" in e:
        tips.append(tr("影片已下載，但轉換成選擇的影片格式時失敗（例如 H.264 不能放進 webm）。"
                    "新版遇到不相容的編碼會自動改存 mkv，按「重試」即可；或把影片格式改成 mkv / mp4。"))
    if "http error 412" in e:
        tips.append(tr("Bilibili 風控（412）：稍等幾分鐘再試，或重新掃碼登入。"))
    if "n challenge" in e or "js runtime" in e or "javascript runtime" in e or "ejs" in e:
        tips.append(tr("YouTube 需要 JavaScript 執行環境：請到「工具 / 設定」下載 Deno，並更新 yt-dlp。"))
    if "requested format is not available" in e:
        tips.append(tr("指定的畫質 / 格式不存在：請重新「解析格式」選擇，或把畫質改成「最佳畫質」。"))
    if "premium" in e or "大会员" in err or "會員" in err:
        tips.append(tr("此內容需要會員權限，請確認登入的帳號有對應會員。"))
    if "unsupported url" in e:
        tips.append(tr("yt-dlp 不支援這個網址，請確認網址是否正確，或更新 yt-dlp。"))
    return tips


# ======================================================================= 版面小工具
class FlowLayout(QLayout):
    """會自動換行的水平排列（給選項標籤用）。"""

    def __init__(self, parent=None, hspacing=8, vspacing=8):
        super().__init__(parent)
        self._items = []
        self.hs, self.vs = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):  # noqa: N802
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, lh = rect.x(), rect.y(), 0
        for it in self._items:
            if it.isEmpty():
                continue
            sz = it.sizeHint()
            nx = x + sz.width() + self.hs
            if nx - self.hs > rect.right() + 1 and lh > 0:
                x, y = rect.x(), y + lh + self.vs
                nx = x + sz.width() + self.hs
                lh = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x = nx
            lh = max(lh, sz.height())
        return y + lh - rect.y()


def lab(text="", obj=None, font=None, wrap=False, tone=None):
    w = QLabel(text)
    if obj:
        w.setObjectName(obj)
    if font is not None:
        w.setFont(font)
    if wrap:
        w.setWordWrap(True)
    if tone:
        w.setProperty("tone", tone)
    return w


def eyebrow(text):
    return lab(text, "Eyebrow", ui.f_disp(11, spacing=160))


def h1(text):
    return lab(text, "Title", ui.f_ui(26, bold=True))


def h2(text, px=15):
    return lab(text, "Title", ui.f_ui(px, bold=True))


def caption(text):
    return lab(text, "Muted", ui.f_ui(12))


def chip(text, tone=None, mono=False):
    w = lab(text, "Chip", ui.f_mono(12) if mono else ui.f_ui(12))
    if tone:
        w.setProperty("tone", tone)
    return w


def set_tone(w, tone):
    w.setProperty("tone", tone or "")
    ui.repolish(w)


def card(obj="Card"):
    f = QFrame()
    f.setObjectName(obj)
    return f


def vbox(parent=None, m=(0, 0, 0, 0), sp=10):
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(sp)
    return lay


def hbox(parent=None, m=(0, 0, 0, 0), sp=10):
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    lay.setContentsMargins(*m)
    lay.setSpacing(sp)
    return lay


def field(caption_text, widget):
    w = QWidget()
    v = vbox(w, sp=6)
    v.addWidget(caption(caption_text))
    v.addWidget(widget)
    return w


def page_scroll(body: QWidget) -> QScrollArea:
    body.setObjectName("PageBody")
    sa = QScrollArea()
    sa.setObjectName("PageScroll")
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setWidget(body)
    sa.viewport().setAutoFillBackground(False)
    sa.viewport().setObjectName("PageViewport")
    return sa


class StatusPill(QFrame):
    """頁首的「下載中 1」小標籤。"""

    def __init__(self, text, role):
        super().__init__()
        self.setObjectName("Card")
        h = hbox(self, (12, 5, 12, 5), 8)
        self.dot = ui.Dot(role)
        self.label = lab(text, "Text2", ui.f_ui(12))
        h.addWidget(self.dot)
        h.addWidget(self.label)

    def setText(self, t):  # noqa: N802
        self.label.setText(t)


class NumberField(QWidget):
    """整數輸入：[−] 數字 [+]。用一般 QLineEdit 實作（QSpinBox 套樣式後在 Windows 上會點不到）。"""
    valueChanged = Signal(int)

    def __init__(self, lo=1, hi=16, value=1):
        super().__init__()
        self.lo, self.hi = lo, hi
        self._v = value
        h = hbox(self, sp=6)
        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        for b in (self.minus, self.plus):
            b.setFixedSize(40, 40)
            b.setFont(ui.f_mono(16, bold=True))
            b.setStyleSheet("QPushButton { padding: 0; }")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAutoRepeat(True)
        self.edit = QLineEdit(str(value))
        self.edit.setFont(ui.f_mono(14))
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setValidator(QIntValidator(lo, hi, self))
        self.edit.setMinimumWidth(56)
        h.addWidget(self.minus)
        h.addWidget(self.edit, 1)
        h.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self.setValue(self._v - 1))
        self.plus.clicked.connect(lambda: self.setValue(self._v + 1))
        self.edit.editingFinished.connect(self._from_text)
        self.edit.textEdited.connect(self._typing)

    def _typing(self, t):
        if t.isdigit() and self.lo <= int(t) <= self.hi and int(t) != self._v:
            self._v = int(t)
            self.valueChanged.emit(self._v)

    def _from_text(self):
        t = self.edit.text().strip()
        self.setValue(int(t) if t.isdigit() else self._v)

    def setRange(self, lo, hi):  # noqa: N802
        self.lo, self.hi = lo, hi
        self.edit.setValidator(QIntValidator(lo, hi, self))
        self.setValue(self._v)

    def value(self):
        return self._v

    def setValue(self, v):  # noqa: N802
        v = max(self.lo, min(self.hi, int(v)))
        changed = v != self._v
        self._v = v
        if self.edit.text() != str(v):
            self.edit.setText(str(v))
        self.minus.setEnabled(v > self.lo)
        self.plus.setEnabled(v < self.hi)
        if changed:
            self.valueChanged.emit(v)

    def setToolTip(self, t):  # noqa: N802
        super().setToolTip(t)
        self.edit.setToolTip(t)


# ======================================================================= 佇列卡片
CODEC_TONE = {"AV1": "accent", "VP9": "green", "H.264": "amber", "H.265": "pink",
              "Opus": "green", "AAC": "amber", "FLAC": "accent", "E-AC3": "pink", "AC3": "pink"}


class StreamTable(QWidget):
    """解析後列出這支影片所有串流（影片或音訊）。點一列 = 指定下載那個串流；第一列「自動」= 交給畫質 / 編碼設定。
    介面和原本的 QComboBox 一樣用 currentData() 取得 format id（自動 = ""）。"""
    selectionChanged = Signal()

    VIDEO_COLS = [("id", "ID"), ("res", tr("解析度")), ("fps", "FPS"), ("codec", tr("編碼")), ("dr", "HDR"),
                  ("ext", tr("容器")), ("audio", tr("音訊")), ("tbr", tr("位元率")), ("size", tr("大小")), ("proto", tr("傳輸"))]
    AUDIO_COLS = [("id", "ID"), ("codec", tr("編碼")), ("abr", tr("位元率")), ("asr", tr("取樣率")), ("ch", tr("聲道")),
                  ("ext", tr("容器")), ("lang", tr("語言")), ("note", tr("說明")), ("size", tr("大小")), ("proto", tr("傳輸"))]
    RIGHT = {"fps", "tbr", "abr", "asr", "size"}

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.cols = self.VIDEO_COLS if kind == "video" else self.AUDIO_COLS
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(lab("VIDEO" if kind == "video" else "AUDIO", "Tag", ui.f_mono(12, bold=True)))
        self.count_lab = lab("", "Muted", ui.f_ui(12))
        head.addWidget(self.count_lab)
        head.addStretch(1)
        head.addWidget(lab(tr("編碼"), "Muted", ui.f_ui(12)))
        self.filter = QComboBox()
        self.filter.setMinimumWidth(110)
        self.filter.currentIndexChanged.connect(lambda *_: self._apply_filter())
        head.addWidget(self.filter)
        v.addLayout(head)
        self.table = QTableWidget(0, len(self.cols))
        self.table.setObjectName("StreamTable")
        self.table.setHorizontalHeaderLabels([c[1] for c in self.cols])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setFont(ui.f_mono(12))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setMinimumHeight(150)
        self.table.setMaximumHeight(300 if kind == "video" else 220)
        self.table.itemSelectionChanged.connect(self.selectionChanged.emit)
        v.addWidget(self.table)
        self.clear()

    # ---------------------------------------------------------------- 資料
    def clear(self):
        self.table.setRowCount(0)
        self._add_row({c: "" for c, _ in self.cols}, fmt_id="", auto=True)
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem(tr("全部"), "")
        self.filter.blockSignals(False)
        self.filter.setEnabled(False)
        self.count_lab.setText(tr("（按「解析格式」後列出）"))
        self.table.selectRow(0)

    def set_rows(self, formats: list):
        """formats：core.parse_info 的 video_formats / audio_formats。"""
        self.clear()
        codecs = []
        for f in formats:
            if self.kind == "video":
                h = f.get("height") or 0
                vals = {"id": f["id"], "res": f"{h}p" if h else (f.get("label") or ""),
                        "fps": f"{f['fps']:.0f}" if f.get("fps") else "", "codec": f.get("vcodec") or "",
                        "dr": f.get("dr") or "", "ext": f.get("ext") or "",
                        "audio": f.get("acodec") or ("✓" if f.get("has_audio") else ""),
                        "tbr": f"{f['tbr']:.0f}k" if f.get("tbr") else "", "size": f.get("size") or "",
                        "proto": f.get("proto") or ""}
            else:
                vals = {"id": f["id"], "codec": f.get("acodec") or "",
                        "abr": f"{f['abr']:.0f}k" if f.get("abr") else "",
                        "asr": f"{f['asr'] / 1000:g}kHz" if f.get("asr") else "",
                        "ch": str(f.get("channels") or ""), "ext": f.get("ext") or "",
                        "lang": f.get("lang") or "",
                        "note": f.get("label") if f.get("label") not in ("", "audio") else "",
                        "size": f.get("size") or "", "proto": f.get("proto") or ""}
            if vals["codec"] and vals["codec"] not in codecs:
                codecs.append(vals["codec"])
            self._add_row(vals, fmt_id=f["id"])
        self.filter.blockSignals(True)
        for c in codecs:
            self.filter.addItem(c, c)
        self.filter.blockSignals(False)
        self.filter.setEnabled(len(codecs) > 1)
        n = len(formats)
        self.count_lab.setText(tr('{n} 個串流').format(n=n) + (tr(' · {n} 種編碼').format(n=len(codecs)) if codecs else "") if n else tr("沒有可用的串流"))
        self.table.selectRow(0)

    def add_locked(self, name: str, reason: str):
        """B站鎖住的畫質：灰色列出、不能選。"""
        vals = {c: "" for c, _ in self.cols}
        vals.update(id="—", codec=reason)
        if self.kind == "video":
            vals["res"] = name
        else:
            vals["note"] = name
        self._add_row(vals, fmt_id=None, locked=True)

    def _add_row(self, vals: dict, fmt_id, auto=False, locked=False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, (key, _) in enumerate(self.cols):
            it = QTableWidgetItem(str(vals.get(key, "")))
            if key in self.RIGHT:
                it.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            if key == "codec" and not locked and vals.get("codec") in CODEC_TONE:
                it.setForeground(ui.qc(T[CODEC_TONE[vals["codec"]]]))
            if key == "dr" and vals.get("dr"):
                it.setForeground(ui.qc(T["amber"]))
            if locked:
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                it.setForeground(ui.qc(T["disabled"]))
            self.table.setItem(r, c, it)
        first = self.table.item(r, 0)
        if first is not None:
            first.setData(Qt.ItemDataRole.UserRole, fmt_id)
        if auto:
            self.table.setSpan(r, 0, 1, len(self.cols))
            if first is not None:
                first.setText(tr("自動（依畫質 / 編碼設定挑最好的）") if self.kind == "video" else tr("自動（最佳音質）"))
                first.setForeground(ui.qc(T["accent"]))

    # ---------------------------------------------------------------- 篩選 / 選取
    def _apply_filter(self):
        want = self.filter.currentData() or ""
        ci = [k for k, _ in self.cols].index("codec")
        for r in range(1, self.table.rowCount()):
            it = self.table.item(r, ci)
            self.table.setRowHidden(r, bool(want) and (it is None or it.text() != want))
        sel = self.table.selectionModel().selectedRows()
        if sel and self.table.isRowHidden(sel[0].row()):
            self.table.selectRow(0)

    def currentData(self) -> str:  # noqa: N802  （跟 QComboBox 同名，原本的程式不用改）
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return ""
        it = self.table.item(rows[0].row(), 0)
        return (it.data(Qt.ItemDataRole.UserRole) or "") if it is not None else ""

    def current_text(self) -> str:
        fid = self.currentData()
        if not fid:
            return tr("自動")
        r = self.table.selectionModel().selectedRows()[0].row()
        parts = [self.table.item(r, c).text() for c in range(1, min(5, len(self.cols)))
                 if self.table.item(r, c) is not None and self.table.item(r, c).text()]
        return f"[{fid}] " + " ".join(parts)


class JobCard(ui.CutFrame):
    stop_clicked = Signal(object)
    remove_clicked = Signal(object)
    retry_clicked = Signal(object)
    open_clicked = Signal(object)

    def __init__(self, job: dict, win: "MainWindow"):
        super().__init__(tr=0, border="line", fill="panel")
        self.job, self.win = job, win
        root = vbox(self, (20, 14, 20, 14), 12)

        top = hbox(sp=14)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        mid = vbox(sp=6)
        self.title = ui.ElideLabel(job["title"])
        self.title.setFont(ui.f_ui(14))
        mid.addWidget(self.title)
        chips = hbox(sp=6)
        tone = SITE_TONE.get(job["site"])
        chips.addWidget(chip(core.SITE_NAMES[job["site"]].replace(tr("其他網站"), tr("其他")), tone))
        chips.addWidget(chip(job["mode_text"]))
        if job.get("aria2"):
            chips.addWidget(chip(tr('多線 ×{get}').format(get=job.get('aria2_n', 16)), "accent"))
        elif job["site"] == "bilibili":
            chips.addWidget(chip(tr("分段下載")))
        chips.addStretch(1)
        mid.addLayout(chips)
        self.sub = ui.ElideLabel("", mode=Qt.TextElideMode.ElideMiddle)
        self.sub.setObjectName("Muted")
        self.sub.setFont(ui.f_mono(12))
        self.sub.hide()
        mid.addWidget(self.sub)
        top.addLayout(mid, 1)
        self.status = lab(tr("等待中"), "Muted", ui.f_ui(13))
        top.addWidget(self.status)
        self.btn_retry = QPushButton(tr("重試"))
        self.btn_retry.setProperty("variant", "warn")
        self.btn_open = QPushButton(tr("開啟位置"))
        self.btn_stop = QPushButton()
        self.btn_stop.setProperty("variant", "icon")
        self.btn_stop.setToolTip(tr("停止這個項目"))
        self.btn_remove = QPushButton()
        self.btn_remove.setProperty("variant", "icon")
        self.btn_remove.setToolTip(tr("從佇列移除"))
        for b in (self.btn_retry, self.btn_open, self.btn_stop, self.btn_remove):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            top.addWidget(b)
        self.btn_stop.clicked.connect(lambda: self.stop_clicked.emit(self.job))
        self.btn_remove.clicked.connect(lambda: self.remove_clicked.emit(self.job))
        self.btn_retry.clicked.connect(lambda: self.retry_clicked.emit(self.job))
        self.btn_open.clicked.connect(lambda: self.open_clicked.emit(self.job))
        root.addLayout(top)

        # 進度（下載中才顯示）
        self.prog_box = QWidget()
        pv = vbox(self.prog_box, sp=8)
        stats = hbox(sp=14)
        self.pct = lab("0.0%", "Tag", ui.f_mono(12, bold=True))
        self.stage = lab("", "Muted", ui.f_mono(12))
        self.speed = lab("", "Text2", ui.f_mono(12))
        self.eta = lab("", "Muted", ui.f_mono(12))
        stats.addWidget(self.pct)
        stats.addWidget(self.stage)
        stats.addStretch(1)
        stats.addWidget(self.speed)
        stats.addWidget(self.eta)
        pv.addLayout(stats)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        pv.addWidget(self.bar)
        root.addWidget(self.prog_box)

        # 錯誤提示
        self.tip = card("TipBox")
        th = hbox(self.tip, (12, 10, 12, 10), 10)
        self.tip_icon = QLabel()
        self.tip_icon.setFixedSize(16, 16)
        th.addWidget(self.tip_icon, 0, Qt.AlignmentFlag.AlignTop)
        tv = vbox(sp=4)
        self.tip_err = lab("", None, ui.f_mono(12), wrap=True, tone="bad")
        self.tip_err.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tip_text = lab("", "Title", ui.f_ui(13), wrap=True)
        tv.addWidget(self.tip_err)
        tv.addWidget(self.tip_text)
        th.addLayout(tv, 1)
        root.addWidget(self.tip)
        self.refresh_icons()
        self.apply_state()

    def refresh_icons(self):
        self.btn_stop.setIcon(ui.make_icon("stop", "text", size=12))
        self.btn_remove.setIcon(ui.make_icon("x", "muted", size=14))
        self.btn_open.setIcon(ui.make_icon("folder", "text2", size=14))
        self.btn_retry.setIcon(ui.make_icon("refresh", "amber", size=14))
        self.tip_icon.setPixmap(ui.svg_pixmap("info", T["amber"], 16))
        self.apply_state()

    def apply_state(self):
        st = self.job["state"]
        running = st in ("running", "post")
        icon = {"waiting": None, "running": None, "post": None, "done": ("check", "green"),
                "failed": ("alert", "red"), "stopped": ("stop", "amber")}.get(st)
        if icon:
            self.icon.setPixmap(ui.svg_pixmap(icon[0], T[icon[1]], 16, 2.4))
        else:
            pm = QPixmap(18, 18)
            pm.fill(Qt.GlobalColor.transparent)
            self.icon.setPixmap(pm)
            self.icon.setPixmap(ui.svg_pixmap("download" if running else "list",
                                              T["accent"] if running else T["muted"], 16))
        text = {"waiting": tr("等待中"), "running": tr("下載中"), "post": self.job.get("post_text", tr("後製中")),
                "done": tr("完成"), "failed": tr("失敗"), "stopped": tr("已停止")}[st]
        tone = {"waiting": "muted", "running": "accent", "post": "accent", "done": "ok",
                "failed": "bad", "stopped": "warn"}[st]
        self.status.setText(text)
        set_tone(self.status, tone)
        self.title.setFont(ui.f_ui(15 if running else 14, bold=running))
        self.prog_box.setVisible(running)
        self.tip.setVisible(st == "failed" and bool(self.job.get("errors") or self.job.get("tips")))
        self.btn_stop.setVisible(running)
        self.btn_remove.setVisible(not running)
        self.btn_retry.setVisible(st in ("failed", "stopped"))
        self.btn_open.setVisible(st == "done")
        if st == "done" and self.job.get("file"):
            self.sub.setText(self.job["file"])
            self.sub.show()
        else:
            self.sub.hide()
        if running:
            self.cut_tr = 16
            self.set_roles(border="accent@0.45", glow=True)
        elif st == "failed":
            self.cut_tr = 0
            self.set_roles(border="red@0.5", glow=False)
        else:
            self.cut_tr = 0
            self.set_roles(border="line", glow=False)
        if st == "failed":
            errs = self.job.get("errors") or []
            self.tip_err.setText(errs[-1] if errs else "")
            self.tip_err.setVisible(bool(errs))
            tips = self.job.get("tips") or []
            self.tip_text.setText("\n".join(tips) if tips else tr("滑鼠移到這裡可複製錯誤訊息；詳細內容請看「執行紀錄」。"))

    def set_progress(self, pct, speed, eta, stage):
        if pct is None:
            self.bar.setRange(0, 0)
        else:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(pct * 10))
            self.pct.setText(f"{pct:.1f}%")
        self.speed.setText(speed)
        self.eta.setText(f"ETA {eta}" if eta else "")
        self.stage.setText(stage)

    def set_post(self, text):
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        self.pct.setText(tr("後製中"))
        self.stage.setText(text)
        self.speed.setText("")
        self.eta.setText("")


# ======================================================================= Cookie 設定面板
class CookiePanel(QWidget):
    """單一網站的登入方式：Segmented + 對應內容。"""
    changed = Signal()

    def __init__(self, site: str, win: "MainWindow"):
        super().__init__()
        self.site, self.win, self.s = site, win, win.s
        c = self.s["cookies"][site]
        v = vbox(self, sp=14)
        modes = [("none", tr("不使用")), ("browser", tr("瀏覽器")), ("file", "cookies.txt")]
        if site == "bilibili":
            modes = [("login", tr("QR Code 登入")), ("browser", tr("瀏覽器")), ("file", "cookies.txt"), ("none", tr("不使用"))]
        self.seg = ui.Segmented(modes)
        v.addWidget(self.seg, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack = QStackedWidget()
        self.pages = {}

        none = QWidget()
        nl = vbox(none)
        nl.addWidget(lab(tr("下載時不帶任何登入資訊。"), "Text2", ui.f_ui(13), wrap=True))
        nl.addStretch(1)
        self.pages["none"] = none

        br = QWidget()
        bl = vbox(br, sp=12)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.browser = QComboBox()
        self.browser.addItems(core.BROWSERS)
        self.browser.setCurrentText(c.get("browser", "firefox"))
        self.profile = QLineEdit(c.get("profile", ""))
        self.profile.setPlaceholderText(tr("留空 = 預設設定檔"))
        g.addWidget(field(tr("瀏覽器"), self.browser), 0, 0)
        g.addWidget(field(tr("設定檔"), self.profile), 0, 1)
        bl.addLayout(g)
        self.browser_tip = card("TipBox")
        th = hbox(self.browser_tip, (12, 10, 12, 10), 10)
        self.browser_tip_icon = QLabel()
        self.browser_tip_icon.setFixedSize(16, 16)
        th.addWidget(self.browser_tip_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.browser_tip_text = lab("", "Title", ui.f_ui(13), wrap=True)
        th.addWidget(self.browser_tip_text, 1)
        bl.addWidget(self.browser_tip)
        bl.addStretch(1)
        self.pages["browser"] = br

        fp = QWidget()
        fl = vbox(fp, sp=8)
        fl.addWidget(caption(tr("Netscape 格式 cookies.txt")))
        r = hbox(sp=8)
        self.file_edit = QLineEdit(c.get("file", ""))
        self.file_edit.setFont(ui.f_mono(12))
        self.file_edit.setPlaceholderText(tr("選擇 cookies.txt"))
        b1 = QPushButton(tr("瀏覽…"))
        b1.clicked.connect(self.pick_file)
        b2 = QPushButton(tr("檢查"))
        b2.clicked.connect(self.check_file)
        r.addWidget(self.file_edit, 1)
        r.addWidget(b1)
        r.addWidget(b2)
        fl.addLayout(r)
        self.file_status = lab(tr("可用瀏覽器擴充功能「Get cookies.txt LOCALLY」匯出。"), "Muted", ui.f_ui(13), wrap=True)
        fl.addWidget(self.file_status)
        fl.addStretch(1)
        self.pages["file"] = fp

        if site == "bilibili":
            lp = QWidget()
            ll = vbox(lp, sp=12)
            ll.addWidget(lab(tr("登入後可下載 1080P 以上；大會員可下載 4K、1080P 60 幀與 Hi-Res 音軌。"),
                             "Text2", ui.f_ui(14), wrap=True))
            self.qr_msg = lab(tr("用手機 Bilibili App 掃描左邊的 QR Code。登入資料只存在這台電腦。"),
                              "Muted", ui.f_ui(13), wrap=True)
            ll.addWidget(self.qr_msg)
            ll.addStretch(1)
            bb = hbox(sp=8)
            self.btn_qr = ui.PrimaryButton(tr("產生 QR Code"), "qr", cut=10)
            self.btn_qr.setMinimumHeight(42)
            self.btn_check = QPushButton(tr("重新檢查狀態"))
            self.btn_logout = QPushButton(tr("登出"))
            self.btn_logout.setProperty("variant", "danger")
            bb.addWidget(self.btn_qr)
            bb.addWidget(self.btn_check)
            bb.addWidget(self.btn_logout)
            bb.addStretch(1)
            ll.addLayout(bb)
            self.pages["login"] = lp

        for k, _t in modes:
            self.stack.addWidget(self.pages[k])
        v.addWidget(self.stack, 1)

        mode = c.get("mode", "none")
        if mode not in self.pages:
            mode = "none"
        self.seg.set(mode)
        self.stack.setCurrentWidget(self.pages[mode])
        self.seg.changed.connect(self._mode_changed)
        self.browser.currentTextChanged.connect(self._sync)
        self.browser.currentTextChanged.connect(self.update_browser_tip)
        self.profile.textChanged.connect(self._sync)
        self.file_edit.textChanged.connect(self._sync)
        self.refresh_icons()

    def refresh_icons(self):
        self.browser_tip_icon.setPixmap(ui.svg_pixmap("alert", T["amber"], 16))
        self.update_browser_tip()

    def update_browser_tip(self, *_):
        b = self.browser.currentText()
        if b == "firefox":
            self.browser_tip_text.setText(tr("Firefox 可以在開著的狀態下直接讀取，最穩定。"))
        else:
            extra = tr("Google 不允許第三方程式用帳號密碼登入。") if self.site == "youtube" else ""
            self.browser_tip_text.setText(
                extra + tr("Chrome / Edge 等 Chromium 瀏覽器在 Windows 上的 Cookie 有加密保護，常會讀取失敗，"
                        "讀取時也通常要先完全關閉瀏覽器。建議改用 Firefox 或 cookies.txt。"))

    def mode(self):
        return self.seg.value() or "none"

    def _mode_changed(self, k):
        self.stack.setCurrentWidget(self.pages[k])
        self._sync()

    def pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("選擇 cookies.txt"), str(core.BASE_DIR), tr("Cookies (*.txt);;所有檔案 (*)"))
        if f:
            self.file_edit.setText(os.path.normpath(f))
            self.check_file()

    def check_file(self):
        f = self.file_edit.text().strip()
        if not f:
            self.file_status.setText(tr("尚未選擇檔案"))
            set_tone(self.file_status, "warn")
            return
        ok, msg = core.validate_cookie_file(f)
        self.file_status.setText((tr("有效的 cookies.txt · ") if ok else tr("無法使用：")) + msg)
        set_tone(self.file_status, "ok" if ok else "bad")

    def _sync(self, *_):
        self.s["cookies"][self.site].update(
            mode=self.mode(), browser=self.browser.currentText(),
            profile=self.profile.text().strip(), file=self.file_edit.text().strip())
        self.changed.emit()

    def reload(self):
        """匯入設定後，把這個面板重新套用成 self.s 裡目前的值。"""
        c = self.s["cookies"].get(self.site, {})
        mode = c.get("mode", "none")
        if mode not in self.pages:
            mode = "none"
        for w in (self.browser, self.profile, self.file_edit):
            w.blockSignals(True)
        self.browser.setCurrentText(c.get("browser", "firefox"))
        self.profile.setText(c.get("profile", ""))
        self.file_edit.setText(c.get("file", ""))
        for w in (self.browser, self.profile, self.file_edit):
            w.blockSignals(False)
        self.seg.set(mode)
        self.stack.setCurrentWidget(self.pages[mode])


# ======================================================================= 主視窗
class MainWindow(QMainWindow):
    PAGES = [("download", tr("下載"), "download"), ("queue", tr("下載佇列"), "list"),
             ("history", tr("歷史紀錄"), "clock"), ("logs", tr("執行紀錄"), "terminal"),
             ("accounts", tr("帳號 / Cookie"), "key"), ("tools", tr("工具 / 設定"), "gear")]

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.s = core.load_settings()
        ui.EFFECTS["glow"] = bool(self.s.get("glow", True))
        ui.EFFECTS["grid"] = bool(self.s.get("grid", True))
        self.setWindowTitle(APP_TITLE)
        self.resize(1440, 960)
        self.setMinimumSize(1080, 700)

        self.jobs: list[dict] = []
        self.proc: QProcess | None = None
        self.cur: dict | None = None
        self.buf = b""
        self.stop_all = False
        self.info_proc: QProcess | None = None
        self.info_url = ""
        self.info = None
        self.tool_busy = False
        self.ytdlp_version = ""
        self.log_entries: list[tuple[str, str]] = []
        self.log_filter = "all"
        self.queue_filter = "all"
        self._icon_jobs: list = []      # (callable) 主題切換時重新上色
        self._force_quit = False

        self._build()
        self._build_tray()
        self.aria_timer = QTimer(self)
        self.aria_timer.setInterval(700)
        self.aria_timer.timeout.connect(self._poll_aria2)
        self.aria_timer.start()
        self._load_widgets()
        self.apply_theme()
        self._update_mode_ui()
        self._refresh_tools()
        self._refresh_counts()
        self._refresh_live()
        self._cookie_changed()
        QTimer.singleShot(2500, self._check_updates_async)
        if self.s.get("window_geometry"):
            try:
                self.restoreGeometry(bytes.fromhex(self.s["window_geometry"]))
            except Exception:  # noqa: BLE001
                pass
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                lambda *_: self.s.get("theme") == "system" and self.apply_theme())
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 圖示登記
    def icon(self, btn, name, role="text2", on_role=None, size=18, stroke=2.0):
        def apply():
            btn.setIcon(ui.make_icon(name, role, on_role, size, stroke))
            btn.setIconSize(QSize(size, size))
        self._icon_jobs.append(apply)
        apply()
        return btn

    def icon_label(self, name, role="accent", size=16, stroke=2.0):
        w = QLabel()
        w.setFixedSize(size, size)

        def apply():
            w.setPixmap(ui.svg_pixmap(name, T[role] if not role.startswith("#") else role, size, stroke))
        self._icon_jobs.append(apply)
        apply()
        return w

    # ------------------------------------------------------------------ 主題
    def effective_theme(self):
        t = self.s.get("theme", "dark")
        if t == "system":
            try:
                return "dark" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
            except Exception:  # noqa: BLE001
                return "dark"
        return t

    def apply_theme(self):
        ui.set_palette(self.effective_theme())
        pal = QPalette()
        for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
                          (QPalette.ColorRole.Base, "field"), (QPalette.ColorRole.AlternateBase, "panel2"),
                          (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "panel"),
                          (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.ToolTipBase, "panel2"),
                          (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.Highlight, "accent"),
                          (QPalette.ColorRole.HighlightedText, "accent_text"),
                          (QPalette.ColorRole.PlaceholderText, "muted")):
            pal.setColor(role, QColor(T[key]))
        self.app.setPalette(pal)
        self.app.setStyleSheet(ui.build_qss(ui.write_assets()))
        for fn in self._icon_jobs:
            fn()
        for jc in self.findChildren(JobCard):
            jc.refresh_icons()
        for cp in self.findChildren(CookiePanel):
            cp.refresh_icons()
        for cf in self.findChildren(ui.CutFrame):
            cf.refresh_effects()
        self.theme_btn.setToolTip(tr("切換為淺色主題") if T["name"] == "dark" else tr("切換為深色主題"))
        self.icon_jobs_theme_btn()
        for w in self.findChildren(QWidget):
            w.update()

    def icon_jobs_theme_btn(self):
        self.theme_btn.setIcon(ui.make_icon("moon" if T["name"] == "dark" else "sun", "text2", size=16))

    def toggle_theme(self):
        self.s["theme"] = "light" if self.effective_theme() == "dark" else "dark"
        for k, b in self.theme_opts.items():
            b.setChecked(k == self.s["theme"])
        self.apply_theme()
        self.save()

    # ================================================================== 版面
    def _build(self):
        root = ui.GridRoot()
        self.setCentralWidget(root)
        rv = vbox(root, sp=0)
        body = hbox(sp=0)
        rv.addLayout(body, 1)
        body.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        builders = {"download": self._build_download_page, "queue": self._build_queue_page,
                    "history": self._build_history_page, "logs": self._build_log_page,
                    "accounts": self._build_accounts_page, "tools": self._build_tools_page}
        # 紀錄頁要先建好，其他頁面才能寫紀錄
        self.page_widgets = {}
        self.page_widgets["logs"] = builders["logs"]()
        for key, _t, _i in self.PAGES:
            if key not in self.page_widgets:
                self.page_widgets[key] = builders[key]()
        for key, _t, _i in self.PAGES:
            self.stack.addWidget(self.page_widgets[key])
        body.addWidget(self.stack, 1)
        rv.addWidget(self._build_footer())
        self.go("download")

    def go(self, key):
        self.stack.setCurrentWidget(self.page_widgets[key])
        self.nav_buttons[key].setChecked(True)

    def _build_sidebar(self):
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(236)
        v = vbox(sb, (16, 24, 16, 16), 28)
        brand = vbox(sp=6)
        row = hbox(sp=10)
        row.addWidget(ui.LogoMark())
        row.addWidget(lab("YT-DLP", "Title", ui.f_disp(17, bold=True, spacing=110)))
        row.addStretch(1)
        brand.addLayout(row)
        brand.addWidget(lab("CYBERPUNK EDITION", "Eyebrow", ui.f_disp(10, spacing=210)))
        brand.addWidget(lab(f"v{core.APP_VERSION}", "Version", ui.f_mono(11)))
        bw = QWidget()
        bw.setLayout(brand)
        bw.layout().setContentsMargins(8, 0, 0, 0)
        v.addWidget(bw)

        nav = vbox(sp=4)
        self.nav_buttons = {}
        self.nav_group = QButtonGroup(self)
        for key, text, icon_name in self.PAGES:
            b = QPushButton(text)
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(ui.f_ui(14))
            self.icon(b, icon_name, "text2", "accent", 18)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            nav.addWidget(b)
            if key == "queue":
                bl = hbox(b, (0, 0, 10, 0), 0)
                bl.addStretch(1)
                self.nav_badge = lab("0", "NavBadge", ui.f_mono(11))
                self.nav_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                bl.addWidget(self.nav_badge)
        v.addLayout(nav)
        v.addStretch(1)

        sys_box = card("SysBox")
        sv = vbox(sys_box, (12, 14, 12, 14), 10)
        sv.addWidget(lab("SYSTEM", "Muted", ui.f_disp(10, spacing=190)))
        self.sys_rows = {}
        for key, name in (("ytdlp", "yt-dlp"), ("ffmpeg", "ffmpeg"), ("deno", "Deno"), ("aria2", "aria2c")):
            r = hbox(sp=8)
            d = ui.Dot("muted")
            r.addWidget(d)
            r.addWidget(lab(name, "Title", ui.f_ui(12)), 1)
            val = lab("…", "Muted", ui.f_mono(11))
            r.addWidget(val)
            sv.addLayout(r)
            self.sys_rows[key] = (d, val)
        v.addWidget(sys_box)
        return sb

    def _build_footer(self):
        f = QFrame()
        f.setObjectName("Footer")
        f.setFixedHeight(32)
        h = hbox(f, (20, 0, 20, 0), 12)
        h.addWidget(lab(">", "FooterPrompt", ui.f_mono(12, bold=True)))
        self.footer_msg = ui.ElideLabel(tr("就緒"))
        self.footer_msg.setObjectName("FooterText")
        self.footer_msg.setFont(ui.f_mono(12))
        h.addWidget(self.footer_msg, 1)
        self.footer_right = lab("", "FooterRight", ui.f_mono(12))
        h.addWidget(self.footer_right)
        return f

    def _page_header(self, eb, title, right: QLayout | None = None, desc=None):
        h = hbox(sp=16)
        v = vbox(sp=4)
        v.addWidget(eyebrow(eb))
        v.addWidget(h1(title))
        if desc:
            v.addWidget(lab(desc, "Text2", ui.f_ui(14), wrap=True))
        h.addLayout(v, 1)
        if right is not None:
            h.addLayout(right)
        return h

    # ================================================================== 01 下載
    def _build_download_page(self):
        body = QWidget()
        v = vbox(body, (36, 28, 36, 36), 22)

        right = hbox(sp=8)
        self.pill_running = StatusPill(tr("下載中 0"), "accent")
        self.pill_waiting = StatusPill(tr("等待 0"), "muted")
        self.theme_btn = QPushButton()
        self.theme_btn.setFixedSize(46, 38)
        self.theme_btn.setStyleSheet("QPushButton { padding: 0; }")
        self.theme_btn.clicked.connect(self.toggle_theme)
        right.addWidget(self.pill_running)
        right.addWidget(self.pill_waiting)
        right.addWidget(self.theme_btn)
        v.addLayout(self._page_header("01 // DOWNLOAD", tr("新增下載"), right))

        # --- 網址卡
        uc = ui.CutFrame(tr=18, bl=18, border="line_hi", fill="panel", glow=True)
        uv = vbox(uc, (22, 20, 22, 20), 14)
        lr = hbox(sp=10)
        lr.addWidget(lab("URL", "Tag", ui.f_disp(12, spacing=140)))
        lr.addWidget(caption(tr("影片網址（一行一個，可一次貼多個）")))
        lr.addStretch(1)
        uv.addLayout(lr)
        self.url_edit = QPlainTextEdit()
        self.url_edit.setObjectName("UrlEdit")
        self.url_edit.setFont(ui.f_mono(13))
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=…\nhttps://www.bilibili.com/video/BV…")
        self.url_edit.setFixedHeight(76)
        self.url_edit.textChanged.connect(self._refresh_cookie_chip)
        uv.addWidget(self.url_edit)
        br = hbox(sp=10)
        b_paste = self.icon(QPushButton(tr("貼上")), "clip", "text", "text", 15)
        b_paste.clicked.connect(self.paste_url)
        self.b_analyze = self.icon(QPushButton(tr("解析格式")), "search", "text", "text", 15)
        self.b_analyze.clicked.connect(self.analyze)
        self.cookie_chip = chip("", None)
        self.cookie_chip.hide()
        b_add = QPushButton(tr("加入佇列"))
        b_add.setProperty("variant", "secondary")
        b_add.setMinimumHeight(44)
        b_add.clicked.connect(self.add_to_queue)
        b_go = ui.PrimaryButton(tr("加入並開始下載"), "download")
        b_go.clicked.connect(lambda: (self.add_to_queue(), self.start_queue()))
        for w in (b_paste, self.b_analyze):
            w.setMinimumHeight(40)
            br.addWidget(w)
        br.addWidget(self.cookie_chip)
        br.addStretch(1)
        br.addWidget(b_add)
        br.addWidget(b_go)
        uv.addLayout(br)
        v.addWidget(uc)

        cols = hbox(sp=22)
        left = vbox(sp=22)

        # --- 影片資訊
        ic = card()
        ih = hbox(ic, (18, 18, 18, 18), 20)
        self.thumb = ui.Thumb()
        ih.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        iv = vbox(sp=10)
        self.info_title = lab(tr("尚未解析"), "Title", ui.f_ui(17, bold=True), wrap=True)
        self.info_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_uploader = lab(tr("貼上網址後按「解析格式」，可以預覽影片資訊並挑選串流。"), "Text2", ui.f_ui(13), wrap=True)
        iv.addWidget(self.info_title)
        iv.addWidget(self.info_uploader)
        iv.addStretch(1)
        self.info_chips_w = QWidget()
        self.info_chips = FlowLayout(self.info_chips_w, 8, 8)
        iv.addWidget(self.info_chips_w)
        self.q_box = QWidget()
        qv = vbox(self.q_box, sp=6)
        qv.addWidget(caption(tr("B站畫質（依目前帳號）")))
        self.q_chips_w = QWidget()
        self.q_chips = FlowLayout(self.q_chips_w, 6, 6)
        qv.addWidget(self.q_chips_w)
        self.q_box.hide()
        iv.addWidget(self.q_box)
        self.info_warn = lab("", None, ui.f_ui(13), wrap=True, tone="warn")
        self.info_warn.hide()
        iv.addWidget(self.info_warn)
        ih.addLayout(iv, 1)
        left.addWidget(ic)

        # --- B站 視頻選集（分 P）
        self.ep_card = ui.CutFrame(tr=14, border="pink@0.4", fill="panel")
        ev = vbox(self.ep_card, (22, 18, 22, 18), 12)
        eh = hbox(sp=10)
        eh.addWidget(lab("PARTS", "Tag", ui.f_disp(10, spacing=190)))
        eh.addWidget(h2(tr("視頻選集")))
        self.ep_count = lab("", "Muted", ui.f_mono(12))
        eh.addWidget(self.ep_count, 1)
        ev.addLayout(eh)
        tr_ = hbox(sp=8)
        for text, mode in ((tr("全選"), "all"), (tr("全不選"), "none"), (tr("反選"), "invert")):
            b = QPushButton(text)
            b.setProperty("variant", "small")
            b.clicked.connect(lambda _=False, m=mode: self._ep_select(m))
            tr_.addWidget(b)
        tr_.addStretch(1)
        self.ep_range = QLineEdit()
        self.ep_range.setPlaceholderText(tr("選取範圍，例如 1-5, 8, 10-"))
        self.ep_range.setFont(ui.f_mono(12))
        self.ep_range.setMaximumWidth(260)
        self.ep_range.returnPressed.connect(self._ep_apply_range)
        b_rng = QPushButton(tr("套用範圍"))
        b_rng.setProperty("variant", "small")
        b_rng.clicked.connect(self._ep_apply_range)
        tr_.addWidget(self.ep_range, 1)
        tr_.addWidget(b_rng)
        ev.addLayout(tr_)
        self.ep_grid_w = QWidget()
        self.ep_flow = FlowLayout(self.ep_grid_w, 6, 6)
        ev.addWidget(self.ep_grid_w)
        self.ck_ep_folder = QCheckBox(tr("放進以影片標題命名的資料夾"))
        self.ck_ep_folder.setChecked(True)
        ev.addWidget(self.ck_ep_folder)
        ev.addWidget(lab(tr("勾選要下載的集數後按上方「加入佇列」，每一集會分別加入佇列；"
                         "上面的下載選項（畫質、格式、音訊⋯）會套用到每一集。"), "Muted", ui.f_ui(12), wrap=True))
        self.ep_card.hide()
        self.ep_data: dict | None = None
        left.addWidget(self.ep_card)

        # --- 下載選項
        oc = card()
        ov = vbox(oc, (22, 22, 22, 22), 20)
        oh = hbox(sp=14)
        oh.addWidget(h2(tr("下載選項")), 1)
        self.mode_seg = ui.Segmented([("video", tr("影片 + 音訊")), ("audio", tr("只要音訊")), ("video_only", tr("只要影片"))])
        self.mode_seg.changed.connect(lambda *_: (self._update_mode_ui(), self._save_soon()))
        oh.addWidget(self.mode_seg)
        ov.addLayout(oh)

        self.cb_quality = QComboBox()
        for t, val in core.VIDEO_QUALITIES:
            self.cb_quality.addItem(t, val)
        self.cb_codec = QComboBox()
        for t, val in core.CODEC_PREFS:
            self.cb_codec.addItem(t, val)
        self.cb_container = QComboBox()
        self.cb_container.addItems(core.CONTAINERS)
        self.cb_container.setFont(ui.f_mono(13))
        self.cb_vaq = QComboBox()
        for t, val in core.VIDEO_AUDIO_PREFS:
            self.cb_vaq.addItem(t, val)
        self.cb_vaq.setToolTip(tr("影片裡的音軌要挑哪一條（直接取網站提供的音軌，不重新壓縮）。"))
        self.cb_afmt = QComboBox()
        for t, val in core.AUDIO_FORMATS:
            self.cb_afmt.addItem(t, val)
        self.cb_aq = QComboBox()
        for t, val in core.AUDIO_QUALITIES:
            self.cb_aq.addItem(t, val)
        self.cb_afmt.currentIndexChanged.connect(lambda *_: self._update_mode_ui())

        self.video_fields = QWidget()
        vg = QGridLayout(self.video_fields)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.setHorizontalSpacing(14)
        vg.setVerticalSpacing(14)
        self.f_vaq = field(tr("影片音質"), self.cb_vaq)
        for i, w in enumerate((field(tr("畫質"), self.cb_quality), field(tr("編碼"), self.cb_codec),
                               field(tr("影片格式"), self.cb_container), self.f_vaq)):
            vg.addWidget(w, 0, i)
            vg.setColumnStretch(i, 1)
        ov.addWidget(self.video_fields)

        self.audio_fields = QWidget()
        ag = vbox(self.audio_fields, sp=10)
        ar = QGridLayout()
        ar.setHorizontalSpacing(14)
        self.f_aq = field(tr("音質"), self.cb_aq)
        ar.addWidget(field(tr("音訊格式"), self.cb_afmt), 0, 0)
        ar.addWidget(self.f_aq, 0, 1)
        ar.setColumnStretch(0, 1)
        ar.setColumnStretch(1, 1)
        ag.addLayout(ar)
        self.afmt_hint = lab("", "Muted", ui.f_ui(13), wrap=True)
        ag.addWidget(self.afmt_hint)
        ov.addWidget(self.audio_fields)

        # 進階：指定串流
        adv = card("Inset")
        av = vbox(adv, (16, 12, 16, 14), 12)
        self.adv_toggle = QPushButton()
        self.adv_toggle.setObjectName("AdvToggle")
        self.adv_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        at = hbox(self.adv_toggle, (0, 0, 0, 0), 10)
        at.addWidget(lab("ADVANCED", "Tag", ui.f_disp(10, spacing=190)))
        at.addWidget(lab(tr("串流清單（解析後列出所有影片 / 音訊編碼，點一列可指定下載那個串流）"), "Text2", ui.f_ui(13)), 1)
        self.adv_chev = QLabel()
        self.adv_chev.setFixedSize(14, 14)
        self._icon_jobs.append(self._paint_adv_chev)
        at.addWidget(self.adv_chev)
        for w in self.adv_toggle.findChildren(QWidget):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.adv_toggle.clicked.connect(self.toggle_advanced)
        av.addWidget(self.adv_toggle)
        self.adv_body = QWidget()
        abv = vbox(self.adv_body, (0, 0, 0, 0), 14)
        self.cb_vfmt = StreamTable("video")
        self.cb_afmt_id = StreamTable("audio")
        abv.addWidget(self.cb_vfmt)
        abv.addWidget(self.cb_afmt_id)
        self.stream_pick = lab("", "Text2", ui.f_ui(12), wrap=True)
        abv.addWidget(self.stream_pick)
        for tbl in (self.cb_vfmt, self.cb_afmt_id):
            tbl.selectionChanged.connect(self._update_stream_pick)
        self._reset_format_combos()
        av.addWidget(self.adv_body)
        ov.addWidget(adv)

        # 附加選項
        ov.addWidget(caption(tr("附加選項")))
        cw = QWidget()
        cf = FlowLayout(cw, 8, 8)
        self.opt_chips = {}
        for key, text in (("embed_thumbnail", tr("嵌入封面")), ("embed_metadata", tr("寫入標題 / 作者資訊")),
                          ("embed_chapters", tr("嵌入章節")), ("write_thumbnail", tr("另存封面圖 jpg")),
                          ("subtitles", tr("下載字幕")), ("auto_subs", tr("自動產生字幕")),
                          ("playlist", tr("整個播放清單 / 合集")), ("sponsorblock", tr("移除業配 SponsorBlock"))):
            b = QPushButton(text)
            b.setProperty("chip", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.icon(b, "check", "transparent", "accent", 13, 2.6)
            b.toggled.connect(lambda *_: (self._update_mode_ui(), self._save_soon()))
            cf.addWidget(b)
            self.opt_chips[key] = b
        ov.addWidget(cw)
        self.sub_row = QWidget()
        sr = hbox(self.sub_row, sp=10)
        sr.addWidget(caption(tr("字幕語言")))
        self.ed_sublangs = QLineEdit()
        self.ed_sublangs.setPlaceholderText(tr("例如 zh-Hant,zh.*,en"))
        self.ed_sublangs.setFont(ui.f_mono(12))
        sr.addWidget(self.ed_sublangs, 1)
        ov.addWidget(self.sub_row)

        # 輸出資料夾
        pg = QGridLayout()
        pg.setHorizontalSpacing(12)
        self.ed_vdir = QLineEdit()
        self.ed_adir = QLineEdit()
        for col, (icon_name, cap, ed) in enumerate((("folder", tr("影片存放"), self.ed_vdir),
                                                    ("music", tr("音訊存放"), self.ed_adir))):
            pr = card("PathRow")
            ph = hbox(pr, (12, 8, 8, 8), 10)
            ph.addWidget(self.icon_label(icon_name, "accent", 16))
            pv = vbox(sp=0)
            pv.addWidget(lab(cap, "Muted", ui.f_ui(11)))
            ed.setFont(ui.f_mono(12))
            ed.setStyleSheet("QLineEdit { border: none; background: transparent; padding: 0; min-height: 22px; }")
            pv.addWidget(ed)
            ph.addLayout(pv, 1)
            b1 = QPushButton(tr("變更"))
            b1.setProperty("variant", "small")
            b1.clicked.connect(lambda _=False, e=ed: self.pick_dir(e))
            b2 = QPushButton(tr("開啟"))
            b2.setProperty("variant", "small")
            b2.clicked.connect(lambda _=False, e=ed: open_path(e.text()))
            ph.addWidget(b1)
            ph.addWidget(b2)
            pg.addWidget(pr, 0, col)
            pg.setColumnStretch(col, 1)
        ov.addLayout(pg)
        left.addStretch(1)
        cols.addLayout(left, 1)

        # --- 右欄：即時佇列
        aside = card()
        aside.setFixedWidth(340)
        sv = vbox(aside, (18, 18, 18, 18), 14)
        ah = hbox(sp=10)
        ah.addWidget(lab("LIVE", "Tag", ui.f_disp(10, spacing=190)))
        ah.addWidget(h2(tr("進行中")), 1)
        lk = QPushButton(tr("全部佇列"))
        lk.setProperty("variant", "link")
        lk.setCursor(Qt.CursorShape.PointingHandCursor)
        lk.clicked.connect(lambda: self.go("queue"))
        ah.addWidget(lk)
        sv.addLayout(ah)

        self.live_cur = ui.CutFrame(tr=12, border="accent@0.35", fill="panel2")
        lv = vbox(self.live_cur, (14, 14, 14, 14), 10)
        self.live_title = lab("", "Title", ui.f_ui(13), wrap=True)
        self.live_title.setMaximumHeight(40)
        lv.addWidget(self.live_title)
        mr = hbox(sp=8)
        self.live_site = lab("", None, ui.f_ui(11))
        self.live_mode = lab("", "Muted", ui.f_ui(11))
        self.live_stage = lab("", "Muted", ui.f_ui(11))
        mr.addWidget(self.live_site)
        mr.addWidget(self.live_mode)
        mr.addWidget(self.live_stage)
        mr.addStretch(1)
        lv.addLayout(mr)
        self.live_bar = QProgressBar()
        self.live_bar.setRange(0, 1000)
        self.live_bar.setTextVisible(False)
        lv.addWidget(self.live_bar)
        sr2 = hbox(sp=12)
        self.live_pct = lab("", "Tag", ui.f_mono(12, bold=True))
        self.live_speed = lab("", "Text2", ui.f_mono(12))
        self.live_eta = lab("", "Muted", ui.f_mono(12))
        sr2.addWidget(self.live_pct, 1)
        sr2.addWidget(self.live_speed)
        sr2.addWidget(self.live_eta)
        lv.addLayout(sr2)
        sv.addWidget(self.live_cur)
        self.live_empty = lab(tr("目前沒有進行中的下載。"), "Muted", ui.f_ui(13), wrap=True)
        sv.addWidget(self.live_empty)
        self.live_rows = vbox(sp=8)
        sv.addLayout(self.live_rows)
        sv.addStretch(1)
        lb = hbox(sp=8)
        b_stop = QPushButton(tr("停止全部"))
        b_stop.clicked.connect(self.stop_queue)
        b_dir = QPushButton(tr("開啟資料夾"))
        b_dir.clicked.connect(lambda: open_path(self.ed_vdir.text()))
        lb.addWidget(b_stop, 1)
        lb.addWidget(b_dir, 1)
        sv.addLayout(lb)
        cols.addWidget(aside, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(cols)
        v.addWidget(oc)          # 下載選項放在兩欄下方，佔滿整頁寬度
        v.addStretch(1)
        return page_scroll(body)

    # ================================================================== 02 佇列
    def _build_queue_page(self):
        body = QWidget()
        v = vbox(body, (36, 28, 36, 36), 20)
        right = hbox(sp=8)
        self.b_start = ui.PrimaryButton(tr("開始下載"), "play")
        self.b_start.clicked.connect(self.start_queue)
        self.b_stop = self.icon(QPushButton(tr("停止")), "stop", "text", None, 12)
        self.b_stop.setMinimumHeight(44)
        self.b_stop.clicked.connect(self.stop_queue)
        b_retry = QPushButton(tr("重試失敗項目"))
        b_retry.setMinimumHeight(44)
        b_retry.clicked.connect(self.retry_failed)
        b_clear = QPushButton(tr("清除已完成"))
        b_clear.setMinimumHeight(44)
        b_clear.clicked.connect(self.clear_done)
        for w in (self.b_start, self.b_stop, b_retry, b_clear):
            right.addWidget(w)
        v.addLayout(self._page_header("02 // QUEUE", tr("下載佇列"), right))

        tabs = QFrame()
        tabs.setStyleSheet("QFrame { border: none; }")
        th = hbox(tabs, sp=0)
        self.queue_tabs = {}
        grp = QButtonGroup(self)
        for key, text in (("all", tr("全部")), ("running", tr("下載中")), ("waiting", tr("等待中")),
                          ("done", tr("完成")), ("failed", tr("失敗"))):
            b = QPushButton(text)
            b.setProperty("tab", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_queue_filter(k))
            grp.addButton(b)
            th.addWidget(b)
            self.queue_tabs[key] = (b, text)
        th.addStretch(1)
        self.queue_tabs["all"][0].setChecked(True)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T['line']};")
        self.queue_line = line
        tv = vbox(sp=0)
        tv.addWidget(tabs)
        tv.addWidget(line)
        v.addLayout(tv)

        self.queue_list = vbox(sp=12)
        v.addLayout(self.queue_list)
        self.queue_empty = lab(tr("佇列是空的。到「下載」頁貼上網址並按「加入佇列」。"), "Muted", ui.f_ui(14), wrap=True)
        v.addWidget(self.queue_empty)
        v.addStretch(1)
        return page_scroll(body)

    # ================================================================== 03 紀錄
    def _build_log_page(self):
        w = QWidget()
        w.setObjectName("PageBody")
        v = vbox(w, (36, 28, 36, 36), 18)
        right = hbox(sp=8)
        b_copy = QPushButton(tr("複製全部"))
        b_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        b_export = QPushButton(tr("匯出 .log"))
        b_export.clicked.connect(self.export_log)
        b_clear = QPushButton(tr("清除"))
        b_clear.clicked.connect(self.clear_log)
        for b in (b_copy, b_export, b_clear):
            right.addWidget(b)
        v.addLayout(self._page_header("04 // TERMINAL", tr("執行紀錄"), right))

        fr = hbox(sp=8)
        grp = QButtonGroup(self)
        self.log_chips = {}
        for key, text in (("all", tr("全部")), ("download", tr("下載")), ("post", tr("後製")), ("error", tr("錯誤與提示"))):
            b = QPushButton(text)
            b.setProperty("chip", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_log_filter(k))
            grp.addButton(b)
            fr.addWidget(b)
            self.log_chips[key] = b
        self.log_chips["all"].setChecked(True)
        fr.addStretch(1)
        self.ck_autoscroll = QCheckBox(tr("自動捲到最新"))
        self.ck_autoscroll.setChecked(True)
        self.ck_wrap = QCheckBox(tr("自動換行"))
        self.ck_wrap.toggled.connect(lambda on: self.log.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth if on else QPlainTextEdit.LineWrapMode.NoWrap))
        fr.addWidget(self.ck_autoscroll)
        fr.addWidget(self.ck_wrap)
        v.addLayout(fr)

        term = card("TermFrame")
        tv = vbox(term, sp=0)
        head = card("TermHead")
        hh = hbox(head, (14, 8, 14, 8), 8)
        hh.addWidget(ui.Dot("#22E4FF", 8, glow=True))
        hh.addWidget(lab("session.log", "TermHeadText", ui.f_mono(11)), 1)
        hh.addWidget(lab("UTF-8", "TermHeadText", ui.f_mono(11)))
        tv.addWidget(head)
        self.log = QPlainTextEdit()
        self.log.setObjectName("Terminal")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        self.log.setFont(ui.f_mono(13))
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        tv.addWidget(self.log, 1)
        v.addWidget(term, 1)
        return w

    # ================================================================== 04 帳號
    def _build_accounts_page(self):
        body = QWidget()
        v = vbox(body, (36, 28, 36, 36), 20)
        v.addLayout(self._page_header("05 // ACCESS", tr("帳號 / Cookie"), None,
                                      tr("下載時依網址自動判斷網站，套用對應的登入方式。帳號資料只存在這台電腦。")))
        self.cookie_panels = {}
        self.cookie_chips = {}

        bc = ui.CutFrame(tr=18, border="pink@0.4", fill="panel")
        bh = hbox(bc, (22, 22, 22, 22), 24)
        qv = vbox(sp=10)
        self.qr = ui.QRView(196)
        qv.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        qv.addWidget(lab(tr("用 Bilibili App 掃描"), "Muted", ui.f_ui(12)), 0, Qt.AlignmentFlag.AlignHCenter)
        qv.addStretch(1)
        bh.addLayout(qv)
        rv = vbox(sp=16)
        trow = hbox(sp=12)
        t = lab("Bilibili", None, ui.f_ui(18, bold=True))
        t.setStyleSheet("")
        t.setObjectName("SiteBili")
        self._site_title_bili = t
        trow.addWidget(t)
        self.bili_status = chip(tr("檢查中…"), None)
        trow.addWidget(self.bili_status)
        trow.addStretch(1)
        rv.addLayout(trow)
        p = CookiePanel("bilibili", self)
        p.changed.connect(self._cookie_changed)
        p.btn_qr.clicked.connect(self.start_qr)
        p.btn_check.clicked.connect(self.refresh_bili_status)
        p.btn_logout.clicked.connect(self.bili_logout)
        self.cookie_panels["bilibili"] = p
        rv.addWidget(p, 1)
        bh.addLayout(rv, 1)
        v.addWidget(bc)

        g = QGridLayout()
        g.setHorizontalSpacing(20)
        for col, (site, name) in enumerate((("youtube", "YouTube"), ("other", tr("其他網站")))):
            c = card()
            cv = vbox(c, (22, 22, 22, 22), 14)
            hr = hbox(sp=12)
            tl = lab(name, None, ui.f_ui(18, bold=True))
            tl.setObjectName("SiteYT" if site == "youtube" else "Title")
            hr.addWidget(tl, 1)
            ch = chip("", "accent")
            hr.addWidget(ch)
            cv.addLayout(hr)
            self.cookie_chips[site] = ch
            p = CookiePanel(site, self)
            p.changed.connect(self._cookie_changed)
            self.cookie_panels[site] = p
            cv.addWidget(p, 1)
            g.addWidget(c, 0, col)
            g.setColumnStretch(col, 1)
        v.addLayout(g)
        v.addStretch(1)

        self.qr_timer = QTimer(self)
        self.qr_timer.setInterval(2000)
        self.qr_timer.timeout.connect(self._poll_qr)
        self.qr_key = None
        self.qr_polling = False
        self.bili = core.BilibiliLogin()
        QTimer.singleShot(400, self.refresh_bili_status)
        self._icon_jobs.append(self._paint_site_titles)
        return page_scroll(body)

    def _paint_site_titles(self):
        self._site_title_bili.setStyleSheet(f"color: {T['pink']};")
        for w in self.findChildren(QLabel, "SiteYT"):
            w.setStyleSheet(f"color: {T['yt']};")
        if hasattr(self, "queue_line"):
            self.queue_line.setStyleSheet(f"background: {T['line']};")

    # ================================================================== 05 工具
    def _build_tools_page(self):
        body = QWidget()
        v = vbox(body, (36, 28, 36, 36), 20)
        v.addLayout(self._page_header("06 // SYSTEM", tr("工具 / 設定")))

        g = QGridLayout()
        g.setHorizontalSpacing(16)
        self.tool_cards = {}
        g.setVerticalSpacing(16)
        for col, (key, name, desc) in enumerate((
                ("yt-dlp", "yt-dlp", ""),
                ("ffmpeg", "ffmpeg", tr("合併影音、轉 MP3 / FLAC、嵌入封面與字幕都需要（約 200MB 壓縮檔）。")),
                ("deno", "Deno", tr("YouTube 需要 JavaScript 執行環境才能取得完整畫質清單。")),
                ("aria2", "aria2c", tr("多線下載用的外部下載工具（約 2.4MB）。開啟下方「多線下載」前請先安裝。")))):
            c = ui.CutFrame(tr=14, border="accent@0.4" if key == "yt-dlp" else "line", fill="panel")
            cv = vbox(c, (20, 20, 20, 20), 14)
            hr = hbox(sp=10)
            dot = ui.Dot("muted", 8, glow=True)
            hr.addWidget(dot)
            hr.addWidget(lab(name, "Title", ui.f_mono(16, bold=True)), 1)
            st = lab("", None, ui.f_mono(13))
            hr.addWidget(st)
            cv.addLayout(hr)
            path = ui.ElideLabel("", mode=Qt.TextElideMode.ElideMiddle)
            path.setObjectName("Muted")
            path.setFont(ui.f_mono(12))
            cv.addWidget(path)
            if desc:
                cv.addWidget(lab(desc, "Text2", ui.f_ui(13), wrap=True))
            if key == "yt-dlp":
                cv.addWidget(caption(tr("更新通道")))
                self.channel_seg = ui.Segmented([("nightly", "nightly"), ("stable", "stable"), ("master", "master")],
                                                stretch=True)
                self.channel_seg.changed.connect(lambda *_: (self._save_soon(), self._refresh_footer()))
                cv.addWidget(self.channel_seg)
            pw = QWidget()
            pl = vbox(pw, sp=6)
            pr = hbox(sp=8)
            plab = lab("", "Muted", ui.f_mono(11))
            pr.addWidget(plab, 1)
            pl.addLayout(pr)
            bar = QProgressBar()
            bar.setProperty("thin", True)
            bar.setTextVisible(False)
            pl.addWidget(bar)
            pw.hide()
            cv.addWidget(pw)
            cv.addStretch(1)
            br = hbox(sp=8)
            if key == "yt-dlp":
                b = ui.PrimaryButton(tr("更新"), "refresh", cut=8)
                b.setMinimumHeight(42)
                b.clicked.connect(self.update_ytdlp)
                b2 = QPushButton(tr("指定 exe…"))
                b2.setMinimumHeight(42)
                b2.clicked.connect(self.pick_ytdlp)
                br.addWidget(b, 1)
                br.addWidget(b2, 1)
            else:
                b = QPushButton(tr("下載 / 更新"))
                b.setMinimumHeight(42)
                b.clicked.connect(lambda _=False, k=key: self.install_tool(k))
                br.addWidget(b, 1)
            cv.addLayout(br)
            self.tool_cards[key] = dict(dot=dot, status=st, path=path, prog=pw, bar=bar, plab=plab, btn=b)
            g.addWidget(c, col // 2, col % 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        v.addLayout(g)

        row = hbox(sp=16)
        sc = card()
        sv = vbox(sc, (22, 22, 22, 22), 16)
        sv.addWidget(h2(tr("檔名與下載")))
        sv.addWidget(caption(tr("檔名格式")))
        trow = hbox(sp=8)
        self.ed_template = QLineEdit()
        self.ed_template.setProperty("mono", True)
        self.ed_template.setFont(ui.f_mono(13))
        self.ed_template.textChanged.connect(self._update_template_preview)
        b_reset = QPushButton(tr("恢復預設"))
        b_reset.clicked.connect(lambda: self.ed_template.setText(core.DEFAULT_TEMPLATE))
        trow.addWidget(self.ed_template, 1)
        trow.addWidget(b_reset)
        sv.addLayout(trow)
        self.tpl_preview = ui.ElideLabel("", mode=Qt.TextElideMode.ElideMiddle)
        self.tpl_preview.setObjectName("Muted")
        self.tpl_preview.setFont(ui.f_ui(12))
        sv.addWidget(self.tpl_preview)
        gg = QGridLayout()
        gg.setHorizontalSpacing(14)
        self.sp_frag = NumberField(1, 16, 4)
        self.sp_frag.valueChanged.connect(lambda *_: self._save_soon())
        self.ed_rate = QLineEdit()
        self.ed_rate.setPlaceholderText(tr("例如 5M，留空 = 不限速"))
        self.sp_frag.setToolTip(tr("yt-dlp -N：同時下載幾個片段。只對 HLS / DASH 分段串流有效。"))
        gg.addWidget(field(tr("分段串流並行數"), self.sp_frag), 0, 0)
        gg.addWidget(field(tr("下載限速"), self.ed_rate), 0, 1)
        gg.setColumnStretch(0, 1)
        gg.setColumnStretch(1, 2)
        sv.addLayout(gg)
        sv.addWidget(lab(tr("「分段串流並行數」只會加速切成很多小片段的串流（HLS / DASH，例如 Twitch、niconico、直播、"
                         "m3u8 網站）。YouTube 一般影片和 B站影片是單一檔案、單一連線下載，不受這個設定影響。"),
                         "Muted", ui.f_ui(12), wrap=True))
        self.cb_bili_cdn = QComboBox()
        for t, val in core.BILI_CDN_CHOICES:
            self.cb_bili_cdn.addItem(t, val)
        self.cb_bili_cdn.setToolTip(tr("B站影片下載中途斷線（bytes read / more expected / Read timed out）時，"
                                    "可以換一個 CDN 節點試試。"))
        self.cb_bili_cdn.currentIndexChanged.connect(lambda *_: self._save_soon())
        sv.addWidget(field(tr("B站 CDN 節點"), self.cb_bili_cdn))
        br = hbox(sp=8)
        b1 = QPushButton(tr("開啟程式資料夾"))
        b1.clicked.connect(lambda: open_path(str(core.BASE_DIR)))
        b2 = QPushButton(tr("顯示目前的下載指令"))
        b2.clicked.connect(self.show_command)
        br.addWidget(b1)
        br.addWidget(b2)
        br.addStretch(1)
        sv.addLayout(br)
        sv.addStretch(1)
        # 左欄：下載相關（檔名與下載、多線下載、設定備份）；右欄：程式相關（系統、外觀、ABOUT）
        lc = vbox(sp=16)
        lc.addWidget(sc)

        rc = vbox(sp=16)
        mc = card()
        mv = vbox(mc, (22, 22, 22, 22), 14)
        mh = hbox(sp=10)
        mh.addWidget(h2(tr("多線下載")), 1)
        mh.addWidget(lab("aria2c", "Tag", ui.f_mono(12, bold=True)))
        mv.addLayout(mh)
        self.ck_multi = QCheckBox(tr("啟用多線下載"))
        self.ck_multi.toggled.connect(lambda *_: (self._update_multi_ui(), self._save_soon()))
        mv.addWidget(self.ck_multi)
        self.sp_conns = NumberField(1, 16, 16)
        self.sp_conns.setToolTip(tr("同一個檔案同時開幾條連線（aria2c -x / -s，最多 16）。"))
        self.sp_conns.valueChanged.connect(lambda *_: self._save_soon())
        mv.addWidget(field(tr("連線數（1–16）"), self.sp_conns))
        mv.addWidget(caption(tr("套用到")))
        sw = QWidget()
        sf = FlowLayout(sw, 8, 8)
        self.multi_chips = {}
        for key, text in (("bilibili", "Bilibili"), ("youtube", "YouTube"), ("other", tr("其他網站"))):
            b = QPushButton(text)
            b.setProperty("chip", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.icon(b, "check", "transparent", "accent", 13, 2.6)
            b.toggled.connect(lambda *_: self._save_soon())
            sf.addWidget(b)
            self.multi_chips[key] = b
        mv.addWidget(sw)
        self.multi_hint = lab("", "Muted", ui.f_ui(12), wrap=True)
        mv.addWidget(self.multi_hint)
        lc.addWidget(mc)

        sysc = card()
        ssv = vbox(sysc, (22, 22, 22, 22), 12)
        ssv.addWidget(h2(tr("系統")))
        self.ck_tray = QCheckBox(tr("關閉視窗時縮到系統匣（而不是結束程式）"))
        self.ck_notify = QCheckBox(tr("下載完成 / 失敗時顯示系統通知"))
        self.ck_check_updates = QCheckBox(tr("啟動時自動檢查 yt-dlp / aria2c 新版"))
        for w in (self.ck_tray, self.ck_notify, self.ck_check_updates):
            w.toggled.connect(lambda *_: self._save_soon())
            ssv.addWidget(w)
        rrow = hbox(sp=8)
        self.ck_auto_retry = QCheckBox(tr("失敗自動重試"))
        self.ck_auto_retry.toggled.connect(lambda *_: (self._update_retry_ui(), self._save_soon()))
        self.sp_auto_retry = NumberField(0, 10, 3)
        self.sp_auto_retry.setToolTip(tr("最多自動重試幾次（間隔會越拉越長：10 秒、20 秒、40 秒…）。"))
        self.sp_auto_retry.valueChanged.connect(lambda *_: self._save_soon())
        rrow.addWidget(self.ck_auto_retry)
        rrow.addWidget(self.sp_auto_retry)
        rrow.addStretch(1)
        ssv.addLayout(rrow)
        rc.addWidget(sysc)

        bkc = card()
        bkv = vbox(bkc, (22, 22, 22, 22), 12)
        bkv.addWidget(h2(tr("設定備份")))
        bkv.addWidget(lab(tr("匯出目前的設定與 B站登入資料，或匯入到另一台電腦。"), "Text2", ui.f_ui(13), wrap=True))
        bkr = hbox(sp=8)
        b_exp = QPushButton(tr("匯出設定…"))
        b_exp.clicked.connect(self.export_settings_file)
        b_imp = QPushButton(tr("匯入設定…"))
        b_imp.clicked.connect(self.import_settings_file)
        bkr.addWidget(b_exp)
        bkr.addWidget(b_imp)
        bkv.addLayout(bkr)
        lc.addWidget(bkc)

        apc = card()
        av = vbox(apc, (22, 22, 22, 22), 16)
        av.addWidget(h2(tr("外觀")))
        tg = hbox(sp=10)
        self.theme_opts = {}
        grp = QButtonGroup(self)
        for key, text in (("dark", tr("深色")), ("light", tr("淺色")), ("system", tr("跟隨系統"))):
            b = ui.ThemeOption(key, text)
            b.clicked.connect(lambda _=False, k=key: self.set_theme(k))
            grp.addButton(b)
            tg.addWidget(b, 1)
            self.theme_opts[key] = b
        av.addLayout(tg)
        self.ck_glow = QCheckBox(tr("霓虹光暈效果"))
        self.ck_grid = QCheckBox(tr("背景網格"))
        self.ck_glow.toggled.connect(self._effects_changed)
        self.ck_grid.toggled.connect(self._effects_changed)
        av.addWidget(self.ck_glow)
        av.addWidget(self.ck_grid)
        # 語言：標題固定附英文，切到看不懂的語言時也找得到這裡
        lrow = hbox(sp=10)
        lrow.addWidget(lab(tr("語言") + ("" if i18n.LANG == "en" else " / Language"), "Text2", ui.f_ui(13)))
        self.cb_lang = QComboBox()
        sys_name = dict(i18n.LANGS).get(i18n.system_language(), "English")
        self.cb_lang.addItem(f"{tr('跟隨系統')}（{sys_name}）" if i18n.LANG != "en" else f"Follow system ({sys_name})", "system")
        for code, name in i18n.LANGS:
            self.cb_lang.addItem(name, code)
        self.cb_lang.activated.connect(lambda *_: self._language_changed())
        lrow.addWidget(self.cb_lang, 1)
        av.addLayout(lrow)
        rc.addWidget(apc)

        abc = ui.CutFrame(tr=14, border="line", fill="panel")
        abv = vbox(abc, (22, 20, 22, 20), 6)
        abv.addWidget(lab("ABOUT", "Tag", ui.f_disp(10, spacing=190)))
        abv.addWidget(lab(core.APP_NAME.upper(), "Title", ui.f_disp(15, bold=True, spacing=110)))
        abv.addWidget(lab(core.APP_EDITION, "Text2", ui.f_ui(13)))
        self.about_ver = lab(tr('版本 v{APP_VERSION}').format(APP_VERSION=core.APP_VERSION), "Tag", ui.f_mono(13, bold=True))
        abv.addWidget(self.about_ver)
        self.about_core = lab("", "Muted", ui.f_mono(12))
        abv.addWidget(self.about_core)
        b_help = QPushButton(tr("開啟使用說明"))
        b_help.clicked.connect(lambda: open_path(str(core.BASE_DIR / "使用說明.txt"))
                               if (core.BASE_DIR / "使用說明.txt").exists() else None)
        abv.addSpacing(6)
        abv.addWidget(b_help, 0, Qt.AlignmentFlag.AlignLeft)
        rc.addWidget(abc)
        rc.addStretch(1)
        lc.addStretch(1)
        row.addLayout(lc, 3)
        row.addLayout(rc, 2)
        v.addLayout(row)
        v.addStretch(1)
        return page_scroll(body)

    # ================================================================== 設定 <-> 介面
    def _set_combo(self, cb: QComboBox, value):
        i = cb.findData(value)
        if i < 0:
            i = cb.findText(str(value))
        cb.setCurrentIndex(max(0, i))

    def _load_widgets(self):
        s = self.s
        self.mode_seg.set(s["mode"] if s["mode"] in ("video", "audio", "video_only") else "video")
        self._set_combo(self.cb_quality, s["video_quality"])
        self._set_combo(self.cb_codec, s["codec_pref"])
        self._set_combo(self.cb_container, s["container"])
        self._set_combo(self.cb_vaq, s["video_audio_pref"])
        self._set_combo(self.cb_afmt, s["audio_format"])
        self._set_combo(self.cb_aq, s["audio_quality"])
        for key, b in self.opt_chips.items():
            b.blockSignals(True)
            b.setChecked(bool(s.get(key)))
            b.blockSignals(False)
        self.ed_sublangs.setText(s["sub_langs"])
        self.ed_vdir.setText(s["video_dir"])
        self.ed_adir.setText(s["audio_dir"])
        self.ed_template.setText(s["template"])
        self.sp_frag.setValue(int(s["concurrent_fragments"] or 1))
        self._set_combo(self.cb_bili_cdn, s.get("bili_cdn") or "auto")
        for w in (self.ck_multi, self.sp_conns, *self.multi_chips.values()):
            w.blockSignals(True)
        self.ck_multi.setChecked(bool(s.get("multi_conn")))
        self.sp_conns.setValue(int(s.get("aria2_conns") or 16))
        for key, b in self.multi_chips.items():
            b.setChecked(bool((s.get("multi_sites") or {}).get(key, True)))
        for w in (self.ck_multi, self.sp_conns, *self.multi_chips.values()):
            w.blockSignals(False)
        self.ed_rate.setText(s["limit_rate"])
        self.channel_seg.set(s.get("update_channel") or "nightly")
        i = self.cb_lang.findData(s.get("language") or "system")
        self.cb_lang.setCurrentIndex(max(0, i))
        for w in (self.ck_tray, self.ck_notify, self.ck_check_updates, self.ck_auto_retry, self.sp_auto_retry):
            w.blockSignals(True)
        self.ck_tray.setChecked(bool(s.get("minimize_to_tray", True)))
        self.ck_notify.setChecked(bool(s.get("notify_on_finish", True)))
        self.ck_check_updates.setChecked(bool(s.get("check_updates_on_start", True)))
        self.ck_auto_retry.setChecked(bool(s.get("auto_retry", True)))
        self.sp_auto_retry.setValue(int(s.get("auto_retry_max", 3) or 0))
        for w in (self.ck_tray, self.ck_notify, self.ck_check_updates, self.ck_auto_retry, self.sp_auto_retry):
            w.blockSignals(False)
        self._update_retry_ui()
        self.theme_opts.get(s.get("theme", "dark"), self.theme_opts["dark"]).setChecked(True)
        self.ck_glow.blockSignals(True)
        self.ck_grid.blockSignals(True)
        self.ck_glow.setChecked(bool(s.get("glow", True)))
        self.ck_grid.setChecked(bool(s.get("grid", True)))
        self.ck_glow.blockSignals(False)
        self.ck_grid.blockSignals(False)
        self.adv_body.setVisible(bool(s.get("advanced_open")))
        self._paint_adv_chev()
        self._update_multi_ui()
        for cb in self.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(6)
        self._update_template_preview()

    def collect(self) -> dict:
        s = self.s
        s["mode"] = self.mode_seg.value() or "video"
        s["video_quality"] = self.cb_quality.currentData()
        s["codec_pref"] = self.cb_codec.currentData()
        s["container"] = self.cb_container.currentText()
        s["video_audio_pref"] = self.cb_vaq.currentData()
        s["audio_format"] = self.cb_afmt.currentData()
        s["audio_quality"] = self.cb_aq.currentData()
        for key, b in self.opt_chips.items():
            s[key] = b.isChecked()
        s["sub_langs"] = self.ed_sublangs.text().strip()
        s["video_dir"] = self.ed_vdir.text().strip()
        s["audio_dir"] = self.ed_adir.text().strip()
        s["template"] = self.ed_template.text().strip() or core.DEFAULT_TEMPLATE
        s["concurrent_fragments"] = self.sp_frag.value()
        s["bili_cdn"] = self.cb_bili_cdn.currentData() or "auto"
        s["multi_conn"] = self.ck_multi.isChecked()
        s["aria2_conns"] = self.sp_conns.value()
        s["multi_sites"] = {k: b.isChecked() for k, b in self.multi_chips.items()}
        s["limit_rate"] = self.ed_rate.text().strip()
        s["update_channel"] = self.channel_seg.value() or "nightly"
        s["minimize_to_tray"] = self.ck_tray.isChecked()
        s["notify_on_finish"] = self.ck_notify.isChecked()
        s["check_updates_on_start"] = self.ck_check_updates.isChecked()
        s["auto_retry"] = self.ck_auto_retry.isChecked()
        s["auto_retry_max"] = self.sp_auto_retry.value()
        s["glow"] = self.ck_glow.isChecked()
        s["grid"] = self.ck_grid.isChecked()
        s["advanced_open"] = not self.adv_body.isHidden()
        return s

    def _save_soon(self):
        QTimer.singleShot(0, self.save)

    def save(self):
        try:
            self.collect()
            core.save_settings(self.s)
        except Exception as e:  # noqa: BLE001
            self.append_log(tr('[GUI] 儲存設定失敗：{e}').format(e=e))

    def set_theme(self, key):
        self.s["theme"] = key
        self.apply_theme()
        self.save()

    def _language_changed(self):
        """換語言：存設定；實際語言有變才問要不要重新啟動（語言在程式啟動時就決定了）。"""
        code = self.cb_lang.currentData() or "system"
        if code == (self.s.get("language") or "system"):
            return
        self.s["language"] = code
        self.save()
        new_lang = i18n.system_language() if code == "system" else code
        if new_lang == i18n.LANG:
            return
        if self.proc is not None:
            QMessageBox.information(self, APP_TITLE, tr(
                "語言已變更，會在下次啟動程式時生效（目前還有下載進行中，所以先不重新啟動）。"))
            return
        r = QMessageBox.question(self, APP_TITLE, tr("語言已變更，重新啟動程式後才會生效。要現在重新啟動嗎？"))
        if r == QMessageBox.StandardButton.Yes:
            self._restart_app()

    def _restart_app(self):
        """開一個新的自己，再關掉目前這個（不縮到系統匣）。"""
        if getattr(sys, "frozen", False):
            prog, args = sys.executable, sys.argv[1:]
        else:
            prog, args = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
        p = QProcess()
        p.setProgram(prog)
        p.setArguments(args)
        p.setWorkingDirectory(str(core.BASE_DIR))
        env = QProcessEnvironment.systemEnvironment()
        # 打包成單一 exe 時：新開的 exe 會繼承這些環境變數，誤以為自己是子程序而沿用舊程式的
        # _MEIxxxx 暫存資料夾；舊程式結束時把它刪掉，新程式就「Failed to start embedded python interpreter」。
        # PYINSTALLER_RESET_ENVIRONMENT=1（PyInstaller 6.9+）讓新程式當成全新啟動、自己解壓縮。
        for k in env.keys():
            if k.upper().startswith("_PYI_") or k.upper() == "_MEIPASS2":
                env.remove(k)
        env.insert("PYINSTALLER_RESET_ENVIRONMENT", "1")
        p.setProcessEnvironment(env)
        res = p.startDetached()
        ok = res[0] if isinstance(res, tuple) else bool(res)
        if not ok:
            QMessageBox.warning(self, APP_TITLE, tr("無法自動重新啟動，請手動關閉程式再打開。"))
            return
        self._force_quit = True
        self.close()

    def _effects_changed(self, *_):
        ui.EFFECTS["glow"] = self.ck_glow.isChecked()
        ui.EFFECTS["grid"] = self.ck_grid.isChecked()
        for cf in self.findChildren(ui.CutFrame):
            cf.refresh_effects()
        self.centralWidget().update()
        self.save()

    def toggle_advanced(self):
        self.adv_body.setVisible(self.adv_body.isHidden())
        self._paint_adv_chev()
        self._save_soon()

    def _paint_adv_chev(self):
        opened = not self.adv_body.isHidden() if hasattr(self, "adv_body") else False
        self.adv_chev.setPixmap(ui.svg_pixmap("chev_up" if opened else "chev_down", T["muted"], 14))

    def _update_mode_ui(self):
        mode = self.mode_seg.value() or "video"
        audio = mode == "audio"
        self.video_fields.setVisible(not audio)
        self.audio_fields.setVisible(audio)
        self.f_vaq.setEnabled(mode == "video")
        afmt = self.cb_afmt.currentData()
        self.f_aq.setEnabled(afmt in ("mp3", "m4a", "opus"))
        tips = {
            "mp3": tr("MP3：相容性最好。網站音源本身是有損的，選 320 kbps 或 VBR 0 即可。"),
            "flac": tr("FLAC：無損格式，但網站來源（AAC / Opus）本身已是有損，轉 FLAC 不會讓音質變好，只是避免再次壓縮；檔案會大很多。"),
            "m4a": tr("M4A：若來源就是 AAC 會直接取出，不重新壓縮。"),
            "opus": tr("Opus：YouTube 的最高音質來源通常就是 Opus。"),
            "wav": tr("WAV：未壓縮，檔案最大，無法嵌入封面。"),
            "original": tr("原始音訊：直接取出網站提供的最佳音軌，完全不轉檔。"),
        }
        self.afmt_hint.setText(tips.get(afmt, ""))
        self.cb_vfmt.setEnabled(not audio)
        self.cb_afmt_id.setEnabled(mode != "video_only")
        self.opt_chips["embed_chapters"].setEnabled(not audio)
        self.opt_chips["auto_subs"].setEnabled(self.opt_chips["subtitles"].isChecked())
        self.sub_row.setVisible(self.opt_chips["subtitles"].isChecked())

    def _update_retry_ui(self):
        self.sp_auto_retry.setEnabled(self.ck_auto_retry.isChecked())

    def _update_multi_ui(self):
        on = self.ck_multi.isChecked()
        has = bool(core.find_aria2())
        self.sp_conns.setEnabled(on)
        for b in self.multi_chips.values():
            b.setEnabled(on)
        if on and not has:
            self.multi_hint.setText(tr("尚未安裝 aria2c：請先在上方 aria2c 卡片按「下載 / 更新」，安裝前會自動改用一般下載。"))
            set_tone(self.multi_hint, "warn")
        else:
            self.multi_hint.setText(tr('同一個檔案開多條連線同時下載，頻寬沒跑滿時通常快很多。B站最多用 {BILI_MAX_CONNS} 條（連線太多會被 CDN 限流）。多線下載時進度約每秒更新一次；遇到問題可以關掉，回到一般下載。').format(BILI_MAX_CONNS=core.BILI_MAX_CONNS))
            set_tone(self.multi_hint, None)

    def _update_template_preview(self, *_):
        tpl = self.ed_template.text().strip() or core.DEFAULT_TEMPLATE
        info = self.info if (self.info and not self.info.get("is_playlist")) else None
        vals = {"upload_date": (info or {}).get("upload_date") or "20260201",
                "title": (info or {}).get("title") or tr("影片標題"),
                "id": tr("影片ID"), "ext": self.cb_container.currentText() or "mkv",
                "uploader": (info or {}).get("uploader") or tr("上傳者")}
        prev = re.sub(r"%\((\w+)[^)]*\)[sdf]", lambda m: str(vals.get(m.group(1), m.group(1))), tpl)
        self.tpl_preview.setText(tr("預覽：") + prev)

    # ================================================================== 網址 / 解析
    def urls(self) -> list[str]:
        out = []
        for line in self.url_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = core.normalize_url(line)
            if line not in out:
                out.append(line)
        return out

    def paste_url(self):
        t = QApplication.clipboard().text().strip()
        if t:
            cur = self.url_edit.toPlainText().strip()
            self.url_edit.setPlainText((cur + "\n" + t).strip() if cur else t)

    def _refresh_cookie_chip(self, *_):
        urls = self.urls()
        if not urls:
            self.cookie_chip.hide()
            return
        site = core.detect_site(urls[0])
        args = core.cookie_args(site, self.s)
        txt = f"{core.SITE_NAMES[site]} · {core.cookie_status_text(site, self.s)}"
        self.cookie_chip.setText(txt)
        set_tone(self.cookie_chip, "ok" if args else "warn" if site == "bilibili" else None)
        self.cookie_chip.show()

    def _cookie_changed(self):
        self._refresh_cookie_chip()
        for site, ch in self.cookie_chips.items():
            ch.setText(core.cookie_status_text(site, self.s))
            mode = self.s["cookies"][site].get("mode")
            set_tone(ch, "accent" if mode != "none" else None)
        self._save_soon()

    def _reset_format_combos(self):
        self.cb_vfmt.clear()
        self.cb_afmt_id.clear()
        self._update_stream_pick()

    def _update_stream_pick(self):
        """串流清單底下的一行說明：目前會怎麼挑串流。"""
        if not hasattr(self, "stream_pick"):
            return
        v, a = self.cb_vfmt.currentData(), self.cb_afmt_id.currentData()
        if not v and not a:
            self.stream_pick.setText(tr("目前：自動。依上面的畫質 / 編碼設定挑最好的串流；點表格裡的一列可以改成指定那個串流（只套用在解析的這個網址）。"))
            set_tone(self.stream_pick, None)
            return
        parts = []
        if v:
            parts.append(tr("影片 ") + self.cb_vfmt.current_text())
        if a:
            parts.append(tr("音訊 ") + self.cb_afmt_id.current_text())
        self.stream_pick.setText(tr("指定下載：") + tr("　＋　").join(parts) + tr("（只套用在解析的這個網址）"))
        set_tone(self.stream_pick, "accent")

    def _set_quality_chips(self, items):
        """items: [(文字, tone, tooltip)]；None = 隱藏區塊"""
        while self.q_chips.count():
            it = self.q_chips.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        if not items:
            self.q_box.hide()
            return
        for text, tone, tip in items:
            c = chip(text, tone)
            if tip:
                c.setToolTip(tip)
            self.q_chips.addWidget(c)
        self.q_box.show()
        self.q_chips_w.updateGeometry()

    @staticmethod
    def _bili_qualities(err: str):
        """從解析時的 stderr 取出 BiliCDNFix 外掛回報的畫質清單。"""
        items = None
        for line in err.splitlines():
            if line.startswith("@@BILIFMT@@"):
                try:
                    items = json.loads(line[len("@@BILIFMT@@"):])
                except ValueError:
                    pass
        return items

    def _show_bili_qualities(self, items, best_label):
        has_cookie = bool(core.cookie_args("bilibili", self.s))
        chips_, vip_locked, login_locked = [], [], []
        for it in items:
            name = it.get("name") or str(it.get("q"))
            if it.get("ok"):
                chips_.append((name, "ok", tr("可以下載")))
                continue
            if it.get("vip") or (has_cookie and not it.get("login")):
                reason = "vip"
            elif it.get("login") and not has_cookie:
                reason = "login"
            else:
                reason = "vip" if has_cookie else "login"
            if reason == "vip":
                vip_locked.append(name)
                chips_.append((tr('{name} · 需大會員').format(name=name), "warn", tr("目前帳號不是大會員，無法下載這個畫質")))
            else:
                login_locked.append(name)
                chips_.append((tr('{name} · 需登入').format(name=name), "bad", tr("需要登入 B站 才能下載這個畫質")))
        self._set_quality_chips(chips_)
        # 串流清單裡也列出來（灰色、不能選）
        for name in vip_locked + login_locked:
            self.cb_vfmt.add_locked(name, tr("需大會員") if name in vip_locked else tr("需登入"))
        msgs = []
        if login_locked:
            msgs.append(tr('未登入 B站：{join} 需要登入才能下載，請到「帳號 / Cookie」掃碼登入。').format(join='、'.join(login_locked)))
        if vip_locked:
            msgs.append(tr('目前帳號不是大會員：{join} 無法下載。').format(join='、'.join(vip_locked)))
        if msgs:
            if best_label:
                msgs.append(tr('會下載可用的最高畫質：{best_label}。').format(best_label=best_label))
            self.info_warn.setText("".join(msgs))
            self.info_warn.show()

    def _set_info_chips(self, items):
        while self.info_chips.count():
            it = self.info_chips.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        for text, tone in items:
            self.info_chips.addWidget(chip(text, tone, mono=True))
        self.info_chips_w.updateGeometry()

    def analyze(self):
        urls = self.urls()
        if not urls:
            QMessageBox.information(self, APP_TITLE, tr("請先輸入網址"))
            return
        exe = self._need_ytdlp()
        if not exe:
            return
        if self.info_proc and self.info_proc.state() != QProcess.ProcessState.NotRunning:
            return
        self.collect()
        url = urls[0]
        self.info_url = url
        self.info = None
        self._reset_format_combos()
        self.info_title.setText(tr("解析中…"))
        self.info_uploader.setText(url)
        self.info_warn.hide()
        self._set_info_chips([])
        self._set_quality_chips(None)
        self.thumb.set_pixmap(None)
        self.thumb.set_duration("")
        self.b_analyze.setEnabled(False)
        self._ep_clear()
        bvid = core.bili_bvid(url)
        if bvid:
            cur_p = core.bili_part_of(url) or 1
            run_bg(lambda _p: core.bili_pagelist(bvid),
                   lambda pages, b=bvid, c=cur_p: self._show_parts(b, pages, c),
                   lambda e: self.append_log(tr('[GUI] 讀取視頻選集失敗：{e}').format(e=e)))
        p = QProcess(self)
        p.setProcessEnvironment(proc_env(self.s))
        p.setProgram(exe)
        p.setArguments(core.build_info_args(url, self.s))
        p.finished.connect(lambda code, _st, proc=p: self._analyze_done(proc, code))
        p.errorOccurred.connect(lambda err, proc=p: self._analyze_error(proc, err))
        self.info_proc = p
        self.append_log(tr('[解析] {url}').format(url=url))
        p.start()

    def _analyze_error(self, _p, err):
        if err == QProcess.ProcessError.FailedToStart:
            self.b_analyze.setEnabled(True)
            self.info_title.setText(tr("無法執行 yt-dlp"))
            self.info_uploader.setText(tr("請到「工具 / 設定」檢查 yt-dlp。"))

    def _analyze_done(self, p: QProcess, code: int):
        self.b_analyze.setEnabled(True)
        out = decode_out(p.readAllStandardOutput().data())
        err_raw = decode_out(p.readAllStandardError().data())
        err = "\n".join(x for x in err_raw.splitlines() if not x.startswith("@@BILIFMT@@"))
        if code != 0 or not out.strip():
            msg = err.strip().splitlines()[-1] if err.strip() else tr('結束代碼 {code}').format(code=code)
            self.info_title.setText(tr("解析失敗"))
            self.info_uploader.setText(msg)
            for line in err.strip().splitlines():
                self.append_log(line)
            for t in explain_error(err):
                self.append_log(tr("提示 › ") + t)
            tips = explain_error(err)
            if tips:
                self.info_warn.setText(tips[0])
                self.info_warn.show()
            return
        try:
            info = core.parse_info(json.loads(out.strip().splitlines()[-1]))
        except Exception as e:  # noqa: BLE001
            self.info_title.setText(tr("解析結果讀取失敗"))
            self.info_uploader.setText(str(e))
            return
        self.info = info
        site = core.detect_site(self.info_url)
        self.info_title.setText(info["title"] or self.info_url)
        if info["is_playlist"]:
            self.info_uploader.setText(tr('{uploader}\u3000播放清單 / 合集').format(uploader=info['uploader']))
            self._set_info_chips([(tr('共 {count} 部').format(count=info['count']), "accent")])
            self.info_warn.setText(tr("這是播放清單 / 合集網址，加入佇列後會下載全部；"
                                   "開啟「整個播放清單 / 合集」會另外建立以清單名稱命名的子資料夾。"))
            self.info_warn.show()
            self._update_template_preview()
            return
        self.cb_vfmt.set_rows(info["video_formats"])
        self.cb_afmt_id.set_rows(info["audio_formats"])
        self._update_stream_pick()
        if info["video_formats"] or info["audio_formats"]:
            if self.adv_body.isHidden():       # 解析完自動展開串流清單
                self.toggle_advanced()
        self.info_uploader.setText(info["uploader"] or core.SITE_NAMES[site])
        self.thumb.set_duration(info["duration"])
        chips = []
        d = info["upload_date"]
        if d and len(d) == 8:
            chips.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", None))
        if info["video_formats"]:
            chips.append(("MAX " + info["video_formats"][0]["label"].split(" ")[0], "accent"))
        chips.append((tr('影片串流 {n}').format(n=len(info['video_formats'])), None))
        chips.append((tr('音訊串流 {n}').format(n=len(info['audio_formats'])), None))
        self._set_info_chips(chips)
        heights = [vf["height"] for vf in info["video_formats"]]
        bili_q = self._bili_qualities(err_raw) if site == "bilibili" else None
        if bili_q:
            ok_names = [it.get("name") for it in bili_q if it.get("ok")]
            self._show_bili_qualities(bili_q, ok_names[0] if ok_names else "")
        elif site == "bilibili" and heights and max(heights) <= 720 and not core.cookie_args("bilibili", self.s):
            self.info_warn.setText(tr("目前未登入 Bilibili，最高只有 720P。請到「帳號 / Cookie」掃碼登入。"))
            self.info_warn.show()
        self.append_log(tr('[解析] 完成：{title}（{n} 個影片串流、{n2} 個音訊串流）').format(title=info['title'], n=len(info['video_formats']), n2=len(info['audio_formats'])))
        self._update_template_preview()
        if info.get("thumbnail"):
            url = info["thumbnail"]
            run_bg(lambda _p: fetch_bytes(url), self._thumb_loaded, lambda _e: None)

    # ------------------------------------------------------------------ 視頻選集
    def _ep_clear(self):
        self.ep_data = None
        while self.ep_flow.count():
            it = self.ep_flow.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        self.ep_range.clear()
        self.ep_card.hide()

    def _show_parts(self, bvid, pages, cur_p):
        cur_bvid = core.bili_bvid(self.info_url) if self.info_url else None
        if cur_bvid != bvid or len(pages) < 2:     # 已經改解析別的網址，或只有一集
            return
        self._ep_clear()
        self.ep_data = {"bvid": bvid, "pages": pages, "buttons": {}}
        fm = self.fontMetrics()
        for pg in pages:
            n, part = pg["page"], pg["part"]
            label = f"{n}  {part}" if part else str(n)
            b = QPushButton(fm.elidedText(label, Qt.TextElideMode.ElideRight, 170))
            b.setProperty("chip", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            dur = core.human_duration(pg.get("duration"))
            b.setToolTip(f"P{n}  {part}" + (tr('\n長度 {dur}').format(dur=dur) if dur else ""))
            self.icon(b, "check", "transparent", "accent", 13, 2.6)
            b.setChecked(n == cur_p)
            b.toggled.connect(lambda *_: self._ep_update_count())
            self.ep_flow.addWidget(b)
            self.ep_data["buttons"][n] = b
        self.ep_card.show()
        self.ep_grid_w.updateGeometry()
        self._ep_update_count()
        self.append_log(tr('[解析] 視頻選集：共 {n} 集').format(n=len(pages)))

    def _ep_selected(self) -> list[int]:
        if not self.ep_data:
            return []
        return [n for n, b in sorted(self.ep_data["buttons"].items()) if b.isChecked()]

    def _ep_update_count(self):
        if self.ep_data:
            self.ep_count.setText(tr('共 {n} 集 · 已選 {n2}').format(n=len(self.ep_data['pages']), n2=len(self._ep_selected())))

    def _ep_select(self, mode):
        if not self.ep_data:
            return
        for b in self.ep_data["buttons"].values():
            b.blockSignals(True)
            b.setChecked(True if mode == "all" else False if mode == "none" else not b.isChecked())
            b.blockSignals(False)
        self._ep_update_count()

    def _ep_apply_range(self):
        if not self.ep_data:
            return
        sel = set(core.parse_ranges(self.ep_range.text(), max(self.ep_data["buttons"])))
        if not sel:
            QMessageBox.information(self, APP_TITLE, tr("看不懂這個範圍，請用像「1-5, 8, 10-」的寫法。"))
            return
        for n, b in self.ep_data["buttons"].items():
            b.blockSignals(True)
            b.setChecked(n in sel)
            b.blockSignals(False)
        self._ep_update_count()

    def _thumb_loaded(self, data: bytes):
        pm = QPixmap()
        if pm.loadFromData(data):
            self.thumb.set_pixmap(pm)

    def _custom_format_for(self, url: str) -> str:
        """只有對「剛解析過的那個網址」才套用指定串流。"""
        if url != self.info_url or not self.info or self.info.get("is_playlist"):
            return ""
        mode = self.s["mode"]
        vid = self.cb_vfmt.currentData() or ""
        aid = self.cb_afmt_id.currentData() or ""
        if mode == "audio":
            return f"{aid}/ba/b" if aid else ""
        if mode == "video_only":
            return vid
        if vid and aid:
            return f"{vid}+{aid}"
        if vid:
            return f"{vid}+ba/{vid}"
        if aid:
            q = self.s["video_quality"]
            vv = "bv*" if q == "best" else f"bv*[height<={q}]"
            return f"{vv}+{aid}/{vv}+ba/b"
        return ""

    # ================================================================== 佇列
    def _mode_text(self, snap, fmt):
        if snap["mode"] == "audio":
            name = dict((v, t.split(tr("（"))[0].strip()) for t, v in core.AUDIO_FORMATS)[snap["audio_format"]]
            txt = tr('只要音訊 · {name}').format(name=name)
            if snap["audio_format"] in ("mp3", "m4a", "opus"):
                q = dict((v, t) for t, v in core.AUDIO_QUALITIES)[snap["audio_quality"]]
                txt += " " + ("VBR 0" if snap["audio_quality"] == "0" else q.replace(" kbps", "k"))
        else:
            txt = (tr("影片 + 音訊") if snap["mode"] == "video" else tr("只要影片")) + f" · {snap['container']}"
            if snap["video_quality"] != "best" and not fmt:
                txt += f" · ≤{snap['video_quality']}p"
            if snap["mode"] == "video" and snap["video_audio_pref"] != "best" and not fmt:
                txt += " · " + {"abr": tr("高位元率"), "aac": "AAC", "opus": "Opus", "flac": "FLAC",
                                "small": tr("省空間")}.get(snap["video_audio_pref"], "")
        if fmt:
            txt += tr(" · 指定串流")
        return txt

    def add_to_queue(self):
        urls = self.urls()
        if not urls:
            QMessageBox.information(self, APP_TITLE, tr("請先輸入網址"))
            return
        self.collect()
        self.save()
        # 視頻選集：把剛解析的那支 BV 展開成勾選的各集
        items = []          # (url, 標題, 資料夾, 指定格式)
        ep = self.ep_data if (self.ep_data and not self.ep_card.isHidden()) else None
        for url in urls:
            if ep and core.bili_bvid(url) == ep["bvid"]:
                sel = self._ep_selected()
                if not sel:
                    QMessageBox.information(self, APP_TITLE, tr("視頻選集裡還沒有勾選任何一集。"))
                    return
                base_title = core.strip_part_suffix((self.info or {}).get("title") or "") or ep["bvid"]
                folder = core.safe_folder(base_title).replace("%", "%%") \
                    if (self.ck_ep_folder.isChecked() and len(sel) > 1) else None
                base_fmt = self._custom_format_for(self.info_url)
                parts = {pg["page"]: pg["part"] for pg in ep["pages"]}
                for n in sel:
                    purl = core.bili_part_url(ep["bvid"], n)
                    fmt = base_fmt
                    if fmt:      # 指定串流是依解析的那一集挑的，其他集找不到時退回自動
                        fmt += {"audio": "/ba/b", "video_only": "/bv"}.get(self.s["mode"], "/bv*+ba/b")
                    items.append((purl, f"{base_title} p{n:02d} {parts.get(n, '')}".strip(), folder, fmt))
            else:
                title = (self.info or {}).get("title") if url == self.info_url else None
                items.append((url, title or url, None, self._custom_format_for(url)))
        for url, title, folder, fmt in items:
            snap = copy.deepcopy(self.s)
            if folder:
                snap["template"] = f"{folder}/" + (snap.get("template") or core.DEFAULT_TEMPLATE)
                snap["playlist"] = False
            job = {"url": url, "s": snap, "fmt": fmt, "state": "waiting", "title": title, "file": "",
                   "out_dir": snap["audio_dir"] if snap["mode"] == "audio" else snap["video_dir"],
                   "site": core.detect_site(url), "errors": [], "tips": [], "stage": 0,
                   "aria2": core.uses_aria2(url, snap), "aria2_n": int(snap.get("aria2_conns") or 16),
                   "cdn_list": core.bili_cdn_rotation(snap.get("bili_cdn") or "auto"), "cdn_idx": 0,
                   "auto_retries": 0}
            if job["aria2"] and job["site"] == "bilibili":
                job["aria2_n"] = min(job["aria2_n"], core.BILI_MAX_CONNS)
            job["mode_text"] = self._mode_text(snap, fmt)
            jc = JobCard(job, self)
            jc.stop_clicked.connect(self.stop_job)
            jc.remove_clicked.connect(self.remove_job)
            jc.retry_clicked.connect(self.retry_job)
            jc.open_clicked.connect(lambda j: reveal_file(j.get("file", ""), j["out_dir"]))
            job["card"] = jc
            self.jobs.append(job)
            self.queue_list.addWidget(jc)
        self.url_edit.clear()
        self._ep_clear()
        self.append_log(tr('[GUI] 加入佇列 {n} 個項目').format(n=len(items)))
        if self.s.get("multi_conn") and not core.find_aria2():
            self.append_log(tr("提示 › 已開啟多線下載，但尚未安裝 aria2c，這次改用一般下載。請到「工具 / 設定」安裝 aria2c。"))
        self._refresh_counts()
        self._refresh_live()

    def _set_state(self, job, state):
        job["state"] = state
        job["card"].apply_state()
        self._refresh_counts()
        self._refresh_live()

    def _refresh_counts(self):
        cnt = {"running": 0, "waiting": 0, "done": 0, "failed": 0}
        for j in self.jobs:
            st = j["state"]
            key = "running" if st in ("running", "post") else "failed" if st in ("failed", "stopped") else st
            cnt[key] += 1
        total = len(self.jobs)
        self.pill_running.setText(tr('下載中 {running}').format(running=cnt['running']))
        self.pill_waiting.setText(tr('等待 {waiting}').format(waiting=cnt['waiting']))
        active = cnt["running"] + cnt["waiting"]
        self.nav_badge.setText(str(active))
        self.nav_badge.setVisible(active > 0)
        for key, (b, text) in self.queue_tabs.items():
            n = total if key == "all" else cnt[key]
            b.setText(f"{text}  {n}")
        self.queue_empty.setVisible(total == 0)
        self.b_start.setEnabled(self.proc is None and cnt["waiting"] > 0)
        self.b_stop.setEnabled(self.proc is not None)
        self._apply_queue_filter()

    def set_queue_filter(self, key):
        self.queue_filter = key
        self._apply_queue_filter()

    def _apply_queue_filter(self):
        f = self.queue_filter
        for j in self.jobs:
            st = j["state"]
            key = "running" if st in ("running", "post") else "failed" if st in ("failed", "stopped") else st
            j["card"].setVisible(f == "all" or f == key)

    def _refresh_live(self):
        cur = self.cur
        self.live_cur.setVisible(cur is not None)
        self.live_empty.setVisible(cur is None)
        if cur:
            self.live_title.setText(cur["title"])
            self.live_site.setText(core.SITE_NAMES[cur["site"]].replace(tr("其他網站"), tr("其他")))
            col = {"youtube": T["yt"], "bilibili": T["pink"]}.get(cur["site"], T["text2"])
            self.live_site.setStyleSheet(f"color: {col};")
            self.live_mode.setText(cur["mode_text"].split(" · ")[0] + " " +
                                   (cur["s"]["container"] if cur["s"]["mode"] != "audio" else ""))
        while self.live_rows.count():
            it = self.live_rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        others = [j for j in self.jobs if j is not cur]
        others.sort(key=lambda j: {"waiting": 0, "failed": 1, "stopped": 1, "done": 2}.get(j["state"], 3))
        for j in others[:5]:
            row = card("LiveRow")
            h = hbox(row, (14, 10, 14, 10), 10)
            st = j["state"]
            if st == "done":
                ic = QLabel()
                ic.setPixmap(ui.svg_pixmap("check", T["green"], 12, 3))
                h.addWidget(ic)
            elif st in ("failed", "stopped"):
                ic = QLabel()
                ic.setPixmap(ui.svg_pixmap("alert", T["red"] if st == "failed" else T["amber"], 12, 2.4))
                h.addWidget(ic)
            else:
                h.addWidget(ui.Dot("muted"))
            t = ui.ElideLabel(j["title"])
            t.setFont(ui.f_mono(12) if j["title"].startswith("http") else ui.f_ui(13))
            t.setObjectName("Text2" if st != "waiting" else "Title")
            h.addWidget(t, 1)
            stx = {"waiting": (tr("等待"), "muted"), "done": (tr("完成"), "ok"), "failed": (tr("失敗"), "bad"),
                   "stopped": (tr("已停止"), "warn")}.get(st, ("", "muted"))
            h.addWidget(lab(stx[0], None, ui.f_ui(11), tone=stx[1]))
            self.live_rows.addWidget(row)

    def _update_live_progress(self, pct, speed, eta, stage):
        if pct is None:
            self.live_bar.setRange(0, 0)
        else:
            if self.live_bar.maximum() == 0:
                self.live_bar.setRange(0, 1000)
            self.live_bar.setValue(int(pct * 10))
            self.live_pct.setText(f"{pct:.1f}%")
        self.live_speed.setText(speed)
        self.live_eta.setText(f"ETA {eta}" if eta else "")
        self.live_stage.setText(stage)

    def start_queue(self):
        self.stop_all = False
        if self.proc is None:
            self._next_job()

    def stop_queue(self):
        self.stop_all = True
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.append_log(tr("[GUI] 停止下載…"))
            self._kill_proc()

    def stop_job(self, job):
        if job is self.cur and self.proc:
            job["cancel"] = True
            self.append_log(tr("[GUI] 停止目前項目…"))
            self._kill_proc()

    def _kill_proc(self):
        pid = self.proc.processId() if self.proc else 0
        if core.IS_WIN and pid:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, creationflags=0x08000000)
        elif self.proc:
            self.proc.kill()

    def remove_job(self, job):
        if job is self.cur:
            return
        self.jobs.remove(job)
        job["card"].setParent(None)
        job["card"].deleteLater()
        self._refresh_counts()
        self._refresh_live()

    def retry_job(self, job):
        job["errors"], job["tips"] = [], []
        job["auto_retries"] = 0
        job["card"].bar.setValue(0)
        self._set_state(job, "waiting")
        self.start_queue()

    def retry_failed(self):
        for j in self.jobs:
            if j["state"] in ("failed", "stopped"):
                j["errors"], j["tips"] = [], []
                j["auto_retries"] = 0
                j["card"].bar.setValue(0)
                j["state"] = "waiting"
                j["card"].apply_state()
        self._refresh_counts()
        self._refresh_live()
        self.start_queue()

    def clear_done(self):
        for j in [j for j in self.jobs if j["state"] == "done"]:
            self.remove_job(j)

    def _next_job(self):
        if self.stop_all:
            self._refresh_counts()
            return
        job = next((j for j in self.jobs if j["state"] == "waiting"), None)
        if job is None:
            self.proc = None
            self.cur = None
            self._refresh_counts()
            self._refresh_live()
            done = sum(j["state"] == "done" for j in self.jobs)
            fail = sum(j["state"] == "failed" for j in self.jobs)
            if done or fail:
                self.append_log(tr('[GUI] 佇列結束：完成 {done}，失敗 {fail}').format(done=done, fail=fail))
                self._notify(tr('下載佇列已結束：完成 {done} 個，失敗 {fail} 個。').format(done=done, fail=fail), warn=fail > 0)
            return
        exe = self._need_ytdlp()
        if not exe:
            self._refresh_counts()
            return
        try:
            os.makedirs(job["out_dir"], exist_ok=True)
        except OSError as e:
            job["errors"] = [tr('無法建立資料夾 {out_dir}：{e}').format(out_dir=job['out_dir'], e=e)]
            job["tips"] = [tr("請到「下載」頁確認存放資料夾的磁碟是否存在。")]
            self._set_state(job, "failed")
            QTimer.singleShot(0, self._next_job)
            return
        args = core.build_download_args(job["url"], job["s"], job["fmt"])
        self.cur = job
        self.buf = b""
        job["stage"] = 0
        job["errors"] = []
        job["tips"] = []
        job["tail"] = []
        job["cancel"] = False
        env_s = job["s"]
        if job["site"] == "bilibili" and job.get("cdn_idx"):
            env_s = dict(job["s"], bili_cdn=job["cdn_list"][job["cdn_idx"]])
        job["cut_errs"] = 0
        job["rotate"] = False
        p = QProcess(self)
        p.setProcessEnvironment(proc_env(env_s))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.setProgram(exe)
        p.setArguments(args)
        p.readyReadStandardOutput.connect(self._on_output)
        p.finished.connect(self._on_finished)
        p.errorOccurred.connect(self._on_proc_error)
        self.proc = p
        self.append_log("=" * 70)
        self.append_log(tr('[開始] {url}').format(url=job['url']))
        self.append_log(tr("[指令] yt-dlp ") + subprocess.list2cmdline(args))
        job["card"].set_progress(0.0, "", "", "")
        self._update_live_progress(0.0, "", "", "")
        self._set_state(job, "running")
        p.start()

    def _on_proc_error(self, err):
        if err == QProcess.ProcessError.FailedToStart and self.cur:
            self.append_log(tr("[GUI] 無法啟動 yt-dlp"))
            self.cur["errors"] = [tr("無法啟動 yt-dlp")]
            job = self.cur
            self.proc = None
            self.cur = None
            self._set_state(job, "failed")
            QTimer.singleShot(0, self._next_job)

    def _on_output(self):
        if not self.proc:
            return
        self.buf += self.proc.readAllStandardOutput().data()
        self.buf = self.buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        *lines, self.buf = self.buf.split(b"\n")
        for raw in lines:
            self._handle_line(ANSI_RE.sub("", decode_out(raw)).rstrip())

    def _handle_line(self, line: str):
        job = self.cur
        if not line or job is None or line.startswith("@@BILIFMT@@"):
            return
        if core.PROGRESS_TAG not in line:
            tail = job.setdefault("tail", [])
            tail.append(line)
            del tail[:-40]
            if job["site"] == "bilibili" and CUT_RE.search(line):
                job["cut_errs"] = job.get("cut_errs", 0) + 1
                if (job["cut_errs"] >= 5 and not job.get("rotate")
                        and job.get("cdn_idx", 0) < len(job.get("cdn_list", [])) - 1):
                    job["rotate"] = True
                    self.append_log(line)
                    self.append_log(tr("[GUI] B站 CDN 節點一直中途斷線，停止這次下載並自動換下一個節點…"))
                    self._kill_proc()
                    return
        if core.PROGRESS_TAG in line:
            job["aria_ctrl"] = None       # yt-dlp 自己回報進度（內建下載器或完成）時就不必讀控制檔
            pct, speed, eta, _fid = core.parse_progress(line)
            stage = tr('串流 {stage}').format(stage=job['stage']) if job["stage"] > 1 else ""
            if job["state"] == "post":
                job["state"] = "running"
                job["card"].apply_state()
            job["card"].set_progress(pct, speed, eta, stage)
            self._update_live_progress(pct, speed, eta, stage)
            self.footer_msg.setText(" · ".join(x for x in (f"{pct:.1f}%" if pct is not None else "", speed,
                                                             f"ETA {eta}" if eta else "") if x))
            return
        if line.startswith(core.TITLE_TAG):
            title = line[len(core.TITLE_TAG):]
            job["title"] = title
            job["card"].title.setText(title)
            self.live_title.setText(title)
            self.append_log(tr('[標題] {title}').format(title=title))
            return
        if line.startswith(core.FILE_TAG):
            job["file"] = line[len(core.FILE_TAG):]
            self.append_log(tr('[存檔] {file}').format(file=job['file']))
            return
        if line.startswith("[download] Destination:"):
            job["stage"] += 1
            if job.get("aria2"):
                dest = line.split(":", 1)[1].strip()
                job["aria_ctrl"] = [dest + ".part.aria2", dest + ".aria2"]
                job["aria_hist"] = []
                stage = tr('多線 ×{aria2_n}').format(aria2_n=job['aria2_n']) + (tr(' · 串流 {stage}').format(stage=job['stage']) if job["stage"] > 1 else "")
                job["card"].set_progress(None, "", "", stage)
                self._update_live_progress(None, "", "", stage)
        else:
            m = re.match(r"^\[(\w+)\]", line)
            if m and m.group(1) in POST_NAMES:
                job["post_text"] = POST_NAMES[m.group(1)] + "…"
                job["state"] = "post"
                job["card"].apply_state()
                job["card"].set_post(job["post_text"])
                self._update_live_progress(100.0, "", "", job["post_text"])
            elif line.strip() == "ERROR:":
                job["error_next"] = True      # yt-dlp 有時把錯誤內容印在下一行
                return
            elif line.startswith("ERROR:"):
                job["errors"].append(line)
            elif job.pop("error_next", False):
                line = "ERROR: " + line
                job["errors"].append(line)
        self.append_log(line)

    def _should_rotate(self, job, code) -> bool:
        """B站下載被節點掐斷（或 aria2c 失敗）時，換下一個 CDN 節點重試。"""
        if job["site"] != "bilibili" or self.stop_all or job.get("cancel") or code == 0:
            return False
        if job.get("cdn_idx", 0) >= len(job.get("cdn_list", [])) - 1:
            return False
        if job.get("rotate"):
            return True
        text = "\n".join(job.get("tail", []))
        return bool(CUT_RE.search(text) or "aria2c exited with code" in text)

    def _poll_aria2(self):
        """aria2c 不會即時回報進度給 yt-dlp，改讀它的 .aria2 控制檔。"""
        import time
        job = self.cur
        if not job or not job.get("aria_ctrl") or job["state"] != "running":
            return
        r = None
        for path in job["aria_ctrl"]:
            r = core.aria2_progress(path)
            if r:
                break
        if not r:
            return
        done, total = r
        now = time.monotonic()
        hist = job.setdefault("aria_hist", [])
        hist.append((now, done))
        while len(hist) > 2 and now - hist[0][0] > 5:     # 用最近 5 秒平均速度
            hist.pop(0)
        speed_bps = 0.0
        if len(hist) >= 2 and hist[-1][0] > hist[0][0]:
            speed_bps = max(0.0, (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0]))
        pct = done / total * 100
        speed = f"{core.human_size(speed_bps)}/s" if speed_bps else ""
        eta = ""
        if speed_bps > 0:
            sec = int((total - done) / speed_bps)
            eta = f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"
        stage = tr('多線 ×{aria2_n}').format(aria2_n=job['aria2_n']) + (tr(' · 串流 {stage}').format(stage=job['stage']) if job["stage"] > 1 else "")
        job["card"].set_progress(pct, speed, eta, stage)
        self._update_live_progress(pct, speed, eta, stage)
        self.footer_msg.setText(" · ".join(x for x in (f"{pct:.1f}%", speed, f"ETA {eta}" if eta else "",
                                                         f"aria2c ×{job['aria2_n']}") if x))

    def _on_finished(self, code, _status):
        if self.buf:
            self._handle_line(decode_out(self.buf))
            self.buf = b""
        job = self.cur
        self.proc = None
        self.cur = None
        if job is not None and self._should_rotate(job, code):
            job["cdn_idx"] += 1
            host = job["cdn_list"][job["cdn_idx"]]
            name = tr("原始網址（不修改）") if host == "off" else host
            self.append_log(tr('[GUI] 改用 B站 CDN 節點：{name}（第 {v}/{n} 個），已下載的部分會續傳').format(name=name, v=job['cdn_idx'] + 1, n=len(job['cdn_list'])))
            job["errors"], job["tips"] = [], []
            job["state"] = "waiting"
            job["card"].apply_state()
            self._refresh_counts()
            self._refresh_live()
            QTimer.singleShot(1500, self._next_job)
            return
        if job is not None:
            if self.stop_all or job.get("cancel"):
                job["state"] = "stopped"
                self._record_history(job)
            elif code == 0:
                job["state"] = "done"
                job["auto_retries"] = 0
                self._record_history(job)
            else:
                if not job["errors"]:
                    job["errors"] = [tr('yt-dlp 結束代碼 {code}').format(code=code)]
                tips = explain_error("\n".join(job["errors"] + job.get("tail", [])))
                max_retry = int(self.s.get("auto_retry_max", 3) or 0)
                will_retry = (self.s.get("auto_retry", True) and not job.get("cancel")
                              and job.get("auto_retries", 0) < max_retry)
                if will_retry:
                    job["auto_retries"] = job.get("auto_retries", 0) + 1
                    delay = min(120, 10 * (2 ** (job["auto_retries"] - 1)))
                    tips = tips + [tr('將在 {delay} 秒後自動重試（第 {auto_retries}/{max_retry} 次）。若不想等，可直接按「重試」。').format(delay=delay, auto_retries=job['auto_retries'], max_retry=max_retry)]
                    self.append_log(tr('[GUI] 下載失敗，{delay} 秒後自動重試第 {auto_retries} 次：{url}').format(delay=delay, auto_retries=job['auto_retries'], url=job['url']))
                    QTimer.singleShot(delay * 1000, lambda j=job: self._auto_retry_now(j))
                else:
                    self._record_history(job, state="failed", error="\n".join(job["errors"]))
                job["tips"] = tips
                for t in tips:
                    self.append_log(tr("提示 › ") + t)
                job["state"] = "failed"
            job["card"].apply_state()
        self._refresh_counts()
        self._refresh_live()
        QTimer.singleShot(0, self._next_job)

    def _auto_retry_now(self, job):
        """自動重試的延遲時間到了：如果使用者沒有手動處理過這個項目，就重新排入佇列。"""
        if job not in self.jobs or job["state"] != "failed":
            return
        job["errors"], job["tips"] = [], []
        job["card"].bar.setValue(0)
        self._set_state(job, "waiting")
        self.start_queue()

    # ================================================================== 紀錄
    def _classify(self, line: str) -> str:
        if line.startswith(("ERROR", "WARNING", tr("提示 ›"))):
            return "error"
        m = re.match(r"^\[(\w+)\]", line)
        if m:
            tag = m.group(1)
            if tag in POST_NAMES:
                return "post"
            if tag in (tr("開始"), tr("標題"), tr("存檔"), tr("指令"), "GUI", tr("工具"), tr("更新"), tr("解析")):
                return "gui"
            return "download"
        return "gui"

    def _fmt_html(self, line: str) -> str:
        c = ui.TERM
        esc = html.escape(line)
        if line.startswith("ERROR"):
            body = f'<span style="color:{c["red"]}">{esc}</span>'
        elif line.startswith("WARNING"):
            body = f'<span style="color:{c["amber"]}">{esc}</span>'
        elif line.startswith(tr("提示 ›")):
            body = f'<span style="color:{c["amber"]}">{esc}</span>'
        elif set(line) == {"="}:
            body = f'<span style="color:{c["sep"]}">{esc}</span>'
        else:
            m = re.match(r"^(\[[^\]]+\])(.*)$", line)
            if m:
                tag = m.group(1)[1:-1]
                rest = html.escape(m.group(2))
                if tag in (tr("開始"), tr("標題"), tr("解析"), "GUI", tr("工具"), tr("更新")):
                    col = c["cyan"]
                elif tag == tr("存檔"):
                    col = c["green"]
                elif tag == tr("指令"):
                    return f'<span style="white-space:pre;color:{c["dim"]}">{esc}</span>'
                elif tag in POST_NAMES:
                    col = c["purple"]
                elif tag.lower().startswith("bili"):
                    col = c["pink"]
                else:
                    col = c["blue"]
                body = (f'<span style="color:{col}">{html.escape(m.group(1))}</span>'
                        f'<span style="color:{c["text"]}">{rest}</span>')
            else:
                body = f'<span style="color:{c["text"]}">{esc}</span>'
        return f'<span style="white-space:pre">{body}</span>'

    def append_log(self, text: str):
        if not text:
            return
        for line in text.splitlines():
            if not line.strip() or core.PROGRESS_TAG in line:
                continue
            cat = self._classify(line)
            h = self._fmt_html(line)
            self.log_entries.append((cat, h))
            if len(self.log_entries) > 20000:
                del self.log_entries[:2000]
            if self.log_filter == "all" or self.log_filter == cat:
                self._append_html(h)
            if hasattr(self, "footer_msg"):
                self.footer_msg.setText(line)

    def _append_html(self, h):
        sb = self.log.verticalScrollBar()
        keep = sb.value()
        self.log.appendHtml(h)
        if self.ck_autoscroll.isChecked():
            sb.setValue(sb.maximum())
        else:
            sb.setValue(keep)

    def set_log_filter(self, key):
        self.log_filter = key
        self.log.clear()
        self.log.setUpdatesEnabled(False)
        for cat, h in self.log_entries:
            if key == "all" or key == cat:
                self.log.appendHtml(h)
        self.log.setUpdatesEnabled(True)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def clear_log(self):
        self.log_entries.clear()
        self.log.clear()

    def export_log(self):
        f, _ = QFileDialog.getSaveFileName(self, tr("匯出紀錄"), str(core.BASE_DIR / "session.log"), "Log (*.log *.txt)")
        if f:
            Path(f).write_text(self.log.toPlainText(), encoding="utf-8")
            self.append_log(tr('[GUI] 紀錄已匯出：{f}').format(f=f))

    # ================================================================== B站登入
    def start_qr(self):
        self.qr_timer.stop()
        p = self.cookie_panels["bilibili"]
        p.qr_msg.setText(tr("正在取得 QR Code…"))
        self.bili = core.BilibiliLogin()
        run_bg(lambda _p: self.bili.generate(), self._show_qr,
               lambda e: p.qr_msg.setText(tr('取得 QR Code 失敗：{e}').format(e=e)))

    def _show_qr(self, result):
        url, key = result
        self.qr_key = key
        p = self.cookie_panels["bilibili"]
        if qrcode is None:
            self.qr.set_message(tr("缺少 qrcode 套件"))
            p.qr_msg.setText(tr("找不到 qrcode 套件，請執行「pip install -r requirements.txt」。"))
            return
        q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(url)
        q.make(fit=True)
        self.qr.set_matrix(q.get_matrix())
        p.qr_msg.setText(tr("請用 Bilibili App 掃描 QR Code（約 3 分鐘內有效）。"))
        self.qr_timer.start()

    def _poll_qr(self):
        if not self.qr_key or self.qr_polling:
            return
        self.qr_polling = True
        key = self.qr_key
        run_bg(lambda _p: self.bili.poll(key), self._poll_result, self._poll_fail)

    def _poll_fail(self, e):
        self.qr_polling = False
        self.cookie_panels["bilibili"].qr_msg.setText(tr('查詢失敗（會自動重試）：{e}').format(e=e))

    def _poll_result(self, r):
        self.qr_polling = False
        code, msg = r
        p = self.cookie_panels["bilibili"]
        p.qr_msg.setText(msg)
        if code == 0:
            self.qr_timer.stop()
            self.qr_key = None
            self.qr.set_message(tr("登入成功"))
            p.seg.set("login")
            p._mode_changed("login")
            self.refresh_bili_status()
        elif code == 86038:
            self.qr_timer.stop()
            self.qr_key = None
            self.qr.set_message(tr("QR Code 已過期"))

    def refresh_bili_status(self):
        if not core.BILI_COOKIE_FILE.is_file():
            self.bili_status.setText(tr("未登入 · 最高 720P"))
            set_tone(self.bili_status, "warn")
            self._cookie_changed()
            return
        self.bili_status.setText(tr("檢查中…"))
        set_tone(self.bili_status, None)
        run_bg(lambda _p: core.BilibiliLogin().status(), self._show_bili_status,
               lambda e: (self.bili_status.setText(tr("無法確認狀態")), set_tone(self.bili_status, "warn"),
                          self.append_log(tr('[GUI] B站狀態查詢失敗：{e}').format(e=e))))

    def _show_bili_status(self, st):
        if st.get("login"):
            vip = st.get("vip_label") or (tr("大會員") if st.get("vip") else tr("一般會員"))
            self.bili_status.setText(tr('已登入 · {get} · {vip}').format(get=st.get('uname'), vip=vip))
            set_tone(self.bili_status, "ok")
        else:
            self.bili_status.setText(tr("Cookie 已失效，請重新掃碼"))
            set_tone(self.bili_status, "bad")
        self._cookie_changed()

    def bili_logout(self):
        if QMessageBox.question(self, tr("登出"), tr("確定要刪除本機的 Bilibili 登入 Cookie？")) \
                != QMessageBox.StandardButton.Yes:
            return
        core.BilibiliLogin().logout()
        self.qr.set_message(tr("已登出"))
        self.refresh_bili_status()

    # ================================================================== 工具
    def _need_ytdlp(self) -> str | None:
        exe = core.find_ytdlp(self.s)
        if exe:
            return exe
        r = QMessageBox.question(self, APP_TITLE, tr("找不到 yt-dlp.exe，要現在從 GitHub 下載嗎？"))
        if r == QMessageBox.StandardButton.Yes:
            self.go("tools")
            self.install_tool("yt-dlp")
        return None

    def _refresh_footer(self):
        ch = self.channel_seg.value() or "nightly"
        self.footer_right.setText(f"yt-dlp {ch} · v{core.APP_VERSION}")

    def _set_tool_ui(self, key, ok, path, status, warn=False):
        c = self.tool_cards[key]
        c["dot"].set_role("green" if ok else ("amber" if warn else "red"))
        c["status"].setText(status)
        set_tone(c["status"], "accent" if (ok and key == "yt-dlp") else "ok" if ok else "warn" if warn else "bad")
        c["path"].setText(path or "")
        d, val = self.sys_rows[{"yt-dlp": "ytdlp"}.get(key, key)]
        d.set_role("green" if ok else ("amber" if warn else "red"))
        val.setText(status if key == "yt-dlp" else ("OK" if ok else tr("未安裝")))

    def _refresh_tools(self):
        self._refresh_footer()
        ff = core.find_ffmpeg()
        deno = core.find_deno()
        self._set_tool_ui("ffmpeg", bool(ff), ff, tr("已安裝") if ff else tr("未安裝"))
        self._set_tool_ui("deno", bool(deno), deno, tr("已安裝") if deno else tr("未安裝"), warn=not deno)
        ar = core.find_aria2()
        self._set_tool_ui("aria2", bool(ar), ar, tr("已安裝") if ar else tr("未安裝"), warn=not ar)
        if hasattr(self, "ck_multi"):
            self._update_multi_ui()
        exe = core.find_ytdlp(self.s)
        if not exe:
            self._set_tool_ui("yt-dlp", False, "", tr("找不到"))
            self.about_core.setText(tr("核心：找不到 yt-dlp"))
            return
        self._set_tool_ui("yt-dlp", True, exe, tr("讀取中…"))
        p = QProcess(self)
        p.setProcessEnvironment(proc_env())

        def done(*_):
            ver = decode_out(p.readAllStandardOutput().data()).strip() or "?"
            self.ytdlp_version = ver
            self._set_tool_ui("yt-dlp", True, exe, ver)
            self.about_core.setText(tr('核心：yt-dlp {ver}').format(ver=ver))
        p.finished.connect(done)
        p.start(exe, ["--version"])

    def pick_ytdlp(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("選擇 yt-dlp.exe"), str(core.BASE_DIR),
                                           tr("yt-dlp (yt-dlp*.exe);;所有檔案 (*)"))
        if f:
            self.s["ytdlp_path"] = f
            self.save()
            self._refresh_tools()

    def update_ytdlp(self):
        exe = core.find_ytdlp(self.s)
        if not exe:
            self.install_tool("yt-dlp")
            return
        if self.proc:
            QMessageBox.information(self, APP_TITLE, tr("下載進行中，請等佇列結束後再更新。"))
            return
        ch = self.channel_seg.value() or "nightly"
        self.save()
        self.append_log(tr('[更新] yt-dlp --update-to {ch}').format(ch=ch))
        c = self.tool_cards["yt-dlp"]
        c["prog"].show()
        c["bar"].setRange(0, 0)
        c["plab"].setText(tr('更新到 {ch}…').format(ch=ch))
        p = QProcess(self)
        p.setProcessEnvironment(proc_env())
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(
            lambda: self.append_log(decode_out(p.readAllStandardOutput().data()).rstrip()))

        def fin(*_):
            c["prog"].hide()
            self.append_log(tr("[更新] 結束"))
            self._refresh_tools()
        p.finished.connect(fin)
        p.start(exe, ["--update-to", ch])

    def install_tool(self, name: str):
        if self.tool_busy:
            QMessageBox.information(self, APP_TITLE, tr("另一個工具正在下載中，請稍候。"))
            return
        fn = {"ffmpeg": core.install_ffmpeg, "deno": core.install_deno, "yt-dlp": core.install_ytdlp,
              "aria2": core.install_aria2}[name]
        c = self.tool_cards[name]
        self.tool_busy = True
        c["prog"].show()
        c["bar"].setRange(0, 0)
        c["plab"].setText(tr("連線中…"))
        c["btn"].setEnabled(False)
        self.append_log(tr('[工具] 開始下載 {name}…').format(name=name))

        def prog(done, total):
            if total:
                c["bar"].setRange(0, 1000)
                c["bar"].setValue(int(done / total * 1000))
                c["plab"].setText(tr('下載中  {human_size} / {human_size2}').format(human_size=core.human_size(done), human_size2=core.human_size(total)))

        def finish():
            self.tool_busy = False
            c["prog"].hide()
            c["btn"].setEnabled(True)

        def ok(_r):
            finish()
            self.append_log(tr('[工具] {name} 安裝完成').format(name=name))
            self._refresh_tools()

        def fail(e):
            finish()
            self.append_log(tr('[工具] {name} 下載失敗：{e}').format(name=name, e=e))
            QMessageBox.warning(self, APP_TITLE, tr('{name} 下載失敗：\n{e}').format(name=name, e=e))

        run_bg(lambda p: fn(progress=p), ok, fail, prog)

    def show_command(self):
        self.collect()
        url = (self.urls() or ["https://www.youtube.com/watch?v=XXXX"])[0]
        args = core.build_download_args(url, self.s, self._custom_format_for(url))
        dlg = QMessageBox(self)
        dlg.setWindowTitle(tr("下載指令"))
        dlg.setText(tr("目前設定會執行的指令（複製到 BAT 使用時，% 要改成 %%）："))
        dlg.setDetailedText("yt-dlp " + subprocess.list2cmdline(args))
        dlg.exec()

    def pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, tr("選擇資料夾"), edit.text() or str(core.BASE_DIR))
        if d:
            edit.setText(os.path.normpath(d))
            self.save()

    # ================================================================== 系統匣
    def _build_tray(self):
        self.tray = QSystemTrayIcon(ui.app_icon(), self)
        self.tray.setToolTip(APP_TITLE)
        menu = QMenu()
        self._tray_menu = menu   # 保留參考，避免被 GC 回收造成選單消失 / 當掉
        act_show = menu.addAction(tr("顯示主視窗"))
        act_show.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        act_quit = menu.addAction(tr("結束程式"))
        act_quit.triggered.connect(self._quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._force_quit = True
        self.close()

    def _notify(self, text: str, warn: bool = False):
        if self.s.get("notify_on_finish", True) and hasattr(self, "tray") and self.tray.isVisible():
            icon = QSystemTrayIcon.MessageIcon.Warning if warn else QSystemTrayIcon.MessageIcon.Information
            self.tray.showMessage(APP_TITLE, text, icon, 5000)

    # ================================================================== 自動檢查更新
    def _check_updates_async(self):
        if not self.s.get("check_updates_on_start", True):
            return

        def work(_prog):
            info = {}
            exe = core.find_ytdlp(self.s)
            if exe:
                cur = self.ytdlp_version or core.tool_version(exe)
                r = core.check_update("yt-dlp", cur)
                if r:
                    info["yt-dlp"] = r
            ar = core.find_aria2()
            if ar:
                cur = core.tool_version(ar)
                r = core.check_update("aria2c", cur)
                if r:
                    info["aria2c"] = r
            return info

        def done(info):
            msgs = [tr('{name} 有新版本 {latest} 可更新').format(name=name, latest=r['latest']) for name, r in (info or {}).items() if r.get("has_update")]
            if msgs:
                text = tr("；").join(msgs) + tr("。可到「工具 / 設定」更新。")
                self.append_log(tr('[GUI] 檢查更新：{text}').format(text=text))
                self._notify(text)

        run_bg(work, done, lambda e: None)

    # ================================================================== 下載歷史
    def _record_history(self, job, state=None, error=""):
        core.record_history(job.get("title") or job.get("url", ""), job.get("url", ""),
                            job.get("site", ""), job.get("s", {}).get("mode", ""),
                            state or job["state"], job.get("file", ""), error)

    def _build_history_page(self):
        body = QWidget()
        v = vbox(body, (36, 28, 36, 36), 18)
        right = hbox(sp=8)
        b_refresh = QPushButton(tr("重新整理"))
        b_refresh.clicked.connect(self._load_history)
        b_clear = QPushButton(tr("清除紀錄"))
        b_clear.setProperty("variant", "danger")
        b_clear.clicked.connect(self._clear_history)
        right.addWidget(b_refresh)
        right.addWidget(b_clear)
        v.addLayout(self._page_header("03 // HISTORY", tr("下載歷史"), right,
                                      tr("完成 / 失敗 / 已停止的下載都會留下紀錄，重開程式後仍可查詢。")))

        fr = hbox(sp=10)
        self.hist_search = QLineEdit()
        self.hist_search.setPlaceholderText(tr("搜尋標題或網址…"))
        self.hist_search.returnPressed.connect(self._load_history)
        fr.addWidget(self.hist_search, 1)
        self.hist_filter = ui.Segmented([("all", tr("全部")), ("done", tr("成功")), ("failed", tr("失敗")), ("stopped", tr("已停止"))])
        self.hist_filter.changed.connect(lambda *_: self._load_history())
        fr.addWidget(self.hist_filter)
        b_search = QPushButton(tr("搜尋"))
        b_search.clicked.connect(self._load_history)
        fr.addWidget(b_search)
        v.addLayout(fr)

        self.hist_list = vbox(sp=10)
        v.addLayout(self.hist_list)
        self.hist_empty = lab(tr("還沒有任何紀錄。"), "Muted", ui.f_ui(14), wrap=True)
        v.addWidget(self.hist_empty)
        v.addStretch(1)
        self.hist_filter.set("all")
        QTimer.singleShot(200, self._load_history)
        return page_scroll(body)

    def _load_history(self):
        while self.hist_list.count():
            it = self.hist_list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        kw = self.hist_search.text().strip()
        state = self.hist_filter.value() or "all"
        rows = core.query_history(kw, state, 300)
        self.hist_empty.setVisible(not rows)
        for row in rows:
            self.hist_list.addWidget(self._history_row(*row))

    def _history_row(self, item_id, ts, title, url, site, mode, state, file, error):
        c = card("Inset")
        h = hbox(c, (16, 12, 16, 12), 12)
        nm, role = {"done": ("check", "green"), "failed": ("alert", "red"),
                    "stopped": ("stop", "amber")}.get(state, ("list", "muted"))
        ic = QLabel()
        ic.setFixedSize(16, 16)
        ic.setPixmap(ui.svg_pixmap(nm, T[role], 16, 2.4))
        h.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        mid = vbox(sp=4)
        t = ui.ElideLabel(title or url)
        t.setFont(ui.f_ui(13))
        mid.addWidget(t)
        try:
            when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            when = ""
        sub = ui.ElideLabel(" · ".join(x for x in (when, core.SITE_NAMES.get(site, site), file or url) if x),
                            mode=Qt.TextElideMode.ElideMiddle)
        sub.setObjectName("Muted")
        sub.setFont(ui.f_mono(11))
        mid.addWidget(sub)
        h.addLayout(mid, 1)
        if state == "done" and file:
            b_open = QPushButton(tr("開啟位置"))
            b_open.clicked.connect(lambda: reveal_file(file, os.path.dirname(file) or str(core.BASE_DIR)))
            h.addWidget(b_open)
        b_del = QPushButton("×")
        b_del.setProperty("variant", "small")
        b_del.setToolTip(tr("刪除這筆紀錄"))
        b_del.clicked.connect(lambda: self._delete_history(item_id, c))
        h.addWidget(b_del)
        return c

    def _delete_history(self, item_id, widget):
        core.delete_history_item(item_id)
        widget.setParent(None)
        widget.deleteLater()

    def _clear_history(self):
        r = QMessageBox.question(self, APP_TITLE, tr("確定要清除所有歷史紀錄？這個動作無法復原。"))
        if r == QMessageBox.StandardButton.Yes:
            core.clear_history()
            self._load_history()

    # ================================================================== 設定備份
    def export_settings_file(self):
        f, _ = QFileDialog.getSaveFileName(self, tr("匯出設定"), str(core.BASE_DIR / "ytdl_settings.zip"), "ZIP (*.zip)")
        if not f:
            return
        try:
            self.collect()
            self.save()
            core.export_settings(f)
            QMessageBox.information(self, APP_TITLE, tr('已匯出到：\n{f}').format(f=f))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_TITLE, tr('匯出失敗：\n{e}').format(e=e))

    def import_settings_file(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("匯入設定"), str(core.BASE_DIR), "ZIP (*.zip)")
        if not f:
            return
        r = QMessageBox.question(self, APP_TITLE, tr("匯入會覆蓋目前的設定與 B站登入資料，確定要繼續？"))
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            new_s = core.import_settings(f)
            self.s.clear()
            self.s.update(new_s)
            self._load_widgets()
            for p in self.cookie_panels.values():
                p.reload()
            self._cookie_changed()
            self.refresh_bili_status()
            self.apply_theme()
            QMessageBox.information(self, APP_TITLE, tr("匯入完成，設定已套用。"))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_TITLE, tr('匯入失敗：\n{e}').format(e=e))

    # ================================================================== 關閉
    def closeEvent(self, ev):  # noqa: N802
        if (not self._force_quit and self.s.get("minimize_to_tray", True)
                and hasattr(self, "tray") and self.tray.isVisible()):
            ev.ignore()
            self.hide()
            self._notify(tr("已縮到系統匣，雙擊圖示可還原視窗；從選單可以結束程式。"))
            return
        if self.proc is not None:
            r = QMessageBox.question(self, APP_TITLE, tr("還有下載進行中，確定要關閉？（未完成的檔案下次可續傳）"))
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.stop_all = True
            self._kill_proc()
        self.s["window_geometry"] = self.saveGeometry().data().hex()
        self.save()
        if hasattr(self, "tray"):
            self.tray.hide()
        ev.accept()
        self.app.quit()   # setQuitOnLastWindowClosed(False)：縮到系統匣時不能讓關視窗=結束程式，所以真正關閉要自己呼叫


def install_qt_translation(app) -> bool:
    """載入 Qt 內建翻譯（跟著介面語言）：輸入框右鍵選單（復原 / 剪下 / 貼上…）、對話框的「是 / 否 / 取消」等。
    英文不用載入。依序找：Qt 自己回報的翻譯資料夾、PySide6 套件裡的 translations、PyInstaller 解壓縮的暫存資料夾。"""
    name = i18n.qt_locale_name()
    if not name:
        return False
    from PySide6.QtCore import QLibraryInfo, QTranslator
    dirs = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    try:
        import PySide6
        dirs.append(str(Path(PySide6.__file__).resolve().parent / "translations"))
    except Exception:  # noqa: BLE001
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(str(Path(meipass) / "PySide6" / "translations"))
    for d in dirs:
        translator = QTranslator(app)          # parent = app：跟著 app 活著，不會被回收
        if d and translator.load(f"qtbase_{name}", d):
            app.installTranslator(translator)
            return True
    return False


def main():
    if core.IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ytdl.gui.cyberpunk")
        except Exception:  # noqa: BLE001
            pass

    def excepthook(etype, value, tb):
        import traceback
        txt = "".join(traceback.format_exception(etype, value, tb))
        try:
            with open(core.BASE_DIR / "error.log", "a", encoding="utf-8") as f:
                f.write(f"[v{core.APP_VERSION}]\n{txt}\n")
        except OSError:
            pass
        if QApplication.instance():
            QMessageBox.critical(None, APP_TITLE, tr('程式發生錯誤（已記錄到 error.log）：\n\n{value}').format(value=value))
    sys.excepthook = excepthook

    app = QApplication(sys.argv)
    app.setApplicationName(core.APP_NAME)
    app.setApplicationVersion(core.APP_VERSION)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)   # 關閉視窗時可能只是縮到系統匣，不能讓程式就這樣結束
    install_qt_translation(app)            # 右鍵選單、對話框按鈕跟著介面語言
    ui.load_fonts()
    app.setWindowIcon(ui.app_icon())      # 視窗左上角與工作列圖示（程式內直接繪製）
    app.setFont(ui.f_ui(14))
    w = MainWindow(app)
    w.setWindowIcon(app.windowIcon())
    w.show()
    w.append_log(tr('[GUI] {APP_NAME} {APP_EDITION} v{APP_VERSION} 啟動').format(APP_NAME=core.APP_NAME, APP_EDITION=core.APP_EDITION, APP_VERSION=core.APP_VERSION))
    if not core.find_ffmpeg():
        QTimer.singleShot(800, lambda: w.append_log(
            tr("提示 › 尚未安裝 ffmpeg：合併影音、轉 MP3/FLAC 需要它，請到「工具 / 設定」一鍵下載。")))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
