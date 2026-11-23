"""Use the authorized local CLI without reading or copying its credentials."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from .common import IntegrationError


def executable():
    candidate = shutil.which('lark-cli')
    bundled = Path.home()/'.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli'
    if candidate:
        return candidate
    if bundled.is_file():
        return str(bundled)
    raise IntegrationError('本机未找到已授权的飞书 CLI，请安装并登录后重试')


def request(method, path, data=None, params=None):
    if method not in ('GET','POST','PUT') or not re.fullmatch(r'/(?:bitable/v1/apps/[A-Za-z0-9/]+|wiki/v2/spaces/get_node)',path):
        raise IntegrationError('不支持的飞书 CLI 请求路径')
    args=[executable(),'api',method,'/open-apis'+path]
    if params:
        args+=['--params',json.dumps(params)]
    if data is not None:
        args+=['--data','-']
    try:
        result=subprocess.run(args,input=json.dumps(data,ensure_ascii=False) if data is not None else None,
                              text=True,capture_output=True,timeout=45)
        payload=json.loads(result.stderr if result.returncode and not result.stdout.strip() else result.stdout)
        if result.returncode or payload.get('ok') is not True:
            error=payload.get('error') or {}
            code=error.get('code')
            if code==91403:
                raise IntegrationError('飞书模板不可编辑（91403）：请在模板中添加当前 CLI 应用为文档应用，并授予可编辑权限')
            if code==99991672:
                raise IntegrationError('飞书 CLI 权限不足（99991672）：请为应用开通多维表格读写权限 bitable:app 并发布生效，同时授权访问模板')
            raise IntegrationError('飞书 CLI 请求失败；请核对登录状态、模板授权与读写权限（未回传凭证或原始错误）')
        data=payload.get('data',{})
        # Raw API CLI wraps the OpenAPI response; shortcut versions return data directly.
        if isinstance(data,dict) and 'code' in data:
            if data['code']!=0:
                raise IntegrationError('飞书返回业务错误，请核对应用权限与字段配置')
            data=data.get('data',{})
        return data
    except IntegrationError:
        raise
    except (OSError,subprocess.SubprocessError,ValueError,TypeError):
        raise IntegrationError('飞书 CLI 连接结果不确定，请核对远端记录后重试；系统将先按获客空间ID查重') from None
