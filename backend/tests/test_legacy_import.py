import json
import pytest
from sqlalchemy import select
from backend.app.main import create_app
from backend.app.db import Record, User
from backend.app.legacy_import import import_archive


def archive(tmp_path):
    p = tmp_path / 'archive.json'
    p.write_text(json.dumps({'accounts': [{'name': '保险博主', 'url': 'https://www.douyin.com/user/sec1', 'tier': '头部同行', 'fans': 200, 'median_base': 12}], 'works': [{'id': '保险博主_123', 'account_name': '保险博主', 'video_url': 'https://www.douyin.com/video/123', 'title': '保险怎么买', 'likes': 800, 'comments': 30, 'publish_time': '2026-11-01T00:00:00+08:00'}], 'comments': [{'work_id': '保险博主_123', 'user': '甲', 'text': '多少钱？', 'likes': 1}]}))
    return p


def test_preview_import_links_and_repeat_preserves_local_edits(tmp_path):
    app = create_app(tmp_path / 'data'); source = archive(tmp_path)
    with app.state.Session() as db:
        user = db.scalar(select(User))
        preview = import_archive(db, user, source)
        assert preview['created'] == {'authors': 1, 'materials': 1, 'leads': 1}
        assert not list(db.scalars(select(Record).where(Record.kind == 'authors')))
        result = import_archive(db, user, source, apply=True); db.commit()
        assert result['created'] == preview['created']
        a = db.scalar(select(Record).where(Record.kind == 'authors'))
        m = db.scalar(select(Record).where(Record.kind == 'materials'))
        c = db.scalar(select(Record).where(Record.kind == 'leads'))
        assert m.payload['author_id'] == a.id and c.payload['material_id'] == m.id
        assert c.payload['author_id'] == a.id and c.payload['intent'] == '问价格'
        assert not c.payload['contactable'] and not c.payload.get('platform_comment_id')
        assert a.payload['legacy_baseline']['median_likes'] == 12
        assert 'collected_at' not in a.payload and 'interaction_snapshots' not in m.payload
        m.payload = {**m.payload, 'likes': 9000, 'status': 'adopted'}
        a.payload = {**a.payload, 'tier': '跨界结构'}
        db.commit()
        second = import_archive(db, user, source, apply=True); db.commit()
        assert second['created'] == {'authors': 0, 'materials': 0, 'leads': 0}
        assert m.payload['likes'] == 9000 and m.payload['status'] == 'adopted'
        assert a.payload['tier'] == '跨界结构'


def test_invalid_archive_and_nonadmin_are_rejected_before_writes(tmp_path):
    app = create_app(tmp_path / 'data'); source = archive(tmp_path)
    with app.state.Session() as db:
        user = db.scalar(select(User)); user.role = 'member'
        with pytest.raises(ValueError, match='管理员'): import_archive(db, user, source, apply=True)
        user.role = 'admin'; d = json.loads(source.read_text()); d['comments'][0]['work_id'] = 'missing'; source.write_text(json.dumps(d))
        with pytest.raises(ValueError, match='作品'): import_archive(db, user, source, apply=True)
        assert not list(db.scalars(select(Record).where(Record.kind == 'authors')))


def test_identity_reuses_platform_id_not_nickname(tmp_path):
    app = create_app(tmp_path / 'data'); source = archive(tmp_path)
    with app.state.Session() as db:
        user = db.scalar(select(User))
        db.add(Record(id='existing', kind='authors', owner_id=user.id, payload={'title': '改过昵称', 'platform': '抖音', 'platform_id': 'sec1', 'followers': 999, 'tier': '跨界结构'})); db.flush()
        result = import_archive(db, user, source, apply=True)
        assert result['created']['authors'] == 0
        assert db.scalar(select(Record).where(Record.kind == 'materials')).payload['author_id'] == 'existing'
        assert db.get(Record, 'existing').payload['followers'] == 999
