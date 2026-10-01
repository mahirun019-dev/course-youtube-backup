import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("COURSE_BACKUP_DATA", ROOT / "data")).resolve()
CONFIG = DATA / "config"
TEMP = DATA / "temp"
SUBTITLES = DATA / "subtitles"
HISTORY = DATA / "history"
YOUTUBE_PRIVACY = "private"
VERSION = "1.0.0"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]

def prepare():
    for p in (DATA, CONFIG, TEMP, SUBTITLES, HISTORY):
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.chmod(0o700)

def private_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)
