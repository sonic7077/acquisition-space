import {useState,type FormEvent} from 'react';
import {post,saveText,type Row} from '../api';
import {type Ctx,useRows,verifiedKnowledge} from '../context';
import {Card,Field,Modal,Notice} from '../components/ui';

export default function DefaultWriters(c:Ctx){
  const target=c.ip;
  const [selected,setSelected]=useState(''),[proposal,setProposal]=useState<Row|null>(null),[busy,setBusy]=useState(false);
  const defaults=useRows('/default-writers'+(target?'?ip_id='+encodeURIComponent(target):''),c.version);
  const docs=verifiedKnowledge(useRows('/knowledge',c.version));
  const drafts=useRows('/drafts',c.version).filter(d=>d.ip_id===target&&d.history?.some((h:Row)=>h.body&&h.body!==d.body));
  const analyses=useRows('/writing/analyses',c.version).filter(d=>d.ip_id===target);
  const current=defaults.find(w=>w.id===selected),manage=c.me.role!=='member';
  async function action(fn:()=>Promise<any>,message:string){setBusy(true);try{return await c.run(fn,message)}finally{setBusy(false)}}
  async function save(e:FormEvent<HTMLFormElement>){e.preventDefault();if(!current||!proposal)return;const r=await action(()=>post(`/default-writers/${current.skill}/candidates`,{...proposal,ip_id:target,version:current.enhancement_version}),'已保存候选建议，等待管理员或团队长采纳');if(r)setProposal(null)}
  return <Card title="默认8个写手SKILL" subtitle="5 个内容写手生成待审稿，3 个辅助写手生成分析结果。">
    <div className="default-writer-grid">{defaults.map(w=><article className="default-writer-card" key={w.id}><div className="default-writer-title"><h3>{w.name}</h3><span className="badge">{w.kind==='content'?'内容写作':'辅助分析'}</span></div><p>基础 v{w.version} · IP增强 v{w.enhancement_version}</p><small>{w.knowledge?`有效知识 ${w.knowledge.count} 条 · 版本 ${w.knowledge.fingerprint.slice(0,8)}`:'请选择目标IP查看知识增强'}</small><p className="muted">{w.rules.length} 条生效规则{w.inactive_rule_count?` · ${w.inactive_rule_count} 条因知识失效停用`:''}</p><div className="button-row"><button onClick={()=>setSelected(w.id)}>规则与知识增强</button></div></article>)}</div>
    {current&&<Modal title={current.name+' · 规则与知识增强'} onClose={()=>setSelected('')}>
      <p>基础 v{current.version}（{current.base_version}） · IP增强 v{current.enhancement_version}</p>
      {current.knowledge?<><Notice>当前有效知识 {current.knowledge.count} 条。知识更新用于新调用；历史任务保留快照。基础模板共享，以下规则只对所选IP生效。</Notice><details><summary>知识版本与变化</summary><p className="muted">相对于最近规则变更时的知识快照：新增 {current.knowledge.changes.added.length} · 变更 {current.knowledge.changes.changed.length} · 移出有效范围 {current.knowledge.changes.removed.length}</p>{(['added','changed','removed'] as const).map((key,i)=><div key={key}><strong>{['新增','变更','移出有效范围'][i]}</strong>{current.knowledge.changes[key].map((d:Row)=><p key={d.id}>{d.title} · {d.category_path||d.category_id||'目录未记录'}</p>)}</div>)}<small>知识指纹 {current.knowledge.fingerprint.slice(0,16)}</small></details></>:<Notice>请选择目标IP后可提交该IP的完善建议。</Notice>}
      <h3>生效规则 · {current.rules.length}</h3>{current.rules.map((r:Row)=><div className="default-rule" key={r.id}><strong>{r.title}</strong><p><del>{r.before||'（原规则无）'}</del> → <ins>{r.after}</ins></p><small>{r.source==='business'?'知识支持的业务补充':r.source==='learned'?'真实改稿学习':'人工表达偏好'} · 采纳人 {r.adopted_by?.slice(0,8)}</small>{r.sources.map((s:Row)=><p key={s.id} className="muted">知识：{s.title} · {s.hash.slice(0,8)}</p>)}</div>)}
      {current.inactive_rule_count>0&&<Notice>{current.inactive_rule_count} 条规则的知识依据已失效，当前不参与写作；恢复有效证据或重新提交建议。</Notice>}
      <button disabled={!target||busy} onClick={()=>setProposal({title:'',before:'',after:'',source:'business',knowledge_ids:[],evidence_ids:[],analysis_id:null})}>提交完善建议</button>
      <h3>候选建议</h3>{current.candidates.filter((r:Row)=>r.status==='candidate').map((r:Row)=><div className="default-rule" key={r.id}><strong>{r.title}</strong><p><del>{r.before||'（无）'}</del> → <ins>{r.after}</ins></p><small>来源：{r.source==='business'?'知识业务补充':r.source==='learned'?'真实改稿学习':'人工偏好'}{r.analysis_id?' · 关联分析报告':''}</small>{manage&&<button disabled={busy} onClick={()=>action(()=>post(`/default-writers/${current.skill}/candidates/${r.id}/adopt`,{ip_id:target,version:current.enhancement_version}),'建议已采纳，生成新的IP增强版本')}>采纳建议</button>}</div>)}
      <details><summary>历史与回退 · {current.history.length} 个版本</summary>{current.history.slice().reverse().map((h:Row)=><div className="default-rule" key={h.version}><p>IP增强 v{h.version} · {h.rules.length} 条规则 · {new Date(h.changed_at).toLocaleString()}</p>{h.rules.map((r:Row)=><p key={r.id}>{r.title}：{r.after}</p>)}{manage&&<button disabled={busy} onClick={()=>action(()=>post(`/default-writers/${current.skill}/rollback`,{ip_id:target,version:current.enhancement_version,target_version:h.version}),'已回退规则并生成新版本，仍使用当前有效知识')}>回退到 v{h.version}</button>}</div>)}</details>
      <details><summary>查看基础SKILL全文</summary><textarea className="context-text" aria-label="默认写手SKILL全文" readOnly value={current.text}/><button onClick={()=>saveText(current.name+'-SKILL.md',current.text)}>下载基础SKILL</button></details>
    </Modal>}
    {proposal&&current&&<Modal title="默认写手完善建议" onClose={()=>setProposal(null)}><form onSubmit={save}>
      <Field label="规则来源"><select aria-label="规则来源" value={proposal.source} onChange={e=>setProposal({...proposal,source:e.target.value})}><option value="business">共享知识支持的业务补充</option><option value="learned">真实改稿学习（至少3篇独立内容）</option><option value="manual">人工表达偏好（不作为事实）</option></select></Field>
      {[['title','规则说明'],['before','改前规则'],['after','改后规则']].map(([key,label])=><Field key={key} label={label}><textarea aria-label={label} required={key!=='before'} maxLength={key==='title'?200:3000} rows={2} value={proposal[key]} onChange={e=>setProposal({...proposal,[key]:e.target.value})}/></Field>)}
      <Field label="参考分析报告（可选）"><select aria-label="参考分析报告（可选）" value={proposal.analysis_id||''} onChange={e=>setProposal({...proposal,analysis_id:e.target.value||null})}><option value="">不关联报告</option>{analyses.map(a=><option key={a.id} value={a.id}>{a.title} · {a.id.slice(0,8)}</option>)}</select></Field>
      {proposal.source==='business'&&<fieldset><legend>知识证据（至少1条）</legend>{docs.map(d=><label className="check-row" key={d.id}><input type="checkbox" checked={proposal.knowledge_ids.includes(d.id)} onChange={e=>setProposal({...proposal,knowledge_ids:e.target.checked?[...proposal.knowledge_ids,d.id]:proposal.knowledge_ids.filter((id:string)=>id!==d.id)})}/>{d.title}</label>)}{!docs.length&&<p>请先上传并核验共享知识。</p>}</fieldset>}
      {proposal.source==='learned'&&<fieldset><legend>真实改稿证据（至少3篇独立根内容）</legend>{drafts.map(d=><label className="check-row" key={d.id}><input type="checkbox" checked={proposal.evidence_ids.includes(d.id)} onChange={e=>setProposal({...proposal,evidence_ids:e.target.checked?[...proposal.evidence_ids,d.id]:proposal.evidence_ids.filter((id:string)=>id!==d.id)})}/>{d.title}</label>)}</fieldset>}
      <Notice>采用共享库中的已核验资料支持业务规则。辅助分析和表达偏好可作为候选建议。</Notice><button className="primary" disabled={busy}>保存候选建议</button>
    </form></Modal>}
  </Card>;
}
