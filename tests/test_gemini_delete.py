from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from app import main,config
from app.database import Database,public_job
from app.youtube import YouTube
from app.errors import AppError

VIDEO='abcdefghijk'
def remote(privacy='private'):
    return {'id':VIDEO,'etag':'synthetic-etag','status':{'privacyStatus':privacy,'uploadStatus':'processed','license':'youtube','embeddable':True,'publicStatsViewable':False,'selfDeclaredMadeForKids':False,'containsSyntheticMedia':False}}

@pytest.fixture
def setup(tmp_path,monkeypatch):
    db=Database(tmp_path/'history.sqlite3');yt=Mock()
    monkeypatch.setattr(main,'db',db);monkeypatch.setattr(main,'youtube',yt)
    for name in ('TEMP','SUBTITLES'):
        p=tmp_path/name.lower();p.mkdir();monkeypatch.setattr(config,name,p)
    with TestClient(main.app,base_url='http://localhost') as client:
        h={'X-Local-Token':client.get('/api/status').json()['token']}
        yield db,yt,client,h

def job(db,id='one',video=True,captions=True):
    db.create(id,'Course M2','synthetic-url')
    db.update(id,state='uploaded' if video else 'failed',video_id=VIDEO if video else '',uploaded_at='synthetic' if video else '',
        caption_state='ready' if captions else 'waiting',caption_id='synthetic-asr' if captions else '',
        visibility='private' if video else 'unknown',visibility_uncertain=0,visibility_checked_at='synthetic')
    return db.get(id)

def files(db,id='one'):
    sub=config.SUBTITLES/id;sub.mkdir();temp=config.TEMP/id;temp.mkdir()
    (sub/'course.srt').write_text('synthetic subtitle');(sub/'course.txt').write_text('synthetic subtitle');(temp/'video.mp4').write_bytes(b'synthetic')
    db.update(id,srt_path=str(sub/'course.srt'),txt_path=str(sub/'course.txt'),temp_path=str(temp/'video.mp4'))
    return sub,temp

def test_public_needs_explicit_confirmation_and_captions(setup):
    db,yt,c,h=setup;job(db,captions=False)
    assert c.post('/api/jobs/one/privacy',json={'target':'public'},headers=h).status_code==400
    assert c.post('/api/jobs/one/privacy',json={'target':'public','confirmed':True},headers=h).status_code==409
    assert c.post('/api/jobs/one/privacy',json={'target':'unlisted','confirmed':True},headers=h).status_code==422
    yt.update_privacy.assert_not_called()

def test_public_and_restore_use_api_state(setup):
    db,yt,c,h=setup;job(db);yt.get_video.return_value=remote();yt.update_privacy.return_value=remote('public')
    r=c.post('/api/jobs/one/privacy',json={'target':'public','confirmed':True},headers=h)
    assert r.is_success and r.json()['job']['visibility']=='public' and not r.json()['job']['visibility_uncertain']
    yt.update_privacy.assert_called_once_with(remote(),'public')
    yt.get_video.return_value=remote('public');yt.update_privacy.return_value=remote('private')
    assert c.post('/api/jobs/one/privacy',json={'target':'private','confirmed':True},headers=h).is_success
    assert db.get('one')['visibility']=='private'

def test_api_refuses_public_preserves_private(setup):
    db,yt,c,h=setup;job(db);yt.get_video.return_value=remote();yt.update_privacy.side_effect=AppError('YouTube 拒绝公开',403)
    assert c.post('/api/jobs/one/privacy',json={'target':'public','confirmed':True},headers=h).status_code==403
    assert db.get('one')['visibility']=='private' and not db.get('one')['visibility_uncertain']

def test_returned_state_never_optimistic(setup):
    db,yt,c,h=setup;job(db);yt.get_video.return_value=remote();yt.update_privacy.return_value=remote()
    assert c.post('/api/jobs/one/privacy',json={'target':'public','confirmed':True},headers=h).status_code==409
    assert db.get('one')['visibility']=='private'

def test_lost_response_reconciles_actual_public(setup):
    db,yt,c,h=setup;job(db);yt.get_video.side_effect=[remote(),remote('public')]
    def failure(*args):
        assert db.get('one')['visibility_uncertain']==1
        raise AppError('响应丢失',503)
    yt.update_privacy.side_effect=failure
    assert c.post('/api/jobs/one/privacy',json={'target':'public','confirmed':True},headers=h).status_code==503
    assert db.get('one')['visibility']=='public' and not db.get('one')['visibility_uncertain']
    assert yt.update_privacy.call_count==1

def test_uncertain_visibility_not_confirmed_private(setup):
    db,yt,c,h=setup;job(db);yt.get_video.side_effect=[remote(),OSError('offline')];yt.update_privacy.side_effect=OSError('offline')
    assert c.post('/api/jobs/one/privacy',json={'target':'public','confirmed':True},headers=h).status_code==503
    assert db.get('one')['visibility_uncertain']==1

def test_restore_allowed_without_captions(setup):
    db,yt,c,h=setup;job(db,captions=False);db.update('one',visibility='public')
    yt.get_video.return_value=remote('public');yt.update_privacy.return_value=remote()
    assert c.post('/api/jobs/one/privacy',json={'target':'private','confirmed':True},headers=h).is_success

def test_privacy_sync_missing_orphan_and_no_writes(setup):
    db,yt,c,h=setup;job(db);yt.get_videos.return_value=[]
    assert c.post('/api/visibility/sync',json={},headers=h).is_success
    orphan=db.get('one');assert orphan['state']=='orphan' and orphan['remote_missing']==1
    assert not public_job(orphan)['can_delete_remote']
    yt.update_privacy.assert_not_called();yt.delete_video.assert_not_called()

def test_sync_read_failure_marks_unknown(setup):
    db,yt,c,h=setup;job(db);yt.get_videos.side_effect=OSError('offline')
    assert c.post('/api/visibility/sync',json={},headers=h).status_code==503
    assert db.get('one')['visibility_uncertain']==1

def test_delete_failed_without_id_only_local(setup):
    db,yt,c,h=setup;job(db,video=False);sub,temp=files(db)
    r=c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h)
    assert r.is_success and not sub.exists() and not temp.exists()
    assert not db.list();yt.delete_video.assert_not_called()

def test_local_delete_uploaded_never_touches_youtube_or_others(setup):
    db,yt,c,h=setup;job(db);job(db,'other');sub,temp=files(db);other_sub,other_temp=files(db,'other')
    assert c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h).is_success
    assert not sub.exists() and not temp.exists() and other_sub.exists() and other_temp.exists()
    assert db.get('other')['title']=='Course M2';yt.delete_video.assert_not_called()

def test_invalid_video_id_is_local_only(setup):
    db,yt,c,h=setup;job(db);db.update('one',video_id='invalid')
    assert not public_job(db.get('one'))['can_delete_remote']
    body={'mode':'remote','confirmed':True,'confirmation_title':'Course M2'}
    assert c.post('/api/jobs/one/delete',json=body,headers=h).status_code==409
    assert c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h).is_success
    yt.delete_video.assert_not_called()

def test_remote_delete_needs_two_confirmations_and_valid_upload(setup):
    db,yt,c,h=setup;job(db)
    assert c.post('/api/jobs/one/delete',json={'mode':'remote','confirmed':True},headers=h).status_code==400
    assert c.post('/api/jobs/one/delete',json={'mode':'remote','confirmed':False,'confirmation_title':'Course M2'},headers=h).status_code==400
    db.update('one',state='failed')
    assert c.post('/api/jobs/one/delete',json={'mode':'remote','confirmed':True,'confirmation_title':'Course M2'},headers=h).status_code==409
    yt.delete_video.assert_not_called()

def test_remote_delete_confirmed_success_before_local_cleanup(setup):
    db,yt,c,h=setup;job(db);sub,temp=files(db)
    def confirmed(id):
        assert sub.exists() and temp.exists() and db.get('one')
        return True
    yt.delete_video.side_effect=confirmed
    r=c.post('/api/jobs/one/delete',json={'mode':'remote','confirmed':True,'confirmation_title':'Course M2'},headers=h)
    assert r.is_success and not db.list() and not sub.exists() and not temp.exists()
    yt.delete_video.assert_called_once_with(VIDEO)

def test_remote_failure_retains_history_and_all_files(setup):
    db,yt,c,h=setup;job(db);sub,temp=files(db);yt.delete_video.side_effect=AppError('forbidden',403)
    r=c.post('/api/jobs/one/delete',json={'mode':'remote','confirmed':True,'confirmation_title':'Course M2'},headers=h)
    assert r.status_code==403 and db.get('one') and sub.exists() and temp.exists()
    assert '本地记录已保留' in db.get('one')['message']

def test_remote_unconfirmed_response_retains_everything(setup):
    db,yt,c,h=setup;job(db);sub,temp=files(db);yt.delete_video.return_value=None
    assert c.post('/api/jobs/one/delete',json={'mode':'remote','confirmed':True,'confirmation_title':'Course M2'},headers=h).status_code==502
    assert db.get('one') and sub.exists() and temp.exists()

def test_remote_success_local_failure_can_retry_without_second_remote_call(setup,monkeypatch):
    db,yt,c,h=setup;job(db);files(db);yt.delete_video.return_value=True
    cleanup=main.remove_local_data;monkeypatch.setattr(main,'remove_local_data',Mock(side_effect=OSError('denied')))
    body={'mode':'remote','confirmed':True,'confirmation_title':'Course M2'}
    assert c.post('/api/jobs/one/delete',json=body,headers=h).status_code==503
    assert db.get('one')['remote_deleted']==1
    monkeypatch.setattr(main,'remove_local_data',cleanup)
    assert c.post('/api/jobs/one/delete',json=body,headers=h).is_success
    assert yt.delete_video.call_count==1

def test_delete_active_and_wrong_confirmation_do_nothing(setup):
    db,yt,c,h=setup;job(db);db.update('one',state='uploading')
    assert c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h).status_code==409
    assert c.post('/api/jobs/one/delete',json={'mode':'local'},headers=h).status_code==400
    assert db.get('one');yt.delete_video.assert_not_called()

def test_different_record_paths_rejected(setup):
    db,yt,c,h=setup;job(db);job(db,'other');sub,temp=files(db,'other');db.update('one',txt_path=str(sub/'course.txt'))
    assert c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h).status_code==400
    assert db.get('one') and sub.exists() and temp.exists()

def test_symlink_directory_not_followed(setup):
    db,yt,c,h=setup;job(db);other=config.SUBTITLES/'other';other.mkdir();(other/'file').write_text('keep')
    (config.SUBTITLES/'one').symlink_to(other,target_is_directory=True)
    assert c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h).status_code==400
    assert (other/'file').exists()

def test_second_directory_symlink_rejected_before_removing_subtitles(setup):
    db,yt,c,h=setup;job(db)
    sub=config.SUBTITLES/'one';sub.mkdir();(sub/'keep.txt').write_text('keep')
    other=config.TEMP/'other';other.mkdir();(other/'keep.part').write_text('keep')
    (config.TEMP/'one').symlink_to(other,target_is_directory=True)
    assert c.post('/api/jobs/one/delete',json={'mode':'local','confirmed':True},headers=h).status_code==400
    assert (sub/'keep.txt').exists() and (other/'keep.part').exists() and db.get('one')

def test_migration_existing_private_not_assumed_current(tmp_path):
    p=tmp_path/'history.sqlite3';db=Database(p);job(db)
    with sqlite3.connect(p) as c:
        for field in ('visibility','visibility_uncertain','visibility_checked_at','remote_missing','remote_deleted','remote_upload_status'):
            c.execute('ALTER TABLE jobs DROP COLUMN '+field)
    migrated=Database(p).get('one')
    assert migrated['visibility']=='unknown' and migrated['visibility_uncertain']==1

def test_sdk_update_preserves_status_and_sets_etag(monkeypatch):
    service=Mock();request=service.videos.return_value.update.return_value;request.headers={};request.execute.return_value=remote('public')
    yt=YouTube();monkeypatch.setattr(yt,'service',lambda:service)
    before=remote();before['status']['publishAt']='2099-01-01T00:00:00Z'
    assert yt.update_privacy(before,'public')['status']['privacyStatus']=='public'
    kw=service.videos.return_value.update.call_args.kwargs
    assert kw['part']=='status' and kw['body']['status']['privacyStatus']=='public'
    assert kw['body']['status']['embeddable'] is True and kw['body']['status']['selfDeclaredMadeForKids'] is False
    assert 'publishAt' not in kw['body']['status'] and 'uploadStatus' not in kw['body']['status']
    assert request.headers['If-Match']=='synthetic-etag'
    request.execute.assert_called_once_with(num_retries=0)

@pytest.mark.parametrize('status',[204,200])
def test_sdk_delete_requires_http204(monkeypatch,status):
    service=Mock();request=service.videos.return_value.delete.return_value;callbacks=[]
    request.add_response_callback.side_effect=callbacks.append
    request.execute.side_effect=lambda **kw:[cb(SimpleNamespace(status=status)) for cb in callbacks]
    yt=YouTube();monkeypatch.setattr(yt,'service',lambda:service)
    if status==204:assert yt.delete_video(VIDEO) is True
    else:
        with pytest.raises(AppError):yt.delete_video(VIDEO)
    service.videos.return_value.delete.assert_called_once_with(id=VIDEO)
