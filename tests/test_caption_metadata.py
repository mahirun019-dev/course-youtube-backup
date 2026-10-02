from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.captions import choose_asr,asr_tracks,is_japanese
from app.database import Database
from app import main,youtube,config

@pytest.mark.parametrize('kind',['asr','ASR','AsR'])
@pytest.mark.parametrize('language',['ja','ja-JP','JA-jp','ja-Hira-JP'])
def test_live_metadata_case_and_bcp47(kind,language):
    item={'id':'synthetic','snippet':{'trackKind':kind,'language':language,'status':'serving','isDraft':False}}
    assert choose_asr([item])==item and is_japanese(language)

@pytest.mark.parametrize('status',[None,'','serving','SERVING'])
def test_optional_status_does_not_hide_asr(status):
    snippet={'trackKind':'asr','language':'ja'}
    if status is not None:snippet['status']=status
    item={'id':'synthetic','snippet':snippet}
    assert choose_asr([item])==item

@pytest.mark.parametrize('status',['syncing','failed'])
def test_asr_detected_even_if_not_downloadable(status):
    item={'id':'synthetic','snippet':{'trackKind':'asr','language':'ja','status':status}}
    assert asr_tracks([item])==[item] and choose_asr([item]) is None


def test_diag_whitelist_and_hashed_ids(monkeypatch,tmp_path,caplog):
    monkeypatch.setattr(config,'HISTORY',tmp_path)
    monkeypatch.setenv('COURSE_CAPTION_DEBUG','1')
    service=Mock();raw={'kind':'youtube#captionListResponse','items':[{'id':'private-caption-id','snippet':{'videoId':'private-video-id','name':'private title','trackKind':'asr','language':'ja','status':'serving','isDraft':False}}]}
    service.captions.return_value.list.return_value.execute.return_value=raw
    yt=youtube.YouTube();monkeypatch.setattr(yt,'service',lambda:service)
    with caplog.at_level('INFO',logger='course-backup'):
        assert yt.list_captions('private-video-id')==raw['items']
    service.captions.return_value.list.assert_called_once_with(part='snippet',videoId='private-video-id')
    p=tmp_path/'captions-diagnostic.log';logged=p.read_text()
    assert p.stat().st_mode & 0o777==0o600
    assert 'private-caption-id' not in logged and 'private-video-id' not in logged and 'private title' not in logged
    assert '"trackKind": "asr"' in logged and '"language": "ja"' in logged
    assert 'private-video-id' not in caplog.text


def test_diag_is_opt_in(monkeypatch,tmp_path):
    monkeypatch.setattr(config,'HISTORY',tmp_path);monkeypatch.delenv('COURSE_CAPTION_DEBUG',raising=False)
    yt=youtube.YouTube();service=Mock();service.captions.return_value.list.return_value.execute.return_value={'items':[]}
    monkeypatch.setattr(yt,'service',lambda:service)
    assert yt.list_captions('synthetic')==[]
    assert not (tmp_path/'captions-diagnostic.log').exists()

@pytest.mark.parametrize('items,state,message',[
    ([], 'api_pending','API 尚未同步'),
    ([{'id':'synthetic','snippet':{'trackKind':'asr','language':'ja','status':'syncing','isDraft':False}}],'processing','API 已返回自动字幕'),
    ([{'id':'synthetic','snippet':{'trackKind':'standard','language':'ja','status':'serving'}}],'api_pending','尚未返回 ASR'),
    ([{'id':'synthetic','snippet':{'trackKind':'asr','language':'ja','status':'serving','isDraft':True}}],'processing','草稿'),
])
def test_api_messages_are_evidence_based(monkeypatch,tmp_path,items,state,message):
    db=Database(tmp_path/'history.sqlite3');db.create('synthetic','M2','synthetic-url');db.update('synthetic',state='uploaded',video_id='synthetic-video')
    yt=Mock();yt.list_captions.return_value=items
    monkeypatch.setattr(main,'db',db);monkeypatch.setattr(main,'youtube',yt)
    with TestClient(main.app,base_url='http://localhost') as client:
        token=client.get('/api/status').json()['token']
        r=client.post('/api/jobs/synthetic/captions/check',json={},headers={'X-Local-Token':token})
        assert r.is_success and message in r.json()['message']
        assert db.get('synthetic')['caption_state']==state
        assert '可能未生成' not in db.get('synthetic')['message']


def test_lowercase_asr_ui_and_download(monkeypatch,tmp_path):
    db=Database(tmp_path/'history.sqlite3');db.create('synthetic','m2','synthetic-url');db.update('synthetic',state='uploaded',video_id='synthetic-video')
    yt=Mock();yt.list_captions.return_value=[{'id':'synthetic-asr','snippet':{'trackKind':'asr','language':'ja-JP','status':'serving','isDraft':False}}]
    yt.download_caption.return_value='1\n00:00:13,000 --> 00:00:14,000\n合成字幕\n'
    monkeypatch.setattr(main,'db',db);monkeypatch.setattr(main,'youtube',yt);monkeypatch.setattr(config,'SUBTITLES',tmp_path)
    with TestClient(main.app,base_url='http://localhost') as client:
        h={'X-Local-Token':client.get('/api/status').json()['token']}
        assert client.post('/api/jobs/synthetic/captions/check',json={},headers=h).is_success
        job=client.get('/api/jobs').json()[0]
        assert job['caption_state']=='ready' and job['language']=='ja-JP'
        assert client.post('/api/jobs/synthetic/captions/fetch',json={},headers=h).is_success
        assert '合成字幕' in client.get('/api/jobs/synthetic/subtitle/txt').text
        yt.download_caption.assert_called_once_with('synthetic-asr')
