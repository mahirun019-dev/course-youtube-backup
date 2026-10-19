"""Recoverable multi-source preparation; the existing worker uploads once."""
import json
import time
import re
from pathlib import Path
from . import config
from .database import sources_of
from .downloader import download, inspect_video, validate_url
from .errors import AppError
from .media import probe, merge_videos, ensure_space, task_guard, GIB


def task_path(value, folder):
    path = Path(value)
    if not path.is_absolute() or not path.is_relative_to(folder) or path == folder:
        raise AppError("任务临时文件路径无效，未读取或修改其他记录。")
    for entry in (path, *path.parents):
        if entry == folder.parent:
            break
        if entry.is_symlink():
            raise AppError("任务临时文件路径包含符号链接，未处理其他记录。")
    if not path.resolve().is_relative_to(folder.resolve()):
        raise AppError("任务临时文件路径无效，未读取或修改其他记录。")
    return path


def prepare_merged(job, db):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job["id"]):
        raise AppError("任务目录标识无效，未处理其他记录。")
    folder = config.TEMP / job["id"]
    if folder.is_symlink():
        raise AppError("任务目录为符号链接，未处理其他记录。")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with task_guard(folder):
        return _prepare_merged(job, db, folder)


def _prepare_merged(job, db, folder):
    sources = sources_of(job)
    if not 2 <= len(sources) <= 8:
        raise AppError("合并任务必须包含 2–8 个视频。")
    # An interrupted encoder's partial output is never an upload source.
    # Drop only this task's uncommitted output, preserving all downloaded parts.
    partial = folder / "merged.partial.mp4"
    if partial.is_symlink():
        raise AppError("合并临时文件为符号链接，未处理其他记录。")
    partial.unlink(missing_ok=True)
    def save(**values):
        db.update(job["id"], sources_json=json.dumps(sources, ensure_ascii=False), **values)
    save(state="downloading", progress=None, message="正在检查原视频信息与可用磁盘空间……")
    for n, source in enumerate(sources):
        path = task_path(source["path"], folder) if source.get("path") else None
        if path and path.is_file():
            info = probe(path)
            source.update(duration=info["duration"], estimated_bytes=info["size"])
        else:
            source.pop("path", None)
            if not source.get("estimated_bytes"):
                try:
                    source.update(inspect_video(validate_url(source["url"])))
                except AppError as exc:
                    raise AppError(f"第 {n + 1}/{len(sources)} 个视频：{exc.message}", exc.status) from None
        save()
    estimates = sum(s["estimated_bytes"] for s in sources)
    duration = sum(s.get("duration", 0) for s in sources)
    remaining = sum(s["estimated_bytes"] for s in sources if not s.get("path"))
    ensure_space(folder, int(remaining * 1.25 + max(estimates * 2, duration * 12_000_000 / 8)) + GIB)
    paths = []
    for n, source in enumerate(sources):
        path = task_path(source["path"], folder) if source.get("path") else None
        if not path or not path.is_file():
            part = folder / "parts" / f"{n + 1:02d}"
            if part.is_symlink() or (folder / "parts").is_symlink():
                raise AppError("分段目录为符号链接，未处理其他记录。")
            ensure_space(folder, int(source["estimated_bytes"] * 1.25) + GIB)
            save(progress=None, message=f"正在下载第 {n + 1}/{len(sources)} 个视频（最高 1080p）……")
            def metadata(title):
                source["title"] = title
                save()
            last = [0.0]
            def progress(value):
                if time.monotonic() - last[0] >= .5:
                    db.update(job["id"], progress=value)
                    last[0] = time.monotonic()
            try:
                path = task_path(download(validate_url(source["url"]), part, progress, metadata), part)
                probe(path)
            except AppError as exc:
                raise AppError(f"第 {n + 1}/{len(sources)} 个视频下载失败：{exc.message}", exc.status) from None
            source["path"] = str(path)
            save(progress=None)
        paths.append(path)
    save(state="merging", progress=None, message="全部下载完成。正在合并视频……")
    def stage(message):
        db.update(job["id"], message=message, progress=None)
    return merge_videos(paths, folder, stage)
