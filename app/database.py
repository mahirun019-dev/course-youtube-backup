import sqlite3
from datetime import datetime, timezone
from .config import HISTORY
from .errors import AppError

FIELDS = {"source_title", "state", "progress", "message", "temp_path", "video_id", "uploaded_at", "caption_state", "caption_id", "language", "srt_path", "txt_path", "session_uri", "session_expired", "checked_at"}

def now():
    return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path=None):
        self.path = path or HISTORY / "history.sqlite3"
        self.error = None
        try:
            with self.connect() as c:
                if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("integrity")
                c.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, created_at TEXT NOT NULL,
                source_title TEXT DEFAULT '', state TEXT DEFAULT 'queued', progress REAL DEFAULT 0,
                message TEXT DEFAULT '', temp_path TEXT DEFAULT '', video_id TEXT DEFAULT '',
                uploaded_at TEXT DEFAULT '', caption_state TEXT DEFAULT 'waiting', caption_id TEXT DEFAULT '',
                language TEXT DEFAULT '', srt_path TEXT DEFAULT '', txt_path TEXT DEFAULT '',
                session_uri TEXT DEFAULT '', session_expired INTEGER DEFAULT 0, checked_at TEXT DEFAULT '')""")
                c.execute("UPDATE jobs SET state='failed', message='上次任务被中断。已保留临时文件；可重试。' WHERE state IN ('queued','downloading','uploading')")
            self.path.chmod(0o600)
        except sqlite3.DatabaseError:
            self.error = "历史数据库损坏或不可读取。原文件已保留，请退出后备份 data/history，再移走损坏数据库后重启。"

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def run(self, sql, args=(), read=False):
        if self.error:
            raise AppError(self.error, 503)
        try:
            with self.connect() as c:
                c.row_factory = sqlite3.Row
                result = c.execute(sql, args)
                return [dict(r) for r in result.fetchall()] if read else None
        except sqlite3.DatabaseError:
            raise AppError("历史数据库读写失败。请检查磁盘空间、目录权限；原文件已保留。", 503)

    def create(self, id, title, url):
        self.run("INSERT INTO jobs (id,title,url,created_at) VALUES (?,?,?,?)", (id,title,url,now()))

    def update(self, id, **fields):
        if not fields or not fields.keys() <= FIELDS:
            raise ValueError("invalid database fields")
        self.run("UPDATE jobs SET " + ",".join(k+"=?" for k in fields) + " WHERE id=?", (*fields.values(),id))

    def get(self, id):
        rows = self.run("SELECT * FROM jobs WHERE id=?", (id,), True)
        if not rows:
            raise AppError("找不到该任务。", 404)
        return rows[0]

    def list(self):
        return self.run("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200", read=True)

def public_job(job):
    return {k:v for k,v in job.items() if k not in {"session_uri", "temp_path", "srt_path", "txt_path"}} | {"has_subtitles": bool(job["srt_path"] and job["txt_path"]), "has_temp": bool(job["temp_path"])}
