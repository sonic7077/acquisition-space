import io
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db import Record,User
from sqlalchemy import select


@pytest.fixture()
def env(tmp_path):
    app=create_app(tmp_path);c=TestClient(app);c.post('/api/auth/login',json={'username':'admin','password':'admin'})
    team=c.post('/api/teams',json={'name':'知识目录组'}).json();ip=c.post('/api/ips',json={'name':'知识IP','team_id':team['id']}).json()
    return app,c,ip,team,tmp_path


def upload(c,ip=None,category='product-critical-illness',name='条款.md',content='重疾险相关说明，具体保障范围按条款核对。'.encode()):
    data={}
    if ip is not None:data['ip_id']=ip
    if category is not None:data['category_id']=category
    return c.post('/api/knowledge/preview',data=data,files={'files':(name,content,'application/octet-stream')})


def test_leaf_required_and_preview_confirm_destination(env):
    app,c,ip,team,path=env
    for category in [None,'products','unknown']:
        assert upload(c,ip['id'],category).status_code==422
    tree=c.get('/api/knowledge/categories');assert tree.status_code==200
    roots=tree.json()['tree'];assert len(roots)==7
    assert roots[2]['name']=='2-产品库' and any(x['name']=='重疾险' for x in roots[2]['children'])
    r=upload(c);assert r.status_code==200,r.text
    batch=r.json();assert batch['category_path']=='2-产品库/重疾险'
    assert batch.get('ip_id') is None
    assert batch['items'][0]['category_id']=='product-critical-illness'
    doc=c.post('/api/knowledge/confirm',json={'batch_id':batch['id'],'category_id':'products'}).json()['items'][0]
    assert doc['path']=='条款.md' and doc['category_path']=='2-产品库/重疾险'
    assert doc['status']=='pending'
    assert doc['ip_id'] is None
    assert c.post('/api/knowledge/confirm',json={'batch_id':batch['id']}).json()['items'][0]['id']==doc['id']


def test_dedup_scoped_to_directory_and_zip_keeps_paths(env):
    app,c,ip,team,path=env
    z=io.BytesIO()
    with zipfile.ZipFile(z,'w') as archive:
        archive.writestr('原库/条款.md','真实条款资料')
    first=upload(c,ip['id'],name='资料.zip',content=z.getvalue()).json()
    c.post('/api/knowledge/confirm',json={'batch_id':first['id']})
    other_ip=c.post('/api/ips',json={'name':'其他IP','team_id':team['id']}).json()
    again=upload(c,other_ip['id'],name='资料.zip',content=z.getvalue()).json()
    assert again['items'][0]['duplicate'] is True
    reused=c.post('/api/knowledge/confirm',json={'batch_id':again['id']}).json()['items'][0]
    assert reused['id']==c.get('/api/knowledge').json()[0]['id']
    other=upload(c,ip['id'],'product-savings',name='资料.zip',content=z.getvalue()).json()
    assert not other['items'][0]['duplicate']
    d=c.post('/api/knowledge/confirm',json={'batch_id':other['id']}).json()['items'][0]
    assert d['path']=='原库/条款.md' and d['category_path']=='2-产品库/储蓄险'


def test_history_migration_and_metadata_move_preserve_verification(env):
    app,c,ip,team,path=env
    with app.state.Session() as db:
        owner=db.scalar(select(User)).id
        for key,source in [('legacy','原库/2-产品库/储蓄险.md'),('unmapped','原库/随便记录.md')]:
            db.add(Record(id=key,kind='knowledge',ip_id=ip['id'],owner_id=owner,payload={'path':source,'title':key,'text':'真实保障条款','hash':'keep-hash','status':'verified','history':[{'text':'old'}]}))
        db.add(Record(id='old-batch',kind='import_batch',ip_id=ip['id'],owner_id=owner,payload={'items':[],'confirmed_ids':None}))
        db.add(Record(id='confirmed-batch',kind='import_batch',ip_id=ip['id'],owner_id=owner,payload={'items':[],'confirmed_ids':['legacy']}))
        db.commit()
    app=create_app(path);c=TestClient(app);c.post('/api/auth/login',json={'username':'admin','password':'admin'})
    docs=c.get('/api/knowledge').json();a=next(d for d in docs if d['id']=='legacy');b=next(d for d in docs if d['id']=='unmapped')
    assert a['category_id']=='product-savings' and b['category_id'] is None
    assert a['ip_id'] is None and b['ip_id'] is None
    assert a['hash']=='keep-hash' and a['status']=='verified'
    assert c.post('/api/knowledge/confirm',json={'batch_id':'old-batch'}).status_code==422
    assert c.post('/api/knowledge/confirm',json={'batch_id':'confirmed-batch'}).json()['items'][0]['id']=='legacy'
    with app.state.Session() as db:
        assert db.get(Record,'old-batch').ip_id is None
        assert db.get(Record,'confirmed-batch').ip_id is None
    assert c.patch('/api/knowledge/legacy',json={'category_id':'products'}).status_code==422
    r=c.patch('/api/knowledge/legacy',json={'category_id':'product-critical-illness'});assert r.status_code==200,r.text
    assert r.json()['status']=='verified' and r.json()['hash']=='keep-hash' and len(r.json()['history'])==1
    ctx=c.post('/api/writing/context',json={'ip_id':ip['id'],'topic':'保障条款','skill':'original-video-writer'}).json()
    assert next(s for s in ctx['sources'] if s['id']=='legacy')['category_path']=='2-产品库/重疾险'


def test_category_move_and_upload_permissions(env):
    app,c,ip,team,path=env
    batch=upload(c,ip['id']).json();doc=c.post('/api/knowledge/confirm',json={'batch_id':batch['id']}).json()['items'][0]
    c.post('/api/users',json={'username':'member','name':'队员','password':'Member-123456!','role':'member','team_id':team['id'],'ip_ids':[]})
    m=TestClient(app);m.post('/api/auth/login',json={'username':'member','password':'Member-123456!'})
    assert m.get('/api/knowledge/categories').status_code==200
    assert m.patch('/api/knowledge/'+doc['id'],json={'category_id':'product-savings'}).status_code==403
    assert upload(m,category='product-savings').status_code==200
    assert upload(m,'missing','product-savings').status_code==200
    assert m.get('/api/knowledge/'+doc['id']).status_code==200
    private_batch=upload(c,category='product-savings').json()
    assert m.post('/api/knowledge/confirm',json={'batch_id':private_batch['id']}).status_code==403
    member_batch=upload(m).json()
    assert c.post('/api/knowledge/confirm',json={'batch_id':member_batch['id']}).status_code==200


def test_legacy_shared_duplicates_keep_referenced_ids_without_creating_another(env):
    import hashlib

    app, admin, ip, team, data_dir = env
    other_ip = admin.post('/api/ips', json={'name': '旧业务IP', 'team_id': team['id']}).json()
    content = '保留旧稿引用的重疾险资料'.encode()
    payload = {'title': '重疾险资料', 'path': '条款.md', 'text': content.decode(),
               'hash': hashlib.sha256(content).hexdigest(), 'status': 'verified',
               'category_id': 'product-critical-illness', 'category_path': '2-产品库/重疾险'}
    with app.state.Session.begin() as db:
        owner = db.scalar(select(User)).id
        db.add_all([
            Record(id='legacy-first', kind='knowledge', ip_id=ip['id'], owner_id=owner, payload=payload),
            Record(id='legacy-second', kind='knowledge', ip_id=other_ip['id'], owner_id=owner, payload=payload),
            Record(id='legacy-batch', kind='import_batch', ip_id=other_ip['id'], owner_id=owner,
                   payload={'confirmed_ids': ['legacy-second']}),
        ])
    restarted = TestClient(create_app(data_dir))
    assert restarted.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 200
    documents = restarted.get('/api/knowledge').json()
    assert {(doc['id'], doc['ip_id']) for doc in documents} == {('legacy-first', None), ('legacy-second', None)}
    old = restarted.post('/api/knowledge/confirm', json={'batch_id': 'legacy-batch'}).json()
    assert old['items'][0]['id'] == 'legacy-second'
    batch = upload(restarted, content=content).json()
    assert batch['items'][0]['duplicate']
    confirmed = restarted.post('/api/knowledge/confirm', json={'batch_id': batch['id']}).json()['items']
    assert confirmed[0]['id'] == 'legacy-first'
    assert len(restarted.get('/api/knowledge').json()) == 2


def test_preexisting_confirmed_batch_stays_idempotent_and_templates_stay_inert(env):
    app,c,ip,team,path=env
    batch=upload(c,ip['id'],name='README.md',content='仅作为目录使用说明，不是业务事实。'.encode()).json()
    doc=c.post('/api/knowledge/confirm',json={'batch_id':batch['id']}).json()['items'][0]
    assert doc['status']=='template'
    assert c.patch('/api/knowledge/'+doc['id'],json={'status':'verified'}).status_code==422
    with app.state.Session() as db:
        row=db.get(Record,batch['id']);row.payload={k:v for k,v in row.payload.items() if k!='category_id'};db.commit()
    assert c.post('/api/knowledge/confirm',json={'batch_id':batch['id']}).json()['items'][0]['id']==doc['id']


def test_ambiguous_legacy_paths_stay_unclassified_and_shared_team_leader_may_move(env):
    from backend.app.knowledge_categories import infer
    assert infer('原库/重疾险.md')['category_id'] is None
    assert infer('原库/2-产品库/重疾险/5-方法论库/专业判断/规则.md')['category_id'] is None
    assert infer('知识库架子/0-录音处理/待处理/放录音转写稿.md')['category_id']=='recordings-pending'
    assert infer('知识库架子/5-方法论库/专业判断/怎么给客户做规划.md')['category_id']=='methods-judgment'
    app,c,ip,team,path=env
    batch=upload(c,ip['id']).json();doc=c.post('/api/knowledge/confirm',json={'batch_id':batch['id']}).json()['items'][0]
    other=c.post('/api/teams',json={'name':'其他团队'}).json()
    c.post('/api/users',json={'username':'outsider','name':'外团队','password':'Leader-123456!','role':'leader','team_id':other['id'],'ip_ids':[]})
    outsider=TestClient(app);outsider.post('/api/auth/login',json={'username':'outsider','password':'Leader-123456!'})
    assert outsider.patch('/api/knowledge/'+doc['id'],json={'category_id':'product-savings'}).status_code==200
    assert [d['id'] for d in outsider.get('/api/knowledge').json()]==[doc['id']]
    pending=upload(c,category='product-savings').json()
    assert outsider.post('/api/knowledge/confirm',json={'batch_id':pending['id']}).status_code==403
