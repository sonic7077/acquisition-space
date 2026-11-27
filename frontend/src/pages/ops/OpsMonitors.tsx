import { useEffect, useState } from "react";
import { RefreshCw, Play, EyeOff } from "lucide-react";
import { api, post, type Row } from "../../api";
import { Badge, Card, Empty, Modal, Notice } from "../../components/ui";
import { type Ctx } from "../../context";

export default function OpsMonitors(c: Ctx) {
  const [monitors, setMonitors] = useState<Row[]>([]),
    [accounts, setAccounts] = useState<Row[]>([]),
    [flowRows, setFlowRows] = useState<Row[]>([]),
    [punishRows, setPunishRows] = useState<Row[]>([]),
    [runTarget, setRunTarget] = useState<Row | null>(null),
    [hideTarget, setHideTarget] = useState<Row | null>(null),
    [notice, setNotice] = useState("");

  const load = () => {
    api<Row>("/ops/monitors")
      .then((d) => {
        setMonitors(d.list || []);
        if (d.notice) setNotice(d.notice);
      })
      .catch(() => {});
    api<Row>("/ops/monitors/flow-report")
      .then((d) => setFlowRows(d.list || []))
      .catch(() => {});
    api<Row>("/ops/punish-feeds")
      .then((d) => setPunishRows(d.list || []))
      .catch(() => {});
    api<Row[]>("/ops/accounts").then(setAccounts).catch(() => {});
  };

  useEffect(() => {
    load();
  }, [c.version]);

  const abnormalAccounts = accounts.filter(
    (a) => a.login_status !== "登录有效" || (a.verify_summary?.abnormalPostCount || 0) > 0,
  );

  return (
    <>
      <Notice>
        {notice ||
          "监控告警中心聚合账号异常、渠道推广器监控任务、作品流速报告与视频号违规作品检查；违规作品支持隐藏为仅自己可见，删除作品功能本期「暂不开放」。"}
      </Notice>

      <Card
        title={`账号异常与待办 · ${abnormalAccounts.length}`}
        action={
          <button onClick={load}>
            <RefreshCw size={15} />
            刷新监控
          </button>
        }
      >
        {abnormalAccounts.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>账号</th>
                  <th>平台</th>
                  <th>登录状态</th>
                  <th>异常作品数</th>
                  <th>建议处理</th>
                </tr>
              </thead>
              <tbody>
                {abnormalAccounts.map((a) => (
                  <tr key={a.id}>
                    <td>{a.nickname}</td>
                    <td>{a.platform_name}</td>
                    <td>
                      <Badge value={a.login_status} />
                    </td>
                    <td>{a.verify_summary?.abnormalPostCount ?? 0}</td>
                    <td>
                      <button className="text-button" onClick={() => c.go("ops-accounts")}>
                        前往账号与授权处理
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="全部已绑定账号健康" body="未发现登录失效或异常视频号作品告警。" />
        )}
      </Card>

      <Card title={`渠道推广器监控任务 · ${monitors.length}`}>
        {monitors.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>任务名称</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>最近执行</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {monitors.map((m) => (
                  <tr key={m.id}>
                    <td>{m.name || `任务 #${m.id}`}</td>
                    <td>{m.type || "—"}</td>
                    <td>
                      <Badge value={m.enabled ? "online" : "offline"} />
                    </td>
                    <td>{m.lastRunTime || "—"}</td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          onClick={() =>
                            c.run(async () => {
                              await post(`/ops/monitors/${m.id}/toggle`, { enabled: !m.enabled });
                              load();
                            }, "监控任务状态已切换")
                          }
                        >
                          {m.enabled ? "停用" : "启用"}
                        </button>
                        <button className="text-button" onClick={() => setRunTarget(m)}>
                          <Play size={14} />
                          立即执行一次
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无渠道推广器监控任务" body="如需启停监控任务，请先在渠道推广器桌面端建立基础监控配置。" />
        )}
      </Card>

      <Card title={`流速监控报告 · ${flowRows.length}`}>
        {flowRows.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>作品</th>
                  <th>平台</th>
                  <th>当前播放</th>
                  <th>近期增量</th>
                </tr>
              </thead>
              <tbody>
                {flowRows.map((r, i) => (
                  <tr key={r.postId || i}>
                    <td>{r.title || r.postId || "—"}</td>
                    <td>{r.platform || "—"}</td>
                    <td>{r.views ?? r.view ?? "—"}</td>
                    <td>{r.delta ?? r.increment ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无流速报告数据" body="渠道推广器暂未配置或采集作品流速样本。" />
        )}
      </Card>

      <Card title={`视频号异常/违规作品 · ${punishRows.length}`}>
        {punishRows.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>作品标识 / 描述</th>
                  <th>账号</th>
                  <th>异常原因</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {punishRows.map((p, i) => (
                  <tr key={p.exportId || i}>
                    <td>{p.title || p.exportId || "—"}</td>
                    <td>{p.nickname || p.uid || "—"}</td>
                    <td>{p.reason || p.abnormalReason || "—"}</td>
                    <td>
                      <div className="button-row">
                        <button
                          className="text-button"
                          disabled={!p.account_id || !p.exportId}
                          onClick={() => setHideTarget(p)}
                        >
                          <EyeOff size={14} />
                          设为仅自己可见
                        </button>
                        <button
                          className="text-button"
                          disabled
                          title="暂不开放，当前版本不支持线上删稿"
                        >
                          删除作品（暂不开放）
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无异常违规作品" body="视频号账号诊断后若存在违规作品，将在这里列出并支持设为仅自己可见。" />
        )}
      </Card>

      {runTarget && (
        <Modal title={`立即执行监控任务 · ${runTarget.name || `#${runTarget.id}`}`} onClose={() => setRunTarget(null)}>
          <Notice>若该任务属于自动评论/自动私信类监控，立即执行将真实触发对应动作，请确认后再运行。</Notice>
          <div className="button-row">
            <button
              className="primary"
              onClick={() =>
                c.run(async () => {
                  await post(`/ops/monitors/${runTarget.id}/run`, { confirm: true });
                  setRunTarget(null);
                  load();
                }, "已触发执行监控任务")
              }
            >
              确认立即执行
            </button>
          </div>
        </Modal>
      )}

      {hideTarget && (
        <Modal title="隐藏异常作品（设为仅自己可见）" onClose={() => setHideTarget(null)}>
          <Notice>将把视频号异常作品 {hideTarget.exportId} 设为仅自己可见，不会永久删除作品。</Notice>
          <div className="button-row">
            <button
              className="primary"
              onClick={() =>
                c.run(async () => {
                  await post("/ops/punish-feeds/hide", {
                    account_id: hideTarget.account_id,
                    export_id: String(hideTarget.exportId),
                    confirm: true,
                  });
                  setHideTarget(null);
                  load();
                }, "已将异常作品设为仅自己可见")
              }
            >
              确认设为仅自己可见
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}