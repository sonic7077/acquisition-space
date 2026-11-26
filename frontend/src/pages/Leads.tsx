import { useState, type FormEvent } from "react";
import { Search, Plus } from "lucide-react";
import { patch, post, type Row } from "../api";
import { Card, Field, Modal, STATE } from "../components/ui";
import { filterIp, IpSelect, useRows, type Ctx } from "../context";
import LeadTree from "./LeadTree";

const TYPES = ["需求", "反对", "追问", "共鸣", "待核实"];
const INTENTS = ["问价格", "要资料", "问方案", "无关", "待核实", "有意向"];
const FOLLOWUP = ["new", "待回复", "已回复", "咨询中", "已成交", "已流失"];

export default function Leads(c: Ctx) {
  const rows = filterIp(useRows("/records/leads", c.version), c.ip);
  const [query, setQuery] = useState("");
  const [intent, setIntent] = useState("all");
  const [contentType, setContentType] = useState("all");
  const [leadType, setLeadType] = useState("all");
  const [editing, setEditing] = useState<Row | null>(null);
  const [topicLead, setTopicLead] = useState<Row | null>(null);
  const [target, setTarget] = useState("");
  const [saving, setSaving] = useState(false);

  async function saveLead(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    const data = Object.fromEntries(new FormData(event.currentTarget));
    for (const field of ["content_type", "intent"]) {
      if (!data[field] || data[field] === editing[field]) delete data[field];
    }
    setSaving(true);
    try {
      await c.run(async () => {
        if (editing.id) await patch("/records/leads/" + editing.id, data);
        else await post("/records/leads", { ...data, ip_id: target });
        setEditing(null);
      }, "线索已保存");
    } finally {
      setSaving(false);
    }
  }

  async function createTopic(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!topicLead || !target) return;
    const title = String(new FormData(event.currentTarget).get("title") ?? "").trim();
    if (!title) return;
    setSaving(true);
    try {
      const draft = await c.run(() => post("/drafts", {
        ip_id: target,
        need_id: topicLead.id,
        title,
        body: "",
        skill: "original-video-writer",
      }), "已创建选题稿并关联来源线索");
      if (draft) {
        setTopicLead(null);
        c.go("drafts", draft.id);
      }
    } finally {
      setSaving(false);
    }
  }

  return <>
    <Card
      title="来源博主与客户线索"
      subtitle={`共 ${rows.length} 条 · 有效需求 ${rows.filter(r => r.effective_demand).length} 条`}
      action={<button className="primary" onClick={() => {
        setTarget(c.ip);
        setEditing({});
      }}><Plus size={16} />新增线索</button>}
    >
      <details className="lead-standards">
        <summary>评论分类与有效需求标准</summary>
        <p><strong>评论类型</strong> 依次判断需求（个人或家庭情境、直接求助）、反对（否定或质疑）、追问（疑问词或问号）、共鸣（经验分享或附和）。信息不足或明显推广记为待核实。</p>
        <p><strong>咨询意向</strong> 先排除明显质疑、推广和否定，再按问价格、要资料、问方案的顺序识别。问方案包括求助动作、明确提问，以及结合个人情况的年龄、预算、体况或险种信息。其余归为无关或待核实。</p>
        <p><strong>有效需求</strong> 有原话，意向为问价格、要资料或问方案，且类型为非反对。点「转为选题」即可引用原话创建稿件。修改标签后会按新标签计算，重分析保留人工修正。</p>
        <p><strong>跟进</strong> 采集评论用于研究。实际跟进前确认个人需求、业务匹配和联系路径。数量按记录统计，回复内容由人工发送。</p>
      </details>
      <div className="toolbar">
        <div className="tabs" aria-label="客户意向筛选">
          {["all", ...INTENTS].map(value => <button
            key={value}
            className={intent === value ? "active" : ""}
            onClick={() => setIntent(value)}
          >{value === "all" ? "全部" : value}</button>)}
        </div>
        <select aria-label="线索类型" value={leadType} onChange={e => setLeadType(e.target.value)}>
          <option value="all">全部线索与评论</option>
          <option value="research">研究评论</option>
          <option value="contactable">人工登记/已标记</option>
        </select>
        <select aria-label="评论类型筛选" value={contentType} onChange={e => setContentType(e.target.value)}>
          <option value="all">全部评论类型</option>
          {TYPES.map(value => <option key={value}>{value}</option>)}
        </select>
        <div className="search-box">
          <Search size={16} />
          <input aria-label="搜索记录" placeholder="搜索原话、博主或作品" value={query} onChange={e => setQuery(e.target.value)} />
        </div>
        <button onClick={() => c.run(() => post("/comments/analyze", { ip_id: c.ip || null }), "已完成本地分析，人工标签保留")}>本地重分析评论</button>
      </div>
      <LeadTree
        c={c}
        rows={rows}
        q={query}
        type={leadType}
        intent={intent}
        contentType={contentType}
        onEdit={row => { setTarget(row.ip_id || ""); setEditing(row); }}
        onTopic={row => { setTarget(row.ip_id || c.ip); setTopicLead(row); }}
      />
    </Card>
    {editing && <Modal title={editing.id ? "编辑线索" : "新增线索"} onClose={() => setEditing(null)}>
      <form onSubmit={saveLead}>
        <Field label="目标IP">
          {editing.id ? <input readOnly value={c.ips.find(ip => ip.id === editing.ip_id)?.name || "未归属IP"} />
            : <IpSelect ips={c.ips} value={target} onChange={setTarget} />}
        </Field>
        <Field label="标题"><input name="title" required defaultValue={editing.title} /></Field>
        <Field label="评论或咨询原话"><textarea name="body" rows={4} defaultValue={editing.body} /></Field>
        <Field label="来源链接或说明"><input name="source" defaultValue={editing.source} /></Field>
        <div className="form-grid">
          <Field label="评论类型">
            <select name="content_type" defaultValue={editing.content_type || ""}>
              {!editing.id && <option value="">自动判断</option>}
              {TYPES.map(value => <option key={value}>{value}</option>)}
            </select>
          </Field>
          <Field label="客户意向">
            <select name="intent" defaultValue={editing.intent || ""}>
              {!editing.id && <option value="">自动判断</option>}
              {INTENTS.map(value => <option key={value}>{value}</option>)}
            </select>
          </Field>
        </div>
        {editing.id && <Field label="跟进阶段">
          <select name="status" defaultValue={editing.status || "new"}>
            {editing.status && !FOLLOWUP.includes(editing.status) && <option>{editing.status}</option>}
            {FOLLOWUP.map(value => <option key={value} value={value}>{STATE[value] || value}</option>)}
          </select>
        </Field>}
        <Field label="建议回复 / 回复记录"><textarea name="reply" defaultValue={editing.reply} /></Field>
        <div className="form-grid">
          <Field label="负责人"><input name="owner" defaultValue={editing.owner} /></Field>
          <Field label="下次跟进"><input name="next_followup" type="date" defaultValue={editing.next_followup} /></Field>
        </div>
        <button className="primary" disabled={saving}>保存线索</button>
      </form>
    </Modal>}
    {topicLead && <Modal title="从线索转为选题" onClose={() => setTopicLead(null)}>
      <form onSubmit={createTopic}>
        <Field label="目标IP">
          {topicLead.ip_id ? <input readOnly value={c.ips.find(ip => ip.id === target)?.name || ""} />
            : <IpSelect ips={c.ips} value={target} onChange={setTarget} />}
        </Field>
        <Field label="线索原话"><textarea rows={3} readOnly value={topicLead.body || ""} /></Field>
        <Field label="选题标题"><input name="title" required maxLength={200} defaultValue={`客户${topicLead.intent}选题`} /></Field>
        <button className="primary" disabled={saving || !target}>创建选题稿</button>
      </form>
    </Modal>}
  </>;
}
