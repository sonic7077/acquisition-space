"""Schema-aware, idempotent three-table synchronization into the user's Base."""
from hashlib import sha256
import json
import logging
from uuid import UUID

import httpx
from sqlalchemy import select
from ..db import Record, User, as_record, now
from ..discovery import timestamp
from ..knowledge import shared_documents
from ..security import active_ip
from .feishu_target import resolve
from .common import config, client, claim, finish, update, authorized, IntegrationError

ROOT = 'https://open.feishu.cn/open-apis'
LOGGER = logging.getLogger(__name__)
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

    def table(self, key, available):
        tid = self.cfg.get(key + '_table_id')
        matches = [t for t in available if (t['table_id'] == tid if tid else t['name'] == TABLE_NAMES[key])]
        if len(matches) != 1:
            return None
        table = dict(matches[0])
        table['fields'] = self.items(self.path(table['table_id']) + '/fields')
        mapping = self.mapping.get(key, {})
        table['mapping'] = {}
        aliases_by_field = {**ALIASES, 'transcript': ['逐字稿']} if key == 'works' else ALIASES
        for local, aliases in aliases_by_field.items():
            explicit = mapping.get(local)
            found = next((f for f in table['fields'] if (explicit in (f['field_name'], f['field_id']) if explicit else f['field_name'] in aliases)), None)
            allowed = (1,) if local == 'transcript' else (1, 2, 3, 4, 5, 7, 15, 18, 21)
            if found and found['type'] in allowed:
                table['mapping'][local] = found
        if key == 'works':
            reserved = {'逐字稿'}
            if transcript := table['mapping'].get('transcript'):
                reserved.add(transcript['field_name'])
            for local, field in list(table['mapping'].items()):
                if local != 'transcript' and field['field_name'] in reserved:
                    del table['mapping'][local]
        return table

    def schema(self):
        available = self.items(self.path())
        tables, warnings = {}, []
        for key, name in TABLE_NAMES.items():
            table = self.table(key, available)
            if not table:
                warnings.append(f'无法唯一识别{name}表，请管理员填写表ID')
                continue
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
        data = {k: v for k, v in data.items() if not k.startswith('feishu_') and k != 'transcript'}
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

    def find_record(self, table, row):
        path = self.path(table['table_id']) + '/records'
        unique = table['mapping']['local_id']
        result = self.request('POST', path + '/search', {'filter': {'conjunction': 'and', 'conditions': [
            {'field_name': unique['field_name'], 'operator': 'is', 'value': [row.id]}]}}, page_size=100)
        existing = result.get('items') or []
        if len(existing) > 1 or result.get('has_more'):
            raise SyncConflict(table, row, [])
        if not existing:
            return None
        record = existing[0]
        if not record.get('record_id') or comparable((record.get('fields') or {}).get(unique['field_name']), unique) != row.id:
            raise SyncConflict(table, row, [])
        return record

    def create_work_record(self, table, row, fields):
        path = self.path(table['table_id']) + '/records'
        token = str(UUID(bytes=sha256(f'{ROOT}{path}/{row.id}'.encode()).digest()[:16], version=4))
        try:
            result = self.request('POST', path, {'fields': fields}, client_token=token)
        except IntegrationError:
            existing = self.find_record(table, row)
            if existing:
                return existing
            raise
        # Collection and transcription can arrive together. A replay may
        # return the other's record, so verify the actual cells by work ID.
        existing = self.find_record(table, row)
        if not existing or existing['record_id'] != (result.get('record') or {}).get('record_id'):
            raise IntegrationError('飞书新建作品结果无法确认，请核对作品表后重试')
        return existing

    def upsert(self, table, row, refs):
        path = self.path(table['table_id']) + '/records'
        existing = self.find_record(table, row)
        fields = self.fields(table, row, refs)
        create = existing is None
        if create and row.kind == 'materials':
            existing = self.create_work_record(table, row, fields)
        if existing:
            previous = row.payload.get('feishu_snapshot', {}) if row.payload.get('feishu_table_id') == table['table_id'] else {}
            remote = existing.get('fields') or {}
            differences = []
            fill_empty = False
            for field in table['mapping'].values():
                name = field['field_name']
                if name not in fields:
                    continue
                baseline = previous.get(name, fields[name])
                actual = comparable(remote.get(name), field)
                if actual != comparable(baseline, field):
                    if row.kind == 'materials' and name not in previous and actual == comparable(None, field):
                        fill_empty = True
                        continue
                    differences.append({'field': name, 'last_sync': baseline, 'local': fields[name], 'feishu': actual})
            if differences:
                raise SyncConflict(table, row, differences)
            if not create or fill_empty:
                result = self.request('PUT', path + '/' + existing['record_id'], {'fields': fields})
            else:
                result = {'record': existing}
        else:
            result = self.request('POST', path, {'fields': fields})
        rid = (result.get('record') or {}).get('record_id')
        if not rid:
            raise IntegrationError('飞书写入结果缺少记录ID，请核对后重试')
        self.last_fields = fields
        return {'table_id': table['table_id'], 'record_id': rid}


def transcript_cell(value):
    if value is None or isinstance(value, str):
        return value or ''
    if isinstance(value, list) and all(isinstance(part, dict) and isinstance(part.get('text'), str)
                                      and part.get('type', 'text') == 'text' for part in value):
        return ''.join(part['text'] for part in value)
    return None


def work_author_ref(app, material, table):
    author_field = table['mapping'].get('author_id', {})
    author_table = author_field.get('property', {}).get('table_id')
    author_id = material.payload.get('author_id')
    if not author_table or not author_id:
        return {}
    with app.state.Session() as db:
        author = db.get(Record, author_id)
        if author and author.kind == 'authors' and author.ip_id == material.ip_id:
            record_id = author.payload.get('feishu_record_id')
            if record_id and author.payload.get('feishu_table_id') == author_table:
                return {author_id: {'record_id': record_id, 'table_id': author_table}}
    return {}


def save_work_binding(app, material_id, table_id, record_id, initial_fields=None):
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(Record, material_id)
        if not row or row.kind != 'materials':
            return
        payload = dict(row.payload)
        same_record = payload.get('feishu_table_id') == table_id and payload.get('feishu_record_id') == record_id
        if initial_fields is not None and (not same_record or 'feishu_snapshot' not in payload):
            payload.update(feishu_snapshot=initial_fields, feishu_synced_at=now())
        elif not same_record:
            payload.pop('feishu_snapshot', None)
            payload.pop('feishu_synced_at', None)
        row.payload = {**payload, 'feishu_table_id': table_id, 'feishu_record_id': record_id}
        db.commit()


def sync_work_transcript(app, material, text, *, before=None):
    snapshot = config(app, 'feishu')
    if not snapshot.get('enabled'):
        return {'status': 'skipped', 'message': '飞书同步未启用，逐字稿已保留在本地'}
    if not isinstance(text, str) or not text.strip():
        return {'status': 'skipped', 'message': '逐字稿为空'}

    def check_config():
        if config(app, 'feishu') != snapshot:
            raise IntegrationError('管理员已变更飞书设置，请重新同步逐字稿')
        if before:
            before()

    api = None
    try:
        api = Feishu(app, snapshot, before=check_config)
        table = api.table('works', api.items(api.path()))
        if not table:
            raise IntegrationError('无法唯一识别飞书作品表，请核对系统设置中的表ID')
        mapping = table['mapping']
        transcript = mapping.get('transcript')
        if not transcript:
            raise IntegrationError('飞书作品表缺少可编辑的文本字段「逐字稿」')
        if not mapping.get('local_id') or mapping['local_id']['type'] != 1:
            raise IntegrationError('飞书作品表缺少文本字段「获客空间ID」，请先准备同步字段')
        tid = table['table_id']
        field_name = transcript['field_name']
        path = api.path(tid) + '/records'
        remote = api.find_record(table, material)
        initial_fields = None
        if not remote:
            if material.payload.get('feishu_table_id') == tid and material.payload.get('feishu_record_id'):
                return {'status': 'conflict', 'message': '已关联的飞书作品标识缺失，请核对作品表中的获客空间ID'}
            desired = api.fields(table, material, work_author_ref(app, material, table))
            remote = api.create_work_record(table, material, {**desired, field_name: text})
            actual = remote.get('fields') or {}
            initial_fields = {name: actual.get(name) for name in desired}
        rid = remote['record_id']
        current = transcript_cell((remote.get('fields') or {}).get(field_name))
        if current is None or current.strip() and current != text:
            return {'status': 'conflict', 'message': '飞书作品已有不同逐字稿，已保留原稿及本地转写结果'}
        if current != text:
            result = api.request('PUT', f'{path}/{rid}', {'fields': {field_name: text}})
            if (result.get('record') or {}).get('record_id') != rid:
                raise IntegrationError('飞书写入结果无法确认，请核对作品表后重试')
        check_config()
        save_work_binding(app, material.id, tid, rid, initial_fields)
        return {'status': 'synced', 'message': '逐字稿已同步到飞书作品表'}
    except SyncConflict:
        return {'status': 'conflict', 'message': '飞书作品表的获客空间ID有重复或不一致，请核对对应记录'}
    except IntegrationError as exc:
        return {'status': 'failed', 'message': str(exc)}
    except Exception:
        LOGGER.warning('Unexpected Feishu transcript sync error for material %s', material.id)
        return {'status': 'failed', 'message': '飞书逐字稿同步异常，请核对作品表后重试'}
    finally:
        if api:
            api.http.close()


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
