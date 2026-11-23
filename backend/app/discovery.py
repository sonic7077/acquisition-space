"""Pure, explainable discovery rules; no provider requests or credentials."""
from datetime import datetime, timezone
import math
import statistics
from fastapi import HTTPException
from .db import as_record

DEFAULT_RULES = {
    'denom_floor': 100, 'min_mult': 3, 'min_likes_candidate': 0,
    'likes_high': 1000, 'likes_mid': 300, 'low_fan_min_likes': 500, 'low_fan_ratio': 3,
    'traffic_high': .05, 'traffic_mid': .02, 'min_comments': 20,
    'rise_delta': 300, 'rise_new_likes': 1000, 'rise_mult': 3, 'rise_hours': 48,
    'stale_days': 180, 'version': 1,
}
INTEGER_RULES = {'min_likes_candidate', 'likes_high', 'likes_mid', 'low_fan_min_likes',
                 'min_comments', 'rise_delta', 'rise_new_likes', 'rise_hours', 'stale_days'}
AUTHOR_FIELDS = {'enabled', 'is_own', 'tier', 'note'}
TIERS = ('未分层', '头部同行', '低粉黑马', '跨界结构')


def number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def timestamp(value):
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        return number(value)
    except (ValueError, TypeError, OverflowError):
        return None


def rules_for(settings):
    return {**DEFAULT_RULES, **settings.get('discovery', {})}


def validate_rules(patch, previous):
    result = {**DEFAULT_RULES, **previous}
    for key, value in patch.items():
        if key not in DEFAULT_RULES or key == 'version':
            raise HTTPException(422, '未知作品推荐规则字段')
        if number(value) is None or value > 100000000 or (key in INTEGER_RULES and value != int(value)):
            raise HTTPException(422, '作品推荐阈值须为有效非负数字，计数与时间须为整数')
        if key in ('stale_days', 'rise_hours') and value < 1:
            raise HTTPException(422, '保鲜天数和起量小时数至少为1')
        result[key] = value
    if not 0 <= result['traffic_mid'] <= result['traffic_high'] <= 1 or result['likes_mid'] > result['likes_high']:
        raise HTTPException(422, '评赞比须在0至1之间，且高档阈值不能小于中档')
    if any(result[k] != previous.get(k, DEFAULT_RULES[k]) for k in patch):
        result['version'] = previous.get('version', 1) + 1
    return result


def author_patch(data):
    patch = {k: v for k, v in data.items() if k in AUTHOR_FIELDS}
    for key in ('enabled', 'is_own'):
        if key in patch and not isinstance(patch[key], bool):
            raise HTTPException(422, '博主启用和本人标记必须是布尔值')
    if 'tier' in patch and patch['tier'] not in TIERS:
        raise HTTPException(422, '博主分层无效')
    if 'note' in patch and (not isinstance(patch['note'], str) or len(patch['note']) > 2000):
        raise HTTPException(422, '备注最多2000字')
    return patch


def snapshot_patch(previous, incoming):
    """Ignore stale observations, retain a bounded series, never invent prior likes."""
    patch = dict(incoming)
    t = timestamp(patch.get('collected_at'))
    if t is None or number(patch.get('likes')) is None:
        return patch
    history = list(previous.get('interaction_snapshots', []))
    last = timestamp(history[-1].get('at')) if history else timestamp(previous.get('collected_at'))
    if last is not None and t <= last:
        for key in ('likes', 'comments_count', 'collects', 'shares', 'plays', 'collected_at'):
            patch.pop(key, None)
        return patch
    # Existing observations are valid evidence even if created before snapshots existed.
    if not history and last is not None and number(previous.get('likes')) is not None:
        history.append({'at': previous['collected_at'], 'likes': previous['likes'], 'comments_count': previous.get('comments_count')})
    history.append({'at': patch['collected_at'], **{k: patch.get(k) for k in ('likes', 'comments_count', 'collects', 'shares', 'plays')}})
    patch['interaction_snapshots'] = history[-30:]
    return patch


def score_material(p, author, rules, at=None):
    r = {**DEFAULT_RULES, **rules}
    a = author or {}
    current = (at or datetime.now(timezone.utc)).timestamp()
    likes, comments, fans = number(p.get('likes')), number(p.get('comments_count')), number(a.get('followers'))
    base, count = number(p.get('author_sample_median_likes')), number(p.get('author_sample_count')) or 0
    denom = max(base, r['denom_floor']) if count >= 3 and base is not None and base > 0 else None
    mult = likes / denom if likes is not None and denom else None
    low_ratio = likes / fans if likes is not None and fans else None
    low_hit = low_ratio is not None and likes > r['low_fan_min_likes'] and low_ratio > r['low_fan_ratio']
    ratio = comments / likes if comments is not None and likes else None
    enough_comments = comments is not None and comments >= r['min_comments']
    high_ratio = bool(enough_comments and ratio is not None and ratio >= r['traffic_high'])
    ratio_level = '未知' if ratio is None else '评论样本不足' if not enough_comments else '高' if high_ratio else '中' if ratio >= r['traffic_mid'] else '低'
    published = timestamp(p.get('published_at'))
    age = current - published if published is not None and published > 0 else None
    freshness = 'unknown' if age is None else 'future' if age < 0 else 'stale' if age > r['stale_days'] * 86400 else 'fresh'
    delta, interval = None, None
    history = p.get('interaction_snapshots') or []
    if len(history) >= 2:
        old, new = history[-2:]
        start, end = timestamp(old.get('at')), timestamp(new.get('at'))
        before, after = number(old.get('likes')), number(new.get('likes'))
        if start is not None and end is not None and end > start and before is not None and after is not None:
            delta, interval = after-before, end-start
    new_work = age is not None and 0 <= age <= r['rise_hours'] * 3600
    rising = bool(delta is not None and delta >= r['rise_delta'] or new_work and (likes is not None and likes >= r['rise_new_likes'] or mult is not None and mult >= r['rise_mult']))
    reasons = []
    if mult is not None and mult >= r['min_mult'] and likes >= r['min_likes_candidate']:
        reasons.append('超基线倍数达标')
    if low_hit:
        reasons.append('低粉高赞')
    if high_ratio:
        reasons.append('高评赞比')
    if rising:
        reasons.append('起量')
    excluded = '博主已停用' if a.get('enabled') is False else '本人账号' if a.get('is_own') else '素材已排除' if p.get('status') == 'excluded' else '作品不可用' if p.get('unavailable') else '超过保鲜期' if freshness == 'stale' else '发布时间待核实' if freshness != 'fresh' else ''
    reference = .45*math.log2(1+(mult or 0)) + .35*min((ratio or 0)*100, 15)/3 + .2*int(rising)
    return {'version': r['version'], 'multiplier': mult, 'denominator': denom, 'baseline': base, 'sample_count': count,
            'like_level': '未知' if likes is None else '高赞' if likes > r['likes_high'] else '普通' if likes > r['likes_mid'] else '低赞',
            'low_fan_ratio': low_ratio, 'low_fan_hit': low_hit, 'comment_ratio': ratio, 'comment_ratio_level': ratio_level,
            'high_comment_ratio': high_ratio, 'likes_delta': delta, 'observation_seconds': interval, 'rising': rising,
            'freshness': freshness, 'reasons': reasons, 'excluded_reason': excluded,
            'recommended': bool(reasons and not excluded),
            'candidate': bool(reasons and not excluded and p.get('status') != 'adopted' and not p.get('adopted')),
            'reference_score': round(reference, 4)}


def discovery_records(materials, authors, rules):
    """Call only with permission-filtered rows. Samples cannot cross owner/platform scope."""
    author_data, material_data = {}, []
    author_rows = {a.id: a for a in authors}
    samples = {}
    for m in materials:
        samples.setdefault((m.payload.get('author_id'), m.ip_id, m.owner_id if not m.ip_id else None, m.payload.get('platform')), []).append(m)
    for a in authors:
        sample = samples.get((a.id, a.ip_id, a.owner_id if not a.ip_id else None, a.payload.get('platform')), [])
        valid = [m.payload['likes'] for m in sample if number(m.payload.get('likes')) is not None]
        author_data[a.id] = {**as_record(a), 'enabled': a.payload.get('enabled', True), 'is_own': a.payload.get('is_own', False),
                             'tier': a.payload.get('tier', '未分层'), 'work_count': len(sample),
                             'sample_count': len(valid), 'sample_median_likes': statistics.median(valid) if len(valid) >= 3 else None}
    for m in materials:
        author_row = author_rows.get(m.payload.get('author_id'))
        same_scope = author_row is not None and author_row.ip_id == m.ip_id and author_row.payload.get('platform') == m.payload.get('platform') and (m.ip_id or author_row.owner_id == m.owner_id)
        a = author_data.get(author_row.id) if same_scope else None
        p = {**as_record(m), 'author_sample_count': a['sample_count'] if a else 0, 'author_sample_median_likes': a['sample_median_likes'] if a else None}
        material_data.append({**p, 'discovery': score_material(p, a, rules)})
    return material_data, list(author_data.values())
