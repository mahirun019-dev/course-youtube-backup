#!/usr/bin/env python3
"""Build a relocatable universal AppKit launcher. No data or credentials in bundle."""
import plistlib
import re
import subprocess
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "课程视频备份.app"

def build(root=ROOT):
    root = Path(root)
    version = re.search(r'^VERSION = "([0-9.]+)"',(root/"app/config.py").read_text(),re.M).group(1)
    bundle = root/APP_NAME
    binary = bundle/"Contents/MacOS/CourseBackup"
    commands = subprocess.check_output(["/bin/ps","-axo","command="],text=True).splitlines()
    if any(line.strip()==str(binary) or line.strip().startswith(str(binary)+" ") for line in commands):
        raise RuntimeError("请先退出课程视频备份 App 再构建；不会覆盖正在运行的启动器。")
    # Build outside Documents: Finder/iCloud may attach FinderInfo there even
    # between signing and verification. The distribution contains no xattrs.
    with tempfile.TemporaryDirectory(prefix="course-backup-build-") as folder:
        staged = Path(folder)/APP_NAME
        build_bundle(root,staged,version)
        shutil.copytree(staged,bundle,dirs_exist_ok=True,copy_function=shutil.copyfile)
        (bundle/"Contents/MacOS/CourseBackup").chmod(0o755)
    print("已构建并签名 universal macOS App："+str(bundle))
    return bundle

def build_bundle(root,bundle,version):
    contents = bundle/"Contents";binary = contents/"MacOS/CourseBackup"
    binary.parent.mkdir(parents=True,exist_ok=True)
    resources = contents/"Resources";resources.mkdir(exist_ok=True)
    work = root/"work/macos-build";work.mkdir(parents=True,exist_ok=True)
    sdk = subprocess.check_output(["xcrun","--show-sdk-path"],text=True).strip()
    parts = []
    for arch in ("arm64","x86_64"):
        target = work/("CourseBackup-"+arch);parts.append(target)
        subprocess.run(["xcrun","swiftc","-swift-version","5","-O","-sdk",sdk,"-target",arch+"-apple-macosx13.0",
            "-module-cache-path",str(work/"modules"),str(root/"macos/App.swift"),"-o",str(target)],check=True)
    subprocess.run(["lipo","-create",*[str(x) for x in parts],"-output",str(binary)],check=True)
    info = {"CFBundleName":"课程视频备份","CFBundleDisplayName":"课程视频备份","CFBundleIdentifier":"dev.course-youtube-backup.app",
        "CFBundleExecutable":"CourseBackup","CFBundlePackageType":"APPL","CFBundleShortVersionString":version,
        "CFBundleVersion":version,"LSMinimumSystemVersion":"13.0","NSHighResolutionCapable":True,
        "NSHumanReadableCopyright":"Course YouTube Backup · MIT License"}
    (contents/"Info.plist").write_bytes(plistlib.dumps(info))
    (contents/"PkgInfo").write_bytes(b"APPL????")
    # Finder may attach extended attributes while inspecting a freshly created bundle.
    subprocess.run(["xattr","-cr",str(bundle)],check=True)
    subprocess.run(["codesign","--force","--sign","-",str(bundle)],check=True)
    subprocess.run(["codesign","--verify","--deep","--strict",str(bundle)],check=True)

if __name__ == "__main__":build()
