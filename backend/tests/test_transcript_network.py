import os
import sys
from textwrap import dedent

import httpx
import pytest
from fastapi import HTTPException

from backend.app.integrations import transcript


def platform_detail(platform, payload):
    material = {
        'platform': platform,
        'platform_id': '7372484719365098803' if platform == '抖音' else '697c0eee000000000a03c308',
        'content_type': 'video',
    }
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.url.host == 'api.tikhub.io'
        return httpx.Response(200, json={'code': 200, 'data': payload})

    with httpx.Client(transport=httpx.MockTransport(upstream)) as http:
        try:
            return transcript.response_media(http, transcript.video_identity(material), 'fake-token-for-test')
        finally:
            assert len(requests) == 1


def test_douyin_filter_detail_identifies_unavailable_video():
    with pytest.raises(HTTPException) as exc:
        platform_detail('抖音', {
            'status_code': 10209,
            'aweme_detail': None,
            'filter_detail': {'detail_msg': 'provider diagnostic that should not be copied'},
        })
    assert exc.value.status_code == 422
    assert '原视频不可公开访问' in exc.value.detail
    assert 'provider diagnostic' not in exc.value.detail


def test_douyin_uses_bitrate_media_when_primary_address_is_missing_or_malformed():
    assert platform_detail('抖音', {'aweme_detail': {'video': {
        'play_addr_h264': {'url_list': None},
        'play_addr': ['non-object value'],
        'bit_rate': [
            {'play_addr': {'url_list': ['https://invalid.example/private', 'https://v26-web.douyinvod.com/clip.mp4']}},
        ],
    }}}) == ['https://v26-web.douyinvod.com/clip.mp4']


def test_xhs_uses_only_allowed_backup_urls_from_the_video_stream():
    assert platform_detail('小红书', {'data': [{'note_list': [
        {'video': {'media': {'stream': {'h264': [{
            'master_url': 'https://sns-video-bd.xhscdn.com/clip.mp4',
            'backup_urls': ['https://invalid.example/private', 'https://sns-video-bd.xhscdn.com/backup.mp4'],
        }]}}}},
    ]}]}) == [
        'https://sns-video-bd.xhscdn.com/clip.mp4',
        'https://sns-video-bd.xhscdn.com/backup.mp4',
    ]


def test_xhs_accepts_rednote_video_cdn_and_skips_subtitles_and_lookalikes():
    assert platform_detail('小红书', {'code': 0, 'data': [{
        'type': 'video',
        'video_info_v2': {'media': {
            'video': {'subtitles': {'source': [{'url': 'https://sns-subtitle-s10.rednotecdn.com/clip.srt'}]}},
            'stream': {'h264': [{
                'master_url': 'https://sns-v11.rednotecdn.com/clip.mp4',
                'backup_urls': ['https://sns-v27.rednotecdn.com/clip.mp4',
                                'https://sns-v27.rednotecdn.com.example.test/clip.mp4',
                                'https://sns-i11.rednotecdn.com/cover.jpg'],
            }]},
        }},
    }]}) == [
        'https://sns-v11.rednotecdn.com/clip.mp4',
        'https://sns-v27.rednotecdn.com/clip.mp4',
    ]


def test_douyin_accepts_its_provider_returned_zjcdn_video_hosts():
    assert platform_detail('抖音', {'aweme_detail': {'video': {
        'play_addr': {'url_list': [
            'https://v5-dy-ov-experiment.zjcdn.com/clip.mp4',
            'https://v3-dy-o.zjcdn.com/clip.mp4',
            'https://v3-dy-o.zjcdn.com.example.org/clip.mp4',
            'https://www.douyin.com/aweme/v1/play/',
        ]},
    }}}) == [
        'https://v5-dy-ov-experiment.zjcdn.com/clip.mp4',
        'https://v3-dy-o.zjcdn.com/clip.mp4',
    ]


def test_local_whisper_reports_live_progress_before_process_exits(tmp_path):
    fake_whisper = tmp_path / 'fake_whisper.py'
    fake_whisper.write_text(dedent(r'''
        import sys
        import time
        from pathlib import Path

        script = Path(__file__)
        sys.stderr.write('provider log containing 99%| that must be ignored\r 25%| partial')
        sys.stderr.flush()
        deadline = time.monotonic() + 2
        while not script.with_suffix('.ack').exists():
            if time.monotonic() >= deadline:
                sys.exit(1)
            time.sleep(0.02)
        sys.stderr.write('\r 20%| stale\r100%| complete\n')
        sys.stderr.flush()
        script.with_suffix('.txt').write_text('原始逐字稿。', encoding='utf-8')
    '''), encoding='utf-8')
    observed = []

    def progress(percent):
        observed.append(percent)
        if percent == 25:
            assert not fake_whisper.with_suffix('.txt').exists()
            fake_whisper.with_suffix('.ack').touch()

    assert transcript.transcribe_locally((sys.executable, 'unused-model'), fake_whisper, tmp_path, progress) == '原始逐字稿。'
    assert observed == [25, 100]


def test_local_whisper_reaps_process_when_progress_is_interrupted(tmp_path):
    fake_whisper = tmp_path / 'fake_whisper.py'
    fake_whisper.write_text(dedent(r'''
        import os
        import sys
        import time
        from pathlib import Path

        Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
        sys.stderr.write('\r 25%| partial')
        sys.stderr.flush()
        time.sleep(2)
    '''), encoding='utf-8')

    def progress(_percent):
        raise HTTPException(409, '转写任务已变更')

    with pytest.raises(HTTPException, match='转写任务已变更'):
        transcript.transcribe_locally((sys.executable, 'unused-model'), fake_whisper, tmp_path, progress)
    with pytest.raises(ProcessLookupError):
        os.kill(int(fake_whisper.with_suffix('.pid').read_text()), 0)
    assert transcript.LOCAL_ASR.acquire(blocking=False)
    transcript.LOCAL_ASR.release()
