# Course YouTube Backup v1.0.0

首个版本。本工具在 Mac 本地运行。

- 有权限的 YouTube URL 下载（最高 1080p）
- 手动标题，固定 Private 上传
- Resumable upload、进度、失败重试和本地文件清理
- YouTube 自动字幕手动检测、SRT / TXT 导出、复制字幕
- SQLite 历史记录、macOS start.command

需要 Python 3.10+、ffmpeg、Deno 或 Node 和自己的 Google Desktop App OAuth 凭据。无需 AI API 或云服务器。

YouTube 不保证产生自动字幕；官方字幕 API 的可用性和权限由 YouTube 决定，403 时请使用 YouTube Studio。下载 ZIP 后解压，按 README 配置 Google OAuth，再运行 start.command。
