#!/usr/bin/env python3
"""Real browser + local ffmpeg + mock upload, with entirely isolated data."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix='course-merge-ui-') as directory:
        folder=Path(directory)
        os.environ['COURSE_BACKUP_DATA']=str(folder/'data')
        os.environ['PATH']='/opt/homebrew/bin:/usr/local/bin:'+os.environ.get('PATH','')
        from app import main as backend, config, multivideo
        import uvicorn
        urls=['https://www.youtube.com/watch?v='+letter*11 for letter in 'ABCD']
        materials=[]
        for n,color in enumerate(('red','green','blue','yellow')):
            path=folder/f'source-{n}.mp4';materials.append(path)
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i',f'color=c={color}:s=160x90:r=12:d=1',
                '-f','lavfi','-i',f'sine=frequency={400+n*200}:sample_rate=48000:duration=1',
                '-c:v','libx264','-preset','veryfast','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
        config.private_write(config.CONFIG/'token.json','{"synthetic":true}')
        config.private_write(config.CONFIG/'client_secret.json','{"synthetic":true}')
        uploads=[];requests=[];release_upload=threading.Event()
        class MockYouTube:
            def credentials(self):return object()
            def get_videos(self,ids):return [{'id':id,'status':{'privacyStatus':'private','uploadStatus':'processed'}} for id in ids]
            def upload(self,job,path,update):
                uploads.append({'title':job['title'],'file':path.name,'sources':json.loads(job['sources_json'] or '[]')})
                update(progress=45)
                if len(uploads)==1 and not release_upload.wait(20):raise RuntimeError('UI did not observe upload progress')
                return f'localtest{len(uploads):02d}'
        backend.youtube=MockYouTube()
        def info(url):return {'title':'本地合成第 '+str(urls.index(url)+1)+' 部分','duration':1,'estimated_bytes':20000}
        def download(url,destination,progress,metadata):
            destination.mkdir(parents=True,exist_ok=True);path=destination/'video.mp4'
            shutil.copyfile(materials[urls.index(url)],path);metadata(info(url)['title']);progress(55);return path
        multivideo.inspect_video=info;multivideo.download=download;backend.download=download
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(backend.app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            address=f'http://localhost:{port}'
            for _ in range(100):
                try:urllib.request.urlopen(address+'/api/status',timeout=1);break
                except OSError:time.sleep(.1)
            with sync_playwright() as p:
                chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser=p.chromium.launch(executable_path=str(chrome) if chrome.exists() else None,headless=True)
                page=browser.new_page(viewport={'width':1120,'height':1040});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:requests.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/api/jobs') else None)
                page.goto(address);expect(page.locator('#connection')).to_contain_text('已连接')
                assert page.locator('#mode-single').get_attribute('aria-pressed')=='true'
                expect(page.locator('#url')).to_be_visible();page.fill('#title','M3')
                page.click('#mode-merge');expect(page.locator('#url')).to_be_hidden()
                expect(page.locator('#source-inputs input')).to_have_count(2)
                for _ in range(2):page.click('#add-source')
                for n,url in enumerate(urls):page.fill('#source-'+str(n),url)
                for index in (3,2,1):page.get_by_role('button',name=f'第 {index+1} 部分上移',exact=True).click()
                ordered=[urls[3],*urls[:3]]
                assert page.locator('#source-inputs input').evaluate_all('(nodes)=>nodes.map(n=>n.value)')==ordered
                page.get_by_role('button',name='删除第 4 部分',exact=True).click();page.click('#add-source');page.fill('#source-3',urls[2])
                for _ in range(4):page.click('#add-source')
                expect(page.locator('#source-inputs input')).to_have_count(8);expect(page.locator('#add-source')).to_be_disabled()
                for index in (8,7,6,5):page.get_by_role('button',name=f'删除第 {index} 部分',exact=True).click()
                assert page.input_value('#title')=='M3'
                page.click('#start')
                expect(page.locator('.job .progress-row')).to_contain_text('上传 YouTube：45%',timeout=20000)
                assert uploads[0]['title']=='M3' and uploads[0]['file']=='merged.mp4'
                assert requests[0]=={'urls':ordered,'title':'M3'}
                release_upload.set();expect(page.locator('.job.uploaded')).to_have_count(1,timeout=15000)
                expect(page.locator('.job')).to_contain_text('4 个视频合并')
                page.locator('.source-list summary').click()
                expect(page.locator('.source-list li')).to_have_count(4)
                assert page.locator('.source-list a').evaluate_all('(nodes)=>nodes.map(n=>n.href)')==ordered
                assert not (config.TEMP/backend.db.list()[0]['id']).exists()
                page.evaluate("document.getElementById('notice').hidden=true")
                page.screenshot(path=str(ROOT/'docs/merge-screenshot.png'),full_page=True)
                # Null progress remains indeterminate and merging is treated as active.
                item=page.evaluate('latest[0]');item.update(state='merging',progress=None,message='正在合并视频……')
                page.evaluate('(j)=>render([j])',item)
                assert page.locator('.job progress').get_attribute('value') is None
                expect(page.locator('.job .delete-button')).to_be_disabled()
                assert '%' not in page.locator('.job .progress-row').inner_text()
                page.evaluate('refresh()')
                for index in (4,3):page.get_by_role('button',name=f'删除第 {index} 部分',exact=True).click()
                for n,url in enumerate(urls[:2]):page.fill('#source-'+str(n),url)
                page.fill('#title','M4');page.click('#start')
                expect(page.locator('.job.uploaded')).to_have_count(2,timeout=15000)
                assert len(uploads)==2 and len(uploads[1]['sources'])==2
                page.click('#mode-single');page.fill('#url',urls[0]);page.fill('#title','Single M5');page.click('#start')
                expect(page.locator('.job.uploaded')).to_have_count(3,timeout=15000)
                assert uploads[2]['sources']==[] and uploads[2]['file']=='video.mp4'
                page.click('#mode-merge');page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                assert not errors,errors
                assert len(backend.db.list())==3 and all(j['visibility']=='private' for j in backend.db.list())
                assert not list(config.TEMP.iterdir())
                browser.close()
            print('Merge UI verified: default single / 2–8 inputs / add-delete-reorder / manual title / real local merge / one upload per task / truthful progress / source history / cleanup / mobile layout; all Google calls mocked.')
        finally:
            release_upload.set();server.should_exit=True;thread.join(timeout=10);sock.close()

if __name__=='__main__':main()
