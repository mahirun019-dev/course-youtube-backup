import requests
import pytest
from app.youtube import YouTube
from app import youtube as module
from app.errors import AppError

class Response:
    def __init__(self,status,headers=None,data=None):
        self.status_code=status;self.headers=headers or {};self.data=data or {}
    def json(self): return self.data

class Session:
    def __init__(self,responses):
        self.responses=list(responses);self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def request(self,method,url,**kw):
        self.calls.append((method,url,kw))
        item=self.responses.pop(0)
        if isinstance(item,Exception): raise item
        return item
    def put(self,url,**kw): return self.request('PUT',url,**kw)

@pytest.fixture
def file(tmp_path):
    f=tmp_path/'synthetic.mp4';f.write_bytes(b'abcdefghij');return f

FINAL=Response(201,data={'id':'owned-video','status':{'privacyStatus':'private'}})
URI='https://www.googleapis.com/upload/youtube/v3/videos?upload_id=synthetic'

def run(monkeypatch,file,responses,uri=''):
    sess=Session(responses);monkeypatch.setattr(module,'AuthorizedSession',lambda creds:sess)
    monkeypatch.setattr(module.time,'sleep',lambda x:None)
    yt=YouTube();monkeypatch.setattr(yt,'credentials',lambda:object())
    updates=[]
    result=yt.upload({'title':'M1','session_uri':uri},file,lambda **kw:updates.append(kw))
    return result,sess.calls,updates

def test_new_private_upload(monkeypatch,file):
    result,calls,updates=run(monkeypatch,file,[Response(200,{'Location':URI}),Response(308),FINAL])
    assert result=='owned-video'
    assert calls[0][2]['json']['status']=={'privacyStatus':'private'}
    assert calls[0][2]['json']['snippet']['title']=='M1'
    assert calls[-1][2]['headers']['Content-Range']=='bytes 0-9/10'
    assert updates[0]['session_uri']==URI

def test_resume_no_new_insert(monkeypatch,file):
    result,calls,updates=run(monkeypatch,file,[Response(308,{'Range':'bytes=0-4'}),FINAL],URI)
    assert all(x[0]=='PUT' for x in calls)
    assert calls[-1][2]['data']==b'fghij'
    assert calls[-1][2]['headers']['Content-Range']=='bytes 5-9/10'
    assert {'progress':50} in updates

def test_lost_final_response(monkeypatch,file):
    result,calls,_=run(monkeypatch,file,[Response(308),requests.ConnectionError('lost response'),FINAL],URI)
    assert result=='owned-video' and len(calls)==3
    assert calls[-1][2]['headers']['Content-Range']=='bytes */10'

def test_already_complete_after_restart(monkeypatch,file):
    result,calls,_=run(monkeypatch,file,[FINAL],URI)
    assert result=='owned-video' and len(calls)==1

def test_expired_session_retained(monkeypatch,file):
    session=Session([Response(404)])
    monkeypatch.setattr(module,'AuthorizedSession',lambda c:session)
    yt=YouTube();monkeypatch.setattr(yt,'credentials',lambda:object());updates=[]
    with pytest.raises(AppError): yt.upload({'title':'M1','session_uri':URI},file,lambda **kw:updates.append(kw))
    assert updates==[{'session_expired':1}] and file.exists()
    assert len(session.calls)==1

def test_private_confirmation_required(monkeypatch,file):
    with pytest.raises(AppError,match='private'):
        run(monkeypatch,file,[Response(201,data={'id':'owned','status':{'privacyStatus':'public'}})],URI)

def test_quota_no_fallback(monkeypatch,file):
    with pytest.raises(AppError,match='配额'):
        run(monkeypatch,file,[Response(403,data={'error':{'errors':[{'reason':'quotaExceeded'}]}})],URI)
    assert file.exists()

def test_no_progress_is_bounded(monkeypatch,file):
    with pytest.raises(AppError,match='没有进展'):
        run(monkeypatch,file,[Response(308)]*10,URI)

def test_network_retry_is_bounded(monkeypatch):
    session=Session([requests.ConnectionError('offline')]*5)
    monkeypatch.setattr(module.time,'sleep',lambda x:None)
    with pytest.raises(AppError,match='网络中断'):
        YouTube.request(session,'PUT',URI,data=b'')
    assert len(session.calls)==5

def test_reject_wrong_session_host(monkeypatch,file):
    with pytest.raises(AppError,match='地址无效'):
        run(monkeypatch,file,[],'https://evil.com/upload')
