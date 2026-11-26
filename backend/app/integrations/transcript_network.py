"""Read only the media addresses returned by the current TikHub request."""
from collections.abc import Iterator
from itertools import zip_longest

from fastapi import HTTPException
import httpx

from . import tikhub
from .common import IntegrationError


def object_value(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def url_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        yield from (url for url in value if isinstance(url, str))


def douyin_detail(data: dict) -> dict:
    return object_value(data.get('aweme_detail')) or object_value(data.get('aweme_info'))


def reject_filtered_douyin(data: dict) -> None:
    if not douyin_detail(data) and data.get('filter_detail'):
        raise HTTPException(422, '原视频不可公开访问，请手工补充逐字稿')


def douyin_body_after_error(response: httpx.Response) -> dict:
    # TikHub documents that filtered works can arrive in a successful HTTP
    # response with a nonzero upstream status and a filter_detail.
    if response.status_code != 200 or len(response.content) > 12_000_000:
        return {}
    try:
        payload = object_value(response.json())
    except ValueError:
        return {}
    return object_value(payload.get('data')) if payload.get('code', 200) == 200 else {}


def douyin_urls(data: dict) -> Iterator[str]:
    reject_filtered_douyin(data)
    detail = douyin_detail(data)
    status = object_value(detail.get('status'))
    if status.get('is_private') or status.get('is_delete'):
        raise HTTPException(422, '原视频不可公开访问，请手工补充逐字稿')
    video = object_value(detail.get('video'))

    def playback_urls(entry: dict) -> Iterator[str]:
        for key in ('play_addr_h264', 'play_addr', 'play_addr_265', 'download_addr'):
            yield from url_values(object_value(entry.get(key)).get('url_list'))

    def known_bitrate(entry):
        value = str(entry.get('bit_rate') or '')
        return int(value) if value.isascii() and value.isdigit() and len(value) <= 10 else 0

    bitrates = video.get('bit_rate')
    variants = [object_value(entry) for entry in bitrates] if isinstance(bitrates, list) else []
    low_first = sorted((entry for entry in variants if known_bitrate(entry)), key=known_bitrate)
    preferred = next((urls for entry in low_first if (urls := list(playback_urls(entry)))), [])
    # Keep a copy of the original stream among the first candidates. A low
    # bitrate stream can download successfully while containing no usable audio.
    for low, original in zip_longest(preferred, playback_urls(video)):
        if low:
            yield low
        if original:
            yield original
    for entry in variants:
        yield from playback_urls(entry)


def xhs_video_urls(detail: dict) -> Iterator[str]:
    pending = [(detail, False)]
    while pending:
        item, in_video = pending.pop()
        if isinstance(item, list):
            pending.extend((entry, in_video) for entry in reversed(item))
        elif isinstance(item, dict):
            if in_video:
                for key in ('master_url', 'masterUrl', 'play_url', 'playUrl', 'video_url', 'videoUrl', 'url',
                            'backup_url', 'backup_urls', 'backupUrl', 'backupUrls'):
                    yield from url_values(item.get(key))
            pending.extend(
                (value, in_video or key in ('media', 'video', 'video_info', 'stream'))
                for key, value in reversed(tuple(item.items())) if isinstance(value, (dict, list))
            )


def platform_media_candidates(response: httpx.Response, platform: str) -> list[str]:
    try:
        data = tikhub.unwrap(response)
    except IntegrationError:
        if platform == '抖音':
            reject_filtered_douyin(douyin_body_after_error(response))
        raise

    if platform == '抖音':
        return list(douyin_urls(data))
    if platform == '小红书':
        return list(xhs_video_urls(data))
    media = object_value(data.get('media'))
    if media.get('decode_key'):
        raise HTTPException(422, '视频号返回加密媒体，请手工补充对标逐字稿')
    full_url = media.get('full_url')
    if isinstance(full_url, str) and full_url:
        return [full_url]
    url, token = media.get('url'), media.get('url_token') or ''
    return [url + token] if isinstance(url, str) and isinstance(token, str) else []
