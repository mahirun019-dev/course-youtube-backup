#!/usr/bin/env python3
import stat
import re
import zipfile
import plistlib
import subprocess
import shutil
import tempfile
from pathlib import Path
from safety import ROOT, git, scan, inspect
VERSION = re.search(r'^VERSION = "([0-9.]+)"', (ROOT/"app/config.py").read_text(), re.M).group(1)

def package(root=ROOT, output=None):
    scan(root)
    output = output or root.parent / f"course-youtube-backup-v{VERSION}.zip"
    names = [x for x in git("ls-files","-z",root=root).decode().split("\0") if x]
    bundle = root/"课程视频备份.app"
    bundle_names = []
    bundle_contents = {}
    if bundle.exists():
        info = plistlib.loads((bundle/"Contents/Info.plist").read_bytes())
        if info.get("CFBundleShortVersionString") != VERSION:
            raise ValueError("Mac App 版本与源码不一致，请先重新构建。")
        allowed = {"Contents/Info.plist","Contents/PkgInfo","Contents/MacOS/CourseBackup","Contents/_CodeSignature/CodeResources"}
        for path in bundle.rglob("*"):
            if path.is_symlink():
                raise ValueError("App 包含符号链接，拒绝打包。")
            if path.is_file():
                if path.relative_to(bundle).as_posix() not in allowed:
                    raise ValueError("App 包含非构建文件，拒绝打包。")
                bundle_names.append(path.relative_to(root).as_posix())
        # Finder/FileProvider can add FinderInfo to a Documents bundle. Verify
        # exactly the bytes shipped in ZIP, without copying those local xattrs.
        with tempfile.TemporaryDirectory(prefix="course-backup-package-") as folder:
            staged = Path(folder)/bundle.name
            for name in bundle_names:
                destination = staged/(root/name).relative_to(bundle)
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(root/name,destination)
                destination.chmod(stat.S_IMODE((root/name).stat().st_mode))
            subprocess.run(["codesign","--verify","--deep","--strict",str(staged)],check=True)
            bundle_contents = {name:(staged/(root/name).relative_to(bundle)).read_bytes() for name in bundle_names}
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as z:
        for name in names+bundle_names:
            content = bundle_contents[name] if name in bundle_contents else (root/name).read_bytes()
            check_name = name.replace("课程视频备份.app/","macos-release/")
            inspect(check_name,content)
            info = zipfile.ZipInfo("course-youtube-backup/"+name)
            info.create_system = 3
            executable = name in ("start.command","scripts/app-control.sh","课程视频备份.app/Contents/MacOS/CourseBackup")
            info.external_attr = ((stat.S_IFREG | (0o755 if executable else 0o644)) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info,content)
    with zipfile.ZipFile(output) as z:
        for name in z.namelist():
            inspect(name.removeprefix("course-youtube-backup/").replace("课程视频备份.app/","macos-release/"),z.read(name))
        assert z.testzip() is None
    print(f"已生成并检查 ZIP：{output}（{len(names)+len(bundle_names)} 个文件）")
    return output

if __name__ == "__main__":
    package()
