#!/usr/bin/env python3
"""Real Chrome, isolated SQLite/files, synthetic YouTube. Never uses a real account."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = '''
import os,uvicorn
from app import main,config
from app.errors import AppError
from fastapi import Request
(config.CONFIG/'token.json').write_text('synthetic-unused')
videos={};events=[];mode={'value':''}
for id,title,video in [('gemini','Course M2','abcdefghijk'),('local','Local copy','lmnopqrstuv'),('remote','Delete test','ABCDEFGHIJK'),('failed','Cancelled test','')]:
    main.db.create(id,title,'synthetic')
    sub=config.SUBTITLES/id;sub.mkdir();tmp=config.TEMP/id;tmp.mkdir()
    (sub/'course.txt').write_text('synthetic subtitle');(sub/'course.srt').write_text('synthetic subtitle');(tmp/'temp.part').write_text('synthetic')
    main.db.update(id,state='uploaded' if video else 'failed',video_id=video,caption_id='synthetic-asr' if video else '',caption_state='saved' if video else 'waiting',language='ja',uploaded_at='2026-10-02T00:00:00Z' if video else '',srt_path=str(sub/'course.srt'),txt_path=str(sub/'course.txt'))
    if video:videos[video]={'id':video,'status':{'privacyStatus':'private','uploadStatus':'processed'}}
class FakeYouTube:
    def get_videos(self,ids):return [videos[id] for id in ids if id in videos]
    def get_video(self,id):return videos.get(id)
    def update_privacy(self,video,target):
        events.append(['privacy',video['id'],target])
        if mode['value']=='restore-fail' and target=='private':raise AppError('synthetic restore failure',403)
        videos[video['id']]={'id':video['id'],'status':{'privacyStatus':target,'uploadStatus':'processed'}}
        if mode['value']=='lost-public' and target=='public':raise AppError('synthetic lost response',503)
        return videos[video['id']]
    def delete_video(self,id):
        events.append(['delete',id])
        if mode['value']=='delete-fail':raise AppError('synthetic deletion failure',403)
        del videos[id];return True
main.db.update('local',caption_state='waiting',caption_id='')
main.youtube=FakeYouTube()
@main.app.get('/qa/state')
def state():return {'events':events,'videos':videos,'ids':[j['id'] for j in main.db.list()]}
@main.app.post('/qa/mode')
async def set_mode(request:Request):
    mode['value']=(await request.json())['value'];return {}
uvicorn.run(main.app,host='127.0.0.1',port=int(os.environ['QA_PORT']),access_log=False,log_level='error')
'''

def main():
    with tempfile.TemporaryDirectory(prefix='course-gemini-ui-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-c',BOOTSTRAP],cwd=ROOT,env={**os.environ,'COURSE_BACKUP_DATA':temp,'QA_PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            base=f'http://localhost:{port}'
            for _ in range(100):
                try:urllib.request.urlopen(base+'/api/status',timeout=1);break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('isolated server did not start: '+process.stderr.read().decode())
            with sync_playwright() as pw:
                chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser=pw.chromium.launch(executable_path=str(chrome) if chrome.exists() else None,headless=True)
                context=browser.new_context(viewport={'width':1120,'height':1040},permissions=['clipboard-read','clipboard-write'])
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base)
                token=context.request.get(base+'/api/status').json()['token']
                def state():return context.request.get(base+'/qa/state').json()
                def mode(value):assert context.request.post(base+'/qa/mode',data={'value':value},headers={'X-Local-Token':token}).ok
                def card(id):return page.locator(f'[data-job-id="{id}"]')
                dialog=page.locator('#confirm-dialog')
                gemini=card('gemini')
                expect(gemini.get_by_role('button',name='临时设为公开')).to_be_visible()
                assert state()['events']==[]  # Page-load sync must be read-only.
                assert card('local').get_by_role('button',name='临时设为公开').count()==0
                assert card('failed').get_by_role('button',name='临时设为公开').count()==0
                gemini.get_by_role('button',name='临时设为公开').click()
                expect(dialog).to_contain_text('任何人都可能访问')
                dialog.get_by_role('button',name='取消',exact=True).click();assert state()['events']==[]
                gemini.get_by_role('button',name='临时设为公开').click();dialog.get_by_role('button',name='确认设为公开').click()
                expect(gemini.locator('.visibility-status')).to_have_text('当前：公开')
                assert state()['videos']['abcdefghijk']['status']['privacyStatus']=='public'
                gemini.get_by_role('button',name='复制 Gemini 用链接').click()
                expect(page.locator('#notice')).to_contain_text('Gemini 用链接已复制')
                assert page.evaluate('navigator.clipboard.readText()')=='https://youtu.be/abcdefghijk'
                expect(gemini.get_by_role('button',name='复制 Gemini 用链接')).to_be_enabled()
                page.mouse.move(0,0);gemini.screenshot(path=str(ROOT/'docs/gemini-screenshot.png'))
                mode('restore-fail');gemini.get_by_role('button',name='恢复为非公开').click()
                expect(page.locator('#notice')).to_contain_text('synthetic restore failure')
                expect(gemini.locator('.visibility-status')).to_have_text('当前：公开')
                mode('');gemini.get_by_role('button',name='恢复为非公开').click()
                expect(gemini.get_by_role('button',name='临时设为公开')).to_be_visible()
                assert state()['videos']['abcdefghijk']['status']['privacyStatus']=='private'
                mode('lost-public');gemini.get_by_role('button',name='临时设为公开').click();dialog.get_by_role('button',name='确认设为公开').click()
                expect(page.locator('#notice')).to_contain_text('synthetic lost response')
                expect(gemini.locator('.visibility-status')).to_have_text('当前：公开')
                mode('');gemini.get_by_role('button',name='恢复为非公开').click()
                expect(gemini.get_by_role('button',name='临时设为公开')).to_be_visible()
                # Failed/no-ID: exact local-only confirmation, cancel then delete.
                failed=card('failed');failed.get_by_role('button',name='删除',exact=True).click()
                expect(dialog.locator('#dialog-title')).to_have_text('删除这条本地记录？')
                expect(dialog).to_contain_text('这只会删除本机保存的历史记录和相关本地字幕/临时数据。')
                assert dialog.get_by_role('button',name='删除 YouTube 视频和本地记录',exact=True).count()==0
                dialog.get_by_role('button',name='取消',exact=True).click();expect(failed).to_be_visible()
                failed.get_by_role('button',name='删除',exact=True).click();dialog.get_by_role('button',name='删除记录',exact=True).click();expect(failed).to_have_count(0)
                assert not (Path(temp)/'subtitles/failed').exists() and not (Path(temp)/'temp/failed').exists()
                # Uploaded local-only does not call remote delete or touch other files.
                card('local').get_by_role('button',name='删除',exact=True).click();dialog.get_by_role('button',name='仅删除本地记录',exact=True).click();expect(card('local')).to_have_count(0)
                assert 'lmnopqrstuv' in state()['videos'] and (Path(temp)/'subtitles/remote/course.txt').exists()
                assert not any(e[0]=='delete' for e in state()['events'])
                # Remote deletion: two confirms, title input and failure retention.
                remote=card('remote');remote.get_by_role('button',name='删除',exact=True).click();dialog.get_by_role('button',name='删除 YouTube 视频和本地记录',exact=True).click()
                expect(dialog).to_contain_text('视频标题：Delete test')
                danger=dialog.get_by_role('button',name='永久删除视频和记录');expect(danger).to_be_disabled()
                dialog.get_by_role('textbox',name='输入完整视频标题以确认').fill('wrong');expect(danger).to_be_disabled()
                dialog.get_by_role('button',name='取消',exact=True).click();assert not any(e[0]=='delete' for e in state()['events'])
                def confirm_remote():
                    remote.get_by_role('button',name='删除',exact=True).click();dialog.get_by_role('button',name='删除 YouTube 视频和本地记录',exact=True).click()
                    dialog.get_by_role('textbox',name='输入完整视频标题以确认').fill('Delete test');expect(danger).to_be_enabled();danger.click()
                mode('delete-fail');confirm_remote();expect(remote.locator('.message')).to_contain_text('本地记录已保留')
                assert (Path(temp)/'subtitles/remote/course.txt').exists() and 'ABCDEFGHIJK' in state()['videos']
                mode('');confirm_remote();expect(remote).to_have_count(0)
                assert 'ABCDEFGHIJK' not in state()['videos'] and not (Path(temp)/'subtitles/remote').exists()
                assert state()['ids']==['gemini']
                # Auxiliary subtitles still copy; mobile modal has no overflow.
                gemini.locator('.subtitle-tools summary').click();gemini.get_by_role('button',name='复制字幕',exact=True).click()
                expect(page.locator('#notice')).to_contain_text('字幕已复制')
                assert page.evaluate('navigator.clipboard.readText()')=='synthetic subtitle'
                page.set_viewport_size({'width':390,'height':844});gemini.get_by_role('button',name='临时设为公开').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                dialog.get_by_role('button',name='取消',exact=True).click()
                assert not errors,errors
                browser.close()
            print('Gemini/删除 Chrome 测试通过：取消不写入、确认公开/复制/恢复、失败及丢失响应核对、无 ID 本地删除、上传后仅本地删除、两级危险确认、远端删除失败保留/成功清理、辅助字幕与手机布局。全部使用隔离合成数据。')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill()

if __name__=='__main__':main()
