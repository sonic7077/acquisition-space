import json
import httpx
import pytest
from backend.tests.test_collection import setup, FeishuMock, add_sync_data
from backend.app.db import Record

WIKI = 'https://wcn8zd3z0yjw.feishu.cn/wiki/Me3owpcvFirsbEkqMc9cZ7PLnAf'


class WikiMock(FeishuMock):
    def __init__(self, object_type='bitable'):
        super().__init__()
        self.object_type = object_type
        self.paths = []
        self.blocks = []
        self.created = []

    def __call__(self, req):
        self.paths.append((req.method, req.url.path))
        if '/wiki/v2/' in req.url.path:
            if req.url.path.endswith('/get_node'):
                data = {'node': {'space_id': 'space1', 'node_token': 'Me3owpcvFirsbEkqMc9cZ7PLnAf', 'obj_type': self.object_type, 'obj_token': 'newbase' if self.object_type=='bitable' else 'templatedoc', 'title': '爆款空间'}}
            elif req.method == 'POST':
                self.created.append(json.loads(req.content))
                data = {'node': {'obj_type': 'docx', 'obj_token': 'exportdoc'+str(len(self.created)), 'node_token': 'exportnode'+str(len(self.created))}}
            else:
                data = {'items': [], 'has_more': False}
            return httpx.Response(200, json={'code': 0, 'data': data})
        if '/docx/v1/documents/' in req.url.path:
            body = json.loads(req.content)
            self.blocks.extend(body['children'])
            return httpx.Response(200, json={'code': 0, 'data': {'children': [{'block_id': 'block'+str(n)} for n in range(len(body['children']))]}})
        return super().__call__(req)


def configure(c):
    r = c.put('/api/settings', json={'feishu': {'url': WIKI, 'app_id': 'fake-app', 'app_secret': 'fake-secret', 'enabled': True}})
    assert r.status_code == 200, r.text


def test_wiki_bitable_resolution_and_three_table_sync(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,c,ip,_=setup
    configure(c)
    mock=WikiMock();app.state.integration_transport=httpx.MockTransport(mock)
    schema=c.get('/api/integrations/feishu/schema')
    assert schema.status_code==200,schema.text
    assert schema.json()['base_token']=='newbase'
    assert schema.json()['target']['type']=='bitable'
    add_sync_data(app,ip['id'])
    job=c.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json()
    run_sync_job(app,job['id'])
    assert len(mock.rows['tbl-works'])==1
    assert any('/apps/newbase/tables' in path for _,path in mock.paths)
    assert all('/apps/FPfJ' not in path for _,path in mock.paths)


def test_docx_target_rejected_without_writes(setup):
    app,c,ip,_=setup
    configure(c)
    mock=WikiMock('docx');app.state.integration_transport=httpx.MockTransport(mock)
    assert c.get('/api/integrations/feishu/schema').status_code==409
    assert not mock.writes


def test_wiki_read_denial_sanitized_and_no_fallback(setup):
    app,c,_,_=setup;configure(c)
    mock=WikiMock()
    def transport(req):
        if '/wiki/' in req.url.path:
            return httpx.Response(200,json={'code':999,'msg':'fake-secret-sensitive'})
        return mock(req)
    app.state.integration_transport=httpx.MockTransport(transport)
    r=c.get('/api/integrations/feishu/schema')
    assert r.status_code==409 and 'fake-secret' not in r.text
    assert all('/bitable/' not in path for _,path in mock.paths)


def test_cli_config_and_schema_admin_only(setup):
    from fastapi.testclient import TestClient
    app,c,ip,team=setup
    assert c.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).status_code==409
    c.post('/api/users',json={'username':'wiki-member','password':'Member-123456!','name':'成员','team_id':team['id'],'role':'member','ip_ids':[]})
    member=TestClient(app);member.post('/api/auth/login',json={'username':'wiki-member','password':'Member-123456!'})
    assert member.get('/api/integrations/feishu/schema').status_code==403
    assert member.put('/api/settings',json={'feishu':{'auth_mode':'cli'}}).status_code==403
    assert c.put('/api/settings',json={'feishu':{'url':'http://evil.test/wiki/token'}}).status_code==422
    r=c.put('/api/settings',json={'feishu':{'auth_mode':'cli','enabled':True,'url':WIKI}})
    assert r.status_code==200
    assert c.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).status_code==200


def test_running_sync_stops_on_destination_change(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,c,ip,_=setup
    c.put('/api/settings',json={'feishu':{'app_id':'fake','app_secret':'fake','enabled':True}})
    add_sync_data(app,ip['id']);mock=WikiMock()
    def transport(req):
        result=mock(req)
        if 'tenant_access_token' in req.url.path:
            c.put('/api/settings',json={'feishu':{'url':WIKI}})
        return result
    app.state.integration_transport=httpx.MockTransport(transport)
    job=c.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json()
    run_sync_job(app,job['id'])
    assert not mock.writes
    assert c.get('/api/collection/runs').json()[0]['status']=='failed'


def test_admin_all_scope_preview_and_sync(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,c,ip,team=setup;configure(c)
    add_sync_data(app,ip['id'])
    ip2=c.post('/api/ips',json={'name':'第二IP','team_id':team['id']}).json()
    with app.state.Session() as db:
        db.add(Record(id='second-author',kind='authors',ip_id=ip2['id'],payload={'title':'第二IP博主'}))
        db.add(Record(id='knowledge-unmapped',kind='knowledge',payload={'title':'知识材料'}))
        db.commit()
    preview=c.post('/api/integrations/feishu/preview',json={'all_scopes':True})
    assert preview.status_code==200,preview.text
    assert preview.json()['counts']=={'authors':2,'works':1,'comments':1}
    assert preview.json()['unmapped_counts']['knowledge']==1
    mock=WikiMock();app.state.integration_transport=httpx.MockTransport(mock)
    for _ in range(2):
        job=c.post('/api/integrations/feishu/sync',json={'all_scopes':True}).json()
        run_sync_job(app,job['id'])
    assert len(mock.rows['tbl-authors'])==2
    assert c.get('/api/collection/runs').json()[0]['status']=='completed'
    from fastapi.testclient import TestClient
    c.post('/api/users',json={'username':'all-member','name':'成员','password':'Member-123456!','role':'member','team_id':team['id'],'ip_ids':[ip['id']]})
    member=TestClient(app);member.post('/api/auth/login',json={'username':'all-member','password':'Member-123456!'})
    assert member.post('/api/integrations/feishu/sync',json={'all_scopes':True}).status_code==403
    assert member.post('/api/integrations/feishu/preview',json={'all_scopes':True}).status_code==403


def test_destination_change_clears_previous_table_bindings(setup):
    _,c,_,_=setup
    c.put('/api/settings',json={'feishu':{'authors_table_id':'old-authors','field_mapping':'{"authors":{"title":"old-title"}}'}})
    result=c.put('/api/settings',json={'feishu':{'url':WIKI}}).json()['feishu']
    assert not result.get('authors_table_id') and not result.get('field_mapping')


def test_new_install_defaults_to_latest_user_template(tmp_path):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    c=TestClient(create_app(tmp_path))
    c.post('/api/auth/login',json={'username':'admin','password':'admin'})
    assert c.get('/api/settings').json()['feishu']['url']==WIKI
