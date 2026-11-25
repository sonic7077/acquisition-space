import hashlib
import json
import pytest
from backend.tests.test_writer_profiles import env, ready
from backend.tests.test_writer_rewrite import draft, params
from backend.app.db import Record
from backend.app.main import create_app
from backend.app.worker import run_once


def defaults(c,ip=None):
    r=c.get('/api/default-writers',params={'ip_id':ip} if ip else {})
    assert r.status_code==200,r.text
    return r.json()


def knowledge(app,text='预算咨询包含需求梳理，不承诺收益。',status='verified'):
    with app.state.Session() as db:
        r=Record(kind='knowledge',payload={'title':'预算咨询','text':text,'hash':hashlib.sha256(text.encode()).hexdigest(),'status':status,'path':'咨询.md','category_id':'methods-service'})
        db.add(r);db.flush();key=r.id;db.commit();return key


def material(c,ip):
    return c.post('/api/records/materials',json={'ip_id':ip,'title':'预算咨询怎么安排','body':'这是作品描述，不是逐字稿','source':'https://www.douyin.com/video/123'}).json()


def test_eight_stable_defaults_and_live_knowledge(env):
    app,c,ip,users=env
    rows=defaults(c);assert len(rows)==8 and len({r['id'] for r in rows})==8
    assert sum(r['kind']=='content' for r in rows)==5
    assert all(r['text'] and r['base_version'] and r['name'] for r in rows)
    again=create_app(app.state.data_dir)
    from fastapi.testclient import TestClient
    second=TestClient(again);second.post('/api/auth/login',json={'username':'admin','password':'admin'})
    assert [x['id'] for x in defaults(second)]==[x['id'] for x in rows]
    before=defaults(c,ip['id'])[0]['knowledge']['fingerprint']
    key=knowledge(app);knowledge(app,'未核实的新价格999',status='pending')
    items=defaults(c,ip['id']);assert items[0]['knowledge']['count']==1 and items[0]['knowledge']['fingerprint']!=before
    w=next(x for x in items if x['skill']=='original-video-writer')
    r=c.post('/api/writing/context',json={'ip_id':ip['id'],'topic':'预算咨询','skill':w['skill'],'writer_id':w['id'],'writer_version':1})
    assert r.status_code==200,r.text
    ctx=r.json();assert ctx['writer_snapshot']['is_system'] and ctx['sources'][0]['id']==key and '未核实的新价格999' not in ctx['prompt']
    c.patch('/api/knowledge/'+key,json={'status':'archived'})
    assert defaults(c,ip['id'])[0]['knowledge']['count']==0


def test_material_call_defaults_transcript_permissions_and_rewrite(env):
    app,c,ip,users=env;m=material(c,ip['id']);rows=defaults(c,ip['id']);w=next(x for x in rows if x['skill']=='original-video-writer')
    l=c.post('/api/records/leads',json={'ip_id':ip['id'],'title':'预算问题','body':'预算怎么办？','material_id':m['id']}).json()
    payload={'ip_id':ip['id'],'writer_id':w['id'],'writer_version':1,'comment_ids':[l['id']],'instructions':'清楚回答预算问题'}
    url=f"/api/materials/{m['id']}/writing"
    r=c.post(url+'/context',json=payload);assert r.status_code==200,r.text
    ctx=r.json();assert ctx['material_id']==m['id'] and ctx['comments'][0]['id']==l['id']
    assert ctx['writer_snapshot']['is_system'] and ctx['structured_output']
    viral=next(x for x in rows if x['skill']=='viral-rewrite-writer')
    p={**payload,'writer_id':viral['id']}
    assert c.post(url+'/context',json=p).status_code==422
    preview = c.post(url+'/context',json={**p,'reference':'真实口播全文','transcript_confirmed':True})
    assert preview.status_code == 200
    assert preview.json()['transcript_confirmed'] is True
    assert '连续8字' in preview.json()['prompt']
    unrelated=c.post('/api/records/leads',json={'ip_id':ip['id'],'title':'其他','body':'多少钱','material_id':'else'}).json()
    assert c.post(url+'/context',json={**payload,'comment_ids':[unrelated['id']]}).status_code==422
    other=c.post('/api/teams',json={'name':'无权限团队'}).json();outside=c.post('/api/ips',json={'name':'外部IP','team_id':other['id']}).json()
    assert users['member'].post(url+'/context',json={**payload,'ip_id':outside['id']}).status_code==403
    d=draft(c,ip['id']);p=params({'id':w['id'],'version':1},d);p['skill']='original-video-writer'
    assert c.post(f"/api/drafts/{d['id']}/rewrite/context",json=p).status_code==200
    assert c.post(url,json={**payload,'request_id':'no-model'}).status_code==409


def test_shared_knowledge_matches_material_reference_and_draft_body(env):
    app, client, ip, users = env
    with app.state.Session.begin() as db:
        for doc_id, status, valid_until in (
            ('shared-current', 'verified', None),
            ('shared-expired', 'verified', '2000-01-01'),
            ('shared-pending', 'pending', None),
        ):
            db.add(Record(id=doc_id, kind='knowledge', payload={
                'title': '甲状腺结节除外承保', 'path': doc_id + '.md',
                'text': '甲状腺结节除外承保须核对合同。', 'hash': doc_id, 'status': status,
                'valid_until': valid_until,
            }))
    assert defaults(users['member'], ip['id'])[0]['knowledge']['count'] == 1
    work = client.post('/api/records/materials', json={
        'ip_id': ip['id'], 'title': '答疑', 'body': '甲状腺结节除外承保',
    }).json()
    original = client.post('/api/drafts', json={
        'ip_id': ip['id'], 'title': '答疑', 'body': '甲状腺结节除外承保',
    }).json()
    writer = ready(client, ip['id'])
    for url, payload in (
        (f"/api/materials/{work['id']}/writing/context", {
            'ip_id': ip['id'], 'writer_id': 'default:original-video-writer', 'writer_version': 1,
        }),
        (f"/api/drafts/{original['id']}/analysis/context", {
            'ip_id': ip['id'], 'writer_id': 'default:writer-self-evolve', 'writer_version': 1,
            'source_version': original['version'],
        }),
        (f"/api/drafts/{original['id']}/rewrite/context", params(writer, original)),
    ):
        response = users['member'].post(url, json=payload)
        assert response.status_code == 200, response.text
        assert {doc['id'] for doc in response.json()['sources']} == {'shared-current'}


def test_auxiliary_and_content_jobs_preserve_sources_and_knowledge_invalidation(env,monkeypatch):
    import httpx
    import backend.app.worker as worker
    app,c,ip,users=env;key=knowledge(app);m=material(c,ip['id']);rows=defaults(c,ip['id'])
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            raw=json.dumps({'title':'预算先明确','body':'先明确你的咨询需求，再确认服务范围。','fact_check':'价格待核实'}) if '最终返回一个JSON对象' in kwargs['json']['messages'][1]['content'] else '分析建议：先明确预算疑问；缺少真实改稿证据，不能形成长期语感规则。'
            return httpx.Response(200,json={'choices':[{'message':{'content':raw}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(worker.httpx,'Client',Client)
    c.put('/api/settings',json={'model':{'base_url':'https://example.org/v1','model':'controlled','api_key':'test','enabled':True}})
    for w in rows:
        p={'ip_id':ip['id'],'writer_id':w['id'],'writer_version':1,'instructions':'分析预算咨询','reference':'已核对的预算咨询口播全文','transcript_confirmed':True,'request_id':w['id']}
        url=f"/api/materials/{m['id']}/writing"
        r=c.post(url,json=p);assert r.status_code==200,r.text
        assert c.post(url,json=p).json()['id']==r.json()['id']
        assert c.post(url,json={**p,'instructions':'不同输入'}).status_code==409
        assert worker.run_once(app)
        j=next(x for x in c.get('/api/jobs').json() if x['id']==r.json()['id']);assert j['status']=='completed',j
        out=next(x for x in c.get('/api/drafts' if w['kind']=='content' else '/api/writing/analyses').json() if x['id']==j.get('draft_id',j.get('analysis_id')))
        assert out['material_id']==m['id'] and out['writer_snapshot']['id']==w['id'] and out['knowledge_snapshot']['count']==1
        if w['kind']=='content':
            assert out['reference']==p['reference'] and out['transcript_confirmed'] is True
    assert len(c.get('/api/drafts').json())==5 and len(c.get('/api/writing/analyses').json())==3
    w=next(x for x in rows if x['kind']=='content')
    j=c.post(url,json={**p,'writer_id':w['id'],'request_id':'stale-doc'}).json()
    c.patch('/api/knowledge/'+key,json={'status':'archived'})
    assert worker.run_once(app)
    job=next(x for x in c.get('/api/jobs').json() if x['id']==j['id']);assert job['status']=='failed'


def test_ip_enhancement_review_evidence_versions_rollback_and_isolation(env):
    app,c,ip,users=env;key=knowledge(app);w=next(x for x in defaults(c,ip['id']) if x['skill']=='original-video-writer')
    endpoint='/api/default-writers/original-video-writer'
    data={'ip_id':ip['id'],'version':1,'title':'先明确服务范围','before':'泛泛介绍','after':'先说明咨询包含需求梳理，不承诺收益。','source':'business','knowledge_ids':[key]}
    r=users['member'].post(endpoint+'/candidates',json=data);assert r.status_code==200,r.text
    ident=r.json()['id'];review={'ip_id':ip['id'],'version':1}
    assert users['member'].post(endpoint+f'/candidates/{ident}/adopt',json=review).status_code==403
    r=c.post(endpoint+f'/candidates/{ident}/adopt',json=review);assert r.status_code==200,r.text
    v=next(x for x in defaults(c,ip['id']) if x['skill']==w['skill']);assert v['enhancement_version']==2 and v['rules'][0]['after']==data['after']
    assert c.post(endpoint+'/rollback',json={**review,'target_version':1}).status_code==409
    other=c.post('/api/ips',json={'name':'别的业务IP','team_id':ip['team_id']}).json()
    assert next(x for x in defaults(c,other['id']) if x['skill']==w['skill'])['rules']==[]
    assert c.post(endpoint+'/candidates',json={**data,'version':2,'source':'learned','knowledge_ids':[]}).status_code==409
    assert c.post(endpoint+'/candidates',json={**data,'version':2,'knowledge_ids':[]}).status_code==422
    c.patch('/api/knowledge/'+key,json={'status':'archived'})
    v=next(x for x in defaults(c,ip['id']) if x['skill']==w['skill']);assert v['rules']==[] and v['inactive_rule_count']==1
    assert c.post(endpoint+'/rollback',json={'ip_id':ip['id'],'version':2,'target_version':1}).status_code==200
    assert c.post(endpoint+'/rollback',json={'ip_id':ip['id'],'version':3,'target_version':2}).status_code==200
    assert next(x for x in defaults(c,ip['id']) if x['skill']==w['skill'])['rules']==[]


def test_default_enhancement_changes_cancel_queued_work(env):
    app,c,ip,users=env;m=material(c,ip['id']);knowledge(app);w=next(x for x in defaults(c,ip['id']) if x['skill']=='original-video-writer')
    c.put('/api/settings',json={'model':{'base_url':'http://127.0.0.1:1/v1','model':'controlled','api_key':'test','enabled':True}})
    j=c.post(f"/api/materials/{m['id']}/writing",json={'ip_id':ip['id'],'writer_id':w['id'],'writer_version':1,'request_id':'rules-change'}).json()
    endpoint='/api/default-writers/original-video-writer'
    candidate=c.post(endpoint+'/candidates',json={'ip_id':ip['id'],'version':1,'title':'直说','after':'开头直接给判断','source':'manual'}).json()
    c.post(endpoint+f"/candidates/{candidate['id']}/adopt",json={'ip_id':ip['id'],'version':1})
    assert run_once(app)
    job=next(x for x in c.get('/api/jobs').json() if x['id']==j['id']);assert job['status']=='failed' and '规则' in job['error']


def test_system_auxiliary_cannot_be_published_and_personal_material_writer_works(env):
    app,c,ip,users=env;m=material(c,ip['id']);w=ready(c,ip['id'])
    payload={'ip_id':ip['id'],'writer_id':w['id'],'writer_version':w['version'],'reference':'已核对完整逐字稿','transcript_confirmed':True}
    r=c.post(f"/api/materials/{m['id']}/writing/context",json=payload);assert r.status_code==200,r.text
    assert r.json()['writer_snapshot']['id']==w['id']
    d=draft(c,ip['id']);aux=defaults(c)[0]
    p={**params({'id':aux['id'],'version':1},d),'skill':aux['skill']}
    assert c.post(f"/api/drafts/{d['id']}/rewrite/context",json=p).status_code==422


def test_learned_rules_require_independent_edits_and_analysis_ip(env):
    app,c,ip,users=env;endpoint='/api/default-writers/original-video-writer';ids=[]
    for i in range(3):
        d=draft(c,ip['id']);c.patch('/api/drafts/'+d['id'],json={'version':1,'body':f'先核对责任条款，再考虑预算，第{i}篇。'});ids.append(d['id'])
    r=c.post(endpoint+'/candidates',json={'ip_id':ip['id'],'version':1,'title':'先说责任','after':'开头先交代责任边界','source':'learned','evidence_ids':ids})
    assert r.status_code==200,r.text
    assert c.post(endpoint+f"/candidates/{r.json()['id']}/adopt",json={'ip_id':ip['id'],'version':1}).status_code==200
    with app.state.Session() as db:
        a=Record(kind='writer_analysis',ip_id='another-ip',payload={'body':'其他IP分析'});db.add(a);db.flush();aid=a.id;db.commit()
    assert c.post(endpoint+'/candidates',json={'ip_id':ip['id'],'version':2,'title':'越权分析','after':'建议','source':'manual','analysis_id':aid}).status_code in (403,422)


def test_private_material_and_comment_snapshot_not_exposed_to_ip_members(env):
    from backend.app.demand_analysis import public_record
    from backend.app.db import User
    from sqlalchemy import select
    app,c,ip,users=env
    with app.state.Session() as db:
        admin=db.scalar(select(User).where(User.username=='admin'));member=db.scalar(select(User).where(User.username=='member'))
        m=Record(kind='materials',owner_id=admin.id,payload={'title':'私有素材','body':'私有作品内容'});db.add(m);db.flush()
        l=Record(kind='leads',owner_id=admin.id,payload={'body':'私有评论原话','material_id':m.id});db.add(l);db.flush()
        d=Record(kind='drafts',ip_id=ip['id'],payload={'title':'共享稿件','body':'已经生成的正文','material_id':m.id,'material_snapshot':m.payload,'comments':[{'id':l.id,'body':l.payload['body']}]});db.add(d);db.flush()
        out=public_record(db,member,d)
        assert '私有评论原话' not in json.dumps(out,ensure_ascii=False) and '私有作品内容' not in json.dumps(out,ensure_ascii=False)
        assert out['source_evidence_restricted']


def test_default_preview_keeps_material_unassigned_and_detects_stale_enhancement(env):
    from backend.app.db import User
    from sqlalchemy import select
    app,c,ip,users=env
    with app.state.Session() as db:
        admin=db.scalar(select(User).where(User.username=='admin'))
        m=Record(kind='materials',owner_id=admin.id,payload={'title':'未归属素材','body':'素材说明'});db.add(m);db.flush();key=m.id;db.commit()
    data={'ip_id':ip['id'],'writer_id':'default:original-video-writer','writer_version':1,'enhancement_version':99}
    r=c.post(f'/api/materials/{key}/writing/context',json=data);assert r.status_code==409
    r=c.post(f'/api/materials/{key}/writing/context',json={**data,'enhancement_version':1});assert r.status_code==200,r.text
    with app.state.Session() as db:assert db.get(Record,key).ip_id is None


def test_review_auxiliary_analysis_is_not_rewrite(env):
    app,c,ip,users=env;d=draft(c,ip['id'])
    body={'ip_id':ip['id'],'writer_id':'default:writer-self-evolve','writer_version':1,'source_version':d['version']}
    r=c.post(f"/api/drafts/{d['id']}/analysis/context",json=body)
    assert r.status_code==200,r.text
    assert d['body'] in r.json()['prompt'] and r.json()['parent_draft_id']==d['id']
    assert not r.json().get('structured_output')
    assert c.post(f"/api/drafts/{d['id']}/analysis/context",json={**body,'source_version':99}).status_code==409
    assert c.post(f"/api/drafts/{d['id']}/analysis/context",json={**body,'writer_id':'default:original-video-writer'}).status_code==422


def test_material_adoption_occurs_only_after_successful_content_generation(env, monkeypatch):
    import httpx
    import backend.app.worker as worker
    app,c,ip,_=env
    m=material(c,ip['id'])
    c.put('/api/settings',json={'model':{'base_url':'https://example.org/v1','model':'controlled','api_key':'test','enabled':True}})
    response=['需求分析，仅用于研究']
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):return httpx.Response(200,json={'choices':[{'message':{'content':response[0]}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(worker.httpx,'Client',Client)
    endpoint=f"/api/materials/{m['id']}/writing"
    def generate(skill,request_id):
        j=c.post(endpoint,json={'ip_id':ip['id'],'writer_id':'default:'+skill,'writer_version':1,'request_id':request_id})
        assert j.status_code==200,j.text
        assert worker.run_once(app)
        return next(x for x in c.get('/api/jobs').json() if x['id']==j.json()['id'])
    assert generate('customer-needs-miner','analysis')['status']=='completed'
    assert next(x for x in c.get('/api/records/materials').json() if x['id']==m['id'])['status']=='new'
    assert generate('original-video-writer','bad-content')['status']=='failed'
    assert next(x for x in c.get('/api/records/materials').json() if x['id']==m['id'])['status']=='new'
    response[0]=json.dumps({'title':'先问清范围','body':'先明确需求，再核对服务范围。','fact_check':'具体事实待核实'})
    assert generate('original-video-writer','content')['status']=='completed'
    saved=next(x for x in c.get('/api/records/materials').json() if x['id']==m['id'])
    assert saved['status']=='adopted' and not saved['discovery']['candidate']
