import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from backend.tests.test_writer_profiles import env,ready,answers
from backend.app.worker import run_once
from backend.app.db import Record,User
from sqlalchemy import select


def draft(c,ip):
    r=c.post('/api/drafts',json={'ip_id':ip,'title':'选保障先看责任','body':'先核对保障责任，再看免赔和免责。','skill':'original-video-writer','fact_status':'none'})
    assert r.status_code==200,r.text
    return r.json()


def params(w,d):
    return {'writer_id':w['id'],'writer_version':w['version'],'version':d['version'],'skill':'viral-rewrite-writer','reference':'买保险先看这三件事。第一是免责条款，第二是健康告知，第三是家庭预算。不要跟风下单。','transcript_confirmed':True,'comment_ids':[],'instructions':'用具体场景表达'}


def add_verified_knowledge(c, title, text):
    preview = c.post('/api/knowledge/preview', data={'category_id': 'product-critical-illness'},
                     files={'files': (title + '.md', text.encode(), 'text/markdown')})
    assert preview.status_code == 200, preview.text
    confirmed = c.post('/api/knowledge/confirm', json={'batch_id': preview.json()['id']})
    assert confirmed.status_code == 200, confirmed.text
    doc = confirmed.json()['items'][0]
    verified = c.patch('/api/knowledge/' + doc['id'], json={'status': 'verified'})
    assert verified.status_code == 200, verified.text
    return verified.json()


def set_writer_knowledge(app, writer, linked, matched=()):
    with app.state.Session.begin() as db:
        row = db.get(Record, writer['id'])
        row.payload = {**row.payload, 'knowledge_ids': [doc['id'] for doc in linked],
                       'knowledge_sources': [{k: doc[k] for k in ('id', 'title', 'hash', 'category_path')}
                                             for doc in (*linked, *matched)]}


def test_linked_knowledge_is_prioritized_and_demand_brings_live_matches(env):
    app, c, ip, _ = env
    w = ready(c, ip['id'])
    linked = add_verified_knowledge(c, '服务手册', '核验资料正文甲')
    current = add_verified_knowledge(c, '门诊慢特病', '申请门诊慢特病的核验说明')
    unrelated = add_verified_knowledge(c, '异地门店', '旧配置匹配的门店专用说明')
    set_writer_knowledge(app, w, [linked], [unrelated])
    need = c.post('/api/records/needs', json={'ip_id': ip['id'], 'title': '门诊慢特病'}).json()
    d = draft(c, ip['id'])
    m = c.post('/api/records/materials', json={'ip_id': ip['id'], 'title': '选保障先看责任'}).json()
    for path, args in (
        (f"/api/drafts/{d['id']}/rewrite/context", {}),
        (f"/api/materials/{m['id']}/writing/context", {'ip_id': ip['id']}),
        ('/api/writing/context', {'ip_id': ip['id'], 'topic': '选保障先看责任'}),
    ):
        response = c.post(path, json={**params(w, d), **args, 'need_id': need['id']})
        assert response.status_code == 200, response.text
        ctx = response.json()
        assert [doc['id'] for doc in ctx['sources']] == [linked['id'], current['id']]
        assert [doc['id'] for doc in ctx['knowledge_snapshot']['documents']] == [linked['id'], current['id']]
        assert linked['text'] in ctx['prompt'] and current['text'] in ctx['prompt']
        assert unrelated['title'] not in ctx['prompt']


def test_edited_or_expired_writer_knowledge_is_omitted_until_verified(env):
    app, c, ip, _ = env
    w = ready(c, ip['id'])
    edited = add_verified_knowledge(c, '旧知识独有标题', '旧正文专有标记')
    expired = add_verified_knowledge(c, '过期知识独有标题', '过期正文专有标记')
    set_writer_knowledge(app, w, [edited, expired])
    assert c.patch('/api/knowledge/' + edited['id'], json={'text': '核验后的最新正文'}).json()['status'] == 'pending'
    assert c.patch('/api/knowledge/' + expired['id'], json={'valid_until': '2000-01-01'}).status_code == 200
    d = draft(c, ip['id'])
    url = f"/api/drafts/{d['id']}/rewrite/context"
    response = c.post(url, json=params(w, d))
    assert response.status_code == 200, response.text
    ctx = response.json()
    assert not ctx['sources'] and ctx['knowledge_snapshot']['count'] == 0
    for doc in (edited, expired):
        assert doc['title'] not in ctx['prompt'] and doc['text'] not in ctx['prompt']
    assert '核验后的最新正文' not in ctx['prompt']
    assert c.patch('/api/knowledge/' + edited['id'], json={'status': 'verified'}).status_code == 200
    refreshed = c.post(url, json=params(w, d)).json()
    assert refreshed['sources'][0]['id'] == edited['id']
    assert refreshed['sources'][0]['hash'] != edited['hash']
    assert '核验后的最新正文' in refreshed['prompt']


def test_context_requires_real_transcript_and_active_same_ip_writer(env):
    app,c,ip,users=env;w=ready(c,ip['id']);d=draft(c,ip['id']);p=params(w,d);url=f"/api/drafts/{d['id']}/rewrite/context"
    r=c.post(url,json={**p,'reference':''});assert r.status_code==422
    assert c.post(url,json={**p,'transcript_confirmed':False}).status_code==422
    r=c.post(url,json=p);assert r.status_code==200,r.text
    ctx=r.json();assert w['name'] in ctx['prompt'] and p['reference'] in ctx['prompt']
    assert ctx['writer_snapshot']['version']==w['version'] and ctx['word_limit']==1500
    assert ctx['parent_draft_id']==d['id']
    assert c.post(url,json={**p,'skill':'writer-self-evolve'}).status_code==422
    team=c.post('/api/teams',json={'name':'外组'}).json();outside=c.post('/api/ips',json={'name':'外IP','team_id':team['id']}).json();ow=ready(c,outside['id'])
    assert c.post(url,json={**p,'writer_id':ow['id'],'writer_version':ow['version']}).status_code==422
    assert c.post(url,json={**p,'version':9}).status_code==409
    assert c.post(url,json={**p,'writer_version':1}).status_code==409
    newer=c.patch(f"/api/writers/{w['id']}",json={'version':w['version'],'name':'新写手名字'}).json()
    assert c.post(url,json={**p,'writer_version':newer['version']}).status_code==409


def test_selected_comments_permissions_and_different_writer_prompt(env):
    app,c,ip,users=env;w=ready(c,ip['id']);d=draft(c,ip['id']);p=params(w,d)
    mat=c.post('/api/records/materials',json={'ip_id':ip['id'],'title':'原作品','body':'作品说明'}).json()
    lead=c.post('/api/records/leads',json={'ip_id':ip['id'],'title':'预算问题','body':'预算有限怎么办','material_id':mat['id']}).json()
    with app.state.Session() as db:
        row=db.get(Record,d['id']);row.payload={**row.payload,'material_id':mat['id']};db.commit()
    url=f"/api/drafts/{d['id']}/rewrite/context"
    ctx=c.post(url,json={**p,'comment_ids':[lead['id']]}).json()
    assert ctx['comments'][0]['id']==lead['id'] and '预算有限怎么办' in ctx['prompt']
    assert c.post(url,json={**p,'comment_ids':[mat['id']]}).status_code==422
    unrelated=c.post('/api/records/leads',json={'ip_id':ip['id'],'title':'无关','body':'另一作品','material_id':'other'}).json()
    assert c.post(url,json={**p,'comment_ids':[unrelated['id']]}).status_code==422
    w2=ready(c,ip['id'],'温和顾问');ctx2=c.post(url,json={**p,'writer_id':w2['id'],'writer_version':w2['version']}).json()
    assert ctx2['prompt']!=ctx['prompt'] and '温和顾问' in ctx2['prompt']


def test_rewrite_uses_selected_format_and_keeps_personal_configuration(env):
    app,c,ip,_=env
    w=ready(c,ip['id'])
    d=draft(c,ip['id'])
    url=f"/api/drafts/{d['id']}/rewrite/context"
    with app.state.Session.begin() as db:
        profile=db.get(Record,w['id'])
        text=profile.payload['skill_text'].replace('## 内容文体参考：复刻口播\n', '## 内容文体参考：复刻口播\n历史文体专用指令\n')
        profile.payload={**profile.payload,'skill_text':text}
    payload={**params(w,d),'skill':'gzh-writer','reference':'未经确认的参考片段','transcript_confirmed':False}
    response=c.post(url,json=payload)
    assert response.status_code==200,response.text
    context=response.json()
    assert context['skill']=='gzh-writer' and context['word_limit']==2500
    assert '称呼你' in context['prompt'] and '历史文体专用指令' not in context['prompt']
    assert '历史文体专用指令' in context['writer_snapshot']['skill_text']


def test_unconfirmed_reference_label_in_material_and_rewrite_context(env):
    _,c,ip,_=env
    d=draft(c,ip['id'])
    m=c.post('/api/records/materials',json={'ip_id':ip['id'],'title':'报销条件','body':'作品说明'}).json()
    payload={'writer_id':'default:original-video-writer','writer_version':1,
             'reference':'未经确认的参考片段','transcript_confirmed':False}
    for path,args in (
        (f"/api/drafts/{d['id']}/rewrite/context",{**payload,'version':d['version'],'skill':'original-video-writer'}),
        (f"/api/materials/{m['id']}/writing/context",{**payload,'ip_id':ip['id']}),
    ):
        response=c.post(path,json=args)
        assert response.status_code==200,response.text
        prompt=response.json()['prompt']
        assert '用户提供的参考文本（未确认逐字稿）' in prompt
        assert '未经确认的参考片段' in prompt


def test_queue_candidate_preserves_original_and_parses_fields(env):
    app, c, ip, _ = env
    linked = add_verified_knowledge(c, '服务手册', '本次模型提示包含的核验材料。')
    writer = c.post('/api/writers/generate', json={'ip_id': ip['id'], 'name': '专属改编写手', 'industry': '保险',
                    'skill': 'viral-rewrite-writer', 'answers': answers(), 'knowledge_ids': [linked['id']]})
    assert writer.status_code == 200, writer.text
    w = writer.json()
    d = draft(c, ip['id'])
    p = params(w, d)
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            content=json.dumps({'title':'条款怎么核对','cover':'先看责任','body':'先列出你关心的保障，再逐条核对免责。','publish_copy':'问清责任再做选择','tags':['保障'],'fact_check':'具体产品条款待核实'},ensure_ascii=False)
            raw=json.dumps({'choices':[{'message':{'content':content}}]},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        c.put('/api/settings',json={'model':{'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'controlled','api_key':'local-test','enabled':True}})
        url=f"/api/drafts/{d['id']}/rewrite"
        r=c.post(url,json={**p,'request_id':'same-click'});assert r.status_code==200,r.text
        job=r.json();assert c.post(url,json={**p,'request_id':'same-click'}).json()['id']==job['id']
        assert run_once(app)
        jobs=c.get('/api/jobs').json();result=next(j for j in jobs if j['id']==job['id']);assert result['status']=='completed',result
        candidate=c.get('/api/drafts/'+result['draft_id']).json()
        assert candidate['body']=='先列出你关心的保障，再逐条核对免责。'
        assert candidate['title']=='条款怎么核对' and candidate['cover']=='先看责任'
        assert candidate['writer_snapshot']['name']==w['name'] and candidate['word_limit']==1500
        assert candidate['sources'][0]['id'] == linked['id']
        assert candidate['knowledge_snapshot']['documents'][0]['hash'] == linked['hash']
        assert candidate['reference']==p['reference'] and candidate['parent_draft_id']==d['id']
        assert candidate['fact_status']=='pending' and candidate['approved_version'] is None
        assert c.get('/api/drafts/'+d['id']).json()['body']==d['body']
        assert p['reference'] in seen[0]['messages'][1]['content']
        assert linked['text'] in seen[0]['messages'][1]['content']
        assert any(x['name']=='正文不超过1500字' for x in candidate['checks'])
    finally:server.shutdown();server.server_close();thread.join()


def test_no_model_and_revoked_permissions(env):
    app,c,ip,users=env;w=ready(c,ip['id']);d=draft(c,ip['id']);p=params(w,d)
    assert c.post(f"/api/drafts/{d['id']}/rewrite",json={**p,'request_id':'empty-model'}).status_code==409
    c.put('/api/settings',json={'model':{'base_url':'http://127.0.0.1:1/v1','model':'controlled','api_key':'local-test','enabled':True}})
    r=users['member'].post(f"/api/drafts/{d['id']}/rewrite",json={**p,'request_id':'revoked'});assert r.status_code==200,r.text
    member=next(u for u in c.get('/api/users').json() if u['role']=='member')
    c.patch('/api/users/'+member['id'],json={'active':False})
    assert run_once(app)
    result=next(j for j in c.get('/api/jobs').json() if j['id']==r.json()['id'])
    assert result['status']=='failed' and '权限' in result['error']


def test_bad_output_preserved_and_auxiliary_results_are_not_drafts(env,monkeypatch):
    import httpx
    import backend.app.worker as worker
    app,c,ip,users=env;w=ready(c,ip['id']);d=draft(c,ip['id'])
    c.put('/api/settings',json={'model':{'base_url':'https://example.org/v1','model':'controlled','api_key':'local-test','enabled':True}})
    content=['模型没有按格式返回的完整原文']
    class ControlledClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):return httpx.Response(200,json={'choices':[{'message':{'content':content[0]}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(worker.httpx,'Client',ControlledClient)
    j=c.post(f"/api/drafts/{d['id']}/rewrite",json={**params(w,d),'request_id':'bad-format'}).json()
    assert worker.run_once(app)
    result=next(r for r in c.get('/api/jobs').json() if r['id']==j['id'])
    assert result['status']=='failed' and result['raw_output']==content[0]
    assert len(c.get('/api/drafts').json())==1
    content[0]='需求分析：预算是首要顾虑，具体成交情况待核实。'
    job=c.post('/api/writing/generate',json={'ip_id':ip['id'],'topic':'分析预算问题','skill':'customer-needs-miner'}).json()
    assert worker.run_once(app)
    assert len(c.get('/api/drafts').json())==1
    result=next(r for r in c.get('/api/jobs').json() if r['id']==job['id'])
    assert result['status']=='completed' and result.get('analysis_id')
    assert c.get('/api/writing/analyses').json()[0]['body']==content[0]


def test_learned_rules_cannot_count_same_root_candidates_and_auxiliary_approval(env):
    app,c,ip,users=env;w=ready(c,ip['id']);root=draft(c,ip['id'])
    rule=c.post(f"/api/writers/{w['id']}/rules",json={'kind':'delete','title':'开头不寒暄','before':'大家好','after':'','source':'learned'}).json()
    ids=[]
    with app.state.Session() as db:
        for i in range(3):
            row=Record(kind='drafts',ip_id=ip['id'],payload={'title':'候选','body':'修改后的正文','history':[{'body':'修改前的正文'}],'root_draft_id':root['id']})
            db.add(row);db.flush();ids.append(row.id)
        db.commit()
    r=c.post(f"/api/writers/{w['id']}/rules/{rule['id']}/adopt",json={'evidence_ids':ids})
    assert r.status_code==409
    auxiliary=c.post('/api/drafts',json={'ip_id':ip['id'],'skill':'customer-needs-miner','title':'需求分析','body':'客户关心预算。','fact_status':'none'}).json()
    assert c.post(f"/api/drafts/{auxiliary['id']}/approve",json={'version':1}).status_code==409
