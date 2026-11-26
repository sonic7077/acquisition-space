import { useEffect, useState, type FormEvent } from "react";
import { Send, RefreshCw, Rocket } from "lucide-react";
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

export default function OpsTasks(c: Ctx & { initialScheduleId?: string }) {
  const schedules = filterIp(useRows("/schedules", c.version), c.ip),
    [tasks, setTasks] = useState<Row[]>([]),
    [accounts, setAccounts] = useState<Row[]>([]),
    [dispatchModal, setDispatchModal] = useState(Boolean(c.initialScheduleId)),
    [selectedScheduleId, setSelectedScheduleId] = useState(c.initialScheduleId || ""),
    [promoteTarget, setPromoteTarget] = useState<Row | null>(null),
    [wechatModal, setWechatModal] = useState<Row | null>(null);

  const load = () => {
    api<Row[]>(`/ops/publish-tasks${c.ip ? `?ip_id=${encodeURIComponent(c.ip)}` : ""}`)
      .then(setTasks)
      .catch(() => {});
    api<Row[]>(`/ops/accounts${c.ip ? `?ip_id=${encodeURIComponent(c.ip)}` : ""}`)
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

  async function dispatch(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const scheduleId = String(f.get("schedule_id") || "");
    await c.run(async () => {
      await post(`/ops/schedules/${scheduleId}/dispatch`, {
        account_id: f.get("account_id"),
        mode: f.get("mode"),
        schedule_owner: f.get("schedule_owner"),
      });
      setDispatchModal(false);
      load();
    }, "已提交执行动作");
  }

  async function promoteDraft(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!promoteTarget) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      await post(`/ops/publish-tasks/${promoteTarget.id}/publish-draft`, {
        confirm: true,
        schedule_owner: f.get("schedule_owner") || "server",
      });
      setPromoteTarget(null);
      load();
    }, "草稿已转为正式发布任务");
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
        统一发布任务中心：抖音/小红书/视频号/B站走小V猫 MCP（支持草稿一键转正式发布）；公众号首期仅推送到草稿箱（DRAFT_PUSHED），必须由人工群发并回填公开链接才记为已发表。结果未知（UNKNOWN）禁止直接重试，须先点「核对状态」。
      </Notice>

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
                {tasks.map((t) => (
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
                      <Badge value={t.state} />
                    </td>
                    <td>
                      {t.evidence_url ? (
                        <a href={t.evidence_url} target="_blank" rel="noreferrer">
                          查看发表链接
                        </a>
                      ) : t.vendor_draft_id ? (
                        <small>草稿ID: {t.vendor_draft_id}</small>
                      ) : (
                        <small className="muted">{t.error_message || "—"}</small>
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
                        {t.vendor_local_id && !["PUBLISHED", "CANCELLED"].includes(t.state) && (
                          <button
                            className="text-button"
                            onClick={() =>
                              c.run(async () => {
                                await post(`/ops/publish-tasks/${t.id}/reconcile`, {});
                                load();
                              }, "已从执行节点核对最新状态")
                            }
                          >
                            核对状态
                          </button>
                        )}
                        {["RETRYABLE_FAILED", "WAITING_CLIENT_ONLINE"].includes(t.state) && (
                          <button
                            className="text-button"
                            onClick={() =>
                              c.run(async () => {
                                await post(`/ops/publish-tasks/${t.id}/retry`, {});
                                load();
                              }, "已重新提交执行")
                            }
                          >
                            安全重试
                          </button>
                        )}
                        {t.state === "DRAFT_PUSHED" && t.platform === "GongZhongHao" && (
                          <button className="text-button" onClick={() => setWechatModal(t)}>
                            确认群发链接
                          </button>
                        )}
                        {!["PUBLISHED", "CANCELLED"].includes(t.state) && (
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
                ))}
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
            <Notice>系统会自动校验稿件批准状态、视频 SHA-256 哈希与每账号每日最多 3 条上限。</Notice>
            <button className="primary" disabled={!platformAccounts.length}>提交执行</button>
          </form>
        </Modal>
      )}

      {promoteTarget && (
        <Modal title={`草稿转正式发布 · ${promoteTarget.title || promoteTarget.draft_id}`} onClose={() => setPromoteTarget(null)}>
          <Notice>
            将调用小V猫把草稿 #{promoteTarget.vendor_draft_id} 转为正式发布任务（账号：{promoteTarget.account_nickname}）。系统将继续校验批准版本与每日 3 条上限。
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
