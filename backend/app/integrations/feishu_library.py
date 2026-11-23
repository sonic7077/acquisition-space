"""Admin-only material export using existing tasks/IP tables and two reference tables.

Only passive content fields are mapped: publishing and collection trigger fields are
never written. Built-in skills remain inert text and retain durable sync receipts.
"""
import hashlib
import json
from sqlalchemy import select
from ..db import Record, now
from ..default_writers import builtin
from ..security import active_ip
from ..writing import SKILLS
from .common import IntegrationError, update

# local key, remote column, type; do not map approval/publishing trigger columns.
SPECS = {
    'ips': ('IP账号', False, [('title','IP',1),('body','获客空间·账号资料',1)]),
    'knowledge': ('知识库', True, [('title','标题',1),('body','正文',1),('category_path','末级目录',1),('status','状态',1),('path','来源路径',1),('hash','内容指纹',1)]),
    'writers': ('写手SKILL', True, [('title','名称',1),('skill','文体能力',1),('body','SKILL正文',1),('answers_text','身份问答',1),('rules_text','语感规则',1),('version','版本',2),('status','状态',1),('provenance','来源类型',1),('enhancements_text','知识增强',1)]),
    'drafts': ('改写任务', False, [('display_id','编号',1),('title','定稿标题',1),('body','口播正文',1),('material_id','作品',18),('created_at','创建时间',5),('status','获客空间·状态',1),('version','获客空间·版本',2)]),
}
COMMON = [('local_id','获客空间ID',1),('structure','内容结构',1),('ip_id','获客空间·IP',1)]


def schema(api, prepare=False):
    available=api.items(api.path())
    tables,warnings={},[]
    for kind,(name,create,columns) in SPECS.items():
        matches=[t for t in available if t['name']==name]
        if len(matches)>1:
            raise IntegrationError(f'飞书存在多张{name}表，请先核对表名；未写入数据')
        if not matches and prepare and create:
            fields=[{'field_name':label,'type':typ} for _,label,typ in columns+COMMON]
            result=api.request('POST',api.path(),{'table':{'name':name,'default_view_name':'全部材料','fields':fields}})
            tid=result.get('table_id')
            if not tid:
                raise IntegrationError(f'飞书新建{name}表结果不确定，请核对表列表后重试')
            matches=[{'table_id':tid,'name':name}]
        if not matches:
            warnings.append(f'缺少{name}表，请管理员准备材料同步表')
            continue
        table=dict(matches[0]);path=api.path(table['table_id'])+'/fields'
        fields=api.items(path)
        if prepare:
            names={f['field_name']:f for f in fields}
            for _,label,typ in columns+COMMON:
                if label not in names:
                    # Relationship targets and existing workflow fields must exist already.
                    if typ==18:
                        raise IntegrationError(f'{name}表缺少原有{label}关系字段，请核对模板结构')
                    api.request('POST',path,{'field_name':label,'type':typ})
                elif names[label]['type']!=typ:
                    raise IntegrationError(f'{name}表的{label}字段类型不兼容，未改动该字段')
            fields=api.items(path)
        mapping={}
        for local,label,typ in columns+COMMON:
            field=next((f for f in fields if f['field_name']==label and f['type']==typ),None)
            if field:
                mapping[local]=field
            else:
                warnings.append(f'{name}缺少兼容字段{label}')
        table.update(fields=fields,mapping=mapping)
        tables[kind]=table
    return {'tables':tables,'warnings':warnings,'ready':not warnings,'target':api.target()}


def rows(db,kind,run):
    from .feishu import sync_rows
    if kind=='ips':
        query=select(Record).where(Record.kind=='ips')
        if not run.get('options',{}).get('all_scopes'):
            query=query.where(Record.id==run['ip_id'])
        return [r for r in db.scalars(query.order_by(Record.created_at)) if active_ip(r)]
    return sync_rows(db,kind,run)


def counts(db,run):
    return {**{kind:len(rows(db,kind,run)) for kind in SPECS},'builtin_writers':len(SKILLS)}


def checkpoint_id(api,ident):
    return 'feishu-builtin-'+hashlib.sha256((api.target()['token']+':'+ident).encode()).hexdigest()[:32]


def project(row,db):
    p=dict(row.payload)
    p['title']=p.get('title') or p.get('name') or row.id
    if row.kind=='knowledge':
        p['body']=p.get('text','')
    elif row.kind=='writers':
        p['body']=p.get('skill_text','')
        p['answers_text']=json.dumps(p.get('answers',[]),ensure_ascii=False)
        p['rules_text']=json.dumps(p.get('rules',[]),ensure_ascii=False)
        p['provenance']='系统内置' if p.get('is_system') else '用户创建'
        enhancements=[
            r for r in db.scalars(select(Record).where(Record.kind=='default_writer_enhancements'))
            if r.payload.get('writer_id')==row.id and r.ip_id and active_ip(db.get(Record,r.ip_id))
        ]
        p['enhancements_text']=json.dumps([{'ip_id':r.ip_id,**r.payload} for r in enhancements],ensure_ascii=False)
    elif row.kind=='ips':
        p['body']=json.dumps(p.get('profile',{}),ensure_ascii=False)
    elif row.kind=='drafts':
        p['display_id']='HK-'+row.id[:12]
    return Record(id=row.id,kind=row.kind,ip_id=row.id if row.kind=='ips' else row.ip_id,
                  owner_id=row.owner_id,payload=p,created_at=row.created_at,updated_at=row.updated_at)


def builtins(api,db):
    result=[]
    for key in SKILLS:
        data=builtin('default:'+key)
        saved=db.get(Record,checkpoint_id(api,data['id']))
        if saved:
            data.update(saved.payload)
        result.append(Record(id=data['id'],kind='writers',payload=data,ip_id=None,created_at=None,updated_at=None))
    return result


def sync(api,app,key,run,refs,processed):
    spec=schema(api)
    if not spec['ready']:
        raise IntegrationError('；'.join(spec['warnings']))
    completed={}
    for kind,table in spec['tables'].items():
        with app.state.Session() as db:
            selected=rows(db,kind,run)
            if kind=='writers':
                selected+=builtins(api,db)
            selected=[project(r,db) for r in selected]
        completed[kind]=0
        for row in selected:
            ref=api.upsert(table,row,refs)
            refs[row.id]=ref
            receipt={'feishu_record_id':ref['record_id'],'feishu_table_id':ref['table_id'],
                     'feishu_synced_at':now(),'feishu_snapshot':api.last_fields}
            with app.state.Session() as db:
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
                if row.payload.get('is_system'):
                    ident=checkpoint_id(api,row.id)
                    local=db.get(Record,ident)
                    if not local:
                        local=Record(id=ident,kind='feishu_export_receipts',payload={})
                        db.add(local)
                else:
                    local=db.get(Record,row.id)
                    if not local:
                        raise IntegrationError('本地材料已删除，请刷新后重新同步')
                local.payload={**local.payload,**receipt}
                db.commit()
            processed+=1;completed[kind]+=1
            update(app,key,processed=processed,library_counts=completed,progress=f'{table["name"]}：已同步 {completed[kind]} 条；累计 {processed} 条')
    return processed
