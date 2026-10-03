#!/usr/bin/env python3
"""Check index + Git history without exposing secret contents."""
import re
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAD_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".srt", ".vtt", ".sbv", ".sqlite", ".sqlite3", ".db", ".zip", ".pyc"}
SECRET = re.compile(rb"(?:GOCSPX-[A-Za-z0-9_-]{12,}|ya29\.[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
EXAMPLE = {"installed":{"client_id":"YOUR_CLIENT_ID.apps.googleusercontent.com", "project_id":"YOUR_PROJECT_ID",
    "auth_uri":"https://accounts.google.com/o/oauth2/auth", "token_uri":"https://oauth2.googleapis.com/token",
    "client_secret":"PLACEHOLDER_ONLY", "redirect_uris":["http://localhost"]}}

def credential_json(value):
    if isinstance(value,dict):
        return any((k in {"client_secret","refresh_token","access_token","token","private_key"} and bool(v))
            or credential_json(v) for k,v in value.items())
    return isinstance(value,list) and any(credential_json(v) for v in value)

def git(*args,root=ROOT):
    return subprocess.check_output(["git",*args],cwd=root)

def safe_name(name):
    p = Path(name)
    if any(x in {"data", ".venv", "__pycache__", ".git", ".pytest_cache", "work"} for x in p.parts):
        return False
    if any(x.endswith(".app") for x in p.parts):
        return False
    if p.suffix.lower() in BAD_SUFFIXES or ".sqlite" in p.name:
        return False
    if p.name == "client_secret.example.json":
        return True
    return not (p.name.startswith(("client_secret", "token", "credentials", ".env")) or p.name.endswith(".log"))

def inspect(name, content):
    if not safe_name(name):
        raise ValueError("不允许发布的文件：" + name)
    if SECRET.search(content):
        raise ValueError("检测到疑似真实凭据，拒绝发布：" + name)
    if Path(name).suffix.lower() == ".json":
        try:
            value = json.loads(content)
        except (ValueError,UnicodeDecodeError):
            if name == "client_secret.example.json":
                raise ValueError("示例配置必须仅含假 placeholder。")
            return
        if name == "client_secret.example.json":
            if value != EXAMPLE:
                raise ValueError("示例配置必须仅含假 placeholder。")
        elif credential_json(value):
            raise ValueError("检测到凭据 JSON，拒绝发布：" + name)

def scan(root=ROOT,history=True):
    names = git("ls-files", "-z",root=root).decode().split("\0")
    for name in filter(None,names):
        # Scan the staged version AND working file: ignore cannot hide staged secrets.
        inspect(name,git("show",":"+name,root=root))
        file = root/name
        if file.exists():
            inspect(name,file.read_bytes())
    if history:
        for commit in git("rev-list","--all",root=root).decode().splitlines():
            files = git("ls-tree","-r","--name-only","-z",commit,root=root).decode().split("\0")
            for name in filter(None,files):
                inspect(name,git("show",commit+":"+name,root=root))
    return len(list(filter(None,names)))

if __name__ == "__main__":
    try:
        print(f"安全检查通过：{scan()} 个追踪文件，含 index 与全部 Git 历史。")
    except (ValueError,subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
