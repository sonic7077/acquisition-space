import { useEffect, useState, type FormEvent } from "react";
import { RefreshCw, Link2, ShieldCheck, QrCode, Trash2, Unlink, MessageSquarePlus } from "lucide-react";
import { api, post, type Row } from "../../api";
import { Badge, Card, Empty, Field, Modal, Notice } from "../../components/ui";
import ExecutorNodesPanel from "../../components/ExecutorNodesPanel";
import { type Ctx, filterIp } from "../../context";

const PLATFORMS = [
  ["抖音", "Douyin"],
  ["小红书", "XiaoHongShu"],
  ["视频号", "Channels"],
  ["哔哩哔哩", "Bili"],
];

export default function OpsAccounts(c: Ctx) {
  const [accounts, setAccounts] = useState<Row[]>([]),
    [nodesInfo, setNodesInfo] = useState<Row | null>(null),
    [includeRemoved, setIncludeRemoved] = useState(false),
    [loginModal, setLoginModal] = useState(false),
    [challenge, setChallenge] = useState<Row | null>(null),
    [bindTarget, setBindTarget] = useState<Row | null>(null),
    [removeTarget, setRemoveTarget] = useState<Row | null>(null),
    [remarkTarget, setRemarkTarget] = useState<Row | null>(null),
    [dupData, setDupData] = useState<Row | null>(null),
    [pairResult, setPairResult] = useState<Row | null>(null),
    [busy, setBusy] = useState(false);

  const load = (showRemoved = includeRemoved) => {
    const params = new URLSearchParams();
    if (c.ip) params.set("ip_id", c.ip);
    if (showRemoved) params.set("include_removed", "true");
    const q = params.toString() ? `?${params.toString()}` : "";
    api<Row[]>(`/ops/accounts${q}`)
      .then(setAccounts)
      .catch(() => {});
    api<Row>("/ops/nodes").then(setNodesInfo).catch(() => {});
  };

  useEffect(() => {
    load(includeRemoved);
  }, [c.version, c.ip, includeRemoved]);

  const ipName = (id: string) => c.ips.find((x) => x.id === id)?.name || "未绑定IP";

  async function createIframeLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    await c.run(async () => {
      const ch = await post("/ops/login-links", {
        platform: f.get("platform"),
        ...(f.get("node_id") ? { node_id: f.get("node_id") } : {}),
      });
      setChallenge(ch);
    }, "已获取上号页面，请在下方扫码确认");
    setBusy(false);
  }

  async function bindIp(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!bindTarget) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      await post(`/ops/accounts/${bindTarget.id}/bind`, { ip_id: f.get("ip_id") });
      setBindTarget(null);
      load();
    }, "平台账号归属已绑定");
  }

  async function submitRemoveAccount(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!removeTarget) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      await post(`/ops/accounts/${removeTarget.id}/remove`, {
        confirm: true,
        confirm_nickname: String(f.get("confirm_nickname") || ""),
        cancel_pending: f.get("cancel_pending") === "on",
      });
      setRemoveTarget(null);
      load();
    }, "已从小V猫移除该账号托管（历史记录已保留）");
  }

  async function submitRemark(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!remarkTarget) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      await post(`/ops/accounts/${remarkTarget.id}/remark`, {
        remark: String(f.get("remark") || ""),
      });
      setRemarkTarget(null);
    }, "账号备注已更新");
  }

  return (
    <>
      <Notice>
        支持「服务器联通模式」与「Web前端+本机小V猫模式」统一账号管理；新增账号通过内嵌官方登录助手完成，移除账号仅解除小V猫托管且保留历史作品与审计。
      </Notice>

      <Card
        title={`平台账号与授权 · ${accounts.length}`}
        subtitle={`当前执行器模式：${nodesInfo?.mode || "manual"} · 在线节点 ${(nodesInfo?.nodes || []).filter((n: Row) => n.status === "online").length} 个`}
        action={
          <div className="button-row">
            <label className="check-row">
              <input
                type="checkbox"
                checked={includeRemoved}
                onChange={(e) => setIncludeRemoved(e.target.checked)}
              />
              显示已移除历史账号
            </label>
            <button
              onClick={async () => {
                setBusy(true);
                await c.run(async () => {
                  const res = await post("/ops/accounts/sync", {});
                  setAccounts(res.accounts || []);
                }, "已从执行节点同步账号列表");
                setBusy(false);
              }}
              disabled={busy}
            >
              <RefreshCw size={15} />
              同步节点账号
            </button>
            <button className="primary" onClick={() => { setChallenge(null); setLoginModal(true); }}>
              <QrCode size={15} />
              Web 扫码新增账号
            </button>
            <button
              onClick={() =>
                c.run(async () => {
                  setPairResult(await post("/ops/nodes/pair-code", { node_scope: "client_local", label: `${c.me.name}的本机小V猫` }));
                }, "已生成本机小V猫配对命令")
              }
            >
              <Link2 size={15} />
              配对我的本机小V猫
            </button>
            {c.me.role === "admin" && (
              <button
                onClick={() =>
                  c.run(async () => {
                    setDupData(await api("/ops/accounts/duplicates"));
                  })
                }
              >
                账号查重
              </button>
            )}
          </div>
        }
      >
        {pairResult && (
          <div className="schema-preview">
            <strong>在你的电脑运行以下命令接入「Web 前端 + 本机小V猫模式」（30分钟内有效）：</strong>
            <pre>{pairResult.command_hint}</pre>
          </div>
        )}

        {accounts.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>账号昵称 / UID</th>
                  <th>平台</th>
                  <th>所属节点模式</th>
                  <th>绑定IP</th>
                  <th>登录状态</th>
                  <th>诊断/验证</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filterIp(accounts, "").map((a) => (
                  <tr key={a.id}>
                    <td>
                      <strong>{a.nickname}</strong>
                      <small>{a.vendor_uid}</small>
                    </td>
                    <td>{a.platform_name}</td>
                    <td>{a.node_scope || "-"}</td>
                    <td>{ipName(a.ip_id)}</td>
                    <td>
                      <Badge value={a.login_status} />
                    </td>
                    <td>
                      {a.platform === "Channels" ? (
                        <small>{a.is_verified ? "已诊断" : "未诊断"}</small>
                      ) : (
                        <small className="muted">—</small>
                      )}
                    </td>
                    <td>
                      {a.active ? (
                        <div className="button-row">
                          <button className="text-button" onClick={() => setBindTarget(a)}>
                            绑定IP
                          </button>
                          {a.ip_id && (
                            <button
                              className="text-button"
                              onClick={() =>
                                c.run(async () => {
                                  await post(`/ops/accounts/${a.id}/unbind`, {});
                                  load();
                                }, "已解除账号 IP 绑定")
                              }
                            >
                              <Unlink size={14} />
                              解绑IP
                            </button>
                          )}
                          <button className="text-button" onClick={() => setRemarkTarget(a)}>
                            <MessageSquarePlus size={14} />
                            备注
                          </button>
                          {a.platform === "Channels" && (
                            <>
                              <button
                                className="text-button"
                                onClick={() =>
                                  c.run(async () => {
                                    await post(`/ops/accounts/${a.id}/verify`, {});
                                    load();
                                  }, "已完成视频号账号诊断")
                                }
                              >
                                <ShieldCheck size={14} />
                                诊断
                              </button>
                              <button
                                className="text-button"
                                onClick={() =>
                                  c.run(async () => {
                                    await post(`/ops/accounts/${a.id}/verify-link`, {});
                                    c.go("ops-challenges");
                                  }, "已生成视频号验证二维码，请在授权中心扫码")
                                }
                              >
                                扫脸验证
                              </button>
                            </>
                          )}
                          <button className="text-button" onClick={() => setRemoveTarget(a)}>
                            <Trash2 size={14} />
                            移除账号
                          </button>
                        </div>
                      ) : (
                        <small className="muted">已移除（保留审计）</small>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无已同步的平台账号"
            body="请先在系统设置配置小V猫连接或配对本机代理，然后点击「同步节点账号」或「Web 扫码新增账号」。"
          />
        )}
      </Card>

      <ExecutorNodesPanel ctx={c} nodesInfo={nodesInfo} onChanged={setNodesInfo} />

      {loginModal && (
        <Modal title="Web 无感上号（内嵌官方登录助手）" onClose={() => setLoginModal(false)}>
          <form onSubmit={createIframeLogin} className="ops-inline-form">
            <Field label="选择平台">
              <select name="platform" defaultValue="抖音">
                {PLATFORMS.map(([cn]) => (
                  <option key={cn} value={cn}>
                    {cn}
                  </option>
                ))}
              </select>
            </Field>
            {(nodesInfo?.nodes || []).length > 0 && (
              <Field label="执行节点（可选）">
                <select name="node_id" defaultValue="">
                  <option value="">自动按系统默认模式选择</option>
                  {(nodesInfo?.nodes || []).filter((n: Row) => n.status !== "revoked").map((n: Row) => (
                    <option key={n.id} value={n.id}>
                      {n.name} ({n.node_scope})
                    </option>
                  ))}
                </select>
              </Field>
            )}
            <button className="primary" disabled={busy}>
              生成并加载登录助手
            </button>
          </form>

          {challenge?.iframe_url && (
            <div className="ops-iframe-box">
              <Notice>
                请在内嵌页面点击「获取登录二维码」并用对应平台手机 App 扫码确认；完成后点击下方「检测新入库账号」。
              </Notice>
              <iframe
                title="平台登录助手"
                src={challenge.iframe_url}
                referrerPolicy="no-referrer"
                className="ops-auth-iframe"
              />
              <div className="button-row">
                <button
                  className="primary"
                  onClick={() =>
                    c.run(async () => {
                      await post(`/ops/auth-challenges/${challenge.id}/complete`, {});
                      const res = await post("/ops/accounts/sync", { node_id: challenge.node_id });
                      setAccounts(res.accounts || []);
                      setLoginModal(false);
                    }, "已同步最新入库账号")
                  }
                >
                  我已完成扫码 · 同步新账号
                </button>
                <button
                  onClick={() =>
                    c.run(async () => {
                      await post(`/ops/auth-challenges/${challenge.id}/cancel`, {});
                      setChallenge(null);
                      setLoginModal(false);
                    }, "已取消待办并回收上号链接")
                  }
                >
                  取消并回收链接
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}

      {bindTarget && (
        <Modal title={`绑定归属IP · ${bindTarget.nickname}`} onClose={() => setBindTarget(null)}>
          <form onSubmit={bindIp}>
            <Field label="目标授权IP">
              <select name="ip_id" defaultValue={bindTarget.ip_id || ""} required>
                <option value="">请选择IP</option>
                {c.ips.map((ip) => (
                  <option key={ip.id} value={ip.id}>
                    {ip.name}
                  </option>
                ))}
              </select>
            </Field>
            <button className="primary">保存绑定</button>
          </form>
        </Modal>
      )}

      {removeTarget && (
        <Modal title={`从小V猫移除账号 · ${removeTarget.nickname}`} onClose={() => setRemoveTarget(null)}>
          <Notice>
            此操作仅从小V猫软件中移除对该账号的托管，不会注销抖音/小红书/视频号平台账号本身；但小V猫桌面端内该账号的统计与发布记录会被清除，获客空间历史作品与审计记录仍会保留。
          </Notice>
          <form onSubmit={submitRemoveAccount}>
            <Field label={`请输入账号昵称「${removeTarget.nickname}」以确认移除`}>
              <input name="confirm_nickname" required placeholder={removeTarget.nickname} />
            </Field>
            <label className="check-row">
              <input type="checkbox" name="cancel_pending" />
              若存在排队中的待发任务，同时取消后再移除
            </label>
            <button className="primary">确认从小V猫移除</button>
          </form>
        </Modal>
      )}

      {remarkTarget && (
        <Modal title={`设置账号备注 · ${remarkTarget.nickname}`} onClose={() => setRemarkTarget(null)}>
          <form onSubmit={submitRemark}>
            <Field label="备注内容（留空清除）">
              <input name="remark" maxLength={200} placeholder="例如：核心主号-华东区" />
            </Field>
            <button className="primary">保存备注</button>
          </form>
        </Modal>
      )}

      {dupData && (
        <Modal title="重复账号检查与去重" onClose={() => setDupData(null)}>
          <pre>{JSON.stringify(dupData, null, 2)}</pre>
          <div className="button-row">
            <button
              className="primary"
              onClick={() =>
                c.run(async () => {
                  await post("/ops/accounts/dedupe", { confirm: true });
                  setDupData(null);
                  load();
                }, "已执行账号去重")
              }
            >
              确认执行去重
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}