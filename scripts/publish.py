#!/usr/bin/env python3
"""Publish only after tests and staged/history scans pass. Public repo explicitly requested."""
import json
import re
import subprocess
import sys
from pathlib import Path
from safety import ROOT,scan
from package import package

def run(*args,capture=False,check=True):
    return subprocess.run(args,cwd=ROOT,text=True,capture_output=capture,check=check)

def main():
    run("gh","auth","status")
    run(sys.executable,"-m","pytest","-q")
    login = run("gh","api","user","--jq",".login",capture=True).stdout.strip()
    owner_id = run("gh","api","user","--jq",".id",capture=True).stdout.strip()
    full = login+"/course-youtube-backup"
    repository = "https://github.com/"+full
    pages = "https://"+login+".github.io/course-youtube-backup/"
    if not (ROOT/".git").exists():
        run("git","init","-b","main")
    remote = run("git","remote","get-url","origin",capture=True,check=False)
    exists = run("gh","repo","view",full,"--json","name",capture=True,check=False).returncode==0
    if exists and (remote.returncode!=0 or full not in remote.stdout):
        raise SystemExit("同名仓库已经存在但未与本项目关联；请先核对，未修改远端。")
    if remote.returncode==0 and full not in remote.stdout:
        raise SystemExit("origin 指向其他仓库；未发布。")
    (ROOT/"docs/project.json").write_text(json.dumps({"repository":repository,"pages":pages},ensure_ascii=False,indent=2)+"\n")
    readme = ROOT/"README.md"
    text = readme.read_text()
    block = f"<!-- PUBLIC_LINKS_START -->\n- [GitHub Repository]({repository})\n- [GitHub Pages 介绍网站]({pages})\n- [v1.0.0 Release]({repository}/releases/tag/v1.0.0)\n<!-- PUBLIC_LINKS_END -->"
    readme.write_text(re.sub(r"<!-- PUBLIC_LINKS_START -->.*?<!-- PUBLIC_LINKS_END -->",lambda m:block,text,flags=re.S))
    run("git","config","user.name",login)
    run("git","config","user.email",owner_id+"+"+login+"@users.noreply.github.com")
    run("git","add",".")
    print(f"发布前安全检查：{scan()} 个文件。")
    run("git","status","--short")
    run("git","ls-files")
    if run("git","diff","--cached","--quiet",check=False).returncode:
        run("git","commit","-m","Release Course YouTube Backup v1.0.0")
    archive = package()
    if remote.returncode != 0:
        run("gh","repo","create",full,"--public","--source=.","--remote=origin","--push","--description","Mac 本地课程 YouTube 备份工具：固定 Private 上传、自动字幕获取，无 AI API。")
    else:
        run("git","push","-u","origin","main")
    page = run("gh","api",f"repos/{full}/pages",capture=True,check=False)
    if page.returncode:
        result = run("gh","api",f"repos/{full}/pages","-X","POST","-f","source[branch]=main","-f","source[path]=/docs",check=False)
    else:
        result = run("gh","api",f"repos/{full}/pages","-X","PUT","-f","source[branch]=main","-f","source[path]=/docs",check=False)
    if result.returncode:
        print("Pages 设置失败；请在 Repository Settings → Pages 选择 main /docs。")
    release = run("gh","release","view","v1.0.0","--repo",full,capture=True,check=False)
    if release.returncode:
        run("gh","release","create","v1.0.0",str(archive),"--repo",full,"--target","main","--title","Course YouTube Backup v1.0.0","--notes-file","RELEASE_NOTES.md")
    else:
        run("gh","release","upload","v1.0.0",str(archive),"--repo",full,"--clobber")
    print("Repository: "+repository+"\nPages: "+pages+"\nRelease: "+repository+"/releases/tag/v1.0.0")

if __name__=="__main__":
    main()
