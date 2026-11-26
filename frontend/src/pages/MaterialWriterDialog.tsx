import {useEffect,useRef,useState} from 'react';
import {api,post,saveText,type Row} from '../api';
import {type Ctx,useRows} from '../context';
import {Modal,Field,Notice,Badge} from '../components/ui';
import LeadDemandSelect, {effectiveLeadsForIp} from './LeadDemandSelect';
import WorkCommentPicker from './WorkCommentPicker';
import './MaterialWriterDialog.css';

export type WriterPreference = {ipId: string; writerId: string};
type FeishuTranscriptResult = {status: string; message?: string};
type TranscriptResult = {
  status?: 'idle' | 'queued' | 'running' | 'completed' | 'failed';
  progress?: number;
  stage?: string;
  text?: string;
  cached?: boolean;
  error?: string;
  feishu?: FeishuTranscriptResult;
};
const ORAL_SKILLS = ['viral-rewrite-writer', 'original-video-writer'];

export default function MaterialWriterDialog({c,material,onClose,preferredWriter,sourceKind='materials'}:{c:Ctx;material:Row;onClose:()=>void;preferredWriter?:WriterPreference|null;sourceKind?:'materials'|'drafts'}){
  const initialTarget = material.ip_id || preferredWriter?.ipId || c.ip || (c.ips.length === 1 ? c.ips[0].id : '');
  const [target,setTarget]=useState(initialTarget),[manualWriterId,setManualWriterId]=useState<string|null>(null),[reference,setReference]=useState(''),[confirmed,setConfirmed]=useState(false),[instructions,setInstructions]=useState(''),[commentIds,setCommentIds]=useState<string[]>([]),[needId,setNeedId]=useState(''),[evidenceIds,setEvidenceIds]=useState<string[]>([]),[context,setContext]=useState<Row|null>(null),[busy,setBusy]=useState(false),[job,setJob]=useState<Row|null>(null),[requestId,setRequestId]=useState(()=>crypto.randomUUID()),[pollError,setPollError]=useState('');
  const extract = sourceKind === 'materials' && material.transcript_supported === true;
  const transcriptPath = `/materials/${material.id}/transcript`;
  const [transcript, setTranscript] = useState<TranscriptResult>({});
  const [transcriptPending, setTranscriptPending] = useState(extract);
  const [transcriptError, setTranscriptError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [feishuBusy, setFeishuBusy] = useState(false);
  const [feishuError, setFeishuError] = useState('');
  const referenceEdited = useRef(false);
  const receivedTranscript = useRef(false);
  useEffect(() => {
    if (!extract) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let failures = 0;
    const apply = (result: TranscriptResult): boolean => {
      setTranscript(result);
      if (result.text && !receivedTranscript.current) {
        receivedTranscript.current = true;
        if (!referenceEdited.current) {
          setReference(result.text);
          setConfirmed(false);
          setContext(null);
          setRequestId(crypto.randomUUID());
        }
      }
      const running = result.status === 'queued' || result.status === 'running';
      setTranscriptPending(running);
      setTranscriptError(result.status === 'failed' ? result.error || '逐字稿提取失败，请重试或手工补录。' : '');
      return running || result.feishu?.status === 'pending';
    };
    async function poll() {
      try {
        const result = await api<TranscriptResult>(transcriptPath, {signal: controller.signal});
        if (controller.signal.aborted) return;
        failures = 0;
        if (apply(result)) timer = window.setTimeout(poll, 900);
        else if (result.status === 'idle') setTranscriptError('转写任务已结束，请重试提取。');
      } catch {
        if (controller.signal.aborted) return;
        if (++failures < 3) timer = window.setTimeout(poll, 1500);
        else {
          setTranscriptPending(false);
          if (receivedTranscript.current) {
            setTranscript(current => ({...current, feishu: {status: 'failed', message: '读取飞书同步状态失败。'}}));
          } else setTranscriptError('读取转写进度失败，请重试或手工补录。');
        }
      }
    }
    async function start() {
      try {
        const current = await api<TranscriptResult>(transcriptPath, {signal: controller.signal});
        if (controller.signal.aborted) return;
        if (current.status !== 'idle' && !(current.status === 'failed' && attempt > 0)) {
          if (apply(current)) timer = window.setTimeout(poll, 900);
          return;
        }
        const result = await api<TranscriptResult>(transcriptPath + '?background=true', {
          method: 'POST', body: '{}', signal: controller.signal,
        });
        if (!controller.signal.aborted && apply(result)) timer = window.setTimeout(poll, 900);
      } catch (error) {
        if (controller.signal.aborted) return;
        setTranscriptPending(false);
        setTranscriptError(error instanceof Error ? error.message : '转写请求失败，请重试或手工补录。');
      }
    }
    timer = window.setTimeout(start, 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [extract, transcriptPath, attempt]);
  async function retryFeishu() {
    setFeishuBusy(true);
    setFeishuError('');
    try {
      const feishu: FeishuTranscriptResult = await post(transcriptPath + '/feishu', {});
      setTranscript(current => ({...current, feishu}));
      if (feishu.status === 'pending') setAttempt(value => value + 1);
    } catch (error) {
      setFeishuError(error instanceof Error ? error.message : '飞书同步失败，请重试。');
    } finally {
      setFeishuBusy(false);
    }
  }
  const defaults=useRows('/default-writers'+(target?'?ip_id='+encodeURIComponent(target):''),c.version)
    .filter(w=>(w.ip_id||'')===target);
  const personal=useRows('/writers',c.version)
    .filter(w=>sourceKind==='materials'&&w.ip_id===target&&w.status==='active')
    .sort((a,b)=>Number(Boolean(b.is_default))-Number(Boolean(a.is_default))
      || String(b.updated_at||'').localeCompare(String(a.updated_at||'')));
  const leads=useRows('/records/leads',c.version);
  const demands=effectiveLeadsForIp(leads,target);
  const selectedNeedId=demands.some(lead=>lead.id===needId)?needId:'';
  const drafts=useRows('/drafts',c.version).filter(d=>d.ip_id===target&&d.history?.some((h:Row)=>h.body&&h.body!==d.body));
  const builtins=defaults.filter(w=>sourceKind==='materials'||w.kind==='assistant');
  const options=[...personal,...builtins];
  const fallbackId=sourceKind==='drafts'?'default:writer-self-evolve':extract?'default:viral-rewrite-writer':'default:original-video-writer';
  const preferred=preferredWriter&&preferredWriter.ipId===target
    ?personal.find(w=>w.id===preferredWriter.writerId):undefined;
  const chosen=options.find(w=>w.id===manualWriterId)||preferred||personal[0]||builtins.find(w=>w.id===fallbackId);
  const aux=chosen?.kind==='assistant';
  function invalidate(){setContext(null);setRequestId(crypto.randomUUID())}
  useEffect(()=>{if(!job||['completed','failed'].includes(job.status))return;let active=true;const timer=setInterval(async()=>{try{const jobs=await api<Row[]>('/jobs');const latest=jobs.find(j=>j.id===job.id);if(active&&latest){setJob(latest);setPollError('')}}catch{if(active)setPollError('状态获取失败，请在工作台查看任务，勿重复提交。')}},2000);return()=>{active=false;clearInterval(timer)}},[job?.id,job?.status]);
  async function submit(generate:boolean){if(!chosen)return;setBusy(true);try{const r=await c.run(()=>post(`/${sourceKind}/${material.id}/${sourceKind==='drafts'?'analysis':'writing'}${generate?'':'/context'}`,{source_version:material.version||null,ip_id:target,writer_id:chosen.id,writer_version:chosen.version,enhancement_version:chosen.is_system?chosen.enhancement_version:null,reference,transcript_confirmed:confirmed,comment_ids:commentIds,evidence_ids:evidenceIds,need_id:selectedNeedId||null,instructions,request_id:requestId}),generate?'写手任务已提交，结果另存':undefined);if(r){if(generate)setJob(r);else setContext(r)}}finally{setBusy(false)}}
  const needsTranscript = chosen && ORAL_SKILLS.includes(chosen.skill)
    && (chosen.skill === 'viral-rewrite-writer' || extract || !!reference.trim());
  const canPreview=target&&chosen&&(chosen.skill!=='viral-rewrite-writer'||reference.trim()&&confirmed);
  const canSubmit=canPreview&&(!needsTranscript||reference.trim()&&confirmed);
  return <Modal title={sourceKind==='drafts'?'稿件辅助分析':'素材调用写手'} onClose={onClose}>
    <p>{material.title}{sourceKind==='materials'&&material.source?.startsWith('https://')&&(
      <> · <a href={material.source} target="_blank" rel="noreferrer">查看原作品</a></>
    )}</p>
    <div className="form-grid">
      <Field label="调用目标IP"><select aria-label="调用目标IP" required disabled={!!material.ip_id||!!job} value={target} onChange={e=>{setTarget(e.target.value);setNeedId('');setEvidenceIds([]);setCommentIds([]);setManualWriterId(null);invalidate()}}><option value="">请选择目标IP</option>{c.ips.map(ip=><option key={ip.id} value={ip.id}>{ip.name}</option>)}</select></Field>
      <Field label="选择写手">
        <select aria-label="选择写手" disabled={!!job} value={chosen?.id||''} onChange={e=>{setManualWriterId(e.target.value);invalidate()}}>
          {!chosen&&<option value="" disabled>正在读取写手…</option>}
          {!!personal.length&&<optgroup label="当前IP专属写手">{personal.map(w=><option key={w.id} value={w.id}>{w.name} · v{w.version}</option>)}</optgroup>}
          <optgroup label={sourceKind==='drafts'?'辅助分析写手':'默认8个写手'}>{builtins.map(w=><option key={w.id} value={w.id}>{w.name} · {w.kind==='content'?'内容写作':'辅助分析'}</option>)}</optgroup>
        </select>
      </Field>
    </div>
    <Notice>{aux?'此能力输出辅助分析报告，不生成可发表稿件；建议须单独审核后才能成为规则。':'此能力生成独立待审稿，并匹配共享库里与文体和任务相关的已核验资料。'}{chosen?.knowledge?` 当前有效知识 ${chosen.knowledge.count} 条，IP增强 v${chosen.enhancement_version}。`:''}</Notice>
    {extract && <div className="transcript-status">
      {(transcriptPending || transcript.text) && <>
        <div className="transcript-progress-label">
          <span>{transcript.stage || (transcript.text ? '逐字稿已保存' : '正在读取转写任务')}</span>
          <strong>{transcript.text ? 100 : transcript.progress ?? 0}%</strong>
        </div>
        <progress aria-label="逐字稿提取进度" max={100} value={transcript.text ? 100 : transcript.progress ?? 0}/>
      </>}
      {transcript.text && <p>视频语音已转写{transcript.cached ? ' · 已复用转写结果' : ''}。请对照原视频核对。</p>}
      {transcriptError && <>
        <p className="error-text" role="alert">{transcriptError}</p>
        <button type="button" disabled={!!job||busy} onClick={()=>{
          setTranscript({});
          setTranscriptError('');
          setTranscriptPending(true);
          setAttempt(value=>value+1);
        }}>重试提取</button>
      </>}
      {transcript.feishu && <div className="transcript-feishu">
        <p className={['failed', 'conflict'].includes(transcript.feishu.status) ? 'error-text' : 'muted'} role="status">
          {feishuBusy ? '正在同步飞书' : transcript.feishu.message ||
            (transcript.feishu.status === 'pending' ? '正在同步飞书' :
              transcript.feishu.status === 'synced' ? '已同步到飞书作品表' : '飞书逐字稿尚未同步')}
        </p>
        {!['pending', 'synced'].includes(transcript.feishu.status) &&
          <button type="button" disabled={feishuBusy} onClick={retryFeishu}>重试同步飞书</button>}
        {feishuError && <p className="error-text" role="alert">{feishuError}</p>}
      </div>}
    </div>}
    <Field label="对标逐字稿"><textarea aria-label="对标逐字稿" disabled={!!job} rows={4} maxLength={20000} value={reference} onChange={e=>{referenceEdited.current=true;setReference(e.target.value);setConfirmed(false);invalidate()}} placeholder={transcriptPending?'等待自动提取，也可手工粘贴原视频的口播全文。':'可手工粘贴原视频的口播全文。'}/></Field>
    {transcript.text && referenceEdited.current && !job && reference!==transcript.text && <button type="button" disabled={busy} onClick={()=>{referenceEdited.current=false;setReference(transcript.text || '');setConfirmed(false);invalidate()}}>填入自动转写结果</button>}
    <label className="check-row"><input type="checkbox" disabled={!!job||!reference.trim()} checked={confirmed} onChange={e=>{setConfirmed(e.target.checked);invalidate()}}/>我已核对逐字稿{needsTranscript?'（提交口播前必选）':''}</label>
    <Field label="任务要求"><textarea aria-label="任务要求" disabled={!!job} rows={2} maxLength={2000} value={instructions} onChange={e=>{setInstructions(e.target.value);invalidate()}}/></Field>
    <LeadDemandSelect leads={demands} disabled={!!job||!target} value={selectedNeedId} onChange={id=>{setNeedId(id);invalidate()}}/>
    <WorkCommentPicker materialId={sourceKind==='materials'?material.id:material.material_id} source={sourceKind==='materials'?material:undefined}
      sourceRestricted={material.reference_restricted} version={c.version} ipId={target} leads={leads} selected={commentIds}
      disabled={!!job} onChange={ids=>{setCommentIds(ids);invalidate()}}/>
    {chosen?.skill==='writer-self-evolve'&&<details open><summary>真实改稿对照（可选）</summary>{drafts.map(d=><label className="check-row" key={d.id}><input type="checkbox" disabled={!!job} checked={evidenceIds.includes(d.id)} onChange={e=>{setEvidenceIds(e.target.checked?[...evidenceIds,d.id]:evidenceIds.filter(id=>id!==d.id));invalidate()}}/>{d.title}</label>)}<p className="muted">少于3篇独立改稿只生成候选建议，不形成长期语感规律。</p></details>}
    {!job&&<div className="button-row"><button disabled={!canPreview||busy} onClick={()=>submit(false)}>预览上下文</button><button className="primary" disabled={!canSubmit||busy} onClick={()=>submit(true)}>提交写手任务</button></div>}
    {context&&<div className="rewrite-preview"><p>{context.writer_snapshot.name} · 基础 v{context.writer_snapshot.version} · IP增强 v{context.writer_snapshot.enhancement_version||'专属版本'} · 引用知识 {context.sources.length} 条</p><textarea className="context-text" aria-label="素材写手提示包" readOnly value={context.prompt}/><button onClick={()=>saveText('素材写手提示包.txt',context.prompt)}>导出提示包</button></div>}
    {job&&<div className="rewrite-job"><Badge value={job.status}/><p>任务 {job.id.slice(0,8)}，关闭窗口后仍可在工作台查看。</p>{pollError&&<p className="error-text">{pollError}</p>}{job.error&&<p className="error-text">{job.error}</p>}{job.raw_output&&<Field label="模型原始结果"><textarea readOnly className="context-text" value={job.raw_output}/></Field>}{job.status==='completed'&&<button className="primary" onClick={async()=>{await c.run(async()=>true);onClose();c.go(job.analysis_id?'writers':'drafts',job.draft_id)}}>{job.analysis_id?'查看辅助分析':'查看待审稿'}</button>}</div>}
  </Modal>;
}
