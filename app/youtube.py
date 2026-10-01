import json
import mimetypes
import random
import threading
import time
import requests
from google.auth.transport.requests import Request, AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import httplib2
from google_auth_httplib2 import AuthorizedHttp
from .config import CONFIG, SCOPES, YOUTUBE_PRIVACY, private_write
from .errors import AppError, api_error

AUTH_LOCK = threading.Lock()
CHUNK = 8 * 1024 * 1024
RETRYABLE = {429, 500, 502, 503, 504}

class YouTube:
    def credentials(self):
        with AUTH_LOCK:
            path = CONFIG / "token.json"
            if not path.exists():
                raise AppError("请先连接 YouTube，完成首次 Google 授权。", 401)
            try:
                creds = Credentials.from_authorized_user_file(str(path))
                if not creds.has_scopes(SCOPES):
                    raise AppError("授权范围不足，请重新连接 YouTube。", 401)
                if not creds.valid:
                    if not creds.refresh_token:
                        raise AppError("授权已失效，请重新连接 YouTube。", 401)
                    creds.refresh(Request())
                    private_write(path, creds.to_json())
                return creds
            except (ValueError, KeyError):
                raise AppError("本地 token 文件无效，请重新连接 YouTube。", 401)

    def login(self):
        if not AUTH_LOCK.acquire(blocking=False):
            raise AppError("Google 授权正在进行，请完成浏览器中的操作。", 409)
        try:
            path = CONFIG / "client_secret.json"
            if not path.exists():
                raise AppError("请把 Google 的 Desktop App OAuth 文件放到 data/config/client_secret.json，再点击连接。")
            path.chmod(0o600)
            try:
                info = json.loads(path.read_text())
                if "installed" not in info:
                    raise ValueError()
                flow = InstalledAppFlow.from_client_config(info, SCOPES)
            except (ValueError, KeyError):
                raise AppError("client_secret.json 无效：请选择 Desktop App 类型的 OAuth 客户端。")
            creds = flow.run_local_server(host="localhost", port=0, open_browser=True, timeout_seconds=180,
                access_type="offline", prompt="consent", authorization_prompt_message="请在浏览器完成 Google 授权。",
                success_message="授权完成。可以关闭此页面，回到课程视频备份工具。")
            private_write(CONFIG / "token.json", creds.to_json())
        except AppError:
            raise
        except Exception:
            raise AppError("Google 授权未完成或已超时。请确认测试用户设置，并再次连接。", 401)
        finally:
            AUTH_LOCK.release()

    def service(self):
        return build("youtube", "v3", http=AuthorizedHttp(self.credentials(), http=httplib2.Http(timeout=45)), cache_discovery=False)

    def list_captions(self, video_id):
        return self.service().captions().list(part="snippet", videoId=video_id).execute().get("items", [])

    def download_caption(self, caption_id):
        result = self.service().captions().download(id=caption_id, tfmt="srt").execute()
        return result.decode("utf-8-sig") if isinstance(result, bytes) else result

    @staticmethod
    def checked(response):
        if response.status_code >= 400:
            try:
                reason = response.json()["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError):
                reason = ""
            raise api_error(response.status_code, reason)
        return response

    @staticmethod
    def request(session, method, url, **kw):
        for attempt in range(5):
            try:
                response = session.request(method, url, timeout=(15,120), allow_redirects=False, **kw)
                if response.status_code not in RETRYABLE:
                    return response
            except requests.RequestException:
                if attempt == 4:
                    raise AppError("网络中断。上传会话与视频已保留；点击重试将查询服务器进度后继续。", 503)
            if attempt == 4:
                raise AppError("YouTube 暂时不可用或请求过多。上传会话与视频已保留，请稍后重试。", 503)
            time.sleep(min(16, 2**attempt) + random.random())

    def upload(self, job, path, update):
        size = path.stat().st_size
        if not size:
            raise AppError("临时视频文件为空，请重新创建备份。")
        mime = mimetypes.guess_type(path)[0] or "video/mp4"
        with AuthorizedSession(self.credentials()) as session:
            uri = job["session_uri"]
            if not uri:
                # Privacy is never read from the client. Every new upload is private.
                response = self.checked(self.request(session, "POST", "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType":"resumable", "part":"snippet,status"},
                    json={"snippet":{"title":job["title"], "categoryId":"27"}, "status":{"privacyStatus":YOUTUBE_PRIVACY}},
                    headers={"X-Upload-Content-Length":str(size), "X-Upload-Content-Type":mime}))
                uri = response.headers.get("Location")
                if not uri or not uri.startswith("https://www.googleapis.com/"):
                    raise AppError("YouTube 没有返回有效上传会话，视频已保留。", 502)
                update(session_uri=uri, session_expired=0)
            if not uri.startswith("https://www.googleapis.com/"):
                raise AppError("本地上传会话地址无效。请检查历史数据。")
            # Probe on every attempt, including after app restart. Never silently create a second video.
            response = self.request(session, "PUT", uri, data=b"", headers={"Content-Length":"0", "Content-Range":f"bytes */{size}"})
            if response.status_code in (404,410):
                update(session_expired=1)
            self.checked(response)
            previous_offset, stalls = -1, 0
            with path.open("rb") as f:
                while True:
                    if response.status_code in (200,201):
                        result = response.json()
                        if not result.get("id"):
                            raise AppError("上传结果缺少 Video ID。保留会话与文件，请重试确认。", 502)
                        if result.get("status",{}).get("privacyStatus") != YOUTUBE_PRIVACY:
                            raise AppError("YouTube 未确认视频为 private。文件已保留，请立即在 YouTube Studio 检查权限。", 502)
                        return result["id"]
                    if response.status_code != 308:
                        raise AppError("上传会话响应异常，已保留文件。请稍后重试。", 502)
                    acknowledged = response.headers.get("Range", "")
                    offset = int(acknowledged.rsplit("-",1)[1]) + 1 if acknowledged else 0
                    if offset < 0 or offset > size:
                        raise AppError("YouTube 返回无效上传进度，文件已保留。", 502)
                    stalls = stalls + 1 if offset <= previous_offset else 0
                    previous_offset = offset
                    if stalls >= 5:
                        raise AppError("上传连续多次没有进展。会话和视频已保留，请稍后重试。", 503)
                    if stalls:
                        time.sleep(min(16,2**stalls))
                    update(progress=min(99, offset / size * 100))
                    if offset >= size:
                        raise AppError("YouTube 正在确认上传结果，请稍后重试；不要创建重复上传。", 503)
                    f.seek(offset)
                    chunk = f.read(CHUNK)
                    try:
                        response = session.put(uri, data=chunk, timeout=(15,120), allow_redirects=False, headers={"Content-Type":mime,
                            "Content-Length":str(len(chunk)), "Content-Range":f"bytes {offset}-{offset+len(chunk)-1}/{size}"})
                        if response.status_code in RETRYABLE:
                            time.sleep(2)
                            response = self.request(session,"PUT",uri,data=b"",headers={"Content-Length":"0","Content-Range":f"bytes */{size}"})
                    except requests.RequestException:
                        response = self.request(session,"PUT",uri,data=b"",headers={"Content-Length":"0","Content-Range":f"bytes */{size}"})
                    if response.status_code in (404,410):
                        update(session_expired=1)
                    self.checked(response)
