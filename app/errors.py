import json
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

class AppError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)

def api_error(status, reason="", caption=False):
    if reason in ("quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"):
        return AppError("YouTube API 配额或上传额度已用完。请在 Google Cloud Console 查看额度，稍后重试。", 429)
    if status == 401 or reason in ("invalid_grant", "authError"):
        return AppError("Google 授权已过期或被撤销，请重新连接 YouTube。", 401)
    if status == 403:
        msg = "字幕 API 权限不足（403）。请确认登录的是视频所属频道；自动字幕可能无法通过 API 下载，可在 YouTube Studio 查看并下载字幕。" if caption else "YouTube 拒绝此操作。请确认已启用 API、授权范围及频道权限。"
        return AppError(msg, 403)
    if status in (404, 410):
        return AppError("字幕或视频不存在，或上传会话已过期。保留的视频可重新上传；新会话会从头开始，请先确认频道没有重复视频。", 409)
    if reason == "couldNotConvert":
        return AppError("YouTube 暂时无法将此字幕转换为 SRT。请稍后重试或在 YouTube Studio 下载。")
    return AppError(f"YouTube API 操作失败（HTTP {status}）。请稍后重试。", 502)

def friendly(exc, caption=False):
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, RefreshError):
        return AppError("Google 授权已失效，请重新连接 YouTube。", 401)
    if isinstance(exc, HttpError):
        try:
            reason = json.loads(exc.content)["error"]["errors"][0]["reason"]
        except (ValueError, KeyError, IndexError, TypeError):
            reason = ""
        return api_error(exc.resp.status, reason, caption)
    if isinstance(exc, OSError):
        return AppError("网络或本地文件操作失败。请检查网络、磁盘空间及目录权限后重试。", 503)
    return AppError("操作未完成。请检查本地日志或重新启动后重试。", 500)
