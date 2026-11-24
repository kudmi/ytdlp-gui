# -*- coding: utf-8 -*-
"""
多國語言：繁體中文（原文）/ 简体中文 / English / 日本語。

用法：from i18n import tr
    tr("下載")                       → 依目前語言回傳翻譯
    tr("共 {n} 筆").format(n=3)      → 有變數的句子先翻譯再代入

翻譯檔：lang/zh_CN.json、lang/en.json、lang/ja.json（key = 繁體中文原文，value = 翻譯）。
找不到翻譯的句子會直接顯示繁體中文原文，不會出錯；想修改翻譯直接改 JSON 即可（重開程式生效）。

語言在 import 時就決定（程式裡有些文字是模組層級常數，import 時就要翻好），
所以切換語言要重新啟動程式。設定存在 settings.json 的 "language"：
  "system"（預設，跟隨 Windows 顯示語言）/ "zh_TW" / "zh_CN" / "en" / "ja"
"""
from __future__ import annotations

import json
import locale
import os
import sys
from pathlib import Path

LANGS = [("zh_TW", "繁體中文"), ("zh_CN", "简体中文"), ("en", "English"), ("ja", "日本語")]
LANG_CODES = [c for c, _n in LANGS]

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent


def _lang_dirs() -> list[Path]:
    dirs = [BASE_DIR / "lang"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "lang")
    return dirs


def system_language() -> str:
    """Windows 顯示語言 → 支援的語言代碼；不支援的語言用英文。"""
    name = ""
    if os.name == "nt":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(langid, "")
        except Exception:  # noqa: BLE001
            name = ""
    if not name:
        try:
            name = locale.getlocale()[0] or ""
        except Exception:  # noqa: BLE001
            name = ""
        name = name or os.environ.get("LANG", "")
    n = name.replace("-", "_").lower()
    if n.startswith(("zh_tw", "zh_hk", "zh_mo", "zh_hant", "chinese (traditional)")):
        return "zh_TW"
    if n.startswith(("zh", "chinese")):
        return "zh_CN"
    if n.startswith(("ja", "japanese")):
        return "ja"
    return "en"


def configured_language() -> str:
    """settings.json 裡的 language（還沒設定過 = system）。"""
    try:
        with open(BASE_DIR / "settings.json", encoding="utf-8") as f:
            v = (json.load(f) or {}).get("language") or "system"
    except Exception:  # noqa: BLE001
        v = "system"
    return v if v in LANG_CODES or v == "system" else "system"


def _load(code: str) -> dict:
    if code == "zh_TW":
        return {}
    for d in _lang_dirs():
        p = d / f"{code}.json"
        if p.is_file():
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                return {k: v for k, v in data.items() if isinstance(v, str) and v}
            except Exception:  # noqa: BLE001  翻譯檔壞掉就退回繁體中文
                return {}
    return {}


_setting = configured_language()
LANG = system_language() if _setting == "system" else _setting
_table = _load(LANG)


def tr(text: str) -> str:
    """翻譯一句繁體中文原文；沒有翻譯就回傳原文。"""
    return _table.get(text, text)


def qt_locale_name() -> str:
    """給 Qt 內建翻譯檔用（qtbase_xx.qm）；英文不需要載入。"""
    return {"zh_TW": "zh_TW", "zh_CN": "zh_CN", "ja": "ja"}.get(LANG, "")
