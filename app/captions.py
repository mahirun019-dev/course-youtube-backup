import html
import re
from .errors import AppError

def normalized(value):
    return str(value or "").strip().casefold()


def is_japanese(language):
    return normalized(language).split("-")[0] == "ja"


def asr_tracks(items):
    # Live YouTube responses use lowercase "asr" as well as documented "ASR".
    return [x for x in items if isinstance(x, dict) and normalized(x.get("snippet", {}).get("trackKind")) == "asr"]


def choose_asr(items):
    tracks = [x for x in asr_tracks(items)
              if normalized(x["snippet"].get("status")) in ("", "serving")
              and x["snippet"].get("isDraft") is not True and x.get("id")]
    return next((x for x in tracks if is_japanese(x["snippet"].get("language"))), tracks[0] if tracks else None)


def srt_to_txt(srt):
    srt = srt.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = block.splitlines()
        idx = next((i for i,l in enumerate(lines) if re.match(r"^\d{2,}:\d{2}:\d{2}[,.]\d{3}\s+-->", l)), None)
        if idx is None:
            continue
        body = html.unescape(re.sub(r"<[^>]*>|\{\\[^}]*\}", "", "\n".join(lines[idx+1:]))).strip()
        if body:
            out.append(lines[idx].split(" -->")[0].split(",")[0].split(".")[0] + "\n" + body)
    if not out:
        raise AppError("YouTube 返回的字幕为空或不是有效 SRT，未保存无效文件。")
    return "\n\n".join(out) + "\n"

def safe_name(title):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", title).strip(" .")
    return (name or "字幕")[:70]
