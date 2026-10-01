#!/bin/zsh
set -eu
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
umask 077
trap 'print "启动失败，请查看上面的提示。"; read "?按 Enter 关闭窗口。"' ZERR
if ! command -v python3 >/dev/null; then
  print "请先安装 Python 3.10+：brew install python"
  read "?按 Enter 关闭窗口。"; exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,10), "需要 Python 3.10+"'
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,yt_dlp,googleapiclient,google.auth,google_auth_oauthlib,requests' 2>/dev/null; then
  print "首次启动：正在安装 Python 依赖……"
  .venv/bin/python -m pip install -r requirements.txt
fi
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  print "需要 ffmpeg，请在 Terminal 执行：brew install ffmpeg"
  read "?按 Enter 关闭窗口。"; exit 1
fi
if ! command -v deno >/dev/null && ! command -v node >/dev/null; then
  print "需要 JavaScript 运行时，请执行：brew install deno"
  read "?按 Enter 关闭窗口。"; exit 1
fi
.venv/bin/python -m app.launcher
