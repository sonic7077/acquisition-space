import { useState, type FormEvent } from 'react';
import { Plus, Search, ExternalLink } from 'lucide-react';
import { post, patch, type Row } from '../api';
import { Card, Empty, Field, Modal, Notice, fmt } from '../components/ui';
import { filterIp, useRows, type Ctx } from '../context';
const tiers=['未分层','头部同行','低粉黑马','跨界结构'];
const count=(v:any)=>v==null?'未知':Number(v).toLocaleString('zh-CN');
export default function Authors(c:Ctx) {
  const rows=filterIp(useRows('/records/authors',c.version),c.ip);
  const [q,setQ]=useState(''),[platform,setPlatform]=useState('all'),[tier,setTier]=useState('all'),[state,setState]=useState('all'),[modal,setModal]=useState<Row|null>(null),[target,setTarget]=useState(c.ip),[busy,setBusy]=useState(false);
  const shown=rows.filter(r=>(platform==='all'||r.platform===platform)&&(tier==='all'||(r.tier||'未分层')===tier)&&(state==='all'||state==='own'&&r.is_own||state==='enabled'&&r.enabled!==false&&!r.is_own||state==='disabled'&&r.enabled===false)&&[r.title,r.platform_id,r.body,r.note,...r.keywords||[]].join(' ').toLowerCase().includes(q.toLowerCase())).sort((a,b)=>Number(!!b.is_own)-Number(!!a.is_own)||String(b.last_discovered_at||b.created_at).localeCompare(String(a.last_discovered_at||a.created_at)));
  async function submit(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();const f=new FormData(e.currentTarget),data={...Object.fromEntries(f),enabled:f.has('enabled'),is_own:f.has('is_own')};
    setBusy(true);
    await c.run(async()=>{if(modal?.id)await patch('/records/authors/'+modal.id,data);else await post('/records/authors',{...data,ip_id:target||null});setModal(null)},'博主管理信息已保存');
    setBusy(false);
  }
  return <>
    <Notice>关键词搜索先找到作品，再将作品作者加入博主库；归属IP可选，评论同时自动入库。分层由人工判断，粉丝与样本仅反映已取得的数据。</Notice>
    <Card title="对标博主列表" subtitle={`当前范围 ${rows.length} 位 · 筛选后 ${shown.length} 位`} action={<div className="button-row"><button onClick={()=>c.go('materials')}><Search size={15}/>关键词发现博主</button><button className="primary" onClick={()=>{setTarget(c.ip);setModal({})}}><Plus size={15}/>新增对标博主</button></div>}>
      <div className="toolbar"><select aria-label="博主平台" value={platform} onChange={e=>setPlatform(e.target.value)}><option value="all">全部平台</option>{Array.from(new Set(['抖音','小红书','视频号',...rows.map(r=>r.platform).filter(Boolean)])).map(p=><option key={p}>{p}</option>)}</select><select aria-label="分层筛选" value={tier} onChange={e=>setTier(e.target.value)}><option value="all">全部分层</option>{tiers.map(t=><option key={t}>{t}</option>)}</select><select aria-label="博主状态" value={state} onChange={e=>setState(e.target.value)}><option value="all">全部账号</option><option value="enabled">启用的对标</option><option value="own">我的账号</option><option value="disabled">已停用</option></select><div className="search-box"><Search size={15}/><input aria-label="搜索博主" value={q} placeholder="名称、平台ID、命中词或备注" onChange={e=>setQ(e.target.value)}/></div></div>
      {shown.length?<div className="table-scroll"><table><thead><tr><th>博主 / 平台账号</th><th>管理标记</th><th>粉丝 / 作品样本</th><th>发现与归属</th><th>操作</th></tr></thead><tbody>{shown.map(r=><tr key={r.id}><td><strong className="table-title">{r.title}</strong><small>{r.platform||'平台未记录'}</small><small>平台ID：{r.platform_id||'未记录'}</small>{r.source?.startsWith('https://')&&<a href={r.source} target="_blank" rel="noreferrer">来源链接 <ExternalLink size={12}/></a>}{r.note&&<small>{r.note}</small>}</td><td><span className={'badge '+(r.enabled!==false?'good':'')}>{r.enabled===false?'已停用':'启用'}</span><small><span className="badge">{r.tier||'未分层'}</span>{r.is_own&&<span className="badge">我的账号</span>}</small></td><td><strong>粉丝 {count(r.followers)}</strong><small>已采作品 {r.work_count||0} · 有效样本 {r.sample_count||0}</small><small>样本中位赞 {count(r.sample_median_likes)}</small>{r.legacy_baseline&&<small>旧库中位赞 {count(r.legacy_baseline.median_likes)}（窗口未知，仅供核对）</small>}</td><td><small>{c.ips.find(i=>i.id===r.ip_id)?.name||'未归属IP'}</small><small>命中词：{r.keywords?.join('、')||'未记录'}</small><small>首次发现：{r.first_discovered_at?fmt(r.first_discovered_at):'未记录'}</small><small>最近发现：{r.last_discovered_at?fmt(r.last_discovered_at):'未记录'}</small><small>最近采集：{r.collected_at?fmt(r.collected_at):r.legacy_source?'历史存档，非实时数据':'手工登记'}</small></td><td><button className="text-button" onClick={()=>setModal(r)}>查看 / 编辑</button></td></tr>)}</tbody></table></div>:<Empty title="暂无匹配博主" body="通过关键词搜索作品发现作者，或手工登记已有对标。"/>}
    </Card>
    <Notice>“我的账号”和停用博主不参与对标推荐，历史作品与评论仍保留在全部列表。关键词搜索仍可能命中这些账号，已产生的接口调用正常计入预算。当前未接入按主页批量采集。</Notice>
    {modal&&<Modal title={modal.id?'编辑博主':'新增博主'} onClose={()=>setModal(null)}><form onSubmit={submit}>
      <Field label="归属IP（可选）">{modal.id?<input readOnly value={c.ips.find(i=>i.id===modal.ip_id)?.name||'未归属IP'}/>:<select value={target} onChange={e=>setTarget(e.target.value)}><option value="">暂不归属IP</option>{c.ips.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select>}</Field>
      <Field label="作者名称"><input name="title" required defaultValue={modal.title}/></Field>
      {!modal.id&&<><Field label="来源平台"><select name="platform"><option>抖音</option><option>小红书</option><option>视频号</option><option>其他</option></select></Field><Field label="平台账号ID（可选）"><input name="platform_id"/></Field></>}
      <Field label="简介"><textarea name="body" defaultValue={modal.body} rows={3}/></Field><Field label="来源链接或说明"><input name="source" defaultValue={modal.source}/></Field>
      <Field label="博主分层"><select name="tier" defaultValue={modal.tier||'未分层'}>{tiers.map(t=><option key={t}>{t}</option>)}</select></Field><Field label="博主备注"><textarea name="note" maxLength={2000} defaultValue={modal.note}/></Field>
      <label className="check-row"><input name="enabled" type="checkbox" defaultChecked={modal.enabled!==false}/>启用对标推荐</label><label className="check-row"><input name="is_own" type="checkbox" defaultChecked={modal.is_own}/>这是我的账号</label><button className="primary" disabled={busy}>保存博主</button>
    </form></Modal>}
  </>;
}
