import importlib.util
import json
from pathlib import Path
import os
import signal
import socket
import subprocess
import sys
import time


def test_detached_start_is_idempotent_and_survives_launcher(tmp_path):
    script=Path(__file__).resolve().parents[2]/'scripts/service.py'
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/run.py').write_text('''import http.server, os
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok","version":"0.1.0"}')
http.server.HTTPServer(('127.0.0.1',int(os.environ['ACQUISITION_PORT'])),Handler).serve_forever()
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    # Import in a separate launcher so detached lifetime is actually exercised.
    code=f"import importlib.util; s=importlib.util.spec_from_file_location('svc',{str(script)!r}); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.start({str(tmp_path)!r},{str(sys.executable)!r},{port}))"
    pid=None
    try:
        first=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
        assert first.returncode==0,first.stderr
        pid=int(first.stdout.strip())
        second=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
        assert second.returncode==0,second.stderr
        assert int(second.stdout.strip())==pid
        os.kill(pid,0)
        assert (tmp_path/'.data/server.log').exists()
        os.kill(pid,signal.SIGTERM)
        pid=None
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))!=0:
                    break
            time.sleep(.05)
        third=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15)
        assert third.returncode==0,third.stderr
        pid=int(third.stdout.strip())
    finally:
        if pid:
            os.kill(pid,signal.SIGTERM)
