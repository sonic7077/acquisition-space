from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db import Record


def test_video_generation_removed_and_legacy_configuration_cleared(tmp_path):
    app = create_app(tmp_path)
    with app.state.Session() as db:
        row = db.get(Record, 'system-settings')
        if not row:
            row = Record(id='system-settings', kind='settings', payload={'video': {'enabled': True, 'api_key_encrypted': 'legacy-secret'}, 'model': {'model': 'keep-model'}})
            db.add(row)
        else:
            row.payload = {**row.payload, 'video': {'enabled': True, 'api_key_encrypted': 'legacy-secret'}, 'model': {'model': 'keep-model'}}
        db.add(Record(id='old-video-job', kind='video_jobs', payload={'status': 'queued'}))
        db.add(Record(id='keep-asset', kind='assets', payload={'type': 'video', 'source_type': 'ai_generated'}))
        db.commit()
    app = create_app(tmp_path)
    c = TestClient(app)
    assert c.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 200
    settings = c.get('/api/settings').json()
    assert 'video' not in settings
    assert settings['model']['model'] == 'keep-model'
    assert c.put('/api/settings', json={'video': {'enabled': True}}).status_code == 422
    assert not any(path.startswith('/api/video') for path in c.get('/openapi.json').json()['paths'])
    with app.state.Session() as db:
        assert db.get(Record, 'old-video-job').payload['status'] == 'cancelled'
        assert 'video' not in db.get(Record, 'system-settings').payload
        assert db.get(Record, 'keep-asset').payload['type'] == 'video'
