# YT-DLP Downloader — Cyberpunk Edition

[繁體中文](README.md) | [简体中文](README.zh-CN.md) | [English](README.en.md) | **日本語**

[yt-dlp](https://github.com/yt-dlp/yt-dlp) をベースにした Windows 用デスクトップダウンローダーです。PySide6 で作ったサイバーパンク風のインターフェースを備えています。
YouTube、bilibili をはじめ、yt-dlp が対応する数千のサイトに対応しています。

## 機能

- **ダウンロード**：映像 + 音声 / 音声のみ / 映像のみ。画質、コーデックの優先（H.264 / AV1 / VP9 / HEVC）、コンテナ（mkv / mp4 / webm）を選択可能
- **音声**：MP3 / FLAC / M4A / Opus / WAV / 元の音声（変換しない）
- **ストリーム一覧**：解析後、すべての映像 / 音声ストリームを表で表示（解像度、FPS、コーデック、HDR、ビットレート、サイズ）。コーデックで絞り込み、ダウンロードするストリームを指定可能
- **ダウンロードキュー**：リアルタイムの進行状況、失敗の原因と対処法、失敗時の自動再試行（間隔は徐々に長くなります）
- **マルチ接続ダウンロード**：aria2c を使い、1 つのファイルを複数の接続で同時にダウンロード（任意）
- **bilibili**
  - QR コードでログイン（ログインすると 1080P 以上、大会員なら 4K / Hi-Res もダウンロード可能）
  - 解析時に各画質が「ダウンロード可能 / 大会員限定 / ログインが必要」かを表示
  - 複数エピソードの動画から選んでダウンロード
  - 不安定な CDN ノードを自動で避け、切断が続く場合は別のノードに切り替えてレジューム
- **YouTube**：ブラウザーの Cookie または cookies.txt（メンバー限定・年齢制限のあるコンテンツ）
- **その他**：ダウンロード履歴、システムトレイ、完了通知、起動時の yt-dlp / aria2c の更新確認、設定のエクスポート / インポート、ダーク / ライトテーマ
- **多言語対応**：繁體中文 / 简体中文 / English / 日本語。既定では Windows の表示言語に合わせます

## 動作環境

- Windows 10 / 11（64 ビット）
- Python 3.10 以上（インストール時に「Add python.exe to PATH」にチェック）

## インストールと起動

1. このリポジトリをダウンロードまたは clone し、そのフォルダーでコマンドプロンプトを開きます。
2. 初回のみ：専用の Python 環境を作成し、パッケージをインストールします
   ```bat
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. プログラムを起動します（次回からはこの行だけで起動できます）
   ```bat
   .venv\Scripts\pythonw YTDL_GUI.pyw
   ```
4. プログラム左側の「ツール / 設定」で、必要なツールをワンクリックでダウンロードします：
   - **yt-dlp**（必須）
   - **ffmpeg**：映像と音声の結合、MP3 / FLAC への変換、サムネイルと字幕の埋め込みに必要
   - **Deno**：YouTube ですべての画質を取得するために必要
   - **aria2c**（任意）：マルチ接続ダウンロード

起動しない、またはすぐに終了してしまう場合は、`pythonw` ではなく `python` で起動してください。コマンドプロンプトにエラーメッセージが表示されます（`error.log` にも記録されます）：

```bat
.venv\Scripts\python YTDL_GUI.pyw
```

表示言語は「ツール / 設定 › 外観 › 言語 / Language」で日本語に切り替えられます。詳しい使い方の [`使用說明.txt`](使用說明.txt) は繁体字中国語のみです。

## 単一の exe にパッケージ化

上記の手順で `.venv` を作成してから、PyInstaller でパッケージ化します（翻訳ファイルと Qt 内蔵の翻訳も exe に含まれます）：

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

`dist\YTDL_GUI.exe` が作成されます。exe は `yt-dlp.exe` および `fonts` フォルダーと同じフォルダーに置いてください。

## プロジェクト構成

```
YTDL_GUI.pyw      メインプログラム（UI とダウンロード処理）
ytdl_core.py      ダウンロードコマンド、bilibili ログイン、ツールのダウンロードなどのコアロジック
ui_theme.py       サイバーパンクテーマ：配色、フォント、アイコン、独自描画のウィジェット
i18n.py           多言語対応
lang/             翻訳ファイル（key = 繁体字中国語の原文）
fonts/            Orbitron、JetBrains Mono（SIL Open Font License）
yt_dlp_plugins/   yt-dlp プラグイン：BiliCDNFix（bilibili CDN ノードの修正）、ChromeCookieUnlock
```

実行時に作成されるファイル（`.gitignore` に記載済みで、アップロードされません）：`settings.json`、`history.db`、`cookies/`、`tools/`、`Download/`、`audio/`、`.venv/`。

## 翻訳

翻訳ファイルは `lang/`（`zh_CN.json`、`en.json`、`ja.json`）にあります。key が繁体字中国語の原文、value が翻訳です。
翻訳がない文は繁体字中国語の原文で表示されます。翻訳の改善を歓迎します。

## プライバシー

ログイン情報（bilibili のログイン Cookie、cookies.txt）は PC 上のプログラムフォルダーにのみ保存され、yt-dlp と各サイト以外に送信されることはありません。
**`cookies/`、`settings.json`、cookies.txt は絶対にインターネットにアップロードしないでください**。これらはアカウントにログインした状態そのものです。

## ライセンス

このプロジェクトのコードは [MIT License](LICENSE) で公開しています。

サードパーティのコンポーネント：

| コンポーネント | ライセンス | 備考 |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | 実行時にプログラムがダウンロード（リポジトリには含まれません） |
| [FFmpeg](https://ffmpeg.org/) | LGPL / GPL | 実行時にプログラムがダウンロード（リポジトリには含まれません） |
| [Deno](https://deno.com/) | MIT | 実行時にプログラムがダウンロード（リポジトリには含まれません） |
| [aria2](https://github.com/aria2/aria2) | GPL-2.0 | 実行時にプログラムがダウンロード（リポジトリには含まれません） |
| [PySide6](https://doc.qt.io/qtforpython/) | LGPL-3.0 | pip でインストール |
| Orbitron、JetBrains Mono フォント | SIL OFL 1.1 | ライセンスファイルは `fonts/` にあります |
| [yt-dlp-ChromeCookieUnlock](yt_dlp_plugins/ChromeCookieUnlock) | MIT | ライセンスファイルは同フォルダーにあります |

## 免責事項

このツールは個人的なバックアップと学習のみを目的としています。ダウンロードの際は各サイトの利用規約と著作権法を守り、
権利のないコンテンツをダウンロードしたり配布したりしないでください。
