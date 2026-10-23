"""测试与演示共用的独立 HTTP 服务夹具；不读取或继承模型凭据。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import httpx2


@contextmanager
def http_fixture(root, json_response=False):
    directory = root/'.fast-agent'/'http-fixture'
    directory.mkdir(parents=True,exist_ok=True)
    ready = directory/'ready.json'
    audit = directory/'audit.jsonl'
    token = secrets.token_urlsafe(24)
    argv = [sys.executable,str(Path(__file__).with_name('mcp_http_server.py')), '--workspace',str(root),
            '--port','0','--ready-file',str(ready),'--audit-file',str(audit)]
    if json_response:
        argv.append('--json-response')
    env = {k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','HOME','TMPDIR'}}
    env.update(FAST_AGENT_MCP_DEMO_TOKEN=token,PYTHONDONTWRITEBYTECODE='1')
    with (directory/'server.log').open('w') as log:
        process = subprocess.Popen(argv,env=env,stdout=log,stderr=log)
        try:
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('HTTP 示例服务启动失败，请检查本地 server.log')
                if ready.exists():
                    url = json.loads(ready.read_text())['url']
                    try:
                        # 未认证探针只验证服务已接受连接，不创建 MCP session。
                        response = httpx2.get(url,timeout=.2,trust_env=False)
                        if response.status_code==401:
                            yield {'url':url,'token':token,'process':process,'audit':audit}
                            return
                    except httpx2.HTTPError:
                        pass
                time.sleep(.05)
            raise RuntimeError('HTTP 示例服务启动超时')
        finally:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=3)
