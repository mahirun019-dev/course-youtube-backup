import logging
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from . import config
from .database import Database, public_job, now
from .downloader import validate_url, download, dependencies
from .captions import choose_asr, asr_tracks, is_japanese, normalized, srt_to_txt, safe_name
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
auth_state = {"busy":False, "message":"", "authorization_url":"", "browser_opened":None}

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

def job_lock(id):
    with lock_guard:
        return caption_locks.setdefault(id, threading.Lock())


@contextmanager
def operation(id):
    lock = job_lock(id)
    if not lock.acquire(blocking=False):
        raise AppError("该记录已有操作进行中，请稍后重试。",409)
    try:
        yield
    finally:
        lock.release()


def completed(job):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", job["video_id"] or "")) and job["state"] == "uploaded" and not job["remote_missing"] and not job["remote_deleted"]


class PrivacyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["public", "private"]
    confirmed: bool = False


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["local", "remote"]
    confirmed: bool = False
    confirmation_title: str = ""


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
        db.update(id,video_id=video_id,uploaded_at=now(),state="uploaded",progress=100,caption_state="waiting",
            visibility="private", visibility_uncertain=0, visibility_checked_at=now(), remote_upload_status="uploaded")
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
        "connected":(config.CONFIG / "token.json").exists(), "auth":auth_snapshot(),
        "database_error":db.error, "busy":work_lock.locked()}

@app.get("/api/jobs")
def jobs():
    return [public_job(j) for j in db.list()]

def auth_snapshot():
    with lock_guard:
        return {k:v for k,v in auth_state.items() if not k.startswith("_")}


@app.post("/api/auth")
def auth():
    with lock_guard:
        if auth_state["busy"]:
            raise AppError("授权正在进行，请使用当前授权链接。",409)
        if work_lock.locked():
            raise AppError("备份正在进行，请完成后再重新授权。",409)
        # Fail synchronously: never suggest opening a browser with missing credentials.
        youtube.oauth_config()
        attempt = object()
        auth_state.update(busy=True, message="正在准备 Google 授权链接……", authorization_url="", browser_opened=None, _attempt=attempt)

    def ready(url, opened):
        with lock_guard:
            if not auth_state["busy"] or auth_state.get("_attempt") is not attempt:
                return
            if auth_state["authorization_url"] and auth_state["authorization_url"] != url:
                return
            message = "授权链接已就绪，请点击下方按钮完成 Google 授权。"
            if opened is True:
                message = "已尝试打开默认浏览器。如未看到 Google 授权页，请点击下方按钮。"
            elif opened is False:
                message = "自动打开浏览器失败，请点击下方「打开 Google 授权页面」。"
            auth_state.update(authorization_url=url, browser_opened=opened, message=message)

    def run():
        try:
            youtube.login(on_ready=ready)
            message = "YouTube 授权完成。"
        except Exception as exc:
            message = friendly(exc).message
        finally:
            with lock_guard:
                auth_state.update(busy=False, message=message, authorization_url="", browser_opened=None)
    try:
        threading.Thread(target=run,daemon=True).start()
    except Exception:
        with lock_guard:
            auth_state.update(busy=False, authorization_url="", message="无法启动授权，请重试。")
        raise AppError("无法启动授权，请重试。", 503)
    return {"message":auth_snapshot()["message"]}

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
    with operation(id):
        return retry_locked(id,body)

def retry_locked(id,body):
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
    with operation(id):
        cleanup(id)
    return {"message":"临时文件清理已处理。"}

def caption_action(id, fetch):
    lock = job_lock(id)
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
        items = youtube.list_captions(job["video_id"])
        track = choose_asr(items)
        if track:
            language = track["snippet"].get("language", "")
            message = "自动字幕已生成。" if is_japanese(language) else "未找到可获取的日语轨道，将使用其他语言的自动字幕。"
            db.update(id, caption_id=track["id"], language=language,
                caption_state="saved" if job["txt_path"] else "ready", message=message)
            return {"message":message}
        detected = asr_tracks(items)
        if detected:
            states = sorted({normalized(x.get("snippet", {}).get("status")) or "未提供" for x in detected})
            draft = any(x.get("snippet", {}).get("isDraft") is True for x in detected)
            message = "API 已返回自动字幕轨道，但尚不可获取。状态：" + "、".join(states) + ("；包含草稿轨道。" if draft else "。")
            state = "processing"
        elif not items:
            message = "API 尚未同步：captions.list 当前返回空数组。请以 YouTube Studio 的字幕状态为准，稍后手动检查。"
            state = "api_pending"
        else:
            message = f"API 返回 {len(items)} 条字幕，但尚未返回 ASR 自动轨道。请以 YouTube Studio 的字幕状态为准，稍后手动检查。"
            state = "api_pending"
        db.update(id, caption_state="saved" if job["txt_path"] else state,
            caption_id=job["caption_id"] if job["txt_path"] else "", message=message)
        return {"message":message}
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

def record_visibility(id, video):
    job = db.get(id)
    if not video:
        db.update(id, visibility="unavailable", visibility_uncertain=0, visibility_checked_at=now(),
            remote_missing=1, state="orphan" if job["state"] == "uploaded" else job["state"],
            message="YouTube API 未找到此视频，已标为孤立记录；可仅删除本地记录。")
        return
    remote = video.get("status", {})
    visibility = remote.get("privacyStatus")
    if visibility not in ("private", "public", "unlisted"):
        db.update(id, visibility_uncertain=1)
        raise AppError("YouTube 未提供有效可见性，当前状态待确认。",502)
    state = job["state"]
    if remote.get("uploadStatus") in ("failed", "rejected", "deleted"):
        state = "orphan"
    elif state == "orphan" and remote.get("uploadStatus") in ("uploaded", "processed"):
        state = "uploaded"
    db.update(id, visibility=visibility, visibility_uncertain=0, visibility_checked_at=now(),
        remote_missing=0, remote_upload_status=remote.get("uploadStatus", ""), state=state)


@app.post("/api/visibility/sync")
def sync_visibility():
    jobs = [j for j in db.list() if re.fullmatch(r"[A-Za-z0-9_-]{11}", j["video_id"] or "") and j["state"] not in ("queued","downloading","uploading") and not j["remote_deleted"]]
    count = 0
    for start in range(0,len(jobs),50):
        locked = []
        try:
            for job in jobs[start:start+50]:
                lock = job_lock(job["id"])
                if lock.acquire(blocking=False):
                    locked.append((job,lock))
            if not locked:
                continue
            videos = {v["id"]:v for v in youtube.get_videos([j["video_id"] for j,_ in locked])}
            for job,_ in locked:
                record_visibility(job["id"], videos.get(job["video_id"]))
                count += 1
        except Exception as exc:
            for job,_ in locked:
                db.update(job["id"], visibility_uncertain=1)
            raise friendly(exc)
        finally:
            for _,lock in locked:
                lock.release()
    return {"message":f"已从 YouTube 核对 {count} 条记录的可见性。"}


@app.post("/api/jobs/{id}/privacy")
def privacy(id:str, body:PrivacyInput):
    if not body.confirmed:
        raise AppError("请先明确确认本次可见性操作。",400)
    with operation(id):
        job = db.get(id)
        if not completed(job):
            raise AppError("只有已成功上传的有效视频才能修改可见性。",409)
        if body.target == "public" and (job["caption_state"] not in ("ready","saved") or not job["caption_id"]):
            raise AppError("自动字幕生成并被 API 确认后才能临时公开。",409)
        # Mark uncertain BEFORE contacting Google: a lost response cannot leave a false Private badge.
        db.update(id, visibility_uncertain=1)
        try:
            before = youtube.get_video(job["video_id"])
            record_visibility(id,before)
            if not before or not completed(db.get(id)):
                raise AppError("YouTube 尚未确认有效上传，本地记录已保留。",409)
            if before["status"]["privacyStatus"] != body.target:
                db.update(id,visibility_uncertain=1)
                result = youtube.update_privacy(before,body.target)
                record_visibility(id,result)
            current = db.get(id)
            if current["visibility"] != body.target:
                raise AppError("YouTube 未接受目标可见性；页面保留 API 实际返回状态。",409)
            return {"message":"已设为公开，请在 Gemini 使用结束后恢复为非公开。" if body.target=="public" else "已恢复为非公开。", "job":public_job(current)}
        except Exception as exc:
            # Reconcile any ambiguous update, without sending a second write request.
            try:
                record_visibility(id,youtube.get_video(job["video_id"]))
            except Exception:
                db.update(id,visibility_uncertain=1)
            err = friendly(exc)
            db.update(id,message=err.message)
            raise err


def remove_local_data(job):
    id = job["id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", id):
        raise AppError("任务目录标识无效，未删除本地数据。")
    # Touch only this record's directories; recorded files must be contained in them.
    folders = (config.SUBTITLES / id, config.TEMP / id)
    for field,base in (("srt_path",config.SUBTITLES),("txt_path",config.SUBTITLES),("temp_path",config.TEMP)):
        if job[field]:
            path = owned_path(job[field],base)
            if not path.is_relative_to((base / id).resolve()):
                raise AppError("记录文件不属于该任务目录，未删除其他记录。")
    for folder,base in zip(folders,(config.SUBTITLES,config.TEMP)):
        if folder.is_symlink():
            raise AppError("任务目录为符号链接，未删除本地数据。")
        if folder.exists():
            owned_path(str(folder),base)
    for folder,base in zip(folders,(config.SUBTITLES,config.TEMP)):
        if folder.exists():
            shutil.rmtree(owned_path(str(folder),base))


@app.post("/api/jobs/{id}/delete")
def delete_record(id:str, body:DeleteInput):
    if not body.confirmed:
        raise AppError("请先确认删除记录。",400)
    with operation(id):
        job = db.get(id)
        if job["state"] in ("queued","downloading","uploading"):
            raise AppError("任务仍在进行中，不能删除。请等待任务结束。",409)
        if body.mode == "remote":
            if body.confirmation_title != job["title"]:
                raise AppError("危险操作需要二次确认并输入完整视频标题。",400)
            if not job["remote_deleted"]:
                if not completed(job):
                    raise AppError("此记录没有已确认上传的视频，请仅删除本地记录。",409)
                try:
                    if youtube.delete_video(job["video_id"]) is not True:
                        raise AppError("YouTube 没有确认删除成功，本地记录已保留。",502)
                except Exception as exc:
                    err = friendly(exc)
                    db.update(id,message="YouTube 删除失败，本地记录已保留。"+err.message)
                    raise err
                # Durable marker lets local cleanup be retried without deleting a second video.
                db.update(id,remote_deleted=1,visibility="deleted",visibility_uncertain=0,visibility_checked_at=now())
                job = db.get(id)
        try:
            remove_local_data(job)
            db.delete(id)
        except Exception as exc:
            err = friendly(exc)
            message = "YouTube 已删除，但本地清理未完成。记录已保留，请重试仅删除本地记录。" if job["remote_deleted"] else "本地清理失败，记录已保留，请检查文件权限后重试。"
            db.update(id,message=message)
            raise AppError(message,err.status)
        return {"message":"已删除 YouTube 视频和本地记录。" if body.mode=="remote" else "已删除本地记录；YouTube 视频保持不变。"}


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
