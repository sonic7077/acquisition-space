import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db import Record, business_today
from backend.app.main import create_app


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    from backend.app.integrations import transcript
    monkeypatch.setattr(transcript, 'local_whisper', lambda: None)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).is_success
        team = client.post('/api/teams', json={'name': '转写测试组'}).json()
        ip = client.post('/api/ips', json={'name': '对标IP', 'team_id': team['id']}).json()
        assert client.put('/api/settings', json={
            'collection': {'enabled': True, 'api_key': 'tikhub-test-key', 'daily_budget': 1},
            'transcription': {'base_url': 'https://asr.example/v1', 'model': 'whisper-1', 'api_key': 'asr-test-key'},
        }).is_success
        yield app, client, team, ip


def add_material(client, ip, **overrides):
    return client.post('/api/records/materials', json={
        'title': '作品标题', 'body': '描述不是逐字稿', 'ip_id': ip['id'], 'platform': '抖音',
        'platform_id': '7348888888888888888', 'source': 'https://www.douyin.com/video/7348888888888888888',
        **overrides,
    }).json()


def wav_body(seconds=0.1):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * int(seconds * 16_000))
    return buf.getvalue()


def detail(url='https://v26-web.douyinvod.com/media.wav'):
    return httpx.Response(200, json={'code': 200, 'data': {'aweme_detail': {
        'video': {'play_addr_h264': {'url_list': [url]}},
    }}})


def test_long_video_uses_lower_bitrate_and_bounded_cloud_uploads(setup):
    app, client, _, ip = setup
    material = add_material(client, ip, duration=905.695)
    media, uploads = [], []

    def handler(request):
        if request.url.host == 'api.tikhub.io':
            return httpx.Response(200, json={'data': {'aweme_detail': {'video': {
                'play_addr': {'url_list': ['https://v26-web.douyinvod.com/original.wav']},
                'bit_rate': [
                    {'bit_rate': 800_000, 'play_addr': {'url_list': ['https://v26-web.douyinvod.com/high.wav']}},
                    {'bit_rate': 200_000, 'play_addr': {'url_list': ['https://v26-web.douyinvod.com/low.wav']}},
                ],
            }}}})
        if request.url.host == 'v26-web.douyinvod.com':
            media.append(request.url.path)
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body(905.695))
        assert request.url.host == 'asr.example'
        upload = request.read()
        sound_start = upload.index(b'RIFF')
        size = int.from_bytes(upload[sound_start + 4:sound_start + 8], 'little') + 8
        with wave.open(io.BytesIO(upload[sound_start:sound_start + size]), 'rb') as audio:
            uploads.append(audio.getnframes())
        return httpx.Response(200, json={'text': '第一段。' if len(uploads) == 1 else '第二段。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    response = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert response.status_code == 200, response.text
    assert response.json()['text'] == '第一段。\n第二段。'
    assert media == ['/low.wav']
    assert uploads == [600 * 16_000, int(905.695 * 16_000) - 600 * 16_000]
    too_long = add_material(client, ip, platform_id='7348888888888888889', duration=1201)
    unsupported = client.post(f'/api/materials/{too_long["id"]}/transcript?background=true')
    assert unsupported.status_code == 422
    assert '20 分钟' in unsupported.json()['detail']


def test_oversize_platform_stream_tries_smaller_alternative(setup):
    app, client, _, ip = setup
    material = add_material(client, ip)
    attempted = []

    def handler(request):
        if request.url.host == 'api.tikhub.io':
            return httpx.Response(200, json={'data': {'aweme_detail': {'video': {'play_addr': {'url_list': [
                'https://v26-web.douyinvod.com/oversize.wav', 'https://v25-web.douyinvod.com/alternate.wav',
            ]}}}}})
        if request.url.host.endswith('.douyinvod.com'):
            attempted.append(request.url.path)
            if request.url.path == '/oversize.wav':
                return httpx.Response(200, headers={'Content-Type': 'audio/wav', 'Content-Length': '200000000'})
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        return httpx.Response(200, json={'text': '备用音轨。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    response = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert response.status_code == 200, response.text
    assert attempted == ['/oversize.wav', '/alternate.wav']


def test_douyin_tries_original_stream_when_low_bitrate_has_no_decodable_audio(setup):
    app, client, _, ip = setup
    material = add_material(client, ip)
    seen = []

    def handler(request):
        if request.url.host == 'api.tikhub.io':
            seen.append('detail')
            return httpx.Response(200, json={'data': {'aweme_detail': {'video': {
                'play_addr': {'url_list': ['https://v26.douyinvod.com/original.wav']},
                'bit_rate': [{
                    'bit_rate': 200_000, 'play_addr': {'url_list': [
                        'https://v26.douyinvod.com/low.wav',
                        'https://v27.douyinvod.com/low.wav',
                        'https://v28.douyinvod.com/low.wav',
                    ]},
                }],
            }}}})
        if request.url.host.endswith('.douyinvod.com'):
            seen.append(request.url.path)
            content = wav_body() if request.url.path == '/original.wav' else b'not playable'
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=content)
        return httpx.Response(200, json={'text': '已从备用音轨转写。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    response = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert response.status_code == 200, response.text
    assert response.json()['text'] == '已从备用音轨转写。'
    assert seen == ['detail', '/low.wav', '/original.wav']


def test_uses_platform_media_and_asr_and_caches_without_rebilling(setup):
    app, client, _, ip = setup
    material = add_material(client, ip)
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'api.tikhub.io':
            assert request.url.path == '/api/v1/douyin/web/fetch_one_video'
            assert request.url.params['aweme_id'] == material['platform_id']
            assert request.headers['Authorization'] == 'Bearer tikhub-test-key'
            return detail()
        if request.url.host.endswith('.douyinvod.com'):
            assert 'Authorization' not in request.headers
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        assert request.url.path == '/v1/audio/transcriptions'
        assert request.headers['Authorization'] == 'Bearer asr-test-key'
        assert b'name="model"' in request.read() and b'whisper-1' in request.content and b'RIFF' in request.content
        # Other requests may take a write lock while the provider is responding.
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            db.rollback()
        return httpx.Response(200, json={'text': '  这是真实的口播。  '})

    app.state.integration_transport = httpx.MockTransport(handler)
    url = f'/api/materials/{material["id"]}/transcript'
    r = client.post(url, json={})
    assert r.status_code == 200, r.text
    result = r.json()
    assert (result['text'], result['source'], result['cached']) == ('这是真实的口播。', 'speech_recognition', False)
    assert result['feishu']['status'] == 'skipped'
    assert client.post(url, json={}).json()['cached'] is True
    assert calls == ['api.tikhub.io', 'v26-web.douyinvod.com', 'asr.example']
    with app.state.Session() as db:
        assert db.get(Record, 'tikhub-budget-' + business_today()).payload['calls'] == 1
    distinct = add_material(client, ip, platform_id='7348888888888888889')
    assert client.post(f'/api/materials/{distinct["id"]}/transcript', json={}).status_code == 429


def test_public_tikhub_path_rejects_private_and_redirected_media(setup):
    app, client, _, ip = setup
    material = add_material(client, ip)
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'api.tikhub.io':
            return detail()
        if request.url.host == 'v26-web.douyinvod.com':
            return httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
        pytest.fail('不安全地址不能被访问')

    app.state.integration_transport = httpx.MockTransport(handler)
    r = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert r.status_code == 502
    assert calls == ['api.tikhub.io', 'v26-web.douyinvod.com']
    assert '127.0.0.1' not in r.text


def test_xhs_video_uses_note_detail(setup):
    app, client, _, ip = setup
    material = add_material(client, ip, platform='小红书', platform_id='697c0eee000000000a03c308',
                            source='https://www.xiaohongshu.com/explore/697c0eee000000000a03c308', content_type='video')

    def handler(request):
        if request.url.host == 'api.tikhub.io':
            assert request.url.path == '/api/v1/xiaohongshu/app_v2/get_video_note_detail'
            assert request.url.params['note_id'] == material['platform_id']
            return httpx.Response(200, json={'code': 200, 'data': {'data': [{
                'note_list': [{'note_id': material['platform_id'], 'type': 'video',
                               'video': {'media': {'stream': {'h264': [{'master_url': 'https://sns-video-bd.xhscdn.com/clip.wav'}]}}}}],
            }]}})
        if request.url.host.endswith('.xhscdn.com'):
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        return httpx.Response(200, json={'text': '小红书口播文本。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    result = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert result.status_code == 200, result.text
    assert result.json()['text'] == '小红书口播文本。'


def test_protected_wechat_and_unconfigured_and_image_notes_have_no_paid_media_call(setup):
    app, client, _, ip = setup
    material = add_material(client, ip, platform='视频号', platform_id='14941130915890399732', source='')
    calls = []

    def handler(request):
        calls.append(request.url.host)
        assert request.url.path == '/api/v1/wechat_channels/v2/fetch_video_detail'
        assert json.loads(request.read()) == {'object_id': material['platform_id'], 'raw': False}
        return httpx.Response(200, json={'code': 200, 'data': {'media': {
            'full_url': 'https://finder.video.qq.com/clip.mp4', 'decode_key': 'kept-private',
        }}})

    app.state.integration_transport = httpx.MockTransport(handler)
    assert client.put('/api/settings', json={'transcription': {'api_key': None}}).is_success
    url = f'/api/materials/{material["id"]}/transcript'
    result = client.post(url, json={})
    assert result.status_code == 409 and '转写' in result.json()['detail']
    assert calls == []
    assert client.put('/api/settings', json={'transcription': {'api_key': 'asr-test-key'}}).is_success
    note = add_material(client, ip, platform='小红书', content_type='normal')
    assert client.post(f'/api/materials/{note["id"]}/transcript', json={}).status_code == 422
    assert calls == []
    result = client.post(url, json={})
    assert result.status_code == 422 and '加密' in result.json()['detail']
    assert 'kept-private' not in result.text and calls == ['api.tikhub.io']


def test_member_cannot_transcribe_another_ip(setup):
    app, admin, team, ip = setup
    material = add_material(admin, ip)
    assert admin.post('/api/users', json={
        'username': 'transcript-member', 'name': '队员', 'password': 'example-password', 'role': 'member',
        'team_id': team['id'], 'ip_ids': [],
    }).is_success
    app.state.integration_transport = httpx.MockTransport(lambda _: pytest.fail('权限失败不能调用服务'))
    with TestClient(app) as member:
        url = f'/api/materials/{material["id"]}/transcript'
        assert member.post(url, json={}).status_code == 401
        assert member.post('/api/auth/login', json={'username': 'transcript-member', 'password': 'example-password'}).is_success
        assert member.post(url, json={}).status_code == 403


def test_local_asr_runs_without_remote_credentials(setup, monkeypatch):
    from backend.app.integrations import transcript
    app, client, _, ip = setup
    material = add_material(client, ip)
    assert client.put('/api/settings', json={'transcription': {'api_key': None}}).is_success
    monkeypatch.setattr(transcript, 'local_whisper', lambda: ('whisper', 'base.pt'))

    def recognize(spec, audio, folder, progress):
        assert spec == ('whisper', 'base.pt') and audio.is_file()
        progress(50)
        return '本机语音识别。'

    monkeypatch.setattr(transcript, 'transcribe_locally', recognize)

    def handler(request):
        if request.url.host == 'api.tikhub.io':
            return detail()
        if request.url.host.endswith('.douyinvod.com'):
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        pytest.fail('已有本机转写，不应调用云端')

    app.state.integration_transport = httpx.MockTransport(handler)
    r = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert r.status_code == 200, r.text
    assert r.json()['text'] == '本机语音识别。'


def test_falls_back_to_another_platform_cdn_from_same_provider_result(setup):
    app, client, _, ip = setup
    material = add_material(client, ip)
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == 'api.tikhub.io':
            return httpx.Response(200, json={'code': 200, 'data': {'aweme_detail': {'video': {
                'play_addr_h264': {'url_list': ['https://v26-webf.douyinvod.com/stale.wav',
                                               'https://v26-web.douyinvod.com/fresh.wav']},
            }}}})
        if request.url.host == 'v26-webf.douyinvod.com':
            return httpx.Response(403)
        if request.url.host == 'v26-web.douyinvod.com':
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        return httpx.Response(200, json={'text': '成功读取备用播放源。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    r = client.post(f'/api/materials/{material["id"]}/transcript', json={})
    assert r.status_code == 200, r.text
    assert seen == ['api.tikhub.io', 'v26-webf.douyinvod.com', 'v26-web.douyinvod.com', 'asr.example']


def test_same_video_concurrency_starts_only_one_tikhub_call(setup):
    app, client, _, ip = setup
    material = add_material(client, ip)
    provider_started, release_provider = Event(), Event()

    def handler(request):
        if request.url.host == 'api.tikhub.io':
            provider_started.set()
            assert release_provider.wait(timeout=5)
            return detail()
        if request.url.host.endswith('.douyinvod.com'):
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        return httpx.Response(200, json={'text': '只识别一次。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    url = f'/api/materials/{material["id"]}/transcript'
    with ThreadPoolExecutor(max_workers=1) as executor, TestClient(app, cookies=client.cookies) as second_client:
        first = executor.submit(client.post, url, json={})
        try:
            assert provider_started.wait(timeout=5)
            second = second_client.post(url, json={})
            assert second.status_code == 409 and '正在' in second.json()['detail']
        finally:
            release_provider.set()
        assert first.result(timeout=5).is_success
    with app.state.Session() as db:
        assert db.get(Record, 'tikhub-budget-' + business_today()).payload['calls'] == 1


def test_search_queues_transcript_and_worker_reports_progress_and_syncs(setup, monkeypatch):
    from backend.app.integrations import feishu
    from backend.app.worker import run_once

    app, client, _, ip = setup
    assert client.put('/api/settings', json={'collection': {'daily_budget': 20}}).is_success
    work_id = '7348888888888888888'
    calls = []

    def sync(app_, material, text, *, before=None):
        if before:
            before()
        calls.append(('feishu', material.id, text))
        return {'status': 'synced', 'message': '飞书作品逐字稿已同步'}

    monkeypatch.setattr(feishu, 'sync_work_transcript', sync, raising=False)
    asr_started, release_asr = Event(), Event()

    def handler(request):
        if request.url.path.endswith('/fetch_video_search_v1'):
            return httpx.Response(200, json={'data': {'data': [{'aweme_info': {
                'aweme_id': work_id, 'desc': '异步转写素材', 'video': {'duration': 5000},
            }}], 'has_more': 0}})
        if request.url.path.endswith('/fetch_video_comments'):
            return httpx.Response(200, json={'data': {'comments': [], 'has_more': 0}})
        if request.url.path.endswith('/fetch_one_video'):
            calls.append('detail')
            return detail()
        if request.url.host.endswith('.douyinvod.com'):
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        assert request.url.host == 'asr.example'
        asr_started.set()
        assert release_asr.wait(5)
        return httpx.Response(200, json={'text': '搜索完成后后台识别并保存。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    run = client.post('/api/collection/search', json={
        'ip_id': ip['id'], 'keywords': '测试搜索', 'platforms': ['douyin'], 'pages': 1,
        'max_videos': 1, 'comment_pages': 1, 'enrich_authors': False,
    })
    assert run.is_success, run.text
    assert run_once(app)
    material, = client.get('/api/records/materials').json()
    url = f'/api/materials/{material["id"]}/transcript'
    assert client.get(url).json()['status'] == 'queued'
    assert material['transcript_status'] == 'queued'
    assert client.post(url + '?background=true', json={}).status_code == 202
    assert not calls
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(run_once, app)
        try:
            assert asr_started.wait(5)
            progress = client.get(url).json()
            assert progress['status'] == 'running'
            assert 40 <= progress['progress'] < 100
        finally:
            release_asr.set()
        assert work.result(timeout=5)
    result = client.get(url).json()
    assert result['progress'] == 100 and result['status'] == 'completed'
    assert result['text'] == '搜索完成后后台识别并保存。'
    assert result['feishu']['status'] == 'synced'
    assert calls == ['detail', ('feishu', material['id'], result['text'])]
    assert client.post(url + '?background=true', json={}).json()['text'] == result['text']
    again = client.post('/api/collection/search', json={
        'ip_id': ip['id'], 'keywords': '另一关键词', 'platforms': ['douyin'], 'pages': 1,
        'max_videos': 1, 'comment_pages': 1, 'enrich_authors': False,
    })
    assert again.is_success
    assert run_once(app)
    assert not run_once(app)
    assert calls == ['detail', ('feishu', material['id'], result['text'])]


def test_feishu_retry_uses_saved_text_and_does_not_repeat_provider_calls(setup, monkeypatch):
    from backend.app.integrations import feishu
    app, client, _, ip = setup
    material = add_material(client, ip)
    url = f'/api/materials/{material["id"]}/transcript'
    results = iter(('failed', 'synced'))
    submitted = []

    def sync(app_, material_, text, *, before=None):
        submitted.append((material_.id, text))
        return {'status': next(results), 'message': '同步状态'}

    monkeypatch.setattr(feishu, 'sync_work_transcript', sync)
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'api.tikhub.io':
            return detail()
        if request.url.host.endswith('.douyinvod.com'):
            return httpx.Response(200, headers={'Content-Type': 'audio/wav'}, content=wav_body())
        return httpx.Response(200, json={'text': '已经保存的原文。'})

    app.state.integration_transport = httpx.MockTransport(handler)
    assert client.post(url, json={}).json()['feishu']['status'] == 'failed'
    assert client.get(url).json()['text'] == '已经保存的原文。'
    assert client.post(url + '/feishu', json={}).json()['status'] == 'synced'
    assert client.get(url).json()['feishu']['status'] == 'synced'
    assert calls == ['api.tikhub.io', 'v26-web.douyinvod.com', 'asr.example']
    assert submitted == [(material['id'], '已经保存的原文。')] * 2


def test_worker_resumes_interrupted_feishu_write_from_saved_transcript(setup, monkeypatch):
    from backend.app.integrations import feishu, transcript
    from backend.app.worker import run_once

    app, client, _, ip = setup
    material = add_material(client, ip)
    with app.state.Session() as db:
        row = db.get(Record, material['id'])
        job = transcript.enqueue_transcript(db, row, row.owner_id)
        job.payload = {**job.payload, 'status': 'completed', 'progress': 100, 'text': '已识别的口播。',
                       'feishu': {'status': 'pending'}, 'sync_token': 'interrupted', 'sync_until': 0}
        db.commit()

    def sync(app_, material_, text, *, before=None):
        before()
        assert material_.id == material['id'] and text == '已识别的口播。'
        return {'status': 'synced'}

    monkeypatch.setattr(feishu, 'sync_work_transcript', sync)
    app.state.integration_transport = httpx.MockTransport(lambda _: pytest.fail('已识别文字只需继续回写'))
    assert run_once(app)
    assert client.get(f'/api/materials/{material["id"]}/transcript').json()['feishu']['status'] == 'synced'
    assert not run_once(app)
