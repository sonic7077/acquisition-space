"""Local administrator recovery and consistent database backups."""
import argparse
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import shutil
import sqlite3
from sqlalchemy import select
from .db import User, LoginSession, Record, now
from .security import password_hash


def reset_password(app, username, password):
    with app.state.Session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        user = db.scalar(select(User).where(User.username == username))
        if not user or user.role != 'admin' or not user.active:
            raise ValueError('只能恢复已有且有效的管理员账号')
        user.password_hash = password_hash(password)
        user.failures = 0
        user.locked_until = None
        for sess in db.scalars(select(LoginSession).where(LoginSession.user_id == user.id)):
            db.delete(sess)
        db.add(Record(kind='audit', owner_id=user.id, payload={'actor': '本机管理员恢复命令', 'action': '重置管理员密码并撤销会话', 'object_id': user.id}))
        db.commit()


def backup(app, destination):
    source = app.state.data_dir.resolve()
    target = Path(destination).resolve()
    if target.exists() or source in target.parents or target == source:
        raise ValueError('请指定数据目录之外、尚不存在的备份目录')
    target.mkdir(parents=True, mode=0o700)
    try:
        with sqlite3.connect(source / 'system.db') as live, sqlite3.connect(target / 'system.db') as snap:
            live.backup(snap)
        shutil.copy2(source / 'master.key', target / 'master.key')
        os.chmod(target / 'master.key', 0o600)
        shutil.copytree(source / 'assets', target / 'assets')
        (target / 'backup-info.txt').write_text('获客空间数据备份\n时间：' + now() + '\n包括数据库、加密主密钥和上传资产。恢复前停止Web与worker，使用整个目录。\n')
    except Exception:
        # Keep partial backups inspectable; never present them as valid.
        (target / 'INCOMPLETE').write_text('备份未完成，不可用于恢复。')
        raise
    return target


def main():
    from .main import create_app
    parser = argparse.ArgumentParser(description='获客空间本机管理工具')
    parser.add_argument('action', choices=['reset-password', 'backup', 'import-legacy'])
    parser.add_argument('--username')
    parser.add_argument('--destination')
    parser.add_argument('--source')
    parser.add_argument('--apply', action='store_true', help='实际写入；不传时仅预览')
    args = parser.parse_args()
    app = create_app()
    if args.action == 'reset-password':
        if not args.username:
            parser.error('需要 --username')
        password = getpass.getpass('新管理员密码（至少10位）：')
        if password != getpass.getpass('再次输入：'):
            parser.error('两次密码不一致')
        reset_password(app, args.username, password)
        print('管理员密码已更新，既有会话已撤销。')
    elif args.action == 'import-legacy':
        from .legacy_import import import_archive
        if not args.source or not args.username:
            parser.error('需要 --source 和 --username（已有管理员）')
        with app.state.Session() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            user = db.scalar(select(User).where(User.username == args.username))
            if not user:
                parser.error('管理员不存在')
            result = import_archive(db, user, args.source, apply=args.apply)
            if args.apply:
                db.commit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        target = args.destination or ('backups/' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        print('备份已保存：' + str(backup(app, target)))


if __name__ == '__main__':
    main()
