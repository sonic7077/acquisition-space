import json
import httpx
from backend.tests.test_collection import setup, FeishuMock, add_sync_data
from backend.app.db import Record


class LibraryMock(FeishuMock):
    def __init__(self):
        super().__init__()
        self.created_tables=[]
        for tid,name in [('tbl-ips','IP账号'),('tbl-drafts','改写任务')]:
            self.tables.append({'table_id':tid,'name':name})
            self.fields[tid]=[{'field_id':'primary','field_name':'IP' if name=='IP账号' else '编号','type':1}]
            self.rows[tid]={}
        self.fields['tbl-drafts'] += [{'field_id':'work','field_name':'作品','type':18,'property':{'table_id':'tbl-works'}}, {'field_id':'trigger','field_name':'让AI改','type':7}]

    def __call__(self,req):
        if req.method=='POST' and req.url.path.endswith('/tables'):
            data=json.loads(req.content)['table']
            tid='tbl-extra-'+str(len(self.created_tables)+1)
            self.created_tables.append(data['name'])
            self.tables.append({'table_id':tid,'name':data['name']})
            self.fields[tid]=[{'field_id':f'f{i}','field_name':f['field_name'],'type':f['type']} for i,f in enumerate(data['fields'])]
            self.rows[tid]={}
            return httpx.Response(200,json={'code':0,'data':{'table_id':tid}})
        response=super().__call__(req)
        if req.url.path.endswith('/records/search'):
            payload=response.json()
            tid=next(tid for tid in self.fields if tid in req.url.path)
            for row in payload['data']['items']:
                fields=dict(row['fields'])
                for field in self.fields[tid]:
                    name=field['field_name']
                    if field['type'] in (18,21) and name in fields:
                        fields[name]={'link_record_ids':fields[name]}
                row['fields']=fields
            return httpx.Response(200,json=payload)
        return response


def configure(c):
    c.put('/api/settings',json={'feishu':{'app_id':'fake','app_secret':'fake-secret','enabled':True}})


def test_library_prepare_sync_and_repeat_preserve_template_and_no_triggers(setup):
    from backend.app.integrations.feishu import run_sync_job
    app,c,ip,team=setup;configure(c)
    mock=LibraryMock();app.state.integration_transport=httpx.MockTransport(mock)
    add_sync_data(app,ip['id'])
    with app.state.Session() as db:
        db.add(Record(id='kb-1',kind='knowledge',payload={'title':'真实知识','text':'完整知识正文','category_path':'方法论/流程','status':'verified','hash':'hash1'}))
        db.add(Record(id='writer-1',kind='writers',ip_id=ip['id'],payload={'name':'专属写手','answers':['真实身份'],'skill_text':'专属规则','status':'draft','version':1}))
        db.add(Record(id='draft-1',kind='drafts',ip_id=ip['id'],payload={'title':'稿件标题','body':'稿件正文','material_id':'local-work','status':'draft','version':1}))
        db.commit()
    p=c.post('/api/integrations/feishu/preview',json={'all_scopes':True,'include_library':True})
    assert p.status_code==200,p.text
    assert p.json()['library_counts']=={'knowledge':1,'writers':1,'builtin_writers':8,'drafts':1,'ips':1}
    prepared=c.post('/api/integrations/feishu/library/prepare',json={})
    assert prepared.status_code==200,prepared.text
    assert prepared.json()['ready']
    assert set(mock.created_tables)=={'知识库','写手SKILL'}
    for _ in range(2):
        job=c.post('/api/integrations/feishu/sync',json={'all_scopes':True,'include_library':True}).json()
        run_sync_job(app,job['id'])
        assert c.get('/api/collection/runs').json()[0]['status']=='completed',c.get('/api/collection/runs').json()[0]
    assert len(mock.rows['tbl-extra-1'])==1
    assert len(mock.rows['tbl-extra-2'])==9
    assert len(mock.rows['tbl-drafts'])==1 and len(mock.rows['tbl-ips'])==1
    draft=next(iter(mock.rows['tbl-drafts'].values()))
    assert draft['口播正文']=='稿件正文' and draft['作品']==['rec-1']
    assert '让AI改' not in draft and '通过' not in draft
    knowledge=next(iter(mock.rows['tbl-extra-1'].values()))
    assert knowledge['正文']=='完整知识正文' and knowledge['末级目录']=='方法论/流程'
    assert 'fake-secret' not in json.dumps(mock.writes)
    # Remote edits stop a retry instead of overwriting collaboration.
    knowledge['正文']='飞书手工修改'
    job=c.post('/api/integrations/feishu/sync',json={'all_scopes':True,'include_library':True}).json()
    run_sync_job(app,job['id'])
    assert c.get('/api/collection/runs').json()[0]['status']=='partial'
    assert knowledge['正文']=='飞书手工修改'


def test_library_requires_admin_even_for_single_ip(setup):
    from fastapi.testclient import TestClient
    _,c,ip,team=setup;configure(c)
    c.post('/api/users',json={'username':'lib-member','name':'成员','password':'Member-123456!','role':'member','team_id':team['id'],'ip_ids':[ip['id']]})
    member=TestClient(c.app);member.post('/api/auth/login',json={'username':'lib-member','password':'Member-123456!'})
    for path in ('sync','preview'):
        assert member.post('/api/integrations/feishu/'+path,json={'ip_id':ip['id'],'include_library':True}).status_code==403
    assert member.post('/api/integrations/feishu/library/prepare',json={}).status_code==403


def test_scoped_feishu_library_preview_and_sync_include_shared_knowledge(setup):
    from backend.app.integrations.feishu import run_sync_job

    app, client, ip, _ = setup
    configure(client)
    mock = LibraryMock()
    app.state.integration_transport = httpx.MockTransport(mock)
    with app.state.Session.begin() as db:
        db.add(Record(id='shared-knowledge', kind='knowledge', owner_id='different-uploader', payload={
            'title': '共享资料', 'text': '完整知识正文', 'status': 'verified', 'hash': 'shared-hash',
        }))
    selection = {'ip_id': ip['id'], 'include_library': True}
    preview = client.post('/api/integrations/feishu/preview', json=selection)
    assert preview.status_code == 200, preview.text
    assert preview.json()['counts'] == {'authors': 0, 'works': 0, 'comments': 0}
    assert preview.json()['library_counts']['knowledge'] == 1
    assert client.post('/api/integrations/feishu/library/prepare', json={}).json()['ready']
    job = client.post('/api/integrations/feishu/sync', json=selection)
    assert job.status_code == 200, job.text
    run_sync_job(app, job.json()['id'])
    assert client.get('/api/collection/runs').json()[0]['status'] == 'completed'
    fields = next(fields for _, fields in mock.writes if fields['获客空间ID'] == 'shared-knowledge')
    assert fields['正文'] == '完整知识正文'
    assert '获客空间·IP' not in fields


def test_archived_ip_is_excluded_from_feishu_preview_and_export(setup):
    from backend.app.integrations.feishu import run_sync_job

    app, client, ip, team = setup
    configure(client)
    mock = LibraryMock()
    app.state.integration_transport = httpx.MockTransport(mock)
    add_sync_data(app, ip['id'])
    archived = client.post('/api/ips', json={'name': '已归档的IP', 'team_id': team['id']}).json()
    with app.state.Session() as db:
        row = db.get(Record, archived['id'])
        row.payload = {**row.payload, 'archived_at': '2026-11-21T06:00:00+00:00'}
        for kind in ('authors', 'materials', 'leads', 'writers', 'drafts'):
            db.add(Record(kind=kind, ip_id=row.id, payload={'title': '已归档内容', 'source_type': 'platform_comment'}))
        for ip_id in (row.id, ip['id']):
            db.add(Record(kind='default_writer_enhancements', ip_id=ip_id,
                          payload={'writer_id': 'default:original-video-writer', 'rules': []}))
        db.commit()

    options = {'all_scopes': True, 'include_library': True}
    preview = client.post('/api/integrations/feishu/preview', json=options)
    assert preview.status_code == 200
    assert preview.json()['counts'] == {'authors': 1, 'works': 1, 'comments': 1}
    assert preview.json()['library_counts'] == {'ips': 1, 'knowledge': 0, 'drafts': 0, 'writers': 0, 'builtin_writers': 8}
    scoped = {'ip_id': archived['id'], 'include_library': True}
    assert client.post('/api/integrations/feishu/preview', json=scoped).status_code == 403
    assert client.post('/api/integrations/feishu/sync', json=scoped).status_code == 403

    assert client.post('/api/integrations/feishu/library/prepare', json={}).json()['ready']
    job = client.post('/api/integrations/feishu/sync', json=options)
    assert job.status_code == 200, job.text
    run_sync_job(app, job.json()['id'])
    result = client.get('/api/collection/runs').json()[0]
    assert result['status'] == 'completed', result
    assert result['processed'] == 12
    assert archived['id'] not in json.dumps(mock.writes)
    writer = next(fields for _, fields in mock.writes if fields['获客空间ID'] == 'default:original-video-writer')
    assert [item['ip_id'] for item in json.loads(writer['知识增强'])] == [ip['id']]


def test_actual_template_platform_and_comment_mapping(setup):
    from backend.app.integrations.feishu import Feishu
    app,c,ip,_=setup;configure(c)
    mock=FeishuMock();app.state.integration_transport=httpx.MockTransport(mock)
    mock.fields['tbl-comments'] += [{'field_id':'body','field_name':'评论内容','type':1},{'field_id':'user','field_name':'评论者','type':1},{'field_id':'work','field_name':'作品','type':18,'property':{'table_id':'tbl-works'}}]
    mock.fields['tbl-works'] += [{'field_id':'platform','field_name':'平台','type':3,'property':{'options':[{'name':'抖音'},{'name':'小红书'}]}},{'field_id':'author','field_name':'博主','type':18,'property':{'table_id':'tbl-authors'}}]
    api=Feishu(app)
    try:
        schema=api.schema()
        row=Record(id='m1',kind='materials',ip_id=ip['id'],payload={'title':'作品','platform':'douyin','author_id':'a1'})
        fields=api.fields(schema['tables']['works'],row,{'a1':{'table_id':'tbl-authors','record_id':'rec1'}})
        assert fields['平台']=='抖音' and fields['博主']==['rec1']
        assert schema['tables']['comments']['mapping']['title']['field_name']=='标题'
        assert schema['tables']['comments']['mapping']['material_id']['field_name']=='作品'
    finally:api.http.close()


def test_real_feishu_link_response_variants_compare_by_record_ids():
    from backend.app.integrations.feishu import comparable
    field={'type':18}
    assert comparable({'link_record_ids':['rec2','rec1']},field)==['rec1','rec2']
    assert comparable([{'record_ids':['rec2','rec1'],'table_id':'tbl1','text_arr':['名字'],'type':'text'}],field)==['rec1','rec2']
    assert comparable([{'record_id':'rec1'}],field)==['rec1']
    assert comparable([],field)==[]
    assert comparable({'link_record_ids':[]},field)==[]
    assert comparable({'link_record_ids':['rec3']},field)!=['rec1','rec2']


def test_real_url_response_metadata_does_not_trigger_false_conflict():
    from backend.app.integrations.feishu import comparable
    field={'type':15}
    sent={'link':'https://example.org/profile','text':'原始链接'}
    received={**sent,'type':'url'}
    assert comparable(received,field)==comparable(sent,field)
    assert comparable({**received,'link':'https://example.org/changed'},field)!=comparable(sent,field)
    assert comparable({**received,'text':'人工修改'},field)!=comparable(sent,field)
