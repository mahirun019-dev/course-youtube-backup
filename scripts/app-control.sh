#!/bin/zsh
# Non-interactive counterpart to start.command; called by the native App.
set -eu
cd "${0:A:h:h}"
export PATH="$PWD/.venv/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.deno/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
umask 077
fail() { printf '{"ok":false,"message":"%s"}\n' "$1"; exit 1; }
action="${1:-status}"
if [[ "$action" == start || "$action" == login-enable ]]; then
  command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || fail "需要安装 ffmpeg，请参考项目 README 的首次配置说明。"
  command -v deno >/dev/null || command -v node >/dev/null || fail "需要安装 Deno 或 Node，请参考 README 的首次配置说明。"
  if [[ ! -x .venv/bin/python ]]; then
    command -v python3 >/dev/null || fail "需要 Python 3.10+，请参考 README 的首次配置说明。"
    python3 -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null || fail "需要 Python 3.10 或更新版本。"
    python3 -m venv .venv >/dev/null 2>&1 || fail "无法创建 Python 环境，请检查项目目录写入权限。"
  fi
  if ! .venv/bin/python -c 'import fastapi,uvicorn,yt_dlp,googleapiclient,google.auth,google_auth_oauthlib,requests' >/dev/null 2>&1; then
    mkdir -p data/history
    .venv/bin/python -m pip install -r requirements.txt >data/history/setup.log 2>&1 || fail "依赖安装失败，请检查网络并参考 data/history/setup.log。"
  fi
fi
[[ -x .venv/bin/python ]] || fail "本地 Python 环境尚未创建，请先双击 App 启动。"
exec .venv/bin/python scripts/macos_service.py "$action"
