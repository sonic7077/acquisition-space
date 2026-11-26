import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app


@pytest.fixture()
def env(tmp_path):
    app = create_app(tmp_path)
    admin = TestClient(app)
    from backend.app.manage import reset_password
    reset_password(app, 'admin', 'Admin-123456!')
    assert admin.post('/api/auth/login', json={'username': 'admin', 'password': 'Admin-123456!'}).status_code == 200
    team = admin.post('/api/teams', json={'name': '内容一组'}).json()
    ip = admin.post('/api/ips', json={'name': '测试IP', 'team_id': team['id']}).json()
    users = {}
    for role in ['leader', 'member']:
        r = admin.post('/api/users', json={'username': role, 'password': 'Member-123456!', 'name': role, 'role': role, 'team_id': team['id'], 'ip_ids': [ip['id']]})
        assert r.status_code == 200, r.text
        c = TestClient(app)
        assert c.post('/api/auth/login', json={'username': role, 'password': 'Member-123456!'}).status_code == 200
        users[role] = c
    return app, admin, users, ip, team


def test_settings_admin_only_and_secret_never_returned(env):
    app, admin, users, ip, team = env
    assert TestClient(app).get('/api/settings').status_code == 401
    for c in users.values():
        assert c.get('/api/settings').status_code == 403
        assert c.put('/api/settings', json={'model': {'api_key': 'stolen'}}).status_code == 403
        assert c.get('/api/users').status_code == 403
    key = 'test-private-api-key-87654321'
    r = admin.put('/api/settings', json={'model': {'base_url': 'https://example.org/v1', 'model': 'test-model', 'api_key': key}})
    assert r.status_code == 200, r.text
    assert key not in r.text
    assert r.json()['model']['api_key_configured'] is True
    assert key not in admin.get('/api/settings').text
    assert key.encode() not in (app.state.data_dir / 'system.db').read_bytes()
    assert key not in admin.get('/api/audit').text


def test_only_three_roles_and_last_admin(env):
    app, admin, users, ip, team = env
    assert admin.post('/api/users', json={'username': 'other', 'name': '其他', 'password': 'Abcd-123456!', 'role': 'viewer'}).status_code == 422
    me = admin.get('/api/auth/me').json()
    assert admin.patch(f"/api/users/{me['id']}", json={'role': 'member'}).status_code == 409
    assert admin.patch(f"/api/users/{me['id']}", json={'active': False}).status_code == 409
    assert users['leader'].post('/api/teams', json={'name': '越权'}).status_code == 403


def test_session_revoke_and_origin(env):
    app, admin, users, ip, team = env
    assert admin.put('/api/settings', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    member = next(u for u in admin.get('/api/users').json() if u['role'] == 'member')
    assert admin.patch(f"/api/users/{member['id']}", json={'active': False}).status_code == 200
    assert users['member'].get('/api/auth/me').status_code == 401


def upload(client, ip_id, name='产品/服务.md', content=b'Our service includes weekly consulting.'):
    return client.post('/api/knowledge/preview', data={'ip_id': ip_id, 'category_id': 'methods-service'}, files=[('files', (name, content, 'application/octet-stream'))])


def test_knowledge_preview_confirm_verify_and_context(env):
    app, admin, users, ip, team = env
    c = users['member']
    batch = upload(c, ip['id']).json()
    assert batch['items'][0]['path'] == '产品/服务.md'
    assert admin.get('/api/knowledge').json() == []
    r = c.post('/api/knowledge/confirm', json={'batch_id': batch['id']})
    assert r.status_code == 200, r.text
    doc = r.json()['items'][0]
    assert doc['status'] == 'pending'
    assert c.patch(f"/api/knowledge/{doc['id']}", json={'status': 'verified'}).status_code == 403
    assert c.post('/api/writing/context', json={'ip_id': ip['id'], 'topic': 'weekly consulting', 'skill': 'original-video-writer'}).json()['sources'] == []
    assert users['leader'].patch(f"/api/knowledge/{doc['id']}", json={'status': 'verified'}).status_code == 200
    ctx = c.post('/api/writing/context', json={'ip_id': ip['id'], 'topic': 'weekly consulting', 'skill': 'original-video-writer'}).json()
    assert ctx['sources'][0]['id'] == doc['id']
    assert 'weekly consulting' in ctx['prompt']
    assert c.post('/api/knowledge/confirm', json={'batch_id': batch['id']}).json()['items'][0]['id'] == doc['id']


def test_knowledge_zip_security_and_templates(env):
    app, admin, users, ip, team = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('../escape.md', 'bad')
    assert upload(admin, ip['id'], 'bad.zip', buf.getvalue()).status_code == 422
    r = upload(admin, ip['id'], '老板说明书.md', '姓名：【填写】\n这是模板'.encode())
    assert r.status_code == 200
    doc = admin.post('/api/knowledge/confirm', json={'batch_id': r.json()['id']}).json()['items'][0]
    assert doc['status'] == 'template'
    assert admin.patch(f"/api/knowledge/{doc['id']}", json={'status': 'verified'}).status_code == 422


def test_cross_team_scope_in_all_routes(env):
    app, admin, users, ip, team = env
    t2 = admin.post('/api/teams', json={'name': '其他团队'}).json()
    ip2 = admin.post('/api/ips', json={'name': '其他IP', 'team_id': t2['id']}).json()
    doc = admin.post('/api/knowledge/confirm', json={'batch_id': upload(admin, ip2['id']).json()['id']}).json()['items'][0]
    for c in users.values():
        assert ip2['id'] not in [x['id'] for x in c.get('/api/ips').json()]
        assert c.get(f"/api/knowledge/{doc['id']}").status_code == 200
        assert c.post('/api/writing/context', json={'ip_id': ip2['id'], 'topic': 'secret', 'skill': 'original-video-writer'}).status_code == 403
        assert c.get('/api/dashboard', params={'ip_id': ip['id']}).json()['knowledge'] == 1
        assert c.post('/api/records/materials', json={'ip_id': ip2['id'], 'title': 'bad'}).status_code == 403


def test_archived_ip_is_unavailable_but_keeps_its_records(env):
    from backend.app.db import Record, now

    app, admin, users, ip, team = env
    ip_id = ip['id']
    material = admin.post('/api/records/materials', json={'ip_id': ip_id, 'title': '停用前的素材'}).json()
    other_ip = admin.post('/api/ips', json={'name': '新IP', 'team_id': team['id']}).json()
    assert admin.put('/api/settings', json={'collection': {'enabled': True, 'api_key': 'test-key'}}).status_code == 200
    with app.state.Session.begin() as db:
        row = db.get(Record, ip_id)
        row.payload = {**row.payload, 'archived_at': now()}

    for client in (admin, users['leader'], users['member']):
        assert ip_id not in {row['id'] for row in client.get('/api/ips').json()}
        assert client.get('/api/records/materials').json() == []
        assert client.post('/api/collection/search', json={'ip_id': ip_id, 'keywords': '保险'}).status_code == 403
    assert [row['id'] for row in admin.get('/api/ips').json()] == [other_ip['id']]
    member_id = users['member'].get('/api/auth/me').json()['id']
    assert admin.patch(f'/api/users/{member_id}', json={'ip_ids': [ip_id]}).status_code == 422
    assert admin.post('/api/collection/search', json={'keywords': '保险'}).status_code == 200
    with app.state.Session() as db:
        assert db.get(Record, material['id']).ip_id == ip_id


def test_draft_approval_version_and_schedule_evidence(env):
    app, admin, users, ip, team = env
    c = users['member']
    draft = c.post('/api/drafts', json={'ip_id': ip['id'], 'title': '标题', 'body': '这是没有具体事实声明的原创口播。', 'skill': 'original-video-writer'}).json()
    did = draft['id']
    assert c.post(f'/api/drafts/{did}/approve', json={'version': 1}).status_code == 403
    assert users['leader'].post(f'/api/drafts/{did}/approve', json={'version': 1}).status_code == 200
    assert c.patch(f'/api/drafts/{did}', json={'version': 1, 'body': '修改后的原创内容。'}).status_code == 200
    assert c.get(f'/api/drafts/{did}').json()['approved_version'] is None
    assert c.patch(f'/api/drafts/{did}', json={'version': 1, 'body': '旧版覆盖'}).status_code == 409
    assert admin.post('/api/schedules', json={'draft_id': did, 'platform': '抖音', 'account': '自有账号', 'planned_at': '2026-11-20T10:00:00+08:00'}).status_code == 409
    assert users['leader'].post(f'/api/drafts/{did}/approve', json={'version': 2}).status_code == 200
    asset = c.post('/api/assets', data={'draft_id': did}, files={'file': ('T-test.mp4', b'\x00\x00\x00\x18ftypmp42' + b'0'*32, 'video/mp4')})
    assert asset.status_code == 200, asset.text
    sched = c.post('/api/schedules', json={'draft_id': did, 'platform': '抖音', 'account': '自有账号', 'planned_at': '2026-11-20T10:00:00+08:00'}).json()
    assert c.patch(f"/api/schedules/{sched['id']}", json={'status': 'published'}).status_code == 422
    assert c.patch(f"/api/schedules/{sched['id']}", json={'status': 'published', 'evidence': 'https://example.org/post/1'}).status_code == 200


def test_source_overlap_blocks_and_adoption_idempotent(env):
    app, admin, users, ip, team = env
    body = '这是一个完全相同的连续对标逐字稿测试片段。'
    d = admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '重复测试', 'body': body, 'reference': body}).json()
    assert admin.post(f"/api/drafts/{d['id']}/approve", json={'version': 1}).status_code == 409
    mat = admin.post('/api/records/materials', json={'ip_id': ip['id'], 'title': '素材', 'body': '参考内容'}).json()
    a = admin.post(f"/api/materials/{mat['id']}/adopt", json={'skill': 'original-video-writer'})
    b = admin.post(f"/api/materials/{mat['id']}/adopt", json={'skill': 'original-video-writer'})
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()['id'] == b.json()['id']


def test_metrics_import_idempotent_and_missing_model(env):
    app, admin, users, ip, team = env
    data = 'date,work_id,views,leads\n2026-11-15,v1,100,3\n'.encode()
    for _ in range(2):
        r = admin.post('/api/metrics/import', data={'ip_id': ip['id'], 'platform': '抖音'}, files={'file': ('report.csv', data, 'text/csv')})
        assert r.status_code == 200, r.text
    assert len(admin.get('/api/records/metrics').json()) == 1
    assert admin.post('/api/writing/generate', json={'ip_id': ip['id'], 'topic': '产品', 'skill': 'original-video-writer'}).status_code == 409
    assert admin.get('/api/reports').status_code == 200


def test_all_eight_skill_templates_available(env):
    app, admin, users, ip, team = env
    skills = admin.get('/api/skills').json()
    assert len(skills) == 8
    for skill in skills:
        r = admin.post('/api/writing/context', json={'ip_id': ip['id'], 'topic': '测试主题', 'skill': skill['id']})
        assert r.status_code == 200
        assert r.json()['template_available'], skill['id']


def test_rules_need_three_independent_drafts_and_human_adoption(env):
    app, admin, users, ip, team = env
    drafts = [admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '独立稿', 'body': '原始内容'}).json() for _ in range(3)]
    rule = admin.post('/api/records/rules', json={'ip_id': ip['id'], 'title': '用客户原话开头', 'body': '开头使用真实客户问题'}).json()
    assert admin.post(f"/api/rules/{rule['id']}/adopt", json={'evidence_ids': [drafts[0]['id']]*3}).status_code == 409
    assert users['member'].post(f"/api/rules/{rule['id']}/adopt", json={'evidence_ids': [d['id'] for d in drafts]}).status_code == 403
    r = users['leader'].post(f"/api/rules/{rule['id']}/adopt", json={'evidence_ids': [d['id'] for d in drafts]})
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'adopted'
    ctx = admin.post('/api/writing/context', json={'ip_id': ip['id'], 'topic': '服务', 'skill': 'original-video-writer'}).json()
    assert '用客户原话开头' in ctx['prompt']


def test_worker_missing_configuration_returns_safe_failure(env):
    from backend.app.worker import run_once
    from backend.app.db import Record
    app, admin, users, ip, team = env
    with app.state.Session() as db:
        row = Record(kind='jobs', ip_id=ip['id'], owner_id=admin.get('/api/auth/me').json()['id'], payload={'status': 'queued', 'context': {'topic': '服务', 'prompt': 'text', 'ip_id': ip['id'], 'skill': 'original-video-writer'}, 'attempts': 0})
        db.add(row)
        db.commit()
    assert run_once(app) is True
    jobs = admin.get('/api/jobs').json()
    assert jobs[0]['status'] == 'failed'
    assert '配置' in jobs[0]['error']


def test_member_cannot_create_verified_facts_or_exceed_limits(env):
    app, admin, users, ip, team = env
    r = users['member'].post('/api/drafts', json={'ip_id': ip['id'], 'title': '事实', 'body': '已经核实。', 'fact_status': 'verified'})
    assert r.status_code == 403
    r = admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '事实', 'body': '正文', 'fact_status': 'arbitrary'})
    assert r.status_code == 422


def test_persistence_across_app_restart(env):
    app, admin, users, ip, team = env
    admin.post('/api/records/needs', json={'ip_id': ip['id'], 'title': '客户问价格', 'body': '多少钱？'})
    restarted = TestClient(create_app(app.state.data_dir))
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'Admin-123456!'}).status_code == 200
    assert restarted.get('/api/records/needs').json()[0]['title'] == '客户问价格'


def test_valid_zip_retains_nested_paths_and_deduplicates(env):
    app, admin, users, ip, team = env
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('原知识库/产品/咨询服务.md', '咨询包含每周沟通。')
        z.writestr('原知识库/案例/实例.txt', '客户经过咨询后明确了选择范围。')
    batch = upload(admin, ip['id'], '知识库.zip', buf.getvalue()).json()
    assert len(batch['items']) == 2
    assert batch['items'][0]['path'] == '原知识库/产品/咨询服务.md'
    assert admin.post('/api/knowledge/confirm', json={'batch_id': batch['id']}).status_code == 200
    second = upload(admin, ip['id'], '知识库.zip', buf.getvalue()).json()
    assert all(d['duplicate'] for d in second['items'])
    admin.post('/api/knowledge/confirm', json={'batch_id': second['id']})
    assert len(admin.get('/api/knowledge').json()) == 2


def test_knowledge_change_invalidates_unpublished_approval(env):
    app, admin, users, ip, team = env
    batch = upload(admin, ip['id']).json()
    doc = admin.post('/api/knowledge/confirm', json={'batch_id': batch['id']}).json()['items'][0]
    admin.patch('/api/knowledge/'+doc['id'], json={'status': 'verified'})
    draft = admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '参考事实', 'body': '事实已人工确认。', 'fact_status': 'verified', 'source_ids': [doc['id']]}).json()
    assert admin.post('/api/drafts/'+draft['id']+'/approve', json={'version': 1}).status_code == 200
    admin.patch('/api/knowledge/'+doc['id'], json={'text': 'Changed terms require new verification.'})
    changed = admin.get('/api/drafts/'+draft['id']).json()
    assert changed['approved_version'] is None
    assert changed['fact_status'] == 'pending'


def test_shared_knowledge_can_be_cited_by_another_team_and_revokes_approval(env):
    app, admin, users, ip, team = env
    second_team = admin.post('/api/teams', json={'name': '保险团队'}).json()
    second_ip = admin.post('/api/ips', json={'name': '保险IP', 'team_id': second_team['id']}).json()
    batch = admin.post('/api/knowledge/preview', data={'category_id':'needs-faq'},
                       files=[('files', ('报销说明.md', '医保外用药报销需查病种和条款'.encode()))])
    assert batch.status_code == 200, batch.text
    doc = admin.post('/api/knowledge/confirm', json={'batch_id':batch.json()['id']}).json()['items'][0]
    assert users['leader'].patch('/api/knowledge/'+doc['id'], json={'status':'verified'}).status_code == 200
    context = admin.post('/api/writing/context', json={
        'ip_id':second_ip['id'], 'skill':'original-video-writer', 'topic':'医保外用药报销',
    }).json()
    assert [source['id'] for source in context['sources']] == [doc['id']]
    draft = admin.post('/api/drafts', json={
        'ip_id':second_ip['id'], 'title':'报销解读', 'body':'查看条款中的责任范围。',
        'fact_status':'verified', 'source_ids':[doc['id']],
    })
    assert draft.status_code == 200, draft.text
    key = draft.json()['id']
    assert admin.post(f'/api/drafts/{key}/approve', json={'version':1}).status_code == 200
    assert admin.post('/api/assets', data={'draft_id':key},
                      files={'file':('guide.mp4', b'\x00\x00\x00\x18ftypmp42' + b'0'*32, 'video/mp4')}).status_code == 200
    schedule = admin.post('/api/schedules', json={'draft_id':key,'platform':'抖音','account':'保险科普','planned_at':'2026-12-12T10:00:00+08:00'})
    assert schedule.status_code == 200, schedule.text
    assert users['leader'].get(f'/api/drafts/{key}').status_code == 403
    assert users['leader'].patch('/api/knowledge/'+doc['id'], json={'text':'修改后需要复核。'}).status_code == 200
    assert admin.get(f'/api/drafts/{key}').json()['approved_version'] is None
    assert admin.get('/api/schedules').json()[0]['status'] == 'needs_review'


def test_skill_prioritizes_matching_categories_and_omits_irrelevant_knowledge():
    from backend.app.db import Record
    from backend.app.writing import build_context

    ip = Record(id='example', kind='ips', payload={'name':'保险科普'})
    documents = [
        Record(id='article', kind='knowledge', payload={'title':'院外用药','path':'A.md','category_id':'product-medical',
               'category_path':'2-产品库/带病可投的医疗险','text':'院外用药理赔以合同为准。','hash':'a','status':'verified'}),
        Record(id='case', kind='knowledge', payload={'title':'院外用药','path':'B.md','category_id':'cases-customers',
               'category_path':'4-素材案例库/客户案例','text':'院外用药理赔以合同为准。','hash':'b','status':'verified'}),
        Record(id='irrelevant', kind='knowledge', payload={'title':'摄影灯具','path':'C.md','category_id':'cases-customers',
               'category_path':'4-素材案例库/客户案例','text':'灯光的色温需要统一。','hash':'c','status':'verified'}),
    ]
    for skill, expected in [('gzh-writer',['article','case']), ('moments-writer',['case','article'])]:
        context = build_context(ip, documents, '院外用药理赔', skill)
        assert [source['id'] for source in context['sources']] == expected


def test_worker_success_against_controlled_model(env):
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from backend.app.worker import run_once
    import json
    app, admin, users, ip, team = env
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.path, self.headers.get('Authorization'), data))
            output = json.dumps({'choices': [{'message': {'content': '这是受控模型返回的口播正文。'}}]}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(output)
        def log_message(self, *_):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        admin.put('/api/settings', json={'model': {'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'controlled-model', 'api_key': 'local-test-key', 'enabled': True}})
        r = users['member'].post('/api/writing/generate', json={'ip_id': ip['id'], 'topic': '客户咨询', 'skill': 'original-video-writer'})
        assert r.status_code == 200
        assert run_once(app)
        job = next(j for j in users['member'].get('/api/jobs').json() if j['id'] == r.json()['id'])
        assert job['status'] == 'completed', job
        draft = admin.get('/api/drafts/'+job['draft_id']).json()
        assert '受控模型' in draft['body']
        assert draft['approved_version'] is None and draft['fact_status'] == 'pending'
        assert seen[0][0] == '/v1/chat/completions'
        assert seen[0][1] == 'Bearer local-test-key'
        assert seen[0][2]['model'] == 'controlled-model'
    finally:
        server.shutdown()
        server.server_close()


def test_overlap_counts_every_occurrence_without_double_count():
    from backend.app.writing import checks
    p = {'title': '重复覆盖', 'body': 'abcdefghijk abcdefghijk', 'reference': 'abcdefghijk', 'skill': 'viral-rewrite-writer', 'fact_status': 'none'}
    report = checks(p)[-1]
    assert report['ratio'] == 1.0
    assert sum(x['length'] for x in report['snippets']) == 22


def test_overlap_reports_full_draft_sentences_ranked_by_matching_characters():
    from backend.app.writing import checks

    long_sentence = '先' * 250 + '春夏秋冬、东西南北前后。'
    body = '甲乙丙丁。戊己庚辛。' + long_sentence
    report = checks({'body': body, 'reference': '甲乙丙丁戊己庚辛。春夏秋冬东西南北前后'})[-1]
    assert report['matched'] == 18
    assert report['matched_sentences'] == [
        {'text': long_sentence, 'matched': 10, 'fragments': [{'text': '春夏秋冬东西南北前后', 'length': 10}]},
        {'text': '甲乙丙丁。', 'matched': 4, 'fragments': [{'text': '甲乙丙丁', 'length': 4}]},
        {'text': '戊己庚辛。', 'matched': 4, 'fragments': [{'text': '戊己庚辛', 'length': 4}]},
    ]


def test_oral_review_rules_preserve_configured_limit_and_flag_phrases():
    from backend.app.writing import checks
    payload = {
        'title': '这是一个刻意写到十六个字以上的口播标题',
        'body': '先看条款——然后到评 论 区打「保障」关键词，私信领取资料。',
        'skill': 'viral-rewrite-writer',
        'fact_status': 'none',
        'word_limit': 2000,
    }
    report = {rule['id']: rule for rule in checks(payload)}
    assert report['title_length']['passed'] is False
    assert report['title_length']['limit'] == 16
    assert report['body_length']['limit'] == 1500
    assert report['dash']['passed'] is False
    assert report['comments_phrase']['passed'] is False
    assert report['traffic']['passed'] is False and report['traffic']['blocking']
    assert report['resource_hook']['passed'] is False and not report['resource_hook']['blocking']
    valid = {rule['id']: rule for rule in checks({
        **payload,
        'body': '先比对这两项。 我把核对步骤整理成了一份《家庭保单检查清单》，里面有三个检查项。',
        'word_limit': 700,
    })}
    assert valid['resource_hook']['passed'] is True
    assert valid['traffic']['passed'] is True
    assert valid['body_length']['limit'] == 700
    non_oral = {rule['id'] for rule in checks({**payload, 'skill': 'gzh-writer', 'word_limit': 2500})}
    assert 'resource_hook' not in non_oral and 'traffic' not in non_oral


def test_live_review_preview_uses_saved_rules_and_does_not_modify_draft(env):
    app, admin, users, ip, team = env
    saved = admin.post('/api/drafts', json={
        'ip_id': ip['id'], 'title': '逐字稿核对', 'body': '甲乙丙丁，戊己庚辛。',
        'reference': '甲乙丙丁戊己庚辛', 'skill': 'viral-rewrite-writer',
    }).json()
    endpoint = '/api/drafts/checks'
    source = {'draft_id': saved['id'], 'version': saved['version']}
    assert TestClient(app).post(endpoint, json=source).status_code == 401
    assert users['member'].post(endpoint, json={**source, 'version': 2}).status_code == 409
    editing = {**source, 'body': saved['body'] + '壬' * 92}
    preview = users['member'].post(endpoint, json=editing)
    assert preview.status_code == 200, preview.text
    overlap = preview.json()['checks'][-1]
    assert overlap['state'] == 'compared'
    assert overlap['ratio'] == overlap['threshold'] == .08
    assert overlap['passed'] and overlap['min_match'] == 8
    assert overlap['snippets'] == [{'text': '甲乙丙丁戊己庚辛', 'length': 8, 'sentence': saved['body']}]
    assert admin.get('/api/drafts/' + saved['id']).json()['body'] == saved['body']
    assert admin.post(f"/api/drafts/{saved['id']}/approve", json={'version': 1}).status_code == 409
    assert admin.put('/api/settings', json={'rules': {'overlap_min': 9, 'overlap_max': .07}}).status_code == 200
    changed = users['member'].post(endpoint, json=editing).json()['checks'][-1]
    assert changed['min_match'] == 9 and changed['threshold'] == .07
    assert changed['ratio'] == 0 and changed['snippets'] == []


def test_legacy_material_description_waits_for_real_reference(env):
    app, admin, users, ip, team = env
    body = '只有作品描述不宜当作逐字稿'
    material = admin.post('/api/records/materials', json={
        'ip_id': ip['id'], 'title': '作品描述', 'body': body,
    }).json()
    saved = admin.post(f"/api/materials/{material['id']}/adopt", json={
        'skill': 'viral-rewrite-writer',
    }).json()
    endpoint = '/api/drafts/checks'
    source = {'draft_id': saved['id'], 'version': 1, 'body': body}
    report = admin.post(endpoint, json=source).json()['checks'][-1]
    assert report['state'] == 'missing_reference'
    assert report['ratio'] is None and report['blocking']
    report = admin.post(endpoint, json={**source, 'reference': '这是已核实的来源逐字稿'}).json()['checks'][-1]
    assert report['state'] == 'compared' and report['ratio'] == 0


def test_review_does_not_expose_reference_from_another_members_material(env):
    from backend.app.db import Record

    app, admin, users, ip, team = env
    private_reference = '只有有权访问作者才能看到的逐字稿'
    material = admin.post('/api/records/materials', json={'title': '私有素材'}).json()
    with app.state.Session.begin() as db:
        row = Record(kind='drafts', ip_id=ip['id'], payload={
            'title': '团队稿', 'body': private_reference, 'material_id': material['id'],
            'reference': private_reference, 'transcript_confirmed': True,
            'fact_status': 'none', 'skill': 'viral-rewrite-writer', 'version': 1,
        })
        db.add(row)
        db.flush()
        draft_id = row.id
    endpoint = '/api/drafts/' + draft_id
    member = users['member']
    assert admin.get(endpoint).json()['reference'] == private_reference
    visible = member.get(endpoint).json()
    assert 'reference' not in visible and visible['reference_restricted']
    report = visible['checks'][-1]
    assert report['state'] == 'restricted' and report['snippets'] == [] and report['ratio'] is None
    assert not report.get('matched_sentences')
    edits = {'version': 1, 'reference': '伪造来源'}
    assert member.post('/api/drafts/checks', json={**edits, 'draft_id': draft_id}).status_code == 403
    assert member.patch(endpoint, json=edits).status_code == 403
    assert member.patch(endpoint, json={'version': 1, 'body': '修订团队稿件'}).status_code == 200
    assert admin.get(endpoint).json()['reference'] == private_reference


def test_settings_reject_invalid_configuration_types(env):
    app, admin, users, ip, team = env
    assert admin.put('/api/settings', json={'model': {'enabled': 'false'}}).status_code == 422
    assert admin.put('/api/settings', json={'model': {'base_url': 'https://example.org/v1?api_key=plaintext'}}).status_code == 422
    assert admin.put('/api/settings', json={'collection': {'daily_budget': -1}}).status_code == 422


def test_worker_wont_send_outdated_knowledge(env):
    from backend.app.worker import run_once
    app, admin, users, ip, team = env
    doc = admin.post('/api/knowledge/confirm', json={'batch_id': upload(admin, ip['id']).json()['id']}).json()['items'][0]
    admin.patch('/api/knowledge/'+doc['id'], json={'status': 'verified'})
    admin.put('/api/settings', json={'model': {'base_url': 'http://127.0.0.1:1/v1', 'model': 'test', 'api_key': 'test-key', 'enabled': True}})
    r = users['member'].post('/api/writing/generate', json={'ip_id': ip['id'], 'topic': 'weekly consulting', 'skill': 'original-video-writer'})
    assert r.status_code == 200
    admin.patch('/api/knowledge/'+doc['id'], json={'status': 'archived'})
    assert run_once(app)
    job = users['member'].get('/api/jobs').json()[0]
    assert job['status'] == 'failed'
    assert '知识' in job['error'] and job['attempts'] == 0


def test_schedule_cannot_publish_after_source_expiration(env):
    app, admin, users, ip, team = env
    batch = upload(admin, ip['id']).json()
    doc = admin.post('/api/knowledge/confirm', json={'batch_id': batch['id']}).json()['items'][0]
    admin.patch('/api/knowledge/'+doc['id'], json={'status': 'verified'})
    draft = admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '服务范围', 'body': '我们先确认你的实际需求。', 'source_ids': [doc['id']]}).json()
    admin.post('/api/drafts/'+draft['id']+'/approve', json={'version': 1})
    asset = admin.post('/api/assets', data={'draft_id': draft['id']}, files={'file': ('a.mp4', b'\x00\x00\x00\x18ftypmp42' + b'0'*32, 'video/mp4')})
    assert asset.status_code == 200
    sched = admin.post('/api/schedules', json={'draft_id': draft['id'], 'platform': '抖音', 'account': '自有账号', 'planned_at': '2026-11-20T10:00:00+08:00'}).json()
    from backend.app.db import Record
    with app.state.Session() as db:
        row = db.get(Record, doc['id'])
        row.payload = {**row.payload, 'valid_until': '2020-01-01'}
        db.commit()
    r = admin.patch('/api/schedules/'+sched['id'], json={'status': 'published', 'evidence': 'https://example.org/post/1'})
    assert r.status_code == 409


def test_local_backup_and_admin_recovery(env, tmp_path):
    from backend.app.manage import backup, reset_password
    app, admin, users, ip, team = env
    admin.put('/api/settings', json={'model': {'api_key': 'persist-private-key'}})
    snap = backup(app, tmp_path.parent / (tmp_path.name + '-backup'))
    recovered = TestClient(create_app(snap))
    assert recovered.post('/api/auth/login', json={'username': 'admin', 'password': 'Admin-123456!'}).status_code == 200
    assert recovered.get('/api/settings').json()['model']['api_key_configured']
    reset_password(app, 'admin', 'Recovered-123456!')
    assert admin.get('/api/auth/me').status_code == 401
    assert admin.post('/api/auth/login', json={'username': 'admin', 'password': 'Recovered-123456!'}).status_code == 200
    with pytest.raises(ValueError):
        backup(app, app.state.data_dir / 'nested-backup')


def test_dashboard_and_reports_follow_ip_filter(env):
    app, admin, users, ip, team = env
    ip2 = admin.post('/api/ips', json={'name': '第二IP', 'team_id': team['id']}).json()
    admin.post('/api/records/leads', json={'ip_id': ip['id'], 'title': '咨询客户'})
    admin.post('/api/records/leads', json={'ip_id': ip2['id'], 'title': '其他客户'})
    assert admin.get('/api/dashboard', params={'ip_id': ip['id']}).json()['leads'] == 1
    admin.post('/api/metrics/import', data={'ip_id': ip['id'], 'platform': '抖音'}, files={'file': ('one.csv', b'date,work_id,views\n2026-11-15,a,100', 'text/csv')})
    admin.post('/api/metrics/import', data={'ip_id': ip2['id'], 'platform': '抖音'}, files={'file': ('two.csv', b'date,work_id,views\n2026-11-15,b,200', 'text/csv')})
    assert admin.get('/api/reports', params={'ip_id': ip['id']}).json()['views'] == 100
    assert users['member'].get('/api/dashboard', params={'ip_id': ip2['id']}).status_code == 403


def test_metric_import_preview_has_no_side_effects(env):
    app, admin, users, ip, team = env
    data = b'date,work_id,views\n2026-11-15,v1,100\n2026-11-15,,bad\n'
    r = admin.post('/api/metrics/import', data={'ip_id': ip['id'], 'platform': '抖音', 'preview': 'true'}, files={'file': ('report.csv', data, 'text/csv')})
    assert r.status_code == 200
    assert r.json()['preview'] is True
    assert len(r.json()['items']) == 1 and len(r.json()['errors']) == 1
    assert admin.get('/api/records/metrics').json() == []


def test_directory_upload_reports_ignored_non_knowledge_files(env):
    app, admin, users, ip, team = env
    r = admin.post('/api/knowledge/preview', data={'ip_id': ip['id'], 'category_id': 'methods-service'}, files=[
        ('files', ('我的知识库/产品/服务.md', '服务说明。'.encode(), 'text/markdown')),
        ('files', ('我的知识库/.DS_Store', b'metadata', 'application/octet-stream')),
        ('files', ('我的知识库/图片/封面.png', b'image', 'image/png')),
        ('files', ('我的知识库/同步.ps1', b'Write-Output unsafe', 'text/plain')),
    ])
    assert r.status_code == 200, r.text
    assert len(r.json()['items']) == 1
    assert len(r.json()['ignored']) == 3


def test_fresh_workspace_has_default_admin_without_initialization(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.get('/api/bootstrap').status_code == 404
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    assert response.status_code == 200
    assert response.json()['role'] == 'admin'
    assert len(client.get('/api/users').json()) == 1


def test_existing_admin_password_is_not_reset_on_restart(tmp_path):
    from backend.app.manage import reset_password
    app = create_app(tmp_path)
    reset_password(app, 'admin', 'Changed-123456!')
    client = TestClient(create_app(tmp_path))
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'Changed-123456!'}).status_code == 200
    assert len(client.get('/api/users').json()) == 1
