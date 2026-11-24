# YT-DLP Downloader — Cyberpunk Edition

**繁體中文** | [简体中文](README.zh-CN.md) | [English](README.en.md) | [日本語](README.ja.md)

以 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 為核心的 Windows 桌面下載器，PySide6 製作的 Cyberpunk 風格介面。
支援 YouTube、Bilibili 以及 yt-dlp 支援的上千個網站。

## 功能

- **下載**：影片 + 音訊 / 只要音訊 / 只要影片；可選畫質、編碼偏好（H.264 / AV1 / VP9 / HEVC）、容器（mkv / mp4 / webm）
- **音訊**：MP3 / FLAC / M4A / Opus / WAV / 原始音訊（不轉檔）
- **串流清單**：解析後用表格列出所有影片 / 音訊串流（解析度、FPS、編碼、HDR、位元率、大小），可依編碼篩選並指定下載
- **下載佇列**：即時進度、失敗原因與建議、失敗自動重試（間隔漸長）
- **多線下載**：可選用 aria2c，同一檔案多條連線
- **Bilibili**
  - QR Code 掃碼登入（登入後可下載 1080P 以上，大會員可下載 4K / Hi-Res）
  - 解析時標示每種畫質是「可下載 / 需大會員 / 需登入」
  - 視頻選集（分 P）勾選下載
  - CDN 節點自動避開不穩定節點，連續斷線時自動換節點續傳
- **YouTube**：瀏覽器 Cookie 或 cookies.txt（會員、年齡限制內容）
- **其他**：下載歷史、系統匣、完成通知、啟動時檢查 yt-dlp / aria2c 新版、設定匯出 / 匯入、深色 / 淺色主題
- **多國語言**：繁體中文 / 简体中文 / English / 日本語，預設跟隨 Windows 顯示語言

## 系統需求

- Windows 10 / 11（64 位元）
- Python 3.10 以上（安裝時勾選「Add python.exe to PATH」）

## 安裝與執行

1. 下載或 clone 這個 repo，在資料夾裡開啟命令提示字元。
2. 第一次執行：建立獨立的 Python 環境並安裝套件
   ```bat
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. 啟動程式（之後每次都用這行）
   ```bat
   .venv\Scripts\pythonw YTDL_GUI.pyw
   ```
4. 到程式左側「工具 / 設定」，一鍵下載需要的工具：
   - **yt-dlp**（必要）
   - **ffmpeg**：合併影音、轉 MP3 / FLAC、嵌入封面與字幕都需要
   - **Deno**：YouTube 需要它才能取得完整畫質
   - **aria2c**（選用）：多線下載

程式打不開或閃退時，改用 `python` 啟動（不是 `pythonw`），命令提示字元會顯示錯誤訊息；錯誤也會寫到 `error.log`：

```bat
.venv\Scripts\python YTDL_GUI.pyw
```

詳細操作說明見 [`使用說明.txt`](使用說明.txt)。

## 打包成單一 exe

先照上面建立好 `.venv`，再用 PyInstaller 打包（翻譯檔與 Qt 內建翻譯會一起包進 exe）：

```bat
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --clean --windowed --onefile ^
    --name YTDL_GUI --icon app.ico --workpath build --distpath dist ^
    --add-data ".venv\Lib\site-packages\PySide6\translations\qtbase_zh_TW.qm;PySide6\translations" ^
    --add-data ".venv\Lib\site-packages\PySide6\translations\qtbase_zh_CN.qm;PySide6\translations" ^
    --add-data ".venv\Lib\site-packages\PySide6\translations\qtbase_ja.qm;PySide6\translations" ^
    --add-data "lang;lang" ^
    YTDL_GUI.pyw
```

會產生 `dist\YTDL_GUI.exe`。exe 需要和 `yt-dlp.exe`、`fonts` 資料夾放在同一個資料夾。

## 專案結構

```
YTDL_GUI.pyw      主程式（介面與下載流程）
ytdl_core.py      下載指令、Bilibili 登入、工具下載等核心邏輯
ui_theme.py       Cyberpunk 主題：配色、字型、圖示、自繪元件
i18n.py           多國語言
lang/             翻譯檔（key = 繁體中文原文）
fonts/            Orbitron、JetBrains Mono（SIL Open Font License）
yt_dlp_plugins/   yt-dlp 外掛：BiliCDNFix（B站 CDN 節點修正）、ChromeCookieUnlock
```

執行後才會產生的檔案（已列在 `.gitignore`，不會上傳）：`settings.json`、`history.db`、`cookies/`、`tools/`、`Download/`、`audio/`、`.venv/`。

## 翻譯

翻譯檔在 `lang/`（`zh_CN.json`、`en.json`、`ja.json`），key 是繁體中文原文、value 是翻譯。
找不到翻譯的句子會顯示繁體中文原文。歡迎修正翻譯。

## 隱私

登入資料（Bilibili 登入 Cookie、cookies.txt）只存在本機的程式資料夾，不會傳到 yt-dlp 與各網站以外的地方。
**請勿把 `cookies/`、`settings.json` 或任何 cookies.txt 上傳到網路上**——它們等同於你的帳號登入狀態。

## 授權

本專案程式碼以 [MIT License](LICENSE) 授權。

第三方元件：

| 元件 | 授權 | 說明 |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | 執行時由程式下載，不包含在 repo 中 |
| [FFmpeg](https://ffmpeg.org/) | LGPL / GPL | 執行時由程式下載，不包含在 repo 中 |
| [Deno](https://deno.com/) | MIT | 執行時由程式下載，不包含在 repo 中 |
| [aria2](https://github.com/aria2/aria2) | GPL-2.0 | 執行時由程式下載，不包含在 repo 中 |
| [PySide6](https://doc.qt.io/qtforpython/) | LGPL-3.0 | 由 pip 安裝 |
| Orbitron、JetBrains Mono 字型 | SIL OFL 1.1 | 授權檔見 `fonts/` |
| [yt-dlp-ChromeCookieUnlock](yt_dlp_plugins/ChromeCookieUnlock) | MIT | 授權檔見該資料夾 |

## 免責聲明

本工具僅供個人備份與學習使用。下載內容時請遵守各網站的服務條款與著作權法規，
不要下載或散布你沒有權利取得的內容。
