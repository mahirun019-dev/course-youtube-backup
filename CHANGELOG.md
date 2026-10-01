# 更新日志

## v1.0.1 — 2026-10-02

- 修复 OAuth 尚未检查配置就提示浏览器已打开的问题。
- 授权链接生成后始终提供「打开 Google 授权页面」和「复制授权链接」。
- 检查自动打开失败或异常，浏览器启动与回调监听分开进行。
- 授权链接仅保存在内存中；成功、拒绝、超时后自动清除。
- 增加真实 loopback 回调与 Chrome 页面回归测试，Google 响应使用合成数据。

## v1.0.0 — 2026-10-02

- Mac 本地 FastAPI 应用，中文界面与 start.command。
- yt-dlp + ffmpeg 下载，最高 1080p。
- 手动标题、固定 private、OAuth 本机保存。
- 8 MiB resumable upload、服务器进度查询、跨重启恢复。
- 上传确认后清理临时视频，失败后保留并支持重试。
- 手动 ASR 字幕检查，优先 ja，官方 API 获取 SRT / TXT。
- SQLite 历史，字幕复制和下载。
- 静态 GitHub Pages 介绍网站，不处理视频。
