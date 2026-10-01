import logging
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from . import config
from .database import Database, public_job, now
from .downloader import validate_url, download, dependencies
from .captions import choose_asr, srt_to_txt, safe_name
from .youtube import YouTube
from .errors import AppError, friendly

log = logging.getLogger("course-backup")
config.prepare()
db = Database()
youtube = YouTube()
csrf = secrets.token_urlsafe(32)
work_lock = threading.Lock()
caption_locks = {}
lock_guard = threading.Lock()
auth_state = {"busy":False, "message":""}

@asynccontextmanager
async def lifespan(app):
    yield

app = FastAPI(title="Course YouTube Backup", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

@app.middleware("http")
async def local_only(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
        return JSONResponse({"message":"仅允许本地页面访问。"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(request.headers.get("x-local-token", ""), csrf):
        return JSONResponse({"message":"本地会话已刷新，请重新加载页面。"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response

@app.exception_handler(AppError)
async def app_error(request, exc):
    return JSONResponse({"message":exc.message}, status_code=exc.status)

@app.exception_handler(Exception)
async def unexpected(request, exc):
    log.error("本地请求失败：%s", type(exc).__name__)
    err = friendly(exc)
    return JSONResponse({"message":err.message}, status_code=err.status)

class BackupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str
    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        try:
            validate_url(value)
            return value.strip()
        except AppError as exc:
            raise ValueError(exc.message)
    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        value = value.strip()
        if not value or len(value) > 100 or any(ord(c)<32 for c in value) or "<" in value or ">" in value:
            raise ValueError("标题必须为 1–100 个字符，不能包含控制字符或 < >。")
        return value

class RetryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    restart_expired: bool = False

def owned_path(value, base):
    p = Path(value).resolve()
    if not p.is_relative_to(base.resolve()) or p == base.resolve():
        raise AppError("本地文件路径无效。")
    return p

def cleanup(id):
    job = db.get(id)
    if not job["video_id"] or job["state"] != "uploaded":
        raise AppError("尚未确认上传成功，不能删除临时视频。")
    folder = config.TEMP / id
    try:
        if folder.exists():
            shutil.rmtree(owned_path(str(folder), config.TEMP))
        db.update(id, temp_path="", session_uri="", message="上传完成。YouTube 正在生成自动字幕，请稍后手动检查。")
    except OSError:
        db.update(id, message="上传已完成，但临时文件删除失败。请检查目录权限后点击清理临时文件。")

def worker(id):
    try:
        job = db.get(id)
        youtube.credentials()  # Do not download gigabytes before discovering missing authorization.
        path = owned_path(job["temp_path"],config.TEMP) if job["temp_path"] else None
        if not path or not path.is_file():
            if job["session_uri"]:
                raise AppError("上传会话对应的视频文件缺失，不能安全恢复。请创建新备份。")
            db.update(id, state="downloading", progress=0, message="正在下载视频（最高 1080p）……")
            last = [0.0]
            def progress(p):
                if time.monotonic() - last[0] >= 0.5:
                    db.update(id, progress=p)
                    last[0] = time.monotonic()
            path = download(validate_url(job["url"]),config.TEMP / id,progress,lambda title: db.update(id,source_title=title))
            db.update(id,temp_path=str(path))
        db.update(id,state="uploading",progress=0,message="正在上传至 YouTube · 非公開（固定）……")
        job = db.get(id)
        video_id = youtube.upload(job,path,lambda **kw: db.update(id,**kw))
        # Commit the remote ID before deleting anything locally.
        db.update(id,video_id=video_id,uploaded_at=now(),state="uploaded",progress=100,caption_state="waiting")
        cleanup(id)
    except Exception as exc:
        log.error("备份任务失败：%s", type(exc).__name__)
        err = friendly(exc)
        try:
            job = db.get(id)
            msg = err.message + (" 临时文件已保留。" if (config.TEMP / id).exists() else "")
            db.update(id,state="failed" if not job["video_id"] else "uploaded",message=msg)
        except Exception:
            log.error("任务状态保存失败，请检查历史数据库。")
    finally:
        work_lock.release()

def launch(id):
    try:
        threading.Thread(target=worker,args=(id,),daemon=True).start()
    except Exception:
        work_lock.release()
        raise

@app.get("/api/status")
def status():
    return {"version":config.VERSION, "token":csrf, "dependencies":dependencies(),
        "credential_present":(config.CONFIG / "client_secret.json").exists(),
        "connected":(config.CONFIG / "token.json").exists(), "auth":auth_state.copy(),
        "database_error":db.error, "busy":work_lock.locked()}

@app.get("/api/jobs")
def jobs():
    return [public_job(j) for j in db.list()]

@app.post("/api/auth")
def auth():
    with lock_guard:
        if auth_state["busy"]:
            raise AppError("授权正在进行，请完成浏览器操作。",409)
        if work_lock.locked():
            raise AppError("备份正在进行，请完成后再重新授权。",409)
        auth_state.update(busy=True,message="请在打开的浏览器中完成 Google 授权。")
    def run():
        try:
            youtube.login()
            auth_state["message"] = "YouTube 授权完成。"
        except Exception as exc:
            auth_state["message"] = friendly(exc).message
        finally:
            auth_state["busy"] = False
    threading.Thread(target=run,daemon=True).start()
    return {"message":auth_state["message"]}

@app.post("/api/jobs",status_code=202)
def create(body:BackupInput):
    if auth_state["busy"]:
        raise AppError("请先完成 Google 授权。",409)
    youtube.credentials()
    if not work_lock.acquire(blocking=False):
        raise AppError("已有备份任务进行中，请等待完成。",409)
    id = uuid.uuid4().hex
    try:
        db.create(id,body.title,body.url)
    except Exception:
        work_lock.release()
        raise
    launch(id)
    return {"id":id}

@app.post("/api/jobs/{id}/retry",status_code=202)
def retry(id:str,body:RetryInput):
    job = db.get(id)
    if job["state"] != "failed" or job["video_id"]:
        raise AppError("只能重试失败且尚未确认上传的任务。",409)
    if auth_state["busy"]:
        raise AppError("请先完成 Google 授权。",409)
    youtube.credentials()
    if not work_lock.acquire(blocking=False):
        raise AppError("已有备份正在进行。",409)
    try:
        if job["session_expired"]:
            if not body.restart_expired:
                raise AppError("会话已过期。请先确认 YouTube Studio 没有重复视频，再选择重新上传。",409)
            db.update(id,session_uri="",session_expired=0)
        db.update(id,state="queued",message="正在重试……")
    except Exception:
        work_lock.release()
        raise
    launch(id)
    return {"id":id}

@app.post("/api/jobs/{id}/cleanup")
def clean(id:str):
    cleanup(id)
    return {"message":"临时文件清理已处理。"}

def caption_action(id, fetch):
    with lock_guard:
        lock = caption_locks.setdefault(id,threading.Lock())
    if not lock.acquire(blocking=False):
        raise AppError("该视频的字幕操作正在进行。",409)
    try:
        job = db.get(id)
        if not job["video_id"]:
            raise AppError("请先完成上传。")
        if fetch:
            if job["caption_state"] == "saved":
                return {"message":"字幕已保存在本地。"}
            if not job["caption_id"]:
                raise AppError("请先点击检查字幕。")
            srt = youtube.download_caption(job["caption_id"])
            txt = srt_to_txt(srt)
            stem = safe_name(job["title"])
            folder = config.SUBTITLES / id
            folder.mkdir(mode=0o700,exist_ok=True)
            srt_path, txt_path = folder / (stem+".srt"), folder / (stem+".txt")
            config.private_write(srt_path,srt)
            config.private_write(txt_path,txt)
            db.update(id,srt_path=str(srt_path),txt_path=str(txt_path),caption_state="saved",message="字幕已保存，可以复制或下载。")
            return {"message":"字幕已保存。"}
        # Manual only; enforce cooldown even for repeated clicks or API requests.
        if job["checked_at"]:
            from datetime import datetime, timezone
            if (datetime.now(timezone.utc)-datetime.fromisoformat(job["checked_at"])).total_seconds()<60:
                raise AppError("字幕检查间隔至少 60 秒，避免消耗配额。",429)
        db.update(id,checked_at=now())
        track = choose_asr(youtube.list_captions(job["video_id"]))
        if track:
            db.update(id,caption_id=track["id"],language=track["snippet"]["language"],caption_state="saved" if job["txt_path"] else "ready",message="自动字幕已生成。" if track["snippet"]["language"].split("-")[0]=="ja" else "未找到日语字幕，将使用其他语言的自动字幕。")
            return {"message":"自动字幕已生成。"}
        db.update(id,caption_state="saved" if job["txt_path"] else "waiting",message="API 尚未返回可用自动字幕。YouTube 可能仍在生成，也可能未生成；请稍后检查或打开 YouTube Studio。")
        return {"message":"尚未发现可用自动字幕。"}
    except Exception as exc:
        err = friendly(exc,caption=True)
        db.update(id,message=err.message)
        raise err
    finally:
        lock.release()

@app.post("/api/jobs/{id}/captions/check")
async def check_caption(id:str):
    return await run_in_threadpool(caption_action,id,False)

@app.post("/api/jobs/{id}/captions/fetch")
async def fetch_caption(id:str):
    return await run_in_threadpool(caption_action,id,True)

@app.get("/api/jobs/{id}/subtitle/{fmt}")
def subtitle(id:str,fmt:str):
    if fmt not in ("txt","srt"):
        raise AppError("只支持 TXT / SRT。",404)
    job = db.get(id)
    value = job[fmt+"_path"]
    if not value:
        raise AppError("请先获取字幕。",404)
    path = owned_path(value,config.SUBTITLES)
    if not path.is_file():
        raise AppError("字幕文件不存在，请在本地目录确认。",404)
    return FileResponse(path,media_type="text/plain; charset=utf-8",filename=path.name)

app.mount("/static",StaticFiles(directory=config.ROOT / "app/static"),name="static")

@app.get("/")
def home():
    return FileResponse(config.ROOT / "app/static/index.html")
