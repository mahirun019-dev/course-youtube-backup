import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from app import main,config
from app.database import Database,public_job
from app.downloader import validate_url,FORMAT
from app.captions import choose_asr,srt_to_txt,safe_name
from app.errors import AppError,friendly,api_error

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path/"history.sqlite3")

@pytest.fixture
def client(monkeypatch,db):
    monkeypatch.setattr(main,"db",db)
    with TestClient(main.app,base_url="http://localhost") as c:
        yield c

def headers(c):
    return {"X-Local-Token":c.get("/api/status").json()["token"]}

@pytest.mark.parametrize('url',["https://youtu.be/abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk&list=x", "https://m.youtube.com/shorts/abcdefghijk", "https://youtube.com/live/abcdefghijk"])
def test_url(url):
    assert validate_url(url)=="https://www.youtube.com/watch?v=abcdefghijk"

@pytest.mark.parametrize('url',["", "https://evil.com/watch?v=abcdefghijk", "https://youtube.com.evil.com/watch?v=abcdefghijk", "file:///etc/passwd", "https://localhost/watch?v=abcdefghijk", "https://youtube.com/playlist?list=x", "https://youtu.be/short", "https://u:p@youtube.com/watch?v=abcdefghijk", "https://youtube.com:8000/watch?v=abcdefghijk"])
def test_bad_url(url):
    with pytest.raises(AppError): validate_url(url)

@pytest.mark.parametrize('body',[{"url":"https://youtu.be/abcdefghijk","title":" "},{"url":"no","title":"M1"},{"url":"https://youtu.be/abcdefghijk","title":"M1","privacyStatus":"public"}])
def test_api_validation(client,body):
    assert client.post('/api/jobs',json=body,headers=headers(client)).status_code==422

def test_no_csrf_and_wrong_origin(client):
    assert client.post('/api/auth',json={}).status_code==403
    assert client.get('/api/status',headers={"Origin":"https://evil.com"}).status_code==403
    assert client.get('/api/jobs',headers={"Sec-Fetch-Site":"cross-site"}).status_code==403
    assert client.get('/api/status',headers={"Host":"evil.com"}).status_code==400

def test_history_recover(db):
    db.create('one','M1','https://youtu.be/abcdefghijk')
    db.update('one',state='uploading',temp_path='file',session_uri='secret-session')
    other = Database(db.path)
    job = other.get('one')
    assert job['state']=='failed' and job['temp_path']=='file' and job['session_uri']=='secret-session'
    assert 'session_uri' not in public_job(job)
    assert 'temp_path' not in public_job(job)

def test_corrupt_db_preserved(tmp_path):
    p = tmp_path/'history.sqlite3';p.write_bytes(b'broken original')
    db = Database(p)
    assert db.error and p.read_bytes()==b'broken original'
    with pytest.raises(AppError): db.list()

def track(id,language,kind='ASR',status='serving'):
    return {'id':id,'snippet':{'language':language,'trackKind':kind,'status':status}}

def test_asr_select():
    assert choose_asr([track('en','en'),track('ja','ja-JP')])['id']=='ja'
    assert choose_asr([track('en','en')])['id']=='en'
    assert choose_asr([track('ja','ja',status='syncing'),track('normal','ja','standard')]) is None
    t = track('ja','ja');t['snippet']['isDraft']=True
    assert choose_asr([t]) is None

SRT = '1\r\n00:00:13,120 --> 00:00:16,500\r\n<b>今日は</b> &amp; テスト\r\n\r\n2\r\n00:00:28,000 --> 00:00:30,000\r\nまず、この問題について。\r\n'
def test_caption_text():
    txt = srt_to_txt(SRT)
    assert '00:00:13\n今日は & テスト' in txt and '00:00:28' in txt
    assert '-->' not in txt and '<b>' not in txt
    assert safe_name('../M1/<test>')=='_M1__test_'
    with pytest.raises(AppError): srt_to_txt('WEBVTT\nno valid cues')

def test_api_errors():
    assert api_error(403,'quotaExceeded').status==429
    assert '重新连接' in friendly(RefreshError('revoked')).message
    e = HttpError(SimpleNamespace(status=403,reason='Forbidden'),json.dumps({'error':{'errors':[{'reason':'forbidden'}]}}).encode())
    assert 'Studio' in friendly(e,caption=True).message

def test_private_and_height():
    assert config.YOUTUBE_PRIVACY=='private'
    assert '1080' in FORMAT and FORMAT.count('height<=1080')==2

def test_worker_success_and_failure(monkeypatch,db,tmp_path):
    monkeypatch.setattr(main,'db',db)
    temp=tmp_path/'temp';temp.mkdir();monkeypatch.setattr(config,'TEMP',temp)
    youtube = Mock();monkeypatch.setattr(main,'youtube',youtube)
    def download(url,folder,progress,metadata):
        folder.mkdir();p=folder/'video.mp4';p.write_bytes(b'synthetic-video');metadata('Synthetic course');return p
    monkeypatch.setattr(main,'download',download)
    db.create('ok','M1','https://youtu.be/abcdefghijk');youtube.upload.return_value='own-video'
    main.work_lock.acquire();main.worker('ok')
    job=db.get('ok');assert job['state']=='uploaded' and job['video_id']=='own-video'
    assert job['source_title']=='Synthetic course' and job['title']=='M1'
    assert not (temp/'ok').exists()
    db.create('fail','M2','https://youtu.be/abcdefghijk');youtube.upload.side_effect=AppError('上传失败')
    main.work_lock.acquire();main.worker('fail')
    job=db.get('fail');assert job['state']=='failed' and Path(job['temp_path']).exists()
    # Retrying reuses the downloaded file, never calls downloader again.
    youtube.upload.side_effect=None;youtube.upload.return_value='own-video2'
    monkeypatch.setattr(main,'download',Mock(side_effect=AssertionError('must reuse video')))
    main.work_lock.acquire();main.worker('fail')
    assert db.get('fail')['state']=='uploaded' and not (temp/'fail').exists()

def test_cleanup_fail_and_no_delete_before_success(monkeypatch,db,tmp_path):
    monkeypatch.setattr(main,'db',db);monkeypatch.setattr(config,'TEMP',tmp_path)
    db.create('j','M1','url');folder=tmp_path/'j';folder.mkdir()
    with pytest.raises(AppError): main.cleanup('j')
    assert folder.exists()
    db.update('j',state='uploaded',video_id='id',temp_path=str(folder/'video.mp4'))
    monkeypatch.setattr(main.shutil,'rmtree',Mock(side_effect=OSError('denied')))
    main.cleanup('j');assert '删除失败' in db.get('j')['message']

def test_caption_api_flow(client,db,monkeypatch,tmp_path):
    db.create('j','M1','url');db.update('j',state='uploaded',video_id='own')
    yt=Mock();yt.list_captions.return_value=[track('asr-ja','ja')];yt.download_caption.return_value=SRT
    monkeypatch.setattr(main,'youtube',yt);monkeypatch.setattr(config,'SUBTITLES',tmp_path)
    h=headers(client)
    assert client.post('/api/jobs/j/captions/check',json={},headers=h).status_code==200
    assert db.get('j')['caption_state']=='ready'
    assert client.post('/api/jobs/j/captions/check',json={},headers=h).status_code==429
    assert yt.list_captions.call_count==1
    assert client.post('/api/jobs/j/captions/fetch',json={},headers=h).status_code==200
    job=db.get('j');assert job['caption_state']=='saved'
    assert Path(job['srt_path']).name=='M1.srt' and Path(job['txt_path']).name=='M1.txt'
    assert '今日は' in client.get('/api/jobs/j/subtitle/txt').text
    assert client.get('/api/jobs/j/subtitle/other').status_code==404
    yt.download_caption.assert_called_once_with('asr-ja')

def test_caption_no_tracks_and_forbidden(client,db,monkeypatch):
    db.create('j','M1','url');db.update('j',state='uploaded',video_id='own')
    yt=Mock();yt.list_captions.return_value=[];monkeypatch.setattr(main,'youtube',yt)
    assert client.post('/api/jobs/j/captions/check',json={},headers=headers(client)).status_code==200
    assert db.get('j')['caption_state']=='waiting'
    db.update('j',checked_at='')
    yt.list_captions.side_effect=AppError('字幕 API 权限不足',403)
    assert client.post('/api/jobs/j/captions/check',json={},headers=headers(client)).status_code==403
    assert db.get('j')['caption_state']=='waiting'

def test_missing_auth_in_ui(client):
    assert client.get('/').status_code==200
    assert client.post('/api/jobs',json={'url':'https://youtu.be/abcdefghijk','title':'M1'},headers=headers(client)).status_code==401

