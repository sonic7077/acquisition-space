import { useState, type FormEvent } from "react";
import { Search, Plus, Link as LinkIcon } from "lucide-react";
import { post, patch, type Row } from "../api";
import {
  Badge,
  Empty,
  Card,
  Modal,
  Field,
  Notice,
  STATE,
} from "../components/ui";
import LeadTree from "./LeadTree";
import { type Ctx, useRows, filterIp, IpSelect } from "../context";
export default function Records(c: Ctx & { kind: string }) {
  const rows = filterIp(useRows("/records/" + c.kind, c.version), c.ip),
    [q, setQ] = useState(""),
    [tab, setTab] = useState("all"),
    [contentType,setContentType] = useState("all"),
    [leadType,setLeadType] = useState("all"),
    [modal, setModal] = useState<Row | null>(null),
    [target, setTarget] = useState(c.ip);
  const noun =
    (
      {
        materials: "素材",
        authors: "对标博主",
        needs: "需求",
        leads: "线索",
      } as Row
    )[c.kind] || "记录";
  const shown = rows.filter(
    (r) =>
      (r.title + " " + r.body + " " + (r.source || "") + " " + (r.work_title || "") + " " + (r.platform_id || "")).includes(q) &&
      (c.kind !== "leads" || leadType === "all" || (leadType === "research" ? r.contactable === false : r.contactable !== false)) &&
      (tab === "all" ||
        (c.kind === "leads" ? r.intent === tab : r.status === tab)),
  );
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await c.run(async () => {
      if (modal?.id) await patch(`/records/${c.kind}/${modal.id}`, data);
      else await post("/records/" + c.kind, { ...data, ip_id: target });
      setModal(null);
    }, "已保存");
  }
  return (
    <>
      <Notice>
        {c.kind === "materials"
          ? "当前支持手工录入对标素材；在线关键词搜索需要后续采集适配，调用前应核对预算。"
          : c.kind === "authors"
            ? "TikHub获取的作品作者会自动进入对应IP的对标池；样本统计仅反映已获取作品，作者详情可在采集时补充。"
            : c.kind === "leads"
              ? "按来源博主 → 作品 → 评论/线索查看。研究评论不计入可跟进数量；人工登记/已标记沿用原统计口径，未必经过有效性核验。关键词意向仅为建议，回复记录不会自动发送。"
              : "保留客户原话与来源，先脱敏，再提炼需求与选题。"}
      </Notice>
      <Card
        title={c.kind === "leads" ? "来源博主与客户线索" : noun + "列表"}
        subtitle={`当前范围共 ${rows.length} 条${c.kind === "leads" ? ` · 研究评论 ${rows.filter(r=>r.contactable===false).length} 条 · 人工登记/已标记 ${rows.filter(r=>r.contactable!==false).length} 条` : ""}`}
        action={
          <button
            className="primary"
            onClick={() => {
              setTarget(c.ip);
              setModal({});
            }}
          >
            <Plus size={16} />
            新增{noun}
          </button>
        }
      >
        <div className="toolbar">
          <div className="tabs">
            {(c.kind === "leads"
              ? [
                  ["all", "全部"],
                  ["有意向", "有意向"],
                  ["要资料", "要资料"],
                  ["问方案", "问方案"],
                  ["问价格", "问价格"],
                  ["无关", "无关"],
                  ["待核实", "待核实"],
                ]
              : c.kind === "materials"
                ? [
                    ["all", "全部"],
                    ["new", "可采用"],
                    ["adopted", "已采用"],
                    ["excluded", "已排除"],
                  ]
                : [["all", "全部"]]
            ).map(([k, n]) => (
              <button
                key={k}
                className={tab === k ? "active" : ""}
                onClick={() => setTab(k)}
              >
                {n}
              </button>
            ))}
          </div>
          {c.kind === 'leads' && <select aria-label="线索类型" value={leadType} onChange={e=>setLeadType(e.target.value)}><option value="all">全部线索与评论</option><option value="research">研究评论</option><option value="contactable">人工登记/已标记（原可跟进口径）</option></select>}
          {c.kind === 'leads' && <><select aria-label="评论类型筛选" value={contentType} onChange={e=>setContentType(e.target.value)}>{['all','追问','反对','共鸣','需求','待核实'].map(v=><option key={v} value={v}>{v==='all'?'全部评论类型':v}</option>)}</select><button onClick={()=>c.run(()=>post('/comments/analyze',{ip_id:c.ip||null}),'已完成本地分析，人工标签保留')}>本地重分析评论</button></>}
          <div className="search-box">
            <Search size={16} />
            <input
              aria-label="搜索记录"
              placeholder="搜索标题、内容或来源"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
        {c.kind === "leads" ? <LeadTree c={c} rows={rows} q={q} type={leadType} intent={tab} contentType={contentType} onEdit={r=>{setTarget(r.ip_id||"");setModal(r)}}/> : shown.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{noun} / 内容</th>
                  <th>所属IP</th>
                  <th>来源</th>
                  <th>{c.kind === "leads" ? "意向" : "状态"}</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong className="table-title">{r.title}</strong>
                      <small className="ellipsis">
                        {r.body || r.reply || "—"}
                      </small>
                      {r.platform && <span className="badge">{r.platform}</span>}
                      {c.kind === "authors" && r.platform_id && <small>平台ID：{r.platform_id}</small>}
                      {c.kind === "authors" && r.source_type === "tikhub" && <small>粉丝 {r.followers == null ? '未知' : Number(r.followers).toLocaleString()} · 已采 {r.sample_count || 0} 条作品 · 样本中位点赞 {r.sample_median_likes ?? '样本不足'}</small>}
                      {c.kind === "leads" && r.source_type === "platform_comment" && <><span className="badge">研究评论</span><small>来自作品：{r.work_title || r.material_id}</small><small>评论ID：{r.platform_comment_id} · 赞 {r.likes ?? "—"}</small><small>{r.intent_basis || '意向待核实'}</small></>}
                      {r.feishu_synced_at && <small>已同步飞书</small>}
                      {c.kind === "leads" && (
                        <small>
                          负责人：{r.owner || "未分配"} · 下次跟进：
                          {r.next_followup || "未安排"}
                        </small>
                      )}
                    </td>
                    <td>{c.ips.find((i) => i.id === r.ip_id)?.name || "未归属IP"}</td>
                    <td>
                      {r.source?.startsWith("https://") ? (
                        <a href={r.source} target="_blank" rel="noreferrer">
                          原始来源 <LinkIcon size={12} />
                        </a>
                      ) : (
                        r.source || "手动记录"
                      )}
                    </td>
                    <td>
                      <Badge
                        value={
                          c.kind === "leads" ? r.intent || "待核实" : r.status
                        }
                      />
                    </td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          onClick={() => {
                            setTarget(r.ip_id);
                            setModal(r);
                          }}
                        >
                          查看 / 编辑
                        </button>
                        {c.kind === "materials" && (
                          <>
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(async () => {
                                  await post(`/materials/${r.id}/adopt`, {
                                    skill: "original-video-writer",
                                  });
                                  c.go("drafts");
                                }, "已创建待写稿件；有明确来源时作者同时加入对标池")
                              }
                            >
                              加入写稿
                            </button>
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(
                                  () =>
                                    patch(`/records/materials/${r.id}`, {
                                      status:
                                        r.status === "excluded"
                                          ? "new"
                                          : "excluded",
                                    }),
                                  "素材状态已更新",
                                )
                              }
                            >
                              {r.status === "excluded" ? "恢复" : "不要"}
                            </button>
                          </>
                        )}
                        {c.kind === "needs" && (
                          <button
                            className="text-button"
                            onClick={() =>
                              c.run(async () => {
                                await post("/drafts", {
                                  ip_id: r.ip_id,
                                  title: r.title.slice(0, 16),
                                  body: "",
                                  skill: "original-video-writer",
                                });
                                c.go("drafts");
                              }, "已创建原创选题稿")
                            }
                          >
                            转选题
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={`暂无${noun}`}
            body={`新增第一条${noun}，逐步积累真实业务数据。`}
          />
        )}
      </Card>
      {c.kind === "leads" && <Card title="客户线索标准与当前判定方式"><div className="lead-standards"><p><strong>双维度标签：</strong>类型为追问、反对、共鸣、需求、待核实；意向为问价格、问方案、要资料、无关、待核实。先排除明显质疑、否定和广告，再结合询问/索取表达判定，展示规则命中依据。人工标签在重分析和重采时保留。</p><p><strong>需求收集：</strong>在需求库配置主题关键词后整理评论。按当前标签将问价格、问方案、要资料计入有效需求，反对/共鸣单列研究；含质疑的评论也可能有真实需求，应结合原话人工修正，不确定表达保留待核实。</p><p><strong>统计口径：</strong>采集评论默认为研究评论（不计入可跟进数）。手工新增或旧记录未明确标记时，沿用系统默认计入的口径；登记、编辑意向或记录回复不等于已核验客户。</p><p><strong>建议有效线索标准：</strong>需求明确、与自身业务匹配、有可跟进路径，并经人工核实；再判断紧急度和购买意愿。关键词规则无法可靠识别所有否定、同行调研、广告或机器人，“要资料”不等于有购买意愿。当前尚未实现上述核验门槛。</p><p>记录数按评论或登记条目统计，同一用户在多条作品下评论可能出现多次，不等于去重客户人数；未关联来源的记录单列保留。</p></div></Card>}
      {modal && (
        <Modal
          title={(modal.id ? "编辑" : "新增") + noun}
          onClose={() => setModal(null)}
        >
          <form onSubmit={submit}>
            <Field label="目标IP">
              {modal.id ? <input readOnly value={c.ips.find(i=>i.id===modal.ip_id)?.name||"未归属IP"}/> : <IpSelect ips={c.ips} value={target} onChange={setTarget} />}
            </Field>
            <Field label={c.kind === "authors" ? "作者名称" : "标题"}>
              <input name="title" required defaultValue={modal.title} />
            </Field>
            <Field
              label={
                c.kind === "leads" ? "评论或咨询原话" : "正文 / 简介 / 原话"
              }
            >
              <textarea name="body" rows={5} defaultValue={modal.body} />
            </Field>
            <Field label="来源链接或说明">
              <input name="source" defaultValue={modal.source} />
            </Field>
            {c.kind === "materials" && (
              <Field label="作者">
                <input name="author" defaultValue={modal.author} />
              </Field>
            )}
            {c.kind === "leads" && (
              <>
                <Field label="评论类型"><select aria-label="评论类型" name="content_type" defaultValue={modal.content_type||'待核实'}>{['追问','反对','共鸣','需求','待核实'].map(v=><option key={v}>{v}</option>)}</select></Field>
                <div className="form-grid">
                  <Field label="客户意向">
                    <select
                      aria-label="客户意向" name="intent"
                      defaultValue={modal.intent || "待核实"}
                    >
                      {["有意向", "要资料", "问方案", "问价格", "无关", "待核实"].map(
                        (s) => (
                          <option key={s}>{s}</option>
                        ),
                      )}
                    </select>
                  </Field>
                  <Field label="跟进阶段">
                    <select name="status" defaultValue={modal.status || "new"}>
                      {[
                        "new",
                        "待回复",
                        "已回复",
                        "咨询中",
                        "已成交",
                        "已流失",
                      ].map((s) => (
                        <option key={s} value={s}>
                          {STATE[s] || s}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Field label="建议回复 / 回复记录">
                  <textarea name="reply" defaultValue={modal.reply} />
                </Field>
                <div className="form-grid">
                  <Field label="负责人">
                    <input name="owner" defaultValue={modal.owner} />
                  </Field>
                  <Field label="下次跟进">
                    <input
                      name="next_followup"
                      type="date"
                      defaultValue={modal.next_followup}
                    />
                  </Field>
                </div>
              </>
            )}
            <button className="primary">保存{noun}</button>
          </form>
        </Modal>
      )}
    </>
  );
}
