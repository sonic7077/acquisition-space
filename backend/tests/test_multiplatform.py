import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db import Record
from backend.app.integrations.collection import run_collection_job


@pytest.fixture()
def env(tmp_path):
    app=create_app(tmp_path); c=TestClient(app)
    c.post('/api/auth/login',json={'username':'admin','password':'admin'})
    team=c.post('/api/teams',json={'name':'多平台组'}).json()
    ip=c.post('/api/ips',json={'name':'业务IP','team_id':team['id']}).json()
    c.put('/api/settings',json={'collection':{'enabled':True,'api_key':'test-private','daily_budget':100}})
    return app,c,team,ip


def start(c,**opts):
    r=c.post('/api/collection/search',json={'keywords':'咨询服务','pages':1,'max_videos':20,'comment_pages':1,**opts})
    assert r.status_code==200,r.text
    return r.json()['id']


def transport(req):
    path=req.url.path
    if 'douyin/search' in path:
        data={'data':[{'aweme_info':{'aweme_id':'same-id','desc':'抖音内容','author':{'uid':'author','nickname':'抖音博主'},'statistics':{'digg_count':2},'video':{'duration':1000}}}], 'has_more':0}
    elif 'douyin/web/fetch_video_comments' in path:
        data={'comments':[{'cid':'comment','text':'求资料','user':{'uid':'visitor','nickname':'读者'}}],'has_more':0}
    elif path.endswith('search_notes'):
        data={'data':{'items':[{'id':'same-id','model_type':'note','note_card':{'note_id':'same-id','display_title':'小红书内容','type':'normal','user':{'user_id':'author','nickname':'小红书博主'},'interact_info':{'liked_count':'1200','comment_count':'3'}}}], 'has_more':False,'search_id':'sid','search_session_id':'session'}}
    elif path.endswith('get_note_comments'):
        data={'data':{'comments':[{'id':'comment','content':'多少钱','user_info':{'user_id':'reader','nickname':'笔记读者'},'like_count':1}],'has_more':False,'cursor':'end','index':1,'pageArea':'UNFOLDED'}}
    elif path.endswith('fetch_search_videos'):
        data={'items':[{'title':'<em>视频号</em>内容','exportId':'export/sample','docID':14941130915890399732,'jumpInfo':{'extInfo':{'feedNonceId':14941130915890399733}}}],'continue_flag':False}
    elif path.endswith('fetch_video_detail'):
        body=json.loads(req.content);assert body['export_id']=='export/sample';assert body['object_nonce_id']=='14941130915890399733'
        data={'id':14941130915890399732,'username':'v2_author@finder','nickname':'视频号博主','title':'视频号内容','like_count':50,'comment_count':1,'create_time':1770000000,'media':{'url':'https://ignored.example/video','decode_key':'not-saved'}}
    elif path.endswith('fetch_video_comments'):
        body=json.loads(req.content);assert body['object_id']=='14941130915890399732';assert body['raw'] is False
        data={'comments':[{'comment_id':14941130915890399734,'content':'要资料','nickname':'视频号读者','username':'reader','like_count':3}],'down_continue':False}
    else:
        raise AssertionError(path)
    return httpx.Response(200,json={'code':200,'data':data})


def test_default_collection_options_match_new_saved_search_options(env):
    _, client, _, _ = env
    query = {'keywords': '咨询服务'}
    run = client.post('/api/collection/search', json=query)
    plan = client.post('/api/discovery/searches', json={'name': '默认采集方案', 'options': query})
    assert run.status_code == 200, run.text
    assert plan.status_code == 200, plan.text
    expected = {
        'ip_id': None,
        'platforms': ['douyin', 'xiaohongshu'],
        'keywords': '咨询服务',
        'pages': 1,
        'max_videos': 20,
        'per_word_limit': 10,
        'comment_pages': 1,
        'enrich_authors': False,
        'sort_type': '1',
        'publish_time': 'platform_default',
        'keywords_list': ['咨询服务'],
        'estimated_calls': 22,
    }
    assert run.json()['options'] == expected
    assert plan.json()['options'] == expected
    assert client.get('/api/discovery/searches').json()[0]['options'] == expected


@pytest.mark.parametrize(('time_mode', 'expected_dates'), [
    ('platform_default', ('180', '不限', 'half_year')),
    ('0', ('0', '不限', 'all')),
    ('1', ('1', '一天内', 'day')),
    ('7', ('7', '一周内', 'week')),
    ('180', ('180', '半年内', 'half_year')),
])
def test_collection_search_date_filters_match_each_provider(env, time_mode, expected_dates):
    app, client, _, _ = env
    dates = []

    def handler(request):
        path = request.url.path
        if 'douyin/search' in path or path.endswith('fetch_search_videos'):
            dates.append(json.loads(request.content)['publish_time'])
        elif path.endswith('search_notes'):
            dates.append(request.url.params['time_filter'])
        return transport(request)

    app.state.integration_transport = httpx.MockTransport(handler)
    key = start(client, platforms=['douyin', 'xiaohongshu', 'wechat_channels'], publish_time=time_mode)
    run_collection_job(app, key)
    run = client.get('/api/collection/runs').json()[0]
    assert run['status'] == 'completed', run
    assert tuple(dates) == expected_dates


def test_three_platform_search_without_ip_automatically_imports_comments(env):
    app,c,team,ip=env; seen=[]
    app.state.integration_transport=httpx.MockTransport(lambda r:(seen.append(r.url.path) or transport(r)))
    key=start(c,platforms=['douyin','xiaohongshu','wechat_channels'],min_likes=300,min_duration=30)
    with app.state.Session() as db:
        job = db.get(Record, key)
        assert 'min_likes' not in job.payload['options'] and 'min_duration' not in job.payload['options']
        # Older queued jobs and retries can still contain the retired settings.
        job.payload = {**job.payload, 'options': {**job.payload['options'], 'min_likes': 300, 'min_duration': 30}}
        db.commit()
    run_collection_job(app,key)
    run=c.get('/api/collection/runs').json()[0]
    assert run['status']=='completed',run
    assert run['calls']==7 and run['estimated_calls']==43
    mats=c.get('/api/records/materials').json();leads=c.get('/api/records/leads').json()
    assert len(mats)==3 and len(leads)==3
    assert {m['platform'] for m in mats}=={'抖音','小红书','视频号'}
    assert all(m['ip_id'] is None and m['collected_comments']==1 and m['comments_status']=='completed' for m in mats)
    assert {l['platform_comment_id'] for l in leads}=={'comment','14941130915890399734'}
    assert all(l['contactable'] is False for l in leads)
    assert 'decode_key' not in json.dumps(mats)
    wc=next(m for m in mats if m['platform']=='视频号');assert wc['platform_id']=='14941130915890399732'
    assert all('eligible' not in m and m['discovery']['available'] for m in mats)
    assert 'xiaohongshu' in c.get('/api/collection/status').json()['platform_ids']


def test_unassigned_pool_isolated_and_adoption_requires_authorized_ip(env):
    app,c,team,ip=env
    def member(name,teamid,role='member'):
        c.post('/api/users',json={'username':name,'name':name,'password':'Member-123456!','role':role,'team_id':teamid,'ip_ids':[ip['id']] if teamid==team['id'] else []})
        x=TestClient(app);assert x.post('/api/auth/login',json={'username':name,'password':'Member-123456!'}).status_code==200;return x
    a=member('alice',team['id']);b=member('bob',team['id']);lead=member('leader',team['id'],'leader')
    other=c.post('/api/teams',json={'name':'其他组'}).json();outsider=member('outsider',other['id'],'leader')
    app.state.integration_transport=httpx.MockTransport(transport)
    key=start(a,platforms=['douyin']);run_collection_job(app,key)
    mats=a.get('/api/records/materials').json();assert len(mats)==1
    assert b.get('/api/records/materials').json()==[] and outsider.get('/api/collection/runs').json()==[]
    assert len(lead.get('/api/records/materials').json())==1
    assert b.patch('/api/records/materials/'+mats[0]['id'],json={'status':'excluded'}).status_code==403
    assert a.post('/api/materials/'+mats[0]['id']+'/adopt',json={}).status_code==422
    assert outsider.post('/api/materials/'+mats[0]['id']+'/adopt',json={'ip_id':ip['id']}).status_code==403
    r=a.post('/api/materials/'+mats[0]['id']+'/adopt',json={'ip_id':ip['id']});assert r.status_code==200,r.text
    assert r.json()['ip_id']==ip['id']
    assert a.post('/api/collection/search',json={'keywords':'测试','ip_id':'missing'}).status_code==403


def test_platform_dedup_and_partial_failure_do_not_lose_other_platforms(env):
    app,c,team,ip=env
    def handler(req):
        if req.url.path.endswith('search_notes'):return httpx.Response(503)
        return transport(req)
    app.state.integration_transport=httpx.MockTransport(handler)
    key=start(c,ip_id=ip['id'],platforms=['xiaohongshu','douyin']);run_collection_job(app,key)
    run=c.get('/api/collection/runs').json()[0];assert run['status']=='partial' and '小红书' in run['error']
    assert len(c.get('/api/records/materials').json())==1
    key=start(c,ip_id=ip['id'],platforms=['douyin']);run_collection_job(app,key)
    assert len(c.get('/api/records/materials').json())==1 and len(c.get('/api/records/leads').json())==1


def test_invalid_platform_and_comment_range_rejected(env):
    app,c,team,ip=env
    for extra in [{'platforms':[]},{'platforms':['unknown']},{'comment_pages':0},{'max_videos':0}]:
        assert c.post('/api/collection/search',json={'keywords':'测试',**extra}).status_code==422


def test_xhs_search_and_comments_pagination(env):
    app,c,team,ip=env;seen=[]
    def handler(req):
        seen.append((req.url.path,dict(req.url.params)))
        if req.url.path.endswith('search_notes'):
            page=int(req.url.params['page'])
            if page==2:assert req.url.params['search_id']=='sid' and req.url.params['search_session_id']=='sess'
            data={'data':{'items':[{'id':'n'+str(page),'note_card':{'display_title':'笔记'+str(page),'user':{'user_id':'writer'}}}], 'has_more':page==1,'search_id':'sid','search_session_id':'sess'}}
        else:
            cursor=req.url.params['cursor']; key=req.url.params['note_id']
            if cursor:assert req.url.params['index']=='5' and req.url.params['pageArea']=='FOLDED'
            data={'data':{'comments':[{'id':key+('b' if cursor else 'a'),'content':'求资料'}],'has_more':not bool(cursor),'cursor':'next' if not cursor else 'end','index':5,'pageArea':'FOLDED'}}
        return httpx.Response(200,json={'code':200,'data':data})
    app.state.integration_transport=httpx.MockTransport(handler)
    key=start(c,platforms=['xiaohongshu'],pages=2,comment_pages=2);run_collection_job(app,key)
    assert c.get('/api/collection/runs').json()[0]['status']=='completed'
    assert len(c.get('/api/records/leads').json())==4 and len(seen)==6


def test_channels_cursor_and_failure_budget_bound(env):
    app,c,team,ip=env;seen=[]
    def handler(req):
        seen.append(req)
        if req.url.path.endswith('fetch_search_videos'):
            data={'items':[{'exportId':'export/'+str(i)} for i in range(5)],'continue_flag':True,'cursor':'next'}
            return httpx.Response(200,json={'code':200,'data':data})
        return httpx.Response(503)
    app.state.integration_transport=httpx.MockTransport(handler)
    key=start(c,platforms=['wechat_channels'],pages=5,max_videos=2);run_collection_job(app,key)
    run=c.get('/api/collection/runs').json()[0]
    assert len(seen)==3 and run['status']=='failed'
    assert run['calls']<=run['estimated_calls']


def test_comment_failure_does_not_block_next_work(env):
    app,c,team,ip=env
    def handler(req):
        if req.url.path.endswith('get_note_comments'):return httpx.Response(502)
        return transport(req)
    app.state.integration_transport=httpx.MockTransport(handler)
    key=start(c,platforms=['xiaohongshu','douyin']);run_collection_job(app,key)
    run=c.get('/api/collection/runs').json()[0];assert run['status']=='partial'
    mats=c.get('/api/records/materials').json()
    assert len(mats)==2 and len(c.get('/api/records/leads').json())==1
    assert next(m for m in mats if m['platform']=='小红书')['comments_status']=='failed'


def test_unassigned_feishu_sync_only_uses_submitter_pool(env):
    from backend.tests.test_collection import FeishuMock
    from backend.app.integrations.feishu import run_sync_job
    app,c,team,ip=env
    c.put('/api/settings',json={'feishu':{'enabled':True,'app_id':'fake','app_secret':'fake'}})
    c.post('/api/users',json={'username':'other','name':'其他成员','password':'Member-123456!','team_id':team['id'],'role':'member','ip_ids':[]})
    other=TestClient(app);other.post('/api/auth/login',json={'username':'other','password':'Member-123456!'})
    c.post('/api/records/materials',json={'title':'本人素材'})
    other.post('/api/records/materials',json={'title':'其他人素材'})
    mock=FeishuMock();app.state.integration_transport=httpx.MockTransport(mock)
    r=c.post('/api/integrations/feishu/sync',json={});assert r.status_code==200,r.text
    run_sync_job(app,r.json()['id'])
    assert len(mock.rows['tbl-works'])==1
    assert list(mock.rows['tbl-works'].values())[0]['标题']=='本人素材'


def test_baseline_excludes_missing_likes_and_cross_platform_authors(env):
    from backend.app.integrations.collection import upsert,refresh_baselines
    from backend.app.db import User
    from sqlalchemy import select
    app,c,team,ip=env
    with app.state.Session() as db:
        owner=db.scalar(select(User)).id
        a=upsert(db,'authors',None,owner,'writer',{'platform':'小红书','title':'同一博主'})
        for i,likes in enumerate([10,20,60,None]):
            upsert(db,'materials',None,owner,str(i),{'platform':'小红书','author_id':a.id,'likes':likes})
        db.commit()
    refresh_baselines(app,None)
    mats=c.get('/api/records/materials').json()
    assert all(m['author_sample_count']==3 and m['author_sample_median_likes']==20 for m in mats)


def test_revoked_unassigned_submitter_never_calls_provider(env):
    from backend.app.db import User
    from sqlalchemy import select
    app,c,team,ip=env
    c.post('/api/users',json={'username':'queued','name':'提交者','password':'Member-123456!','team_id':team['id'],'role':'member','ip_ids':[]})
    m=TestClient(app);m.post('/api/auth/login',json={'username':'queued','password':'Member-123456!'})
    key=start(m,platforms=['douyin'])
    with app.state.Session() as db:
        u=db.scalar(select(User).where(User.username=='queued'));u.active=False;db.commit()
    calls=[];app.state.integration_transport=httpx.MockTransport(lambda r:(calls.append(r) or transport(r)))
    run_collection_job(app,key)
    assert calls==[]
    assert next(r for r in c.get('/api/collection/runs').json() if r['id']==key)['status']=='failed'
