"""Local media inspection and ordered concatenation. Never accesses OAuth."""
import json
import math
import shutil
import subprocess
import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
from fractions import Fraction
from pathlib import Path
from .errors import AppError

GIB = 1024 ** 3
_guard = ContextVar("merge_task_guard", default=None)

@contextmanager
def task_guard(folder):
    folder = Path(folder).resolve()
    existing = _guard.get()
    if existing and existing[0] == folder:
        yield existing[1]
        return
    lock_path = folder / ".merge.lock"
    if lock_path.is_symlink():
        raise AppError("任务锁为符号链接，未处理其他记录。")
    with lock_path.open("a") as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AppError("该任务的本地处理仍在运行，请稍后重试；没有启动第二个合并进程。", 409) from None
        token = _guard.set((folder, lock.fileno()))
        try:
            yield lock.fileno()
        finally:
            _guard.reset(token)
        # Close rather than explicitly unlock: an inherited child descriptor
        # keeps ownership if the server crashes while ffmpeg is still running.

def number(value, default=0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default

def rate(value):
    try:
        return float(Fraction(value))
    except (ValueError, TypeError, ZeroDivisionError):
        return 0

def probe(path):
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-show_data_hash", "sha256", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError()
        info = json.loads(result.stdout)
        video = next(s for s in info["streams"] if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic"))
        audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
        vd = number(video.get("duration"), number(info["format"].get("duration")))
        offset = number((audio or {}).get("start_time")) - number(video.get("start_time"))
        container_end = number(info["format"].get("duration")) + number(info["format"].get("start_time")) - number(video.get("start_time"))
        duration = max(vd, number((audio or {}).get("duration")) + offset, container_end)
        if not 0 < duration < 7 * 24 * 3600 or not all(0 < int(video[k]) <= 16384 for k in ("width", "height")):
            raise ValueError()
        return {"video": video, "audio": audio, "duration": duration, "video_duration": vd,
                "audio_offset": offset, "size": Path(path).stat().st_size}
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        raise AppError("无法读取视频或视频流损坏。已保留各部分文件，请检查文件和 ffprobe 后重试。") from None

def rotation(video):
    return number(next((s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s), video.get("tags", {}).get("rotate", 0))) % 360

def copy_compatible(infos):
    """Conservative: uncertain timestamps/configuration take the normalized path."""
    video_keys = ("codec_name", "profile", "level", "width", "height", "pix_fmt", "sample_aspect_ratio",
                  "field_order", "r_frame_rate", "avg_frame_rate", "time_base", "extradata_hash",
                  "color_range", "color_space", "color_transfer", "color_primaries")
    audio_keys = ("codec_name", "profile", "sample_fmt", "sample_rate", "channels", "channel_layout", "time_base", "extradata_hash")
    signatures = []
    for info in infos:
        v, a = info["video"], info["audio"]
        if not a or v.get("codec_name") != "h264" or a.get("codec_name") != "aac" or v.get("pix_fmt") != "yuv420p":
            return False
        if not v.get("extradata_hash") or not a.get("extradata_hash") or rotation(v):
            return False
        if int(v["width"]) > 1920 or int(v["height"]) > 1080 or abs(info["audio_offset"]) > .05:
            return False
        if abs(number(a.get("duration")) - info["video_duration"]) > .08:
            return False
        if not rate(v.get("avg_frame_rate")) or v.get("r_frame_rate") != v.get("avg_frame_rate"):
            return False
        signatures.append((tuple(v.get(k) for k in video_keys), tuple(a.get(k) for k in audio_keys)))
    return bool(signatures) and all(signature == signatures[0] for signature in signatures)

def ensure_space(folder, needed):
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise AppError(f"磁盘空间不足：预计还需 {needed / GIB:.1f} GiB，可用 {free / GIB:.1f} GiB。已完成的文件保留，释放空间后重试。")

def run_ffmpeg(args, folder, label):
    log = folder / "merge.log"
    with log.open("ab") as output:
        log.chmod(0o600)
        output.write(("\n" + label + "\n").encode()); output.flush()
        try:
            result = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error", *args],
                stdout=subprocess.DEVNULL, stderr=output, timeout=24 * 3600,
                pass_fds=(_guard.get()[1],) if _guard.get() else ())
        except subprocess.TimeoutExpired:
            raise AppError("ffmpeg 处理超时，各部分文件已保留；请查看该任务目录的 merge.log 后重试。") from None
    if result.returncode:
        raise AppError(f"{label}失败（ffmpeg {result.returncode}）。各部分文件已保留；具体错误见该任务目录的 merge.log，可重试合并。")

def validate_output(path, expected, count, folder):
    info = probe(path)
    if not info["audio"] or abs(info["duration"] - expected) > max(.35, count * .10):
        raise AppError("合并后音视频流或总时长校验失败，各部分文件已保留。请重试合并。")
    if abs(info["audio_offset"]) > .1 or abs(number(info["audio"].get("duration")) - info["video_duration"]) > .25:
        raise AppError("合并后音视频时长不一致，各部分文件已保留，请重试合并。")
    # Decode to a null sink: no second video file or full-frame buffer on disk.
    run_ffmpeg(["-xerror", "-err_detect", "explode", "-threads", "2", "-i", str(path),
                "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], folder, "合并文件解码校验")
    return info

def normalized_graph(infos):
    dimensions = []
    for info in infos:
        v = info["video"]
        sar = rate(v.get("sample_aspect_ratio", "1:1").replace(":", "/")) or 1
        w, h = int(v["width"]) * sar, int(v["height"])
        if rotation(v) in (90, 270):
            w, h = h, w
        dimensions.append((w, h))
    width = min(1920, math.ceil(max(w for w, _ in dimensions) / 2) * 2)
    height = min(1080, math.ceil(max(h for _, h in dimensions) / 2) * 2)
    fps = min(60, max(rate(i["video"].get("avg_frame_rate")) or 30 for i in infos))
    filters, labels = [], []
    for n, info in enumerate(infos):
        duration = info["duration"]
        filters.append(f"[{n}:v:0]setpts=PTS-STARTPTS,scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.6f},format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration={duration:.6f},trim=duration={duration:.6f}[v{n}]")
        if info["audio"]:
            audio = f"[{n}:a:0]asetpts=PTS-STARTPTS+({info['audio_offset']:.6f})/TB,aresample=48000:async=1:first_pts=0,"
        else:
            audio = "anullsrc=r=48000:cl=stereo,"
        filters.append(audio + f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[a{n}]")
        labels.append(f"[v{n}][a{n}]")
    filters.append("".join(labels) + f"concat=n={len(infos)}:v=1:a=1[v][a]")
    return ";".join(filters)

def merge_videos(paths, folder, stage):
    with task_guard(folder):
        return _merge_videos(paths, folder, stage)

def _merge_videos(paths, folder, stage):
    folder = Path(folder)
    infos = [probe(path) for path in paths]
    expected = sum(i["duration"] for i in infos)
    output = folder / "merged.mp4"
    partial = folder / "merged.partial.mp4"
    if output.is_file():
        # Handles a restart between atomic output rename and saving temp_path.
        try:
            validate_output(output, expected, len(paths), folder)
            return output
        except AppError:
            output.unlink()
    direct = copy_compatible(infos)
    reserve = sum(i["size"] for i in infos)
    ensure_space(folder, int(reserve * 1.2) + GIB)
    if direct:
        stage("正在合并视频（编码兼容，直接拼接，不重新编码）……")
        manifest = folder / "concat.txt"
        def quoted(path):
            return str(Path(path).resolve()).replace("'", "'\\''")
        manifest.write_text("ffconcat version 1.0\n" + "".join(f"file '{quoted(p)}'\n" for p in paths))
        manifest.chmod(0o600)
        try:
            run_ffmpeg(["-f", "concat", "-safe", "0", "-protocol_whitelist", "file,pipe", "-i", str(manifest),
                "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", str(partial)], folder, "直接拼接")
            stage("合并完成，正在校验音视频……")
            validate_output(partial, expected, len(paths), folder)
        except AppError:
            direct = False
    if not direct:
        partial.unlink(missing_ok=True)
        ensure_space(folder, int(max(reserve * 2, expected * 12_000_000 / 8)) + GIB)
        stage("正在合并视频（统一 H.264/AAC，等比缩放与填充，保留完整画面）……")
        inputs = []
        for path in paths:
            inputs += ["-threads", "2", "-protocol_whitelist", "file,pipe", "-i", str(path)]
        run_ffmpeg([*inputs, "-filter_complex_threads", "1", "-filter_complex", normalized_graph(infos),
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "4",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(partial)], folder, "统一编码合并")
        stage("合并完成，正在校验音视频……")
        validate_output(partial, expected, len(paths), folder)
    partial.replace(output)
    return output
