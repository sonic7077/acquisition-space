import hashlib
import hmac
import os
from pathlib import Path
from cryptography.fernet import Fernet
from fastapi import HTTPException
from .db import User, Record

ROLES = ('admin', 'leader', 'member')


def password_hash(password, *, default_admin=False):
    if (len(password) < 10 and not (default_admin and password == "admin")) or len(password) > 256:
        raise HTTPException(422, '密码长度应为10至256位')
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + digest.hex()


def password_ok(password, stored):
    if len(password) > 256:
        return False
    salt, expected = stored.split(':')
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, expected)


def public_user(u):
    return {'id': u.id, 'username': u.username, 'name': u.name, 'role': u.role, 'team_id': u.team_id, 'ip_ids': u.ip_ids, 'active': u.active}


def admin_only(user):
    if user.role != 'admin':
        raise HTTPException(403, '仅管理员可以操作系统设置和用户管理')


def approver(user):
    if user.role not in ('admin', 'leader'):
        raise HTTPException(403, '仅管理员或团队长可以批准')


def active_ip(row):
    return row is not None and row.kind == 'ips' and not row.payload.get('archived_at')


def can_ip(db, user, ip_id):
    ip = db.get(Record, ip_id)
    if not active_ip(ip):
        return False
    if user.role == 'admin':
        return True
    if ip.payload.get('team_id') != user.team_id:
        return False
    return user.role == 'leader' or ip.id in user.ip_ids


def require_ip(db, user, ip_id):
    if not ip_id or not can_ip(db, user, ip_id):
        raise HTTPException(403, '没有此IP的访问权限')


def can_record(db, user, row):
    """Shared knowledge is readable across teams; import previews stay private."""
    if row.ip_id:
        return can_ip(db, user, row.ip_id)
    if row.kind == 'knowledge':
        return True
    if row.kind == 'import_batch':
        return user.role == 'admin' or row.owner_id == user.id
    if row.kind not in ('materials', 'authors', 'leads', 'collection_runs'):
        return False
    if user.role == 'admin' or row.owner_id == user.id:
        return True
    owner = db.get(User, row.owner_id) if row.owner_id else None
    return bool(user.role == 'leader' and user.team_id and owner and owner.team_id == user.team_id)


def require_record(db, user, row):
    if not can_record(db, user, row):
        raise HTTPException(403, '没有此记录的访问权限')


class SecretStore:
    def __init__(self, directory: Path):
        path = directory / 'master.key'
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(Fernet.generate_key())
        os.chmod(path, 0o600)
        self.cipher = Fernet(path.read_bytes())

    def encrypt(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.cipher.decrypt(value.encode()).decode() if value else ''
