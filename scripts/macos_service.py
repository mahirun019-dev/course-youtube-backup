#!/usr/bin/env python3
"""Start/stop only this project's local server; optional per-user LaunchAgent."""
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import config
from app.runtime import owns,listener,metadata

class ServiceError(Exception):
    pass

def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/status",timeout=2) as response:
            value = json.load(response)
        return value if isinstance(value,dict) and value.get("version") else None
    except (OSError,ValueError):
        return None

def owned_pid():
    candidates = listener()
    info = metadata(config.CONFIG / "server-runtime.json")
    if candidates:
        if len(candidates) != 1 or not owns(candidates[0],ROOT):
            raise ServiceError("端口 8000 被其他程序或其他项目占用；没有停止或覆盖它。")
        if info.get("pid") == candidates[0] and info.get("data") != str(config.DATA):
            raise ServiceError("现有服务使用不同的数据目录；没有重复启动或停止它，请沿用原启动配置。")
        return candidates[0]
    pid = info.get("pid")
    if info.get("project") == str(ROOT) and info.get("data") == str(config.DATA) and owns(pid,ROOT,info.get("started")):
        return pid
    return None

def require_idle(info):
    if not info:
        raise ServiceError("服务状态尚未确认，请稍后再停止；没有强制终止进程。")
    if info.get("busy") or info.get("operations_busy") or info.get("auth",{}).get("busy"):
        raise ServiceError("备份、授权或视频操作正在进行。请完成后再停止或退出，数据仍保留。")

def label():
    return "dev.course-youtube-backup."+hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]

def agent_path():
    return Path.home()/"Library/LaunchAgents"/(label()+".plist")

def agent_config():
    return {"Label":label(),"ProgramArguments":[str(ROOT/".venv/bin/python"),"-m","app.launcher"],
        "WorkingDirectory":str(ROOT),"RunAtLoad":True,"KeepAlive":False,"ProcessType":"Background",
        "EnvironmentVariables":{"COURSE_BACKUP_NO_BROWSER":"1","COURSE_BACKUP_DATA":str(config.DATA),"PATH":os.environ.get("PATH","")},
        # launchd cannot open Documents log paths on behalf of this user process.
        # The launcher opens its own private log after it starts.
        "StandardOutPath":"/dev/null","StandardErrorPath":"/dev/null"}

def agent_enabled():
    path = agent_path()
    if not path.exists():
        return False
    try:
        info = plistlib.loads(path.read_bytes())
    except (OSError,ValueError):
        raise ServiceError("登录启动配置不可读取；没有覆盖或删除原文件。")
    expected = agent_config()
    if any(info.get(k) != expected[k] for k in ("Label","ProgramArguments","WorkingDirectory")):
        raise ServiceError("登录启动配置不属于当前项目；没有覆盖或删除它。")
    return True

def launchctl(*args):
    return subprocess.run(["/bin/launchctl",*args],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode

def target():
    return "gui/"+str(os.getuid())+"/"+label()

def snapshot():
    pid = owned_pid()
    info = health() if pid else None
    return {"ok":True,"running":bool(pid and info),"pid":pid,"login_enabled":agent_enabled(),
        "busy":bool(info and (info.get("busy") or info.get("operations_busy") or info.get("auth",{}).get("busy"))),
        "message":"服务已运行。" if pid and info else "服务未运行。"}

def wait_ready(pid=None):
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        current = owned_pid()
        if current and health():
            return {**snapshot(),"message":"本地服务已就绪。"}
        if pid is not None and not owns(pid,ROOT):
            break
        time.sleep(.25)
    raise ServiceError("服务未能启动。请查看项目 data/history/service.log；没有删除数据或启动重复实例。")

def start():
    if owned_pid():
        return wait_ready()
    if agent_enabled():
        if launchctl("print",target()) != 0:
            if launchctl("bootstrap","gui/"+str(os.getuid()),str(agent_path())) != 0:
                raise ServiceError("无法载入登录服务，请关闭登录启动后再试。")
        elif launchctl("kickstart",target()) != 0:
            raise ServiceError("无法启动登录服务，请关闭登录启动后再试。")
        return wait_ready()
    env = {**os.environ,"COURSE_BACKUP_NO_BROWSER":"1","COURSE_BACKUP_DATA":str(config.DATA)}
    with (config.HISTORY/"service.log").open("ab") as log:
        process = subprocess.Popen([str(ROOT/".venv/bin/python"),"-m","app.launcher"],cwd=ROOT,env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return wait_ready(process.pid)

def stop():
    pid = owned_pid()
    if not pid:
        return {**snapshot(),"message":"服务已停止。"}
    require_idle(health())
    # Check start time and ownership again immediately before signalling. No killall/pkill.
    from app.runtime import started
    stamp = started(pid)
    if not owns(pid,ROOT,stamp):
        raise ServiceError("进程身份发生变化，没有停止其他程序。")
    os.kill(pid,signal.SIGTERM)
    for _ in range(80):
        if not owns(pid,ROOT,stamp):
            return {**snapshot(),"message":"服务已安全停止。"}
        time.sleep(.1)
    raise ServiceError("服务仍在安全退出，请稍后重试；没有强制杀死进程。")

def login_enable():
    if agent_enabled():
        return {**snapshot(),"message":"登录启动已经开启。"}
    path = agent_path();path.parent.mkdir(parents=True,exist_ok=True)
    config.private_write(path,plistlib.dumps(agent_config()).decode())
    if launchctl("bootstrap","gui/"+str(os.getuid()),str(path)) != 0:
        path.unlink(missing_ok=True)
        raise ServiceError("macOS 未接受登录启动配置，已恢复为关闭。")
    return {**snapshot(),"message":"登录启动已开启；下次登录只启动后台服务，不打开浏览器。"}

def login_disable():
    if not agent_enabled():
        return {**snapshot(),"message":"登录启动已经关闭。"}
    if owned_pid():
        require_idle(health())
    if launchctl("print",target()) == 0 and launchctl("bootout",target()) != 0:
        raise ServiceError("无法卸载登录启动；配置保留，请稍后重试。")
    agent_path().unlink()
    return {**snapshot(),"message":"登录启动已关闭；需要时可双击 App 启动。"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action",choices=("start","stop","status","login-enable","login-disable","agent-plist"))
    action = parser.parse_args().action
    config.prepare()
    log = config.HISTORY/"service.log";log.touch(mode=0o600,exist_ok=True);log.chmod(0o600)
    try:
        if action == "agent-plist":
            sys.stdout.buffer.write(plistlib.dumps(agent_config()));return
        with (config.CONFIG/"control.lock").open("a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            function = {"start":start,"stop":stop,"status":snapshot,"login-enable":login_enable,"login-disable":login_disable}[action]
            result = function()
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc,ServiceError) else "本地服务操作失败；请检查目录权限和运行环境，原数据保留。"
        print(json.dumps({"ok":False,"message":message},ensure_ascii=False));sys.exit(1)

if __name__ == "__main__":
    os.umask(0o077)
    main()
