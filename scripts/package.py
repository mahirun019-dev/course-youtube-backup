#!/usr/bin/env python3
import stat
import re
import zipfile
from pathlib import Path
from safety import ROOT, git, scan, inspect
VERSION = re.search(r'^VERSION = "([0-9.]+)"', (ROOT/"app/config.py").read_text(), re.M).group(1)

def package(root=ROOT, output=None):
    scan(root)
    output = output or root.parent / f"course-youtube-backup-v{VERSION}.zip"
    names = [x for x in git("ls-files","-z",root=root).decode().split("\0") if x]
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as z:
        for name in names:
            content = (root/name).read_bytes()
            inspect(name,content)
            info = zipfile.ZipInfo("course-youtube-backup/"+name)
            info.create_system = 3
            info.external_attr = ((stat.S_IFREG | (0o755 if name=="start.command" else 0o644)) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info,content)
    with zipfile.ZipFile(output) as z:
        for name in z.namelist():
            inspect(name.removeprefix("course-youtube-backup/"),z.read(name))
        assert z.testzip() is None
    print(f"已生成并检查 ZIP：{output}（{len(names)} 个文件）")
    return output

if __name__ == "__main__":
    package()
