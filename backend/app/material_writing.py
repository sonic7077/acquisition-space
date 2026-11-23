"""Preview and queue a selected default or personal writer against a material."""
import hashlib
import json
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from .db import Record
from .knowledge import shared_documents
from .security import require_ip, require_record, can_record
from .writing import build_context, reference_context
from .writer_profiles import get_writer, CONTENT_SKILLS
from .default_writers import PREFIX,builtin
from .rewrite import writer_context
from .demand_analysis import labels,attach_demand


class MaterialInput(BaseModel):
    ip_id:str
    writer_id:str
    writer_version:int
    enhancement_version:int|None=None
    source_version:int|None=None
    reference:str=Field(default='',max_length=20000)
    transcript_confirmed:bool=False
    comment_ids:list[str]=Field(default_factory=list,max_length=50)
    evidence_ids:list[str]=Field(default_factory=list,max_length=30)
    need_id:str|None=None
    instructions:str=Field(default='',max_length=2000)
    request_id:str=Field(default='',max_length=100)


def material_inputs_current(db,user,ctx):
    for key,kind in [(ctx.get('material_id'),'materials'),*[(c['id'],'leads') for c in ctx.get('comments',[])],*[(d['id'],'drafts') for d in ctx.get('evolution_evidence',[])]]:
        if not key:continue
        row=db.get(Record,key)
        if not row or row.kind!=kind or not can_record(db,user,row):return False
        if row.ip_id and row.ip_id!=ctx['ip_id']:return False
        if kind=='leads' and row.payload.get('material_id')!=ctx.get('material_id'):return False
    return True


def register(app,get_db,current,audit):
    def assemble(db,user,key,data,source_kind='materials'):
        require_ip(db,user,data.ip_id)
        mat=db.get(Record,key)
        if not mat or mat.kind!=source_kind:raise HTTPException(404,'源记录不存在')
        require_record(db,user,mat)
        if mat.ip_id and mat.ip_id!=data.ip_id:raise HTTPException(422,'素材须属于目标IP，未归属素材可显式选IP')
        w=builtin(data.writer_id) if data.writer_id.startswith(PREFIX) else get_writer(db,user,data.writer_id).payload
        skill=w['skill']
        if source_kind=='drafts':
            if data.source_version!=mat.payload.get('version'):raise HTTPException(409,'稿件已变更，请刷新')
            if skill in CONTENT_SKILLS:raise HTTPException(422,'此入口只作辅助分析，内容改编请使用用写手改编')
        material_id=key if source_kind=='materials' else mat.payload.get('material_id')
        if skill=='viral-rewrite-writer' and (not data.reference.strip() or not data.transcript_confirmed):raise HTTPException(422,'复刻口播须补充并确认逐字稿，作品描述不能代替全文')
        comments=[]
        for ident in dict.fromkeys(data.comment_ids):
            r=db.get(Record,ident)
            if not r or r.kind!='leads':raise HTTPException(422,'评论不存在')
            require_record(db,user,r)
            if not material_id or r.payload.get('material_id')!=material_id or (r.ip_id and r.ip_id!=data.ip_id):raise HTTPException(422,'只能选择同作品且有权限的评论')
            comments.append({'id':r.id,'body':r.payload.get('body','')[:2000],**labels(r.payload),'likes':r.payload.get('likes')})
        rules=[r.payload for r in db.scalars(select(Record).where(Record.kind=='rules',Record.ip_id==data.ip_id)) if r.payload.get('status')=='adopted']
        knowledge_query = '\n'.join([
            (mat.payload.get('body') or '')[:3000],
            data.reference[:2000],
            *(comment['body'][:500] for comment in comments[:5]),
        ])
        ctx=build_context(db.get(Record,data.ip_id),shared_documents(db),(mat.payload.get('title','素材任务')+' '+data.instructions)[:2000],skill,rules,knowledge_query=knowledge_query)
        source={k:mat.payload.get(k) for k in ('title','body','source','author_id','author','platform')}
        ctx.update(material_id=material_id,author_id=mat.payload.get('author_id'),comments=comments,reference=data.reference)
        if source_kind=='materials':
            ctx['material_snapshot']=source
        else:
            ctx.update(parent_draft_id=key,parent_version=mat.payload['version'],analysis_draft_snapshot=source)
        evidence=[]
        for ident in dict.fromkeys(data.evidence_ids):
            d=db.get(Record,ident)
            if not d or d.kind!='drafts':raise HTTPException(422,'改稿证据不存在')
            require_record(db,user,d)
            if d.ip_id!=data.ip_id:raise HTTPException(422,'改稿证据须属于目标IP')
            before=next((h.get('body') for h in reversed(d.payload.get('history',[])) if h.get('body') and h['body']!=d.payload.get('body')),None)
            if not before:raise HTTPException(422,'须有真实改前改后正文')
            evidence.append({'id':d.id,'before':before[:5000],'after':d.payload.get('body','')[:5000],'root_id':d.payload.get('material_id') or d.payload.get('root_draft_id') or d.id})
        ctx['evolution_evidence']=evidence
        ctx['prompt']+='\n素材参考数据（作品描述不是已确认逐字稿；经历不自动当成本IP事实）：\n'+json.dumps(source,ensure_ascii=False)
        ctx['prompt']+=reference_context(data.reference,data.transcript_confirmed)
        ctx['prompt']+='\n选择的原作品评论：\n'+json.dumps(comments,ensure_ascii=False)+'\n真实改稿对照证据：\n'+json.dumps(evidence,ensure_ascii=False)
        if skill=='writer-self-evolve' and len({d['root_id'] for d in evidence})<3:
            ctx['prompt']+='\n缺少3篇独立真实改稿证据，只能提候选建议，不得声称形成长期语感规律。'
        attach_demand(db,user,ctx,data.need_id)
        ctx=writer_context(db,user,ctx,data.writer_id,data.writer_version)
        if data.enhancement_version is not None and ctx['writer_snapshot'].get('is_system') and ctx['writer_snapshot']['enhancement_version']!=data.enhancement_version:
            raise HTTPException(409,'IP增强规则已变更，请刷新后重新预览')
        return ctx

    @app.post('/api/drafts/{key}/analysis/context')
    def draft_preview(key:str,data:MaterialInput,user=Depends(current),db=Depends(get_db)):
        return assemble(db,user,key,data,'drafts')

    @app.post('/api/drafts/{key}/analysis')
    def draft_submit(key:str,data:MaterialInput,user=Depends(current),db=Depends(get_db)):
        return queue_job(db,user,key,data,'drafts')

    @app.post('/api/materials/{key}/writing/context')
    def preview(key:str,data:MaterialInput,user=Depends(current),db=Depends(get_db)):
        return assemble(db,user,key,data)

    @app.post('/api/materials/{key}/writing')
    def submit(key:str,data:MaterialInput,user=Depends(current),db=Depends(get_db)):
        return queue_job(db,user,key,data,'materials')

    def queue_job(db,user,key,data,source_kind):
        if not data.request_id.strip():raise HTTPException(422,'缺少提交标识')
        digest=hashlib.sha256((source_kind+key+data.model_dump_json()).encode()).hexdigest()
        for j in db.scalars(select(Record).where(Record.kind=='jobs',Record.owner_id==user.id)):
            if j.payload.get('request_id')==data.request_id:
                if j.payload.get('input_hash')!=digest:raise HTTPException(409,'同一提交标识内容已变化，请重新提交')
                require_record(db,user,j)
                return {'id':j.id,'status':j.payload['status']}
        ctx=assemble(db,user,key,data,source_kind)
        settings=db.get(Record,'system-settings');model=settings.payload.get('model',{}) if settings else {}
        if not model.get('enabled') or not all(model.get(k) for k in ('base_url','model','api_key_encrypted')):raise HTTPException(409,'管理员尚未启用模型，可先预览或导出提示包')
        j=Record(kind='jobs',ip_id=data.ip_id,owner_id=user.id,payload={'status':'queued','context':ctx,'attempts':0,'request_id':data.request_id,'input_hash':digest})
        db.add(j);db.flush();audit(db,user,'素材调用写手',j.id)
        return {'id':j.id,'status':'queued'}
