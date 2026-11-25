import { useEffect, useState } from 'react';
import { api, type Row } from '../api';
export default function DiscoveryRules({version=0}:{version?:number}) {
  const [r,setR]=useState<Row|null>(null),[error,setError]=useState('');
  useEffect(()=>{let active=true;api('/discovery/rules').then(v=>{if(active){setR(v);setError('')}}).catch(()=>{if(active)setError('规则读取失败，请刷新后重试')});return()=>{active=false}},[version]);
  if(error)return <p className="error-text">{error}</p>;
  if(!r)return <p className="muted">正在读取作品推荐规则…</p>;
  return <div className="discovery-rules"><p>当前规则 v{r.version} · <strong>推荐倍数 ≥ {r.min_mult}</strong> · 阈值由管理员在系统设置中维护，修改规则不会重新采集。</p><div className="table-scroll"><table><thead><tr><th>指标</th><th>计算与当前阈值</th></tr></thead><tbody>
    <tr><td>样本倍数</td><td>点赞 ÷ max(已采作品中位赞, {r.denom_floor})；同博主、平台和归属范围内至少3条有效样本，中位数须大于0。倍数 ≥ {r.min_mult} 且点赞 ≥ {r.min_likes_candidate} 命中。该点赞下限仅用于倍数推荐。</td></tr>
    <tr><td>点赞等级</td><td>高赞 &gt; {r.likes_high}；普通 &gt; {r.likes_mid}；其余低赞。等级本身不会直接触发推荐。</td></tr>
    <tr><td>低粉高赞</td><td>点赞 &gt; {r.low_fan_min_likes} 且点赞/粉丝 &gt; {r.low_fan_ratio}；粉丝缺失或为0时未知。</td></tr>
    <tr><td>评赞比</td><td>评论/点赞；至少 {r.min_comments} 条评论，高 ≥ {(r.traffic_high*100).toFixed(1)}%，中 ≥ {(r.traffic_mid*100).toFixed(1)}%。高评赞比触发参考推荐，不能据此判断引流、成交。</td></tr>
    <tr><td>起量</td><td>相邻两轮有效快照涨赞 ≥ {r.rise_delta}；或发布 {r.rise_hours} 小时内点赞 ≥ {r.rise_new_likes} / 倍数 ≥ {r.rise_mult}。单次数据不能计算涨赞，未来日期不算新作。</td></tr>
    <tr><td>推荐池</td><td>倍数、低粉高赞、高评赞比、起量任一命中，且未排除/不可用、博主启用且非本人、发布时间有效并在 {r.stale_days} 天内。时间未知单列待核实，历史作品仍保留在全部。</td></tr>
    <tr><td>候选池</td><td>满足推荐规则且尚未采用的作品；采用后退出候选池，仍保留在全部、推荐和已写稿中。</td></tr>
    <tr><td>可用素材</td><td>未排除、未采用且来源可用的作品。推荐池外的作品也可供研究和写稿。</td></tr>
    <tr><td>综合参考分</td><td>0.45 × log₂(1+倍数) + 0.35 × min(评赞比×100,15)/3 + 0.20 × 起量标记；缺失项不贡献分数，不代表内容价值或获客效果。</td></tr>
  </tbody></table></div><p className="muted">头部同行指同业内容标杆，低粉黑马指值得研究的小体量或异常表现账号，跨界结构指可借鉴表达结构的其他赛道账号；分层由人工选择，不按粉丝阈值自动推断。样本受关键词和采集范围影响，不等于博主近30条或历史平均表现。未知指标不补0；不同平台的数据不宜直接比较价值。人工仍可采用推荐池外的作品。</p></div>;
}
