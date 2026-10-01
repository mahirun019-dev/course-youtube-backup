#!/usr/bin/env python3
"""Real Chrome + SDK loopback OAuth regression. Google response is simulated."""
import html
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs,urlparse,urlencode
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
BOOTSTRAP='''
import json,requests,uvicorn
from app import youtube
original_send=requests.sessions.Session.send
def send(session,request,**kwargs):
    if request.url=="https://oauth2.googleapis.com/token":
        response=requests.Response();response.status_code=200;response.request=request
        response._content=json.dumps({"access_token":"synthetic-access","refresh_token":"synthetic-refresh","token_type":"Bearer","expires_in":3600,"scope":" ".join(youtube.SCOPES)}).encode()
        response.headers["Content-Type"]="application/json"
        return response
    return original_send(session,request,**kwargs)
requests.sessions.Session.send=send
def failed_open(*args,**kwargs):return False
youtube.webbrowser.open=failed_open
original_run=youtube.BrowserReadyFlow.run_local_server
def short_run(self,**kwargs):
    kwargs["timeout_seconds"]=8
    return original_run(self,**kwargs)
youtube.BrowserReadyFlow.run_local_server=short_run
uvicorn.run("app.main:app",host="127.0.0.1",port=int(__import__("os").environ["OAUTH_TEST_PORT"]),access_log=False,log_level="error")
'''

def main():
    with tempfile.TemporaryDirectory(prefix='course-oauth-ui-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-c',BOOTSTRAP],cwd=ROOT,env={**os.environ,'COURSE_BACKUP_DATA':temp,'OAUTH_TEST_PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            base=f'http://localhost:{port}'
            for _ in range(100):
                try:urllib.request.urlopen(base+'/api/status',timeout=1);break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('测试服务器未启动')
            with sync_playwright() as pw:
                chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser=pw.chromium.launch(executable_path=str(chrome) if chrome.exists() else None,headless=True)
                context=browser.new_context(viewport={'width':1120,'height':900},permissions=['clipboard-read','clipboard-write'])
                errors=[]
                def authorization(route):
                    q=parse_qs(urlparse(route.request.url).query)
                    assert q['client_id']==['synthetic.apps.googleusercontent.com']
                    assert q.get('code_challenge_method')==['S256']
                    redirect=q['redirect_uri'][0]
                    grant=redirect+'?'+urlencode({'code':'synthetic-code','state':q['state'][0]})
                    deny=redirect+'?'+urlencode({'error':'access_denied','state':q['state'][0]})
                    route.fulfill(content_type='text/html; charset=utf-8',body=f'<h1>OAuth 测试（模拟 Google 响应）</h1><a href="{html.escape(grant)}">模拟同意授权</a><br><a href="{html.escape(deny)}">模拟拒绝授权</a>')
                context.route('https://accounts.google.com/**',authorization)
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base);expect(page.locator('#connection')).to_contain_text('首次使用')
                page.locator('#connect').click()
                expect(page.locator('#notice')).to_contain_text('client_secret.json')
                assert not page.locator('#oauth-controls').is_visible()
                config=Path(temp)/'config'
                (config/'client_secret.json').write_text(json.dumps({'installed':{'client_id':'synthetic.apps.googleusercontent.com','client_secret':'synthetic-secret','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}))
                page.locator('#connect').click()
                expect(page.locator('#setup-text')).to_contain_text('自动打开浏览器失败')
                expect(page.locator('#oauth-open')).to_be_visible()
                original_url=page.locator('#oauth-open').get_attribute('href')
                assert urlparse(original_url).netloc=='accounts.google.com'
                page.locator('#oauth-copy').click()
                expect(page.locator('#notice')).to_contain_text('授权链接已复制')
                assert page.evaluate('navigator.clipboard.readText()')==original_url
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                with page.expect_popup() as info:page.locator('#oauth-open').click()
                popup=info.value;popup.get_by_role('link',name='模拟同意授权').click()
                expect(popup.locator('body')).to_contain_text('已收到 Google 授权回调')
                expect(page.locator('#setup-text')).to_contain_text('YouTube 授权完成',timeout=10000)
                expect(page.locator('#oauth-controls')).to_be_hidden()
                token=config/'token.json';assert token.exists() and token.stat().st_mode & 0o777==0o600
                saved=token.read_bytes()
                page.locator('#connect').click()
                expect(page.locator('#oauth-open')).to_be_visible()
                with page.expect_popup() as info:page.locator('#oauth-open').click()
                popup=info.value;popup.get_by_role('link',name='模拟拒绝授权').click()
                expect(page.locator('#setup-text')).to_contain_text('授权失败',timeout=10000)
                expect(page.locator('#oauth-controls')).to_be_hidden()
                assert token.read_bytes()==saved
                page.locator('#connect').click()
                expect(page.locator('#oauth-open')).to_be_visible()
                expect(page.locator('#setup-text')).to_contain_text('已超时',timeout=15000)
                expect(page.locator('#oauth-controls')).to_be_hidden()
                expect(page.locator('#connect')).to_be_enabled()
                status=context.request.get(base+'/api/status').json()
                assert not status['auth']['busy'] and status['auth']['authorization_url']==''
                assert token.read_bytes()==saved
                assert not errors,errors
                browser.close()
            print('OAuth 浏览器测试通过：缺少配置即时错误、自动打开失败、手动打开/复制链接、真实本地回调、PKCE、成功保存、拒绝/超时清理、手机宽度。Google 授权和 token 响应使用合成数据。')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill()

if __name__=='__main__':main()
