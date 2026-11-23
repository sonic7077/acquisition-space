import { useState } from "react";
import {
  BookOpen,
  Plus,
  ArrowRight,
  Download,
  Sparkles,
  FileText,
} from "lucide-react";
import { post, patch, saveText, type Row } from "../api";
import { Badge, Empty, Card, Modal, Field, Notice } from "../components/ui";
import { type Ctx, useRows, filterIp, IpSelect } from "../context";
import WriterProfiles from "./WriterProfiles";
export default function Writers(c: Ctx) {
  const skills = useRows("/skills", c.version),
    rules = filterIp(useRows("/records/rules", c.version), c.ip),
    drafts = useRows("/drafts", c.version),
    needs = useRows("/records/needs", c.version),
    writers = useRows("/writers", c.version),
    analyses = filterIp(useRows("/writing/analyses", c.version), c.ip);
  const [target, setTarget] = useState(c.ip),
    [skill, setSkill] = useState("original-video-writer"),
    [writerId, setWriterId] = useState(""),
    [needId, setNeedId] = useState(""),
    [topic, setTopic] = useState(""),
    [context, setContext] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [profile, setProfile] = useState(false),
    [ruleModal, setRuleModal] = useState<Row | null>(null);
  async function action(generate = false) {
    if (!target || !topic.trim()) {
      c.notify("请选择目标IP并填写主题");
      return;
    }
    setBusy(true);
    const r = await c.run(
      () =>
        post(generate ? "/writing/generate" : "/writing/context", {
          ip_id: target,
          topic,
          skill,
          need_id: needId || null,
          ...(!writerId ? {writer_id: "default:"+skill, writer_version: 1} : {}),
          ...(writerId ? {writer_id: writerId, writer_version: writers.find(w=>w.id===writerId)?.version} : {}),
        }),
      generate ? "写作任务已提交，可在工作台查看进度" : undefined,
    );
    if (r && !generate) setContext(r);
    setBusy(false);
  }
  return (
    <>
      <WriterProfiles {...c}/>
      <Card
        title="IP档案与专属写手"
        subtitle="每个IP独立管理身份、文体和表达规则"
        action={
          c.me.role === "admin" ? (
            <button onClick={() => c.go("users")}>
              <Plus size={16} />
              创建IP
            </button>
          ) : undefined
        }
      >
        <div className="ip-grid">
          {c.ips.map((i) => (
            <button
              key={i.id}
              className={"ip-card " + (target === i.id ? "selected" : "")}
              onClick={() => {setTarget(i.id);setWriterId("")}}
            >
              <span className="ip-avatar">{i.name.slice(0, 1)}</span>
              <strong>{i.name}</strong>
              <small>{i.profile ? "已填写身份档案" : "身份档案待补充"}</small>
            </button>
          ))}
          {!c.ips.length && (
            <Empty
              title="请先创建团队与IP"
              body="管理员在用户管理中创建团队和IP，再分配给成员。"
            />
          )}
        </div>
        {target && (
          <div className="button-row">
            <span className="muted">
              {c.ips.find((i) => i.id === target)?.profile ||
                "尚无身份档案，写手不会假定你的身份与经历。"}
            </span>
            {c.me.role !== "member" && (
              <button className="text-button" onClick={() => setProfile(true)}>
                编辑档案
              </button>
            )}
          </div>
        )}
      </Card>
      <Card
        title="创建内容任务"
        subtitle="按写手能力和任务检索共享知识，使用目标IP已采纳的规则"
      >
        <div className="form-grid">
          <Field label="目标IP">
            <IpSelect ips={c.ips} value={target} onChange={v=>{setTarget(v);setWriterId("");setNeedId("");setContext(null)}} />
          </Field>
          <Field label="写手能力">
            <select value={skill} onChange={(e) => setSkill(e.target.value)}>
              {skills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="使用专属写手（可选）"><select value={writerId} onChange={e=>setWriterId(e.target.value)}><option value="">使用所选能力对应的默认写手</option>{writers.filter(w=>w.ip_id===target&&w.status==='active').map(w=><option key={w.id} value={w.id}>{w.name} · v{w.version}</option>)}</select></Field>
        <Field label="关联需求（可选）"><select aria-label="关联需求（可选）" value={needId} onChange={e=>{setNeedId(e.target.value);setContext(null)}}><option value="">不关联需求</option>{needs.filter(n=>n.ip_id===target&&n.status!=='expired').map(n=><option key={n.id} value={n.id}>{n.title} · {n.topic_id?`${n.count}条有效需求`:'手工登记'}</option>)}</select></Field>
        <Field label="内容主题与要求">
          <textarea
            rows={4}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="例如：客户常问服务包含什么，希望用真实场景讲清楚选择标准。"
          />
        </Field>
        <div className="button-row">
          <button disabled={busy} onClick={() => action()}>
            <FileText size={16} />
            生成上下文预览
          </button>
          <button
            disabled={busy}
            className="primary"
            onClick={() => action(true)}
          >
            <Sparkles size={16} />
            提交AI写作任务
          </button>
        </div>
        <Notice>
          模型由管理员在系统设置中统一配置。未配置时可导出提示包，在外部完成写作后手工创建稿件。
        </Notice>
      </Card>
      {context && (
        <Card
          title="写作上下文"
          subtitle={`已引用 ${context.sources.length} 条已核验知识；未命中时不要编造资料。`}
          action={
            <button onClick={() => saveText("写作上下文.txt", context.prompt)}>
              <Download size={16} />
              导出提示包
            </button>
          }
        >
          <div className="source-list">
            {context.sources.map((s: Row) => (
              <span className="source-chip" key={s.id}>
                <BookOpen size={14} />
                {s.title}
              </span>
            ))}
          </div>
          <textarea className="context-text" aria-label="写作上下文预览" readOnly value={context.prompt} />
          <button className="text-button" onClick={() => c.go("drafts")}>
            去审稿页面导入成稿 <ArrowRight size={15} />
          </button>
        </Card>
      )}
      <Card
        title="写手学习与规则"
        subtitle="至少3篇独立根内容支持，再由团队长或管理员采纳"
        action={
          <button
            onClick={() =>
              setRuleModal({ title: "", body: "", evidence_ids: [] })
            }
          >
            <Plus size={16} />
            新增候选规律
          </button>
        }
      >
        {rules.length ? (
          <div className="rule-list">
            {rules.map((r) => (
              <div key={r.id}>
                <div>
                  <strong>{r.title}</strong>
                  <p>{r.body}</p>
                  <Badge value={r.status} />
                </div>
                {c.me.role !== "member" && (
                  <div className="button-row">
                    {r.status === "candidate" && (
                      <button
                        onClick={() => setRuleModal({ ...r, evidence_ids: [] })}
                      >
                        选择证据并采纳
                      </button>
                    )}
                    {r.status === "adopted" && (
                      <button
                        onClick={() =>
                          c.run(
                            () => post(`/rules/${r.id}/archive`, {}),
                            "规则已归档",
                          )
                        }
                      >
                        归档规则
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <Empty
            title="尚未形成写手规律"
            body="积累真实改稿证据，保持每条规则可解释、可回溯。"
          />
        )}
      </Card>
      {analyses.length>0&&<Card title="辅助分析结果" subtitle="需求挖掘、写手初始化与进化结果单独保存，不作为口播稿件批准。">{analyses.map(a=><details key={a.id}><summary>{a.title} · {skills.find(s=>s.id===a.skill)?.name}</summary><p className="muted">{a.writer_snapshot?.name||"通用分析"} · {a.ip_id?c.ips.find(i=>i.id===a.ip_id)?.name:""}{a.knowledge_snapshot?` · 知识版本 ${a.knowledge_snapshot.fingerprint.slice(0,8)}`:""} · 报告 {a.id.slice(0,8)}{a.material_id?` · 素材 ${a.material_id.slice(0,8)}`:""}</p><pre className="writer-history">{a.body}</pre><button onClick={()=>saveText(a.title+".md",a.body)}>导出分析结果</button></details>)}</Card>}
      {profile && (
        <Modal title="编辑IP身份档案" onClose={() => setProfile(false)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const d = Object.fromEntries(new FormData(e.currentTarget));
              c.run(async () => {
                await patch("/ips/" + target, d);
                setProfile(false);
              }, "档案已保存");
            }}
          >
            <Field label="身份、产品、受众与表达要求">
              <textarea
                name="profile"
                rows={9}
                defaultValue={c.ips.find((i) => i.id === target)?.profile}
                required
              />
            </Field>
            <button className="primary">保存档案</button>
          </form>
        </Modal>
      )}
      {ruleModal && (
        <Modal
          title={ruleModal.id ? "选择独立证据" : "新增候选规律"}
          onClose={() => setRuleModal(null)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const d = Object.fromEntries(new FormData(e.currentTarget));
              c.run(async () => {
                if (ruleModal.id)
                  await post(`/rules/${ruleModal.id}/adopt`, {
                    evidence_ids: ruleModal.evidence_ids,
                  });
                else await post("/records/rules", { ...d, ip_id: target });
                setRuleModal(null);
              }, "规则已保存");
            }}
          >
            {!ruleModal.id ? (
              <>
                <Field label="目标IP">
                  <IpSelect ips={c.ips} value={target} onChange={setTarget} />
                </Field>
                <Field label="规律名称">
                  <input name="title" required />
                </Field>
                <Field label="规则内容">
                  <textarea name="body" required rows={4} />
                </Field>
              </>
            ) : (
              <>
                <p>选择至少3篇独立根内容，不能用同稿改版凑数。</p>
                {drafts
                  .filter((d) => d.ip_id === ruleModal.ip_id)
                  .map((d) => (
                    <label className="check-row" key={d.id}>
                      <input
                        type="checkbox"
                        checked={ruleModal.evidence_ids.includes(d.id)}
                        onChange={(e) =>
                          setRuleModal({
                            ...ruleModal,
                            evidence_ids: e.target.checked
                              ? [...ruleModal.evidence_ids, d.id]
                              : ruleModal.evidence_ids.filter(
                                  (id: string) => id !== d.id,
                                ),
                          })
                        }
                      />
                      {d.title} · {d.id.slice(0, 8)}
                    </label>
                  ))}
              </>
            )}
            <button className="primary">
              {ruleModal.id ? "人工采纳规则" : "保存候选"}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
