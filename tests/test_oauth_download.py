import json
from unittest.mock import Mock
import pytest
from app import config,downloader
from app import youtube as module
from app.errors import AppError
from app.youtube import YouTube


def test_token_permissions_and_refresh(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'CONFIG',tmp_path)
    (tmp_path/'token.json').write_text('{}')
    credentials=Mock();credentials.valid=False;credentials.refresh_token='synthetic';credentials.to_json.return_value='{"synthetic":true}'
    monkeypatch.setattr(module.Credentials,'from_authorized_user_file',lambda p:credentials)
    assert YouTube().credentials() is credentials
    credentials.refresh.assert_called_once()
    assert (tmp_path/'token.json').stat().st_mode & 0o777==0o600


def test_oauth_desktop_flow(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'CONFIG',tmp_path)
    (tmp_path/'client_secret.json').write_text(json.dumps({'installed':{'client_id':'synthetic.apps.googleusercontent.com','client_secret':'synthetic-secret','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}))
    creds=Mock();creds.to_json.return_value='{"synthetic":true}'
    flow=Mock();flow.run_local_server.return_value=creds
    monkeypatch.setattr(module.InstalledAppFlow,'from_client_config',lambda cfg,scopes:flow)
    YouTube().login()
    args=flow.run_local_server.call_args.kwargs
    assert args['host']=='localhost' and args['port']==0 and args['timeout_seconds']==300 and args['open_browser'] is False and args['bind_addr']=='127.0.0.1'
    assert (tmp_path/'token.json').stat().st_mode & 0o777==0o600


def test_missing_and_invalid_credentials(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'CONFIG',tmp_path)
    with pytest.raises(AppError,match='连接 YouTube'):YouTube().credentials()
    with pytest.raises(AppError,match='client_secret'):YouTube().login()
    (tmp_path/'client_secret.json').write_text('{"web":{}}')
    with pytest.raises(AppError,match='Desktop App'):YouTube().login()


def test_missing_ffmpeg(monkeypatch,tmp_path):
    monkeypatch.setattr(downloader,'dependencies',lambda:{'ffmpeg':False,'ffprobe':False,'deno':True,'node':True})
    with pytest.raises(AppError,match='ffmpeg'):
        downloader.download('url',tmp_path,lambda p:None,lambda t:None)


def test_downloader_merged_result(monkeypatch,tmp_path):
    monkeypatch.setattr(downloader,'dependencies',lambda:{'ffmpeg':True,'ffprobe':True,'deno':False,'node':True})
    options=[]
    class YDL:
        def __init__(self,opts): options.append(opts)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            if download:
                (tmp_path/'video.mp4').write_bytes(b'synthetic merged video')
            return {'title':'Original title','ext':'mp4','filepath':str(tmp_path/'video.mp4')}
        def prepare_filename(self,info):return str(tmp_path/'video.mp4')
    monkeypatch.setattr(downloader.yt_dlp,'YoutubeDL',YDL)
    metadata=[]
    assert downloader.download('url',tmp_path,lambda p:None,metadata.append).name=='video.mp4'
    assert metadata==['Original title']
    assert options[0]['noplaylist'] and options[0]['merge_output_format']=='mp4'
    assert options[0]['js_runtimes']=={'node':{}}
