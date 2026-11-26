import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db import Record


@pytest.fixture()
def setup(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    team = client.post('/api/teams', json={'name': '采集组'}).json()
    ip = client.post('/api/ips', json={'name': '顾问IP', 'team_id': team['id']}).json()
    # Legacy direct-base fixture remains explicit; production defaults leave URL unconfigured until set.
    client.put('/api/settings',json={'feishu':{'url':'https://example-tenant.feishu.cn/base/ExampleBaseToken001'}})
    client.put('/api/settings', json={'collection': {'api_key': 'test-tikhub-private', 'enabled': True, 'daily_budget': 50}})
    return app, client, ip, team


def video(key='v1', likes=500):
    return {'aweme_id': key, 'desc': '选择服务之前先问清楚', 'create_time': 1770000000,
            'author': {'uid': 'u1', 'sec_uid': 'sec-u1', 'nickname': '真实作者', 'follower_count': 6000, 'signature': '专业服务'},
            'video': {'duration': 45000, 'cover': {'url_list': ['https://example.org/cover.jpg']}},
            'statistics': {'digg_count': likes, 'comment_count': 2, 'collect_count': 80, 'share_count': 30}}


def options(ip_id, **extra):
    return {'ip_id': ip_id, 'platforms': ['douyin'], 'keywords': '咨询服务', 'pages': 2, 'min_likes': 300, 'min_duration': 30, 'max_videos': 3, 'comment_pages': 1, 'enrich_authors': False, **extra}


def test_keyword_collection_routes_and_pagination(setup):
    from backend.app.integrations.collection import run_collection_job
    app, client, ip, team = setup
    seen = []
    def handler(req):
        assert req.headers['authorization'] == 'Bearer test-tikhub-private'
        seen.append((req.method, req.url.path, dict(req.url.params), json.loads(req.content) if req.content else None))
        if 'fetch_video_search_v1' in req.url.path:
            body = json.loads(req.content)
            if body['cursor'] == 0:
                data = {'data': [{'aweme_info': video()}], 'cursor': 12, 'search_id': 'sid-1', 'backtrace': 'bt-1', 'has_more': 1}
            else:
                assert body['cursor'] == 12 and body['search_id'] == 'sid-1' and body['backtrace'] == 'bt-1'
                data = {'data': [{'aweme_info': video()}, {'aweme_info': video('v2', 100)}], 'cursor': 24, 'has_more': 0}
        elif 'fetch_video_comments' in req.url.path:
            assert req.url.params['aweme_id'] in ('v1','v2')
            data = {'comments': [{'cid': 'c-'+req.url.params['aweme_id'], 'text': '请问多少钱？', 'digg_count': 3, 'user': {'uid': 'c-u1', 'nickname': '咨询者'}}], 'cursor': 20, 'has_more': 0}
        else:
            raise AssertionError(str(req.url))
        return httpx.Response(200, json={'code': 200, 'data': data})
    app.state.integration_transport = httpx.MockTransport(handler)
    r = client.post('/api/collection/search', json=options(ip['id']))
    assert r.status_code == 200, r.text
    assert r.json()['estimated_calls'] == 5
    job_id = r.json()['id']
    run_collection_job(app, job_id)
    job = client.get('/api/collection/runs').json()[0]
    assert job['status'] == 'completed', job
    assert job['calls'] == 4
    mats = client.get('/api/records/materials').json()
    authors = client.get('/api/records/authors').json()
    leads = client.get('/api/records/leads').json()
    assert len(mats) == 2 and len(authors) == 1 and len(leads) == 2
    mat = next(m for m in mats if m['platform_id'] == 'v1')
    assert mat['likes'] == 500 and mat['duration'] == 45 and mat['author_id'] == authors[0]['id']
    assert mat['collected_comments'] == 1
    lead=next(l for l in leads if l['material_id']==mat['id'])
    assert lead['platform_comment_id'] == 'c-v1'
    assert leads[0]['contactable'] is False and leads[0]['intent'] == '问价格'
    assert client.get('/api/dashboard').json()['leads'] == 0
    assert 'test-tikhub-private' not in json.dumps(job)
    # Same upstream IDs refresh without duplicates, including an excluded material.
    client.patch('/api/records/materials/'+mat['id'], json={'status': 'excluded'})
    second = client.post('/api/collection/search', json=options(ip['id'])).json()
    run_collection_job(app, second['id'])
    assert len(client.get('/api/records/materials').json()) == 2
    assert len(client.get('/api/records/leads').json()) == 2
    assert next(m for m in client.get('/api/records/materials').json() if m['id'] == mat['id'])['status'] == 'excluded'


def test_budget_enforced_before_calls_and_no_infinite_retry(setup):
    from backend.app.integrations.collection import run_collection_job
    app, client, ip, team = setup
    client.put('/api/settings', json={'collection': {'daily_budget': 1}})
    seen = []
    app.state.integration_transport = httpx.MockTransport(lambda r: (seen.append(r) or httpx.Response(200, json={'code': 200, 'data': {'data': [{'aweme_info': video()}], 'has_more': 1, 'cursor': 1}})))
    job = client.post('/api/collection/search', json=options(ip['id'])).json()
    run_collection_job(app, job['id'])
    current = client.get('/api/collection/runs').json()[0]
    assert len(seen) == 1 and current['calls'] == 1 and current['status'] == 'partial'
    assert '预算' in current['error']
    assert len(client.get('/api/records/materials').json()) == 1


def test_auth_error_never_echoes_key_and_requires_manual_retry(setup):
    from backend.app.integrations.collection import run_collection_job
    app, client, ip, team = setup
    seen=[]
    app.state.integration_transport = httpx.MockTransport(lambda r: (seen.append(r) or httpx.Response(401, json={'error':'test-tikhub-private'})))
    job=client.post('/api/collection/search',json=options(ip['id'])).json()
    run_collection_job(app,job['id'])
    rows=client.get('/api/collection/runs').json()
    assert len(seen)==1 and rows[0]['status']=='failed'
    assert '401' in rows[0]['error'] and 'test-tikhub-private' not in str(rows)
    assert client.post('/api/collection/retry/'+job['id'], json={}).status_code==200


def test_unauthorized_ip_and_admin_configuration_only(setup):
    app, client, ip, team = setup
    client.post('/api/users', json={'username':'member','password':'Member-123456!','name':'队员','role':'member','team_id':team['id'],'ip_ids':[]})
    member=TestClient(app);member.post('/api/auth/login',json={'username':'member','password':'Member-123456!'})
    assert member.post('/api/collection/search',json=options(ip['id'])).status_code==403
    assert member.get('/api/integrations/feishu/schema').status_code==403
    assert member.post('/api/integrations/feishu/prepare',json={}).status_code==403
    assert client.post('/api/collection/search',json=options(ip['id'],pages=100)).status_code==422
    client.put('/api/settings',json={'collection':{'api_key':None}})
    assert client.post('/api/collection/search',json=options(ip['id'])).status_code==409


def test_comments_can_be_fetched_again_without_duplicates(setup):
    from backend.app.integrations.collection import run_collection_job
    app, client, ip, team=setup
    def handler(req):
        data={'data':[{'aweme_info':video()}],'has_more':0} if req.method=='POST' else {'comments':[{'cid':'c1','text':'求资料','user':{'nickname':'用户'}}],'has_more':0}
        return httpx.Response(200,json={'code':200,'data':data})
    app.state.integration_transport=httpx.MockTransport(handler)
    job=client.post('/api/collection/search',json=options(ip['id'],pages=1,comment_pages=1)).json();run_collection_job(app,job['id'])
    mat=client.get('/api/records/materials').json()[0]
    for _ in range(2):
        r=client.post('/api/collection/materials/'+mat['id']+'/comments',json={'pages':1});assert r.status_code==200,r.text
        run_collection_job(app,r.json()['id'])
    assert len(client.get('/api/records/leads').json())==1


class FeishuMock:
    def __init__(self, lost_create=False):
        self.tables=[{'table_id':'tbl-authors','name':'博主'},{'table_id':'tbl-works','name':'作品'},{'table_id':'tbl-comments','name':'评论'}]
        self.fields={t['table_id']:[{'field_id':'f1','field_name':'获客空间ID','type':1},{'field_id':'f2','field_name':'标题','type':1},{'field_id':'f3','field_name':'内容结构','type':1},{'field_id':'formula','field_name':'推荐','type':20}] for t in self.tables}
        self.rows={t['table_id']:{} for t in self.tables};self.writes=[];self.lost_create=lost_create
    def __call__(self,req):
        path=req.url.path;data=json.loads(req.content) if req.content else {}
        if 'tenant_access_token' in path:return httpx.Response(200,json={'code':0,'tenant_access_token':'fake-feishu-token','expire':7200})
        assert req.headers['Authorization']=='Bearer fake-feishu-token'
        if path.endswith('/wiki/v2/spaces/get_node'):
            return httpx.Response(200,json={'code':0,'data':{'node':{'obj_type':'bitable','obj_token':'mock-base','title':'测试爆款空间'}}})
        tid=next((x for x in self.fields if x in path),None)
        if path.endswith('/tables'):
            payload={'items':self.tables,'has_more':False}
        elif path.endswith('/fields'):
            if req.method=='POST':self.fields[tid].append({'field_id':'new-'+data['field_name'],'field_name':data['field_name'],'type':data['type']})
            payload={'items':self.fields[tid],'has_more':False}
        elif path.endswith('/records/search'):
            value=data['filter']['conditions'][0]['value'][0]
            payload={'items':[{'record_id':key,'fields':row} for key,row in self.rows[tid].items() if row.get('获客空间ID')==value],'has_more':False}
        elif path.endswith('/records') and req.method=='POST':
            rid='rec-'+str(len(self.rows[tid])+1);self.rows[tid][rid]=data['fields'];self.writes.append((tid,data['fields']))
            if self.lost_create:self.lost_create=False;raise httpx.ReadTimeout('lost after committed',request=req)
            payload={'record':{'record_id':rid}}
        elif '/records/' in path and req.method=='PUT':
            rid=path.split('/')[-1];self.rows[tid][rid]=data['fields'];self.writes.append((tid,data['fields']));payload={'record':{'record_id':rid}}
        else:raise AssertionError((req.method,path,data))
        return httpx.Response(200,json={'code':0,'data':payload})


def add_sync_data(app, ip_id):
    with app.state.Session() as db:
        a=Record(id='local-author',kind='authors',ip_id=ip_id,payload={'title':'真实作者','platform':'抖音','platform_id':'u1','followers':6000})
        m=Record(id='local-work',kind='materials',ip_id=ip_id,payload={'title':'原视频描述','body':'原视频描述','platform':'抖音','platform_id':'v1','author_id':a.id,'likes':500,'comments_count':2})
        c=Record(id='local-comment',kind='leads',ip_id=ip_id,payload={'title':'评论','body':'求资料','platform_comment_id':'c1','material_id':m.id,'author_id':a.id,'source_type':'platform_comment','contactable':False})
        db.add_all([a,m,c]);db.commit()


def test_feishu_schema_and_three_table_upserts(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,client,ip,team=setup
    client.put('/api/settings',json={'feishu':{'app_id':'fake-app','app_secret':'fake-secret','enabled':True}})
    mock=FeishuMock();app.state.integration_transport=httpx.MockTransport(mock)
    schema=client.get('/api/integrations/feishu/schema')
    assert schema.status_code==200,schema.text
    assert schema.json()['tables']['authors']['name']=='博主'
    add_sync_data(app,ip['id'])
    for _ in range(2):
        r=client.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']});assert r.status_code==200,r.text
        run_sync_job(app,r.json()['id'])
    assert [len(v) for v in mock.rows.values()]==[1,1,1]
    assert [x[0] for x in mock.writes[:3]]==['tbl-authors','tbl-works','tbl-comments']
    work=next(iter(mock.rows['tbl-works'].values()))
    structure=json.loads(work['内容结构'])
    assert structure['author_id']=='local-author' and structure['likes']==500
    assert '推荐' not in work
    rows=client.get('/api/collection/runs').json()
    assert rows[0]['status']=='completed'
    assert 'fake-secret' not in str(rows)


def test_feishu_uncertain_create_reconciles_before_retry(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,client,ip,team=setup
    client.put('/api/settings',json={'feishu':{'app_id':'fake-app','app_secret':'fake-secret','enabled':True}})
    mock=FeishuMock(lost_create=True);app.state.integration_transport=httpx.MockTransport(mock)
    add_sync_data(app,ip['id'])
    job=client.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json()
    run_sync_job(app,job['id'])
    first=next(r for r in client.get('/api/collection/runs').json() if r['id']==job['id'])
    assert first['status']=='partial' and '核对' in first['error']
    assert len(mock.rows['tbl-authors'])==1
    retry=client.post('/api/collection/retry/'+job['id'],json={}).json()
    run_sync_job(app,retry['id'])
    assert [len(v) for v in mock.rows.values()]==[1,1,1]


def test_feishu_prepare_preserves_formulas_and_relation_types(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,client,ip,_=setup
    client.put('/api/settings',json={'feishu':{'app_id':'fake-app','app_secret':'fake-secret','enabled':True}})
    mock=FeishuMock()
    for tid in mock.fields:
        mock.fields[tid]=[f for f in mock.fields[tid] if f['field_name'] not in ('获客空间ID','内容结构')]
    mock.fields['tbl-works'] += [
        {'field_id':'likes','field_name':'点赞数','type':2},
        {'field_id':'url','field_name':'作品链接','type':15},
        {'field_id':'author','field_name':'博主关联','type':18,'property':{'table_id':'tbl-authors'}}]
    mock.fields['tbl-comments'] += [{'field_id':'work','field_name':'作品关联','type':21,'property':{'table_id':'tbl-works'}}]
    app.state.integration_transport=httpx.MockTransport(mock)
    assert not client.get('/api/integrations/feishu/schema').json()['ready']
    prepared=client.post('/api/integrations/feishu/prepare',json={})
    assert prepared.status_code==200 and prepared.json()['ready']
    add_sync_data(app,ip['id'])
    with app.state.Session() as db:
        work=db.get(Record,'local-work');work.payload={**work.payload,'source':'https://www.douyin.com/video/v1'};db.commit()
    job=client.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json();run_sync_job(app,job['id'])
    work=next(iter(mock.rows['tbl-works'].values()))
    assert work['点赞数']==500 and work['作品链接']['link'].endswith('/v1')
    assert work['博主关联']==['rec-1']
    assert next(iter(mock.rows['tbl-comments'].values()))['作品关联']==['rec-1']
    assert all('推荐' not in row for _,row in mock.writes)
    assert all(any(f['type']==20 for f in fields) for fields in mock.fields.values())


def test_worker_runs_collection_then_automatic_feishu_and_ip_isolation(setup):
    from backend.app.worker import run_once
    app,client,ip,team=setup
    client.put('/api/settings',json={'feishu':{'app_id':'fake-app','app_secret':'fake-secret','enabled':True}})
    mock=FeishuMock()
    def handler(req):
        if req.url.host=='api.tikhub.io':
            data={'comments':[],'has_more':0} if 'comments' in req.url.path else {'data':[{'aweme_info':video()}],'has_more':0}
            return httpx.Response(200,json={'code':200,'data':data})
        return mock(req)
    app.state.integration_transport=httpx.MockTransport(handler)
    job=client.post('/api/collection/search',json=options(ip['id'],pages=1,comment_pages=1)).json()
    assert run_once(app) and run_once(app)
    runs=client.get('/api/collection/runs').json()
    assert len(runs)==2 and all(r['status']=='completed' for r in runs)
    assert [len(v) for v in mock.rows.values()]==[1,1,0]
    other=client.post('/api/ips',json={'name':'另一个IP','team_id':team['id']}).json()
    next_job=client.post('/api/collection/search',json=options(other['id'],pages=1,comment_pages=1)).json()
    assert run_once(app)
    assert len(client.get('/api/records/materials').json())==2
    client.post('/api/users',json={'username':'scoped','password':'Scoped-12345!','name':'队员','role':'member','team_id':team['id'],'ip_ids':[other['id']]})
    member=TestClient(app);member.post('/api/auth/login',json={'username':'scoped','password':'Scoped-12345!'})
    assert len(member.get('/api/records/materials').json())==1
    assert member.post('/api/collection/retry/'+job['id'],json={}).status_code==403


def test_cleared_credentials_cancel_already_queued_run(setup):
    from backend.app.integrations.collection import run_collection_job
    app,client,ip,_=setup
    job=client.post('/api/collection/search',json=options(ip['id'])).json()
    client.put('/api/settings',json={'collection':{'api_key':None}})
    app.state.integration_transport=httpx.MockTransport(lambda r:pytest.fail('must not call when key cleared'))
    run_collection_job(app,job['id'])
    assert client.get('/api/collection/runs').json()[0]['status']=='failed'


def test_malformed_search_stops_and_repeated_cursor_is_bounded(setup):
    from backend.app.integrations.collection import run_collection_job
    app,client,ip,_=setup
    app.state.integration_transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'code':200,'data':{'data':None}}))
    job=client.post('/api/collection/search',json=options(ip['id'])).json();run_collection_job(app,job['id'])
    assert client.get('/api/collection/runs').json()[0]['status']=='failed'
    seen=[]
    app.state.integration_transport=httpx.MockTransport(lambda r:(seen.append(r) or httpx.Response(200,json={'code':200,'data':{'data':[{'aweme_info':video()}],'cursor':0,'has_more':1}})))
    job=client.post('/api/collection/search',json=options(ip['id'])).json();run_collection_job(app,job['id'])
    current=client.get('/api/collection/runs').json()[0]
    assert len(seen)==2 and current['status']=='partial' and '游标' in current['error']


def test_revoking_key_during_run_stops_further_paid_requests(setup):
    from backend.app.integrations.collection import run_collection_job
    app,client,ip,_=setup
    calls=[]
    def handler(req):
        calls.append(req)
        # Simulate an administrator removing credentials while first request is in flight.
        client.put('/api/settings',json={'collection':{'api_key':None}})
        return httpx.Response(200,json={'code':200,'data':{'data':[{'aweme_info':video()}],'has_more':1,'cursor':1}})
    app.state.integration_transport=httpx.MockTransport(handler)
    job=client.post('/api/collection/search',json=options(ip['id'])).json();run_collection_job(app,job['id'])
    assert len(calls)==1
    assert client.get('/api/collection/runs').json()[0]['status']=='partial'


def test_keywords_are_preserved_across_queries_and_profile_enrichment(setup):
    from backend.app.integrations.collection import run_collection_job
    app,client,ip,_=setup
    paths=[]
    def handler(req):
        paths.append(req.url.path)
        data={'user':{**video()['author'],'follower_count':9000}} if 'fetch_user_profile' in req.url.path else ({'comments':[],'has_more':0} if 'comments' in req.url.path else {'data':[{'aweme_info':video()}],'has_more':0})
        return httpx.Response(200,json={'code':200,'data':data})
    app.state.integration_transport=httpx.MockTransport(handler)
    job=client.post('/api/collection/search',json=options(ip['id'],keywords='咨询,服务',pages=1,enrich_authors=True,comment_pages=1)).json();run_collection_job(app,job['id'])
    assert len(paths)==4
    assert client.get('/api/records/authors').json()[0]['followers']==9000
    assert client.get('/api/records/materials').json()[0]['keywords']==['咨询','服务']


def test_disabling_feishu_during_sync_stops_next_request(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,client,ip,_=setup
    client.put('/api/settings',json={'feishu':{'app_id':'fake-app','app_secret':'fake-secret','enabled':True}})
    mock=FeishuMock();calls=[]
    def handler(req):
        calls.append(req)
        response=mock(req)
        if 'tenant_access_token' in req.url.path:
            client.put('/api/settings',json={'feishu':{'enabled':False}})
        return response
    app.state.integration_transport=httpx.MockTransport(handler)
    job=client.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json();run_sync_job(app,job['id'])
    assert len(calls)==1
    assert not mock.writes
    assert client.get('/api/collection/runs').json()[0]['status']=='failed'


def test_feishu_remote_edit_conflict_is_not_overwritten(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,client,ip,_=setup
    client.put('/api/settings',json={'feishu':{'app_id':'fake-app','app_secret':'fake-secret','enabled':True}})
    mock=FeishuMock();app.state.integration_transport=httpx.MockTransport(mock)
    add_sync_data(app,ip['id'])
    job=client.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json();run_sync_job(app,job['id'])
    mock.rows['tbl-works']['rec-1']['标题']='飞书同事修改的标题'
    job=client.post('/api/integrations/feishu/sync',json={'ip_id':ip['id']}).json();run_sync_job(app,job['id'])
    assert mock.rows['tbl-works']['rec-1']['标题']=='飞书同事修改的标题'
    current=client.get('/api/collection/runs').json()[0]
    assert current['status']=='partial' and '冲突' in current['error']
