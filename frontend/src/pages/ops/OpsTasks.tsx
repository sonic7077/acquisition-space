import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Send,
  RefreshCw,
  Rocket,
  FileEdit,
  Video,
  RotateCw,
  AlertCircle,
  CheckCircle2,
  Clock,
} from "lucide-react";
import { api, post, type Row } from "../../api";
import { Badge, Card, Empty, Field, Modal, Notice, fmt } from "../../components/ui";
import { type Ctx, filterIp, useRows } from "../../context";

const PLATFORM_CODES: Record<string, string> = {
  抖音: "Douyin",
  小红书: "XiaoHongShu",
  视频号: "Channels",
  哔哩哔哩: "Bili",
  B站: "Bili",
  公众号: "GongZhongHao",
};

const FAILED_STATES = ["REJECTED", "RETRYABLE_FAILED", "MISSED"];
const VERIFYING_STATES = ["SUBMITTING", "QUEUED", "ACCEPTED", "PUBLISHING", "REVIEWING", "UNKNOWN"];

function isVerifying(t: Row) {
  return (
    VERIFYING_STATES.includes(t.state) ||
    (t.verification_status === "verifying" && t.platform !== "GongZhongHao")
  );
}

type FeedbackState = {
  kind: "success" | "error" | "info";
  title: string;
  message: string;
  task?: Row;
} | null;

export default function OpsTasks(c: Ctx & { initialScheduleId?: string }) {
  const schedules = filterIp(useRows("/schedules", c.version), c.ip),
    [tasks, setTasks] = useState<Row[]>([]),
    [accounts, setAccounts] = useState<Row[]>([]),
    [dispatchModal, setDispatchModal] = useState(Boolean(c.initialScheduleId)),
    [selectedScheduleId, setSelectedScheduleId] = useState(c.initialScheduleId || ""),
    [promoteTarget, setPromoteTarget] = useState<Row | null>(null),
    [wechatModal, setWechatModal] = useState<Row | null>(null),
    [feedback, setFeedback] = useState<FeedbackState>(null),
    [reconciling, setReconciling] = useState(false);

  const prevStatesRef = useRef<Record<string, string>>({});
  const reconcilingRef = useRef(false);

  const reportAttemptResult = (res: Row | null) => {
    if (!res) return;
    if (res.state === "PUBLISHED") {
      setFeedback({
        kind: "success",
        title: "发布已确认成功",
        message: res.evidence_url
          ? "已向平台查询确认发布成功，发表链接已回填。"
          : `已向平台查询确认发布成功（作品ID：${res.platform_post_id || "—"}）。`,
        task: res,
      });
      c.notify("已查询确认：发布成功");
    } else if (FAILED_STATES.includes(res.state)) {
      setFeedback({
        kind: "error",
        title: "发布失败",
        message:
          res.error_message ||
          res.verification_detail ||
          "平台拒绝发布，请修改文字或视频后再次发布。",
        task: res,
      });
    } else if (res.state === "DRAFT_PUSHED") {
      const confirmed = res.verification_status === "confirmed_success";
      setFeedback({
        kind: confirmed ? "success" : "info",
        title: confirmed ? "草稿箱推送已确认成功" : "已推送到草稿箱（待回读确认）",
        message: res.verification_detail || `草稿ID: ${res.vendor_draft_id || "—"}`,
        task: res,
      });
      if (confirmed) c.notify("已回读确认草稿箱保存成功");
    } else if (res.state === "PLATFORM_SCHEDULED" && res.verification_status === "confirmed_success") {
      setFeedback({
        kind: "success",
        title: "定时发布任务已确认创建",
        message: res.verification_detail || "已进入平台定时队列，等待到点发布。",
        task: res,
      });
      c.notify("已确认平台定时任务创建成功");
    } else {
      setFeedback({
        kind: "info",
        title: "已提交，正在查询确认平台结果",
        message:
          res.verification_detail ||
          "正在上传视频并等待平台答复，页面将每 3 秒自动更新状态…",
        task: res,
      });
    }
  };

  const syncPrevStates = (rows: Row[]) => {
    const next: Record<string, string> = {};
    for (const r of rows) next[r.id] = r.state;
    prevStatesRef.current = next;
  };

  const load = () => {
    const ipQuery = c.ip ? `?ip_id=${encodeURIComponent(c.ip)}` : "";
    api<Row[]>(`/ops/publish-tasks${ipQuery}`)
      .then((rows) => {
        syncPrevStates(rows);
        setTasks(rows);
      })
      .catch(() => {});
    api<Row[]>(`/ops/accounts${ipQuery}`)
      .then(setAccounts)
      .catch(() => {});
  };

  useEffect(() => {
    load();
  }, [c.version, c.ip]);

  useEffect(() => {
    if (c.initialScheduleId) {
      setSelectedScheduleId(c.initialScheduleId);
      setDispatchModal(true);
    }
  }, [c.initialScheduleId]);

  const verifyingTasks = tasks.filter(isVerifying);
  const verifyingCount = verifyingTasks.length;

  const reconcileActive = async (manual = false) => {
    if (reconcilingRef.current) return;
    reconcilingRef.current = true;
    if (manual) setReconciling(true);
    try {
      const ipQuery = c.ip ? `?ip_id=${encodeURIComponent(c.ip)}` : "";
      const data = (await post(
        `/ops/publish-tasks/reconcile-active${ipQuery}`,
        {},
      )) as { reconciled: number; items: Row[] };
      const items = data.items || [];
      for (const item of items) {
        const prev = prevStatesRef.current[item.id];
        if (prev && prev !== item.state) {
          if (item.state === "PUBLISHED" || FAILED_STATES.includes(item.state)) {
            reportAttemptResult(item);
          }
        }
      }
      syncPrevStates(items);
      setTasks(items);
    } catch {
      // Ignore transient poll errors; manual button or next poll will retry
    } finally {
      reconcilingRef.current = false;
      if (manual) setReconciling(false);
    }
  };

  useEffect(() => {
    if (verifyingCount === 0) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        reconcileActive(false);
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [verifyingCount, c.ip]);

  async function dispatch(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const scheduleId = String(f.get("schedule_id") || "");
    await c.run(async () => {
      const res = await post(`/ops/schedules/${scheduleId}/dispatch`, {
        account_id: f.get("account_id"),
        mode: f.get("mode"),
        schedule_owner: f.get("schedule_owner"),
      });
      setDispatchModal(false);
      reportAttemptResult(res);
      load();
      return res;
    });
  }

  async function promoteDraft(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!promoteTarget) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      const res = await post(`/ops/publish-tasks/${promoteTarget.id}/publish-draft`, {
        confirm: true,
        schedule_owner: f.get("schedule_owner") || "server",
      });
      setPromoteTarget(null);
      reportAttemptResult(res);
      load();
      return res;
    });
  }

  async function republish(t: Row) {
    await c.run(async () => {
      const res = await post(`/ops/publish-tasks/${t.id}/republish`, {});
      reportAttemptResult(res);
      load();
      return res;
    });
  }

  async function confirmWechat(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!wechatModal) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      await post(`/ops/publish-tasks/${wechatModal.id}/confirm-published`, {
        evidence_url: f.get("evidence_url"),
      });
      setWechatModal(null);
      load();
    }, "已核验并登记公众号公开发表链接");
  }

  const readySchedules = schedules.filter((s) => !["cancelled", "needs_review"].includes(s.status));
  const selectedSchedule = readySchedules.find((s) => s.id === selectedScheduleId);
  const platformAccounts = selectedSchedule ? accounts.filter(
    (a) => a.platform === PLATFORM_CODES[selectedSchedule.platform] &&
      (!a.ip_id || !selectedSchedule.ip_id || a.ip_id === selectedSchedule.ip_id),
  ) : [];

  return (
    <>
      <Notice>
        统一发布任务中心：抖音/小红书/视频号/B站走渠道推广器 MCP。提交发布或推草稿箱后，系统会自动向平台查询确认真实结果并反馈失败原因；发布失败可直接修改文字/视频并再次发布。结果未知（UNKNOWN）禁止直接重试，须先核对状态。
      </Notice>

      {feedback && (
        <div role="status" className={`ops-feedback-banner ${feedback.kind}`}>
          <div className="ops-feedback-main">
            {feedback.kind === "success" ? (
              <CheckCircle2 size={18} />
            ) : feedback.kind === "error" ? (
              <AlertCircle size={18} />
            ) : (
              <Clock size={18} />
            )}
            <div>
              <strong>{feedback.title}</strong>
              <p>{feedback.message}</p>
            </div>
          </div>
          <div className="button-row">
            {feedback.kind === "error" && feedback.task && (
              <>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => c.go("drafts", feedback.task!.draft_id)}
                >
                  <FileEdit size={14} />
                  修改文字
                </button>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => c.go("assets", feedback.task!.draft_id)}
                >
                  <Video size={14} />
                  修改视频
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() => republish(feedback.task!)}
                >
                  <RotateCw size={14} />
                  再次发布
                </button>
              </>
            )}
            <button type="button" className="text-button" onClick={() => setFeedback(null)}>
              关闭
            </button>
          </div>
        </div>
      )}

      <Card
        title={`发布任务与草稿记录 · ${tasks.length}`}
        action={
          <div className="button-row">
            <button onClick={load}>
              <RefreshCw size={15} />
              刷新
            </button>
            <button
              className="primary"
              onClick={() => {
                setSelectedScheduleId("");
                setDispatchModal(true);
              }}
            >
              <Send size={15} />
              派发排期到执行节点
            </button>
          </div>
        }
      >
        {verifyingCount > 0 && (
          <div className="ops-live-verify-bar">
            <span>
              正在自动查询确认 {verifyingCount} 条任务的平台结果（每 3 秒自动刷新）
            </span>
            <button
              type="button"
              className="text-button"
              disabled={reconciling}
              onClick={() => reconcileActive(true)}
            >
              {reconciling ? "核对中…" : "立即全部核对"}
            </button>
          </div>
        )}
        {tasks.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>内容 / 排期</th>
                  <th>平台 / 账号</th>
                  <th>模式 / 计划时间</th>
                  <th>执行状态</th>
                  <th>凭证 / 说明</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => {
                  const isFailed = FAILED_STATES.includes(t.state);
                  const verifying = isVerifying(t);
                  return (
                    <tr key={t.id}>
                      <td>
                        <strong>{t.title || t.draft_id}</strong>
                        <small>v{t.draft_version} · 尝试 #{t.attempt_no}</small>
                      </td>
                      <td>
                        {t.platform} · {t.account_nickname || t.account_id}
                      </td>
                      <td>
                        <div>{t.execution_mode === "draft" ? "推草稿箱" : "正式发布"}</div>
                        <small>{fmt(t.planned_at_utc)}</small>
                      </td>
                      <td>
                        <div className="exec-status-cell">
                          <Badge value={t.state} />
                          {verifying ? (
                            <small className="ops-verify-tag verifying">上传/确认中…</small>
                          ) : t.verification_status === "confirmed_success" ? (
                            <small className="ops-verify-tag ok">
                              已查询确认{t.last_verified_at ? ` · ${fmt(t.last_verified_at)}` : ""}
                            </small>
                          ) : t.verification_status === "confirmed_failed" ? (
                            <small className="ops-verify-tag fail">
                              平台确认失败{t.last_verified_at ? ` · ${fmt(t.last_verified_at)}` : ""}
                            </small>
                          ) : t.verification_status === "unverified" ? (
                            <small className="ops-verify-tag">待核对确认</small>
                          ) : null}
                        </div>
                      </td>
                      <td>
                        {isFailed ? (
                          <div>
                            <div className="ops-task-error">
                              {t.error_message || t.verification_detail || "发布失败"}
                            </div>
                            {t.vendor_draft_id && (
                              <small className="muted">草稿ID: {t.vendor_draft_id}</small>
                            )}
                          </div>
                        ) : t.evidence_url ? (
                          <div>
                            <a href={t.evidence_url} target="_blank" rel="noreferrer">
                              查看发表链接
                            </a>
                            {t.verification_detail && (
                              <small className="muted" style={{ display: "block" }}>
                                {t.verification_detail}
                              </small>
                            )}
                          </div>
                        ) : t.state === "PUBLISHED" ? (
                          <div>
                            <small>
                              {t.platform_post_id ? `作品ID: ${t.platform_post_id}` : "已查询确认发布成功"}
                            </small>
                            {t.verification_detail && (
                              <small className="muted" style={{ display: "block" }}>
                                {t.verification_detail}
                              </small>
                            )}
                          </div>
                        ) : t.vendor_draft_id ? (
                          <div>
                            <small>草稿ID: {t.vendor_draft_id}</small>
                            {t.verification_detail && (
                              <small className="muted" style={{ display: "block" }}>
                                {t.verification_detail}
                              </small>
                            )}
                          </div>
                        ) : (
                          <small className="muted">
                            {t.verification_detail || t.error_message || "—"}
                          </small>
                        )}
                      </td>
                      <td>
                        <div className="button-row">
                          {t.state === "DRAFT_PUSHED" && t.platform !== "GongZhongHao" && t.vendor_draft_id && (
                            <button className="text-button" onClick={() => setPromoteTarget(t)}>
                              <Rocket size={14} />
                              一键转正式发布
                            </button>
                          )}
                          {isFailed && (
                            <>
                              <button
                                className="text-button"
                                onClick={() => c.go("drafts", t.draft_id)}
                              >
                                <FileEdit size={14} />
                                修改文字
                              </button>
                              <button
                                className="text-button"
                                onClick={() => c.go("assets", t.draft_id)}
                              >
                                <Video size={14} />
                                修改视频
                              </button>
                              <button
                                className="text-button primary-text"
                                onClick={() => republish(t)}
                              >
                                <RotateCw size={14} />
                                再次发布
                              </button>
                            </>
                          )}
                          {t.state === "WAITING_CLIENT_ONLINE" && (
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(async () => {
                                  const res = await post(`/ops/publish-tasks/${t.id}/retry`, {});
                                  reportAttemptResult(res);
                                  load();
                                  return res;
                                })
                              }
                            >
                              安全重试
                            </button>
                          )}
                          {["ACCEPTED", "PUBLISHING", "REVIEWING", "PLATFORM_SCHEDULED", "UNKNOWN", "DRAFT_PUSHED"].includes(t.state) &&
                            t.platform !== "GongZhongHao" && (
                              <button
                                className="text-button"
                                onClick={() =>
                                  c.run(async () => {
                                    const res = await post(`/ops/publish-tasks/${t.id}/reconcile`, {});
                                    reportAttemptResult(res);
                                    load();
                                    return res;
                                  })
                                }
                              >
                                核对状态
                              </button>
                            )}
                          {t.state === "DRAFT_PUSHED" && t.platform === "GongZhongHao" && (
                            <button className="text-button" onClick={() => setWechatModal(t)}>
                              确认群发链接
                            </button>
                          )}
                          {!["PUBLISHED", "CANCELLED", "REJECTED"].includes(t.state) && (
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(async () => {
                                  await post(`/ops/publish-tasks/${t.id}/cancel`, {});
                                  load();
                                }, "任务已取消")
                              }
                            >
                              取消
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无执行中的发布任务" body="可先在「内容生产 → 发布排期」创建排期，再在此页面选择目标平台账号派发到草稿箱或正式队列。" />
        )}
      </Card>

      {dispatchModal && (
        <Modal title="派发排期到执行节点" onClose={() => setDispatchModal(false)}>
          <form onSubmit={dispatch}>
            <Field label="选择已有排期">
              <select
                name="schedule_id"
                value={selectedScheduleId}
                onChange={(e) => setSelectedScheduleId(e.target.value)}
                required
              >
                <option value="">请选择排期</option>
                {readySchedules.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title} · {s.platform} · {fmt(s.planned_at)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="目标平台账号">
              <select key={selectedScheduleId} name="account_id" required disabled={!platformAccounts.length}>
                <option value="">{selectedSchedule && !platformAccounts.length
                  ? `暂无可用的${selectedSchedule.platform}账号`
                  : "请选择平台账号"}</option>
                {platformAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.platform_name} · {a.nickname} ({a.login_status})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="执行动作">
              <select name="mode" defaultValue="draft">
                <option value="draft">仅推送到草稿箱（安全联调/预审推荐）</option>
                <option value="publish">正式创建发布任务</option>
              </select>
            </Field>
            <Field label="定时调度归属">
              <select name="schedule_owner" defaultValue="server">
                <option value="server">获客空间服务器到点触发</option>
                <option value="platform">提交为平台原生定时（适合前端关机场景）</option>
              </select>
            </Field>
            <Notice>系统会自动校验稿件批准状态、视频 SHA-256 哈希与每账号每日最多 3 条上限；提交后将自动核对平台结果。</Notice>
            <button className="primary" disabled={!platformAccounts.length}>提交执行</button>
          </form>
        </Modal>
      )}

      {promoteTarget && (
        <Modal title={`草稿转正式发布 · ${promoteTarget.title || promoteTarget.draft_id}`} onClose={() => setPromoteTarget(null)}>
          <Notice>
            将调用渠道推广器把草稿 #{promoteTarget.vendor_draft_id} 转为正式发布任务（账号：{promoteTarget.account_nickname}）。提交后会自动查询确认平台结果。
          </Notice>
          <form onSubmit={promoteDraft}>
            <Field label="定时调度归属">
              <select name="schedule_owner" defaultValue={promoteTarget.schedule_owner || "server"}>
                <option value="server">立即进入队列 / 服务端触发</option>
                <option value="platform">提交为平台原生定时</option>
              </select>
            </Field>
            <button className="primary">确认转正式发布</button>
          </form>
        </Modal>
      )}

      {wechatModal && (
        <Modal title="登记公众号群发公开链接" onClose={() => setWechatModal(null)}>
          <form onSubmit={confirmWechat}>
            <Field label="公众号文章公开 URL">
              <input name="evidence_url" type="url" required placeholder="https://mp.weixin.qq.com/s/..." />
            </Field>
            <button className="primary">确认已公开发表</button>
          </form>
        </Modal>
      )}
    </>
  );
}
