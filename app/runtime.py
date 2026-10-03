"""macOS process identity helpers. Never read OAuth files."""
import json
import re
import subprocess
from pathlib import Path

def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ""

def started(pid):
    return command("/bin/ps", "-p", str(pid), "-o", "lstart=")

def owns(pid, root, start=None):
    if not isinstance(pid,int) or pid <= 1:
        return False
    cmd = command("/bin/ps", "-p", str(pid), "-o", "command=")
    cwd = command("/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn")
    return bool(re.search(r"(?:^|\s)-m\s+app\.launcher(?:\s|$)",cmd)
        and "n"+str(Path(root).resolve()) in cwd.splitlines()
        and (start is None or start == started(pid)))

def listener():
    value = command("/usr/sbin/lsof", "-t", "-iTCP:8000", "-sTCP:LISTEN")
    return sorted({int(x) for x in value.splitlines() if x.isdigit()})

def metadata(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}
