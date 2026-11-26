"""Start the workbench independently of the terminal; serialize double clicks."""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def healthy(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
            data=json.load(response)
            return data.get('status')=='ok' and data.get('version')=='0.1.0'
    except (OSError,ValueError):
        return False


def start(root, python, port=8787):
    root=Path(root).resolve()
    data=root/'.data';data.mkdir(exist_ok=True)
    with (data/f'service-{port}.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        pidfile=data/f'service-{port}.pid'
        if healthy(port):
            return int(pidfile.read_text()) if pidfile.exists() else 0
        with socket.socket() as probe:
            probe.settimeout(.5)
            # Binding a recently stopped port misclassifies TCP TIME_WAIT as a listener.
            if probe.connect_ex(('127.0.0.1',port))==0:
                raise RuntimeError(f'端口{port}已占用且健康检查失败，请查看 .data/server.log')
        env={**os.environ,'ACQUISITION_PORT':str(port),'PYTHONUNBUFFERED':'1'}
        with (data/'server.log').open('ab') as log:
            process=subprocess.Popen([python,str(root/'scripts/run.py')],cwd=root,env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                start_new_session=True,close_fds=True)
        pidfile.write_text(str(process.pid))
        for _ in range(100):
            if process.poll() is not None:
                pidfile.unlink(missing_ok=True)
                raise RuntimeError('服务启动失败，请查看 .data/server.log')
            if healthy(port):
                return process.pid
            time.sleep(.1)
        process.terminate()
        pidfile.unlink(missing_ok=True)
        raise RuntimeError('服务启动超时，请查看 .data/server.log')


if __name__=='__main__':
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from backend.app.env import load_env_file
    load_env_file(root / '.env')
    port=int(os.environ.get('ACQUISITION_PORT','8787'))
    try:
        pid=start(root,sys.executable,port)
        print(f'获客空间已启动：http://127.0.0.1:{port}（进程 {pid or "已运行"}）\n关闭此窗口不会停止服务。日志：.data/server.log')
    except RuntimeError as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
