import { useEffect, useState } from 'react';
import { ChevronRight, UserRound, FileText, ExternalLink } from 'lucide-react';
import type { Row } from '../api';
import { Badge, Empty, fmt } from '../components/ui';
import { useRows, type Ctx } from '../context';
import { groupLeads } from './leadGroups';
export default function LeadTree({c,rows,q,type,intent,contentType="all",onEdit,onTopic}:{c:Ctx;rows:Row[];q:string;type:string;intent:string;contentType?:string;onEdit:(r:Row)=>void;onTopic:(r:Row)=>void}) {
  const [sort,setSort]=useState('collected'),[effective,setEffective]=useState(false);
  const authors=useRows('/records/authors',c.version),materials=useRows('/records/materials',c.version);
  const [opened,setOpened]=useState<Record<string,boolean>>({}),[limits,setLimits]=useState<Record<string,number>>({}),[mode,setMode]=useState<'default'|'all'|'none'>('default');
  useEffect(()=>{setOpened({});setMode('default');},[q,type,intent,contentType,c.ip,effective]);
  const selected=rows.filter(r=>!effective||r.effective_demand===true);
  const ordered=[...selected].sort((a,b)=>sort==='likes'?(b.likes??-1)-(a.likes??-1):new Date(b.collected_at||b.created_at||0).getTime()-new Date(a.collected_at||a.created_at||0).getTime());
  const groups=groupLeads(ordered,authors,materials,q,type,intent,contentType);
  const visible=groups.flatMap(g=>g.works.flatMap(w=>w.rows));
  const dist=(field:string)=>Array.from(new Set(visible.map(r=>r[field]||'待核实'))).map(value=>`${value} ${visible.filter(r=>(r[field]||'待核实')===value).length}`).join(' · ');
  const filtered=!!q.trim()||type!=='all'||intent!=='all'||contentType!=='all'||effective;
  const open=(key:string,root:boolean,manual=false)=>opened[key]??(mode==='all'||mode==='default'&&(root||manual||filtered));
  function toggle(key:string,value:boolean){setOpened(v=>({...v,[key]:value}));}
  function all(value:'all'|'none'){setMode(value);setOpened({});}
  const ipName=(id:string|null)=>c.ips.find(i=>i.id===id)?.name||'未归属IP';
  return <div className="lead-tree-area">
    <div className="toolbar"><label>评论排序 <select aria-label="评论排序" value={sort} onChange={e=>setSort(e.target.value)}><option value="collected">按采集时间</option><option value="likes">按点赞数</option></select></label><label className="check-row"><input type="checkbox" checked={effective} onChange={e=>setEffective(e.target.checked)}/>仅看有效需求</label></div>
    <div className="comment-distribution"><p><strong>类型分布</strong>：{dist('content_type')||'暂无'}</p><p><strong>意向分布</strong>：{dist('intent')||'暂无'}</p></div>
    <div className="lead-tree-toolbar"><span>匹配 {groups.length} 个来源分组 · {groups.reduce((n,g)=>n+g.works.length,0)} 个作品分组 · {groups.reduce((n,g)=>n+g.works.reduce((s,w)=>s+w.rows.length,0),0)} 条记录</span><div className="button-row"><button onClick={()=>all('all')}>展开全部</button><button onClick={()=>all('none')}>收起全部</button></div></div>
    {groups.length?<div className="lead-tree" aria-label="按来源博主和作品分组的客户线索">{groups.map(g=>{
      const allRows=g.works.flatMap(w=>w.rows),research=allRows.filter(r=>r.contactable===false).length;
      return <details className="lead-author" key={g.key} data-author-id={g.id} open={open(g.key,true)} onToggle={e=>{if(e.currentTarget.open!==open(g.key,true))toggle(g.key,e.currentTarget.open)}}>
        <summary><ChevronRight className="lead-chevron" size={17}/><span className="lead-node-icon"><UserRound size={18}/></span><div className="lead-node-title"><strong>{g.title}</strong><small>{g.platform} · {ipName(g.ipId)}{g.accountId?` · 平台账号 ${g.accountId}`:''}</small></div><span className="lead-node-count">{g.works.length} 个作品分组 · {allRows.length} 条记录<small>研究评论 {research} · 人工登记/已标记 {allRows.length-research}</small></span></summary>
        <div className="lead-works">{g.works.map(w=>{const key=g.key+':work:'+w.key,manual=w.key==='unlinked';return <details className="lead-work" key={key} open={open(key,false,manual)} onToggle={e=>{if(e.currentTarget.open!==open(key,false,manual))toggle(key,e.currentTarget.open)}}>
          <summary><ChevronRight className="lead-chevron" size={15}/><FileText size={16}/><div className="lead-node-title"><strong>来自作品：{w.title}</strong>{w.material?.published_at&&<small>发布时间：{new Date(w.material.published_at*1000).toLocaleDateString('zh-CN')}</small>}</div><span className="lead-node-count">{w.rows.length} 条记录</span></summary>
          <div className="lead-entries">{w.rows.slice(0,limits[key]||50).map(r=><article className="lead-entry" data-lead-id={r.id} key={r.id}>
            <div className="lead-entry-head">
              <strong>{r.title}</strong><span className="badge">{r.contactable===false?'研究评论':'人工登记/已标记'}</span>
              <Badge value={r.content_type||'待核实'}/><Badge value={r.intent||'待核实'}/>
              {r.effective_demand&&<span className="badge good">有效需求</span>}
              {r.manual_labels?.length>0&&<span className="badge">人工修正</span>}
              <div className="lead-entry-actions">
                <button className="text-button" onClick={()=>onEdit(r)}>查看 / 编辑</button>
                {r.effective_demand&&c.ips.length>0&&<button className="text-button" onClick={()=>onTopic(r)}>转为选题</button>}
              </div>
            </div>
            <p className="lead-original">{r.body||'未记录原话'}</p>
            <div className="lead-entry-meta"><span>跟进阶段：<Badge value={r.status}/></span><span>负责人：{r.owner||'未分配'}</span><span>下次跟进：{r.next_followup||'未安排'}</span></div>
            <small>{r.analysis_basis||r.intent_basis||'标签待核实'} · {r.analysis_version||'历史记录'}</small>
            {r.source_type==='platform_comment'&&<small>评论ID：{r.platform_comment_id||'未记录'} · 用户ID：{r.commenter_id||'未记录'} · 赞 {r.likes??'—'} · {r.comment_scope==='first_level'?'仅一级评论':'采集层级未记录'}{r.reply_count!=null?` · 平台回复数 ${r.reply_count}（未采回复正文）`:''}{r.parent_comment_id?` · 父评论ID ${r.parent_comment_id}`:''}</small>}
            {r.published_at&&<small>评论发布：{new Date(Number(r.published_at)*1000).toLocaleString('zh-CN')}</small>}
            {r.reply&&<p className="lead-reply">回复记录：{r.reply}</p>}
            <div className="lead-entry-footer"><small>{r.collected_at?`采集：${fmt(r.collected_at)}`:r.created_at?`登记：${fmt(r.created_at)}`:''}{r.feishu_synced_at?' · 已同步飞书':''}</small>{r.source?.startsWith('https://')?<a href={r.source} target="_blank" rel="noreferrer">原始来源 <ExternalLink size={12}/></a>:r.source&&<small>来源：{r.source}</small>}</div>
          </article>)}{w.rows.length>(limits[key]||50)&&<button className="text-button" onClick={()=>setLimits(v=>({...v,[key]:(v[key]||50)+50}))}>再显示50条（剩余 {w.rows.length-(limits[key]||50)} 条）</button>}</div>
        </details>})}</div>
      </details>
    })}</div>:<Empty title="暂无匹配线索" body="调整搜索、意向或线索类型，或从选题库搜索内容自动入库评论。"/>}
  </div>;
}
