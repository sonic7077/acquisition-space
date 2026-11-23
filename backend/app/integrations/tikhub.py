"""Paths/parameters verified against docs.tikhub.io (see docs/references/TikHub)."""
from .common import IntegrationError

BASE = 'https://api.tikhub.io'
SEARCH = '/api/v1/douyin/search/fetch_video_search_v1'
COMMENTS = '/api/v1/douyin/web/fetch_video_comments'
PROFILE = '/api/v1/douyin/web/fetch_user_profile_by_uid'


def unwrap(response):
    if response.status_code != 200:
        raise IntegrationError(f'TikHub 返回 HTTP {response.status_code}；请核对配置或额度，未自动重试')
    try:
        if len(response.content) > 12_000_000:
            raise ValueError()
        payload = response.json()
        if payload.get('code', 200) != 200:
            raise IntegrationError(f'TikHub 业务错误（代码 {int(payload.get("code", -1))}）；未自动重试')
        data = payload['data']
        if not isinstance(data, dict) or data.get('status_code', 0) != 0:
            raise ValueError()
        return data
    except IntegrationError:
        raise
    except Exception:
        raise IntegrationError('TikHub 返回格式无效或平台状态异常；未自动重试') from None


def url(value):
    values = (value or {}).get('url_list', [])
    return next((v for v in values if isinstance(v, str) and v.startswith('https://')), '')


def number(value):
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError):
        return 0


def author(data):
    key = str(data.get('uid') or data.get('sec_uid') or '')
    return {'platform': '抖音', 'platform_id': key, 'sec_uid': data.get('sec_uid', ''),
            'title': data.get('nickname') or '未命名博主', 'body': data.get('signature', ''),
            'followers': data.get('follower_count'), 'avatar': url(data.get('avatar_thumb')),
            'source': 'https://www.douyin.com/user/' + str(data.get('sec_uid')) if data.get('sec_uid') else '',
            'source_type': 'tikhub', 'collected_at': None}


def material(data):
    key = str(data.get('aweme_id') or '')
    stats, video = data.get('statistics') or {}, data.get('video') or {}
    return {'platform': '抖音', 'platform_id': key, 'title': data.get('desc') or '无标题作品',
            'body': data.get('desc') or '', 'source': f'https://www.douyin.com/video/{key}',
            'author': (data.get('author') or {}).get('nickname', '未知作者'),
            'duration': number(video.get('duration')) / 1000, 'cover': url(video.get('cover')),
            'published_at': data.get('create_time'), 'likes': number(stats['digg_count']) if stats.get('digg_count') is not None else None,
            'comments_count': number(stats.get('comment_count')), 'collects': number(stats.get('collect_count')),
            'shares': number(stats.get('share_count')), 'plays': stats.get('play_count'),
            'source_type': 'tikhub', 'unavailable': bool((data.get('status') or {}).get('is_delete') or (data.get('status') or {}).get('is_private'))}
