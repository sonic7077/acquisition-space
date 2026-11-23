"""Regression cases from the 2026-11-21 whole-system rules audit."""
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app


@pytest.fixture()
def env(tmp_path):
    app = create_app(tmp_path)
    admin = TestClient(app, raise_server_exceptions=False)
    admin.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    team = admin.post('/api/teams', json={'name': '规则核查组'}).json()
    ip = admin.post('/api/ips', json={'name': '保险核查IP', 'team_id': team['id']}).json()
    admin.post('/api/users', json={'username': 'auditor', 'password': 'Audit-123456!', 'name': '队员', 'role': 'member', 'team_id': team['id'], 'ip_ids': [ip['id']]})
    member = TestClient(app, raise_server_exceptions=False)
    member.post('/api/auth/login', json={'username': 'auditor', 'password': 'Audit-123456!'})
    return app, admin, member, ip


def verified_draft(admin, ip):
    return admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '保险说明', 'body': '这是经过人工核实的服务说明。', 'fact_status': 'verified'}).json()


def test_editing_verified_facts_requires_new_verification(env):
    _, admin, member, ip = env
    d = verified_draft(admin, ip)
    result = member.patch('/api/drafts/'+d['id'], json={'version': 1, 'body': '替换了具体服务范围及承诺内容。'})
    assert result.status_code == 200
    assert result.json()['fact_status'] == 'pending'
    assert admin.post('/api/drafts/'+d['id']+'/approve', json={'version': 2}).status_code == 409
    confirmed = admin.patch('/api/drafts/'+d['id'], json={'version': 2, 'body': '重新核实的服务内容。', 'fact_status': 'verified'})
    assert confirmed.status_code == 200 and confirmed.json()['fact_status'] == 'verified'


def test_edit_invalidates_staged_schedule_but_preserves_cancelled(env):
    _, admin, member, ip = env
    d = verified_draft(admin, ip)
    assert admin.post('/api/drafts/'+d['id']+'/approve', json={'version': 1}).status_code == 200
    assert member.post('/api/assets', data={'draft_id': d['id']}, files={'file': ('audit.mp4', b'\0\0\0\x18ftypmp42'+b'0'*32, 'video/mp4')}).status_code == 200
    rows = []
    for account in ('准备发布', '已取消'):
        s = member.post('/api/schedules', json={'draft_id': d['id'], 'platform': '抖音', 'account': account, 'planned_at': '2026-12-01T10:00:00+08:00'}).json()
        member.patch('/api/schedules/'+s['id'], json={'status': 'staged' if account=='准备发布' else 'cancelled'})
        rows.append(s)
    member.patch('/api/drafts/'+d['id'], json={'version': 1, 'body': '改过之后需要重新审稿。'})
    got = {r['id']: r for r in member.get('/api/schedules').json()}
    assert got[rows[0]['id']]['status'] == 'needs_review'
    assert got[rows[1]['id']]['status'] == 'cancelled'


@pytest.mark.parametrize('kind,patch', [
    ('materials', {'title': ['非法标题']}),
    ('materials', {'interaction_snapshots': 'broken'}),
    ('materials', {'likes': '一万'}),
    ('leads', {'body': ['非法评论']}),
    ('authors', {'followers': -10}),
    ('materials', {'published_at': 'not-a-date'}),
])
def test_invalid_record_inputs_are_rejected_without_breaking_library(env, kind, patch):
    _, _, member, ip = env
    r = member.post('/api/records/'+kind, json={'title': '异常输入', 'ip_id': ip['id'], **patch})
    assert r.status_code == 422
    assert member.get('/api/records/'+kind).status_code == 200


def test_null_draft_body_is_validation_error(env):
    _, admin, _, ip = env
    assert admin.post('/api/drafts', json={'ip_id': ip['id'], 'title': '空输入', 'body': None}).status_code == 422


def test_failed_model_raw_output_is_private_to_submitter_and_admin(env):
    from backend.app.db import Record
    _, admin, member, ip = env
    app=env[0]
    with app.state.Session() as db:
        owner=admin.get('/api/auth/me').json()['id']
        row=Record(kind='jobs',ip_id=ip['id'],owner_id=owner,payload={'status':'failed','error':'模型格式无效','raw_output':'提交者私有研究评论原文','context':{'prompt':'私有提示'}})
        db.add(row);db.commit()
    assert admin.get('/api/jobs').json()[0]['raw_output']=='提交者私有研究评论原文'
    r=member.get('/api/jobs')
    assert r.status_code==200 and '私有' not in r.text
    assert r.json()[0]['status']=='failed'


def test_knowledge_change_preserves_cancelled_and_unknown_schedule(env):
    from backend.app.db import Record
    app, admin, _, ip=env
    batch=admin.post('/api/knowledge/preview',data={'ip_id':ip['id'],'category_id':'methods-service'},files={'files':('保险服务.md','保险咨询的范围经过核实。'.encode(),'text/markdown')}).json()
    doc=admin.post('/api/knowledge/confirm',json={'batch_id':batch['id']}).json()['items'][0]
    admin.patch('/api/knowledge/'+doc['id'],json={'status':'verified'})
    d=admin.post('/api/drafts',json={'ip_id':ip['id'],'title':'来源测试','body':'根据核实资料说明服务范围。','fact_status':'verified','source_ids':[doc['id']]}).json()
    with app.state.Session() as db:
        for state in ('cancelled','unknown','staged'):
            db.add(Record(id='schedule-'+state,kind='schedules',ip_id=ip['id'],payload={'draft_id':d['id'],'status':state}))
        db.commit()
    assert admin.patch('/api/knowledge/'+doc['id'],json={'status':'archived'}).status_code==200
    rows={r['id']:r for r in admin.get('/api/schedules').json()}
    assert rows['schedule-cancelled']['status']=='cancelled'
    assert rows['schedule-unknown']['status']=='unknown'
    assert rows['schedule-staged']['status']=='needs_review'


def test_repeated_publish_evidence_update_preserves_first_publish_time(env):
    from backend.app.db import Record
    app,admin,member,ip=env
    d=verified_draft(admin,ip)
    admin.post('/api/drafts/'+d['id']+'/approve',json={'version':1})
    first='2026-11-20T10:00:00+00:00'
    with app.state.Session() as db:
        db.add(Record(id='published-audit',kind='schedules',ip_id=ip['id'],payload={'draft_id':d['id'],'version':1,'status':'published','published_at':first,'evidence':'https://example.org/first'}));db.commit()
    result=member.patch('/api/schedules/published-audit',json={'evidence':'https://example.org/confirmed'})
    assert result.status_code==200
    assert result.json()['published_at']==first


def test_returning_draft_invalidates_waiting_schedules(env):
    from backend.app.db import Record
    app,admin,_,ip=env
    d=verified_draft(admin,ip)
    admin.post('/api/drafts/'+d['id']+'/approve',json={'version':1})
    with app.state.Session() as db:
        db.add(Record(id='return-schedule',kind='schedules',ip_id=ip['id'],payload={'draft_id':d['id'],'status':'staged'}));db.commit()
    assert admin.post('/api/drafts/'+d['id']+'/return',json={'reason':'需要核对条款'}).status_code==200
    assert admin.get('/api/schedules').json()[0]['status']=='needs_review'
