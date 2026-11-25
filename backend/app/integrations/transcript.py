"""Resolve public video media via TikHub and transcribe its original audio."""
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
import time
from urllib.parse import urlsplit
from uuid import uuid4
import wave

from fastapi import HTTPException, Request
import httpx

from ..db import Record, business_today
from ..discovery import number
from ..security import require_record
from . import tikhub
from .common import client, IntegrationError
from .platforms import WX_DETAIL

DOUYIN_DETAIL = '/api/v1/douyin/web/fetch_one_video'
DOUYIN_SHARE = '/api/v1/douyin/web/fetch_one_video_by_share_url'
XHS_DETAIL = '/api/v1/xiaohongshu/app_v2/get_video_note_detail'
MAX_MEDIA_BYTES = 120_000_000
MAX_AUDIO_SECONDS = 600
MAX_TEXT_CHARS = 20_000
LOCAL_ASR = BoundedSemaphore(1)
MEDIA_DOMAINS = {
    '抖音': ('douyinvod.com', 'douyinpic.com', 'bytecdn.cn', 'bytecdn.com'),
    '小红书': ('xhscdn.com', 'xiaohongshu.com', 'xhsimg.com'),
    '视频号': ('video.qq.com', 'weixin.qq.com'),
}


@dataclass(frozen=True)
class VideoIdentity:
    platform: str
    path: str
    parameter: str
    value: str


def host_is(host, domains):
    return bool(host and any(host == domain or host.endswith('.' + domain) for domain in domains))


def parsed_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        p = urlsplit(value)
        if not p.hostname or p.username is not None or p.password is not None or p.fragment:
            return None
        _ = p.port
        return p
    except ValueError:
        return None


def video_identity(payload):
    if payload.get('unavailable'):
        return None
    platform = payload.get('platform')
    if platform not in MEDIA_DOMAINS:
        return None
    work_id = str(payload.get('platform_id') or '')
    share = payload.get('source')
    url = parsed_url(share)
    if url and (url.scheme != 'https' or url.port not in (None, 443)):
        url = None
    if platform == '抖音':
        if not work_id and url and host_is(url.hostname, ('douyin.com',)):
            match = re.fullmatch(r'/video/([0-9]{8,24})/?', url.path)
            if match:
                work_id = match[1]
        if re.fullmatch(r'[0-9]{8,24}', work_id):
            return VideoIdentity(platform, DOUYIN_DETAIL, 'aweme_id', work_id)
        if url and url.hostname == 'v.douyin.com' and re.fullmatch(r'/[a-zA-Z0-9_-]+/?', url.path):
            return VideoIdentity(platform, DOUYIN_SHARE, 'share_url', share)
    if platform == '小红书':
        if str(payload.get('content_type')).lower() in ('image', 'normal', '图文', '图文笔记'):
            return None
        if not work_id and url and host_is(url.hostname, ('xiaohongshu.com',)):
            match = re.fullmatch(r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})/?', url.path)
            if match and not url.query:
                work_id = match[1]
        if re.fullmatch(r'[a-fA-F0-9]{24}', work_id):
            return VideoIdentity(platform, XHS_DETAIL, 'note_id', work_id)
        if url and host_is(url.hostname, ('xiaohongshu.com', 'xhslink.com', 'xhslink.cn')):
            if re.fullmatch(r'/(?:explore/|discovery/item/|o/)?[a-zA-Z0-9_-]+/?', url.path):
                return VideoIdentity(platform, XHS_DETAIL, 'share_text', share)
    if platform == '视频号':
        if re.fullmatch(r'[0-9]{8,26}', work_id):
            return VideoIdentity(platform, WX_DETAIL, 'object_id', work_id)
        if url and url.hostname == 'weixin.qq.com' and re.fullmatch(r'/sph/[a-zA-Z0-9_-]+/?', url.path):
            return VideoIdentity(platform, WX_DETAIL, 'share_url', share)
    return None


def response_media(http, identity, key):
    params = {identity.parameter: identity.value}
    auth = {'Authorization': 'Bearer ' + key}
    if identity.platform == '视频号':
        response = http.post(tikhub.BASE + identity.path, headers=auth, json={**params, 'raw': False})
    else:
        response = http.get(tikhub.BASE + identity.path, headers=auth, params=params)
    data = tikhub.unwrap(response)
    if identity.platform == '抖音':
        detail = data.get('aweme_detail') or data.get('aweme_info') or {}
        status = detail.get('status') or {}
        if status.get('is_private') or status.get('is_delete'):
            raise HTTPException(422, '原视频不可公开访问，请手工补充逐字稿')
        video = detail.get('video') or {}
        candidates = [
            url for address in ('play_addr_h264', 'play_addr', 'download_addr')
            for url in (video.get(address) or {}).get('url_list', [])
        ]
    elif identity.platform == '小红书':
        candidates = list(xhs_video_urls(data))
    else:
        media = data.get('media') or {}
        if media.get('decode_key'):
            raise HTTPException(422, '视频号返回加密媒体，请手工补充对标逐字稿')
        candidates = [media.get('full_url') or ((media.get('url') or '') + (media.get('url_token') or ''))]
    urls = list(dict.fromkeys(
        url for value in candidates
        if (url := media_url(value, identity.platform, allow_http_upgrade=True))
    ))
    if not urls:
        raise IntegrationError('平台未返回可读取的视频媒体，可能是作品已失效或没有音轨')
    return urls[:3]


def xhs_video_urls(detail):
    """The note/detail envelope varies between App versions; only traverse video fields for URLs."""
    pending = [(detail, False)]
    while pending:
        item, in_video = pending.pop()
        if isinstance(item, list):
            pending.extend((entry, in_video) for entry in reversed(item))
        if not isinstance(item, dict):
            continue
        if in_video:
            for key in ('master_url', 'masterUrl', 'play_url', 'playUrl', 'video_url', 'videoUrl', 'url'):
                value = item.get(key)
                if isinstance(value, str):
                    yield value
        pending.extend((value, in_video or key in ('media', 'video', 'video_info', 'stream'))
                       for key, value in reversed(tuple(item.items())) if isinstance(value, (dict, list)))


def media_url(value, platform, *, allow_http_upgrade=False):
    p = parsed_url(value)
    if p and host_is(p.hostname, MEDIA_DOMAINS[platform]) and p.port in (None, 443):
        if p.scheme == 'https':
            return value
        if p.scheme == 'http' and allow_http_upgrade:
            return p._replace(scheme='https').geturl()
    return None


def download_media(http, url, path, started):
    # CDN requests carry no API credentials and cannot follow a redirect.
    with http.stream('GET', url, headers={'Accept-Encoding': 'identity'}, timeout=20) as response:
        media_type = response.headers.get('content-type', '').partition(';')[0].lower()
        if response.status_code != 200 or not (media_type.startswith(('audio/', 'video/')) or media_type == 'application/octet-stream'):
            raise IntegrationError('平台视频暂时无法读取，请核对原视频或手工补充逐字稿')
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            raise IntegrationError('平台返回了无法读取的视频编码，请手工补充逐字稿')
        if (response.headers.get('content-length') or '').isdigit() and int(response.headers['content-length']) > MAX_MEDIA_BYTES:
            raise HTTPException(422, '视频超过转写大小上限，请手工补充逐字稿')
        total = 0
        with path.open('wb') as stream:
            for chunk in response.iter_bytes(256_000):
                total += len(chunk)
                if total > MAX_MEDIA_BYTES or time.monotonic() - started > 45:
                    raise HTTPException(422, '视频太大或读取超时，请手工补充逐字稿')
                stream.write(chunk)
        if not total:
            raise IntegrationError('平台返回的视频为空，请手工补充逐字稿')


def read_platform_media(http, urls, path):
    started = time.monotonic()
    for url in urls:
        if time.monotonic() - started > 45:
            break
        try:
            download_media(http, url, path, started)
            return
        except (IntegrationError, httpx.HTTPError):
            continue
    raise IntegrationError('平台视频暂时无法读取，请核对原视频或手工补充逐字稿')


def audio_file(source, target):
    try:
        with wave.open(str(source), 'rb') as wav:
            if wav.getframerate() == 16_000 and wav.getnchannels() == 1 and wav.getsampwidth() == 2:
                if wav.getnframes() > 16_000 * MAX_AUDIO_SECONDS:
                    raise HTTPException(422, '视频超过 10 分钟转写上限，请手工补充逐字稿')
                return source
    except (wave.Error, EOFError):
        pass
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise HTTPException(409, '音轨提取需要本机安装 FFmpeg，请联系管理员或手工补充逐字稿')
    try:
        subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-protocol_whitelist', 'file',
             '-format_whitelist', 'mov,matroska,webm,mp3,wav,ogg,aac,flac,m4v',
             '-threads', '1', '-i', str(source), '-map', '0:a:0', '-ac', '1', '-ar', '16000',
             '-c:a', 'pcm_s16le', '-t', str(MAX_AUDIO_SECONDS + 1), str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=35, check=True,
        )
        with wave.open(str(target), 'rb') as wav:
            if wav.getnframes() > 16_000 * MAX_AUDIO_SECONDS:
                raise HTTPException(422, '视频超过 10 分钟转写上限，请手工补充逐字稿')
            if not wav.getnframes():
                raise IntegrationError('视频没有可转写的音轨，请手工补充逐字稿')
    except (OSError, subprocess.SubprocessError, wave.Error, EOFError):
        raise IntegrationError('无法读取视频音轨，可能已加密或没有声音，请手工补充逐字稿') from None
    return target


def local_whisper():
    user_dir = Path.home()
    binary = shutil.which('whisper') or user_dir / '.local' / 'bin' / 'whisper'
    model = Path(os.environ.get('XDG_CACHE_HOME') or user_dir / '.cache') / 'whisper' / 'base.pt'
    if Path(binary).is_file() and os.access(binary, os.X_OK) and model.is_file():
        return binary, model
    return None


def transcript_text(value):
    value = value.strip() if isinstance(value, str) else ''
    if not value:
        raise IntegrationError('语音转写没有返回可用文字，请手工补充逐字稿')
    if len(value) > MAX_TEXT_CHARS:
        raise IntegrationError('转写内容超过 20,000 字，请手工分段补充逐字稿')
    return value


def transcribe_locally(spec, audio, folder):
    if not LOCAL_ASR.acquire(blocking=False):
        raise HTTPException(409, '本机正在转写另一条视频，请稍后重试')
    binary, model = spec
    try:
        subprocess.run(
            [str(binary), str(audio), '--model', str(model), '--device', 'cpu',
             '--output_dir', str(folder), '--output_format', 'txt', '--task', 'transcribe',
             '--fp16', 'False', '--verbose', 'False', '--threads', '4',
             '--initial_prompt', '如果是中文口播，请按原话使用简体中文逐字记录。',
             '--temperature', '0', '--temperature_increment_on_fallback', 'None'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=160, check=True,
        )
        return transcript_text((folder / (audio.stem + '.txt')).read_text(encoding='utf-8'))
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise IntegrationError('本机 Whisper 转写失败，请手工补充逐字稿') from None
    finally:
        LOCAL_ASR.release()


def transcribe_remotely(http, app, settings, audio):
    url = settings['base_url'].rstrip('/') + '/audio/transcriptions'
    key = app.state.secrets.decrypt(settings['api_key_encrypted'])
    with audio.open('rb') as stream:
        response = http.post(url, headers={'Authorization': 'Bearer ' + key},
                             files={'file': ('original-audio.wav', stream, 'audio/wav')},
                             data={'model': settings['model'], 'response_format': 'json'},
                             timeout=115)
    if response.status_code != 200:
        raise IntegrationError(f'语音转写服务返回 HTTP {response.status_code}；请检查模型是否支持音频转写')
    try:
        text = response.json().get('text')
    except (ValueError, AttributeError):
        raise IntegrationError('语音转写服务响应格式无效，请核对管理员配置') from None
    return transcript_text(text)


def checked_settings(db, whisper):
    row = db.get(Record, 'system-settings')
    settings = row.payload if row else {}
    collection = settings.get('collection') or {}
    if not collection.get('enabled') or not collection.get('api_key_encrypted'):
        raise HTTPException(409, '请管理员在系统设置启用并配置 TikHub')
    transcription = settings.get('transcription') or {}
    parsed = parsed_url(transcription.get('base_url'))
    remote = transcription if (parsed and parsed.scheme in ('https', 'http') and not parsed.query
                               and transcription.get('api_key_encrypted') and transcription.get('model')) else None
    if not whisper and not remote:
        raise HTTPException(409, '请管理员配置音频转写服务、模型和 API Key，或安装本机 Whisper')
    return collection, remote


def reserve_budget(db, settings):
    key = 'tikhub-budget-' + business_today()
    ledger = db.get(Record, key)
    used = ledger.payload.get('calls', 0) if ledger else 0
    if used >= int(settings.get('daily_budget') or 100):
        raise HTTPException(429, 'TikHub 今日调用预算已耗尽，可手工补充对标逐字稿')
    if ledger:
        ledger.payload = {'calls': used + 1}
    else:
        db.add(Record(id=key, kind='integration_budget', payload={'calls': 1}))


def require_material(db, user, key):
    row = db.get(Record, key)
    if not row or row.kind != 'materials':
        raise HTTPException(404, '素材不存在')
    require_record(db, user, row)
    return row


def register(app, audit, authenticated_user):
    @app.post('/api/materials/{key}/transcript')
    def material_transcript(key: str, request: Request):
        token = uuid4().hex
        whisper = local_whisper()
        # Short transaction to authorize, deduplicate and reserve before any network I/O.
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            user = authenticated_user(request, db)
            material = require_material(db, user, key)
            identity = video_identity(material.payload)
            if not identity:
                raise HTTPException(422, '素材缺少支持的公开视频来源，请手工补充对标逐字稿')
            if (number(material.payload.get('duration')) or 0) > MAX_AUDIO_SECONDS:
                raise HTTPException(422, '视频超过 10 分钟转写上限，请手工补充逐字稿')
            cache_key = 'material-transcript-' + sha256(repr((key, identity)).encode()).hexdigest()
            cached = db.get(Record, cache_key)
            if cached and cached.payload.get('text'):
                return {'text': cached.payload['text'], 'source': 'speech_recognition', 'cached': True}
            if cached and cached.payload.get('lease_until', 0) > time.time():
                raise HTTPException(409, '此视频正在提取逐字稿，请稍后重试')
            collection, settings = checked_settings(db, whisper)
            reserve_budget(db, collection)
            claim = {'token': token, 'lease_until': time.time() + 450}
            if cached:
                cached.payload = claim
            else:
                db.add(Record(id=cache_key, kind='material_transcript', ip_id=material.ip_id,
                              owner_id=material.owner_id, payload=claim))
            db.commit()

        complete = False
        try:
            with client(app) as http, TemporaryDirectory(prefix='acquisition-transcript-') as temp:
                folder = Path(temp)
                urls = response_media(http, identity, app.state.secrets.decrypt(collection['api_key_encrypted']))
                media = folder / 'original.media'
                read_platform_media(http, urls, media)
                audio = audio_file(media, folder / 'audio.wav')
                if whisper:
                    try:
                        transcript = transcribe_locally(whisper, audio, folder)
                    except IntegrationError:
                        if not settings:
                            raise
                        transcript = transcribe_remotely(http, app, settings, audio)
                else:
                    transcript = transcribe_remotely(http, app, settings, audio)
            with app.state.Session() as db:
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                user = authenticated_user(request, db)
                if video_identity(require_material(db, user, key).payload) != identity:
                    raise HTTPException(409, '素材来源已变更，请重新打开调用写手')
                cached = db.get(Record, cache_key)
                if not cached or cached.payload.get('token') != token:
                    raise HTTPException(409, '转写任务已变更，请重新打开调用写手')
                cached.payload = {'text': transcript}
                audit(db, user, '提取素材逐字稿', key)
                db.commit()
                complete = True
            return {'text': transcript, 'source': 'speech_recognition', 'cached': False}
        except IntegrationError as exc:
            raise HTTPException(502, str(exc)) from None
        except (httpx.HTTPError, KeyError, AttributeError, TypeError, ValueError):
            raise HTTPException(502, '读取视频或转写服务失败，请手工补充逐字稿或稍后重试') from None
        finally:
            if not complete:
                with app.state.Session() as db:
                    db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                    cached = db.get(Record, cache_key)
                    if cached and cached.payload.get('token') == token:
                        db.delete(cached)
                        db.commit()
