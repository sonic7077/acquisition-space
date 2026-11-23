import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app


@pytest.fixture()
def env(tmp_path):
    app=create_app(tmp_path);c=TestClient(app)
    c.post('/api/auth/login',json={'username':'admin','password':'admin'})
    team=c.post('/api/teams',json={'name':'写手团队'}).json()
    ip=c.post('/api/ips',json={'name':'写手IP','team_id':team['id']}).json()
    users={}
    for role in ['leader','member']:
        c.post('/api/users',json={'username':role,'name':role,'password':'Password-12345','role':role,'team_id':team['id'],'ip_ids':[ip['id']]})
        u=TestClient(app);u.post('/api/auth/login',json={'username':role,'password':'Password-12345'});users[role]=u
    return app,c,ip,users


def answers():
    return ['提供保险咨询服务，年限待核实','35岁家庭，关心预算','保险咨询，价格待核实','资质待核实','预算有限怎么选择；已有保险怎么调整','不承诺收益','不讲迷信','直接说判断，例如先看条款再看价格','无固定口头禅','称呼你，不叫家人们','1500','小红书和视频号，优先视频号']


def writer(c,ip,name='专业顾问'):
    r=c.post('/api/writers',json={'ip_id':ip,'name':name,'answers':answers(),'skill':'viral-rewrite-writer'})
    assert r.status_code==200,r.text
    return r.json()


def ready(c,ip,name='专业顾问'):
    w=writer(c,ip,name)
    w=c.post(f"/api/writers/{w['id']}/compile",json={'version':w['version']}).json()
    r=c.post(f"/api/writers/{w['id']}/activate",json={'version':w['version']})
    assert r.status_code==200,r.text
    return r.json()


def test_questionnaire_compile_and_versions(env):
    app,c,ip,users=env
    refs=c.get('/api/writers/reference');assert refs.status_code==200
    assert len(refs.json()['questions'])==12 and len(refs.json()['templates'])==8
    w=c.post('/api/writers',json={'ip_id':ip['id'],'name':'未完成','answers':[]}).json()
    assert c.post(f"/api/writers/{w['id']}/compile",json={'version':w['version']}).status_code==422
    assert c.post('/api/writers',json={'ip_id':ip['id'],'name':'无效','answers':answers()[:10]+['-1',answers()[11]]}).status_code==422
    w=ready(c,ip['id']);assert w['word_limit']==1500 and w['status']=='active'
    assert '称呼你' in w['skill_text'] and '待核实' in w['skill_text']
    old=w['version'];r=c.patch(f"/api/writers/{w['id']}",json={'version':old,'name':'新名字','answers':answers()})
    assert r.status_code==200 and r.json()['status']=='stale'
    assert c.patch(f"/api/writers/{w['id']}",json={'version':old,'name':'冲突'}).status_code==409
    assert c.post(f"/api/writers/{w['id']}/activate",json={'version':r.json()['version']}).status_code==409
    assert r.json()['history'][-1]['skill_text']==w['skill_text']


def test_permissions_rules_and_defaults(env):
    app,c,ip,users=env;member=users['member'];leader=users['leader']
    w=writer(member,ip['id']);w=member.post(f"/api/writers/{w['id']}/compile",json={'version':w['version']}).json()
    assert member.post(f"/api/writers/{w['id']}/activate",json={'version':w['version']}).status_code==403
    w=leader.post(f"/api/writers/{w['id']}/activate",json={'version':w['version']}).json()
    assert member.patch(f"/api/writers/{w['id']}",json={'version':w['version'],'name':'修改团队生效规则'}).status_code==403
    r=member.post(f"/api/writers/{w['id']}/rules",json={'kind':'replace','title':'不用模糊判断','before':'差不多','after':'请核对合同','source':'manual'})
    assert r.status_code==200,r.text
    rule=r.json()
    assert member.post(f"/api/writers/{w['id']}/rules/{rule['id']}/adopt",json={}).status_code==403
    assert leader.post(f"/api/writers/{w['id']}/rules/{rule['id']}/adopt",json={}).status_code==200
    w=c.get('/api/writers').json()[0];assert w['status']=='stale'
    w=c.post(f"/api/writers/{w['id']}/compile",json={'version':w['version']}).json()
    w=c.post(f"/api/writers/{w['id']}/activate",json={'version':w['version']}).json()
    assert '不用模糊判断' in w['skill_text']
    assert leader.post(f"/api/writers/{w['id']}/default",json={'version':w['version']}).status_code==200
    other=c.post('/api/teams',json={'name':'外组'}).json();outside=c.post('/api/ips',json={'name':'外部IP','team_id':other['id']}).json()
    assert member.post('/api/writers',json={'ip_id':outside['id'],'name':'越权'}).status_code==403
    outsider=ready(c,outside['id'],'别人的写手')
    assert leader.get(f"/api/writers/{outsider['id']}").status_code==403
    assert all(x['ip_id']==ip['id'] for x in member.get('/api/writers').json())


def test_learned_rules_need_three_independent_drafts(env):
    app,c,ip,users=env;w=ready(c,ip['id'])
    rule=c.post(f"/api/writers/{w['id']}/rules",json={'kind':'delete','title':'去掉问候','before':'大家好','after':'','source':'learned'}).json()
    assert c.post(f"/api/writers/{w['id']}/rules/{rule['id']}/adopt",json={'evidence_ids':[]}).status_code==409


def test_exported_skill_contains_selected_template_not_empty_placeholders(env):
    app,c,ip,users=env
    templates={r['id']:r['text'] for r in c.get('/api/writers/reference').json()['templates']}
    w=ready(c,ip['id'])
    original=c.post('/api/writers',json={'ip_id':ip['id'],'name':'原创写手','answers':answers(),'skill':'original-video-writer'}).json()
    compiled=c.post(f"/api/writers/{original['id']}/compile",json={'version':original['version']}).json()
    for profile in (w,compiled):
        template=templates[profile['skill']]
        writing_rules=template.split('\n## ',2)[2]
        assert '## '+writing_rules.strip() in profile['skill_text']
        assert '## 先填' not in profile['skill_text']
        assert '＿＿＿' not in profile['skill_text']
