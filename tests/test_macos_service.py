import json
import os
import plistlib
import signal
from unittest.mock import Mock
import pytest
from app import config,runtime
from scripts import macos_service as service

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    for name in ('DATA','CONFIG','HISTORY','TEMP','SUBTITLES'):
        monkeypatch.setattr(config,name,tmp_path if name=='DATA' else tmp_path/name.lower())
    config.prepare()
    monkeypatch.setattr(service,'agent_path',lambda:tmp_path/'agents/test.plist')
    monkeypatch.setattr(service,'listener',lambda:[])
    return tmp_path

def test_process_identity_requires_module_directory_and_start(monkeypatch,tmp_path):
    responses={'command=':'python -m app.launcher','cwd':'n'+str(tmp_path),'lstart=':'synthetic start'}
    def command(*args):return responses['cwd' if '-d' in args else args[-1]]
    monkeypatch.setattr(runtime,'command',command)
    assert runtime.owns(100,tmp_path,'synthetic start')
    assert not runtime.owns(100,tmp_path,'old start')
    responses['cwd']='n'+str(tmp_path/'other');assert not runtime.owns(100,tmp_path)
    responses['cwd']='n'+str(tmp_path);responses['command=']='python -m other.launcher'
    assert not runtime.owns(100,tmp_path)
    assert not runtime.owns(None,tmp_path)

def test_foreign_port_never_stopped(isolated,monkeypatch):
    monkeypatch.setattr(service,'listener',lambda:[100]);monkeypatch.setattr(service,'owns',lambda *a:False)
    kill=Mock();monkeypatch.setattr(os,'kill',kill)
    with pytest.raises(service.ServiceError,match='其他'):service.stop()
    kill.assert_not_called()

def test_stale_pid_after_reboot_is_ignored(isolated,monkeypatch):
    config.private_write(config.CONFIG/'server-runtime.json',json.dumps({'pid':100,'project':str(service.ROOT),'data':str(config.DATA),'started':'previous boot'}))
    monkeypatch.setattr(service,'owns',lambda *a:False)
    kill=Mock();monkeypatch.setattr(os,'kill',kill)
    assert service.stop()['running'] is False;kill.assert_not_called()

def test_other_data_directory_not_adopted(isolated,monkeypatch):
    config.private_write(config.CONFIG/'server-runtime.json',json.dumps({'pid':100,'project':str(service.ROOT),'data':'other data'}))
    monkeypatch.setattr(service,'listener',lambda:[100]);monkeypatch.setattr(service,'owns',lambda *a:True)
    with pytest.raises(service.ServiceError,match='不同的数据目录'):service.owned_pid()

def test_start_existing_does_not_spawn_second_server(isolated,monkeypatch):
    monkeypatch.setattr(service,'owned_pid',lambda:100);monkeypatch.setattr(service,'health',lambda:{'version':'synthetic'})
    spawn=Mock();monkeypatch.setattr(service.subprocess,'Popen',spawn)
    assert service.start()['running'] and service.start()['pid']==100
    spawn.assert_not_called()

def test_fresh_start_detached_and_preserves_data_path(isolated,monkeypatch):
    monkeypatch.setattr(service,'owned_pid',lambda:None)
    spawn=Mock(return_value=Mock(pid=100));monkeypatch.setattr(service.subprocess,'Popen',spawn)
    monkeypatch.setattr(service,'wait_ready',lambda pid:{'ok':True,'pid':pid})
    assert service.start()['pid']==100
    kw=spawn.call_args.kwargs
    assert kw['start_new_session'] and kw['cwd']==service.ROOT
    assert kw['env']['COURSE_BACKUP_DATA']==str(config.DATA) and kw['env']['COURSE_BACKUP_NO_BROWSER']=='1'

@pytest.mark.parametrize('info',[{'busy':True},{'operations_busy':True},{'auth':{'busy':True}},None])
def test_busy_or_unknown_server_not_killed(isolated,monkeypatch,info):
    monkeypatch.setattr(service,'owned_pid',lambda:100);monkeypatch.setattr(service,'health',lambda:info)
    kill=Mock();monkeypatch.setattr(os,'kill',kill)
    with pytest.raises(service.ServiceError):service.stop()
    kill.assert_not_called()

def test_stop_checks_identity_and_only_signals_own_pid(isolated,monkeypatch):
    monkeypatch.setattr(service,'owned_pid',lambda:100);monkeypatch.setattr(service,'health',lambda:{'busy':False})
    monkeypatch.setattr(runtime,'started',lambda pid:'synthetic start')
    identity=Mock(side_effect=[True,False]);monkeypatch.setattr(service,'owns',identity)
    kill=Mock();monkeypatch.setattr(os,'kill',kill);monkeypatch.setattr(service,'snapshot',lambda:{'ok':True})
    assert service.stop()['ok'];kill.assert_called_once_with(100,signal.SIGTERM)
    identity.assert_any_call(100,service.ROOT,'synthetic start')

def test_reused_pid_not_signalled(isolated,monkeypatch):
    monkeypatch.setattr(service,'owned_pid',lambda:100);monkeypatch.setattr(service,'health',lambda:{'version':'synthetic'})
    monkeypatch.setattr(service,'owns',lambda *a:False);monkeypatch.setattr(runtime,'started',lambda pid:'new start')
    kill=Mock();monkeypatch.setattr(os,'kill',kill)
    with pytest.raises(service.ServiceError,match='身份'):service.stop()
    kill.assert_not_called()

def test_launchagent_default_off_and_no_browser_or_keepalive(isolated):
    assert service.agent_enabled() is False
    info=service.agent_config()
    assert info['WorkingDirectory']==str(service.ROOT)
    assert info['EnvironmentVariables']['COURSE_BACKUP_DATA']==str(config.DATA)
    assert info['RunAtLoad'] is True and info['KeepAlive'] is False
    assert info['EnvironmentVariables']['COURSE_BACKUP_NO_BROWSER']=='1'
    assert info['StandardOutPath']=='/dev/null' and info['StandardErrorPath']=='/dev/null'

def test_explicit_login_enable_disable_uses_own_plist(isolated,monkeypatch):
    launch=Mock(return_value=0);monkeypatch.setattr(service,'launchctl',launch);monkeypatch.setattr(service,'snapshot',lambda:{'ok':True})
    assert service.login_enable()['ok'];path=service.agent_path()
    assert path.stat().st_mode & 0o777==0o600 and plistlib.loads(path.read_bytes())==service.agent_config()
    assert service.agent_enabled();service.login_disable();assert not path.exists()
    assert ('bootout',service.target()) in [c.args for c in launch.call_args_list]

def test_login_bootstrap_failure_restores_disabled(isolated,monkeypatch):
    monkeypatch.setattr(service,'launchctl',lambda *a:1)
    with pytest.raises(service.ServiceError):service.login_enable()
    assert not service.agent_path().exists()

def test_foreign_agent_configuration_preserved(isolated):
    path=service.agent_path();path.parent.mkdir();original=plistlib.dumps({'Label':'other'});path.write_bytes(original)
    with pytest.raises(service.ServiceError):service.login_disable()
    assert path.read_bytes()==original
