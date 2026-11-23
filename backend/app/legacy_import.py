"""Import public baokuan-space JSON archives; never read its config or auth DB.

Unknown observation dates prohibit replacing existing data. Stable platform identity
is scoped exactly like collection; comments without IDs use a local fingerprint.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from sqlalchemy import select
from .db import Record, now
from .discovery import TIERS, number, timestamp
from .demand_analysis import analyze_comment
from .integrations.collection import stable


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def platform_id(url, segment):
    parsed = urlparse(url or '')
    parts = parsed.path.strip('/').split('/')
    if parsed.scheme != 'https' or parsed.hostname not in ('www.douyin.com', 'douyin.com') or len(parts) != 2 or parts[0] != segment or not parts[1]:
        raise ValueError('存档必须包含有效的抖音主页/作品链接')
    return parts[1]


def import_archive(db, user, source, *, apply=False):
    if user.role != 'admin' or not user.active:
        raise ValueError('仅有效管理员可执行旧系统导入')
    path = Path(source).resolve()
    raw = path.read_bytes()
    if len(raw) > 50 * 1024 * 1024: raise ValueError('存档超过50MB')
    data = json.loads(raw)
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ('accounts', 'works', 'comments')):
        raise ValueError('存档结构无效')
    # Validate every relationship before staging any writes. Only selected public fields enter payloads.
    accounts, works = {}, {}
    account_ids = set()
    for a in data['accounts']:
        aid = platform_id(a.get('url'), 'user')
        if not a.get('name') or a['name'] in accounts or aid in account_ids: raise ValueError('博主身份重复或名称缺失')
        accounts[a['name']] = aid; account_ids.add(aid)
    for w in data['works']:
        wid = platform_id(w.get('video_url'), 'video')
        if not w.get('id') or w['id'] in works or w.get('account_name') not in accounts:
            raise ValueError('作品身份重复或来源博主缺失')
        works[w['id']] = wid
    for c in data['comments']:
        if c.get('work_id') not in works or not isinstance(c.get('text'), str): raise ValueError('评论来源作品缺失或正文无效')
    provenance = {'source_system': 'baokuan-space', 'industry': '保险', 'archive_path': str(path),
                  'archive_sha256': hashlib.sha256(raw).hexdigest(),
                  'archive_modified_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                  'observation_time_known': False}
    result = {'source': provenance, 'input': {k: len(data[v]) for k, v in [('authors','accounts'),('materials','works'),('leads','comments')]},
              'created': dict.fromkeys(('authors','materials','leads'), 0),
              'reused': dict.fromkeys(('authors','materials','leads'), 0), 'applied': apply}
    rows = list(db.scalars(select(Record).where(Record.kind.in_(('authors','materials','leads')), Record.ip_id.is_(None), Record.owner_id == user.id)))
    by_id = {r.id: r for r in rows}
    by_platform = {}
    for r in rows:
        if r.payload.get('platform') == '抖音' and r.payload.get('platform_id'):
            identity = (r.kind, str(r.payload['platform_id']))
            if identity in by_platform: raise ValueError('当前库存在同范围重复平台身份，请先核对')
            by_platform[identity] = r
    staged = {}
    def save(kind, identity, payload, *, provider=True):
        key = stable(kind, 'owner-' + user.id, identity)
        previous = by_platform.get((kind, identity)) if provider else None
        previous = previous or by_id.get(key) or staged.get(key)
        if previous:
            result['reused'][kind] += 1
            return previous.id
        p = {'status': 'new', **payload, 'legacy_source': provenance}
        row = Record(id=key, kind=kind, owner_id=user.id, payload=p)
        staged[key] = row; result['created'][kind] += 1
        if provider: by_platform[(kind, identity)] = row
        return key
    author_map, material_map, material_author, titles = {}, {}, {}, {}
    for a in data['accounts']:
        payload = {'title': a['name'], 'platform': '抖音', 'platform_id': accounts[a['name']], 'source': a['url'],
                   'tier': a.get('tier') if a.get('tier') in TIERS else '未分层', 'enabled': True, 'is_own': False,
                   'followers': number(a.get('fans')), 'works_total': number(a.get('works_total')),
                   'note': '保险行业历史存档；数据截至存档时，不代表实时值。',
                   'legacy_baseline': {'median_likes': number(a.get('median_base')), 'sample_window': '旧采集窗口，样本数未知', 'use_for_scoring': False}}
        author_map[a['name']] = save('authors', accounts[a['name']], payload)
    for w in data['works']:
        author = author_map[w['account_name']]
        title = w.get('title') or w.get('desc') or '历史作品'
        payload = {'title': title, 'body': w.get('desc') or title, 'platform': '抖音', 'platform_id': works[w['id']],
                   'author_id': author, 'source': w['video_url'], 'video_url': w['video_url'], 'published_at': timestamp(w.get('publish_time')),
                   'duration': number(w.get('duration')), 'likes': number(w.get('likes')), 'comments_count': number(w.get('comments')),
                   'collects': number(w.get('collects')), 'shares': number(w.get('shares')), 'content_type': w.get('type'),
                   'legacy_work_id': w['id'], 'comments_status': 'legacy_partial', 'collected_comments': sum(c['work_id']==w['id'] for c in data['comments'])}
        material_map[w['id']] = save('materials', works[w['id']], payload)
        material_author[w['id']] = author; titles[w['id']] = title
    for c in data['comments']:
        # Source has neither comment ID nor commenter ID. Never invent provider identities.
        identity = 'legacy-comment-' + digest([works[c['work_id']], c.get('user'), c['text']])
        payload = {'title': c.get('user') or '历史评论用户', 'body': c['text'], 'likes': number(c.get('likes')),
                   'platform': '抖音', 'material_id': material_map[c['work_id']], 'author_id': material_author[c['work_id']],
                   'work_title': titles[c['work_id']], 'source': 'https://www.douyin.com/video/' + works[c['work_id']],
                   'source_type': 'platform_comment', 'contactable': False, 'legacy_comment_fingerprint': identity,
                   'comment_scope': 'legacy_unknown', **analyze_comment(c['text'])}
        save('leads', identity, payload, provider=False)
    if apply:
        db.add_all(staged.values())
        db.add(Record(kind='audit', owner_id=user.id, payload={'actor': user.username, 'action': '导入保险行业历史存档',
                      'archive_sha256': provenance['archive_sha256'], 'created': result['created'], 'reused': result['reused']}))
        db.flush()
    return result
