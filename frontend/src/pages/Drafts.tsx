import { useEffect, useRef, useState, type FormEvent } from "react";
import { Search, Plus } from "lucide-react";
import { post, patch, type Row } from "../api";
import { Badge, Empty, Card, Modal, Field, Notice } from "../components/ui";
import { type Ctx, useRows, filterIp, IpSelect, verifiedKnowledge } from "../context";
import Teleprompter from "./Teleprompter";
import RewriteDialog from "./RewriteDialog";
import MaterialWriterDialog from "./MaterialWriterDialog";
import { LiveReviewChecks, ReviewChecks } from "./ReviewChecks";

export default function Drafts(c: Ctx & { initialDraftId?: string }) {
  const rows = filterIp(useRows("/drafts", c.version), c.ip),
    skills = useRows("/skills", c.version),
    knowledge = useRows("/knowledge", c.version),
    [selected, setSelected] = useState(c.initialDraftId || ""),
    [q, setQ] = useState(""),
    [edit, setEdit] = useState<Row | null>(null),
    [focusFacts, setFocusFacts] = useState(false),
    [target, setTarget] = useState(c.ip),
    [tele, setTele] = useState(false),
    [rewrite,setRewrite] = useState<Row|null>(null),
    [analysis,setAnalysis] = useState<Row|null>(null);
  useEffect(() => {
    if (c.initialDraftId) setSelected(c.initialDraftId);
  }, [c.initialDraftId]);
  const current = rows.find((d) => d.id === selected) || rows[0];
  const demand = current?.demand_snapshot;
  const editBaseline = useRef<Row | null>(null);
  const canVerifyFacts = c.me.role !== "member";
  const availableKnowledge = verifiedKnowledge(knowledge);

  function openEditor(draft: Row | null, focusFactStatus = false) {
    editBaseline.current = draft;
    setTarget(draft?.ip_id || c.ip);
    setFocusFacts(focusFactStatus);
    setEdit(draft || {});
  }

  function hasSource(draft: Row, id: string) {
    return draft.source_ids?.includes(id) || draft.sources?.some((source: Row) => source.id === id);
  }

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const fields = Object.fromEntries(form);
    const sourceIds = form.getAll("source_ids");
    const baseline = editBaseline.current;
    const sourcesChanged = !baseline || availableKnowledge.some((k) =>
      Boolean(hasSource(baseline, k.id)) !== sourceIds.includes(k.id),
    );
    if (baseline) {
      const factsChanged = sourcesChanged || fields.title !== baseline.title || fields.body !== baseline.body;
      if (!factsChanged && edit?.fact_status === baseline.fact_status) delete fields.fact_status;
      if (fields.reference === (baseline.reference ?? "")) delete fields.reference;
    }
    const data = {
      ...fields,
      ...(sourcesChanged ? { source_ids: sourceIds } : {}),
    };
    await c.run(async () => {
      let r;
      if (edit?.id)
        r = await patch("/drafts/" + edit.id, {
          ...data,
          version: edit.version,
        });
      else r = await post("/drafts", { ...data, ip_id: target });
      setSelected(r.id);
      setEdit(null);
    }, "稿件已保存，需审核当前版本");
  }
  return (
    <>
      <div className="section-actions">
        <Notice>
          所有批准都绑定当前版本。修改后自动撤销旧批准，并重新检查关联排期。
        </Notice>
        <button
          className="primary"
          onClick={() => openEditor(null)}
        >
          <Plus size={16} />
          导入 / 新建稿件
        </button>
      </div>
      <div className="review-layout">
        <Card title={`稿件 · ${rows.length}`} className="draft-list">
          <div className="search-box">
            <Search size={15} />
            <input
              placeholder="搜索标题"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          {rows
            .filter((d) => d.title?.includes(q))
            .map((d) => (
              <button
                className={
                  "draft-item " + (current?.id === d.id ? "selected" : "")
                }
                key={d.id}
                onClick={() => setSelected(d.id)}
              >
                <strong>{d.title || "未命名稿件"}</strong>
                <small>
                  {d.id.slice(0, 8)} · v{d.version}
                </small>
                <Badge value={d.status} />
              </button>
            ))}
          {!rows.length && (
            <Empty title="还没有稿件" body="新建稿件，或从选题库调用写手生成待审稿。" />
          )}
        </Card>
        <Card>
          {current ? (
            <>
              <div className="draft-heading">
                <div>
                  <h2>{current.title}</h2>
                  <p>
                    {c.ips.find((i) => i.id === current.ip_id)?.name} · v
                    {current.version} · 正文{current.body?.length || 0}字
                  </p>
                </div>
                <div className="button-row">
                  <button onClick={() => openEditor(current)}>
                    编辑稿件
                  </button>
                  <button onClick={() => setRewrite(current)}>用写手改编</button><button onClick={()=>setAnalysis(current)}>用辅助写手分析</button>
                  <button onClick={() => setTele(true)}>提词器</button>
                  {c.me.role !== "member" && (
                    <>
                      <button
                        onClick={() => {
                          const reason = window.prompt("请填写退回原因");
                          if (reason)
                            c.run(
                              () =>
                                post(`/drafts/${current.id}/return`, {
                                  reason,
                                }),
                              "已退回",
                            );
                        }}
                      >
                        退回
                      </button>
                      <button
                        className="primary"
                        onClick={() =>
                          c.run(
                            () =>
                              post(`/drafts/${current.id}/approve`, {
                                version: current.version,
                              }),
                            "当前版本已批准",
                          )
                        }
                      >
                        批准通过
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="draft-meta">
                <Badge value={current.status} />
                <span>封面字：{current.cover || "未设置"}</span>
                <span>
                  事实：
                  {current.fact_status === "none"
                    ? "无具体事实声明"
                    : current.fact_status === "verified"
                      ? "已核实"
                      : "待核实"}
                </span>
              </div>
              {current.writer_snapshot&&<Notice>写手：{current.writer_snapshot.name} · v{current.writer_snapshot.version}{current.writer_snapshot.is_system?` · IP增强 v${current.writer_snapshot.enhancement_version}`:""} · 正文上限{current.word_limit}字{current.parent_draft_id?` · 来自原稿 v${current.parent_version} 的独立候选`:""}</Notice>}
              {demand && (
                <details className="draft-output-details">
                  <summary>{demand.source_kind === 'leads' ? '来源线索' : `关联需求：${demand.title}`} · 提交时快照</summary>
                  {demand.evidence_restricted ? <p className="muted">来源评论当前无访问权限。</p> : (
                    <>
                      {demand.source_kind !== 'leads' && (
                        <>
                          <p>有效需求 {demand.count ?? '手工登记'} · 对应资料 {demand.doc_name || '未指定'} · 产品 {demand.product || '未指定'}</p>
                          {demand.body && <p>{demand.body}</p>}
                        </>
                      )}
                      {demand.quotes?.slice(0, 20).map((quote: Row) => (
                        <blockquote key={quote.id}>
                          <p>{quote.body}</p>
                          <small>{quote.content_type} · {quote.intent} · {quote.work_title || '来源作品未记录'}</small>
                        </blockquote>
                      ))}
                    </>
                  )}
                </details>
              )}
              {current.knowledge_snapshot&&<details className="draft-output-details"><summary>本次知识与规则快照 · {current.knowledge_snapshot.count} 条有效知识</summary><p>知识指纹 {current.knowledge_snapshot.fingerprint.slice(0,16)} · 实际引用 {current.sources?.length||0} 条</p>{current.sources?.map((s:Row)=><p key={s.id}>{s.title} · {s.hash.slice(0,8)}</p>)}{current.writer_snapshot?.rules?.map((r:Row)=><p key={r.id}>{r.title}：{r.after}</p>)}</details>}
              {current.source_evidence_restricted&&<Notice>部分来源素材或评论当前无访问权限，原始证据快照已隐藏。</Notice>}
              <article className="script-body">
                {current.body || "稿件尚无正文，请编辑或使用IP与写手生成内容。"}
              </article>
              {current.writer_snapshot&&<details className="draft-output-details"><summary>发布信息、评论洞察与事实核查</summary><p>发布文案：{current.publish_copy||"未提供"}</p><p>标签：{current.tags?.join(" / ")||"未提供"}</p><h4>评论洞察</h4><p>{current.comment_insights||"无"}</p><h4>事实核查（仍需人工核实）</h4><p>{current.fact_check||"未提供，需人工核实"}</p>{current.extras&&<p>{current.extras}</p>}<details><summary>本次写手SKILL快照</summary><pre className="writer-history">{current.writer_snapshot.skill_text}</pre></details></details>}
              {current.return_reason && (
                <p className="error-text">退回原因：{current.return_reason}</p>
              )}
              <ReviewChecks checks={current.checks} onReviewFacts={canVerifyFacts ? () => openEditor(current, true) : undefined} />
              <div className="draft-history">
                <h3>历史版本 · {current.history?.length || 0}</h3>
                {current.history?.map((h: Row) => (
                  <details key={h.version}>
                    <summary>
                      v{h.version} · {h.title}
                    </summary>
                    <p>{h.body}</p>
                  </details>
                ))}
              </div>
            </>
          ) : (
            <Empty
              title="选择一条稿件开始审核"
              body="正文、事实、来源重合度与批准集中处理。"
            />
          )}
        </Card>
      </div>
      {analysis&&<MaterialWriterDialog c={c} material={analysis} sourceKind="drafts" onClose={()=>setAnalysis(null)}/>}
      {edit && (
        <Modal
          title={edit.id ? "编辑稿件" : "新建稿件"}
          onClose={() => setEdit(null)}
        >
          <form onSubmit={save}>
            <div className="form-grid">
              <Field label="目标IP">
                <IpSelect ips={c.ips} value={target} onChange={setTarget} />
              </Field>
              <Field label="文体">
                <select
                  name="skill"
                  aria-label="文体"
                  value={edit.skill || "original-video-writer"}
                  onChange={e=>setEdit({...edit,skill:e.target.value})}
                  disabled={!!edit.id}
                >
                  {skills.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="标题">
              <input name="title" required maxLength={2000} value={edit.title ?? ""}
                onChange={event => setEdit({ ...edit, title: event.target.value, fact_status: "pending" })} />
            </Field>
            <Field label="封面字">
              <input name="cover" defaultValue={edit.cover} />
            </Field>
            <Field label="正文">
              <textarea
                name="body"
                aria-label="正文"
                rows={10}
                required
                maxLength={20000}
                value={edit.body ?? ""}
                onChange={event => setEdit({ ...edit, body: event.target.value, fact_status: "pending" })}
              />
            </Field>
            {!edit.reference_restricted && <Field label="对标逐字稿（复刻必填）">
              <textarea
                name="reference"
                aria-label="对标逐字稿（复刻必填）"
                rows={4}
                maxLength={20000}
                value={edit.reference ?? ""}
                onChange={event => setEdit({ ...edit, reference: event.target.value })}
              />
            </Field>}
            <fieldset className="field">
              <legend>引用的共享知识（用于变更追踪）</legend>
              {availableKnowledge
                .map((k) => (
                  <label className="check-row" key={k.id}>
                    <input
                      type="checkbox"
                      name="source_ids"
                      value={k.id}
                    defaultChecked={hasSource(edit, k.id)}
                    onChange={() => setEdit({ ...edit, fact_status: "pending" })}
                    />
                    {k.title}
                  </label>
                ))}
              <span className="muted">
                模型稿自动保留来源。人工稿请明确登记引用，便于资料变更时复核。
              </span>
            </fieldset>
            <Field label="事实核验状态">
              <select
                name="fact_status"
                autoFocus={focusFacts}
                aria-describedby="draft-fact-guidance"
                value={edit.fact_status || "pending"}
                onChange={event => setEdit({ ...edit, fact_status: event.target.value })}
              >
                <option value="pending">包含待核实的具体事实</option>
                <option value="none">人工确认无具体事实声明</option>
                {(canVerifyFacts || edit.fact_status === "verified") && (
                  <option value="verified" disabled={!canVerifyFacts}>已核实具体事实（审核人确认）</option>
                )}
              </select>
            </Field>
            <p className="muted" id="draft-fact-guidance">
              {canVerifyFacts
                ? "对照有效资料核实稿件中的产品责任、数字和承诺，再选择相应状态。保存新版本后，返回审稿点击「批准通过」。"
                : "新增或改动具体事实后，请由团队长或管理员核验并批准。"}
            </p>
            <LiveReviewChecks data={{
              draft_id: edit.id,
              version: edit.version,
              ip_id: target || undefined,
              skill: edit.skill || "original-video-writer",
              title: edit.title ?? "",
              body: edit.body ?? "",
              reference: edit.reference_restricted ? undefined : edit.reference,
              fact_status: edit.fact_status || "pending",
            }} />
            <button className="primary">保存新版本</button>
          </form>
        </Modal>
      )}
      {rewrite&&<RewriteDialog c={c} draft={rewrite} onClose={()=>setRewrite(null)} onCandidate={id=>{setSelected(id);setRewrite(null)}}/>}
      {tele && current && (
        <Teleprompter draft={current} onClose={() => setTele(false)} />
      )}
    </>
  );
}
