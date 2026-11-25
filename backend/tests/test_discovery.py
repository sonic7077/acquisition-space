"""Discovery rules are tested with local records and mocked paid integrations only."""
from datetime import datetime, timezone
import math
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db import Record, now


@pytest.fixture()
def env(tmp_path):
    app = create_app(tmp_path)
    c = TestClient(app)
    c.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    team = c.post('/api/teams', json={'name': '规则组'}).json()
    ip = c.post('/api/ips', json={'name': '规则IP', 'team_id': team['id']}).json()
    return app, c, team, ip


def member(env, name, role='member'):
    app, c, team, ip = env
    c.post('/api/users', json={'username': name, 'password': 'Member-123456!', 'name': name, 'role': role, 'team_id': team['id'], 'ip_ids': [ip['id']]})
    user = TestClient(app)
    assert user.post('/api/auth/login', json={'username': name, 'password': 'Member-123456!'}).status_code == 200
    return user


def test_scoring_settings_are_admin_only_versioned_and_public_safe(env):
    app, c, _, _ = env
    u = member(env, 'member')
    public = u.get('/api/discovery/rules')
    assert public.status_code == 200
    assert public.json()['denom_floor'] == 100
    assert u.put('/api/settings', json={'discovery': {'min_mult': 5}}).status_code == 403
    assert u.get('/api/settings').status_code == 403
    r = c.put('/api/settings', json={'discovery': {'min_mult': 5}})
    assert r.status_code == 200, r.text
    assert r.json()['discovery']['version'] == 2
    assert u.get('/api/discovery/rules').json()['min_mult'] == 5
    assert 'encrypted' not in u.get('/api/discovery/rules').text
    assert c.put('/api/settings', json={'discovery': {'min_mult': -1}}).status_code == 422
    assert c.put('/api/settings', json={'discovery': {'stale_days': 1.5}}).status_code == 422
    assert c.put('/api/settings', json={'discovery': {'denom_floor': True}}).status_code == 422
    assert c.put('/api/settings', json={'discovery': {'traffic_mid': .9, 'traffic_high': .1}}).status_code == 422
    assert u.get('/api/discovery/rules').json()['version'] == 2


def test_score_unknown_zero_baseline_and_future_dates():
    from backend.app.discovery import score_material, DEFAULT_RULES
    at = datetime(2026, 11, 19, tzinfo=timezone.utc)
    p = {'likes': 100, 'comments_count': 30, 'published_at': at.timestamp() + 10, 'author_sample_count': 3, 'author_sample_median_likes': 0}
    result = score_material(p, {'followers': 0}, DEFAULT_RULES, at)
    assert result['multiplier'] is None and result['low_fan_ratio'] is None
    assert result['rising'] is False and result['freshness'] == 'future'
    assert result['recommended'] is False
    missing = score_material({}, None, DEFAULT_RULES, at)
    assert missing['comment_ratio'] is None and missing['like_level'] == '未知'
    assert missing['freshness'] == 'unknown'
    assert missing['reasons'] == [] and not missing['recommended']
    assert score_material({'likes': True}, None, DEFAULT_RULES, at)['like_level'] == '未知'


def test_score_boundaries_reasons_and_disabled_author():
    from backend.app.discovery import score_material, DEFAULT_RULES
    at = datetime(2026, 11, 19, tzinfo=timezone.utc)
    base = {'likes': 300, 'comments_count': 20, 'published_at': at.timestamp() - 86400, 'author_sample_count': 3, 'author_sample_median_likes': 10}
    s = score_material(base, {'followers': 100}, DEFAULT_RULES, at)
    assert s['denominator'] == 100 and s['multiplier'] == 3
    assert s['like_level'] == '低赞' and not s['low_fan_hit']
    assert s['high_comment_ratio'] and s['rising'] and s['recommended']
    assert len(s['reasons']) == 3 and math.isfinite(s['reference_score'])
    for author in ({'enabled': False}, {'is_own': True}):
        s = score_material(base, author, DEFAULT_RULES, at)
        assert not s['recommended'] and s['excluded_reason']
    stale = score_material({**base, 'published_at': at.timestamp() - 181*86400}, None, DEFAULT_RULES, at)
    assert not stale['recommended'] and stale['freshness'] == 'stale'
    zero = score_material({'likes': 0, 'comments_count': 20}, None, DEFAULT_RULES, at)
    assert zero['comment_ratio'] is None
    assert not score_material({**base, 'comments_count': 19, 'likes': 100}, None, DEFAULT_RULES, at)['high_comment_ratio']
    low = score_material({**base, 'likes': 501}, {'followers': 100}, DEFAULT_RULES, at)
    assert low['low_fan_hit']


def test_snapshots_require_ordered_time_and_bound_history(env):
    from backend.app.integrations.collection import upsert
    from backend.app.discovery import score_material, DEFAULT_RULES
    app, _, _, ip = env
    with app.state.Session() as db:
        for n in range(35):
            row = upsert(db, 'materials', ip['id'], 'owner', 'v', {'platform': '抖音', 'likes': n*100, 'comments_count': 1, 'collected_at': f'2026-11-19T00:{n:02d}:00+00:00'})
        assert len(row.payload['interaction_snapshots']) == 30
        score = score_material(row.payload, None, {**DEFAULT_RULES, 'rise_delta': 100})
        assert score['likes_delta'] == 100 and score['observation_seconds'] == 60
        upsert(db, 'materials', ip['id'], 'owner', 'v', {'platform': '抖音', 'keyword': '另一个词'})
        assert len(row.payload['interaction_snapshots']) == 30
        upsert(db, 'materials', ip['id'], 'owner', 'v', {'platform': '抖音', 'likes': 9999, 'collected_at': '2026-11-19T00:01:00+00:00'})
        assert row.payload['likes'] == 3400
        assert row.payload['interaction_snapshots'][-1]['likes'] == 3400
        db.commit()


def test_author_management_preserved_on_refresh_and_visible_counts(env):
    from backend.app.integrations.collection import upsert
    app, c, _, ip = env
    with app.state.Session() as db:
        a = upsert(db, 'authors', ip['id'], 'owner', 'u', {'platform': '抖音', 'title': '博主', 'keyword': '咨询', 'collected_at': now()})
        aid = a.id
        for n in range(3):
            upsert(db, 'materials', ip['id'], 'owner', str(n), {'platform': '抖音', 'author_id': aid, 'likes': (n+1)*100, 'keyword': '咨询'})
        db.commit()
    assert c.patch('/api/records/authors/'+aid, json={'enabled': False, 'is_own': True, 'tier': '跨界结构', 'note': '人工备注'}).status_code == 200
    assert c.patch('/api/records/authors/'+aid, json={'enabled': 'false'}).status_code == 422
    assert c.patch('/api/records/authors/'+aid, json={'tier': '随便'}).status_code == 422
    with app.state.Session() as db:
        a = upsert(db, 'authors', ip['id'], 'owner', 'u', {'platform': '抖音', 'title': '更新名', 'keyword': '服务', 'collected_at': now(), 'enabled': True, 'tier': '未分层'})
        db.commit()
    author = c.get('/api/records/authors').json()[0]
    assert author['enabled'] is False and author['is_own'] is True and author['tier'] == '跨界结构'
    assert author['note'] == '人工备注' and author['keywords'] == ['咨询', '服务']
    assert author['work_count'] == 3 and author['sample_median_likes'] == 200
    assert author['first_discovered_at'] <= author['last_discovered_at']
    work = c.get('/api/records/materials').json()[0]
    assert work['discovery']['multiplier'] is not None
    assert not work['discovery']['recommended']


def test_private_search_plans_and_keyword_tokenization(env):
    _, c, _, ip = env
    a, b = member(env, 'alice'), member(env, 'bob')
    saved = a.post('/api/discovery/searches', json={'name': '客户问题', 'options': {'keywords': 'AI tools、咨询，服务', 'ip_id': ip['id'], 'per_word_limit': 2}})
    assert saved.status_code == 200, saved.text
    assert saved.json()['options']['keywords_list'] == ['AI tools', '咨询', '服务']
    assert len(a.get('/api/discovery/searches').json()) == 1
    assert b.get('/api/discovery/searches').json() == [] and c.get('/api/discovery/searches').json() == []
    assert b.delete('/api/discovery/searches/'+saved.json()['id']).status_code in (403, 404)
    assert a.post('/api/discovery/searches', json={'name': '非法', 'options': {'keywords': '服务', 'ip_id': 'missing'}}).status_code == 403
    assert a.delete('/api/discovery/searches/'+saved.json()['id']).status_code == 200
    assert a.get('/api/discovery/searches').json() == []
    assert a.post('/api/discovery/searches', json={'name': '空', 'options': {'keywords': ' 、, '}}).status_code == 422


def test_per_word_limit_collects_other_keywords_and_preserves_author_matches(env):
    from backend.app.integrations.collection import run_collection_job
    from backend.tests.test_collection import video
    app, c, _, ip = env
    c.put('/api/settings', json={'collection': {'enabled': True, 'api_key': 'fake-key', 'daily_budget': 50}})
    def transport(req):
        if 'comments' in req.url.path:
            data = {'comments': [{'cid': 'c-'+req.url.params['aweme_id'], 'text': '多少钱', 'reply_comment_total': 2, 'user': {'uid': 'reader'}}], 'has_more': 0}
        else:
            word = __import__('json').loads(req.content)['keyword']
            data = {'data': [{'aweme_info': video(word+str(n))} for n in range(3)], 'has_more': 1, 'cursor': 10}
        return httpx.Response(200, json={'code': 200, 'data': data})
    app.state.integration_transport = httpx.MockTransport(transport)
    r = c.post('/api/collection/search', json={'ip_id': ip['id'], 'platforms': ['douyin'], 'keywords': '咨询、服务', 'per_word_limit': 1, 'max_videos': 3, 'pages': 2})
    assert r.status_code == 200, r.text
    run_collection_job(app, r.json()['id'])
    mats = c.get('/api/records/materials').json()
    assert len(mats) == 2
    assert c.get('/api/records/authors').json()[0]['keywords'] == ['咨询', '服务']
    lead = c.get('/api/records/leads').json()[0]
    assert lead['comment_scope'] == 'first_level' and lead['reply_count'] == 2
    run = c.get('/api/collection/runs').json()[0]
    assert run['calls'] == 4 and run['status'] == 'completed', run


def test_feishu_maps_comment_evidence_fields_without_new_remote_schema(env):
    from backend.tests.test_collection import FeishuMock
    from backend.app.integrations.feishu import Feishu
    app, c, _, _ = env
    c.put('/api/settings', json={'feishu': {'app_id': 'fake', 'app_secret': 'fake'}})
    mock = FeishuMock()
    for i, name in enumerate(['评论类型', '判定依据', '规则版本', '人工覆盖', '负责人', '采集时间', '评论用户ID', '评论范围', '回复数']):
        mock.fields['tbl-comments'].append({'field_id': 'c-'+str(i), 'field_name': name, 'type': 2 if name == '回复数' else 1})
    app.state.integration_transport = httpx.MockTransport(mock)
    fs = Feishu(app)
    try:
        table = fs.schema()['tables']['comments']
        assert 'content_type' in table['mapping'] and 'analysis_basis' in table['mapping']
        row = Record(id='evidence', kind='leads', payload={'content_type': '追问', 'analysis_basis': '问价格', 'analysis_version': 'v1', 'manual_labels': ['intent'], 'owner': '队员', 'collected_at': now(), 'commenter_id': 'reader', 'comment_scope': 'first_level', 'reply_count': 2})
        fields = fs.fields(table, row, {})
        assert fields['评论类型'] == '追问' and fields['回复数'] == 2
        assert fields['评论用户ID'] == 'reader'
    finally:
        fs.http.close()


def test_visible_samples_exclude_other_owners_and_platforms(env):
    from backend.app.db import User
    from sqlalchemy import select
    app, c, _, _ = env
    a = member(env, 'scope-a')
    b = member(env, 'scope-b')
    author = a.post('/api/records/authors', json={'title': '作者', 'platform': '抖音'}).json()
    for n in range(2):
        a.post('/api/records/materials', json={'title': '自己的'+str(n), 'platform': '抖音', 'author_id': author['id'], 'likes': 20})
    b.post('/api/records/materials', json={'title': '别人的', 'platform': '抖音', 'author_id': author['id'], 'likes': 20000})
    a.post('/api/records/materials', json={'title': '其他平台', 'platform': '小红书', 'author_id': author['id'], 'likes': 10000})
    for client in (a, c):
        own_author = next(r for r in client.get('/api/records/authors').json() if r['id'] == author['id'])
        assert own_author['sample_count'] == 2 and own_author['sample_median_likes'] is None
    assert all(m['discovery']['multiplier'] is None for m in a.get('/api/records/materials').json())


def test_material_cannot_borrow_baseline_from_mismatched_author_scope(env):
    app, c, _, ip = env
    a, b = member(env, 'baseline-owner'), member(env, 'other-owner')
    author = a.post('/api/records/authors', json={'title': '基线作者', 'platform': '抖音'}).json()
    for n in range(3):
        a.post('/api/records/materials', json={'title': '样本'+str(n), 'platform': '抖音', 'author_id': author['id'], 'likes': 100})
    b.post('/api/records/materials', json={'title': '跨所有者', 'platform': '抖音', 'author_id': author['id'], 'likes': 1000})
    a.post('/api/records/materials', json={'title': '跨平台', 'platform': '小红书', 'author_id': author['id'], 'likes': 1000})
    c.post('/api/records/materials', json={'ip_id': ip['id'], 'title': '跨IP', 'platform': '抖音', 'author_id': author['id'], 'likes': 1000})
    for m in c.get('/api/records/materials').json():
        if m['title'].startswith('跨'):
            assert m['discovery']['multiplier'] is None


def test_feishu_comment_collection_time_supports_date_fields(env):
    from backend.tests.test_collection import FeishuMock
    from backend.app.integrations.feishu import Feishu
    app, c, _, _ = env
    c.put('/api/settings', json={'feishu': {'app_id': 'fake', 'app_secret': 'fake'}})
    mock = FeishuMock()
    mock.fields['tbl-comments'].append({'field_id': 'date', 'field_name': '采集时间', 'type': 5})
    app.state.integration_transport = httpx.MockTransport(mock)
    fs = Feishu(app)
    try:
        table = fs.schema()['tables']['comments']
        row = Record(id='dated-comment', kind='leads', payload={'collected_at': '2026-11-19T00:00:00+00:00'})
        assert fs.fields(table, row, {})['采集时间'] == 1795046400000
    finally:
        fs.http.close()


def test_candidate_pool_excludes_adopted_but_keeps_recommendation():
    from backend.app.discovery import score_material, DEFAULT_RULES
    at = datetime(2026, 11, 19, tzinfo=timezone.utc)
    p = {'likes': 600, 'comments_count': 30, 'published_at': at.timestamp()-86400, 'author_sample_count': 3, 'author_sample_median_likes': 100}
    assert score_material(p, {}, DEFAULT_RULES, at)['candidate']
    adopted = score_material({**p, 'status': 'adopted'}, {}, DEFAULT_RULES, at)
    assert adopted['recommended'] and not adopted['candidate']
    assert not score_material({**p, 'status': 'excluded'}, {}, DEFAULT_RULES, at)['candidate']


def test_retired_search_limits_do_not_disqualify_historical_materials(env):
    _, client, _, _ = env
    published_at = datetime.now(timezone.utc).timestamp() - 3600
    for platform, duration in [('抖音', 2), ('小红书', None)]:
        response = client.post('/api/records/materials', json={
            'title': platform, 'platform': platform, 'eligible': False,
            'likes': 50, 'duration': duration, 'comments_count': 20, 'published_at': published_at,
        })
        assert response.status_code == 200, response.text

    def scores():
        return [record['discovery'] for record in client.get('/api/records/materials').json()]

    initial = scores()
    assert len(initial) == 2
    assert all(s['available'] and s['recommended'] and s['candidate'] for s in initial)
    assert all(s['reasons'] == ['高评赞比'] for s in initial)

    updated = client.put('/api/settings', json={'discovery': {'min_comments': 21}})
    assert updated.status_code == 200
    assert all(s['available'] and not s['recommended'] and not s['candidate'] for s in scores())


@pytest.mark.parametrize(('changes', 'available', 'recommended', 'candidate'), [
    ({'unavailable': True}, False, False, False),
    ({'status': 'excluded'}, False, False, False),
    ({'status': 'adopted'}, False, True, False),
    ({'adopted': True}, False, True, False),
])
def test_material_availability_and_candidate_respect_source_and_local_status(changes, available, recommended, candidate):
    from backend.app.discovery import score_material, DEFAULT_RULES
    at = datetime(2026, 11, 19, tzinfo=timezone.utc)
    material = {'likes': 100, 'comments_count': 20, 'published_at': at.timestamp() - 86400}
    score = score_material({**material, **changes}, {}, DEFAULT_RULES, at)
    assert (score['available'], score['recommended'], score['candidate']) == (available, recommended, candidate)
