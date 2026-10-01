import fcntl
import os
import socket
import threading
import time
import webbrowser
import requests
import uvicorn
from .config import CONFIG, prepare

def main():
    prepare()
    lock = (CONFIG / "server.lock").open("w")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            info = requests.get("http://localhost:8000/api/status",timeout=2).json()
            if info.get("version"):
                webbrowser.open("http://localhost:8000")
                print("应用已经运行，已打开浏览器。")
                return
        except Exception:
            pass
        print("应用已在运行。请查看先前打开的 Terminal。")
        return
    sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1",8000))
    except OSError:
        print("本地端口 8000 被占用。请关闭占用该端口的程序后重新启动。")
        return
    sock.listen(128)
    def browser():
        for _ in range(60):
            try:
                if requests.get("http://localhost:8000/api/status",timeout=1).ok:
                    webbrowser.open("http://localhost:8000")
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
    threading.Thread(target=browser,daemon=True).start()
    print("课程视频备份已启动：http://localhost:8000  （停止：Control+C）")
    uvicorn.Server(uvicorn.Config("app.main:app",host="127.0.0.1",port=8000,access_log=False)).run(sockets=[sock])
    sock.close()

if __name__ == "__main__":
    os.umask(0o077)
    main()
