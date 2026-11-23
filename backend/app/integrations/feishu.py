"""Schema-aware, idempotent three-table synchronization into the user's Base."""
import json
import httpx
from sqlalchemy import select
from ..db import Record, User, as_record, now
from ..discovery import timestamp
from ..knowledge import shared_documents
from ..security import active_ip
from .feishu_target import resolve
from .common import config, client, claim, finish, update, authorized, IntegrationError

ROOT = 'https://open.feishu.cn/open-apis'
TABLE_NAMES = {'authors': '博主', 'works': '作品', 'comments': '评论'}
KINDS = {'authors': 'authors', 'works': 'materials', 'comments': 'leads'}
# Only mapped editable fields are written. Formula/lookup/system fields are excluded.
ALIASES = {
    'local_id': ['获客空间ID'], 'structure': ['内容结构', '获客空间·内容结构'],
    'title': ['标题', '作品标题', '视频标题', '博主名称', '博主', '昵称', '评论用户', '评论者'],
    'body': ['正文', '作品描述', '文案', '简介', '评论内容', '评论正文'], 'platform_id': ['平台ID', '作品ID', '视频ID', '博主ID'],
    'platform_comment_id': ['评论ID', 'cid'], 'source': ['原始链接', '作品链接', '主页链接', '来源链接'],
    'followers': ['粉丝数', '粉丝量', '粉丝'], 'likes': ['点赞数', '点赞量', '点赞'], 'comments_count': ['评论数'], 'collects': ['收藏数', '收藏'],
    'shares': ['分享数', '转发数', '分享', '转发'], 'duration': ['时长秒'], 'keyword': ['关键词'], 'published_at': ['发布时间', '评论时间'],
    'author_id': ['博主关联', '所属博主', '博主'], 'material_id': ['作品关联', '所属作品', '作品'],
    'content_type': ['评论类型', '内容类型'], 'analysis_basis': ['判定依据', '标签依据'],
    'analysis_version': ['规则版本'], 'manual_labels': ['人工覆盖'], 'owner': ['负责人'],
    'collected_at': ['采集时间'], 'commenter_id': ['评论用户ID'],
    'comment_scope': ['评论范围'], 'parent_comment_id': ['父评论ID'], 'reply_count': ['回复数'],
    'platform': ['平台'], 'platform_label': ['获客空间·来源平台'], 'sec_uid': ['sec_uid'],
    'cover': ['封面'], 'sample_median_likes': ['近30条中位点赞'],
    'intent': ['意向分类'], 'contactable': ['可跟进'], 'ip_id': ['获客空间·IP'],
}
MANAGED = {'获客空间ID': 1, '内容结构': 1, '获客空间·IP': 1, '获客空间·来源平台': 1}


class SyncConflict(IntegrationError):
    def __init__(self, table, row, differences):
        super().__init__(f'飞书{table["name"]}存在编辑冲突（本地ID {row.id}）；已暂停，请核对差异并协调后重试')
        self.details = {'id': row.id, 'title': row.payload.get('title'), 'table': table['name'], 'differences': differences}


def comparable(value, field):
    if field['type']==1 and value is None:
        return ''
    if field['type'] == 1 and isinstance(value, list):
        return ''.join(v.get('text', '') for v in value if isinstance(v, dict))
    if field['type']==15 and isinstance(value,dict):
        return {key:value.get(key) for key in ('link','text')}
    if field['type'] in (18, 21):
        if value is None:
            return []
        if isinstance(value, dict):
            ids=value.get('link_record_ids')
            return sorted(ids) if isinstance(ids,list) and all(isinstance(v,str) for v in ids) else value
        if isinstance(value,list):
            ids=[]
            for item in value:
                if isinstance(item,str):
                    ids.append(item)
                elif isinstance(item,dict) and isinstance(item.get('record_ids'),list):
                    ids.extend(item['record_ids'])
                elif isinstance(item,dict) and isinstance(item.get('record_id'),str):
                    ids.append(item['record_id'])
                else:
                    # Some list endpoints omit IDs; never treat unverified display text as equal.
                    return value
            return sorted(ids) if all(isinstance(v,str) for v in ids) else value
    return value


class Feishu:
    def __init__(self, app, cfg=None, before=None):
        self.app, self.cfg, self.before = app, cfg or config(app, 'feishu'), before
        self.cli_mode = self.cfg.get('auth_mode') == 'cli'
        if not self.cli_mode and (not self.cfg.get('app_id') or not self.cfg.get('app_secret_encrypted')):
            raise IntegrationError('管理员尚未配置飞书 App ID / App Secret')
        self.http = client(app)
        self.token = None
        try:
            if not self.cli_mode:
                result = self.request('POST', '/auth/v3/tenant_access_token/internal', {
                    'app_id': self.cfg['app_id'], 'app_secret': app.state.secrets.decrypt(self.cfg['app_secret_encrypted'])}, auth=False)
                self.token = result.get('tenant_access_token')
                if not self.token:
                    raise IntegrationError('飞书应用授权失败，请核对权限与凭证')
            self.mapping = json.loads(self.cfg.get('field_mapping') or '{}')
        except Exception:
            self.http.close()
            raise

    def request(self, method, path, data=None, auth=True, **params):
        if self.before:
            self.before()
        if self.cli_mode:
            from .feishu_cli import request
            return request(method, path, data, params)
        try:
            r = self.http.request(method, ROOT + path, json=data, params=params or None,
                                  headers={'Authorization': 'Bearer ' + self.token} if auth else {})
            if r.status_code != 200:
                raise IntegrationError(f'飞书返回 HTTP {r.status_code}；请核对应用权限')
            if len(r.content) > 12_000_000:
                raise IntegrationError('飞书返回过大，已停止')
            result = r.json()
            if result.get('code') != 0:
                raise IntegrationError(f'飞书业务错误（代码 {int(result.get("code", -1))}）；请核对Base授权、字段和应用权限')
            return result.get('data', {}) if auth else result
        except IntegrationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            # Never expose upstream bodies/headers, which may echo credentials.
            raise IntegrationError('飞书请求结果不确定，请核对远端记录后手动重试；重试将先按获客空间ID核对') from None

    def target(self):
        if not hasattr(self, '_target'):
            self._target = resolve(self)
        return self._target

    def path(self, tid=None):
        target = self.target()
        if target['type'] != 'bitable':
            raise IntegrationError('此链接是云文档或其他类型，不能当作博主/作品/评论多维表格。请填写实际base/wiki多维表格链接')
        root = f"/bitable/v1/apps/{target['token']}/tables"
        return root + '/' + tid if tid else root

    def items(self, path):
        items, token, seen = [], '', set()
        for _ in range(100):
            data = self.request('GET', path, page_size=100, **({'page_token': token} if token else {}))
            items.extend(data.get('items') or [])
            if not data.get('has_more'):
                return items
            token = data.get('page_token')
            if not token or token in seen:
                break
            seen.add(token)
        raise IntegrationError('飞书分页超过范围或游标异常，已停止')

    def schema(self):
        available = self.items(self.path())
        tables, warnings = {}, []
        for key, name in TABLE_NAMES.items():
            tid = self.cfg.get(key + '_table_id')
            matches = [t for t in available if (t['table_id'] == tid if tid else t['name'] == name)]
            if len(matches) != 1:
                warnings.append(f'无法唯一识别{name}表，请管理员填写表ID')
                continue
            table = dict(matches[0])
            table['fields'] = self.items(self.path(table['table_id']) + '/fields')
            mapping = self.mapping.get(key, {})
            table['mapping'] = {}
            for local, aliases in ALIASES.items():
                explicit = mapping.get(local)
                found = next((f for f in table['fields'] if (explicit in (f['field_name'], f['field_id']) if explicit else f['field_name'] in aliases)), None)
                if found and found['type'] in (1, 2, 3, 4, 5, 7, 15, 18, 21):
                    table['mapping'][local] = found
            unique = table['mapping'].get('local_id')
            structure = table['mapping'].get('structure')
            if not unique or unique['type'] != 1 or not structure or structure['type'] != 1:
                warnings.append(f'{name}缺少文本字段：获客空间ID、内容结构；请检查并准备同步字段')
            tables[key] = table
        return {'target': self.target(), 'base_token': self.target()['token'], 'tables': tables, 'available_tables': available, 'warnings': warnings, 'ready': not warnings}

    def prepare(self):
        schema = self.schema()
        if len(schema['tables']) != 3:
            raise IntegrationError('请先配置三张目标表的名称或ID；不会自动新建或替换现有表')
        for table in schema['tables'].values():
            names = {f['field_name']: f['type'] for f in table['fields']}
            for name, kind in MANAGED.items():
                if name not in names:
                    self.request('POST', self.path(table['table_id']) + '/fields', {'field_name': name, 'type': kind})
                elif names[name] != kind:
                    raise IntegrationError(f'现有字段{name}类型不兼容，请调整映射；未改动原字段')
        return self.schema()

    def fields(self, table, row, refs):
        data = as_record(row)
        # Complete normalized structure, including local and remote relation IDs.
        data = {k: v for k, v in data.items() if not k.startswith('feishu_')}
        data['relations'] = {k: refs.get(data.get(k)) for k in ('author_id', 'material_id') if data.get(k)}
        data['platform_label']={'douyin':'抖音','xiaohongshu':'小红书','wechat_channels':'视频号'}.get(data.get('platform'),data.get('platform'))
        data['local_id'] = row.id
        data['structure'] = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        fields = {}
        for local, field in table['mapping'].items():
            value, kind = data.get(local), field['type']
            if local=='platform':
                value=data.get('platform_label')
            if value is None:
                continue
            name = field['field_name']
            if kind == 1:
                fields[name] = str(value)
            elif kind == 2 and isinstance(value, (int, float)) and not isinstance(value, bool):
                fields[name] = value
            elif kind == 5:
                seconds = timestamp(value)
                if seconds is not None and seconds > 0:
                    fields[name] = int(seconds * 1000)
            elif kind == 7 and isinstance(value, bool):
                fields[name] = value
            elif kind == 15 and isinstance(value, str) and value.startswith('https://'):
                fields[name] = {'text': '原始链接', 'link': value}
            elif kind in (3, 4):
                options = {o['name'] for o in field.get('property', {}).get('options', [])}
                if str(value) in options:
                    fields[name] = str(value) if kind == 3 else [str(value)]
            elif kind in (18, 21) and local in ('author_id', 'material_id'):
                target = refs.get(value)
                if target and field.get('property', {}).get('table_id') == target['table_id']:
                    fields[name] = [target['record_id']]
        return fields

    def upsert(self, table, row, refs):
        path = self.path(table['table_id']) + '/records'
        unique = table['mapping']['local_id']['field_name']
        result = self.request('POST', path + '/search', {'filter': {'conjunction': 'and', 'conditions': [
            {'field_name': unique, 'operator': 'is', 'value': [row.id]}]}}, page_size=100)
        existing = result.get('items') or []
        if len(existing) > 1 or result.get('has_more'):
            raise IntegrationError('飞书唯一ID有重复，请先人工核对重复行')
        fields = self.fields(table, row, refs)
        if existing:
            previous = row.payload.get('feishu_snapshot', {}) if row.payload.get('feishu_table_id') == table['table_id'] else {}
            remote = existing[0].get('fields') or {}
            differences = []
            for field in table['mapping'].values():
                name = field['field_name']
                if name not in fields:
                    continue
                baseline = previous.get(name, fields[name])
                actual = comparable(remote.get(name), field)
                if actual != comparable(baseline, field):
                    differences.append({'field': name, 'last_sync': baseline, 'local': fields[name], 'feishu': actual})
            if differences:
                raise SyncConflict(table, row, differences)
            result = self.request('PUT', path + '/' + existing[0]['record_id'], {'fields': fields})
        else:
            result = self.request('POST', path, {'fields': fields})
        rid = (result.get('record') or {}).get('record_id')
        if not rid:
            raise IntegrationError('飞书写入结果缺少记录ID，请核对后重试')
        self.last_fields = fields
        return {'table_id': table['table_id'], 'record_id': rid}


def sync_rows(db,kind,run):
    if kind == 'knowledge':
        return sorted(shared_documents(db), key=lambda row: (row.created_at, row.id))
    query=select(Record).where(Record.kind==kind)
    all_scopes=run.get('options',{}).get('all_scopes')
    if all_scopes:
        active_ids=[r.id for r in db.scalars(select(Record).where(Record.kind=='ips')) if active_ip(r)]
        query=query.where(Record.ip_id.is_(None) | Record.ip_id.in_(active_ids))
    else:
        if run['ip_id'] and not active_ip(db.get(Record,run['ip_id'])):
            return []
        query=query.where(Record.ip_id==run['ip_id'])
    rows=list(db.scalars(query.order_by(Record.created_at)))
    if not all_scopes and not run['ip_id']:
        rows=[r for r in rows if r.owner_id==run['owner_id']]
    if kind=='leads':
        rows=[r for r in rows if r.payload.get('source_type')=='platform_comment']
    return rows


def run_sync_job(app, key):
    run = claim(app, key, 'feishu_sync')
    if not run:
        return
    api = None
    try:
        snapshot = config(app, 'feishu')
        if not snapshot.get('enabled'):
            raise IntegrationError('飞书同步未启用')
        def before():
            current = config(app, 'feishu')
            if not current.get('enabled') or current != snapshot:
                raise IntegrationError('管理员已停用飞书或变更凭证，停止同步')
            with app.state.Session() as db:
                row = db.get(Record, key)
                submitter=db.get(User,row.owner_id)
                if not authorized(db, row) or ((run.get('options',{}).get('all_scopes') or run.get('options',{}).get('include_library')) and submitter.role!='admin'):
                    raise IntegrationError('提交者权限已变更，停止同步')
                count = row.payload.get('calls', 0)
            update(app, key, calls=count + 1)
        api = Feishu(app, snapshot, before=before)
        schema = api.schema()
        if not schema['ready']:
            raise IntegrationError('；'.join(schema['warnings']))
        refs, processed = {}, 0
        for name, kind in KINDS.items():
            with app.state.Session() as db:
                rows = sync_rows(db,kind,run)
            for row in rows:
                if name == 'comments' and row.payload.get('source_type') != 'platform_comment':
                    continue
                ref = api.upsert(schema['tables'][name], row, refs)
                refs[row.id] = ref
                with app.state.Session() as db:
                    db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                    local = db.get(Record, row.id)
                    local.payload = {**local.payload, 'feishu_record_id': ref['record_id'], 'feishu_table_id': ref['table_id'], 'feishu_synced_at': now(), 'feishu_snapshot': api.last_fields}
                    db.commit()
                processed += 1
                update(app, key, processed=processed, progress=f'{TABLE_NAMES[name]}：已同步 {processed} 条')
        if run.get('options',{}).get('include_library'):
            from .feishu_library import sync
            processed=sync(api,app,key,run,refs,processed)
        finish(app, key)
    except SyncConflict as exc:
        update(app, key, conflict=exc.details)
        finish(app, key, str(exc), uncertain=True)
    except IntegrationError as exc:
        finish(app, key, str(exc), uncertain='核对' in str(exc))
    except Exception:
        finish(app, key, '飞书同步异常；请核对远端记录后手动重试', uncertain=True)
    finally:
        if api:
            api.http.close()
