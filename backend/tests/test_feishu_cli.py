import json
import subprocess
import pytest
from backend.app.integrations.common import IntegrationError


def test_cli_uses_credential_store_and_stdin(monkeypatch):
    from backend.app.integrations import feishu_cli
    seen=[]
    def run(args, **kwargs):
        seen.append((args,kwargs))
        return subprocess.CompletedProcess(args,0,json.dumps({'ok':True,'data':{'items':[]}}),'')
    monkeypatch.setattr(feishu_cli,'executable',lambda:'/trusted/lark-cli')
    monkeypatch.setattr(subprocess,'run',run)
    assert feishu_cli.request('POST','/bitable/v1/apps/token/tables/tbl/records',{'fields':{'正文':'样例'}})=={'items':[]}
    args,kw=seen[0]
    assert '--data' in args and args[args.index('--data')+1]=='-'
    assert json.loads(kw['input'])['fields']['正文']=='样例'
    assert 'shell' not in kw and '样例' not in str(args)


def test_cli_failure_is_sanitized_and_reports_missing_scope(monkeypatch):
    from backend.app.integrations import feishu_cli
    monkeypatch.setattr(feishu_cli,'executable',lambda:'/trusted/lark-cli')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess([],3,'',json.dumps({'ok':False,'error':{'code':99991672,'message':'private-token','missing_scopes':['bitable:app']}})))
    with pytest.raises(IntegrationError) as e:
        feishu_cli.request('GET','/bitable/v1/apps/token/tables')
    assert '99991672' in str(e.value) and 'private-token' not in str(e.value)
    with pytest.raises(IntegrationError):
        feishu_cli.request('GET','https://evil.test')


def test_template_edit_permission_error_is_distinct_from_api_scope(monkeypatch):
    from backend.app.integrations import feishu_cli
    monkeypatch.setattr(feishu_cli,'executable',lambda:'/trusted/lark-cli')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess([],1,'',json.dumps({'ok':False,'error':{'code':91403,'message':'Forbidden private-token'}})))
    with pytest.raises(IntegrationError) as e:
        feishu_cli.request('POST','/bitable/v1/apps/token/tables/tbl/fields',{'field_name':'获客空间ID','type':1})
    assert '91403' in str(e.value) and '可编辑' in str(e.value) and 'private-token' not in str(e.value)
