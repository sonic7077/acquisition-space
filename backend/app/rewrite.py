"""Versioned rewrite inputs and structured candidate output, without overwriting sources."""
import copy
import hashlib
import json
import re
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from .db import Record,as_record
from .security import require_record
from .knowledge import shared_documents
from .writer_profiles import CONTENT_SKILLS, FORMAT_REFERENCE_HEADING, get_writer, revision
from .writing import attach_knowledge, build_context, reference_context
from .demand_analysis import attach_demand, labels

class RewriteInput(BaseModel):
    version:int
    writer_id:str
    writer_version:int
    skill:str='viral-rewrite-writer'
    reference:str=Field(default='',max_length=20000)
    transcript_confirmed:bool=False
    comment_ids:list[str]=Field(default_factory=list,max_length=50)
    need_id:str|None=None
    instructions:str=Field(default='',max_length=2000)
    request_id:str=Field(default='',max_length=100)


def writer_context(db,user,ctx,writer_id,writer_version,*,knowledge_query=''):
    demand = ctx.get('demand_snapshot', {})
    demand_terms = '\n'.join(demand.get(field) or '' for field in ('title', 'body', 'sel_topic', 'doc_name', 'product')).strip()
    if demand_terms:
        knowledge_query = demand_terms[:1500] + '\n' + knowledge_query
    if writer_id.startswith('default:'):
        from .default_writers import apply_context
        attach_knowledge(ctx, shared_documents(db), knowledge_query=knowledge_query)
        return apply_context(db,user,ctx,writer_id,writer_version)
    row=get_writer(db,user,writer_id);revision(row,writer_version)
    if row.ip_id!=ctx['ip_id']:raise HTTPException(422,'写手必须属于稿件目标IP')
    if row.payload['status']!='active':raise HTTPException(409,'请先生成并启用可用写手')
    if ctx['skill'] not in CONTENT_SKILLS:raise HTTPException(422,'请选择内容写作文体')
    p=row.payload
    attach_knowledge(ctx, shared_documents(db), knowledge_query=knowledge_query,
                     pinned_ids=p.get('knowledge_ids', []),
                     preferred_ids=[source['id'] for source in p.get('knowledge_sources', [])])
    sources = [{k: source[k] for k in ('id', 'title', 'hash', 'category_id', 'category_path')}
               for source in ctx['sources']]
    ctx['knowledge_snapshot'] = {'documents': sources, 'count': len(sources),
                                 'fingerprint': hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    snapshot={k:copy.deepcopy(p[k]) for k in ('name','version','answers','skill_text','word_limit','skill')}
    snapshot.update(id=row.id,ip_id=row.ip_id)
    limit=p['word_limit'] if ctx['skill'] in ('viral-rewrite-writer','original-video-writer') else {'gzh-writer':2500,'tietu-writer':500,'moments-writer':150}[ctx['skill']]
    ctx.update(writer_snapshot=snapshot,word_limit=limit,structured_output=True)
    # Keep the full export in the snapshot and apply the archived personal rules with the live task template.
    config,_,_=p['skill_text'].rpartition('\n'+FORMAT_REFERENCE_HEADING)
    ctx['prompt']+='\n专属写手配置（应用本号的身份、称呼、禁区及已采纳语感；文体按本次所选模板）：\n'+(config or p['skill_text'])
    ctx['prompt']+='\n知识规则以本次任务为准。上文的历史写手配置若提及“本IP知识”，请使用系统为此次任务提供的共享知识引用，并核对其中的产品、主体和适用条件。'
    ctx['prompt']+=f'\n本次正文字数硬上限：{limit}字，含标点。此处覆盖模板默认字数。\n'
    ctx['prompt']+='最终返回一个JSON对象，不加代码围栏。字段：title（标题）、cover（封面文字）、body（纯正文）、publish_copy（发布文案）、tags（字符串数组）、comment_insights（评论洞察）、fact_check（事实核查与待核清单）、extras（其他文体输出）。全部使用字符串，只有tags为数组。统计行和[知识ID]引用编号不放进body，编号放在fact_check。没有联网工具，禁止声称已联网核查；未有证据的硬事实列为待核。此JSON格式覆盖模板及前文的输出格式。'
    return ctx


def parse_output(raw,skill):
    text=raw.strip()
    if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I)
    try:out=json.loads(text)
    except (ValueError,TypeError):raise ValueError('模型未按JSON格式返回，原始结果已保留，请人工整理后导入')
    if not isinstance(out,dict):raise ValueError('模型输出不是对象，原始结果已保留')
    for name in ('title','body'):
        if not isinstance(out.get(name),str) or not out[name].strip():raise ValueError('模型结果缺少标题或正文，原始结果已保留')
    for name in ('cover','publish_copy','comment_insights','fact_check','extras'):
        if name in out and not isinstance(out[name],str):raise ValueError('模型输出字段格式不正确，原始结果已保留')
    if len(out['body'])>20000 or len(out['title'])>200:raise ValueError('模型标题或正文过长，原始结果已保留')
    if 'tags' in out and (not isinstance(out['tags'],list) or len(out['tags'])>30 or any(not isinstance(t,str) or len(t)>100 for t in out['tags'])):raise ValueError('模型标签格式不正确，原始结果已保留')
    return {k:v for k,v in out.items() if k in ('title','body','cover','publish_copy','tags','comment_insights','fact_check','extras')}


def rewrite_inputs_current(db,ip_id,ctx):
    if not ctx.get('writer_snapshot'):return True
    if not ctx['writer_snapshot'].get('is_system'):
        w=db.get(Record,ctx['writer_snapshot']['id'])
        if not w or w.kind!='writers' or w.ip_id!=ip_id or w.payload.get('status')=='archived':return False
    parent_id=ctx.get('parent_draft_id')
    parent=db.get(Record,parent_id) if parent_id else None
    return not parent_id or bool(parent and parent.kind=='drafts' and parent.ip_id==ip_id)


def register(app,get_db,current,audit):
    def parent(db,user,key):
        row=db.get(Record,key)
        if not row or row.kind!='drafts':raise HTTPException(404,'稿件不存在')
        require_record(db,user,row);return row

    def assemble(db,user,row,data):
        if row.payload['version']!=data.version:raise HTTPException(409,'原稿已变更，请刷新后重新选择')
        if data.skill not in CONTENT_SKILLS:raise HTTPException(422,'辅助分析能力不能作为改编文体')
        if data.skill=='viral-rewrite-writer' and (not data.reference.strip() or not data.transcript_confirmed):raise HTTPException(422,'复刻改编请补充并确认对标逐字稿；标题或作品描述不能代替口播正文')
        comments=[]
        for key in dict.fromkeys(data.comment_ids):
            lead=db.get(Record,key)
            if not lead or lead.kind!='leads':raise HTTPException(422,'评论不存在')
            require_record(db,user,lead)
            if not row.payload.get('material_id') or lead.payload.get('material_id')!=row.payload['material_id'] or (lead.ip_id and lead.ip_id!=row.ip_id):raise HTTPException(422,'只能选择关联作品且有访问权限的评论')
            comments.append({'id':lead.id,'body':lead.payload.get('body','')[:2000],**labels(lead.payload),'likes':lead.payload.get('likes')})
        ip=db.get(Record,row.ip_id)
        rules=[r.payload for r in db.scalars(select(Record).where(Record.kind=='rules',Record.ip_id==row.ip_id)) if r.payload.get('status')=='adopted']
        topic=row.payload.get('title','改编文案')+' '+data.instructions
        knowledge_query = '\n'.join([
            (row.payload.get('body') or '')[:3000],
            data.reference[:2000],
            *(comment['body'][:500] for comment in comments[:5]),
        ])
        ctx=build_context(ip,None,topic,data.skill,rules)
        ctx.update(parent_draft_id=row.id,parent_version=row.payload['version'],root_draft_id=row.payload.get('root_draft_id') or row.id,material_id=row.payload.get('material_id'),reference=data.reference,
                   transcript_confirmed=bool(data.reference.strip() and data.transcript_confirmed),comments=comments,original_body=row.payload.get('body',''))
        ctx['prompt']+='\n原稿（仅作表达参考，不自动视为事实）：\n'+row.payload.get('body','')
        ctx['prompt']+=reference_context(data.reference,data.transcript_confirmed)
        ctx['prompt']+='\n已选择的评论（只作洞察，不作为事实证据）：\n'+json.dumps(comments,ensure_ascii=False)+'\n改编要求：'+data.instructions
        attach_demand(db,user,ctx,data.need_id if 'need_id' in data.model_fields_set else row.payload.get('need_id'))
        return writer_context(db,user,ctx,data.writer_id,data.writer_version,knowledge_query=knowledge_query)

    @app.post('/api/drafts/{key}/rewrite/context')
    def preview(key:str,data:RewriteInput,user=Depends(current),db=Depends(get_db)):
        return assemble(db,user,parent(db,user,key),data)

    @app.post('/api/drafts/{key}/rewrite')
    def submit(key:str,data:RewriteInput,user=Depends(current),db=Depends(get_db)):
        row=parent(db,user,key)
        if not data.request_id.strip():raise HTTPException(422,'缺少提交标识，请重新打开改编窗口')
        digest=hashlib.sha256(data.model_dump_json().encode()).hexdigest()
        for job in db.scalars(select(Record).where(Record.kind=='jobs',Record.owner_id==user.id,Record.ip_id==row.ip_id)):
            if job.payload.get('request_id')==data.request_id:
                if job.payload.get('input_hash')!=digest:raise HTTPException(409,'同一提交标识内容已变化，请重新提交')
                return {'id':job.id,'status':job.payload['status']}
        ctx=assemble(db,user,row,data)
        config=db.get(Record,'system-settings');model=config.payload.get('model',{}) if config else {}
        if not model.get('enabled') or not all(model.get(k) for k in ('base_url','model','api_key_encrypted')):raise HTTPException(409,'管理员尚未启用模型配置，可先预览或导出改编提示包')
        job=Record(kind='jobs',ip_id=row.ip_id,owner_id=user.id,payload={'status':'queued','context':ctx,'attempts':0,'request_id':data.request_id,'input_hash':digest})
        db.add(job);db.flush();audit(db,user,'提交写手改编',job.id)
        return {'id':job.id,'status':'queued'}
