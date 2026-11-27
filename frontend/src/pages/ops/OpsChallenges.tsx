import { useEffect, useState } from "react";
import { RefreshCw, QrCode, CheckCircle2, XCircle } from "lucide-react";
import { api, post, type Row } from "../../api";
import { Badge, Card, Empty, Modal, Notice, fmt } from "../../components/ui";
import { type Ctx } from "../../context";

export default function OpsChallenges(c: Ctx) {
  const [challenges, setChallenges] = useState<Row[]>([]),
    [offlineTasks, setOfflineTasks] = useState<Row[]>([]),
    [qrItem, setQrItem] = useState<Row | null>(null);

  const load = () => {
    api<Row[]>("/ops/auth-challenges").then(setChallenges).catch(() => {});
    api<Row[]>("/ops/publish-tasks")
      .then((rows) => setOfflineTasks(rows.filter((r) => r.state === "WAITING_CLIENT_ONLINE")))
      .catch(() => {});
  };

  useEffect(() => {
    load();
  }, [c.version]);

  return (
    <>
      <Notice>
        授权中心集中处理视频号云端扫脸验证二维码、Web 上号待办，以及「Web前端+本机渠道推广器」离线挂起任务的上线续跑；无需使用的待办可直接取消并回收登录链接。
      </Notice>

      {offlineTasks.length > 0 && (
        <Card title={`等待本机上线续跑的任务 · ${offlineTasks.length}`} subtitle="操作者电脑开机并启动本机渠道推广器代理后，可一键继续执行">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>内容标题</th>
                  <th>平台 / 账号</th>
                  <th>计划时间</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {offlineTasks.map((t) => (
                  <tr key={t.id}>
                    <td>{t.title || t.draft_id}</td>
                    <td>
                      {t.platform} · {t.account_nickname}
                    </td>
                    <td>{fmt(t.planned_at_utc)}</td>
                    <td>
                      <Badge value={t.state} />
                    </td>
                    <td>
                      <button
                        className="primary"
                        onClick={() =>
                          c.run(async () => {
                            await post(`/ops/publish-tasks/${t.id}/retry`, {});
                            load();
                          }, "已重新派发到本机渠道推广器")
                        }
                      >
                        本机已在线 · 立即续跑
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card
        title={`授权与验证挑战 · ${challenges.length}`}
        action={
          <button onClick={load}>
            <RefreshCw size={15} />
            刷新列表
          </button>
        }
      >
        {challenges.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>类型</th>
                  <th>平台</th>
                  <th>状态</th>
                  <th>过期时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {challenges.map((ch) => (
                  <tr key={ch.id}>
                    <td>{ch.challenge_type === "channels_face_verify" ? "视频号云端扫脸" : "Web 内嵌上号"}</td>
                    <td>{ch.platform_name}</td>
                    <td>
                      <Badge value={ch.status} />
                    </td>
                    <td>{fmt(ch.expires_at)}</td>
                    <td>
                      <div className="button-row">
                        {!["CANCELLED", "EXPIRED"].includes(ch.status) && (
                          <button className="text-button" onClick={() => setQrItem(ch)}>
                            <QrCode size={14} />
                            展示二维码
                          </button>
                        )}
                        {!["VERIFIED", "CANCELLED", "EXPIRED"].includes(ch.status) && (
                          <>
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(async () => {
                                  await post(`/ops/auth-challenges/${ch.id}/complete`, {});
                                  load();
                                }, "已标记为已验证")
                              }
                            >
                              <CheckCircle2 size={14} />
                              标记完成
                            </button>
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(async () => {
                                  await post(`/ops/auth-challenges/${ch.id}/cancel`, {});
                                  load();
                                }, "已取消待办并清理关联链接")
                              }
                            >
                              <XCircle size={14} />
                              取消待办
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="暂无待处理的授权挑战" body="当触发视频号扫脸验证或新增上号时，会在这里展示对应二维码。" />
        )}
      </Card>

      {qrItem && (
        <Modal title={`${qrItem.platform_name} · 手机扫码验证`} onClose={() => setQrItem(null)}>
          <div className="ops-qr-wrap">
            <img
              src={`${qrItem.qr_endpoint}?v=${Date.now()}`}
              alt="授权验证二维码"
              className="ops-qr-img"
            />
            <p className="muted">请使用微信/对应平台 App 扫描上方二维码并在手机端完成确认；二维码仅限当前有权账号短期读取。</p>
            <button
              className="primary"
              onClick={() =>
                c.run(async () => {
                  await post(`/ops/auth-challenges/${qrItem.id}/complete`, {});
                  setQrItem(null);
                  load();
                }, "已确认完成验证")
              }
            >
              我已完成手机确认
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}