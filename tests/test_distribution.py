import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from safety import inspect,scan,safe_name
from package import package

@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    return tmp_path

@pytest.mark.parametrize('name',['data/config/token.json','client_secret.json','.env','course.mp4','M1.srt','data/history/history.sqlite3','.venv/bin/python'])
def test_forbidden_distribution_names(name):
    with pytest.raises(ValueError): inspect(name,b'content')

def test_secret_detection():
    with pytest.raises(ValueError): inspect('innocent.txt',b'GOCSPX-'+b'A'*30)

def test_ignore_rules(repo):
    (repo/'.gitignore').write_text((ROOT/'.gitignore').read_text())
    for name in ['data/config/token.json','data/history/history.sqlite3','data/subtitles/M1.txt','client_secret.json','.env','video.mp4','M1.srt','.venv/file']:
        assert subprocess.run(['git','check-ignore',name],cwd=repo,stdout=subprocess.DEVNULL).returncode==0
    assert subprocess.run(['git','check-ignore','client_secret.example.json'],cwd=repo,stdout=subprocess.DEVNULL).returncode==1

def test_staged_secret_not_hidden_by_ignore(repo):
    (repo/'token.json').write_text('{}');subprocess.run(['git','add','token.json'],cwd=repo,check=True)
    (repo/'.gitignore').write_text('token.json')
    with pytest.raises(ValueError): scan(repo)

def test_secret_history_detected(repo):
    (repo/'client_secret.json').write_text('{}');subprocess.run(['git','add','.'],cwd=repo,check=True)
    subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','synthetic'],cwd=repo,check=True)
    subprocess.run(['git','rm','client_secret.json'],cwd=repo,check=True)
    with pytest.raises(ValueError): scan(repo)

def test_zip_excludes_untracked_data(repo,tmp_path):
    (repo/'start.command').write_text('#!/bin/zsh\n');(repo/'README.md').write_text('example')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    (repo/'data').mkdir();(repo/'data/token.json').write_text('private')
    out=package(repo,repo/'release.zip')
    with zipfile.ZipFile(out) as z:
        assert len(z.namelist())==2 and all('data/' not in n for n in z.namelist())
        assert z.getinfo('course-youtube-backup/start.command').external_attr>>16 & 0o111

def test_command_executable_and_pages_static():
    assert ROOT.joinpath('start.command').stat().st_mode & 0o111
    page=ROOT.joinpath('docs/index.html').read_text()
    assert '本工具在用户自己的 Mac 本地运行' in page
    assert '本项目的服务器' in page
    assert '<form' not in page
