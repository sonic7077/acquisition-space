"""Run the local HTTP server and durable writer worker together."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from backend.app.env import load_env_file

load_env_file(ROOT / '.env')
port = os.environ.get('ACQUISITION_PORT', '8787')
from backend.app.main import create_app
app = create_app()
print(f'\n获客空间工作台：http://127.0.0.1:{port}', flush=True)
print('默认管理员用户名：admin，密码：admin。模型与API Key统一在系统设置中配置。\n', flush=True)
processes = []


def stop(*_):
    for process in processes:
        if process.poll() is None:
            process.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    processes.append(subprocess.Popen([sys.executable, '-m', 'backend.app.worker']))
    processes.append(subprocess.Popen([sys.executable, '-m', 'backend.app.publish_worker']))
    processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', port]))
    while all(process.poll() is None for process in processes):
        time.sleep(1)
finally:
    stop()
    for process in processes:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
