import json
import threading
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import Mock
import requests
import pytest
from fastapi.testclient import TestClient
from app import main
from app import youtube as module
from app.errors import AppError

CONFIG = {'installed':{'client_id':'synthetic.apps.googleusercontent.com','client_secret':'synthetic-secret','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}

@pytest.fixture
def client_config(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'CONFIG',tmp_path)
    (tmp_path/'client_secret.json').write_text(json.dumps(CONFIG))
    return tmp_path

@pytest.mark.parametrize('failure',[False,RuntimeError('cannot open browser')])
def test_real_loopback_manual_fallback(client_config,monkeypatch,failure):
    def browser(url,**kwargs):
        if isinstance(failure,Exception):raise failure
        return failure
    monkeypatch.setattr(module.webbrowser,'open',browser)
    original_send=requests.sessions.Session.send
    exchanges=[]
    def send(session,request,**kwargs):
        if request.url=='https://oauth2.googleapis.com/token':
            exchanges.append(request.body)
            response=requests.Response();response.status_code=200;response.request=request
            response._content=json.dumps({'access_token':'synthetic-access','refresh_token':'synthetic-refresh','token_type':'Bearer','expires_in':3600,'scope':' '.join(module.SCOPES)}).encode()
            response.headers['Content-Type']='application/json'
            return response
        return original_send(session,request,**kwargs)
    monkeypatch.setattr(requests.sessions.Session,'send',send)
    generated=threading.Event();browser_done=threading.Event();state={};errors=[]
    def ready(url,opened):
        state.update(url=url,opened=opened)
        generated.set()
        if opened is not None:browser_done.set()
    def login():
        try:module.YouTube().login(on_ready=ready)
        except Exception as e:errors.append(e)
    thread=threading.Thread(target=login,daemon=True);thread.start()
    assert generated.wait(5) and browser_done.wait(5)
    assert state['opened'] is False
    query=parse_qs(urlparse(state['url']).query)
    assert urlparse(state['url']).netloc=='accounts.google.com'
    assert query['client_id']==['synthetic.apps.googleusercontent.com']
    assert set(query['scope'][0].split())==set(module.SCOPES)
    assert query['access_type']==['offline'] and query['prompt']==['consent']
    assert query['code_challenge_method']==['S256']
    redirect=query['redirect_uri'][0]
    assert urlparse(redirect).hostname=='localhost' and urlparse(redirect).port>0
    r=requests.get(redirect+'?'+urlencode({'code':'synthetic-code','state':query['state'][0]}),timeout=5)
    assert r.ok
    thread.join(5);assert not thread.is_alive() and not errors
    assert exchanges and 'code_verifier=' in exchanges[0]
    token=client_config/'token.json'
    assert token.exists() and token.stat().st_mode & 0o777==0o600


def test_real_loopback_timeout_and_lock_release(client_config,monkeypatch):
    original=module.BrowserReadyFlow.run_local_server
    def short(self,**kw):
        kw['timeout_seconds']=0.05
        return original(self,**kw)
    monkeypatch.setattr(module.BrowserReadyFlow,'run_local_server',short)
    monkeypatch.setattr(module.webbrowser,'open',lambda *a,**k:False)
    with pytest.raises(AppError,match='超时'):module.YouTube().login()
    assert not (client_config/'token.json').exists() and not module.AUTH_LOCK.locked()


def test_auth_preflight_rejects_missing_file(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'CONFIG',tmp_path)
    state={'busy':False,'message':'','authorization_url':'','browser_opened':None}
    monkeypatch.setattr(main,'auth_state',state)
    with TestClient(main.app,base_url='http://localhost') as client:
        token=client.get('/api/status').json()['token']
        r=client.post('/api/auth',json={},headers={'X-Local-Token':token})
        assert r.status_code==400 and 'client_secret.json' in r.json()['message']
        assert not state['busy'] and state['authorization_url']==''


def test_auth_url_visible_during_wait_and_cleared_on_failure(monkeypatch):
    fake=Mock();finish=threading.Event();ready=threading.Event()
    url='https://accounts.google.com/o/oauth2/auth?state=synthetic'
    def login(on_ready):
        on_ready(url,None);on_ready(url,False);ready.set();finish.wait(5)
        raise AppError('Google 授权已超时',401)
    fake.login.side_effect=login;monkeypatch.setattr(main,'youtube',fake)
    state={'busy':False,'message':'','authorization_url':'','browser_opened':None}
    monkeypatch.setattr(main,'auth_state',state)
    with TestClient(main.app,base_url='http://localhost') as client:
        token=client.get('/api/status').json()['token'];h={'X-Local-Token':token}
        assert client.post('/api/auth',json={},headers=h).status_code==200
        assert ready.wait(5)
        status=client.get('/api/status').json()['auth']
        assert status['busy'] and status['authorization_url']==url and status['browser_opened'] is False
        assert '自动打开浏览器失败' in status['message']
        assert client.post('/api/auth',json={},headers=h).status_code==409
        finish.set()
        import time
        for _ in range(100):
            if not state['busy']:break
            time.sleep(.01)
        status=client.get('/api/status').json()['auth']
        assert not status['busy'] and status['authorization_url']=='' and '超时' in status['message']
