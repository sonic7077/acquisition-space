import {useEffect,useState} from 'react';
import {api,post,saveText,type Row} from '../api';
import {Field,Modal,Notice,Badge} from '../components/ui';
import {type Ctx,useRows} from '../context';
import LeadDemandSelect, {effectiveLeadsForIp} from './LeadDemandSelect';
import WorkCommentPicker from './WorkCommentPicker';

const TYPES:Row={'viral-rewrite-writer':'复刻口播','original-video-writer':'原创口播','gzh-writer':'公众号长文','tietu-writer':'图文贴','moments-writer':'朋友圈'};
export default function RewriteDialog({c,draft,onClose,onCandidate}:{c:Ctx;draft:Row;onClose:()=>void;onCandidate:(id:string)=>void}){
  const personal=useRows('/writers',c.version).filter(w=>w.ip_id===draft.ip_id&&w.status==='active');
  const defaults=useRows('/default-writers?ip_id='+encodeURIComponent(draft.ip_id),c.version).filter(w=>w.kind==='content');
  const writers=[...personal,...defaults];
  const leads=useRows('/records/leads',c.version);
  const demands=effectiveLeadsForIp(leads,draft.ip_id);
  const snapshot=draft.demand_snapshot;
  const previous=draft.need_id ? {
    id:draft.need_id,
    label:snapshot?.evidence_restricted ? '已关联需求 · 来源受限'
      : snapshot?.source_kind==='leads' ? '已关联线索 · 待核验'
      : ['历史关联需求',snapshot?.title].filter(Boolean).join(' · '),
  } : undefined;
  const [needId,setNeedId]=useState(draft.need_id||'');
  const selectedNeedId=needId===previous?.id||demands.some(lead=>lead.id===needId)?needId:'';
  const initialSkill = snapshot?.source_kind === 'leads' && !draft.reference?.trim()
    ? 'original-video-writer'
    : 'viral-rewrite-writer';
  const [writerId,setWriterId]=useState(''),[personalSkill,setPersonalSkill]=useState(initialSkill),[reference,setReference]=useState(draft.reference||''),[confirmed,setConfirmed]=useState(false),[instructions,setInstructions]=useState(''),[commentIds,setCommentIds]=useState<string[]>([]),[context,setContext]=useState<Row|null>(null),[busy,setBusy]=useState(false),[requestId,setRequestId]=useState(()=>crypto.randomUUID()),[job,setJob]=useState<Row|null>(null),[pollError,setPollError]=useState('');
  const chosen=writers.find(w=>w.id===writerId);
  const skill = chosen?.is_system ? chosen.skill : personalSkill;
  const fallbackId = (personal.find(w=>w.is_default) || personal[0] || defaults.find(w=>w.skill===initialSkill) || defaults[0])?.id;
  useEffect(() => {
    if (!writerId && fallbackId) setWriterId(fallbackId);
  }, [writerId, fallbackId]);
  function invalidate(){setContext(null);setRequestId(crypto.randomUUID());}
  useEffect(()=>{if(!job||['completed','failed'].includes(job.status))return;let active=true;const timer=setInterval(async()=>{try{const jobs=await api<Row[]>('/jobs');const latest=jobs.find(j=>j.id===job.id);if(active&&latest){setJob(latest);setPollError('')}}catch{if(active)setPollError('任务状态读取失败，请在工作台核对后刷新，勿重复提交。')}},2000);return()=>{active=false;clearInterval(timer)}},[job?.id,job?.status]);
  const valid=!!chosen&&(skill!=='viral-rewrite-writer'||!!reference.trim()&&confirmed);
  async function submit(generate:boolean){if(!chosen)return;setBusy(true);const r=await c.run(()=>post(`/drafts/${draft.id}/rewrite${generate?'':'/context'}`,{writer_id:chosen.id,writer_version:chosen.version,version:draft.version,skill,reference,transcript_confirmed:confirmed,comment_ids:commentIds,need_id:selectedNeedId||null,instructions,request_id:requestId}),generate?'改编任务已提交，原稿保留':undefined);if(r){if(generate)setJob(r);else setContext(r)}setBusy(false)}
  return <Modal title="用写手改编" onClose={onClose}><p className="muted">{draft.title} · 原稿 v{draft.version}。使用稿件所属IP的写手，结果另存为待审候选。</p>
    <div className="form-grid"><Field label="专属写手"><select aria-label="专属写手" disabled={!!job} value={writerId} onChange={e=>{setWriterId(e.target.value);invalidate()}}><option value="">请选择可用写手</option>{writers.map(w=><option key={w.id} value={w.id}>{w.name} · v{w.version}</option>)}</select></Field><Field label="改编文体"><select aria-label="改编文体" disabled={!!job||!chosen||chosen.is_system} value={skill} onChange={e=>{setPersonalSkill(e.target.value);invalidate()}}>{Object.entries(TYPES).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></Field></div>
    {!writers.length&&<Notice>默认写手正在加载；专属写手可在“IP与写手”中填写12问并审核启用。</Notice>}
    <Field label="对标逐字稿"><textarea aria-label="对标逐字稿" rows={5} maxLength={20000} disabled={!!job} value={reference} onChange={e=>{setReference(e.target.value);setConfirmed(false);invalidate()}} placeholder="粘贴已核对的口播全文；视频标题、描述不能代替逐字稿。"/></Field>
    <label className="check-row"><input aria-label="我确认这是已核对的逐字稿" type="checkbox" disabled={!!job} checked={confirmed} onChange={e=>{setConfirmed(e.target.checked);invalidate()}}/>我确认这是已核对的逐字稿（复刻口播必选）</label>
    <Field label="改编要求"><textarea aria-label="改编要求" rows={2} maxLength={2000} disabled={!!job} value={instructions} onChange={e=>{setInstructions(e.target.value);invalidate()}} placeholder="例如：开头用真实场景，重点回答预算问题。"/></Field>
    <LeadDemandSelect leads={demands} previous={previous} disabled={!!job} value={selectedNeedId} onChange={id=>{setNeedId(id);invalidate()}}/>
    <WorkCommentPicker materialId={draft.material_id} sourceRestricted={draft.reference_restricted} version={c.version}
      ipId={draft.ip_id} leads={leads} selected={commentIds} disabled={!!job} initiallyOpen
      onChange={ids=>{setCommentIds(ids);invalidate()}}/>
    <Notice>改编使用所选写手版本、评论和共享库里相关的已核验资料。审稿时请核查事实并批准候选稿。</Notice>
    {!job&&<div className="button-row"><button disabled={!valid||busy} onClick={()=>submit(false)}>预览改编上下文</button><button className="primary" disabled={!valid||busy} onClick={()=>submit(true)}>提交AI改编</button></div>}
    {context&&<div className="rewrite-preview"><p>写手 v{context.writer_snapshot.version} · 知识 {context.sources.length} 条 · 所选评论 {context.comments.length} 条 · 需求 {context.demand_snapshot?.title||"未关联"} · 正文上限 {context.word_limit} 字</p><textarea className="context-text" aria-label="改编提示包" readOnly value={context.prompt}/><button onClick={()=>saveText('改编提示包.txt',context.prompt)}>导出提示包</button></div>}
    {job&&<div className="rewrite-job"><Badge value={job.status}/><p>任务 {job.id.slice(0,8)} · 可关闭窗口，任务会继续执行；工作台可查看结果。</p>{pollError&&<p className="error-text">{pollError}</p>}{job.error&&<p className="error-text">{job.error}</p>}{job.raw_output&&<Field label="模型原始结果（需人工整理）"><textarea className="context-text" readOnly value={job.raw_output}/><button onClick={()=>saveText('改编原始结果.txt',job.raw_output)}>下载原始结果</button></Field>}{job.status==='completed'&&job.draft_id&&<button className="primary" onClick={async()=>{await c.run(async()=>true);onCandidate(job.draft_id)}}>查看改编候选稿</button>}</div>}
  </Modal>;
}
