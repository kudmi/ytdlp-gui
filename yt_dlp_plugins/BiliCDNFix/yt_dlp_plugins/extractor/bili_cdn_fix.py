# -*- coding: utf-8 -*-
"""
yt-dlp 外掛：Bilibili CDN 節點修正（YTDL GUI 附帶）

問題：yt-dlp 的 B站模組只用 B站給的主要網址（baseUrl），忽略備用網址（backupUrl）。
主要網址常被分到 upos-sz-mirrorcosov（騰訊 EdgeOne）或 PCDN 節點，會出現
HTTP 567、Read timed out、「xxx bytes read, yyy more expected」而下載失敗。

做法：在 yt-dlp 解析格式之前，從 baseUrl + backupUrl 裡挑最穩定的節點。
環境變數 YTDL_BILI_CDN：
  auto（預設）   自動避開不穩定節點
  off            不做任何修改
  <主機名稱>      強制把所有 upos 網址換成這個主機，例如 upos-sz-mirrorali.bilivideo.com
"""
import json
import os
import re
import urllib.parse

from yt_dlp.extractor import bilibili as _bili

# 數字越小越優先
_RANK = [
    (re.compile(r"upos-sz-mirrorali(?:b|ov)?\.bilivideo\.com"), 0),
    (re.compile(r"upos-sz-mirrorcos\.bilivideo\.com"), 1),
    (re.compile(r"upos-sz-mirror08[ch]\.bilivideo\.com"), 2),
    (re.compile(r"upos-sz-mirrorhw\.bilivideo\.com"), 3),
    (re.compile(r"upos-hz-mirrorakam\.akamaized\.net"), 4),
    (re.compile(r"upos-sz-(?:mirror|estg)\w*\.bilivideo\.com"), 5),
    (re.compile(r"\.bilivideo\.(?:com|cn)$"), 6),
]
_BAD = re.compile(r"mirrorcosov|mcdn\.bilivideo|szbdyd\.com|\.mountaintoys\.cn|^\d+\.\d+\.\d+\.\d+$")


def _host(url):
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


def _score(url):
    h = _host(url)
    port = urllib.parse.urlparse(url).port
    if _BAD.search(h) or (port and port not in (80, 443)):
        return 100
    for rx, s in _RANK:
        if rx.search(h):
            return s
    return 50


def _candidates(item):
    urls = []
    for key in ("baseUrl", "base_url", "url"):
        u = item.get(key)
        if isinstance(u, str) and u:
            urls.append(u)
    for key in ("backupUrl", "backup_url"):
        for u in item.get(key) or []:
            if isinstance(u, str) and u and u not in urls:
                urls.append(u)
    return urls


def _rewrite_host(url, host):
    p = urllib.parse.urlparse(url)
    if "upos" not in (p.hostname or "") and not _BAD.search(p.hostname or ""):
        return url
    return urllib.parse.urlunparse(p._replace(netloc=host))


def choose_url(item, mode=None):
    mode = (mode or os.environ.get("YTDL_BILI_CDN") or "auto").strip()
    urls = _candidates(item)
    if not urls or mode.lower() == "off":
        return None
    if mode.lower() != "auto":
        return _rewrite_host(urls[0], mode)
    best = min(urls, key=_score)          # min 會保留原本順序中的第一個
    if _score(best) >= 100:
        # 全部都是不穩定節點：換成阿里雲 upos 主機（BBDown 等工具的常見做法）
        return _rewrite_host(urls[0], "upos-sz-mirrorali.bilivideo.com")
    return best


def _fix_play_info(play_info):
    if not isinstance(play_info, dict):
        return
    dash = play_info.get("dash") or {}
    groups = []
    groups += dash.get("video") or []
    groups += dash.get("audio") or []
    groups += (dash.get("dolby") or {}).get("audio") or []
    flac = (dash.get("flac") or {}).get("audio")
    if isinstance(flac, dict):
        groups.append(flac)
    for item in groups:
        if not isinstance(item, dict):
            continue
        new = choose_url(item)
        if new and new != item.get("baseUrl", item.get("base_url")):
            item["_ytdl_original_url"] = item.get("baseUrl") or item.get("base_url")
            for key in ("baseUrl", "base_url"):
                if key in item or key == "baseUrl":
                    item[key] = new


_orig_extract_formats = _bili.BilibiliBaseIE.extract_formats


def _report_qualities(self, play_info):
    """把 B站這支影片的完整畫質清單（含不能下載的）交給 YTDL GUI。
    寫到 stderr（-J 安靜模式下 to_screen 不會輸出），一行：@@BILIFMT@@<json>"""
    dash = play_info.get("dash") or {}
    got = {v.get("id") for v in dash.get("video") or [] if isinstance(v, dict)}
    if not got and play_info.get("durl"):
        got = {play_info.get("quality")}
    items = []
    for f in play_info.get("support_formats") or []:
        if not isinstance(f, dict) or f.get("quality") is None:
            continue
        q = f["quality"]
        items.append({
            "q": q,
            "name": f.get("new_description") or f.get("display_desc") or str(q),
            "ok": q in got,
            "vip": bool(f.get("need_vip")),
            "login": bool(f.get("need_login")),
        })
    if items:
        self._downloader.to_stderr("@@BILIFMT@@" + json.dumps(items, ensure_ascii=True))


def _patched_extract_formats(self, play_info):
    try:
        _report_qualities(self, play_info)
    except Exception:  # noqa: BLE001
        pass
    try:
        _fix_play_info(play_info)
        dash = (play_info or {}).get("dash") or {}
        hosts = sorted({_host(i.get("baseUrl") or i.get("base_url") or "")
                        for i in (dash.get("video") or []) + (dash.get("audio") or []) if isinstance(i, dict)} - {""})
        if hosts:
            mode = os.environ.get("YTDL_BILI_CDN") or "auto"
            self.to_screen(f"[BiliCDNFix] CDN 節點（{mode}）：{', '.join(hosts)}")
    except Exception as e:  # noqa: BLE001  修正失敗就照原本流程
        self.report_warning(f"[BiliCDNFix] {e}")
    return _orig_extract_formats(self, play_info)


if not getattr(_bili.BilibiliBaseIE.extract_formats, "_ytdl_cdn_fix", False):
    _patched_extract_formats._ytdl_cdn_fix = True
    _bili.BilibiliBaseIE.extract_formats = _patched_extract_formats
