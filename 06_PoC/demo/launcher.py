"""One local entry point for the two preview services."""
from http.server import ThreadingHTTPServer
import socket
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import serve


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.5): return True
    except OSError: return False


def is_engineering():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/healthz',timeout=1) as response:
            return response.status == 200 and b'backend' in response.read(8192)
    except (OSError, urllib.error.URLError): return False


def main():
    if port_open(8773):
        print('端口 8773 已被使用。请关闭旧的探索版服务后重试。',flush=True)
        return 1
    child=None
    if port_open(8765):
        if not is_engineering():
            print('端口 8765 被其他服务占用，无法启动工程版。',flush=True)
            return 1
        print('使用已运行的工程版服务。',flush=True)
    else:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        env = os.environ.copy()
        poc = serve.HERE.parent
        if not (poc / 'etl' / 'normalized.json').exists() and not (poc / 'etl' / 'with_inferred.json').exists():
            env.setdefault('GRAPH_SOURCE_FILE', str(poc / 'release' / 'sample' / 'sample_normalized.json'))
            env.setdefault('GRAPH_DATA_FILE', str(poc / 'release' / 'sample' / 'sample_graph.json'))
            print('工程版使用仓库内的发布样例；完整原图未随仓库提供。', flush=True)
        child=subprocess.Popen([sys.executable, str(poc / 'viz_server.py'), '--port', '8765'],
                               cwd=str(poc),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,creationflags=creationflags,env=env)
        for _ in range(40):
            if is_engineering() or child.poll() is not None: break
            time.sleep(.25)
        if not is_engineering():
            child.terminate(); child.wait(timeout=5)
            print('工程版启动失败；请检查 Python 依赖和端口 8765。',flush=True)
            return 1
    try:
        httpd=ThreadingHTTPServer(('127.0.0.1',8773),serve.Handler)
        print('探索版：http://127.0.0.1:8773/demo/',flush=True)
        print('工程版：http://127.0.0.1:8765/',flush=True)
        print('请保持窗口打开；按 Ctrl+C 停止本次启动的服务。',flush=True)
        try: httpd.serve_forever()
        except KeyboardInterrupt: pass
        finally: httpd.server_close()
    finally:
        if child and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
