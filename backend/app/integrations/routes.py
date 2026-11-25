import re
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from ..db import Record, as_record, business_today
from ..discovery import rules_for
from ..security import require_ip, admin_only, can_record, require_record, can_ip
from .common import queue, IntegrationError
from .feishu import Feishu
from .platforms import PLATFORMS


class SearchInput(BaseModel):
    ip_id: str | None = None
    platforms: list[Literal['douyin', 'xiaohongshu', 'wechat_channels']] = Field(default=['douyin', 'xiaohongshu'], min_length=1, max_length=3)
    keywords: str = Field(min_length=1, max_length=300)
    pages: int = Field(default=1, ge=1, le=5)
    max_videos: int = Field(default=20, ge=1, le=20)
    per_word_limit: int = Field(default=10, ge=1, le=20)
    comment_pages: int = Field(default=1, ge=1, le=5)
    enrich_authors: bool = False
    sort_type: Literal['0', '1', '2'] = '1'
    publish_time: Literal['platform_default', '0', '1', '7', '180'] = 'platform_default'

    @field_validator('ip_id')
    @classmethod
    def empty_ip(cls, value):
        return value or None


class SavedSearchInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    options: SearchInput


def search_options(data):
    keywords = list(dict.fromkeys(k.strip() for k in re.split(r'[,，、\n]+', data.keywords) if k.strip()))
    if not keywords or len(keywords) > 5 or any(len(k) > 100 for k in keywords):
        raise HTTPException(422, '请填写1至5个关键词，每个最多100字，以逗号、顿号或换行分隔')
    opts = data.model_dump()
    opts['platforms'] = list(dict.fromkeys(data.platforms))
    opts['keywords_list'] = keywords
    maximum = min(data.max_videos, data.per_word_limit * len(keywords))
    opts['estimated_calls'] = len(keywords) * data.pages * len(opts['platforms']) + maximum * (data.comment_pages * len(opts['platforms']) + int('wechat_channels' in opts['platforms']) + int(data.enrich_authors and 'douyin' in opts['platforms']))
    return opts


class CommentInput(BaseModel):
    pages: int = Field(default=1, ge=1, le=5)


class SyncInput(BaseModel):
    ip_id: str | None = None
    all_scopes: bool = False
    include_library: bool = False


def register(app, get_db, current, audit):
    def settings(db, section):
        row = db.get(Record, 'system-settings')
        return row.payload.get(section, {}) if row else {}

    def ready(db):
        cfg = settings(db, 'collection')
        if not cfg.get('enabled') or not cfg.get('api_key_encrypted'):
            raise HTTPException(409, '请管理员在系统设置中配置并启用 TikHub API Key')

    def enqueue(db, user, ip_id, task, opts):
        if ip_id:
            require_ip(db, user, ip_id)
        active = [r for r in db.scalars(select(Record).where(Record.kind == 'collection_runs', Record.ip_id == ip_id)) if (ip_id or r.owner_id == user.id) and r.payload.get('status') in ('queued', 'running') and r.payload.get('task') == task]
        if len(active) >= 3:
            raise HTTPException(409, '当前范围已有3个等待或运行中的任务，请稍后提交')
        result = queue(db, user.id, ip_id, task, opts)
        audit(db, user, '提交' + task + '任务', result['id'])
        return result

    @app.get('/api/collection/status')
    def status(user=Depends(current), db=Depends(get_db)):
        cfg = settings(db, 'collection')
        fs = settings(db, 'feishu')
        ledger = db.get(Record, 'tikhub-budget-' + business_today())
        return {'configured': bool(cfg.get('api_key_encrypted')), 'enabled': bool(cfg.get('enabled')),
                'daily_budget': int(cfg.get('daily_budget') or 100), 'today_calls': ledger.payload['calls'] if ledger else 0,
                'feishu_enabled': bool(fs.get('enabled')), 'platforms': list(PLATFORMS.values()), 'platform_ids': list(PLATFORMS)}

    @app.post('/api/collection/search')
    def search(data: SearchInput, user=Depends(current), db=Depends(get_db)):
        if data.ip_id:
            require_ip(db, user, data.ip_id)
        ready(db)
        opts = search_options(data)
        return enqueue(db, user, data.ip_id, 'collection', opts)

    @app.get('/api/discovery/rules')
    def discovery_rules(user=Depends(current), db=Depends(get_db)):
        row = db.get(Record, 'system-settings')
        return rules_for(row.payload if row else {})

    @app.get('/api/discovery/searches')
    def saved_searches(user=Depends(current), db=Depends(get_db)):
        return [as_record(r) for r in db.scalars(select(Record).where(Record.kind == 'search_plans', Record.owner_id == user.id).order_by(Record.created_at.desc())) if not r.ip_id or can_ip(db, user, r.ip_id)]

    @app.post('/api/discovery/searches')
    def save_search(data: SavedSearchInput, user=Depends(current), db=Depends(get_db)):
        if data.options.ip_id:
            require_ip(db, user, data.options.ip_id)
        if not data.name.strip():
            raise HTTPException(422, '请填写方案名称')
        opts = search_options(data.options)
        count = len(list(db.scalars(select(Record).where(Record.kind == 'search_plans', Record.owner_id == user.id))))
        if count >= 30:
            raise HTTPException(409, '最多保存30个私人搜索方案，请先删除不用的方案')
        row = Record(kind='search_plans', ip_id=data.options.ip_id, owner_id=user.id, payload={'name': data.name.strip(), 'options': opts})
        db.add(row)
        db.flush()
        audit(db, user, '保存私人搜索方案', row.id)
        return as_record(row)

    @app.delete('/api/discovery/searches/{key}')
    def delete_search(key: str, user=Depends(current), db=Depends(get_db)):
        row = db.get(Record, key)
        if not row or row.kind != 'search_plans' or row.owner_id != user.id:
            raise HTTPException(404, '搜索方案不存在')
        db.delete(row)
        audit(db, user, '删除私人搜索方案', key)
        return {'ok': True}

    @app.get('/api/collection/runs')
    def runs(user=Depends(current), db=Depends(get_db)):
        return [as_record(r) for r in db.scalars(select(Record).where(Record.kind == 'collection_runs').order_by(Record.created_at.desc())) if can_record(db, user, r)]

    @app.post('/api/collection/retry/{key}')
    def retry(key: str, user=Depends(current), db=Depends(get_db)):
        row = db.get(Record, key)
        if not row or row.kind != 'collection_runs':
            raise HTTPException(404, '任务不存在')
        require_record(db, user, row)
        if row.payload.get('status') not in ('partial', 'failed'):
            raise HTTPException(409, '只能重试失败或部分完成任务')
        if row.payload['task'] == 'collection':
            ready(db)
        return enqueue(db, user, row.ip_id, row.payload['task'], {**row.payload['options'], 'retry_of': key})

    @app.post('/api/collection/materials/{key}/comments')
    def comments(key: str, data: CommentInput, user=Depends(current), db=Depends(get_db)):
        row = db.get(Record, key)
        if not row or row.kind != 'materials':
            raise HTTPException(404, '素材不存在')
        require_record(db, user, row)
        ready(db)
        if row.payload.get('source_type') != 'tikhub' or not row.payload.get('platform_id'):
            raise HTTPException(422, '仅支持TikHub获取的作品')
        return enqueue(db, user, row.ip_id, 'collection', {'material_id': key, 'pages': data.pages, 'estimated_calls': data.pages})

    def schema_action(db, prepare=False):
        connection = None
        try:
            connection = Feishu(app, settings(db, 'feishu'))
            return connection.prepare() if prepare else connection.schema()
        except IntegrationError as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception:
            raise HTTPException(409, '飞书连接或字段映射无效，请核对系统设置') from None
        finally:
            if connection:
                connection.http.close()

    @app.get('/api/integrations/feishu/schema')
    def schema(user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        return schema_action(db)

    @app.post('/api/integrations/feishu/prepare')
    def prepare(user=Depends(current), db=Depends(get_db)):
        admin_only(user)
        result = schema_action(db, True)
        audit(db, user, '准备飞书同步字段（保留原字段和公式）')
        return result

    @app.post('/api/integrations/feishu/library/prepare')
    def prepare_library(user=Depends(current), db=Depends(get_db)):
        from .feishu_library import schema as library_schema
        admin_only(user)
        connection=None
        try:
            connection=Feishu(app,settings(db,'feishu'))
            connection.prepare()
            result=library_schema(connection,prepare=True)
            audit(db,user,'准备飞书材料表（知识库、写手SKILL、改写任务、IP账号）')
            return result
        except IntegrationError as exc:
            raise HTTPException(409,str(exc)) from None
        except Exception:
            raise HTTPException(409,'飞书材料表结构读取失败，请核对模板与字段配置') from None
        finally:
            if connection:
                connection.http.close()

    @app.post('/api/integrations/feishu/preview')
    def preview(data: SyncInput, user=Depends(current), db=Depends(get_db)):
        from .feishu import KINDS, sync_rows
        if data.all_scopes or data.include_library:
            admin_only(user)
        if data.ip_id:
            require_ip(db, user, data.ip_id)
        run={'ip_id':data.ip_id,'owner_id':user.id,'options':{'all_scopes':data.all_scopes}}
        counts={name:len(sync_rows(db,kind,run)) for name,kind in KINDS.items()}
        unmapped={kind:len(sync_rows(db,kind,run)) for kind in ('knowledge','writers','drafts','needs')}
        from .feishu_library import counts as library_counts
        library=library_counts(db,run) if data.include_library else None
        return {'counts':counts,'unmapped_counts':unmapped,'library_counts':library,
                'notice':('全部材料同步包含知识库、8个系统写手、专属写手、稿件和IP账号；需求库暂未映射。' if data.include_library else '三表同步包括博主、作品、平台原始评论。其他材料可由管理员使用全部材料同步。')}

    @app.post('/api/integrations/feishu/sync')
    def sync(data: SyncInput, user=Depends(current), db=Depends(get_db)):
        if data.all_scopes or data.include_library:
            admin_only(user)
        if data.ip_id:
            require_ip(db, user, data.ip_id)
        cfg = settings(db, 'feishu')
        if not cfg.get('enabled') or (cfg.get('auth_mode') != 'cli' and (not cfg.get('app_id') or not cfg.get('app_secret_encrypted'))):
            raise HTTPException(409, '请管理员配置并启用飞书同步')
        return enqueue(db, user, data.ip_id, 'feishu_sync', {'all_scopes':data.all_scopes,'include_library':data.include_library})
