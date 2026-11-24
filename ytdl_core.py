# -*- coding: utf-8 -*-
"""
YTDL GUI 核心邏輯（不依賴 Qt，可單獨測試）
- 設定檔讀寫
- 組合 yt-dlp 指令
- 解析 yt-dlp -J 的格式清單
- Bilibili QR Code 登入（產生 Netscape cookies.txt）
- 下載 ffmpeg / deno 工具
"""
from __future__ import annotations

import copy
import http.cookiejar
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from i18n import tr

# ---------------------------------------------------------------- 版本
APP_NAME = "YT-DLP Downloader"
APP_EDITION = "Cyberpunk Edition"
APP_VERSION = "2.6.3"

# ---------------------------------------------------------------- 路徑
if getattr(sys, "frozen", False):          # PyInstaller 打包後
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

TOOLS_DIR = BASE_DIR / "tools"
COOKIES_DIR = BASE_DIR / "cookies"
SETTINGS_FILE = BASE_DIR / "settings.json"
BILI_COOKIE_FILE = COOKIES_DIR / "bilibili_login.txt"
PLUGIN_DIR = BASE_DIR / "yt_dlp_plugins"
FONTS_DIR = BASE_DIR / "fonts"
CACHE_DIR = BASE_DIR / "cache"
HISTORY_DB = BASE_DIR / "history.db"

IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

DEFAULT_TEMPLATE = "%(upload_date)s_%(title)s [%(id)s].%(ext)s"

# ---------------------------------------------------------------- 選項
VIDEO_QUALITIES = [            # (顯示文字, 值)
    (tr("最佳畫質"), "best"),
    ("4K (2160p)", "2160"),
    ("2K (1440p)", "1440"),
    ("1080p", "1080"),
    ("720p", "720"),
    ("480p", "480"),
    ("360p", "360"),
]
CODEC_PREFS = [
    (tr("自動（畫質優先）"), "auto"),
    (tr("H.264 + AAC（相容性最好）"), "h264"),
    ("AV1", "av01"),
    ("VP9", "vp9"),
    ("HEVC / H.265", "h265"),
]
CONTAINERS = ["mkv", "mp4", "webm"]
# webm 只能放 VP8/VP9/AV1 + Vorbis/Opus；來源是 H.264/AAC（X、部分 B站）時改存 mkv，避免轉換失敗
MERGE_FMT = {"webm": "webm/mkv"}          # 合併影音：依序挑第一個相容的容器
REMUX_FMT = {"webm": "webm>webm/mkv"}     # 轉換容器：來源是 webm 才存 webm，其他存 mkv
AUDIO_FORMATS = [
    ("MP3", "mp3"),
    (tr("FLAC（無損容器）"), "flac"),
    ("M4A / AAC", "m4a"),
    ("Opus", "opus"),
    ("WAV", "wav"),
    (tr("原始音訊（不轉檔）"), "original"),
]
VIDEO_AUDIO_PREFS = [          # 「影片 + 音訊」模式選哪條音軌（不轉檔，直接挑網站提供的）
    (tr("最佳音質（自動）"), "best"),
    (tr("最高位元率優先"), "abr"),
    (tr("AAC 優先（mp4 相容性最好）"), "aac"),
    (tr("Opus 優先（YouTube 原始最高音質）"), "opus"),
    (tr("FLAC / Hi-Res 優先（B站大會員）"), "flac"),
    (tr("省空間（約 128 kbps）"), "small"),
]
VIDEO_AUDIO_SORT = {
    "best": [], "abr": ["abr"], "aac": ["acodec:aac"], "opus": ["acodec:opus"],
    "flac": ["acodec:flac"], "small": ["abr~128"],
}
AUDIO_QUALITIES = [            # 給 mp3 / m4a / opus 使用
    (tr("最佳（VBR 0）"), "0"),
    ("320 kbps", "320K"),
    ("256 kbps", "256K"),
    ("192 kbps", "192K"),
    ("128 kbps", "128K"),
]
BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "whale"]
SITES = ("youtube", "bilibili", "other")
SITE_NAMES = {"youtube": "YouTube", "bilibili": "Bilibili", "other": tr("其他網站")}


DEFAULT_SETTINGS = {
    "video_dir": str(BASE_DIR / "Download"),
    "audio_dir": str(BASE_DIR / "audio"),
    "template": DEFAULT_TEMPLATE,
    "mode": "video",               # video / audio / video_only
    "video_quality": "best",
    "codec_pref": "auto",
    "container": "mkv",
    "audio_format": "mp3",
    "audio_quality": "0",
    "video_audio_pref": "best",
    "embed_thumbnail": True,
    "write_thumbnail": False,
    "embed_metadata": True,
    "embed_chapters": True,
    "subtitles": False,
    "sub_langs": "zh-Hant,zh-TW,zh-Hans,zh.*,en",
    "auto_subs": False,
    "playlist": False,
    "sponsorblock": False,
    "concurrent_fragments": 4,
    "bili_cdn": "auto",
    "multi_conn": False,           # 多線下載（aria2c）
    "aria2_conns": 16,
    "multi_sites": {"youtube": True, "bilibili": True, "other": True},            # auto / off / 指定主機（給 BiliCDNFix 外掛）
    "limit_rate": "",
    "update_channel": "nightly",
    "ytdlp_path": "",
    "cookies": {
        "youtube": {"mode": "none", "browser": "firefox", "profile": "", "file": ""},
        "bilibili": {"mode": "login", "browser": "firefox", "profile": "", "file": ""},
        "other": {"mode": "none", "browser": "firefox", "profile": "", "file": ""},
    },
    "window_geometry": "",
    "theme": "dark",               # dark / light / system
    "glow": True,
    "grid": True,
    "advanced_open": False,
    "language": "system",          # system（跟隨 Windows）/ zh_TW / zh_CN / en / ja；改了要重開程式
    "minimize_to_tray": True,      # 關閉視窗時縮到系統匣，而不是結束程式
    "notify_on_finish": True,      # 佇列結束時顯示系統通知
    "check_updates_on_start": True,  # 啟動時背景檢查 yt-dlp / aria2c 是否有新版
    "auto_retry": True,            # 失敗項目自動重試（漸長間隔）
    "auto_retry_max": 3,
}


def _merge(defaults: dict, data: dict) -> dict:
    out = copy.deepcopy(defaults)
    for k, v in (data or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return _merge(DEFAULT_SETTINGS, json.load(f))
    except Exception:
        return copy.deepcopy(DEFAULT_SETTINGS)


def save_settings(s: dict) -> None:
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# ---------------------------------------------------------------- 設定備份
def export_settings(dest) -> Path:
    """匯出 settings.json（+ B站登入 Cookie）成一個 zip，方便搬到另一台電腦。"""
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        if SETTINGS_FILE.is_file():
            z.write(SETTINGS_FILE, "settings.json")
        if BILI_COOKIE_FILE.is_file():
            z.write(BILI_COOKIE_FILE, "cookies/bilibili_login.txt")
    return dest


def import_settings(src) -> dict:
    """匯入設定 zip：寫回 settings.json / cookie，回傳合併後的設定。"""
    src = Path(src)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if "settings.json" in names:
            data = json.loads(z.read("settings.json").decode("utf-8"))
            save_settings(_merge(DEFAULT_SETTINGS, data))
        if "cookies/bilibili_login.txt" in names:
            BILI_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            BILI_COOKIE_FILE.write_bytes(z.read("cookies/bilibili_login.txt"))
    return load_settings()


# ---------------------------------------------------------------- 下載歷史（SQLite）
def _history_conn() -> sqlite3.Connection:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORY_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, title TEXT, url TEXT, "
        "site TEXT, mode TEXT, state TEXT, file TEXT, error TEXT)"
    )
    return conn


def record_history(title: str, url: str, site: str, mode: str, state: str,
                    file: str = "", error: str = "") -> None:
    """寫一筆下載紀錄（完成 / 失敗 / 已停止）。失敗不影響下載本身，所以吞掉例外。"""
    try:
        conn = _history_conn()
        with conn:
            conn.execute(
                "INSERT INTO history (ts, title, url, site, mode, state, file, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), title or url, url, site, mode, state, file, (error or "")[:2000]),
            )
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def query_history(keyword: str = "", state: str = "", limit: int = 300) -> list[tuple]:
    """回傳 [(id, ts, title, url, site, mode, state, file, error), ...]，最新的在前面。"""
    try:
        conn = _history_conn()
        sql = "SELECT id, ts, title, url, site, mode, state, file, error FROM history WHERE 1=1"
        args: list = []
        if keyword:
            sql += " AND (title LIKE ? OR url LIKE ?)"
            args += [f"%{keyword}%", f"%{keyword}%"]
        if state and state != "all":
            sql += " AND state = ?"
            args.append(state)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows
    except Exception:  # noqa: BLE001
        return []


def delete_history_item(item_id: int) -> None:
    try:
        conn = _history_conn()
        with conn:
            conn.execute("DELETE FROM history WHERE id = ?", (item_id,))
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def clear_history() -> None:
    try:
        conn = _history_conn()
        with conn:
            conn.execute("DELETE FROM history")
        conn.close()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 工具位置
def find_ytdlp(s: dict | None = None) -> str | None:
    if s and s.get("ytdlp_path") and Path(s["ytdlp_path"]).is_file():
        return s["ytdlp_path"]
    for p in (BASE_DIR / f"yt-dlp{EXE}", TOOLS_DIR / f"yt-dlp{EXE}", BASE_DIR.parent / f"yt-dlp{EXE}"):
        if p.is_file():
            return str(p)
    return shutil.which("yt-dlp")


def find_ffmpeg() -> str | None:
    for d in (TOOLS_DIR, BASE_DIR):
        if (d / f"ffmpeg{EXE}").is_file():
            return str(d / f"ffmpeg{EXE}")
    return shutil.which("ffmpeg")


def find_aria2() -> str | None:
    for d in (TOOLS_DIR, BASE_DIR):
        if (d / f"aria2c{EXE}").is_file():
            return str(d / f"aria2c{EXE}")
    return shutil.which("aria2c")


def uses_aria2(url: str, s: dict) -> bool:
    """這個網址要不要用 aria2c 多線下載。"""
    if not s.get("multi_conn") or not find_aria2():
        return False
    return bool((s.get("multi_sites") or {}).get(detect_site(url), True))


def find_deno() -> str | None:
    for d in (TOOLS_DIR, BASE_DIR):
        if (d / f"deno{EXE}").is_file():
            return str(d / f"deno{EXE}")
    return shutil.which("deno")


def normalize_url(url: str) -> str:
    """修正 yt-dlp 認不得的網址寫法。
    例：bilibili.com/zh-Hant/video/BV… → bilibili.com/video/BV…（並去掉分享追蹤參數，保留分P）"""
    url = url.strip()
    pr = urllib.parse.urlparse(url)
    host = pr.netloc.lower()
    if host.endswith("bilibili.com"):
        path = re.sub(r"^/zh-(?:Hant|Hans|TW|HK|CN)(?=/)", "", pr.path, flags=re.I)
        keep = {"p", "t", "start_progress"}
        q = [(k, v) for k, v in urllib.parse.parse_qsl(pr.query) if k in keep]
        return urllib.parse.urlunparse(pr._replace(path=path, query=urllib.parse.urlencode(q)))
    return url


BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"/video/av(\d+)", re.I)


def bili_bvid(url: str) -> str | None:
    """B站網址裡的影片編號：BV 號，或舊式 av 號（回傳 "av12345"）；沒有就 None。"""
    if detect_site(url) != "bilibili":
        return None
    m = BV_RE.search(url)
    if m:
        return m.group(1)
    m = AV_RE.search(url)
    return f"av{m.group(1)}" if m else None


def bili_part_of(url: str) -> int | None:
    """網址指定的分 P（?p=N），沒有就 None。"""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(q.get("p", [""])[0])
    except ValueError:
        return None


def bili_part_url(bvid: str, page: int) -> str:
    return f"https://www.bilibili.com/video/{bvid}?p={page}"


def bili_pagelist(bvid: str) -> list[dict]:
    """取得 B站「視頻選集」（分 P）清單：[{page, part, duration, cid}]，只有一集時長度為 1。"""
    if bvid.lower().startswith("av"):
        url = f"https://api.bilibili.com/x/player/pagelist?aid={bvid[2:]}&jsonp=jsonp"
    else:
        url = f"https://api.bilibili.com/x/player/pagelist?bvid={urllib.parse.quote(bvid)}&jsonp=jsonp"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bvid}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    if d.get("code") != 0:
        raise RuntimeError(d.get("message") or f"code {d.get('code')}")
    out = []
    for it in d.get("data") or []:
        if isinstance(it, dict) and it.get("page"):
            out.append({"page": int(it["page"]), "part": (it.get("part") or "").strip(),
                        "duration": it.get("duration") or 0, "cid": it.get("cid")})
    out.sort(key=lambda x: x["page"])
    return out


def parse_ranges(text: str, max_n: int) -> list[int]:
    """「1-5, 8, 10-12」→ [1,2,3,4,5,8,10,11,12]；也接受全形符號、「~」與空白分隔。"""
    text = text.translate(str.maketrans("，、～—－　", ",,~--,")).replace("~", "-")
    got = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)?-(\d+)?", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else max_n
            if a > b:
                a, b = b, a
            got.update(range(max(1, a), min(max_n, b) + 1))
        elif part.isdigit() and 1 <= int(part) <= max_n:
            got.add(int(part))
    return sorted(got)


def safe_folder(name: str, limit: int = 80) -> str:
    """當資料夾名稱用：去掉 Windows 不允許的字元。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip().rstrip(". ")
    return (name[:limit].rstrip(". ") or "未命名")


def strip_part_suffix(title: str) -> str:
    """yt-dlp 給分 P 的標題是「原標題 p01 分集名」，去掉後面那段。"""
    return re.sub(r"\s+p\d{2,}(?:\s.*)?$", "", title or "").strip()


def detect_site(url: str) -> str:
    host = urllib.parse.urlparse(url.strip()).netloc.lower()
    if any(h in host for h in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return "youtube"
    if any(h in host for h in ("bilibili.com", "b23.tv", "bilibili.tv")):
        return "bilibili"
    return "other"


# ---------------------------------------------------------------- Cookie 參數
def cookie_args(site: str, s: dict) -> list[str]:
    c = s["cookies"].get(site, {})
    mode = c.get("mode", "none")
    if mode == "login" and site == "bilibili":
        return ["--cookies", str(BILI_COOKIE_FILE)] if BILI_COOKIE_FILE.is_file() else []
    if mode == "file" and c.get("file"):
        return ["--cookies", c["file"]]
    if mode == "browser" and c.get("browser"):
        spec = c["browser"]
        if c.get("profile"):
            spec += ":" + c["profile"]
        return ["--cookies-from-browser", spec]
    return []


def cookie_status_text(site: str, s: dict) -> str:
    c = s["cookies"].get(site, {})
    mode = c.get("mode", "none")
    if mode == "login":
        return tr("B站登入") if BILI_COOKIE_FILE.is_file() else tr("未登入（將以訪客下載）")
    if mode == "file":
        f = c.get("file", "")
        return tr('cookies 檔：{name}').format(name=Path(f).name) if f and Path(f).is_file() else tr("cookies 檔不存在！")
    if mode == "browser":
        return tr('讀取 {get} 的 Cookie').format(get=c.get('browser'))
    return tr("不使用 Cookie")


# ---------------------------------------------------------------- 指令組合
PROGRESS_TAG = "@@P@@"
TITLE_TAG = "@@T@@"
FILE_TAG = "@@F@@"


def common_args(s: dict, site: str) -> list[str]:
    # --encoding utf-8：強制 yt-dlp 用 UTF-8 輸出，否則 Windows 會用 Big5 造成日文亂碼
    a = ["--ignore-config", "--encoding", "utf-8", "--newline", "--color", "never"]
    ff = find_ffmpeg()
    if ff:
        a += ["--ffmpeg-location", str(Path(ff).parent)]
    deno = find_deno()
    if deno:
        a += ["--js-runtimes", f"deno:{deno}"]
    if PLUGIN_DIR.is_dir():
        a += ["--plugin-dirs", "default", "--plugin-dirs", str(PLUGIN_DIR)]
    a += cookie_args(site, s)
    if site == "bilibili":
        # 沿用原本 bilibili BAT 的保守設定，避免被 412 風控
        a += ["--sleep-requests", "1"]
    return a


def build_download_args(url: str, s: dict, custom_format: str = "", is_playlist_item: bool = False) -> list[str]:
    """回傳 yt-dlp 參數（不含執行檔本身）。"""
    site = detect_site(url)
    mode = s["mode"]
    a = common_args(s, site)
    if site == "bilibili":
        # B站 CDN 常在傳輸中途斷線：重試次數多、間隔短（每次重試都會從斷點續傳）
        retry = ["--retries", "30", "--fragment-retries", "30", "--extractor-retries", "3",
                 "--retry-sleep", "http:linear=2:10:2", "--retry-sleep", "fragment:linear=2:10:2",
                 "--retry-sleep", "extractor:exp=5:60", "--socket-timeout", "20"]
    else:
        retry = ["--retries", "5", "--fragment-retries", "5", "--extractor-retries", "3",
                 "--retry-sleep", "http:exp=5:60", "--retry-sleep", "fragment:exp=5:60",
                 "--retry-sleep", "extractor:exp=5:60"]
    a += ["--no-abort-on-error", "--continue", "--part", "--windows-filenames"] + retry + [
        "-N", str(int(s.get("concurrent_fragments") or 1)),
        "--progress", "--no-quiet",
        "--progress-template",
        "download:" + PROGRESS_TAG + "%(progress.downloaded_bytes)s|%(progress.total_bytes,progress.total_bytes_estimate)s"
        "|%(progress._speed_str)s|%(progress._eta_str)s|%(info.format_id)s",
        "--print", "before_dl:" + TITLE_TAG + "%(title)s",
        "--print", "after_move:" + FILE_TAG + "%(filepath)s",
    ]
    if site == "bilibili":
        # B站部分 CDN 節點（upos-sz-mirrorcosov）不帶 Range 標頭會回 HTTP 567，
        # 分段下載讓每個請求都帶 Range，就能正常下載（yt-dlp issue #17847）；
        # 5MB 一段，節點中途斷線時損失較少
        a += ["--http-chunk-size", "5M"]
    if uses_aria2(url, s):
        n = max(1, min(16, int(s.get("aria2_conns") or 16)))
        if site == "bilibili":
            n = min(n, BILI_MAX_CONNS)    # B站 CDN 對同 IP 太多連線會限流 / 斷線
        # 一般檔案用 aria2c 多連線；HLS / DASH 分段串流仍用內建下載器（-N 控制並行）
        a += ["--downloader", "aria2c", "--downloader", "dash,m3u8:native",
              "--downloader-args",
              f"aria2c:-x{n} -s{n} -k 1M --auto-save-interval=1 --max-tries=30 --retry-wait=2 "
              f"--connect-timeout=20 --timeout=20 --summary-interval=0 "
              # Windows 常見：沒有 IPv6 卻連 IPv6 位址 →「unreachable network」；aria2 內建 DNS 也常出錯
              f"--disable-ipv6=true --async-dns=false "
              # 連線慢到幾乎停住（節點限流）就斷開重連，不要乾等
              f"--lowest-speed-limit=20K"]
    if s.get("limit_rate"):
        a += ["-r", s["limit_rate"]]
    a += ["--yes-playlist"] if s.get("playlist") else ["--no-playlist"]

    out_dir = s["audio_dir"] if mode == "audio" else s["video_dir"]
    template = s.get("template") or DEFAULT_TEMPLATE
    if s.get("playlist"):
        template = "%(playlist_title|未分類)s/" + template if "%(playlist" not in template else template
    a += ["-P", out_dir, "-o", template]

    # -------- 格式
    sort = []
    if mode in ("video", "video_only"):
        q = s.get("video_quality", "best")
        if q != "best":
            sort.append(f"res:{q}")
        if mode == "video":
            sort += VIDEO_AUDIO_SORT.get(s.get("video_audio_pref", "best"), [])
        codec = s.get("codec_pref", "auto")
        if codec == "h264":
            sort += ["vcodec:h264", "acodec:aac"]
        elif codec != "auto":
            sort.append(f"vcodec:{codec}")
        container = s.get("container", "mkv")
        if container == "webm" and codec == "auto":
            sort += ["vcodec:vp9", "acodec:opus"]
        if mode == "video":
            fmt = custom_format or ("bv*+ba/b" if q == "best" else f"bv*[height<={q}]+ba/b[height<={q}]/bv*+ba/b")
            a += ["-f", fmt, "--merge-output-format", MERGE_FMT.get(container, container)]
        else:
            fmt = custom_format or ("bv" if q == "best" else f"bv[height<={q}]/bv")
            a += ["-f", fmt, "--remux-video", REMUX_FMT.get(container, container)]
        if sort and not custom_format:
            a += ["-S", ",".join(sort)]
        if s.get("subtitles"):
            a += ["--write-subs", "--sub-langs", s.get("sub_langs") or "all", "--embed-subs"]
            if s.get("auto_subs"):
                a += ["--write-auto-subs"]
        if s.get("embed_chapters"):
            a += ["--embed-chapters"]
    else:  # audio
        afmt = s.get("audio_format", "mp3")
        fmt = custom_format or "ba/b"
        a += ["-f", fmt]
        if afmt != "original":
            a += ["-x", "--audio-format", afmt]
            if afmt in ("mp3", "m4a", "opus"):
                a += ["--audio-quality", s.get("audio_quality", "0")]
        else:
            a += ["-x"]   # 只抽出音訊，不轉碼
        if s.get("subtitles"):
            a += ["--write-subs", "--sub-langs", s.get("sub_langs") or "all"]

    if s.get("embed_metadata"):
        a += ["--embed-metadata"]
    can_embed_thumb = not (mode == "audio" and s.get("audio_format") == "wav")
    if s.get("embed_thumbnail") and can_embed_thumb:
        a += ["--embed-thumbnail"]
    if s.get("write_thumbnail"):
        a += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
    if s.get("sponsorblock") and site == "youtube":
        a += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]
    a += ["--", url]
    return a


def build_info_args(url: str, s: dict) -> list[str]:
    site = detect_site(url)
    a = common_args(s, site)
    a += ["-J", "--no-warnings"]
    a += ["--flat-playlist"]
    a += ["--yes-playlist"] if s.get("playlist") else ["--no-playlist"]
    a += ["--", url]
    return a


# ---------------------------------------------------------------- 格式解析
def human_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.2f}TB"


def human_duration(sec) -> str:
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return ""
    h, r = divmod(sec, 3600)
    m, s_ = divmod(r, 60)
    return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"


def _short_codec(c: str | None) -> str:
    if not c or c == "none":
        return ""
    c = c.lower()
    for key, name in (("avc", "H.264"), ("h264", "H.264"), ("hev", "H.265"), ("hvc", "H.265"),
                      ("h265", "H.265"), ("av01", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"),
                      ("mp4a", "AAC"), ("aac", "AAC"), ("opus", "Opus"), ("flac", "FLAC"),
                      ("ec-3", "E-AC3"), ("ac-3", "AC3"), ("mp3", "MP3")):
        if c.startswith(key):
            return name
    return c.split(".")[0]


def parse_info(info: dict) -> dict:
    """把 yt-dlp -J 結果整理成 GUI 好用的格式。"""
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        return {
            "is_playlist": True,
            "title": info.get("title") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "count": len(entries),
            "entries": [(e.get("title") or e.get("id") or "", e.get("url") or e.get("webpage_url") or "") for e in entries],
            "video_formats": [], "audio_formats": [],
        }
    vids, auds = [], []
    for f in info.get("formats") or []:
        vc, ac = f.get("vcodec"), f.get("acodec")
        proto = f.get("protocol") or ""
        if "mhtml" in proto or f.get("format_note") == "storyboard":
            continue
        size = f.get("filesize") or f.get("filesize_approx")
        note = f.get("format_note") or ""
        # 傳輸方式：HLS（m3u8）/ DASH（分段）/ 一般 https
        proto_s = "HLS" if "m3u8" in proto else "DASH" if "dash" in proto else ""
        dr = f.get("dynamic_range") or ""
        if vc and vc != "none":
            h = f.get("height")
            res = f"{h}p" if h else (f.get("resolution") or "")
            fps = f.get("fps")
            if fps and fps > 30:
                res += f"{int(fps)}"
            label = res
            if note and note not in label:
                label += f" {note}"
            vids.append({
                "id": str(f.get("format_id")), "label": label.strip(), "height": h or 0, "fps": fps or 0,
                "ext": f.get("ext") or "", "vcodec": _short_codec(vc),
                "acodec": _short_codec(ac), "tbr": f.get("tbr") or f.get("vbr") or 0,
                "size": human_size(size) if size else "", "has_audio": bool(ac and ac != "none"),
                "dr": "" if dr in ("", "SDR") else dr, "proto": proto_s, "note": note,
                "vcodec_raw": vc or "",
            })
        elif ac and ac != "none":
            lang = f.get("language") or ""
            auds.append({
                "id": str(f.get("format_id")), "label": (note or "audio").strip(),
                "ext": f.get("ext") or "", "acodec": _short_codec(ac),
                "abr": f.get("abr") or f.get("tbr") or 0, "asr": f.get("asr") or 0,
                "lang": lang, "size": human_size(size) if size else "",
                "proto": proto_s, "note": note, "acodec_raw": ac or "",
                "channels": f.get("audio_channels") or 0,
            })
    vids.sort(key=lambda v: (v["height"], v["fps"], v["tbr"]), reverse=True)
    auds.sort(key=lambda a: a["abr"] or 0, reverse=True)
    return {
        "is_playlist": False,
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": human_duration(info.get("duration")),
        "upload_date": info.get("upload_date") or "",
        "thumbnail": _pick_thumbnail(info),
        "webpage_url": info.get("webpage_url") or "",
        "video_formats": vids, "audio_formats": auds,
    }


def _pick_thumbnail(info: dict) -> str:
    """挑一張 jpg/png 縮圖（Qt 不一定支援 webp），沒有就用預設的。"""
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    for t in reversed(thumbs):          # yt-dlp 依品質由低到高排
        u = t["url"].split("?")[0].lower()
        if u.endswith((".jpg", ".jpeg", ".png")) and (t.get("width") or 0) <= 1280:
            return t["url"]
    url = info.get("thumbnail") or ""
    if url.startswith("//"):
        url = "https:" + url
    return url.replace("http://", "https://", 1)


def parse_progress(line: str):
    """解析 PROGRESS_TAG 行 → (percent or None, speed, eta, format_id)"""
    body = line.split(PROGRESS_TAG, 1)[1]
    parts = body.split("|")
    while len(parts) < 5:
        parts.append("")
    done, total, speed, eta, fid = (p.strip() for p in parts[:5])
    pct = None
    try:
        if total not in ("", "NA", "None") and float(total) > 0:
            pct = max(0.0, min(100.0, float(done) / float(total) * 100))
    except ValueError:
        pass
    clean = lambda x: "" if (x in ("NA", "None") or "Unknown" in x) else x
    return pct, clean(speed), clean(eta), clean(fid)


# ---------------------------------------------------------------- Bilibili 登入
class BilibiliLogin:
    GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key="
    NAV = "https://api.bilibili.com/x/web-interface/nav"

    def __init__(self, cookie_file: Path = BILI_COOKIE_FILE):
        self.cookie_file = Path(cookie_file)
        self.jar = http.cookiejar.MozillaCookieJar(str(self.cookie_file))
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", UA), ("Referer", "https://www.bilibili.com/")]

    def _get_json(self, url: str) -> dict:
        with self.opener.open(url, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def generate(self) -> tuple[str, str]:
        d = self._get_json(self.GEN)
        if d.get("code") != 0:
            raise RuntimeError(d.get("message") or tr("無法取得 QR Code"))
        return d["data"]["url"], d["data"]["qrcode_key"]

    def poll(self, key: str) -> tuple[int, str]:
        """回傳 (code, 說明)。0=成功 86101=未掃描 86090=已掃描待確認 86038=過期"""
        d = self._get_json(self.POLL + urllib.parse.quote(key))
        data = d.get("data") or {}
        code = data.get("code", d.get("code", -1))
        msgs = {0: tr("登入成功"), 86101: tr("請用 Bilibili App 掃描 QR Code"),
                86090: tr("已掃描，請在手機上確認登入"), 86038: tr("QR Code 已過期，請重新產生")}
        if code == 0:
            self._finish_login(data.get("url") or "")
        return code, msgs.get(code, data.get("message") or d.get("message") or str(code))

    def _finish_login(self, redirect_url: str) -> None:
        # 少數情況 Set-Cookie 沒進 jar，從回傳網址參數補上
        names = {c.name for c in self.jar}
        # 不做 URL 解碼：瀏覽器裡的 SESSDATA 本來就是 %2C 編碼形式
        query = urllib.parse.urlparse(redirect_url).query
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        expires = int(time.time()) + 180 * 86400
        for k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
            if k not in names and q.get(k):
                self.jar.set_cookie(_make_cookie(k, q[k], ".bilibili.com", expires, secure=(k == "SESSDATA")))
        # 取得 buvid3 等裝置 cookie，降低被風控（HTTP 412）的機率
        try:
            self.opener.open("https://www.bilibili.com/", timeout=15).read(2048)
        except Exception:
            pass
        self.save()

    def save(self) -> None:
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.jar.save(ignore_discard=True, ignore_expires=True)

    def load(self) -> bool:
        if not self.cookie_file.is_file():
            return False
        try:
            self.jar.load(ignore_discard=True, ignore_expires=True)
            return True
        except Exception:
            return False

    def status(self) -> dict:
        """查詢登入狀態：{'login': bool, 'uname': str, 'vip': bool, 'vip_label': str}"""
        if not self.load():
            return {"login": False, "uname": "", "vip": False, "vip_label": ""}
        d = self._get_json(self.NAV)
        data = d.get("data") or {}
        vip_label = ((data.get("vip_label") or {}).get("text")) or ""
        return {
            "login": bool(data.get("isLogin")),
            "uname": data.get("uname") or "",
            "vip": data.get("vipStatus") == 1,
            "vip_label": vip_label,
        }

    def logout(self) -> None:
        try:
            self.cookie_file.unlink()
        except FileNotFoundError:
            pass
        self.jar.clear()


def _make_cookie(name, value, domain, expires, secure=False):
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path="/", path_specified=True, secure=secure, expires=expires, discard=False,
        comment=None, comment_url=None, rest={}, rfc2109=False)


def validate_cookie_file(path: str) -> tuple[bool, str]:
    """檢查是否為 yt-dlp 能讀的 Netscape 格式 cookies.txt。"""
    try:
        jar = http.cookiejar.MozillaCookieJar(path)
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        return False, tr('不是有效的 Netscape cookies.txt：{e}').format(e=e)
    domains = sorted({c.domain.lstrip(".") for c in jar})
    if not domains:
        return False, tr("檔案裡沒有任何 cookie")
    return True, tr('共 {n} 筆 cookie，網域：{join}{v}').format(n=len(jar), join=', '.join(domains[:6]), v=' …' if len(domains) > 6 else '')


# ---------------------------------------------------------------- 工具下載
FFMPEG_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
ARIA2_URL = ("https://github.com/aria2/aria2/releases/download/release-1.37.0/"
             "aria2-1.37.0-win-64bit-build1.zip")
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def download_file(url: str, dest: Path, progress=None, cancel=None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel and cancel():
                raise RuntimeError(tr("已取消"))
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, dest)
    return dest


def _extract_members(zip_path: Path, wanted: dict[str, Path]) -> list[Path]:
    """wanted: {檔名: 目的地}；從 zip 任意子資料夾找到同名檔案解出。"""
    got = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.rsplit("/", 1)[-1]
            if name in wanted:
                dst = wanted[name]
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                got.append(dst)
    return got


def install_ffmpeg(progress=None, cancel=None) -> list[Path]:
    zp = TOOLS_DIR / "ffmpeg.zip"
    download_file(FFMPEG_URL, zp, progress, cancel)
    try:
        got = _extract_members(zp, {"ffmpeg.exe": TOOLS_DIR / "ffmpeg.exe",
                                    "ffprobe.exe": TOOLS_DIR / "ffprobe.exe"})
    finally:
        zp.unlink(missing_ok=True)
    if len(got) < 2:
        raise RuntimeError(tr("壓縮檔裡找不到 ffmpeg.exe / ffprobe.exe"))
    return got


def install_deno(progress=None, cancel=None) -> list[Path]:
    zp = TOOLS_DIR / "deno.zip"
    download_file(DENO_URL, zp, progress, cancel)
    try:
        got = _extract_members(zp, {"deno.exe": TOOLS_DIR / "deno.exe"})
    finally:
        zp.unlink(missing_ok=True)
    if not got:
        raise RuntimeError(tr("壓縮檔裡找不到 deno.exe"))
    return got


def install_aria2(progress=None, cancel=None) -> list[Path]:
    zp = TOOLS_DIR / "aria2.zip"
    download_file(ARIA2_URL, zp, progress, cancel)
    try:
        got = _extract_members(zp, {"aria2c.exe": TOOLS_DIR / "aria2c.exe",
                                    "COPYING": TOOLS_DIR / "aria2-COPYING.txt"})
    finally:
        zp.unlink(missing_ok=True)
    if not any(p.name == "aria2c.exe" for p in got):
        raise RuntimeError(tr("壓縮檔裡找不到 aria2c.exe"))
    return got


def aria2_progress(path: str | Path):
    """讀 aria2 控制檔（.aria2）算出已下載位元組。回傳 (done, total) 或 None。
    格式：VER(2) EXT(4) HASHLEN(4) HASH PIECELEN(4) TOTAL(8) UPLOAD(8) BFLEN(4) BITFIELD
          NUM_INFLIGHT(4) {INDEX(4) LEN(4) PBFLEN(4) PBF}…  version 1 為 big-endian。"""
    import struct
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    try:
        ver = struct.unpack_from(">H", data, 0)[0]
        e = ">" if ver == 1 else "<"
        pos = 2 + 4
        hlen = struct.unpack_from(e + "I", data, pos)[0]
        pos += 4 + hlen
        piece_len, total = struct.unpack_from(e + "IQ", data, pos)
        pos += 4 + 8 + 8
        bflen = struct.unpack_from(e + "I", data, pos)[0]
        pos += 4
        bitfield = data[pos:pos + bflen]
        pos += bflen
        if total <= 0 or piece_len <= 0:
            return None
        n_pieces = (total + piece_len - 1) // piece_len
        done = 0
        for i in range(n_pieces):
            if bitfield[i // 8] >> (7 - i % 8) & 1:
                done += min(piece_len, total - i * piece_len)
        # 進行中的 piece：每個 bit 代表 16KiB
        if pos + 4 <= len(data):
            n_inflight = struct.unpack_from(e + "I", data, pos)[0]
            pos += 4
            for _ in range(min(n_inflight, 4096)):
                _idx, plen, pbl = struct.unpack_from(e + "III", data, pos)
                pos += 12
                bits = sum(bin(b).count("1") for b in data[pos:pos + pbl])
                pos += pbl
                done += min(plen, bits * 16384)
        return min(done, total), total
    except (struct.error, IndexError):
        return None


def install_ytdlp(progress=None, cancel=None) -> Path:
    return download_file(YTDLP_URL, BASE_DIR / "yt-dlp.exe", progress, cancel)


# ---------------------------------------------------------------- 自動檢查更新
UPDATE_API = {
    "yt-dlp": "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
    "aria2c": "https://api.github.com/repos/aria2/aria2/releases/latest",
}


def tool_version(exe: str, args=("--version",)) -> str:
    """執行 exe --version 之類指令，從輸出裡挑出版本號字串（抓不到就回傳空字串）。"""
    try:
        flags = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW，避免彈出黑視窗
        r = subprocess.run([exe, *args], capture_output=True, timeout=10, creationflags=flags)
        out = (r.stdout or r.stderr or b"").decode("utf-8", "replace")
        m = re.search(r"(\d{4}\.\d{2}\.\d{2}(?:\.\d+)?|\d+\.\d+(?:\.\d+)?)", out)
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _version_key(v: str) -> tuple:
    parts = []
    for p in re.findall(r"\d+", v or ""):
        parts.append(int(p))
    return tuple(parts)


def check_update(name: str, current: str) -> dict | None:
    """查 GitHub 最新版本。回傳 {'latest': str, 'has_update': bool}；查詢失敗回傳 None（不代表沒有更新）。"""
    url = UPDATE_API.get(name)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        latest = (data.get("tag_name") or "").lstrip("vV").strip()
        if not latest:
            return None
        has_update = bool(current) and _version_key(latest) > _version_key(current)
        return {"latest": latest, "has_update": has_update}
    except Exception:  # noqa: BLE001
        return None


BILI_MAX_CONNS = 8

BILI_CDN_CHOICES = [
    (tr("自動（避開不穩定節點，建議）"), "auto"),
    (tr("阿里雲 upos-sz-mirrorali"), "upos-sz-mirrorali.bilivideo.com"),
    (tr("騰訊雲 upos-sz-mirrorcos"), "upos-sz-mirrorcos.bilivideo.com"),
    (tr("華為雲 upos-sz-mirrorhw"), "upos-sz-mirrorhw.bilivideo.com"),
    ("upos-sz-mirror08c", "upos-sz-mirror08c.bilivideo.com"),
    (tr("Akamai（海外）upos-hz-mirrorakam"), "upos-hz-mirrorakam.akamaized.net"),
    (tr("不修改（yt-dlp 原本行為）"), "off"),
]


def bili_cdn_rotation(first: str) -> list[str]:
    """B站節點被掐斷時，自動依序嘗試的 CDN 清單（第一個是使用者原本的設定）。"""
    hosts = [v for _t, v in BILI_CDN_CHOICES if v not in ("auto", "off")]
    order = [first or "auto"] + [h for h in hosts if h != first]
    if "off" not in order:
        order.append("off")
    return order


def subprocess_env(s: dict | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 讓 yt-dlp 找得到 tools 裡的 ffmpeg / deno
    env["PATH"] = str(TOOLS_DIR) + os.pathsep + env.get("PATH", "")
    # 給 yt_dlp_plugins/BiliCDNFix 外掛：B站 CDN 節點選擇
    env["YTDL_BILI_CDN"] = (s or {}).get("bili_cdn") or "auto"
    return env
