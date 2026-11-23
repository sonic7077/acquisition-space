from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import select
from ..db import Record, User, uid, now, as_record
from ..security import can_record


class IntegrationError(Exception):
    """Only deliberately sanitized messages may be exposed to the user."""


def config(app, section):
    with app.state.Session() as db:
        row = db.get(Record, 'system-settings')
        return dict(row.payload.get(section, {})) if row else {}


def client(app):
    return httpx.Client(transport=getattr(app.state, 'integration_transport', None), timeout=30, follow_redirects=False, trust_env=False)


def queue(db, owner_id, ip_id, task, options):
    row = Record(kind='collection_runs', ip_id=ip_id, owner_id=owner_id,
                 payload={'task': task, 'options': options, 'status': 'queued', 'calls': 0,
                          'processed': 0, 'materials': 0, 'authors': 0, 'comments': 0,
                          'estimated_calls': options.get('estimated_calls', 0)})
    db.add(row)
    db.flush()
    return as_record(row)


def authorized(db, row):
    user = db.get(User, row.owner_id)
    return user and user.active and can_record(db, user, row)


def claim(app, key, task):
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(Record, key)
        if not row or row.kind != 'collection_runs' or row.payload.get('task') != task or row.payload.get('status') != 'queued':
            return None
        if not authorized(db, row):
            row.payload = {**row.payload, 'status': 'failed', 'error': '提交者权限已变更，任务已取消'}
            db.commit()
            return None
        row.payload = {**row.payload, 'status': 'running', 'started_at': now(), 'lease_until': lease()}
        db.commit()
        return {**as_record(row), 'owner_id': row.owner_id}


def lease():
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()


def update(app, key, **patch):
    (app.state.data_dir / 'worker-heartbeat').write_text(now())
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(Record, key)
        row.payload = {**row.payload, **patch, 'lease_until': lease()}
        row.updated_at = now()
        db.commit()


def finish(app, key, error=None, uncertain=False):
    with app.state.Session() as db:
        row = db.get(Record, key)
        partial = uncertain or row.payload.get('processed', 0) > 0
    update(app, key, status=('partial' if partial else 'failed') if error else 'completed',
           error=error, finished_at=now())


def pending(app):
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        rows = list(db.scalars(select(Record).where(Record.kind == 'collection_runs').order_by(Record.created_at)))
        for row in rows:
            if row.payload.get('status') == 'running' and row.payload.get('lease_until', '') < now():
                row.payload = {**row.payload, 'status': 'partial', 'error': '执行中断，请核对后手动重试；已完成的数据保留'}
        item = next((as_record(r) for r in rows if r.payload.get('status') == 'queued'), None)
        db.commit()
        return item
