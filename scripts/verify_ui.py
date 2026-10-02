#!/usr/bin/env python3
"""Real browser QA with an isolated data directory. No Google calls."""
import functools
import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix='course-ui-') as temp:
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env={**os.environ,'COURSE_BACKUP_DATA':temp},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            url=f'http://localhost:{port}'
            for i in range(100):
                try:
                    urllib.request.urlopen(url+'/api/status',timeout=1);break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('测试服务器未启动：'+process.stderr.read().decode())
            with sync_playwright() as p:
                chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser=p.chromium.launch(executable_path=str(chrome) if chrome.exists() else None,headless=True)
                context=browser.new_context(viewport={'width':1120,'height':1040},permissions=['clipboard-read','clipboard-write'])
                page=context.new_page();errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);expect(page.locator("#connection")).to_contain_text("首次使用")
                assert page.locator('#title').input_value()==''
                assert page.locator('#url').input_value()==''
                page.screenshot(path=str(ROOT/'docs/screenshot.png'),full_page=True)
                page.fill('#url','https://youtu.be/abcdefghijk');page.click('#start')
                assert not page.locator('#title').evaluate('(e)=>e.validity.valid')
                page.fill('#title','M1');page.click('#start')
                expect(page.locator("#notice")).to_contain_text("连接 YouTube")
                page.fill('#url','https://evil.com/watch?v=abcdefghijk');page.click('#start')
                expect(page.locator("#notice")).to_contain_text("URL 无效")
                # A synthetic completed job exercises copy/download affordances without a Google account.
                job={'id':'synthetic','title':'M1','source_title':'界面验证用合成课程','created_at':'2026-10-02T00:00:00Z','state':'uploaded','message':'字幕已保存，可以复制或下载。','video_id':'abcdefghijk','caption_state':'saved','language':'ja','has_subtitles':True,'has_temp':False}
                def jobs_route(route):
                    if route.request.method=='GET':route.fulfill(json=[job])
                    else:route.continue_()
                page.route('**/api/jobs',jobs_route)
                page.route('**/api/jobs/synthetic/subtitle/txt',lambda r:r.fulfill(status=200,content_type='text/plain; charset=utf-8',body='00:00:13\n合成课堂字幕'))
                page.evaluate('refresh()');page.locator('.subtitle-tools summary').click();page.get_by_role('button',name='复制字幕').wait_for()
                page.get_by_role('button',name='复制字幕').click()
                expect(page.locator("#notice")).to_contain_text("已复制")
                assert '合成课堂字幕' in page.evaluate('navigator.clipboard.readText()')
                assert page.get_by_role('link',name='下载 SRT').get_attribute('href').endswith('/srt')
                assert page.get_by_role('link',name='打开 YouTube',exact=True).get_attribute('href').startswith('https://www.youtube.com/')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT/'docs'))
                server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
                threading.Thread(target=server.serve_forever,daemon=True).start()
                try:
                    site=context.new_page();site.goto(f'http://localhost:{server.server_port}');site.wait_for_load_state('networkidle')
                    assert site.locator('img').evaluate_all('(imgs)=>imgs.length===2 && imgs.every(img=>img.complete && img.naturalWidth>0)')
                    assert site.get_by_text('本工具在用户自己的 Mac 本地运行。视频和 OAuth 信息不会发送到本项目的服务器。').is_visible()
                    site.set_viewport_size({'width':390,'height':844})
                    assert site.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                finally:server.shutdown();server.server_close()
                assert not errors,errors
                browser.close()
            print('UI 检查通过：桌面 / 手机、空标题 / 无效 URL、未授权提示、复制字幕、下载入口、Pages 和浏览器 JS 无异常。')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill()

if __name__=='__main__':main()
