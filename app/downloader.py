import re
import shutil
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import yt_dlp
from .errors import AppError

FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"

def validate_url(value):
    try:
        u = urlparse(value.strip())
        host = (u.hostname or "").lower()
        if u.scheme not in ("https", "http") or u.username or u.password or u.port not in (None, 443, 80):
            raise ValueError()
        if host == "youtu.be":
            id = u.path.strip("/")
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if u.path == "/watch":
                id = parse_qs(u.query).get("v", [""])[0]
            else:
                match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", u.path)
                id = match.group(1) if match else ""
        else:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", id):
            raise ValueError()
        return "https://www.youtube.com/watch?v=" + id
    except ValueError:
        raise AppError("YouTube URL 无效。请输入单个视频的 youtube.com/watch 或 youtu.be 链接。")

def dependencies():
    return {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "deno", "node")}

class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

def download(url, folder, progress, metadata):
    deps = dependencies()
    if not deps["ffmpeg"] or not deps["ffprobe"]:
        raise AppError("未找到 ffmpeg / ffprobe，请先运行 brew install ffmpeg。")
    if not deps["deno"] and not deps["node"]:
        raise AppError("YouTube 下载需要 JavaScript 运行时，请运行 brew install deno。")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            progress(min(99, d.get("downloaded_bytes",0) / total * 100) if total else 0)
    opts = {"format": FORMAT, "outtmpl": str(folder / "video.%(ext)s"), "merge_output_format":"mp4",
            "noplaylist":True, "logger":QuietLogger(), "progress_hooks":[hook], "retries":3,
            "fragment_retries":3, "socket_timeout":30, "cachedir":False,
            "js_runtimes": {"deno":{}} if deps["deno"] else {"node":{}}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("is_live"):
                raise AppError("请等待直播结束后再备份，避免无限下载。")
            metadata(info.get("title", ""))
            info = ydl.extract_info(url, download=True)
            candidates = [Path(info.get("filepath") or ydl.prepare_filename(info)).with_suffix(".mp4")]
            candidates += [Path(x["filepath"]) for x in info.get("requested_downloads",[]) if x.get("filepath")]
            candidates += list(folder.glob("video.*"))
            for p in candidates:
                if p.is_file() and p.suffix in (".mp4", ".mkv", ".webm", ".mov"):
                    # Do not treat an unmerged video-only/audio-only fragment as the final result.
                    if ".f" not in p.stem:
                        return p
            raise AppError("下载未产生完整视频文件，请重试。")
    except yt_dlp.utils.DownloadError:
        raise AppError("无法访问这个视频或下载失败。请确认 URL、观看权限和网络；需要登录、Private、DRM 或付费的视频不支持。可更新 yt-dlp 后重试。")
