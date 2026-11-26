import json
from uuid import UUID

import httpx
import pytest

from backend.app.db import Record
from backend.app.integrations import feishu
from backend.tests.test_collection import FeishuMock, add_sync_data, setup


TEXT = '第一段是逐字稿。\n第二段保持原换行。'


class TranscriptMock(FeishuMock):
    def __init__(self, *, field_type=1):
        super().__init__()
        self.paths = []
        self.create_tokens = []
        self.fields['tbl-works'].append({'field_id': 'f-transcript', 'field_name': '逐字稿', 'type': field_type})

    def __call__(self, req):
        self.paths.append((req.method, req.url.path))
        if req.url.path.endswith('/tbl-works/records') and req.method == 'POST':
            self.create_tokens.append(req.url.params.get('client_token'))
        if '/tbl-works/records/' in req.url.path and req.method == 'PUT':
            key = req.url.path.rsplit('/', 1)[1]
            fields = json.loads(req.content)['fields']
            self.rows['tbl-works'][key].update(fields)
            self.writes.append(('tbl-works', fields))
            return httpx.Response(200, json={'code': 0, 'data': {'record': {'record_id': key}}})
        return super().__call__(req)


@pytest.fixture()
def work(setup):
    app, client, ip, _ = setup
    response = client.put('/api/settings', json={'feishu': {
        'app_id': 'fake-app', 'app_secret': 'fake-secret', 'enabled': True,
    }})
    assert response.status_code == 200
    add_sync_data(app, ip['id'])
    with app.state.Session() as db:
        material = db.get(Record, 'local-work')
        material.payload = {**material.payload, 'feishu_table_id': 'tbl-works', 'feishu_record_id': 'rec-own'}
        db.commit()
    return app, client, material


def transport(app, mock):
    app.state.integration_transport = httpx.MockTransport(mock)
    return mock


def test_writes_only_transcript_of_unique_work_even_if_stored_record_id_is_stale(work):
    app, _, material = work
    remote = transport(app, TranscriptMock())
    remote.rows['tbl-works'] = {
        'rec-own': {'获客空间ID': 'different-id', '标题': '同名作品', '逐字稿': '另一篇作品原话'},
        'rec-right': {'获客空间ID': material.id, '标题': '同名作品', '逐字稿': None, '推荐': 1},
    }
    result = feishu.sync_work_transcript(app, material, TEXT)
    assert result['status'] == 'synced'
    assert remote.writes == [('tbl-works', {'逐字稿': TEXT})]
    assert remote.rows['tbl-works']['rec-right'] == {
        '获客空间ID': material.id, '标题': '同名作品', '逐字稿': TEXT, '推荐': 1,
    }
    assert remote.rows['tbl-works']['rec-own']['逐字稿'] == '另一篇作品原话'
    assert all('/tbl-authors/' not in path and '/tbl-comments/' not in path for _, path in remote.paths)
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'synced'
    assert len(remote.writes) == 1


@pytest.mark.parametrize('remote_text', ['人工修订正文', [{'type': 'text', 'text': '人工修订正文'}], [{'type': 'mention'}]])
def test_protects_existing_different_transcript_without_disclosing_it(work, remote_text):
    app, _, material = work
    remote = transport(app, TranscriptMock())
    remote.rows['tbl-works']['rec-own'] = {'获客空间ID': material.id, '逐字稿': remote_text}
    result = feishu.sync_work_transcript(app, material, TEXT)
    assert result['status'] == 'conflict'
    assert '人工修订正文' not in result['message']
    assert remote.writes == []


def test_handles_missing_work_field_or_conflicting_identity_without_writes(work):
    app, _, material = work
    remote = transport(app, TranscriptMock(field_type=20))
    remote.rows['tbl-works']['rec-own'] = {'获客空间ID': material.id}
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'failed'
    remote = transport(app, TranscriptMock())
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'conflict'
    remote.rows['tbl-works'] = {
        'rec-own': {'获客空间ID': material.id},
        'rec-duplicate': {'获客空间ID': material.id},
    }
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'conflict'
    assert remote.writes == []


def test_reads_explicit_custom_transcript_mapping_and_preserves_in_full_sync(work):
    app, client, material = work
    response = client.put('/api/settings', json={'feishu': {'field_mapping': json.dumps({
        'works': {'transcript': 'f-transcript', 'body': 'f-transcript'},
    })}})
    assert response.status_code == 200
    remote = transport(app, TranscriptMock())
    remote.fields['tbl-works'][-1]['field_name'] = '口播转写'
    remote.rows['tbl-works']['rec-own'] = {'获客空间ID': material.id}
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'synced'
    assert remote.writes == [('tbl-works', {'口播转写': TEXT})]
    api = feishu.Feishu(app)
    try:
        table = api.schema()['tables']['works']
        material.payload = {**material.payload, 'transcript': TEXT}
        fields = api.fields(table, material, {})
        assert '口播转写' not in fields
        assert 'transcript' not in json.loads(fields['内容结构'])
    finally:
        api.http.close()


def test_creates_single_untouched_work_with_transcript_if_new_to_table(work):
    app, _, _ = work
    with app.state.Session() as db:
        material = db.get(Record, 'local-work')
        material.payload = {k: v for k, v in material.payload.items() if not k.startswith('feishu_')}
        author = db.get(Record, 'local-author')
        author.payload = {**author.payload, 'feishu_record_id': 'rec-author', 'feishu_table_id': 'tbl-authors'}
        db.commit()
    remote = transport(app, TranscriptMock())
    remote.fields['tbl-works'].append({'field_id': 'author', 'field_name': '博主', 'type': 18,
                                       'property': {'table_id': 'tbl-authors'}})
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'synced'
    assert len(remote.rows['tbl-works']) == 1
    row_id, row = next(iter(remote.rows['tbl-works'].items()))
    assert row['标题'] == material.payload['title']
    assert row['获客空间ID'] == material.id
    assert row['逐字稿'] == TEXT
    assert row['博主'] == ['rec-author']
    with app.state.Session() as db:
        stored = db.get(Record, material.id).payload
        assert stored['feishu_record_id'] == row_id
        assert stored['feishu_snapshot']['标题'] == material.payload['title']
        assert '逐字稿' not in stored['feishu_snapshot']
    assert remote.rows['tbl-authors'] == {}
    assert remote.rows['tbl-comments'] == {}


def test_disabled_config_and_settings_change_before_write_are_safe(work):
    app, client, material = work
    remote = transport(app, TranscriptMock())
    client.put('/api/settings', json={'feishu': {'enabled': False}})
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'skipped'
    assert remote.paths == []
    client.put('/api/settings', json={'feishu': {'enabled': True}})
    remote.rows['tbl-works']['rec-own'] = {'获客空间ID': material.id}

    def transport_with_change(req):
        response = remote(req)
        if req.url.path.endswith('/records/search'):
            client.put('/api/settings', json={'feishu': {'enabled': False}})
        return response

    app.state.integration_transport = httpx.MockTransport(transport_with_change)
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'failed'
    assert remote.writes == []


def test_normal_sync_keeps_human_transcript_after_first_transcript_sync(setup):
    app, client, ip, _ = setup
    client.put('/api/settings', json={'feishu': {'app_id': 'fake-app', 'app_secret': 'fake-secret', 'enabled': True}})
    add_sync_data(app, ip['id'])
    remote = transport(app, TranscriptMock())
    remote.fields['tbl-works'].append({'field_id': 'author', 'field_name': '博主', 'type': 18,
                                       'property': {'table_id': 'tbl-authors'}})
    with app.state.Session() as db:
        material = db.get(Record, 'local-work')
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'synced'
    record = next(iter(remote.rows['tbl-works'].values()))
    assert '博主' not in record
    record['逐字稿'] = '运营人员确认后修订的逐字稿'
    response = client.post('/api/integrations/feishu/sync', json={'ip_id': ip['id']})
    assert response.status_code == 200
    feishu.run_sync_job(app, response.json()['id'])
    assert client.get('/api/collection/runs').json()[0]['status'] == 'completed'
    assert len(remote.rows['tbl-works']) == 1
    assert record['逐字稿'] == '运营人员确认后修订的逐字稿'
    assert record['博主'] == list(remote.rows['tbl-authors'])


def test_transcript_and_regular_sync_use_same_create_idempotency_key(setup):
    app, client, ip, _ = setup
    client.put('/api/settings', json={'feishu': {'app_id': 'fake-app', 'app_secret': 'fake-secret', 'enabled': True}})
    add_sync_data(app, ip['id'])
    remote = transport(app, TranscriptMock())
    with app.state.Session() as db:
        material = db.get(Record, 'local-work')
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'synced'
    ordinary = transport(app, TranscriptMock())
    api = feishu.Feishu(app)
    try:
        table = api.schema()['tables']['works']
        api.upsert(table, material, {})
    finally:
        api.http.close()
    assert remote.create_tokens == ordinary.create_tokens
    assert UUID(remote.create_tokens[0]).version == 4


@pytest.mark.parametrize('reject_duplicate', [False, True])
def test_transcript_is_written_if_regular_sync_created_the_work_first(setup, reject_duplicate):
    app, client, ip, _ = setup
    client.put('/api/settings', json={'feishu': {'app_id': 'fake-app', 'app_secret': 'fake-secret', 'enabled': True}})
    add_sync_data(app, ip['id'])
    remote = TranscriptMock()
    with app.state.Session() as db:
        material = db.get(Record, 'local-work')

    def race(req):
        if req.url.path.endswith('/tbl-works/records') and req.method == 'POST':
            fields = json.loads(req.content)['fields']
            fields.pop('逐字稿', None)
            remote.rows['tbl-works']['rec-ordinary'] = fields
            if reject_duplicate:
                return httpx.Response(200, json={'code': 1254012})
            return httpx.Response(200, json={'code': 0, 'data': {'record': {'record_id': 'rec-ordinary'}}})
        return remote(req)

    app.state.integration_transport = httpx.MockTransport(race)
    assert feishu.sync_work_transcript(app, material, TEXT)['status'] == 'synced'
    assert remote.rows['tbl-works']['rec-ordinary']['逐字稿'] == TEXT
    assert remote.writes == [('tbl-works', {'逐字稿': TEXT})]
