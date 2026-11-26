from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
import csv
import hashlib
import io
import json
import os
import secrets
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from .db import User, LoginSession, Record, database, now, uid, as_record, business_today
from .security import password_hash, password_ok, public_user, admin_only, approver, active_ip, can_ip, require_ip, SecretStore, ROLES, can_record, require_record
from .knowledge import expand_uploads, MAX_FILE, shared_documents, template_text, verified_document
from .writing import SKILLS, build_context, draft_checks, transcript_available
from .demand_analysis import labels, patch_labels, demand_view, demand_snapshot, attach_demand, public_record
from .discovery import DEFAULT_RULES, rules_for, validate_rules, author_patch, discovery_records
from .validation import record_input, text_fields
from .knowledge_categories import TREE, flatten, leaf, migrate as migrate_knowledge

from .integrations.feishu_target import default_url as default_feishu_url

KINDS = {'materials', 'authors', 'needs', 'leads', 'metrics', 'rules'}
WECHAT_PLATFORMS = ('视频号', '公众号')
WECHAT_TOUCHPOINTS = ('视频号', '公众号', '朋友圈', '图文贴', '微信群', '私域资料')


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(Credentials):
    name: str = Field(min_length=1, max_length=60)
    role: Literal['admin', 'leader', 'member']
    team_id: str | None = None
    ip_ids: list[str] = Field(default_factory=list)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    role: Literal['admin', 'leader', 'member'] | None = None
    team_id: str | None = None
    ip_ids: list[str] | None = None
    active: bool | None = None
    password: str | None = None


class ContextInput(BaseModel):
    ip_id: str
    topic: str = Field(min_length=1, max_length=2000)
    skill: str = 'original-video-writer'
    writer_id: str | None = None
    writer_version: int | None = None
    need_id: str | None = None


class ReviewCheckInput(BaseModel):
    draft_id: str | None = None
    version: int | None = None
    ip_id: str | None = None
    skill: str = 'original-video-writer'
    title: str = Field(default='', max_length=2000)
    body: str = Field(default='', max_length=20000)
    reference: str = Field(default='', max_length=20000)
    fact_status: Literal['none', 'pending', 'verified'] = 'pending'


def create_app(data_dir=None):
    app = FastAPI(title='获客空间', version='0.1.0')
    folder = Path(data_dir or os.environ.get('ACQUISITION_DATA_DIR', '.data')).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    (folder / 'assets').mkdir(exist_ok=True)
    app.state.data_dir = folder
    engine, Session = database(folder / 'system.db')
    app.state.Session = Session
    app.state.secrets = SecretStore(folder)
    with Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        if not db.scalar(select(func.count()).select_from(User)):
            user = User(username='admin', name='管理员', password_hash=password_hash('admin', default_admin=True), role='admin', ip_ids=[])
            db.add(user)
            db.flush()
            db.add(Record(kind='audit', owner_id=user.id, payload={'actor': '系统', 'action': '创建默认管理员', 'object_id': user.id}))
        settings = db.get(Record, 'system-settings')
        if settings and 'video' in settings.payload:
            settings.payload = {k: v for k, v in settings.payload.items() if k != 'video'}
        # Retain historical records and assets, but retire all unfinished generation tasks.
        for job in db.scalars(select(Record).where(Record.kind == 'video_jobs')):
            if job.payload.get('status') in ('queued', 'submitting', 'processing', 'polling', 'poll_paused', 'unknown'):
                job.payload = {**job.payload, 'status': 'cancelled', 'error': '视频生成功能已撤下，本地任务停止；平台任务需在供应商控制台核对'}
        migrate_knowledge(db, Record)
        db.commit()

    @app.middleware('http')
    async def protect(request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
            host = request.headers.get('host', '')
            if urlparse(origin).netloc != host:
                return JSONResponse({'detail': '请求来源不允许'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def get_db():
        with Session() as db:
            # BEGIN IMMEDIATE serializes read/check/write invariants across processes.
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def authenticated_user(request: Request, db):
        token = request.cookies.get('session')
        sess = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
        user = db.get(User, sess.user_id) if sess and sess.expires_at > now() else None
        if not user or not user.active:
            raise HTTPException(401, '请先登录')
        return user

    def current(request: Request, db=Depends(get_db)):
        return authenticated_user(request, db)

    def audit(db, user, action, object_id=None):
        db.add(Record(kind='audit', owner_id=user.id if user else None, payload={'actor': user.name if user else '初始化', 'action': action, 'object_id': object_id}))

    def row_for(db, user, key, kind):
        row = db.get(Record, key)
        if not row or row.kind != kind:
            raise HTTPException(404, '记录不存在')
        require_record(db, user, row)
        return row

    def visible(db, user, kind):
        return [r for r in db.scalars(select(Record).where(Record.kind == kind).order_by(Record.created_at.desc())) if can_record(db, user, r)]

    def invalidate_schedules(db, draft_ids):
        if not draft_ids:
            return
        for scheduled in db.scalars(select(Record).where(Record.kind == 'schedules')):
            if scheduled.payload.get('draft_id') in draft_ids and scheduled.payload.get('status') in ('planned', 'staged'):
                scheduled.payload = {**scheduled.payload, 'status': 'needs_review'}
        from .db import PublishTarget, PublishAttempt
        for target in db.scalars(select(PublishTarget).where(PublishTarget.draft_id.in_(list(draft_ids)))):
            if target.status in ('QUEUED', 'WAITING_CLIENT_ONLINE'):
                target.status = 'NEEDS_REVIEW'
                for att in db.scalars(select(PublishAttempt).where(PublishAttempt.target_id == target.id)):
                    if att.state in ('QUEUED', 'WAITING_CLIENT_ONLINE'):
                        att.state = 'CANCELLED'
                        att.error_class = 'DRAFT_INVALID'
                        att.error_message = '稿件版本或批准已变化，排期已暂停'

    def setting_data(db):
        row = db.get(Record, 'system-settings')
        return row.payload if row else {'model': {}, 'feishu': {'url': default_feishu_url()}, 'collection': {}, 'transcription': {}, 'wechat': {}, 'executor': {}, 'rules': {'overlap_min': 8, 'overlap_max': .08}}

    def mask_settings(data):
        return {section: {**{k: v for k, v in fields.items() if not k.endswith('_encrypted')}, **{k.removesuffix('_encrypted') + '_configured': bool(v) for k, v in fields.items() if k.endswith('_encrypted')}} for section, fields in data.items()}

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '0.1.0'}

    @app.post('/api/auth/login')
    def login(data: Credentials, db=Depends(get_db)):
        user = db.scalar(select(User).where(User.username == data.username))
        if user and user.locked_until and user.locked_until > now():
            raise HTTPException(429, '登录失败过多，请10分钟后重试')
        if not user or not user.active or not password_ok(data.password, user.password_hash):
            if user:
                user.failures += 1
                if user.failures >= 5:
                    user.locked_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                audit(db, user, '登录失败')
                db.commit()
            raise HTTPException(401, '用户名或密码错误')
        user.failures = 0
        user.locked_until = None
        raw = secrets.token_urlsafe(40)
        db.add(LoginSession(id=hashlib.sha256(raw.encode()).hexdigest(), user_id=user.id, expires_at=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()))
        audit(db, user, '登录')
        response = JSONResponse(public_user(user))
        response.set_cookie('session', raw, httponly=True, samesite='strict', secure=os.environ.get('SECURE_COOKIE') == '1', max_age=43200)
        return response

    @app.get('/api/auth/me')
    def me(user=Depends(current)):
        return public_user(user)

    @app.post('/api/auth/logout')
    def logout(request: Request, user=Depends(current), db=Depends(get_db)):
        sess = db.get(LoginSession, hashlib.sha256(request.cookies['session'].encode()).hexdigest())
        if sess:
            db.delete(sess)
        response = JSONResponse({'ok': True})
        response.delete_cookie('session')
        return response

    @app.get('/api/users')
    def users(user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        result = []
        for u in db.scalars(select(User)):
            result.append({**public_user(u), 'sessions': db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == u.id, LoginSession.expires_at > now()))})
        return result

    def validate_assignment(db, role, team_id, ip_ids):
        if role != 'admin' and not team_id:
            raise HTTPException(422, '团队长和队员必须归属团队')
        if team_id:
            team = db.get(Record, team_id)
            if not team or team.kind != 'teams':
                raise HTTPException(422, '团队不存在')
        for key in ip_ids:
            ip = db.get(Record, key)
            if not active_ip(ip) or ip.payload.get('team_id') != team_id:
                raise HTTPException(422, 'IP不属于所选团队')

    @app.post('/api/users')
    def add_user(data: UserCreate, user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        validate_assignment(db, data.role, data.team_id, data.ip_ids)
        if db.scalar(select(User).where(User.username == data.username)):
            raise HTTPException(409, '登录名已存在')
        u = User(**data.model_dump(exclude={'password'}), password_hash=password_hash(data.password))
        db.add(u)
        db.flush()
        audit(db, user, '新增用户', u.id)
        return public_user(u)

    @app.patch('/api/users/{key}')
    def patch_user(key: str, data: UserPatch, user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        target = db.get(User, key)
        if not target:
            raise HTTPException(404, '用户不存在')
        patch = data.model_dump(exclude_unset=True)
        if any(v is None for k, v in patch.items() if k not in ('team_id',)):
            raise HTTPException(422, '字段不能留空')
        if target.role == 'admin' and target.active and (patch.get('role', 'admin') != 'admin' or patch.get('active') is False):
            if db.scalar(select(func.count()).select_from(User).where(User.role == 'admin', User.active.is_(True))) <= 1:
                raise HTTPException(409, '必须保留至少一名有效管理员')
        validate_assignment(db, patch.get('role', target.role), patch.get('team_id', target.team_id), patch.get('ip_ids', target.ip_ids))
        for k, value in patch.items():
            if k == 'password':
                target.password_hash = password_hash(value)
            else:
                setattr(target, k, value)
        for sess in db.scalars(select(LoginSession).where(LoginSession.user_id == target.id)):
            db.delete(sess)
        audit(db, user, '修改用户并撤销会话', key)
        return public_user(target)

    @app.post('/api/users/{key}/revoke')
    def revoke(key: str, user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        for sess in db.scalars(select(LoginSession).where(LoginSession.user_id == key)):
            db.delete(sess)
        audit(db, user, '撤销用户会话', key)
        return {'ok': True}

    @app.get('/api/teams')
    def teams(user=Depends(current), db=Depends(get_db)):
        rows = db.scalars(select(Record).where(Record.kind == 'teams'))
        return [as_record(r) for r in rows if user.role == 'admin' or r.id == user.team_id]

    @app.post('/api/teams')
    def add_team(data: dict, user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        if not str(data.get('name', '')).strip():
            raise HTTPException(422, '请填写团队名称')
        row = Record(kind='teams', payload={'name': data['name']}, owner_id=user.id)
        db.add(row)
        db.flush()
        audit(db, user, '创建团队', row.id)
        return public_record(db, user, row)

    @app.get('/api/ips')
    def ips(user=Depends(current), db=Depends(get_db)):
        return [as_record(r) for r in db.scalars(select(Record).where(Record.kind == 'ips')) if can_ip(db, user, r.id)]

    @app.post('/api/ips')
    def add_ip(data: dict, user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        team = db.get(Record, data.get('team_id', ''))
        if not team or team.kind != 'teams' or not data.get('name'):
            raise HTTPException(422, '请选择团队并填写IP名称')
        row = Record(kind='ips', payload={k: data[k] for k in ('name', 'team_id', 'profile') if k in data}, owner_id=user.id)
        db.add(row)
        db.flush()
        return public_record(db, user, row)

    @app.patch('/api/ips/{key}')
    def patch_ip(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        approver(user)
        require_ip(db, user, key)
        row = db.get(Record, key)
        row.payload = {**row.payload, **{k: v for k, v in data.items() if k in ('name', 'profile')}}
        audit(db, user, '更新IP档案', key)
        return public_record(db, user, row)

    @app.get('/api/settings')
    def settings(user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        data = setting_data(db)
        return mask_settings({**data, 'discovery': rules_for(data)})

    @app.put('/api/settings')
    def update_settings(data: dict, user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        fields = {
            'model': {'base_url', 'model', 'api_key', 'enabled'},
            'feishu': {'url', 'app_id', 'app_secret', 'enabled', 'authors_table_id', 'works_table_id', 'comments_table_id', 'field_mapping', 'auth_mode'},
            'collection': {'base_url', 'api_key', 'daily_budget', 'enabled'},
            'transcription': {'base_url', 'model', 'api_key'},
            'wechat': {'app_id', 'app_secret', 'webhook'},
            'executor': {'mode', 'base_url', 'token', 'catchup_minutes', 'preflight_minutes', 'allow_client_local', 'allow_interaction_write'},
            'rules': {'overlap_min', 'overlap_max'},
            'discovery': set(DEFAULT_RULES) - {'version'},
        }
        value = setting_data(db)
        for section, patch in data.items():
            if section not in fields or not isinstance(patch, dict):
                raise HTTPException(422, '未知设置分组')
            if section == 'discovery':
                value[section] = validate_rules(patch, value.get(section, {}))
                continue
            merged = dict(value.get(section, {}))
            for key, val in patch.items():
                if key not in fields[section]:
                    raise HTTPException(422, '未知设置字段')
                if section == 'feishu' and key == 'auth_mode' and val not in ('app', 'cli'):
                    raise HTTPException(422, '飞书认证方式为app或cli')
                if section == 'executor' and key == 'mode' and val not in ('manual', 'server_direct', 'server_relay', 'client_local', 'hybrid'):
                    raise HTTPException(422, '执行器模式无效')
                if key in ('enabled', 'allow_client_local', 'allow_interaction_write') and not isinstance(val, bool):
                    raise HTTPException(422, '启用开关必须是布尔值')
                if key == 'daily_budget' and (not isinstance(val, (int, float)) or isinstance(val, bool) or not 1 <= val <= 100000):
                    raise HTTPException(422, '每日调用预算须为1至100000')
                if key in ('catchup_minutes', 'preflight_minutes') and (not isinstance(val, int) or isinstance(val, bool) or not 1 <= val <= 1440):
                    raise HTTPException(422, '时间窗口须为1至1440分钟')
                if key not in ('enabled', 'allow_client_local', 'allow_interaction_write', 'daily_budget', 'overlap_min', 'overlap_max', 'catchup_minutes', 'preflight_minutes') and val is not None and (not isinstance(val, str) or len(val) > 10000):
                    raise HTTPException(422, '配置字段必须为文本且不超过10000字符')
                if key in ('api_key', 'app_secret', 'token', 'webhook'):
                    if val is None:
                        merged.pop(key + '_encrypted', None)
                    elif isinstance(val, str) and val:
                        merged[key + '_encrypted'] = app.state.secrets.encrypt(val)
                else:
                    merged[key] = val
            if section == 'feishu' and 'url' in patch and patch['url'] != value.get(section, {}).get('url'):
                # Table and field IDs belong to one destination. Re-detect before writing.
                for binding in ('authors_table_id','works_table_id','comments_table_id','field_mapping'):
                    merged.pop(binding,None)
            value[section] = merged
        from .integrations.feishu_target import parse_url
        from .integrations.common import IntegrationError
        feishu_cfg = value.get('feishu', {})
        feishu_url = (feishu_cfg.get('url') or '').strip()
        if feishu_url:
            try:
                parse_url(feishu_url)
            except (IntegrationError, ValueError):
                raise HTTPException(422, '请使用有效的飞书 HTTPS base、wiki 或 docx 链接') from None
        elif feishu_cfg.get('enabled') and not default_feishu_url():
            raise HTTPException(422, '启用飞书同步前请配置有效的飞书链接')
        rule = value.get('rules', {})
        try:
            if not 2 <= int(rule.get('overlap_min', 8)) <= 100 or not 0 <= float(rule.get('overlap_max', .08)) <= 1:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, '重合检查规则范围无效')
        model_url = value.get('model', {}).get('base_url', '')
        if model_url and (urlparse(model_url).scheme not in ('http', 'https') or urlparse(model_url).username or urlparse(model_url).query or urlparse(model_url).fragment or not urlparse(model_url).hostname):
            raise HTTPException(422, '模型服务地址必须是HTTP(S)，不能内嵌凭证')
        if value.get('collection', {}).get('base_url') not in (None, '', 'https://api.tikhub.io'):
            raise HTTPException(422, 'TikHub服务地址须为 https://api.tikhub.io')
        try:
            mapping = json.loads(value.get('feishu', {}).get('field_mapping') or '{}')
            if not isinstance(mapping, dict) or any(k not in ('authors', 'works', 'comments') or not isinstance(v, dict) or any(not isinstance(a, str) or not isinstance(b, str) for a,b in v.items()) for k,v in mapping.items()):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, '飞书字段映射须为三表分组的JSON对象')
        row = db.get(Record, 'system-settings')
        if row:
            row.payload = value
        else:
            db.add(Record(id='system-settings', kind='settings', payload=value))
        audit(db, user, '更新系统设置（不记录配置值）')
        return mask_settings(value)

    @app.get('/api/audit')
    def audits(user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        return [as_record(r) for r in db.scalars(select(Record).where(Record.kind == 'audit').order_by(Record.created_at.desc()).limit(200))]

    @app.get('/api/knowledge/categories')
    def knowledge_categories(user=Depends(current)):
        return {'tree': TREE, 'categories': [{k: v for k, v in n.items() if k != 'children'} for n in flatten()]}

    def knowledge_fingerprint(payload):
        return payload.get('category_id'), payload.get('path'), payload.get('hash')

    @app.post('/api/knowledge/preview')
    def preview(category_id: str = Form(...), files: list[UploadFile] = File(...), user=Depends(current), db=Depends(get_db)):
        destination = leaf(category_id)
        if len(files) > 200:
            raise HTTPException(422, '每批最多200个文件')
        uploads = []
        for file in files:
            raw = file.file.read(MAX_FILE + 1)
            uploads.append((file.filename or 'unknown', raw))
        ignored = []
        items = expand_uploads(uploads, ignored)
        existing = {knowledge_fingerprint(d.payload) for d in visible(db, user, 'knowledge')}
        for item in items:
            item.update(destination)
            item['duplicate'] = knowledge_fingerprint(item) in existing
        row = Record(kind='import_batch', owner_id=user.id, payload={'items': items, 'confirmed_ids': None, 'ignored': ignored, **destination})
        db.add(row)
        db.flush()
        return {'id': row.id, 'ip_id': row.ip_id, **destination, 'ignored': ignored, 'items': [{k: v for k, v in d.items() if k != 'text'} | {'preview': d['text'][:500]} for d in items]}

    @app.post('/api/knowledge/confirm')
    def confirm(data: dict, user=Depends(current), db=Depends(get_db)):
        batch = row_for(db, user, data.get('batch_id'), 'import_batch')
        if batch.payload.get('confirmed_ids') is not None:
            return {'items': [as_record(db.get(Record, key)) for key in batch.payload['confirmed_ids']]}
        destination = leaf(batch.payload.get('category_id'))
        if any(item.get('category_id') != destination['category_id'] for item in batch.payload['items']):
            raise HTTPException(422, '导入目录信息不完整，请重新选择末级目录并上传')
        existing = {}
        documents = select(Record).where(Record.kind == 'knowledge', Record.ip_id.is_(None)).order_by(Record.created_at, Record.id)
        for document in db.scalars(documents):
            existing.setdefault(knowledge_fingerprint(document.payload), document)
        confirmed = []
        for item in batch.payload['items']:
            fingerprint = knowledge_fingerprint(item)
            row = existing.get(fingerprint)
            if row is None:
                row = Record(kind='knowledge', owner_id=user.id, payload={k: v for k, v in item.items() if k != 'duplicate'})
                db.add(row)
                existing[fingerprint] = row
            confirmed.append(row)
        db.flush()
        batch.payload = {**batch.payload, 'confirmed_ids': [r.id for r in confirmed]}
        audit(db, user, '确认知识库导入', batch.id)
        return {'items': [as_record(row) for row in confirmed]}

    @app.get('/api/knowledge')
    def knowledge(q: str = '', user=Depends(current), db=Depends(get_db)):
        return [{k: v for k, v in as_record(r).items() if k != 'text'} | {'characters': len(r.payload['text'])} for r in visible(db, user, 'knowledge') if q.lower() in (r.payload['title'] + r.payload['text']).lower()]

    @app.get('/api/knowledge/{key}')
    def document(key: str, user=Depends(current), db=Depends(get_db)):
        return as_record(row_for(db, user, key, 'knowledge'))

    @app.patch('/api/knowledge/{key}')
    def update_document(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        row = row_for(db, user, key, 'knowledge')
        approver(user)
        patch = {k: v for k, v in data.items() if k in ('text', 'title', 'status', 'valid_until', 'category_id')}
        if 'category_id' in patch:
            patch.update(leaf(patch['category_id']))
        if patch.get('status', row.payload['status']) not in ('pending', 'verified', 'template', 'archived'):
            raise HTTPException(422, '知识状态无效')
        merged = {**row.payload, **patch}
        if not merged.get('text', '').strip():
            raise HTTPException(422, '知识内容不能为空')
        if merged['status'] == 'verified' and template_text(merged['path'], merged['text']):
            raise HTTPException(422, '空模板或技能说明不能作为已核验事实，请上传实际业务资料')
        if 'text' in patch:
            merged['hash'] = hashlib.sha256(merged['text'].encode()).hexdigest()
            merged['status'] = 'pending'
        if 'valid_until' in patch and patch['valid_until']:
            try:
                datetime.strptime(patch['valid_until'], '%Y-%m-%d')
            except (TypeError, ValueError):
                raise HTTPException(422, '有效期格式必须为YYYY-MM-DD')
        if 'text' in patch:
            merged['history'] = list(row.payload.get('history', [])) + [{'text': row.payload['text'], 'hash': row.payload['hash'], 'changed_at': now()}]
        affected = set()
        if 'text' in patch or merged['status'] != 'verified' or (merged.get('valid_until') and merged['valid_until'] < business_today()):
            for draft in db.scalars(select(Record).where(Record.kind == 'drafts')):
                source_ids = set(draft.payload.get('source_ids', [])) | {x['id'] for x in draft.payload.get('sources', [])}
                if key in source_ids:
                    draft.payload = {**draft.payload, 'approved_version': None, 'fact_status': 'pending', 'status': 'pending', 'knowledge_change_reason': '引用的知识资料已变更，需复核'}
                    affected.add(draft.id)
            invalidate_schedules(db, affected)
        row.payload = merged
        row.updated_at = now()
        audit(db, user, '更新知识资料', key)
        return public_record(db, user, row)

    def context(db, user, data):
        require_ip(db, user, data.ip_id)
        if data.skill not in SKILLS:
            raise HTTPException(422, '未知写手能力')
        ip = db.get(Record, data.ip_id)
        docs = None if data.writer_id else shared_documents(db)
        rules = [r.payload for r in visible(db, user, 'rules') if r.ip_id == data.ip_id and r.payload.get('status') == 'adopted']
        ctx = build_context(ip, docs, data.topic, data.skill, rules)
        attach_demand(db, user, ctx, data.need_id)
        if data.writer_id:
            from .rewrite import writer_context
            if data.writer_version is None:
                raise HTTPException(422, '缺少写手版本')
            ctx = writer_context(db, user, ctx, data.writer_id, data.writer_version)
        return ctx

    @app.get('/api/writing/analyses')
    def writing_analyses(user=Depends(current), db=Depends(get_db)):
        return [public_record(db, user, r) for r in visible(db, user, 'writer_analysis')]

    @app.get('/api/skills')
    def skills(user=Depends(current)):
        return [{'id': key, 'name': name} for key, (name, _) in SKILLS.items()]

    @app.post('/api/writing/context')
    def writing_context(data: ContextInput, user=Depends(current), db=Depends(get_db)):
        return context(db, user, data)

    @app.post('/api/writing/generate')
    def generate(data: ContextInput, user=Depends(current), db=Depends(get_db)):
        ctx = context(db, user, data)
        model = setting_data(db).get('model', {})
        if not model.get('base_url') or not model.get('model') or not model.get('api_key_encrypted') or not model.get('enabled'):
            raise HTTPException(409, '管理员尚未启用模型配置；可先导出写作上下文包')
        row = Record(kind='jobs', ip_id=data.ip_id, owner_id=user.id, payload={'status': 'queued', 'context': ctx, 'attempts': 0})
        db.add(row)
        db.flush()
        audit(db, user, '提交写作任务', row.id)
        return {'id': row.id, 'status': 'queued'}

    @app.get('/api/jobs')
    def jobs(user=Depends(current), db=Depends(get_db)):
        return [{k: v for k, v in as_record(r).items() if k != 'context' and
                 (k != 'raw_output' or user.role == 'admin' or r.owner_id == user.id)} for r in visible(db, user, 'jobs')]

    @app.get('/api/records/{kind}')
    def records(kind: str, user=Depends(current), db=Depends(get_db)):
        if kind not in KINDS:
            raise HTTPException(404, '页面不存在')
        if kind in ('materials', 'authors'):
            materials, authors = discovery_records(visible(db, user, 'materials'), visible(db, user, 'authors'), rules_for(setting_data(db)))
            if kind == 'authors':
                return authors
            from .integrations.transcript import progress_view, supported_identity, transcript_key, transcript_states
            states = transcript_states(db)
            for material in materials:
                identity = supported_identity(material)
                material['transcript_supported'] = bool(identity)
                if identity:
                    state = progress_view(states.get(transcript_key(material['id'], identity), {}))
                    material.update(transcript_status=state['status'], transcript_progress=state['progress'])
                    if 'feishu' in state:
                        material['transcript_feishu_status'] = state['feishu']['status']
            return materials
        return [demand_view(db, user, r) if kind == 'needs' else ({**as_record(r), **labels(r.payload)} if kind == 'leads' else as_record(r)) for r in visible(db, user, kind)]

    @app.post('/api/records/{kind}')
    def create_record(kind: str, data: dict, user=Depends(current), db=Depends(get_db)):
        if kind not in KINDS - {'metrics'}:
            raise HTTPException(422, '此类型不能直接创建')
        if data.get('ip_id') or kind not in ('materials', 'authors'):
            require_ip(db, user, data.get('ip_id'))
        data = record_input(data)
        if not data.get('title'):
            raise HTTPException(422, '请填写标题')
        payload = {k: v for k, v in data.items() if k not in ('id', 'ip_id', 'status')}
        payload['status'] = 'candidate' if kind == 'rules' else 'new'
        if kind == 'authors':
            payload.update(author_patch(data))
        if kind == 'needs':
            payload = {k: v for k, v in payload.items() if k in ('title', 'body', 'source', 'sel_topic', 'doc_name', 'product', 'note', 'status')}
        if kind == 'leads':
            payload = patch_labels({}, payload)
        row = Record(kind=kind, ip_id=data.get('ip_id') or None, owner_id=user.id, payload=payload)
        db.add(row)
        db.flush()
        return public_record(db, user, row)

    @app.patch('/api/records/{kind}/{key}')
    def patch_record(kind: str, key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        if kind not in KINDS - {'metrics', 'rules'}:
            raise HTTPException(422, '此类型不允许直接更新')
        row = row_for(db, user, key, kind)
        data = record_input(data)
        patch = {k: v for k, v in data.items() if k in ('title', 'body', 'status', 'intent', 'owner', 'next_followup', 'reply', 'source', 'reason', 'content_type', 'sel_topic', 'doc_name', 'product', 'note')}
        if kind == 'authors':
            patch.update(author_patch(data))
        if kind == 'leads':
            patch = patch_labels(row.payload, patch)
        row.payload = {**row.payload, **patch}
        row.updated_at = now()
        audit(db, user, '更新' + kind, key)
        return public_record(db, user, row)

    @app.post('/api/materials/{key}/adopt')
    def adopt(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        mat = row_for(db, user, key, 'materials')
        target_ip = mat.ip_id or data.get('ip_id')
        if not target_ip:
            raise HTTPException(422, '未归属素材加入写稿前请选择目标IP')
        require_ip(db, user, target_ip)
        skill = data.get('skill', 'original-video-writer')
        if skill not in SKILLS:
            raise HTTPException(422, '未知写手能力')
        for draft in visible(db, user, 'drafts'):
            if draft.ip_id == target_ip and draft.payload.get('material_id') == key and draft.payload.get('skill') == skill:
                return draft_review(db, user, draft, setting_data(db).get('rules', {}))
        if mat.payload.get('source') and not mat.payload.get('author_id'):
            existing = [r for r in visible(db, user, 'authors') if r.ip_id == mat.ip_id and r.payload.get('source') == mat.payload['source']]
            if not existing:
                db.add(Record(kind='authors', ip_id=mat.ip_id, owner_id=user.id, payload={'title': mat.payload.get('author', '素材来源作者'), 'source': mat.payload['source'], 'status': 'confirmed', 'body': '由明确采用操作加入对标池'}))
        row = Record(kind='drafts', ip_id=target_ip, owner_id=user.id, payload={'title': mat.payload['title'][:16], 'body': '', 'reference': mat.payload.get('body', ''), 'transcript_confirmed': False, 'skill': skill, 'material_id': key, 'version': 1, 'approved_version': None, 'status': 'pending', 'fact_status': 'pending', 'history': []})
        db.add(row)
        db.flush()
        mat.payload = {**mat.payload, 'status': 'adopted'}
        return draft_review(db, user, row, setting_data(db).get('rules', {}))

    @app.post('/api/rules/{key}/adopt')
    def adopt_rule(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        approver(user)
        row = row_for(db, user, key, 'rules')
        roots = set()
        for draft_id in set(data.get('evidence_ids', [])):
            draft = row_for(db, user, draft_id, 'drafts')
            if draft.ip_id != row.ip_id:
                raise HTTPException(422, '证据必须属于同一IP')
            roots.add(draft.payload.get('material_id') or draft.id)
        if len(roots) < 3:
            raise HTTPException(409, '至少需要3篇独立根内容，重复稿件或同素材改版不能重复计数')
        current_rules = [r for r in visible(db, user, 'rules') if r.ip_id == row.ip_id and r.payload.get('status') == 'adopted' and r.id != key]
        if len(current_rules) >= 15:
            raise HTTPException(409, '规则已达15条，请先合并或归档')
        row.payload = {**row.payload, 'status': 'adopted', 'evidence_ids': sorted(set(data['evidence_ids'])), 'root_ids': sorted(roots), 'adopted_by': user.id}
        audit(db, user, '人工采纳写手规则', key)
        return public_record(db, user, row)

    @app.post('/api/rules/{key}/archive')
    def archive_rule(key: str, user=Depends(current), db=Depends(get_db)):
        approver(user)
        row = row_for(db, user, key, 'rules')
        row.payload = {**row.payload, 'status': 'archived'}
        audit(db, user, '归档写手规则', key)
        return public_record(db, user, row)

    @app.get('/api/drafts')
    def drafts(user=Depends(current), db=Depends(get_db)):
        config = setting_data(db).get('rules', {})
        return [draft_review(db, user, r, config) for r in visible(db, user, 'drafts')]

    def can_review_transcript(db, user, payload):
        material_id = payload.get('material_id')
        if not material_id:
            return True
        material = db.get(Record, material_id)
        return material is not None and material.kind == 'materials' and can_record(db, user, material)

    def review_checks(db, payload, config, reference_restricted):
        result = draft_checks(db, payload, config, include_reference=not reference_restricted)
        if reference_restricted:
            result[-1] = {**result[-1], 'state': 'restricted'}
        return result

    def draft_review(db, user, row, config):
        output = public_record(db, user, row)
        if not transcript_available(row.payload):
            output.pop('reference', None)
        output['checks'] = review_checks(db, row.payload, config, output.get('reference_restricted'))
        return output

    @app.post('/api/drafts/checks')
    def preview_draft_checks(data: ReviewCheckInput, request: Request):
        with Session() as db:
            user = authenticated_user(request, db)
            if data.draft_id:
                row = row_for(db, user, data.draft_id, 'drafts')
                if data.version != row.payload['version']:
                    raise HTTPException(409, '稿件已经变更，请刷新后对比再保存')
                payload = row.payload
            else:
                require_ip(db, user, data.ip_id)
                if data.skill not in SKILLS:
                    raise HTTPException(422, '未知写手能力')
                payload = {'skill': data.skill, 'fact_status': 'pending'}
            edits = data.model_dump(exclude_unset=True, include={'title', 'body', 'reference', 'fact_status'})
            restricted = not can_review_transcript(db, user, payload)
            if 'reference' in edits:
                if restricted:
                    raise HTTPException(403, '没有对标素材的访问权限')
                edits['transcript_confirmed'] = bool(edits['reference'].strip())
            return {'checks': review_checks(db, {**payload, **edits}, setting_data(db).get('rules', {}), restricted)}

    @app.post('/api/drafts')
    def create_draft(data: dict, user=Depends(current), db=Depends(get_db)):
        require_ip(db, user, data.get('ip_id'))
        text_fields(data)
        skill = data.get('skill', 'original-video-writer')
        if skill not in SKILLS:
            raise HTTPException(422, '未知写手能力')
        if len(data.get('body', '')) > 20000 or len(data.get('reference', '')) > 20000:
            raise HTTPException(422, '稿件或参考内容超过2万字')
        if data.get('fact_status', 'none') not in ('none', 'pending', 'verified'):
            raise HTTPException(422, '事实状态无效')
        if data.get('fact_status') == 'verified':
            approver(user)
        source_ids = list(set(data.get('source_ids', [])))
        for source_id in source_ids:
            source = row_for(db, user, source_id, 'knowledge')
            if not verified_document(source):
                raise HTTPException(422, '引用知识须已核验且在有效期内')
        snapshot = demand_snapshot(db, user, data['need_id'], data['ip_id']) if data.get('need_id') else None
        row = Record(kind='drafts', ip_id=data['ip_id'], owner_id=user.id, payload={k: data[k] for k in ('title', 'body', 'reference', 'cover') if k in data} | {'skill': skill, 'version': 1, 'approved_version': None, 'status': 'pending', 'fact_status': data.get('fact_status', 'none'), 'history': [], 'source_ids': source_ids})
        db.add(row)
        db.flush()
        if snapshot:
            row.payload = {**row.payload, 'need_id': data['need_id'], 'demand_snapshot': snapshot}
            need = db.get(Record, data['need_id'])
            if need.kind == 'needs':
                need.payload = {**need.payload, 'status': 'writing'}
        audit(db, user, '创建稿件', row.id)
        return public_record(db, user, row)

    @app.get('/api/drafts/{key}')
    def draft_detail(key: str, user=Depends(current), db=Depends(get_db)):
        row = row_for(db, user, key, 'drafts')
        return draft_review(db, user, row, setting_data(db).get('rules', {}))

    @app.patch('/api/drafts/{key}')
    def edit_draft(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        row = row_for(db, user, key, 'drafts')
        text_fields(data)
        if data.get('version') != row.payload['version']:
            raise HTTPException(409, '稿件已经变更，请刷新后对比再保存')
        if len(data.get('body', '')) > 20000 or len(data.get('reference', '')) > 20000:
            raise HTTPException(422, '稿件或参考内容超过2万字')
        history = list(row.payload.get('history', []))
        history.append({k: row.payload.get(k) for k in ('version', 'title', 'body', 'fact_status')})
        patch = {k: v for k, v in data.items() if k in ('title', 'body', 'reference', 'cover', 'fact_status')}
        if 'reference' in patch:
            if not can_review_transcript(db, user, row.payload):
                raise HTTPException(403, '没有对标素材的访问权限')
            patch['transcript_confirmed'] = bool(patch['reference'].strip())
        if 'source_ids' in data:
            source_ids = list(set(data['source_ids']))
            for source_id in source_ids:
                source = row_for(db, user, source_id, 'knowledge')
                if not verified_document(source):
                    raise HTTPException(422, '引用知识须已核验且在有效期内')
            patch['source_ids'] = source_ids
        if patch.get('fact_status', 'none') not in ('none', 'pending', 'verified'):
            raise HTTPException(422, '事实状态无效')
        if patch.get('fact_status') == 'verified':
            approver(user)
        if row.payload.get('fact_status') == 'verified' and 'fact_status' not in patch and any(
            patch.get(k, row.payload.get(k)) != row.payload.get(k) for k in ('title', 'body', 'source_ids')
        ):
            patch['fact_status'] = 'pending'
        row.payload = {**row.payload, **patch, 'history': history, 'version': row.payload['version'] + 1, 'approved_version': None, 'status': 'pending'}
        row.updated_at = now()
        invalidate_schedules(db, {key})
        audit(db, user, '修改稿件并撤销旧批准', key)
        return draft_review(db, user, row, setting_data(db).get('rules', {}))

    @app.post('/api/drafts/{key}/approve')
    def approve(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        approver(user)
        row = row_for(db, user, key, 'drafts')
        if data.get('version') != row.payload['version']:
            raise HTTPException(409, '批准版本已过时')
        for source_id in set(row.payload.get('source_ids', [])) | {x['id'] for x in row.payload.get('sources', [])}:
            source = row_for(db, user, source_id, 'knowledge')
            if not verified_document(source):
                raise HTTPException(409, '引用知识未核验或已过期，请复核')
        results = draft_checks(db, row.payload, setting_data(db).get('rules', {}))
        if any(not c['passed'] and c['blocking'] for c in results):
            raise HTTPException(409, '稿件存在阻塞检查项，请先修正或核实事实')
        row.payload = {**row.payload, 'approved_version': row.payload['version'], 'status': 'approved', 'approved_by': user.id, 'approved_at': now()}
        audit(db, user, '批准稿件', key)
        return draft_review(db, user, row, setting_data(db).get('rules', {}))

    @app.post('/api/drafts/{key}/return')
    def return_draft(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        approver(user)
        row = row_for(db, user, key, 'drafts')
        if not data.get('reason'):
            raise HTTPException(422, '请填写退回原因')
        row.payload = {**row.payload, 'status': 'returned', 'approved_version': None, 'return_reason': data['reason']}
        invalidate_schedules(db, {key})
        audit(db, user, '退回稿件', key)
        return draft_review(db, user, row, setting_data(db).get('rules', {}))

    @app.get('/api/assets')
    def assets(user=Depends(current), db=Depends(get_db)):
        return [as_record(r) for r in visible(db, user, 'assets')]

    @app.post('/api/assets')
    def upload_asset(draft_id: str = Form(...), file: UploadFile = File(...), user=Depends(current), db=Depends(get_db)):
        draft = row_for(db, user, draft_id, 'drafts')
        if draft.payload.get('approved_version') != draft.payload['version']:
            raise HTTPException(409, '请先批准当前稿件版本')
        raw = file.file.read(100 * 1024 * 1024 + 1)
        if len(raw) > 100 * 1024 * 1024:
            raise HTTPException(422, '媒体文件上限100MB')
        ext = Path(file.filename or '').suffix.lower()
        is_video = ext in ('.mp4', '.mov') and b'ftyp' in raw[:32]
        is_image = (ext in ('.jpg', '.jpeg') and raw.startswith(b'\xff\xd8\xff')) or (ext == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n'))
        if not is_video and not is_image:
            raise HTTPException(422, '请上传有效MP4/MOV视频或PNG/JPEG封面')
        key = uid()
        (folder / 'assets' / key).write_bytes(raw)
        row = Record(id=key, kind='assets', ip_id=draft.ip_id, owner_id=user.id, payload={'draft_id': draft_id, 'draft_version': draft.payload['version'], 'filename': Path(file.filename).name, 'type': 'video' if is_video else 'cover', 'hash': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
        db.add(row)
        db.flush()
        audit(db, user, '上传并明确关联媒体', key)
        return public_record(db, user, row)

    @app.get('/api/assets/{key}/download')
    def download(key: str, user=Depends(current), db=Depends(get_db)):
        row = row_for(db, user, key, 'assets')
        return FileResponse(folder / 'assets' / key, filename=row.payload['filename'])

    def ensure_draft_ready(db, user, draft):
        p = draft.payload
        if p.get('approved_version') != p['version'] or not p.get('approved_version'):
            raise HTTPException(409, '当前稿件批准已经失效')
        if any(not result['passed'] and result['blocking'] for result in draft_checks(db, p, setting_data(db).get('rules', {}))):
            raise HTTPException(409, '稿件不再满足当前审稿规则，请重新检查')
        for source_id in set(p.get('source_ids', [])) | {x['id'] for x in p.get('sources', [])}:
            source = row_for(db, user, source_id, 'knowledge')
            if not verified_document(source):
                raise HTTPException(409, '引用知识已失效或过期，稿件需要重新审核')

    @app.get('/api/schedules')
    def schedules(user=Depends(current), db=Depends(get_db)):
        return [as_record(r) for r in visible(db, user, 'schedules')]

    @app.post('/api/schedules')
    def create_schedule(data: dict, user=Depends(current), db=Depends(get_db)):
        draft = row_for(db, user, data.get('draft_id'), 'drafts')
        p = draft.payload
        ensure_draft_ready(db, user, draft)
        if not p.get('approved_version') or p['approved_version'] != p['version']:
            raise HTTPException(409, '当前稿件未批准')
        media = [a for a in visible(db, user, 'assets') if a.payload.get('draft_id') == draft.id and a.payload['draft_version'] == p['version'] and a.payload['type'] == 'video']
        if not media:
            raise HTTPException(409, '当前批准版本尚无视频资产')
        if not data.get('account') or data.get('platform') not in ('抖音', '视频号', '小红书', '哔哩哔哩', '公众号'):
            raise HTTPException(422, '请选择平台并填写明确账号')
        try:
            planned = datetime.fromisoformat(data['planned_at'])
            if planned.tzinfo is None:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, '排期时间必须包含时区')
        for existing in visible(db, user, 'schedules'):
            ep = existing.payload
            if ep['draft_id'] == draft.id and ep['version'] == p['version'] and ep['platform'] == data['platform'] and ep['account'] == data['account'] and ep['status'] not in ('cancelled', 'needs_review'):
                return as_record(existing)
        platform = data['platform']
        payload = {
            'draft_id': draft.id, 'title': p['title'], 'version': p['version'], 'platform': platform,
            'account': data['account'], 'planned_at': planned.isoformat(), 'status': 'planned',
            'asset_id': media[0].id, 'snapshot': {'body': p['body'], 'asset_hash': media[0].payload['hash']},
        }
        if platform in WECHAT_PLATFORMS:
            payload['touchpoints'] = dict.fromkeys(WECHAT_TOUCHPOINTS, 'unprepared')
        row = Record(kind='schedules', ip_id=draft.ip_id, owner_id=user.id, payload=payload)
        db.add(row)
        db.flush()
        audit(db, user, '创建发布排期', row.id)
        return public_record(db, user, row)

    @app.patch('/api/schedules/{key}')
    def update_schedule(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        row = row_for(db, user, key, 'schedules')
        status = data.get('status', row.payload['status'])
        if status not in ('planned', 'staged', 'published', 'unknown', 'cancelled'):
            raise HTTPException(422, '发布状态无效')
        old = row.payload['status']
        if old == 'published' and status != 'published':
            raise HTTPException(409, '已发布记录不能倒退')
        draft = db.get(Record, row.payload['draft_id'])
        if status in ('planned', 'staged', 'published') and (draft.payload.get('approved_version') != row.payload['version'] or draft.payload['version'] != row.payload['version']):
            raise HTTPException(409, '稿件批准版本已变化，请重新审核排期')
        if status in ('planned', 'staged', 'published'):
            ensure_draft_ready(db, user, draft)
        if status == 'published' and not str(data.get('evidence', row.payload.get('evidence', ''))).strip():
            raise HTTPException(422, '需要实际发表链接或人工核验凭证')
        if old == 'unknown' and status in ('planned', 'staged'):
            raise HTTPException(409, '结果未知需先核对，不允许直接重试')
        row.payload = {**row.payload, 'status': status, 'evidence': data.get('evidence', row.payload.get('evidence')), 'published_at': (row.payload.get('published_at') or now()) if status == 'published' else None}
        audit(db, user, '记录发布状态与凭证', key)
        return public_record(db, user, row)

    @app.patch('/api/schedules/{key}/touchpoints')
    def touchpoints(key: str, data: dict, user=Depends(current), db=Depends(get_db)):
        row = row_for(db, user, key, 'schedules')
        if row.payload.get('platform') not in WECHAT_PLATFORMS:
            raise HTTPException(422, '只有视频号和公众号排期可以管理微信生态触点')
        channel = data.get('channel')
        state = data.get('status')
        if channel not in row.payload['touchpoints'] or state not in ('unprepared', 'producing', 'review', 'dependency', 'manual', 'published', 'skipped'):
            raise HTTPException(422, '触点或状态无效')
        if state in ('published', 'skipped') and not data.get('evidence'):
            raise HTTPException(422, '发表需要凭证，跳过需要原因')
        row.payload = {**row.payload, 'touchpoints': {**row.payload['touchpoints'], channel: state}, 'touchpoint_evidence': {**row.payload.get('touchpoint_evidence', {}), channel: data.get('evidence', '')}}
        return public_record(db, user, row)

    @app.post('/api/metrics/import')
    def import_metrics(ip_id: str = Form(...), platform: str = Form(...), file: UploadFile = File(...), preview: bool = Form(False), user=Depends(current), db=Depends(get_db)):
        require_ip(db, user, ip_id)
        raw = file.file.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise HTTPException(422, '报表上限20MB')
        if platform not in ('抖音', '视频号', '小红书', '哔哩哔哩', '公众号'):
            raise HTTPException(422, '不支持的平台')
        try:
            if (file.filename or '').lower().endswith('.xlsx'):
                import openpyxl
                import zipfile
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if sum(info.file_size for info in archive.infolist()) > 80 * 1024 * 1024:
                        raise ValueError('解压大小超过限制')
                book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
                rows = book.active.iter_rows(values_only=True)
                header = next(rows)
                entries = [dict(zip(header, r)) for r in rows]
            else:
                entries = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
        except Exception:
            raise HTTPException(422, '报表无法读取，请使用UTF-8 CSV或XLSX')
        if len(entries) > 10000:
            raise HTTPException(422, '单批最多10000行')
        added, errors, normalized = 0, [], []
        for index, entry in enumerate(entries, 2):
            row = {str(k): str(v) if v is not None else '' for k, v in entry.items()}
            date = row.get('date', row.get('日期', ''))[:10]
            work = row.get('work_id', row.get('作品ID', ''))
            try:
                datetime.strptime(date, '%Y-%m-%d')
                if not work:
                    raise ValueError()
                metrics = {}
                for metric, cn in [('views', '播放量'), ('leads', '线索数'), ('likes', '点赞量')]:
                    val = row.get(metric, row.get(cn, ''))
                    metrics[metric] = int(val) if val != '' else None
                    if metrics[metric] is not None and metrics[metric] < 0:
                        raise ValueError()
            except (ValueError, TypeError):
                errors.append({'line': index, 'reason': '需要date日期、work_id作品ID和非负整数指标'})
                continue
            key = hashlib.sha256(f'{ip_id}|{platform}|{work}|{date}'.encode()).hexdigest()
            payload = {'date': date, 'work_id': work, 'platform': platform, **metrics, 'metric_type': 'cumulative_snapshot'}
            normalized.append(payload)
            if preview:
                continue
            existing = db.get(Record, key)
            if existing:
                existing.payload = payload
            else:
                db.add(Record(id=key, kind='metrics', ip_id=ip_id, owner_id=user.id, payload=payload))
                added += 1
        if preview:
            return {'preview': True, 'rows': len(entries), 'items': normalized[:100], 'valid_rows': len(normalized), 'errors': errors}
        batch = Record(kind='metric_batch', ip_id=ip_id, owner_id=user.id, payload={'filename': file.filename, 'hash': hashlib.sha256(raw).hexdigest(), 'rows': len(entries), 'added': added, 'errors': errors})
        db.add(batch)
        db.flush()
        return {'id': batch.id, 'added': added, 'errors': errors, 'rows': len(entries)}

    @app.get('/api/reports')
    def reports(ip_id: str = '', user=Depends(current), db=Depends(get_db)):
        if ip_id:
            require_ip(db, user, ip_id)
        def scoped(kind):
            return [r for r in visible(db, user, kind) if not ip_id or r.ip_id == ip_id]
        latest = {}
        for r in sorted(scoped('metrics'), key=lambda x: x.payload['date']):
            latest[(r.ip_id, r.payload['platform'], r.payload['work_id'])] = r.payload
        rows = list(latest.values())
        leads = scoped('leads')
        effective_count = sum(labels(r.payload)['effective_demand'] for r in leads)
        contactable_count = sum(r.payload.get('contactable', True) is not False for r in leads)
        return {'works': len(rows), 'views': sum(r.get('views') or 0 for r in rows), 'leads': sum(r.get('leads') or 0 for r in rows), 'missing_views': sum(r.get('views') is None for r in rows), 'sections': [
            {'title': '本期概况', 'body': f'已导入{len(rows)}条独立平台作品，使用每条作品最后一个累计快照；不能当作本周新增。'},
            {'title': '内容表现', 'body': '按平台分开对比作品；缺失指标不替代为零。'},
            {'title': '客户需求', 'body': f'客户线索中有 {effective_count} 条有效需求，可查看原话和来源。'},
            {'title': '获客与转化', 'body': f'人工登记或已标记线索 {contactable_count} 条；平台指标与实际客户身份未核对时，不计算转化率。'},
            {'title': '下期动作', 'body': '选一个主要变量做内容实验；写手规则需独立证据与人工采纳。'}]}

    @app.get('/api/dashboard')
    def dashboard(ip_id: str = '', user=Depends(current), db=Depends(get_db)):
        if ip_id:
            require_ip(db, user, ip_id)
        def scoped(kind):
            return [r for r in visible(db, user, kind) if not ip_id or r.ip_id == ip_id]
        drafts = scoped('drafts')
        return {'pending': sum(r.payload.get('status') != 'approved' for r in drafts), 'shooting': sum(r.payload.get('status') == 'approved' for r in drafts), 'knowledge': len(visible(db, user, 'knowledge')), 'leads': sum(r.payload.get('contactable', True) is not False for r in scoped('leads')), 'jobs': len(scoped('jobs')), 'integrations': '外部集成尚未真实验证', 'worker': (folder / 'worker-heartbeat').read_text() if (folder / 'worker-heartbeat').exists() else None}

    from .integrations.routes import register
    register(app, get_db, current, audit)

    from .writer_profiles import register as register_writers
    register_writers(app, get_db, current, audit)
    from .demand_analysis import register as register_demands
    register_demands(app, get_db, current, audit)
    from .default_writers import register as register_defaults
    register_defaults(app, get_db, current, audit)
    from .material_writing import register as register_material_writing
    register_material_writing(app, get_db, current, audit)
    from .integrations.transcript import register as register_transcript
    register_transcript(app, audit, authenticated_user)
    from .rewrite import register as register_rewrite
    register_rewrite(app, get_db, current, audit)
    from .operations.routes import register_operations
    register_operations(app, authenticated_user)
    from .operations.agent_routes import register_agent_routes
    register_agent_routes(app)

    dist = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets', check_dir=False), name='frontend-assets')
        @app.get('/')
        def index():
            return FileResponse(dist / 'index.html')
    return app
