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


def upload_knowledge(client, title, text, *, verify=True, valid_until=None):
    preview = client.post('/api/knowledge/preview', data={'category_id': 'product-medical'},
                          files={'files': (title + '.md', text.encode(), 'text/markdown')})
    assert preview.status_code == 200, preview.text
    imported = client.post('/api/knowledge/confirm', json={'batch_id': preview.json()['id']})
    assert imported.status_code == 200, imported.text
    document = imported.json()['items'][0]
    if verify:
        verified = client.patch(f"/api/knowledge/{document['id']}",
                                json={'status': 'verified', 'valid_until': valid_until})
        assert verified.status_code == 200, verified.text
        return verified.json()
    return document


def generate_input(ip, **extra):
    return {'ip_id': ip['id'], 'name': '家庭医疗写手', 'industry': '社保断缴',
            'skill': 'original-video-writer', 'answers': answers(), **extra}


def test_generate_combines_valid_shared_knowledge_and_selected_format(env):
    _, admin, ip, users = env
    automatic = upload_knowledge(admin, '社保断缴咨询流程', '社保断缴后先核对保障生效时间。')
    selected = upload_knowledge(admin, '别的文档', '仅用于用户指定资料测试的正文。')
    upload_knowledge(admin, '社保断缴待核实', '社保断缴情况尚未确认。', verify=False)
    upload_knowledge(admin, '社保断缴历史说明', '社保断缴的旧说明。', valid_until='2000-01-01')

    result = users['leader'].post('/api/writers/generate', json=generate_input(
        ip, knowledge_ids=[selected['id'], selected['id']]))

    assert result.status_code == 200, result.text
    profile = result.json()
    assert profile['status'] == 'active' and profile['ip_id'] == ip['id']
    assert profile['word_limit'] == 1500 and profile['skill'] == 'original-video-writer'
    assert '称呼你' in profile['skill_text'] and '内容文体参考：原创口播' in profile['skill_text']
    assert selected['text'] not in profile['skill_text']
    assert profile['knowledge_ids'] == [selected['id']]
    assert [source['id'] for source in profile['knowledge_sources']] == [selected['id'], automatic['id']]
    for source, document in zip(profile['knowledge_sources'], (selected, automatic)):
        assert source == {key: document[key] for key in ('id', 'title', 'category_id', 'category_path', 'hash')}
    assert admin.get(f"/api/writers/{profile['id']}").json() == profile


def test_regenerate_preserves_versioned_binding_and_rejects_stale_updates(env):
    _, admin, ip, _ = env
    document = upload_knowledge(admin, '服务清单', '各项服务以确认清单为准。')
    data = generate_input(ip, knowledge_ids=[document['id']])
    profile = admin.post('/api/writers/generate', json=data).json()
    assert profile.get('id'), profile
    admin.post(f"/api/writers/{profile['id']}/default", json={'version': profile['version']})
    profile = admin.get(f"/api/writers/{profile['id']}").json()
    update = generate_input(ip, name='改版写手', writer_id=profile['id'], expected_version=profile['version'])

    regenerated = admin.post('/api/writers/generate', json=update)
    assert regenerated.status_code == 200, regenerated.text
    current = regenerated.json()
    assert current['id'] == profile['id'] and current['version'] == profile['version'] + 1
    assert current['status'] == 'active' and current['is_default'] is True
    assert current['knowledge_ids'] == [document['id']]
    assert admin.post('/api/writers/generate', json=update).status_code == 409

    update.update(expected_version=current['version'], knowledge_ids=[])
    cleared = admin.post('/api/writers/generate', json=update).json()
    assert cleared['knowledge_ids'] == []
    assert cleared['knowledge_sources'] == []
    assert cleared['history'][-1]['knowledge_ids'] == [document['id']]
    assert cleared['history'][-1]['knowledge_sources'] == current['knowledge_sources']


def test_generate_enforces_writer_permissions_and_validates_before_saving(env):
    _, admin, ip, users = env
    member = users['member']
    pending = upload_knowledge(admin, '未核实说明', '只供编辑阅读。', verify=False)
    expired = upload_knowledge(admin, '旧说明', '已经过期。', valid_until='2000-01-01')
    for extra in ({'knowledge_ids': [pending['id']]}, {'knowledge_ids': [expired['id']]},
                  {'knowledge_ids': ['missing']}, {'answers': []}, {'skill': 'writer-self-evolve'}):
        assert admin.post('/api/writers/generate', json=generate_input(ip, **extra)).status_code == 422
    assert admin.get('/api/writers').json() == []

    result = member.post('/api/writers/generate', json=generate_input(ip))
    assert result.status_code == 200, result.text
    profile = result.json()
    assert profile['status'] == 'compiled' and not profile.get('approved_by')
    assert member.post(f"/api/writers/{profile['id']}/activate", json={'version': profile['version']}).status_code == 403
    assert member.post('/api/writers/generate', json=generate_input({'id': 'not-authorized'})).status_code == 403
    active = users['leader'].post(f"/api/writers/{profile['id']}/activate", json={'version': profile['version']}).json()
    forbidden = member.post('/api/writers/generate', json=generate_input(
        ip, writer_id=profile['id'], expected_version=active['version']))
    assert forbidden.status_code == 403
    bad_update = admin.post('/api/writers/generate', json=generate_input(
        ip, writer_id=profile['id'], expected_version=active['version'], knowledge_ids=[expired['id']]))
    assert bad_update.status_code == 422
    assert admin.get(f"/api/writers/{profile['id']}").json() == active


def test_draft_apis_preserve_explicit_bindings_for_the_legacy_compile_route(env):
    _, admin, ip, users = env
    member = users['member']
    document = upload_knowledge(admin, '核验资料', '用于编辑草稿时选择。')
    created = member.post('/api/writers', json=generate_input(ip, knowledge_ids=[document['id']]))
    assert created.status_code == 200, created.text
    profile = created.json()
    assert profile['status'] == 'draft' and profile['knowledge_ids'] == [document['id']]
    endpoint = f"/api/writers/{profile['id']}"
    updated = member.patch(endpoint, json={'version': profile['version'], 'industry': '保险咨询'})
    assert updated.status_code == 200 and updated.json()['knowledge_ids'] == [document['id']]
    compiled = member.post(endpoint + '/compile', json={'version': updated.json()['version']})
    assert compiled.status_code == 200 and compiled.json()['knowledge_sources'][0]['id'] == document['id']
    cleared = member.patch(endpoint, json={'version': compiled.json()['version'], 'knowledge_ids': []})
    assert cleared.status_code == 200 and cleared.json()['knowledge_ids'] == []
