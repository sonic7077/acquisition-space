"""Resolve public video media via TikHub and transcribe its original audio."""
from dataclasses import dataclass
from hashlib import sha256
import logging
from math import ceil
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
from sqlalchemy import func, select
from fastapi.responses import JSONResponse

from ..db import Record, User, business_today, now
from ..discovery import number
from ..security import can_record, require_record
from . import tikhub
from .common import client, IntegrationError
from .platforms import WX_DETAIL
from .transcript_network import platform_media_candidates

DOUYIN_DETAIL = '/api/v1/douyin/web/fetch_one_video'
DOUYIN_SHARE = '/api/v1/douyin/web/fetch_one_video_by_share_url'
XHS_DETAIL = '/api/v1/xiaohongshu/app_v2/get_video_note_detail'
MAX_MEDIA_BYTES = 120_000_000
MEDIA_TRANSFER_SECONDS = 180
MAX_AUDIO_SECONDS = 20 * 60
MAX_CLOUD_AUDIO_SECONDS = 10 * 60
MIN_LOCAL_ASR_TIMEOUT = 160
TRANSCRIPT_LEASE_SECONDS = 450
SUPPORTED_AUDIO_ERROR = f'视频超过 {MAX_AUDIO_SECONDS // 60} 分钟转写上限，请手工补充逐字稿'
MAX_TEXT_CHARS = 20_000
LOCAL_ASR = BoundedSemaphore(1)
LOGGER = logging.getLogger(__name__)
MEDIA_DOMAINS = {
    '抖音': ('douyinvod.com', 'douyinpic.com', 'bytecdn.cn', 'bytecdn.com',
           'v5-dy-ov-experiment.zjcdn.com', 'v3-dy-o.zjcdn.com'),
    '小红书': ('xhscdn.com', 'xiaohongshu.com', 'xhsimg.com'),
    '视频号': ('video.qq.com', 'weixin.qq.com'),
}


@dataclass(frozen=True)
class VideoIdentity:
    platform: str
    path: str
    parameter: str
    value: str


class MediaTooLarge(HTTPException):
    def __init__(self):
        super().__init__(422, '视频超过转写大小上限，请手工补充逐字稿')


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
    candidates = platform_media_candidates(response, identity.platform)
    urls = list(dict.fromkeys(
        url for value in candidates
        if (url := media_url(value, identity.platform, allow_http_upgrade=True))
    ))
    if not urls:
        raise IntegrationError('平台未返回可读取的视频媒体，可能是作品已失效或没有音轨')
    return urls[:3]


def media_url(value, platform, *, allow_http_upgrade=False):
    p = parsed_url(value)
    if not p or p.port not in (None, 443):
        return None
    rednote_video_host = platform == '小红书' and re.fullmatch(r'sns-v[0-9]{1,3}\.rednotecdn\.com', p.hostname)
    if rednote_video_host or host_is(p.hostname, MEDIA_DOMAINS[platform]):
        if p.scheme == 'https':
            return value
        if p.scheme == 'http' and allow_http_upgrade:
            return p._replace(scheme='https').geturl()
    return None


def download_media(http, url, path, started, progress):
    # CDN requests carry no API credentials and cannot follow a redirect.
    with http.stream('GET', url, headers={'Accept-Encoding': 'identity'}, timeout=20) as response:
        media_type = response.headers.get('content-type', '').partition(';')[0].lower()
        if response.status_code != 200 or not (media_type.startswith(('audio/', 'video/')) or media_type == 'application/octet-stream'):
            raise IntegrationError('平台视频暂时无法读取，请核对原视频或手工补充逐字稿')
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            raise IntegrationError('平台返回了无法读取的视频编码，请手工补充逐字稿')
        length = response.headers.get('content-length', '')
        expected = int(length) if length.isdigit() else 0
        if expected > MAX_MEDIA_BYTES:
            raise MediaTooLarge()
        total = 0
        reported = 15
        with path.open('wb') as stream:
            for chunk in response.iter_bytes(256_000):
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise MediaTooLarge()
                if time.monotonic() - started > MEDIA_TRANSFER_SECONDS:
                    raise HTTPException(422, '视频下载超过时间上限，请手工补充逐字稿')
                stream.write(chunk)
                if expected:
                    percent = 15 + min(23, total * 23 // expected)
                    if percent >= reported + 4:
                        progress(percent, '下载视频')
                        reported = percent
        if not total:
            raise IntegrationError('平台返回的视频为空，请手工补充逐字稿')


def read_platform_audio(http, urls, media, target, progress):
    started = time.monotonic()
    size_failure = audio_failure = None
    for index, url in enumerate(urls):
        if time.monotonic() - started > MEDIA_TRANSFER_SECONDS:
            break
        progress(15, '尝试备用媒体' if index else '下载视频')
        try:
            download_media(http, url, media, started, progress)
        except MediaTooLarge as error:
            size_failure = error
            continue
        except (IntegrationError, httpx.HTTPError):
            continue
        progress(42, '提取音轨')
        try:
            return audio_file(media, target)
        except IntegrationError as error:
            audio_failure = error
            target.unlink(missing_ok=True)
    if audio_failure:
        raise audio_failure
    if size_failure:
        raise size_failure
    raise IntegrationError('平台视频暂时无法读取，请核对原视频或手工补充逐字稿')


def audio_file(source, target):
    try:
        with wave.open(str(source), 'rb') as wav:
            if wav.getframerate() == 16_000 and wav.getnchannels() == 1 and wav.getsampwidth() == 2:
                if wav.getnframes() > 16_000 * MAX_AUDIO_SECONDS:
                    raise HTTPException(422, SUPPORTED_AUDIO_ERROR)
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
                raise HTTPException(422, SUPPORTED_AUDIO_ERROR)
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


def local_asr_timeout(audio):
    try:
        with wave.open(str(audio), 'rb') as wav:
            seconds = min(MAX_AUDIO_SECONDS, wav.getnframes() / wav.getframerate())
    except (wave.Error, EOFError):
        return MIN_LOCAL_ASR_TIMEOUT
    return max(MIN_LOCAL_ASR_TIMEOUT, ceil(seconds / 3))


def transcribe_locally(spec, audio, folder, progress):
    if not LOCAL_ASR.acquire(blocking=False):
        raise HTTPException(409, '本机正在转写另一条视频，请稍后重试')
    binary, model = spec
    try:
        timeout = local_asr_timeout(audio)
        command = [str(binary), str(audio), '--model', str(model), '--device', 'cpu',
                   '--output_dir', str(folder), '--output_format', 'txt', '--task', 'transcribe',
                   '--fp16', 'False', '--verbose', 'False', '--threads', '4',
                   '--initial_prompt', '如果是中文口播，请按原话使用简体中文逐字记录。',
                   '--temperature', '0', '--temperature_increment_on_fallback', 'None']
        stderr_path = folder / 'whisper-progress.log'
        with stderr_path.open('wb') as writer, stderr_path.open('rb') as reader:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=writer)
            try:
                deadline = time.monotonic() + timeout
                unfinished = b''
                last_percent = -1
                while True:
                    return_code = process.poll()
                    # Whisper writes tqdm progress lines separated by carriage returns.
                    lines = re.split(rb'[\r\n]', unfinished + reader.read())
                    unfinished = lines[-1]
                    for line in lines:
                        if match := re.match(rb'[ \t]*(\d{1,3})%\|', line):
                            percent = int(match.group(1))
                            if last_percent < percent <= 100:
                                progress(percent)
                                last_percent = percent
                    if return_code is not None:
                        if return_code:
                            raise subprocess.CalledProcessError(return_code, command)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        process.wait(timeout=min(remaining, 0.1))
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
        return transcript_text((folder / (audio.stem + '.txt')).read_text(encoding='utf-8'))
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise IntegrationError('本机 Whisper 转写失败，请手工补充逐字稿') from None
    finally:
        LOCAL_ASR.release()


def transcribe_remotely(http, app, settings, audio):
    url = settings['base_url'].rstrip('/') + '/audio/transcriptions'
    key = app.state.secrets.decrypt(settings['api_key_encrypted'])
    with wave.open(str(audio), 'rb') as wav:
        chunk_frames = wav.getframerate() * MAX_CLOUD_AUDIO_SECONDS
        if wav.getnframes() <= chunk_frames:
            return transcript_text(request_cloud_asr(http, url, key, settings['model'], audio))
        chunks = []
        while frames := wav.readframes(chunk_frames):
            part = audio.with_name(f'cloud-part-{len(chunks) + 1}.wav')
            with wave.open(str(part), 'wb') as output:
                output.setparams(wav.getparams())
                output.writeframes(frames)
            chunks.append(request_cloud_asr(http, url, key, settings['model'], part))
        return transcript_text('\n'.join(chunk for chunk in chunks if chunk))


def request_cloud_asr(http, url, key, model, audio):
    with audio.open('rb') as stream:
        response = http.post(url, headers={'Authorization': 'Bearer ' + key},
                             files={'file': ('original-audio.wav', stream, 'audio/wav')},
                             data={'model': model, 'response_format': 'json'},
                             timeout=115)
    if response.status_code != 200:
        raise IntegrationError(f'语音转写服务返回 HTTP {response.status_code}；请检查模型是否支持音频转写')
    try:
        text = response.json().get('text')
    except (ValueError, AttributeError):
        raise IntegrationError('语音转写服务响应格式无效，请核对管理员配置') from None
    if not isinstance(text, str):
        raise IntegrationError('语音转写服务响应格式无效，请核对管理员配置')
    return text.strip()


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



def transcript_key(material_id, identity):
    return 'material-transcript-' + sha256(repr((material_id, identity)).encode()).hexdigest()


def supported_identity(payload):
    if (number(payload.get('duration')) or 0) > MAX_AUDIO_SECONDS:
        return None
    return video_identity(payload)


def transcript_states(db):
    return {row.id: row.payload for row in db.scalars(select(Record).where(Record.kind == 'material_transcript'))}


def progress_view(payload, *, include_text=False):
    status = payload.get('status', 'completed' if payload.get('text') else 'idle')
    if status == 'running' and payload.get('lease_until', 0) <= time.time():
        status = 'failed'
        payload = {**payload, 'stage': '转写已中断', 'error': '转写任务已中断，可重试提取'}
    result = {'status': status, 'progress': payload.get('progress', 100 if status == 'completed' else 0),
              'stage': payload.get('stage', '逐字稿已保存' if status == 'completed' else '等待提取')}
    if payload.get('error'):
        result['error'] = payload['error']
    if status == 'completed':
        feishu = payload.get('feishu', {'status': 'skipped', 'message': '本地逐字稿已保存，可同步至飞书'})
        if feishu['status'] == 'pending' and payload.get('sync_token') and payload.get('sync_until', 0) <= time.time():
            feishu = {'status': 'pending', 'message': '等待恢复飞书同步'}
        result['feishu'] = feishu
        if include_text:
            result.update(text=payload['text'], cached=True)
    return result


def enqueue_transcript(db, material, user_id, *, retry=False):
    identity = supported_identity(material.payload)
    if not identity:
        return None
    key = transcript_key(material.id, identity)
    row = db.get(Record, key)
    if row and (not retry or progress_view(row.payload)['status'] in ('queued', 'running', 'completed')):
        return row
    payload = {'material_id': material.id, 'status': 'queued', 'progress': 0, 'stage': '已排队'}
    if row:
        row.payload, row.owner_id, row.updated_at = payload, user_id, now()
    else:
        row = Record(id=key, kind='material_transcript', ip_id=material.ip_id, owner_id=user_id, payload=payload)
        db.add(row)
    return row


def authorized_material(db, user_id, material_id, key):
    user, material = db.get(User, user_id), db.get(Record, material_id)
    if not user or not user.active or not material or material.kind != 'materials' or not can_record(db, user, material):
        raise HTTPException(403, '素材权限已变更，转写已停止')
    identity = supported_identity(material.payload)
    if not identity or transcript_key(material.id, identity) != key:
        raise HTTPException(409, '素材来源已变更，请重新提取逐字稿')
    return user, material, identity


def prepare_transcript(db, row):
    authorized_material(db, row.owner_id, row.payload['material_id'], row.id)
    whisper = local_whisper()
    collection, settings = checked_settings(db, whisper)
    reserve_budget(db, collection)
    token = uuid4().hex
    row.payload = {**row.payload, 'status': 'running', 'stage': '读取视频来源',
                   'progress': 5, 'token': token, 'lease_until': time.time() + TRANSCRIPT_LEASE_SECONDS}
    row.updated_at = now()
    return collection, settings, whisper, token


def record_failure(row, message):
    row.payload = {**row.payload, 'status': 'failed', 'stage': '提取失败',
                   'error': message, 'token': None, 'lease_until': 0}
    row.updated_at = now()


def sync_transcript(app, key, user_id, material_id, *, automatic=False):
    from .feishu import sync_work_transcript
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(Record, key)
        if not row or not row.payload.get('text'):
            raise HTTPException(409, '逐字稿尚未提取完成')
        status = row.payload.get('feishu', {})
        try:
            _, material, _ = authorized_material(db, user_id, material_id, key)
        except HTTPException as exc:
            if not automatic:
                raise
            result = {'status': 'failed', 'message': exc.detail}
            row.payload = {**row.payload, 'feishu': result, 'sync_token': None, 'sync_until': 0}
            db.commit()
            return result
        if status.get('status') == 'synced' or row.payload.get('sync_until', 0) > time.time():
            return status
        if automatic and status.get('status') != 'pending':
            return status
        token = uuid4().hex
        text = row.payload['text']
        row.payload = {**row.payload, 'sync_token': token, 'sync_until': time.time() + 180,
                       'feishu': {'status': 'pending', 'message': '正在同步飞书作品表'}}
        db.commit()

    def before():
        with app.state.Session() as db:
            authorized_material(db, user_id, material.id, key)
            current = db.get(Record, key)
            if not current or current.payload.get('sync_token') != token:
                raise HTTPException(409, '飞书同步任务已变更')

    try:
        result = sync_work_transcript(app, material, text, before=before)
    except HTTPException as exc:
        result = {'status': 'failed', 'message': exc.detail}
    except Exception:
        result = {'status': 'failed', 'message': '逐字稿已保存在本地；飞书同步失败，可重试同步'}
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(Record, key)
        if row and row.payload.get('sync_token') == token:
            row.payload = {**row.payload, 'feishu': result, 'sync_token': None, 'sync_until': 0}
            db.commit()
    return result


def execute_transcript(app, key, prepared, audit):
    collection, settings, whisper, token = prepared
    stage = '读取视频来源'

    def progress(percent, label):
        nonlocal stage
        stage = label
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = db.get(Record, key)
            if not row or row.payload.get('token') != token:
                raise HTTPException(409, '转写任务已变更，请重新提取')
            row.payload = {**row.payload, 'progress': max(row.payload['progress'], percent), 'stage': stage,
                           'lease_until': time.time() + TRANSCRIPT_LEASE_SECONDS}
            db.commit()

    try:
        with app.state.Session() as db:
            row = db.get(Record, key)
            user_id, material_id = row.owner_id, row.payload['material_id']
            _, _, identity = authorized_material(db, user_id, material_id, key)
        with client(app) as http, TemporaryDirectory(prefix='acquisition-transcript-') as temp:
            folder = Path(temp)
            urls = response_media(http, identity, app.state.secrets.decrypt(collection['api_key_encrypted']))
            audio = read_platform_audio(http, urls, folder / 'original.media', folder / 'audio.wav', progress)
            progress(60, '识别语音')
            if whisper:
                try:
                    text = transcribe_locally(
                        whisper, audio, folder, lambda percent: progress(60 + percent * 35 // 100, '识别语音'),
                    )
                except IntegrationError:
                    if not settings:
                        raise
                    progress(65, '使用云端服务识别语音')
                    text = transcribe_remotely(http, app, settings, audio)
            else:
                text = transcribe_remotely(http, app, settings, audio)
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            user, _, _ = authorized_material(db, user_id, material_id, key)
            row = db.get(Record, key)
            if not row or row.payload.get('token') != token:
                raise HTTPException(409, '转写任务已变更，请重新提取')
            row.payload = {'material_id': material_id, 'status': 'completed', 'progress': 100,
                           'stage': '逐字稿已保存', 'text': text,
                           'feishu': {'status': 'pending', 'message': '等待写入飞书作品表'}}
            row.updated_at = now()
            audit(db, user, '提取素材逐字稿', material_id)
            db.commit()
    except Exception as exc:
        if isinstance(exc, HTTPException):
            error = exc
        elif isinstance(exc, IntegrationError):
            error = HTTPException(502, str(exc))
        elif isinstance(exc, httpx.HTTPError):
            error = HTTPException(502, f'{stage}时连接失败或超时，请重试提取')
        elif isinstance(exc, (KeyError, AttributeError, TypeError, ValueError)):
            error = HTTPException(502, f'{stage}时返回的数据无法解析，请重试提取')
        else:
            LOGGER.warning('Unexpected transcript failure for %s at %s (%s)', key, stage, type(exc).__name__)
            error = HTTPException(502, f'{stage}失败，请重试提取或手工补充逐字稿')
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = db.get(Record, key)
            if row and row.payload.get('token') == token:
                record_failure(row, error.detail)
                db.commit()
        raise error from None
    return text, user_id


def run_next_transcript(app):
    jobs = select(Record).where(Record.kind == 'material_transcript').order_by(Record.created_at, Record.id)
    with app.state.Session() as db:
        retry = db.scalar(jobs.where(Record.payload['status'].as_string() == 'completed',
                                     Record.payload['feishu']['status'].as_string() == 'pending',
                                     func.coalesce(Record.payload['sync_until'].as_float(), 0) <= time.time()).limit(1))
    if retry:
        sync_transcript(app, retry.id, retry.owner_id, retry.payload['material_id'], automatic=True)
        return True
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        active_search = db.scalar(select(Record.id).where(
            Record.kind == 'collection_runs', Record.payload['task'].as_string() == 'collection',
            Record.payload['status'].as_string().in_(('queued', 'running')),
            Record.payload['options']['keywords_list'].as_string().is_not(None),
        ).limit(1))
        if active_search:
            return False  # Keep the shared TikHub budget available to search and its comment collection.
        row = db.scalar(jobs.where(Record.payload['status'].as_string() == 'queued').limit(1))
        if not row:
            return False
        key, material_id = row.id, row.payload['material_id']
        try:
            prepared = prepare_transcript(db, row)
        except HTTPException as exc:
            record_failure(row, exc.detail)
            db.commit()
            return True
        db.commit()

    def audit(db, user, action, material_id):
        db.add(Record(kind='audit', owner_id=user.id,
                      payload={'actor': user.name, 'action': action, 'object_id': material_id}))

    try:
        _, user_id = execute_transcript(app, key, prepared, audit)
        sync_transcript(app, key, user_id, material_id, automatic=True)
    except HTTPException:
        pass  # The status endpoint retains the precise failure.
    return True


def register(app, audit, authenticated_user):
    @app.get('/api/materials/{key}/transcript')
    def transcript_status(key: str, request: Request):
        with app.state.Session() as db:
            material = require_material(db, authenticated_user(request, db), key)
            identity = supported_identity(material.payload)
            row = db.get(Record, transcript_key(material.id, identity)) if identity else None
            return progress_view(row.payload if row else {}, include_text=True)

    @app.post('/api/materials/{key}/transcript')
    def material_transcript(key: str, request: Request, background: bool = False):
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            user = authenticated_user(request, db)
            material = require_material(db, user, key)
            if not video_identity(material.payload):
                raise HTTPException(422, '素材缺少支持的公开视频来源，请手工补充对标逐字稿')
            identity = supported_identity(material.payload)
            if not identity:
                raise HTTPException(422, SUPPORTED_AUDIO_ERROR)
            row = db.get(Record, transcript_key(material.id, identity))
            status = progress_view(row.payload if row else {}, include_text=True)
            if status['status'] == 'completed':
                return {**status, 'source': 'speech_recognition'}
            if status['status'] in ('running', 'queued'):
                if background:
                    return JSONResponse(status, status_code=202)
                raise HTTPException(409, '此视频正在提取逐字稿，请稍后查看')
            row = enqueue_transcript(db, material, user.id, retry=True)
            cache_id = row.id
            if background:
                db.commit()
                return JSONResponse(progress_view(row.payload), status_code=202)
            prepared = prepare_transcript(db, row)
            db.commit()
        text, user_id = execute_transcript(app, cache_id, prepared, audit)
        feishu = sync_transcript(app, cache_id, user_id, key, automatic=True)
        return {'text': text, 'source': 'speech_recognition', 'cached': False, 'feishu': feishu}

    @app.post('/api/materials/{key}/transcript/feishu')
    def retry_transcript_sync(key: str, request: Request):
        with app.state.Session() as db:
            user = authenticated_user(request, db)
            material = require_material(db, user, key)
            identity = supported_identity(material.payload)
            if not identity:
                raise HTTPException(422, '素材来源不可用')
            user_id, cache_id = user.id, transcript_key(material.id, identity)
        return sync_transcript(app, cache_id, user_id, key)
