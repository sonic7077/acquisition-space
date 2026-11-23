import pytest
from backend.tests.test_writer_profiles import env, ready
from backend.tests.test_writer_rewrite import draft, params
from backend.app.db import Record
from backend.app.worker import run_once


@pytest.mark.parametrize('body,typ,intent', [
    ('这个咨询多少钱？','追问','问价格'),
    ('能发一份资料给我吗','追问','要资料'),
    ('我预算有限，怎么办？','需求','问方案'),
    ('不问价格，只想吐槽，都是骗人','反对','无关'),
    ('价格就是智商税','反对','无关'),
    ('太有共鸣了，我也一样','共鸣','无关'),
    ('How much does it cost?','追问','问价格'),
    ('广告联系VX123','待核实','无关'),
    ('hi','待核实','待核实'),
])
def test_comment_analysis(body,typ,intent):
    from backend.app.demand_analysis import analyze_comment
    result=analyze_comment(body)
    assert (result['content_type'],result['intent'])==(typ,intent)
    assert result['analysis_basis'] and result['analysis_version']


def lead(c,ip,body,**extra):
    r=c.post('/api/records/leads',json={'ip_id':ip,'title':'评论用户','body':body,**extra})
    assert r.status_code==200,r.text
    return r.json()


def topic(c,ip):
    r=c.post('/api/demand-topics',json={'ip_id':ip,'title':'家庭预算','keywords':['预算','咨询']})
    assert r.status_code==200,r.text
    return r.json()


def rebuild(c,ip,**extra):
    r=c.post('/api/demands/rebuild',json={'ip_id':ip,**extra})
    assert r.status_code==200,r.text
    return r.json()


def test_aggregation_rebuild_current_content_and_manual_override(env):
    app,c,ip,users=env;t=topic(c,ip['id'])
    a=lead(c,ip['id'],'预算多少钱？',platform='抖音',platform_comment_id='a',commenter_id='same',likes=10,contactable=False)
    lead(c,ip['id'],'预算怎么安排？',platform='抖音',platform_comment_id='b',commenter_id='same',contactable=False)
    lead(c,ip['id'],'预算价格都是骗人',contactable=False)
    lead(c,ip['id'],'预算我也一样',contactable=False)
    lead(c,ip['id'],'有没有清单发我',contactable=False)
    rebuild(c,ip['id']);needs=c.get('/api/records/needs').json();assert len(needs)==1
    n=needs[0];assert n['count']==2 and n['priority']==6 and n['unique_commenters']==1
    assert n['research_count']==2 and len(n['quotes'])==2
    assert len(c.get('/api/demands/unclassified',params={'ip_id':ip['id']}).json())==1
    assert rebuild(c,ip['id'])['created']==0
    c.patch('/api/records/needs/'+n['id'],json={'sel_topic':'先问预算','doc_name':'已有预算表','product':'预算咨询','note':'人工备注'})
    c.patch('/api/records/leads/'+a['id'],json={'intent':'无关','content_type':'反对'})
    c.post('/api/comments/analyze',json={'ip_id':ip['id']})
    a=next(x for x in c.get('/api/records/leads').json() if x['id']==a['id'])
    assert a['intent']=='无关' and a['manual_labels']==['content_type','intent']
    assert a['contactable'] is False
    rebuild(c,ip['id']);n=c.get('/api/records/needs').json()[0]
    assert n['count']==1 and n['sel_topic']=='先问预算' and n['note']=='人工备注'
    assert c.patch('/api/records/leads/'+a['id'],json={'intent':'成交率99%'}).status_code==422
    for x in c.get('/api/records/leads').json():c.patch('/api/records/leads/'+x['id'],json={'intent':'无关'})
    rebuild(c,ip['id']);assert c.get('/api/records/needs').json()[0]['status']=='expired'


def test_unassigned_evidence_acl_and_explicit_assignment(env):
    app,c,ip,users=env;t=topic(c,ip['id'])
    # Unassigned research belongs to the admin; a member with IP access cannot see it.
    with app.state.Session() as db:
        from backend.app.db import User
        from sqlalchemy import select
        admin=db.scalar(select(User).where(User.username=='admin'))
        row=Record(kind='leads',owner_id=admin.id,payload={'title':'私有评论','body':'咨询多少钱？','contactable':False})
        db.add(row);db.flush();key=row.id;db.commit()
    assert rebuild(c,ip['id'])['created']==0
    rebuild(c,ip['id'],include_unassigned_ids=[key]);n=c.get('/api/records/needs').json()[0];assert n['count']==1
    member=users['member'];visible=member.get('/api/records/needs').json()
    assert not any(x.get('count') or x.get('quotes') for x in visible)
    assert member.post('/api/demands/rebuild',json={'ip_id':ip['id'],'include_unassigned_ids':[key]}).status_code==403
    assert member.post('/api/drafts',json={'ip_id':ip['id'],'title':'预算','need_id':n['id']}).status_code==409
    outside=c.post('/api/ips',json={'name':'另一个IP','team_id':ip['team_id']}).json()
    assert c.post('/api/drafts',json={'ip_id':outside['id'],'title':'预算','need_id':n['id']}).status_code==422


def test_classify_unmatched_and_demand_snapshot_in_writer(env):
    app,c,ip,users=env;t=topic(c,ip['id']);l=lead(c,ip['id'],'有清单可以发我吗？',contactable=False)
    r=c.post('/api/demands/assign',json={'ip_id':ip['id'],'topic_id':t['id'],'comment_ids':[l['id']]});assert r.status_code==200,r.text
    rebuild(c,ip['id']);n=c.get('/api/records/needs').json()[0]
    d=c.post('/api/drafts',json={'ip_id':ip['id'],'title':'预算安排','need_id':n['id']}).json()
    assert d['need_id']==n['id'] and d['demand_snapshot']['count']==1
    assert c.get('/api/records/needs').json()[0]['status']=='writing'
    w=ready(c,ip['id']);assert '追问正面回答' in w['skill_text'] and '反对' in w['skill_text']
    p={**params(w,d),'need_id':n['id']}
    r=c.post(f"/api/drafts/{d['id']}/rewrite/context",json=p);assert r.status_code==200,r.text
    ctx=r.json();assert ctx['demand_snapshot']['quotes'][0]['body']==l['body']
    assert l['body'] in ctx['prompt'] and ctx['comments']==[]
    general=c.post('/api/writing/context',json={'ip_id':ip['id'],'topic':'预算','need_id':n['id']});assert general.status_code==200
    assert general.json()['demand_snapshot']['id']==n['id']
    # Removing effective intent invalidates pending work, before any network call.
    c.put('/api/settings',json={'model':{'base_url':'http://127.0.0.1:1/v1','model':'controlled','api_key':'test-key','enabled':True}})
    j=c.post(f"/api/drafts/{d['id']}/rewrite",json={**p,'request_id':'demand-stale'}).json()
    c.patch('/api/records/leads/'+l['id'],json={'intent':'无关'})
    assert run_once(app)
    result=next(x for x in c.get('/api/jobs').json() if x['id']==j['id'])
    assert result['status']=='failed' and '需求' in result['error']


def test_recollection_retains_manual_labels_and_follows_updated_body(env):
    from backend.app.integrations.collection import upsert
    from backend.app.demand_analysis import analyze_comment
    app,c,ip,users=env
    with app.state.Session() as db:
        a=upsert(db,'leads',ip['id'],'admin-owner','c1',{'platform':'抖音','title':'甲','body':'价格多少？',**analyze_comment('价格多少？'),'contactable':False})
        aid=a.id
        b=upsert(db,'leads',ip['id'],'admin-owner','c2',{'platform':'抖音','title':'乙','body':'旧人工意向','intent':'有意向','contactable':False})
        bid=b.id;db.commit()
    c.patch('/api/records/leads/'+aid,json={'intent':'无关','content_type':'反对','reply':'仅记录回复'})
    with app.state.Session() as db:
        upsert(db,'leads',ip['id'],'admin-owner','c1',{'platform':'抖音','title':'甲','body':'预算怎么办？',**analyze_comment('预算怎么办？'),'contactable':False})
        upsert(db,'leads',ip['id'],'admin-owner','c2',{'platform':'抖音','title':'乙','body':'价格多少？',**analyze_comment('价格多少？'),'contactable':False})
        db.commit()
    rows={r['id']:r for r in c.get('/api/records/leads').json()}
    assert rows[aid]['intent']=='无关' and rows[aid]['content_type']=='反对' and rows[aid]['reply']=='仅记录回复'
    assert rows[bid]['intent']=='有意向'


def test_rules_edit_dedupe_and_cross_ip_assignment(env):
    app,c,ip,users=env;t=topic(c,ip['id'])
    lead(c,ip['id'],'预算多少钱？',platform='抖音',platform_comment_id='same')
    lead(c,ip['id'],'预算多少钱？',platform='抖音',platform_comment_id='same')
    rebuild(c,ip['id']);n=c.get('/api/records/needs').json()[0];assert n['count']==1
    assert c.patch('/api/demand-topics/'+t['id'],json={'ip_id':ip['id'],'title':'新主题','keywords':['保障']}).status_code==200
    assert c.get('/api/records/needs').json()[0]['count']==0
    other=c.post('/api/ips',json={'name':'另一IP','team_id':ip['team_id']}).json()
    outside=lead(c,other['id'],'保障多少钱？')
    assert c.post('/api/demands/assign',json={'ip_id':ip['id'],'topic_id':t['id'],'comment_ids':[outside['id']]}).status_code==422
    manual=c.post('/api/records/needs',json={'ip_id':ip['id'],'title':'手工需求','body':'客户明确需要咨询'}).json()
    d=c.post('/api/drafts',json={'ip_id':ip['id'],'title':'手工需求选题','need_id':manual['id']})
    assert d.status_code==200 and d.json()['demand_snapshot']['body']=='客户明确需要咨询'


def test_draft_snapshot_does_not_expand_private_evidence_permissions(env):
    app,c,ip,users=env;t=topic(c,ip['id'])
    from backend.app.db import User
    from sqlalchemy import select
    with app.state.Session() as db:
        owner=db.scalar(select(User).where(User.username=='admin'))
        r=Record(kind='leads',owner_id=owner.id,payload={'body':'咨询多少钱？私有原话','title':'私有'});db.add(r);db.flush();key=r.id;db.commit()
    rebuild(c,ip['id'],include_unassigned_ids=[key]);need=c.get('/api/records/needs').json()[0]
    d=c.post('/api/drafts',json={'ip_id':ip['id'],'title':'咨询选题','body':'','need_id':need['id']}).json()
    member=users['member']
    view=member.get('/api/drafts/'+d['id']);assert view.status_code==200
    assert '私有原话' not in view.text and view.json()['demand_snapshot']['evidence_restricted']
    assert '私有原话' not in member.get('/api/drafts').text
    edited=member.patch('/api/drafts/'+d['id'],json={'version':1,'body':'修订正文'})
    assert edited.status_code==200 and '私有原话' not in edited.text


def test_explicit_clear_demand_and_generated_snapshot_persist(env,monkeypatch):
    import httpx,json
    import backend.app.worker as worker
    app,c,ip,users=env;t=topic(c,ip['id']);lead(c,ip['id'],'咨询多少钱？')
    rebuild(c,ip['id']);n=c.get('/api/records/needs').json()[0]
    d=c.post('/api/drafts',json={'ip_id':ip['id'],'title':'咨询','need_id':n['id']}).json();w=ready(c,ip['id']);p=params(w,d)
    url=f"/api/drafts/{d['id']}/rewrite"
    preview=c.post(url+'/context',json={**p,'need_id':None});assert preview.status_code==200 and 'need_id' not in preview.json()
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            raw=json.dumps({'title':'咨询前先确认','body':'先明确需要，再核对费用。','fact_check':'具体费用待核实'})
            return httpx.Response(200,json={'choices':[{'message':{'content':raw}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(worker.httpx,'Client',Client)
    c.put('/api/settings',json={'model':{'base_url':'https://example.org/v1','model':'controlled','api_key':'test-key','enabled':True}})
    j=c.post(url,json={**p,'need_id':n['id'],'request_id':'persist-snapshot'}).json();assert worker.run_once(app)
    job=next(x for x in c.get('/api/jobs').json() if x['id']==j['id']);assert job['status']=='completed',job
    candidate=c.get('/api/drafts/'+job['draft_id']).json()
    assert candidate['need_id']==n['id'] and candidate['demand_snapshot']['count']==1


@pytest.mark.parametrize('text',['???','😂😂😂？','吗？','?'])
def test_nonmeaningful_questions_are_not_demands(text):
    from backend.app.demand_analysis import analyze_comment
    assert analyze_comment(text)['intent']=='待核实'


def test_job_rechecks_private_source_owner_permissions(env):
    from backend.app.db import User
    from sqlalchemy import select
    app,c,ip,users=env;topic(c,ip['id'])
    with app.state.Session() as db:
        member=db.scalar(select(User).where(User.username=='member'))
        r=Record(kind='leads',owner_id=member.id,payload={'body':'咨询多少钱？','title':'待核实评论'});db.add(r);db.flush();key=r.id;db.commit()
    leader=users['leader'];rebuild(leader,ip['id'],include_unassigned_ids=[key]);n=leader.get('/api/records/needs').json()[0]
    d=leader.post('/api/drafts',json={'ip_id':ip['id'],'title':'需求选题','need_id':n['id']}).json();w=ready(c,ip['id'])
    c.put('/api/settings',json={'model':{'base_url':'http://127.0.0.1:1/v1','model':'controlled','api_key':'key','enabled':True}})
    j=leader.post(f"/api/drafts/{d['id']}/rewrite",json={**params(w,d),'request_id':'source-access-revoked'}).json()
    other=c.post('/api/teams',json={'name':'其他团队'}).json()
    with app.state.Session() as db:
        member=db.scalar(select(User).where(User.username=='member'));member.team_id=other['id'];db.commit()
    assert run_once(app)
    result=next(x for x in leader.get('/api/jobs').json() if x['id']==j['id'])
    assert result['status']=='failed' and '需求' in result['error']
