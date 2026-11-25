import DiscoveryRules from './DiscoveryRules';
import MaterialWriterDialog, { type WriterPreference } from './MaterialWriterDialog';
import { useEffect, useState, type FormEvent } from 'react';
import { Search, Plus, ExternalLink, RefreshCw, ArrowUpRight, SlidersHorizontal } from 'lucide-react';
import { api, post, patch, type Row } from '../api';
import { Card, Field, Notice, Badge, Empty, Modal, fmt } from '../components/ui';
import { type Ctx, useRows, filterIp } from '../context';
const PLATFORMS=[['douyin','抖音'],['xiaohongshu','小红书'],['wechat_channels','视频号']];
const DEFAULT_PLATFORMS=['douyin','xiaohongshu'];
const DEFAULT_PUBLISH_TIME='platform_default';
const LIMIT_FIELDS=[
  {key:'pages',label:'每词每平台搜索页数',min:1,max:5,initial:1},
  {key:'per_word_limit',label:'每词每平台最多作品数',min:1,max:20,initial:10},
  {key:'max_videos',label:'每平台最多入库作品数',min:1,max:20,initial:20},
  {key:'comment_pages',label:'每作品自动评论页数',min:1,max:5,initial:1},
] as const;
type LimitKey=(typeof LIMIT_FIELDS)[number]['key'];
type LimitDrafts=Record<LimitKey,string>;
const DEFAULT_LIMITS=Object.fromEntries(LIMIT_FIELDS.map(({key,initial})=>[key,String(initial)])) as LimitDrafts;
const count=(n:any)=>n==null?'—':Number(n).toLocaleString('zh-CN');
const date=(n:any)=>n?new Date(Number(n)*1000).toLocaleDateString('zh-CN'):'未知';
function sourcePlatform(r:Row) {
  if(r.platform)return r.platform;
  const url=r.source||'';
  if(/douyin\.com/.test(url))return '抖音';
  if(/xiaohongshu\.com|xhslink\./.test(url))return '小红书';
  if(/weixin\.qq\.com\/sph|channels\.weixin/.test(url))return '视频号';
  return '其他';
}
const commentLabel=(r:Row)=>r.comments_status==='legacy_partial'?'历史存档评论，非全量':r.comments_status==='failed'?'评论入库失败':r.comments_status==='pending'?'评论自动入库中':r.comments_has_more?'已采本次页数，仍有更多评论':r.comments_fetched_at?'本次评论已入库':'尚未采集';
export default function Topics(c:Ctx & {preferredWriter?: WriterPreference | null}) {
  const [pulse,setPulse]=useState(0),[service,setService]=useState<Row>({}),[target,setTarget]=useState(c.ip),
    [keywords,setKeywords]=useState(''),[advanced,setAdvanced]=useState(false),[busy,setBusy]=useState(false),
    [platforms,setPlatforms]=useState(DEFAULT_PLATFORMS),[limitDrafts,setLimitDrafts]=useState(DEFAULT_LIMITS),
    [showLimitErrors,setShowLimitErrors]=useState(false),
    [publishTime,setPublishTime]=useState(DEFAULT_PUBLISH_TIME),[searchSort,setSearchSort]=useState('1'),[enrich,setEnrich]=useState(false),
    [q,setQ]=useState(''),[tab,setTab]=useState('all'),[sort,setSort]=useState('likes'),[source,setSource]=useState('all'),
    [page,setPage]=useState(1),[modal,setModal]=useState(false),[writing,setWriting]=useState<Row|null>(null);
  const version=c.version+pulse;
  const rows=filterIp(useRows('/records/materials',version),c.ip),authors=useRows('/records/authors',version),runs=filterIp(useRows('/collection/runs',version),c.ip);
  useEffect(()=>{api<Row>('/collection/status').then(setService).catch(()=>{});},[version]);
  const active=runs.some(r=>['queued','running'].includes(r.status));
  useEffect(()=>{if(!active)return;const timer=setInterval(()=>setPulse(v=>v+1),3000);return()=>clearInterval(timer);},[active]);
  useEffect(()=>setTarget(c.ip),[c.ip]);
  useEffect(()=>setPage(1),[q,tab,source,sort,c.ip]);
  const shown=rows.filter(r=>(tab==='all'||tab==='recommended'&&r.discovery?.recommended||tab==='candidate'&&r.discovery?.candidate||tab==='rising'&&r.discovery?.rising||tab==='low_fan'&&r.discovery?.low_fan_hit||tab==='unknown_time'&&['unknown','future'].includes(r.discovery?.freshness)||tab==='excluded'&&r.status==='excluded'||tab==='adopted'&&r.status==='adopted'||tab==='available'&&r.discovery?.available)&&(source==='all'||sourcePlatform(r)===source)&&`${r.title} ${r.body||''} ${authors.find(a=>a.id===r.author_id)?.title||r.author||''} ${(r.keywords||[r.keyword]).join(' ')}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a,b)=>sort==='multiplier'?(b.discovery?.multiplier??-1)-(a.discovery?.multiplier??-1):sort==='reference'?(b.discovery?.reference_score??-1)-(a.discovery?.reference_score??-1):sort==='date'?(b.published_at||0)-(a.published_at||0):sort==='comments'?(b.comments_count||0)-(a.comments_count||0):sort==='collected'?new Date(b.collected_at||b.created_at).getTime()-new Date(a.collected_at||a.created_at).getTime():(b.likes||0)-(a.likes||0));
  const keywordCount=new Set(keywords.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean)).size;
  const limits=Object.fromEntries(LIMIT_FIELDS.map(({key})=>[key,Number(limitDrafts[key])])) as Record<LimitKey,number>;
  const invalidLimits=LIMIT_FIELDS.filter(({key,min,max})=>!/^\d+$/.test(limitDrafts[key].trim())||limits[key]<min||limits[key]>max);
  const {pages,max_videos:maxVideos,per_word_limit:perWord,comment_pages:commentPages}=limits;
  const estimate=invalidLimits.length?'—':keywordCount*pages*platforms.length+Math.min(maxVideos,perWord*keywordCount)*(commentPages*platforms.length+Number(platforms.includes('wechat_channels'))+Number(enrich&&platforms.includes('douyin')));
  async function collect(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const options=validatedSearchOptions();
    if(!options)return;
    setBusy(true);
    try {
      await c.run(()=>post('/collection/search',options),'搜索任务已提交，作品及评论会自动入库');
    } finally {
      setBusy(false);
    }
  }
  function validatedSearchOptions() {
    if(invalidLimits.length) {
      setAdvanced(true);
      setShowLimitErrors(true);
      const {key}=invalidLimits[0];
      requestAnimationFrame(()=>document.getElementById('topic-limit-'+key)?.focus());
      return null;
    }
    if(!platforms.length) {
      c.notify('请至少选择一个搜索平台');
      return null;
    }
    setShowLimitErrors(false);
    return {ip_id:target||null,platforms,keywords,...limits,enrich_authors:enrich,sort_type:searchSort,publish_time:publishTime};
  }
  function ipSelect(value:string,onChange:(s:string)=>void) {
    return <select aria-label="归属IP（可选）" value={value} onChange={e=>onChange(e.target.value)}><option value="">暂不归属IP</option>{c.ips.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select>;
  }
  return <>
    <Card title="爆款素材" subtitle={`当前范围 ${rows.length} 条内容 · 默认全部展示 · 筛选后 ${shown.length} 条`} action={<div className="button-row"><button onClick={()=>document.getElementById('topic-search')?.scrollIntoView({behavior:'smooth',block:'start'})}><Search size={15}/>关键词搜索</button><button disabled={!service.feishu_enabled} onClick={()=>c.run(()=>post('/integrations/feishu/sync',{ip_id:target||null}),'已提交三表同步任务')} title="同步下方搜索区选定IP；未选IP时仅同步本人未归属素材池">同步到飞书</button><button onClick={()=>setModal(true)}><Plus size={15}/>手工新增素材</button></div>}>
      <div className="toolbar topic-toolbar"><div className="tabs">{[['all','全部'],['recommended','推荐'],['candidate','候选池'],['rising','起量'],['low_fan','低粉高赞'],['unknown_time','时间待核实'],['available','可用素材'],['adopted','已写稿'],['excluded','已排除']].map(([k,t])=><button key={k} className={tab===k?'active':''} onClick={()=>setTab(k)}>{t}</button>)}</div><div className="button-row"><select aria-label="素材来源" value={source} onChange={e=>setSource(e.target.value)}><option value="all">全部平台来源</option>{['抖音','小红书','视频号',...Array.from(new Set(rows.map(sourcePlatform))).filter(p=>!['抖音','小红书','视频号'].includes(p))].map(p=><option key={p} value={p}>{p}</option>)}</select><select aria-label="列表排序" value={sort} onChange={e=>setSort(e.target.value)}><option value="likes">按点赞排序</option><option value="multiplier">按样本倍数</option><option value="reference">按综合参考分</option><option value="date">按发布时间</option><option value="comments">按评论排序</option><option value="collected">按入库时间</option></select><div className="search-box"><Search size={15}/><input aria-label="搜索素材" placeholder="搜标题、文案或博主" value={q} onChange={e=>setQ(e.target.value)}/></div></div></div>
      {active&&<p className="muted topic-help">搜索任务正在执行，内容列表与评论入库数量会自动更新。</p>}
      {shown.length?<div className="table-scroll"><table className="topics-table"><thead><tr><th>素材 / 来源博主</th><th>发布时间</th><th>互动数据</th><th>评论自动入库</th><th>爆款参考</th><th>操作</th></tr></thead><tbody>{shown.slice((page-1)*25,page*25).map(r=>{const a=authors.find(a=>a.id===r.author_id),d=r.discovery||{};return <tr key={r.id}>
        <td><div className="topic-material"><div className="topic-cover">{r.cover?<img src={r.cover} loading="lazy" referrerPolicy="no-referrer" alt="作品封面" onError={e=>{e.currentTarget.style.display='none'}}/>:<span>素材</span>}</div><div><strong className="table-title">{r.title}</strong><small><span className="badge">{sourcePlatform(r)}</span> <button className="text-button" onClick={()=>c.go('authors')}>{a?.title||r.author||'未记录作者'}</button> · 粉丝 {count(a?.followers)} · {r.duration!=null?`${r.duration}秒`:'时长未知'}</small><small>来源词：{r.keywords?.join('、')||r.keyword||(r.legacy_source?'保险行业历史存档':'手工录入')} · {c.ips.find(i=>i.id===r.ip_id)?.name||'未归属IP'}</small><div className="button-row">{r.source?.startsWith('https://')&&<a href={r.source} target="_blank" rel="noreferrer">看原内容 <ExternalLink size={12}/></a>}<Badge value={r.status}/></div></div></div></td>
        <td>{date(r.published_at)}<small>采集：{r.collected_at?fmt(r.collected_at):r.legacy_source?'历史存档（采集时间未知）':'手工记录'}</small></td>
        <td><strong>赞 {count(r.likes)}</strong><small>藏 {count(r.collects)} · 转 {count(r.shares)}</small><small>播放 {count(r.plays)}</small></td>
        <td><strong>平台 {count(r.comments_count)}</strong><small>已入库 {r.collected_comments||0}</small><small className={r.comments_status==='failed'?'error-text':'muted'} title={r.comments_error||''}>{commentLabel(r)}</small><button className="text-button" onClick={()=>c.go('leads')}>查看客户线索</button></td>
        <td><strong>{d.multiplier!=null?`样本倍数 ${d.multiplier.toFixed(1)} 倍`:r.author_sample_median_likes===0?'基线为0，倍数未知':'倍数未知 / 样本不足'}</strong><small>已采 {r.author_sample_count||0} 条有效样本 · 中位赞 {count(r.author_sample_median_likes)} · 分母 {count(d.denominator)}</small><small>{d.like_level||'点赞等级未知'} · 评赞比 {d.comment_ratio!=null?`${(d.comment_ratio*100).toFixed(1)}%（${d.comment_ratio_level}）`:'未知'}</small>{d.low_fan_hit&&<span className="badge">低粉高赞</span>}{d.rising&&<span className="badge">起量</span>}<small>{d.likes_delta!=null?`本轮涨赞 ${d.likes_delta} · 间隔 ${Math.round(d.observation_seconds/60)} 分钟`:'无两轮有效快照，涨赞未知'}</small>{d.reasons?.map((reason:string)=><small key={reason}>{reason}</small>)}{d.excluded_reason&&<small className="muted">{d.excluded_reason}</small>}<small>综合参考分 {d.reference_score??'未知'} · 规则 v{d.version||'—'}</small><span className={'badge '+(d.recommended?'good':'')}>{d.recommended?'推荐参考':'仅供研究'}</span></td>
        <td><div className="topic-actions"><button className="text-button" onClick={()=>setWriting(r)}>调用写手</button><button className="text-button" onClick={()=>c.run(()=>patch(`/records/materials/${r.id}`,{status:r.status==='excluded'?'new':'excluded'}),'素材状态已更新')}>{r.status==='excluded'?'恢复':'不要'}</button><small>{r.feishu_synced_at?'已同步飞书':'未同步飞书'}</small></div></td>
      </tr>})}</tbody></table></div>:<Empty title="暂无符合条件的素材" body="在下方选择平台并搜索关键词，内容和评论会自动入库。"/>}
      {shown.length>25&&<div className="toolbar"><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>上一页</button><span>第{page} / {Math.ceil(shown.length/25)}页</span><button disabled={page*25>=shown.length} onClick={()=>setPage(p=>p+1)}>下一页</button></div>}
      <details className="topic-criteria"><summary>爆款参考怎么算？</summary><DiscoveryRules version={c.version}/></details>
    </Card>
    <div id="topic-search" className="topic-search-anchor"><Card title="按关键词找对标内容" subtitle="TikHub · 多平台关键词搜索，作品与评论一次入库" action={<span className={'badge '+(service.enabled&&service.configured?'good':'')}>{service.enabled&&service.configured?'已配置服务':'等待管理员配置'}</span>}>
      <form className="topic-search" onSubmit={collect}>
        <p className="muted">关键词建议：业务词（你提供什么）、客户问题词（客户会问什么）、场景词（何时何地使用）。每次1至5词，以逗号、顿号或换行分隔；英文短语中的空格保留。</p>
        <fieldset className="topic-platforms"><legend>搜索平台（可多选）</legend>{PLATFORMS.map(([id,name])=><label className="check-row" key={id}><input type="checkbox" checked={platforms.includes(id)} onChange={e=>setPlatforms(p=>e.target.checked?[...p,id]:p.filter(x=>x!==id))}/>{name}</label>)}</fieldset>
        <div className="topic-search-row"><Field label="搜索关键词"><input placeholder="多个词用逗号分隔，如：职场沟通，咨询服务" value={keywords} onChange={e=>setKeywords(e.target.value)} required maxLength={300}/></Field><Field label="归属IP（可选）">{ipSelect(target,setTarget)}</Field><Field label="发布时间"><select name="publish_time" value={publishTime} onChange={e=>setPublishTime(e.target.value)}><option value={DEFAULT_PUBLISH_TIME}>按平台默认</option><option value="0">时间不限</option><option value="1">最近一天</option><option value="7">最近一周</option><option value="180">最近半年</option></select></Field><Field label="搜索排序"><select name="sort_type" value={searchSort} onChange={e=>setSearchSort(e.target.value)}><option value="1">按热度排序</option><option value="0">综合排序</option><option value="2">最新发布</option></select></Field><button className="primary" disabled={busy||!service.enabled||!service.configured||!platforms.length}><Search size={16}/>{busy?'正在提交…':'搜索并自动入库'}</button></div>
        {publishTime===DEFAULT_PUBLISH_TIME&&<p className="muted topic-help">抖音和视频号默认搜索最近半年，小红书不限时间。</p>}
        <button type="button" className="text-button topic-advanced-button" onClick={()=>setAdvanced(!advanced)} aria-expanded={advanced} aria-controls="topic-options"><SlidersHorizontal size={14}/>采集范围{advanced?' · 收起':' · 展开'}</button>
        {showLimitErrors&&invalidLimits.length>0&&<p className="error-text" role="alert">请检查采集范围中的数字。</p>}
        <div id="topic-options" className={'topic-options '+(!advanced?'is-collapsed':'')}>
          {LIMIT_FIELDS.map(field=>{
            const {key,label,min,max}=field;
            const id='topic-limit-'+key;
            const invalid=invalidLimits.includes(field);
            return <Field key={key} label={label}>
              <input id={id} name={key} type="text" inputMode="numeric" aria-label={label} aria-describedby={id+'-range'}
                aria-invalid={showLimitErrors&&invalid||undefined}
                value={limitDrafts[key]}
                onChange={e=>{const value=e.target.value;setLimitDrafts(drafts=>({...drafts,[key]:value}));}}
                onBlur={()=>{
                  const normalized=String(limits[key]);
                  if(!invalid&&normalized!==limitDrafts[key])setLimitDrafts(drafts=>({...drafts,[key]:normalized}));
                }}
              />
              <small id={id+'-range'} className={showLimitErrors&&invalid?'error-text':'muted'}>整数 {count(min)}–{count(max)}</small>
            </Field>;
          })}
          <label className="check-row"><input type="checkbox" checked={enrich} onChange={e=>setEnrich(e.target.checked)}/>补充抖音作者详情</label>
        </div>
        <p className="muted topic-help">本次调用上界 <strong>{estimate}</strong> 次；今日已用 {service.today_calls??'—'} / {service.daily_budget??'—'} 次。{!invalidLimits.length&&`每平台最多入库 ${maxVideos} 条作品，每条自动读取 ${commentPages} 页一级评论。`}视频号含必要详情调用。预算不足或接口失败会显示部分完成。</p>
        <p className="muted">作品入库后，按系统设置中的作品推荐规则统一计算。</p>
        <p className="muted">未选IP时进入本人未归属素材池，管理员与本团队团队长按权限可见。调用写手时再选目标IP。小红书支持图文和视频笔记；平台能力以TikHub实际返回为准。</p>
        {!service.configured&&<p className="muted">请管理员前往系统设置 → TikHub采集填写 API Key 并启用。{c.me.role==='admin'&&<button type="button" className="text-button" onClick={()=>c.go('settings')}>打开系统设置 <ArrowUpRight size={13}/></button>}</p>}
      </form>
    </Card></div>
    <Card title="采集与同步记录" subtitle="同一任务自动搜索并采集评论；重试会按原范围重新调用并去重，不会无限补采。" action={<button className="icon-button" aria-label="刷新采集记录" onClick={()=>setPulse(v=>v+1)}><RefreshCw size={16}/></button>}>
      {runs.length?<div className="table-scroll"><table><thead><tr><th>任务 / 范围</th><th>状态</th><th>调用 / 处理</th><th>结果说明</th><th>时间</th></tr></thead><tbody>{runs.slice(0,20).map(r=><tr key={r.id}><td><strong>{r.task==='feishu_sync'?'飞书三表同步':r.options?.material_id?'作品评论补采':r.options?.keywords}</strong><small>{c.ips.find(i=>i.id===r.ip_id)?.name||'未归属IP'} · {(r.options?.platforms||['douyin']).map((p:string)=>PLATFORMS.find(([id])=>id===p)?.[1]||p).join('、')}</small></td><td><Badge value={r.status}/>{['failed','partial'].includes(r.status)&&<button className="text-button" onClick={()=>c.run(()=>post('/collection/retry/'+r.id,{}),'已提交重试任务')}>手动重试</button>}</td><td>{r.calls} 次调用<small>{r.task==='collection'?`${r.materials||0} 作品 / ${r.authors||0} 博主 / ${r.comments||0} 评论处理`:`已同步 ${r.processed||0} 条`}</small></td><td><span className={r.error?'error-text':'muted'}>{r.error||r.progress||'等待后台执行'}</span>{r.conflict&&<details><summary>查看字段差异</summary><pre className="sync-conflict">{JSON.stringify(r.conflict,null,2)}</pre></details>}{r.sync_run_id&&<small>另有飞书同步任务</small>}</td><td>{fmt(r.created_at)}</td></tr>)}</tbody></table></div>:<Empty title="暂无采集记录" body="提交后显示搜索和评论入库进度、调用次数与错误。"/>}
    </Card>
    <Notice>评论自动按平台ID去重进入客户线索，标为研究评论；意向需人工核实。作品描述不等于语音逐字稿。飞书仅在管理员配置启用后同步；未归属IP同步仅包含本人素材池。</Notice>
    {writing&&<MaterialWriterDialog c={c} material={writing} preferredWriter={c.preferredWriter} onClose={()=>setWriting(null)}/>}
    {modal&&<Modal title="新增素材" onClose={()=>setModal(false)}><form onSubmit={e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.currentTarget));c.run(async()=>{await post('/records/materials',{...data,ip_id:target||null});setModal(false)},'素材已保存')}}><Field label="归属IP（可选）">{ipSelect(target,setTarget)}</Field><Field label="标题"><input name="title" required/></Field><Field label="正文 / 原话"><textarea name="body" rows={5}/></Field><Field label="作者"><input name="author"/></Field><Field label="来源平台"><select name="platform"><option>抖音</option><option>小红书</option><option>视频号</option><option>其他</option></select></Field><Field label="来源链接"><input name="source"/></Field><button className="primary">保存素材</button></form></Modal>}
  </>;
}
