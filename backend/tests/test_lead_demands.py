from sqlalchemy import select

from backend.app.db import Record, User
from backend.app.worker import run_once
from backend.tests.test_demands import lead
from backend.tests.test_writer_profiles import env


def test_draft_uses_lead_as_demand_evidence_without_changing_followup(env):
    _, client, ip, _ = env
    source = lead(
        client, ip['id'], '请问这个咨询多少钱？',
        contactable=False, platform='抖音', platform_comment_id='source-comment',
        source='https://www.douyin.com/video/1',
    )
    client.patch(f"/api/records/leads/{source['id']}", json={'status': 'following'})

    response = client.post('/api/drafts', json={
        'ip_id': ip['id'], 'title': '咨询费用如何确认', 'need_id': source['id'],
    })

    assert response.status_code == 200, response.text
    snapshot = response.json()['demand_snapshot']
    assert snapshot['source_kind'] == 'leads'
    assert snapshot['evidence_ids'] == [source['id']] and snapshot['count'] == 1
    quote, = snapshot['quotes']
    assert quote['body'] == source['body']
    assert quote['platform_comment_id'] == source['platform_comment_id']
    assert quote['source'] == source['source']
    assert len(snapshot['evidence_hash']) == 64
    assert client.get('/api/records/leads').json()[0]['status'] == 'following'
    assert client.get('/api/records/needs').json() == []
    preview = client.post('/api/writing/context', json={
        'ip_id': ip['id'], 'topic': '怎样确认费用', 'need_id': source['id'],
    })
    assert preview.status_code == 200, preview.text
    assert source['body'] in preview.json()['prompt']


def test_lead_demand_rejects_unrelated_comments_and_other_ips(env):
    _, client, ip, _ = env
    unrelated = lead(client, ip['id'], '😂😂', contactable=False)
    rejected = client.post('/api/drafts', json={
        'ip_id': ip['id'], 'title': '无关评论', 'need_id': unrelated['id'],
    })
    assert rejected.status_code == 409, rejected.text

    source = lead(client, ip['id'], '请问这个咨询多少钱？', contactable=False)
    other = client.post('/api/ips', json={'name': '另一目标IP', 'team_id': ip['team_id']}).json()
    rejected = client.post('/api/drafts', json={
        'ip_id': other['id'], 'title': '其他IP稿件', 'need_id': source['id'],
    })
    assert rejected.status_code == 422, rejected.text


def test_unassigned_lead_demand_preserves_source_access_in_other_ip_drafts(env):
    app, client, ip, users = env
    with app.state.Session.begin() as db:
        admin = db.scalar(select(User).where(User.username == 'admin'))
        lead_row = Record(kind='leads', owner_id=admin.id, payload={
            'title': '仅管理员可见评论', 'body': '请问这个咨询多少钱？', 'contactable': False,
        })
        db.add(lead_row)
        db.flush()
        lead_id = lead_row.id
    data = {'ip_id': ip['id'], 'title': '咨询选题', 'need_id': lead_id}
    assert users['member'].post('/api/drafts', json=data).status_code == 403
    response = client.post('/api/drafts', json=data)
    assert response.status_code == 200, response.text
    draft_id = response.json()['id']

    visible = users['member'].get(f'/api/drafts/{draft_id}')

    assert visible.status_code == 200
    snapshot = visible.json()['demand_snapshot']
    assert snapshot['source_kind'] == 'leads' and snapshot['evidence_restricted'] is True
    assert '仅管理员可见评论' not in visible.text
    assert '请问这个咨询多少钱？' not in visible.text


def test_lead_demand_change_invalidates_queued_writer_job(env):
    app, client, ip, _ = env
    source = lead(client, ip['id'], '请问这个咨询多少钱？', contactable=False)
    client.put('/api/settings', json={'model': {
        'base_url': 'http://127.0.0.1:1/v1', 'model': 'controlled',
        'api_key': 'test-key', 'enabled': True,
    }})
    response = client.post('/api/writing/generate', json={
        'ip_id': ip['id'], 'topic': '解释咨询费用', 'need_id': source['id'],
    })
    assert response.status_code == 200, response.text
    job_id = response.json()['id']
    client.patch(f"/api/records/leads/{source['id']}", json={'body': '请问你家咨询费用怎么算？'})

    assert run_once(app)

    job = next(row for row in client.get('/api/jobs').json() if row['id'] == job_id)
    assert job['status'] == 'failed' and '需求证据已失效' in job['error']
