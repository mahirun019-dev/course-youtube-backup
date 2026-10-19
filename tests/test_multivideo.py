"""Real local ffmpeg fixtures; all YouTube calls mocked, never live uploads."""
import json
import os
import shutil
import sqlite3
import struct
import subprocess
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app import main, config, media, multivideo
from app.database import Database, public_job, sources_of
from app.errors import AppError

FFMPEG = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
FFPROBE = shutil.which('ffprobe') or '/opt/homebrew/bin/ffprobe'
URLS = ['https://www.youtube.com/watch?v='+letter*11 for letter in 'ABCD']

@pytest.fixture(autouse=True)
def binaries(monkeypatch):
    monkeypatch.setenv('PATH',str(Path(FFMPEG).parent)+os.pathsep+os.environ.get('PATH',''))


def clip(path, color='red', frequency=400, size='160x90', fps=12, codec='libx264', sample_rate=48000, audio=True, audio_codec='aac', channels=1):
    path.parent.mkdir(parents=True,exist_ok=True)
    args=[FFMPEG,'-hide_banner','-nostdin','-v','error','-y','-f','lavfi','-i',f'color=c={color}:s={size}:r={fps}:d=1']
    if audio:args+=['-f','lavfi','-i',f'sine=frequency={frequency}:sample_rate={sample_rate}:duration=1']
    args+=['-c:v',codec,'-pix_fmt','yuv420p','-threads','1']
    if codec=='libx264':args+=['-preset','veryfast','-crf','20']
    if audio:args+=['-c:a',audio_codec,'-ar',str(sample_rate),'-ac',str(channels)]
    args+=[str(path)]
    subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    return path

@pytest.fixture(scope='module')
def materials(tmp_path_factory):
    folder=tmp_path_factory.mktemp('local-merge-materials')
    return [clip(folder/f'{n}.mp4',color,frequency) for n,(color,frequency) in enumerate(zip(['red','green','blue','yellow'],[400,600,800,1000]))]


def color_at(path,second):
    return tuple(subprocess.check_output([FFMPEG,'-v','error','-ss',str(second),'-i',str(path),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','pipe:1']))


def frequency_at(path,second,length=.35):
    raw=subprocess.check_output([FFMPEG,'-v','error','-ss',str(second),'-i',str(path),'-t',str(length),'-map','0:a:0','-ar','8000','-ac','1','-f','f32le','pipe:1'])
    samples=struct.unpack('<'+'f'*(len(raw)//4),raw)
    return sum(a<0<=b for a,b in zip(samples,samples[1:]))/(len(samples)/8000)

@pytest.mark.parametrize('order',[[0,1],[0,1,2,3],[3,1,0,2]])
def test_real_copy_merge_order_duration_audio(materials,tmp_path,order):
    paths=[materials[n] for n in order];assert media.copy_compatible([media.probe(p) for p in paths])
    folder=tmp_path/"task with ' quote";folder.mkdir();stages=[]
    output=media.merge_videos(paths,folder,stages.append)
    info=media.probe(output)
    assert abs(info['duration']-len(order))<.15
    assert info['video']['codec_name']=='h264' and info['audio']['codec_name']=='aac'
    assert any('直接拼接' in text for text in stages)
    for position,n in enumerate(order):
        rgb=color_at(output,position+.5)
        if n==0:assert rgb[0]>200 and rgb[1]<20
        if n==1:assert rgb[1]>90 and rgb[0]<20
        if n==2:assert rgb[2]>200 and rgb[0]<20
        if n==3:assert rgb[0]>200 and rgb[1]>200
        assert abs(frequency_at(output,position+.2)-[400,600,800,1000][n])<15
    assert not (folder/'merged.partial.mp4').exists()

@pytest.mark.parametrize('variant',[{'size':'240x180'},{'codec':'mpeg4'},{'fps':24},{'sample_rate':44100},{'audio_codec':'libmp3lame'},{'channels':2},{'audio':False}])
def test_real_normalized_merge_preserves_full_frame_audio(materials,tmp_path,variant):
    second=clip(tmp_path/'different.mp4','blue',800,**variant)
    paths=[materials[0],second];assert not media.copy_compatible([media.probe(p) for p in paths])
    folder=tmp_path/'task';folder.mkdir();stages=[]
    output=media.merge_videos(paths,folder,stages.append);info=media.probe(output)
    assert info['video']['codec_name']=='h264' and info['audio']['sample_rate']=='48000'
    assert any('统一 H.264/AAC' in text for text in stages)
    assert abs(info['duration']-2)<.15
    assert abs(frequency_at(output,.2)-400)<15
    if variant.get('audio',True):assert abs(frequency_at(output,1.2)-800)<15
    # Red first, blue second. Padding may reduce brightness, never swaps order.
    assert color_at(output,.5)[0]>color_at(output,.5)[2]
    assert color_at(output,1.5)[2]>color_at(output,1.5)[0]
    if 'size' in variant:
        assert (info['video']['width'],info['video']['height'])==(240,180)
        # 16:9 first input must be padded top/bottom, never cropped to 4:3.
        frame=subprocess.check_output([FFMPEG,'-v','error','-ss','0.5','-i',str(output),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])
        def pixel(x,y):return tuple(frame[(y*240+x)*3:(y*240+x)*3+3])
        assert max(pixel(120,2))<10 and pixel(120,40)[0]>200

@pytest.fixture
def pipeline(tmp_path,monkeypatch,materials):
    db=Database(tmp_path/'history.sqlite3');monkeypatch.setattr(main,'db',db)
    temp=tmp_path/'temp';temp.mkdir();monkeypatch.setattr(config,'TEMP',temp)
    yt=Mock();yt.upload.return_value='merged12345';monkeypatch.setattr(main,'youtube',yt)
    downloads=[]
    def metadata(url):return {'title':'Source '+str(URLS.index(url)),'duration':1,'estimated_bytes':20000}
    def download(url,folder,progress,title):
        downloads.append(url);folder.mkdir(parents=True,exist_ok=True);path=folder/'video.mp4'
        shutil.copyfile(materials[URLS.index(url)],path);title(metadata(url)['title']);progress(55);return path
    monkeypatch.setattr(multivideo,'inspect_video',metadata);monkeypatch.setattr(multivideo,'download',download)
    db.create('joined','M3',URLS[0],urls=URLS)
    def work():main.work_lock.acquire();main.worker('joined');return db.get('joined')
    return db,temp,yt,downloads,work


def test_worker_one_upload_cleanup_and_history(pipeline):
    db,temp,yt,downloads,work=pipeline;job=work()
    assert job['state']=='uploaded' and job['title']=='M3' and downloads==URLS
    yt.upload.assert_called_once();assert yt.upload.call_args.args[1].name=='merged.mp4'
    assert not (temp/'joined').exists() and job['visibility']=='private' and job['temp_path']==''
    public=public_job(job);assert len(public['sources'])==4 and public['mode']=='merge'
    assert not public['has_temp'] and all('path' not in source for source in public['sources'])
    assert [s['url'] for s in public['sources']]==URLS


def test_download_failure_retry_only_remaining(pipeline,monkeypatch):
    db,temp,yt,downloads,work=pipeline;original=multivideo.download
    def fail(url,*args):
        if url==URLS[2]:raise AppError('synthetic part 3 network failure')
        return original(url,*args)
    monkeypatch.setattr(multivideo,'download',fail);job=work()
    assert job['state']=='failed' and '3/4' in job['message'] and len(downloads)==2
    assert all(Path(s['path']).is_file() for s in sources_of(job)[:2]);yt.upload.assert_not_called()
    monkeypatch.setattr(multivideo,'download',original);job=work()
    assert job['state']=='uploaded' and downloads==URLS;yt.upload.assert_called_once()


def test_merge_failure_retries_without_downloading(pipeline,monkeypatch):
    db,temp,yt,downloads,work=pipeline;original=multivideo.merge_videos
    monkeypatch.setattr(multivideo,'merge_videos',Mock(side_effect=AppError('synthetic merge failure')))
    assert work()['state']=='failed';assert len(downloads)==4;yt.upload.assert_not_called()
    assert all(Path(s['path']).exists() for s in sources_of(db.get('joined')))
    monkeypatch.setattr(multivideo,'merge_videos',original)
    assert work()['state']=='uploaded' and len(downloads)==4;yt.upload.assert_called_once()


def test_upload_failure_resumes_same_file_and_session(pipeline,monkeypatch):
    db,temp,yt,downloads,work=pipeline
    def failure(job,path,update):update(session_uri='https://www.googleapis.com/synthetic-session');raise AppError('synthetic upload interruption')
    yt.upload.side_effect=failure;job=work();path=Path(job['temp_path'])
    assert job['state']=='failed' and path.is_file();stamp=path.stat().st_mtime_ns
    monkeypatch.setattr(main,'prepare_merged',Mock(side_effect=AssertionError('must not re-prepare')))
    def success(job,path,update):
        assert path.stat().st_mtime_ns==stamp
        return 'merged12345'
    yt.upload.side_effect=success;job=work()
    assert job['state']=='uploaded' and len(downloads)==4
    assert yt.upload.call_args.args[0]['session_uri']=='https://www.googleapis.com/synthetic-session'
    assert not path.exists()


def test_missing_merged_session_file_never_reuploads(pipeline):
    db,temp,yt,downloads,work=pipeline
    db.update('joined',session_uri='https://www.googleapis.com/synthetic-session')
    job=work();assert job['state']=='failed' and '缺失' in job['message'];assert not downloads;yt.upload.assert_not_called()


def test_disk_full_before_download(pipeline,monkeypatch):
    db,temp,yt,downloads,work=pipeline
    monkeypatch.setattr(media.shutil,'disk_usage',lambda folder:Mock(free=1))
    job=work();assert job['state']=='failed' and '磁盘空间不足' in job['message'];assert not downloads;yt.upload.assert_not_called()


def test_restart_merging_recovers_local_parts(pipeline):
    db,temp,yt,downloads,work=pipeline;db.update('joined',state='merging')
    reopened=Database(db.path);assert reopened.get('joined')['state']=='failed'
    assert len(sources_of(reopened.get('joined')))==4

@pytest.mark.parametrize('body',[{'urls':URLS[:1],'title':'M3'},{'urls':URLS*3,'title':'M3'},{'urls':[URLS[0],'https://evil.com/test'],'title':'M3'}, {'url':URLS[0],'urls':URLS[:2],'title':'M3'}, {'title':'M3'}, {'urls':URLS[:2],'title':''}])
def test_merge_validation_before_launch(pipeline,body,monkeypatch):
    launch=Mock();monkeypatch.setattr(main,'launch',launch)
    with TestClient(main.app,base_url='http://localhost') as client:
        token=client.get('/api/status').json()['token']
        assert client.post('/api/jobs',json=body,headers={'X-Local-Token':token}).status_code==422
    launch.assert_not_called()


def test_merge_api_preserves_user_order_and_manual_title(pipeline,monkeypatch):
    db,*_=pipeline
    def launch(id):main.work_lock.release()
    monkeypatch.setattr(main,'launch',launch)
    with TestClient(main.app,base_url='http://localhost') as client:
        token=client.get('/api/status').json()['token'];ordered=[URLS[3],URLS[1]]
        response=client.post('/api/jobs',json={'urls':ordered,'title':'My M3'},headers={'X-Local-Token':token})
        assert response.status_code==202
        job=db.get(response.json()['id']);assert job['title']=='My M3'
        assert [s['url'] for s in sources_of(job)]==[main.validate_url(u) for u in ordered]
        db.update(job['id'],state='merging')
        assert client.post('/api/jobs/'+job['id']+'/delete',json={'mode':'local','confirmed':True},headers={'X-Local-Token':token}).status_code==409


def test_copy_failure_falls_back_to_real_normalization(materials,tmp_path,monkeypatch):
    original=media.run_ffmpeg;labels=[]
    def run(args,folder,label):
        labels.append(label)
        if label=='直接拼接':raise AppError('synthetic copy incompatibility')
        return original(args,folder,label)
    monkeypatch.setattr(media,'run_ffmpeg',run);stages=[]
    output=media.merge_videos(materials[:2],tmp_path,stages.append)
    assert output.is_file() and '统一编码合并' in labels
    assert abs(frequency_at(output,1.2)-600)<15


def test_cross_task_part_path_rejected(pipeline):
    db,temp,yt,downloads,work=pipeline
    outside=temp/'other';outside.mkdir();keep=outside/'keep.mp4';keep.write_bytes(b'keep')
    sources=sources_of(db.get('joined'));sources[0]['path']=str(keep)
    db.update('joined',sources_json=json.dumps(sources))
    assert work()['state']=='failed' and not downloads and keep.read_bytes()==b'keep'
    yt.upload.assert_not_called()


def test_old_schema_migration_preserves_uploaded_history(tmp_path):
    path=tmp_path/'old.sqlite3';db=Database(path);db.create('existing','Manual M2',URLS[0])
    db.update('existing',state='uploaded',video_id='abcdefghijk')
    with sqlite3.connect(path) as c:c.execute('ALTER TABLE jobs DROP COLUMN sources_json')
    migrated=Database(path);job=migrated.get('existing')
    assert job['title']=='Manual M2' and job['video_id']=='abcdefghijk' and job['state']=='uploaded'
    assert public_job(job)['mode']=='single' and public_job(job)['sources']==[]


def test_inherited_task_guard_blocks_duplicate_after_parent_closes(tmp_path):
    import sys
    with media.task_guard(tmp_path) as fd:
        child=subprocess.Popen([sys.executable,'-c','import os,sys; os.read(0,1)'],stdin=subprocess.PIPE,pass_fds=(fd,))
    try:
        with pytest.raises(AppError,match='仍在运行'):
            with media.task_guard(tmp_path):pass
    finally:
        child.communicate(b'x',timeout=5)
    with media.task_guard(tmp_path):pass


def test_interrupted_merge_partial_removed_before_retry(pipeline,monkeypatch):
    db,temp,yt,downloads,work=pipeline
    task=temp/'joined';task.mkdir();partial=task/'merged.partial.mp4';partial.write_bytes(b'not committed')
    original=multivideo.inspect_video
    def inspect(url):
        assert not partial.exists()
        return original(url)
    monkeypatch.setattr(multivideo,'inspect_video',inspect)
    assert work()['state']=='uploaded'


def test_real_webm_vp9_opus_missing_stream_durations(materials,tmp_path):
    webm=clip(tmp_path/'vp9-opus.webm','blue',800,codec='libvpx-vp9',audio_codec='libopus')
    raw=media.probe(webm);assert raw['video']['codec_name']=='vp9' and raw['audio']['codec_name']=='opus'
    output=media.merge_videos([materials[0],webm],tmp_path,lambda message:None)
    assert media.probe(output)['video']['codec_name']=='h264'
    assert color_at(output,1.5)[2]>200 and abs(frequency_at(output,1.3)-800)<15


def test_audio_tail_preserved_with_last_video_frame(materials,tmp_path):
    first=tmp_path/'longer-audio.mp4'
    subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','color=c=red:s=160x90:r=12:d=1',
        '-f','lavfi','-i','sine=frequency=400:sample_rate=48000:duration=1.3',
        '-c:v','libx264','-preset','veryfast','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(first)],check=True)
    output=media.merge_videos([first,materials[2]],tmp_path,lambda message:None)
    assert abs(media.probe(output)['duration']-2.3)<.2
    assert color_at(output,1.1)[0]>200 and abs(frequency_at(output,1.05,.15)-400)<15
    assert color_at(output,1.8)[2]>200 and abs(frequency_at(output,1.6)-800)<15
