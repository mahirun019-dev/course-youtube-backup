# Course YouTube Backup v1.2.0

双击「课程视频备份.app」即可使用，无需每天打开 Terminal 或运行 start.command。

- 后台启动现有 FastAPI，服务就绪后打开默认浏览器；已运行时只打开页面，不重复启动。
- App 提供停止服务，关闭窗口或 Command+Q 会先安全停止；备份/授权/视频操作忙碌时拒绝退出。
- 保留现有项目 data/ 中的 OAuth、数据库、字幕、历史和临时文件，不迁移或重置。
- 可选登录启动在 App 中明确确认后开启，默认关闭；支持关闭，KeepAlive=false，不自动拉起，不自动公开任何视频。
- 网页 UI 和现有下载、上传、字幕、Gemini、删除业务保持现状。

Release ZIP 包含 Apple Silicon / Intel universal 原生 App，要求 macOS 13+。App 为本地 ad-hoc 签名，未做 Apple 公证；首次打开可能需在系统设置的隐私与安全性中确认。Python、ffmpeg、Deno/Node 的首次环境配置仍按 README；日常使用不需要 Terminal。

发布包不包含 OAuth 凭据、个人课程、字幕、数据库或 .venv。start.command 保留为备用入口。

验证：139 项自动测试通过；实际 Finder 冷启动、重复双击复用 PID、Safari 自动打开、停止按钮与关闭 App 无进程残留；原 OAuth/历史/字幕保留。真实临时 LaunchAgent 两次载入启动/停止通过，模拟重新登录和旧 PID。未实际重启 Mac，日常登录启动保持关闭。
