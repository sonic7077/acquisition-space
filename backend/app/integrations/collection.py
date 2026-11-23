import hashlib
import statistics
import httpx
from sqlalchemy import select
from ..discovery import snapshot_patch, number
from ..demand_analysis import labels, analyze_comment
from ..db import Record, as_record, business_today, now
from .common import config, client, claim, update, finish, queue, authorized, lease, IntegrationError
from . import tikhub, platforms


def stable(kind, ip_id, key, platform='douyin'):
    return kind + '-' + hashlib.sha256(f'{ip_id}:{platform}:{key}'.encode()).hexdigest()[:32]


def upsert(db, kind, ip_id, owner, platform_id, payload):
    platform = next((k for k, v in platforms.PLATFORMS.items() if v == payload.get('platform')), 'douyin')
    key = stable(kind, ip_id or ('owner-' + owner), platform_id, platform)
    row = db.get(Record, key)
    if kind == 'materials':
        payload = snapshot_patch(row.payload if row else {}, payload)
    if kind == 'authors' and payload.get('keyword'):
        previous = row.payload if row else {}
        discovered = payload.get('collected_at') or now()
        payload = {**payload, 'keywords': list(dict.fromkeys([*previous.get('keywords', []), payload['keyword']])),
                   'first_discovered_at': previous.get('first_discovered_at') or discovered, 'last_discovered_at': discovered}
    if row:
        # Refresh source data while retaining local decisions and annotations.
        if kind == 'materials' and payload.get('keyword'):
            payload = {**payload, 'keywords': list(dict.fromkeys([*row.payload.get('keywords', []), payload['keyword']]))}
        local = {k: v for k, v in row.payload.items() if k in ('status', 'intent', 'owner', 'next_followup', 'reply', 'reason', 'contactable', 'feishu_record_id', 'feishu_table_id', 'feishu_synced_at', 'manual_labels', 'demand_topics', 'demand_ips', 'enabled', 'is_own', 'tier', 'note')}
        if kind == 'leads':
            local['manual_labels'] = labels(row.payload)['manual_labels']
            for k in ('content_type', 'intent'):
                if k in labels(row.payload)['manual_labels']:
                    local[k] = row.payload[k]
            if 'intent' not in labels(row.payload)['manual_labels']:
                local.pop('intent', None)
        row.payload = {**row.payload, **payload, **local}
        if kind == 'leads':
            row.payload = {**row.payload, **labels(row.payload)}
        row.updated_at = now()
    else:
        if kind == 'materials' and payload.get('keyword'):
            payload = {**payload, 'keywords': [payload['keyword']]}
        row = Record(id=key, kind=kind, ip_id=ip_id, owner_id=owner, payload={'status': 'new', **payload})
        db.add(row)
    db.flush()
    return row


class Collector:
    def __init__(self, app, run):
        self.app, self.run = app, run
        self.settings = config(app, 'collection')
        if not self.settings.get('enabled') or not self.settings.get('api_key_encrypted'):
            raise IntegrationError('管理员尚未配置或启用 TikHub')
        self.key = app.state.secrets.decrypt(self.settings['api_key_encrypted'])
        self.http = client(app)

    def request(self, path, body=None, **params):
        # Reserve one call atomically before every paid request, including errors.
        with self.app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            job = db.get(Record, self.run['id'])
            if not authorized(db, job):
                raise IntegrationError('提交者权限已变更，停止采集')
            setting = db.get(Record, 'system-settings').payload.get('collection', {})
            if not setting.get('enabled') or setting.get('api_key_encrypted') != self.settings.get('api_key_encrypted'):
                raise IntegrationError('管理员已停用 TikHub 或变更凭证，停止采集')
            budget = int(setting.get('daily_budget') or 100)
            key = 'tikhub-budget-' + business_today()
            ledger = db.get(Record, key)
            used = ledger.payload.get('calls', 0) if ledger else 0
            if used >= budget:
                raise IntegrationError('TikHub 今日调用预算已耗尽；已取得的数据保留')
            if ledger:
                ledger.payload = {'calls': used + 1}
            else:
                db.add(Record(id=key, kind='integration_budget', payload={'calls': 1}))
            job.payload = {**job.payload, 'calls': job.payload.get('calls', 0) + 1, 'lease_until': lease()}
            db.commit()
        response = self.http.request('POST' if body is not None else 'GET', tikhub.BASE + path,
                                     headers={'Authorization': 'Bearer ' + self.key}, json=body, params=params or None)
        return tikhub.unwrap(response)

    def progress(self, **patch):
        update(self.app, self.run['id'], **patch)

    def comments(self, material, pages):
        state, seen = {}, set()
        platform=next((k for k,v in platforms.PLATFORMS.items() if v==material.get('platform')), 'douyin')
        for page in range(pages):
            entries, more, nxt=platforms.comments(self.request,platform,material,state)
            with self.app.state.Session() as db:
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                for c in entries:
                    cid=c['id']
                    if not cid:
                        continue
                    text=c['text']
                    upsert(db, 'leads', self.run['ip_id'], self.run['owner_id'], cid, {
                        'title': c['name'] or '平台评论用户', 'body': text,
                        'platform': platforms.PLATFORMS[platform], 'platform_comment_id': cid, 'commenter_id': c['user_id'],
                        'material_id': material['id'], 'author_id': material.get('author_id'),
                        'work_title': material.get('title'), 'source': material.get('source'),
                        'source_type': 'platform_comment', 'contactable': False, **analyze_comment(text), 'likes': c['likes'],
                        'published_at': c['time'], 'collected_at': now(), 'comment_scope': 'first_level',
                        **{k: c[k] for k in ('parent_comment_id', 'reply_count') if c.get(k) is not None}})
                count = sum(r.payload.get('material_id') == material['id'] for r in db.scalars(select(Record).where(Record.kind == 'leads', Record.ip_id == self.run['ip_id'])))
                row = db.get(Record, material['id'])
                row.payload = {**row.payload, 'collected_comments': count, 'comments_fetched_at': now(),
                               'comments_status': 'range_complete' if more and page+1==pages else 'completed',
                               'comments_error': None, 'comments_has_more': more}
                db.commit()
            self.tick('comments', len(entries))
            if not more:
                break
            token=str(nxt.get('cursor'))
            if nxt.get('cursor') is None or token in seen or nxt.get('cursor')==state.get('cursor',0):
                raise IntegrationError('评论分页游标重复或缺失，已停止以避免重复付费')
            seen.add(token)
            state=nxt

    def tick(self, section, count=1):
        with self.app.state.Session() as db:
            row = db.get(Record, self.run['id'])
            patch = {section: row.payload.get(section, 0) + count, 'processed': row.payload.get('processed', 0) + count}
        self.progress(**patch)

    def search(self, opts):
        errors=[]
        candidates, authors={},{}
        selected=list(dict.fromkeys(opts.get('platforms') or ['douyin']))
        for platform in selected:
            platform_materials={}
            seen_results={}
            attempts=0
            for keyword in opts['keywords_list']:
                word_attempts = 0
                state, seen={},set()
                for page in range(1,opts['pages']+1):
                    try:
                        items, more, nxt=platforms.search(self.request,platform,keyword,opts,page,state)
                        for raw in items:
                            # Dedupe repeated search hits before the paid Channels detail request.
                            raw_id=str(raw.get('aweme_id') or raw.get('id') or raw.get('note_id') or raw.get('exportId') or '')
                            if raw_id and raw_id in seen_results:
                                cached=seen_results[raw_id]
                                if cached:
                                    with self.app.state.Session() as db:
                                        upsert(db,'materials',self.run['ip_id'],self.run['owner_id'],cached['platform_id'],{'platform':cached['platform'],'keyword':keyword})
                                        if cached.get('author_id'):
                                            author = db.get(Record, cached['author_id'])
                                            if author:
                                                upsert(db, 'authors', self.run['ip_id'], self.run['owner_id'], author.payload['platform_id'], {'platform': cached['platform'], 'keyword': keyword})
                                        db.commit()
                                continue
                            if attempts>=opts['max_videos'] or word_attempts>=opts.get('per_word_limit', 20):
                                break
                            attempts+=1
                            word_attempts+=1
                            if raw_id:seen_results[raw_id]=None
                            try:
                                m,a=platforms.normalize(self.request,platform,raw)
                                if not m or not m.get('platform_id'):continue
                                with self.app.state.Session() as db:
                                    db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                                    author_row=None
                                    if a and a['platform_id']:
                                        a.update({'collected_at': now(), 'keyword': keyword})
                                        author_row=upsert(db,'authors',self.run['ip_id'],self.run['owner_id'],a['platform_id'],a)
                                        authors[author_row.id]=as_record(author_row)
                                    m.update({'author_id':author_row.id if author_row else None,'keyword':keyword,'collected_at':now(),
                                              'eligible':(m['likes'] or 0)>=opts['min_likes'] and (m['duration'] or 0)>=opts['min_duration'] and not m['unavailable'],
                                              'comments_status':'pending'})
                                    row=upsert(db,'materials',self.run['ip_id'],self.run['owner_id'],m['platform_id'],m)
                                    material=as_record(row)
                                    candidates[row.id]=material;platform_materials[row.id]=material
                                    if raw_id:seen_results[raw_id]=material
                                    db.commit()
                                self.progress(materials=len(candidates),authors=len(authors),processed=len(candidates),progress=f"{platforms.PLATFORMS[platform]} · {keyword}：第 {page} 页，自动采集评论")
                                # Every admitted work gets comments, including low-like/short works.
                                try:
                                    self.comments(material,opts['comment_pages'])
                                except (IntegrationError,httpx.HTTPError) as exc:
                                    error=str(exc) if isinstance(exc,IntegrationError) else '评论请求连接失败或超时，未自动重试'
                                    with self.app.state.Session() as db:
                                        row=db.get(Record,material['id'])
                                        row.payload={**row.payload,'comments_status':'failed','comments_error':error}
                                        db.commit()
                                    errors.append(f"{platforms.PLATFORMS[platform]}作品评论：{error}")
                            except (IntegrationError,httpx.HTTPError) as exc:
                                errors.append(platforms.PLATFORMS[platform]+'作品：'+(str(exc) if isinstance(exc,IntegrationError) else '连接失败或超时'))
                        if not more or attempts>=opts['max_videos'] or word_attempts>=opts.get('per_word_limit', 20):
                            break
                        token=str(nxt.get('cursor'))
                        if nxt.get('cursor') is None or token in seen or nxt.get('cursor')==state.get('cursor',0):
                            raise IntegrationError('搜索分页游标重复或缺失，已停止以避免重复付费')
                        seen.add(token);state=nxt
                    except (IntegrationError,httpx.HTTPError) as exc:
                        errors.append(platforms.PLATFORMS[platform]+'搜索：'+(str(exc) if isinstance(exc,IntegrationError) else '连接失败或超时，未自动重试'))
                        break
                if attempts>=opts['max_videos']:break
        if opts.get('enrich_authors'):
            for a in [a for a in authors.values() if a.get('platform')=='抖音'][:opts['max_videos']]:
                try:
                    data=self.request(tikhub.PROFILE,uid=a['platform_id']);raw=data.get('user')
                    if not isinstance(raw,dict):raise IntegrationError('TikHub作者详情格式无效')
                    with self.app.state.Session() as db:
                        upsert(db,'authors',self.run['ip_id'],self.run['owner_id'],a['platform_id'],{**tikhub.author(raw),'collected_at':now()});db.commit()
                except (IntegrationError,httpx.HTTPError) as exc:
                    errors.append('抖音作者详情：'+(str(exc) if isinstance(exc,IntegrationError) else '连接失败或超时'))
        if errors:
            raise IntegrationError('；'.join(dict.fromkeys(errors))[:2000])


def refresh_baselines(app, ip_id):
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        mats = list(db.scalars(select(Record).where(Record.kind == 'materials', Record.ip_id == ip_id)))
        for a in db.scalars(select(Record).where(Record.kind == 'authors', Record.ip_id == ip_id)):
            sample = [m for m in mats if m.payload.get('author_id') == a.id and m.payload.get('platform') == a.payload.get('platform') and (ip_id or m.owner_id == a.owner_id)]
            valid = [m.payload['likes'] for m in sample if number(m.payload.get('likes')) is not None]
            median = statistics.median(valid) if len(valid) >= 3 else None
            a.payload = {**a.payload, 'sample_count': len(valid), 'sample_median_likes': median}
            for m in sample:
                m.payload = {**m.payload, 'author_sample_count': len(valid), 'author_sample_median_likes': median}
        db.commit()


def run_collection_job(app, key):
    run = claim(app, key, 'collection')
    if not run:
        return
    collector = None
    try:
        collector = Collector(app, run)
        opts = run['options']
        if opts.get('material_id'):
            with app.state.Session() as db:
                material = as_record(db.get(Record, opts['material_id']))
            collector.comments(material, opts['pages'])
        else:
            collector.search(opts)
        refresh_baselines(app, run['ip_id'])
        finish(app, key)
    except IntegrationError as exc:
        finish(app, key, str(exc))
    except httpx.HTTPError:
        finish(app, key, 'TikHub 连接失败或超时；未自动重试，请核对调用记录')
    except Exception:
        finish(app, key, '采集发生异常，请管理员核对接口响应；已取得的数据保留')
    finally:
        if collector:
            collector.http.close()
    # Feishu errors are independent of collection status, never re-run paid search.
    cfg = config(app, 'feishu')
    with app.state.Session() as db:
        row = db.get(Record, key)
        if cfg.get('enabled') and row.payload.get('processed', 0):
            sync = queue(db, run['owner_id'], run['ip_id'], 'feishu_sync', {'collection_id': key})
            row.payload = {**row.payload, 'sync_run_id': sync['id']}
            db.commit()
