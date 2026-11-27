import { useEffect, useState, type FormEvent } from "react";
import {
  RefreshCw,
  Link2,
  ShieldCheck,
  QrCode,
  Trash2,
  Unlink,
  MessageSquarePlus,
  Plus,
  Pencil,
  AlertTriangle,
} from "lucide-react";
import { api, patch, post, type Row } from "../../api";
import { Badge, Card, Empty, Field, Modal, Notice } from "../../components/ui";
import ExecutorNodesPanel from "../../components/ExecutorNodesPanel";
import { type Ctx, filterIp } from "../../context";

const PLATFORMS = [
  ["抖音", "Douyin"],
  ["小红书", "XiaoHongShu"],
  ["视频号", "Channels"],
  ["哔哩哔哩", "Bili"],
];

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("zh-CN", { hour12: false });
}

export default function OpsAccounts(c: Ctx) {
  const [accounts, setAccounts] = useState<Row[]>([]),
    [nodesInfo, setNodesInfo] = useState<Row | null>(null),
    [promoterData, setPromoterData] = useState<Row>({ items: [], pending_activations: [] }),
    [includeRemoved, setIncludeRemoved] = useState(false),
    [filterPromoterId, setFilterPromoterId] = useState(""),
    [filterAvailability, setFilterAvailability] = useState(""),
    [unboundOnly, setUnboundOnly] = useState(false),
    [loginModal, setLoginModal] = useState(false),
    [challenge, setChallenge] = useState<Row | null>(null),
    [bindTarget, setBindTarget] = useState<Row | null>(null),
    [removeTarget, setRemoveTarget] = useState<Row | null>(null),
    [remarkTarget, setRemarkTarget] = useState<Row | null>(null),
    [dupData, setDupData] = useState<Row | null>(null),
    [pairResult, setPairResult] = useState<Row | null>(null),
    [activateOpen, setActivateOpen] = useState(false),
    [activateNodeId, setActivateNodeId] = useState(""),
    [previewData, setPreviewData] = useState<Row | null>(null),
    [previewLoading, setPreviewLoading] = useState(false),
    [previewError, setPreviewError] = useState(""),
    [editPromoter, setEditPromoter] = useState<Row | null>(null),
    [busy, setBusy] = useState(false);

  const load = (
    showRemoved = includeRemoved,
    pId = filterPromoterId,
    avail = filterAvailability,
    onlyUnbound = unboundOnly,
  ) => {
    const params = new URLSearchParams();
    if (c.ip) params.set("ip_id", c.ip);
    if (showRemoved) params.set("include_removed", "true");
    if (pId) params.set("promoter_account_id", pId);
    if (avail) params.set("availability_status", avail);
    if (onlyUnbound) params.set("unbound_only", "true");
    const q = params.toString() ? `?${params.toString()}` : "";
    api<Row[]>(`/ops/accounts${q}`)
      .then(setAccounts)
      .catch(() => {});
    api<Row>("/ops/nodes").then(setNodesInfo).catch(() => {});
    api<Row>("/ops/promoter-accounts").then(setPromoterData).catch(() => {});
  };

  useEffect(() => {
    load(includeRemoved, filterPromoterId, filterAvailability, unboundOnly);
  }, [c.version, c.ip, includeRemoved, filterPromoterId, filterAvailability, unboundOnly]);

  const fetchPreview = async (nodeId: string) => {
    if (!nodeId) {
      setPreviewData(null);
      setPreviewError("");
      return;
    }
    setPreviewLoading(true);
    setPreviewError("");
    try {
      const res = await post("/ops/promoter-accounts/preview-import", { node_id: nodeId });
      setPreviewData(res);
    } catch (err: any) {
      setPreviewData(null);
      setPreviewError(err?.message || "无法获取该节点的渠道推广器账号预览");
    } finally {
      setPreviewLoading(false);
    }
  };

  const openActivateModal = (nodeId?: string) => {
    const availableNodes: Row[] = (nodesInfo?.nodes || []).filter((n: Row) => n.status !== "revoked");
    const preferred =
      nodeId ||
      promoterData?.pending_activations?.[0]?.node_id ||
      availableNodes.find((n: Row) => n.status === "online")?.id ||
      availableNodes[0]?.id ||
      "";
    setActivateNodeId(preferred);
    setPreviewData(null);
    setPreviewError("");
    setActivateOpen(true);
    if (preferred) {
      void fetchPreview(preferred);
    }
  };

  useEffect(() => {
    let pendingNode = "";
    try {
      pendingNode = sessionStorage.getItem("pending_activate_node") || "";
      if (pendingNode) sessionStorage.removeItem("pending_activate_node");
    } catch {
      // ignore storage errors
    }
    if (!pendingNode && typeof window !== "undefined") {
      const hash = window.location.hash || "";
      const idx = hash.indexOf("activate_node=");
      if (idx >= 0) {
        pendingNode = decodeURIComponent(hash.slice(idx + "activate_node=".length).split("&")[0] || "");
      }
    }
    if (pendingNode && nodesInfo) {
      openActivateModal(pendingNode);
    }
  }, [nodesInfo]);

  const ipName = (id: string) => c.ips.find((x) => x.id === id)?.name || "未绑定IP";
  const promoters: Row[] = promoterData?.items || [];
  const pendingActivations: Row[] = promoterData?.pending_activations || [];
  const canShareManage = c.me.role === "admin" || c.me.role === "leader";

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
    }, "已从渠道推广器移除该账号托管（历史记录已保留）");
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
      load();
    }, "账号备注已更新");
  }

  async function submitActivate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!activateNodeId) return;
    const f = new FormData(e.currentTarget);
    setBusy(true);
    let summaryText = "已激活渠道推广器并导入业务账号";
    await c.run(async () => {
      const res = await post("/ops/promoter-accounts/activate", {
        node_id: activateNodeId,
        remark: String(f.get("remark") || "").trim(),
        masked_mobile: String(f.get("masked_mobile") || "").trim() || null,
        ...(canShareManage && f.get("owner_scope")
          ? { owner_scope: String(f.get("owner_scope")) }
          : {}),
        manual_name: String(f.get("manual_name") || "").trim(),
        import_posts: f.get("import_posts") === "on",
        import_verify: f.get("import_verify") === "on",
        import_stats: f.get("import_stats") === "on",
      });
      const imp = res?.import_summary || {};
      const cnt = imp.accounts_imported ?? 0;
      const postCnt = imp.posts_imported ?? 0;
      summaryText = `已激活渠道推广器「${res?.promoter_account?.display_label || ""}」并导入 ${cnt} 个业务账号（作品快照 ${postCnt} 条）`;
      setActivateOpen(false);
      load();
    }, summaryText);
    setBusy(false);
  }

  async function submitEditPromoter(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!editPromoter) return;
    const f = new FormData(e.currentTarget);
    await c.run(async () => {
      await patch(`/ops/promoter-accounts/${editPromoter.id}`, {
        remark: String(f.get("remark") || "").trim(),
        masked_mobile: String(f.get("masked_mobile") || "").trim() || null,
        ...(canShareManage && f.get("owner_scope")
          ? { owner_scope: String(f.get("owner_scope")) }
          : {}),
      });
      setEditPromoter(null);
      load();
    }, "渠道推广器账号信息已更新");
  }

  const nonRevokedNodes: Row[] = (nodesInfo?.nodes || []).filter((n: Row) => n.status !== "revoked");

  return (
    <>
      <Notice>
        支持「服务器联通模式」与「Web前端+本机渠道推广器模式」统一账号管理；新增账号通过内嵌官方登录助手或渠道推广器一键激活导入完成，移除账号仅解除渠道推广器托管且保留历史作品与审计。
      </Notice>

      {pendingActivations.length > 0 && (
        <div className="ops-pending-banner" role="region" aria-label="待激活渠道推广器提示">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertTriangle size={16} color="#9a6108" />
            <strong>
              检测到 {pendingActivations.length} 个在线节点登录了尚未激活导入的渠道推广器账号
            </strong>
          </div>
          {pendingActivations.map((item: Row) => (
            <div className="ops-pending-item" key={`${item.node_id}-${item.external_user_id}`}>
              <div>
                <strong>{item.display_label || `渠道推广器 #${item.external_user_id}`}</strong>
                <small className="muted" style={{ marginLeft: 8 }}>
                  节点：{item.node_name} · 外部账号 #{item.external_user_id}
                  {item.username ? ` · ${item.username}` : ""}
                  {item.masked_mobile ? ` · ${item.masked_mobile}` : ""}
                </small>
              </div>
              <button
                type="button"
                className="primary"
                onClick={() => openActivateModal(String(item.node_id))}
              >
                激活并导入
              </button>
            </div>
          ))}
        </div>
      )}

      <Card
        title={`渠道推广器账号管理 · ${promoters.length}`}
        subtitle="记录各执行节点登录的渠道推广器身份与旗下平台账号映射，不保存任何登录密码或敏感凭据"
        action={
          <div className="button-row">
            <button
              type="button"
              className="primary"
              onClick={() => openActivateModal()}
              disabled={nonRevokedNodes.length === 0}
            >
              <Plus size={15} />
              添加 / 激活渠道推广器
            </button>
          </div>
        }
      >
        {promoters.length > 0 ? (
          <div className="ops-promoter-grid">
            {promoters.map((pa: Row) => {
              const cardCls =
                "ops-promoter-card" +
                (pa.runtime_status === "switched_away"
                  ? " switched"
                  : pa.runtime_status === "offline" || pa.runtime_status === "service_error"
                    ? " offline"
                    : "");
              const filtered = filterPromoterId === pa.id;
              return (
                <div className={cardCls} key={pa.id}>
                  <div className="ops-promoter-card-head">
                    <div>
                      <strong>{pa.display_label}</strong>
                      <div className="ops-promoter-meta" style={{ marginTop: 3 }}>
                        <span>#{pa.external_user_id}</span>
                        {pa.username && <span>@{pa.username}</span>}
                        {pa.masked_mobile && <span>{pa.masked_mobile}</span>}
                      </div>
                    </div>
                    <Badge value={pa.runtime_status || "offline"} />
                  </div>
                  <div className="ops-promoter-meta">
                    <span className="ops-chip">
                      {pa.owner_scope === "team_shared" ? "团队共享" : "个人账号"}
                    </span>
                    <span className="ops-chip">绑定节点：{pa.node_name || "未绑定"}</span>
                    <span className="ops-chip">平台账号 {pa.account_count ?? 0} 个</span>
                  </div>
                  <small className="muted">
                    {pa.runtime_reason || "状态正常"} · 最近同步：{formatTime(pa.last_synced_at)}
                  </small>
                  <div className="ops-promoter-actions">
                    <button
                      type="button"
                      className="text-button"
                      disabled={busy || pa.runtime_status !== "online_active"}
                      title={
                        pa.runtime_status === "online_active"
                          ? "从当前在线客户端全量同步旗下平台账号"
                          : "仅在对应客户端在线且处于当前登录账号时可同步"
                      }
                      onClick={async () => {
                        setBusy(true);
                        await c.run(async () => {
                          await post(`/ops/promoter-accounts/${pa.id}/sync`, {});
                          load();
                        }, `已同步「${pa.display_label}」旗下业务账号`);
                        setBusy(false);
                      }}
                    >
                      <RefreshCw size={13} />
                      同步业务账号
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setEditPromoter(pa)}
                    >
                      <Pencil size={13} />
                      编辑备注/信息
                    </button>
                    <button
                      type="button"
                      className={"text-button" + (filtered ? " primary-text" : "")}
                      onClick={() => setFilterPromoterId(filtered ? "" : String(pa.id))}
                    >
                      {filtered ? "取消筛选" : "筛选旗下账号"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <Empty
            title="暂无已激活的渠道推广器账号"
            body="启动本机或服务器 Agent 并登录渠道推广器后，页面会自动提示「激活并导入」，也可点击右上角「添加 / 激活渠道推广器」。"
          />
        )}
      </Card>

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
                  load();
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
                  setPairResult(await post("/ops/nodes/pair-code", { node_scope: "client_local", label: `${c.me.name}的本机渠道推广器` }));
                }, "已生成本机渠道推广器配对命令")
              }
            >
              <Link2 size={15} />
              配对我的本机渠道推广器
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
        <div className="ops-filter-bar">
          <select
            aria-label="按所属渠道推广器筛选"
            value={filterPromoterId}
            onChange={(e) => setFilterPromoterId(e.target.value)}
          >
            <option value="">全部渠道推广器</option>
            {promoters.map((pa: Row) => (
              <option key={pa.id} value={pa.id}>
                {pa.display_label} (#{pa.external_user_id})
              </option>
            ))}
          </select>
          <select
            aria-label="按综合可用态筛选"
            value={filterAvailability}
            onChange={(e) => setFilterAvailability(e.target.value)}
          >
            <option value="">全部综合可用态</option>
            <option value="ready">可执行 (ready)</option>
            <option value="platform_expired">登录失效 (platform_expired)</option>
            <option value="promoter_switched">宿主账号已切换 (promoter_switched)</option>
            <option value="node_offline">节点离线 (node_offline)</option>
            <option value="removed">已移除 (removed)</option>
          </select>
          <label className="check-row">
            <input
              type="checkbox"
              checked={unboundOnly}
              onChange={(e) => setUnboundOnly(e.target.checked)}
            />
            只看未绑定IP账号
          </label>
          {(filterPromoterId || filterAvailability || unboundOnly) && (
            <button
              type="button"
              className="text-button"
              onClick={() => {
                setFilterPromoterId("");
                setFilterAvailability("");
                setUnboundOnly(false);
              }}
            >
              重置筛选
            </button>
          )}
        </div>

        {pairResult && (
          <div className="schema-preview">
            <strong>在你的电脑运行以下命令接入「Web 前端 + 本机渠道推广器模式」（30分钟内有效）：</strong>
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
                  <th>所属渠道推广器</th>
                  <th>接入来源</th>
                  <th>绑定IP</th>
                  <th>登录状态</th>
                  <th>综合可用态</th>
                  <th>诊断/验证</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {filterIp(accounts, "").map((a) => {
                  const avail = String(a.availability_status || "ready");
                  const isDimmed = avail === "promoter_switched" || avail === "node_offline";
                  const liveDisabled = !["ready", "platform_expired"].includes(avail);
                  return (
                    <tr key={a.id} className={isDimmed ? "ops-row-dimmed" : undefined}>
                      <td>
                        <strong>{a.nickname}</strong>
                        <small style={{ display: "block" }}>{a.vendor_uid}</small>
                        {a.remark && <small className="muted">备注：{a.remark}</small>}
                      </td>
                      <td>{a.platform_name}</td>
                      <td>
                        <div>{a.promoter_account_label || "未关联"}</div>
                        {a.promoter_external_user_id && (
                          <small className="muted">#{a.promoter_external_user_id}</small>
                        )}
                      </td>
                      <td>
                        <Badge value={a.source || "promoter_import"} />
                      </td>
                      <td>{ipName(a.ip_id)}</td>
                      <td>
                        <Badge value={a.login_status} />
                      </td>
                      <td>
                        <div className="exec-status-cell">
                          <Badge value={avail} />
                          {a.availability_reason && (
                            <small className="muted">{a.availability_reason}</small>
                          )}
                        </div>
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
                                  disabled={liveDisabled}
                                  title={liveDisabled ? a.availability_reason || "当前不可实时执行" : undefined}
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
                                  disabled={liveDisabled}
                                  title={liveDisabled ? a.availability_reason || "当前不可实时执行" : undefined}
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
                            <button
                              className="text-button"
                              disabled={liveDisabled}
                              title={liveDisabled ? a.availability_reason || "当前不可从客户端移除" : undefined}
                              onClick={() => setRemoveTarget(a)}
                            >
                              <Trash2 size={14} />
                              移除账号
                            </button>
                          </div>
                        ) : (
                          <small className="muted">已移除（保留审计）</small>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无已同步的平台账号"
            body="请先激活渠道推广器、在系统设置配置渠道推广器连接或配对本机代理，然后点击「同步节点账号」或「Web 扫码新增账号」。"
          />
        )}
      </Card>

      <ExecutorNodesPanel
        ctx={c}
        nodesInfo={nodesInfo}
        onChanged={setNodesInfo}
        onActivateNode={(nodeId) => openActivateModal(nodeId)}
      />

      {activateOpen && (
        <Modal title="添加 / 激活渠道推广器账号并导入" onClose={() => setActivateOpen(false)}>
          <form onSubmit={submitActivate}>
            <Field label="选择在线执行节点">
              <div className="deploy-url-row">
                <select
                  name="node_id"
                  value={activateNodeId}
                  onChange={(e) => {
                    const nextId = e.target.value;
                    setActivateNodeId(nextId);
                    void fetchPreview(nextId);
                  }}
                  required
                >
                  <option value="">请选择节点</option>
                  {nonRevokedNodes.map((n: Row) => (
                    <option key={n.id} value={n.id}>
                      {n.name} ({n.status === "online" ? "在线" : n.status})
                      {n.active_promoter_label ? ` · ${n.active_promoter_label}` : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => fetchPreview(activateNodeId)}
                  disabled={previewLoading || !activateNodeId}
                >
                  {previewLoading ? "读取中..." : "刷新探测"}
                </button>
              </div>
            </Field>

            {previewError && <Notice>{previewError}</Notice>}

            {previewData && (
              <div className="schema-preview" style={{ marginBottom: 12 }}>
                <div style={{ display: "grid", gap: 6 }}>
                  <div>
                    <strong>识别到渠道推广器账号：</strong>
                    {previewData.detected ? (
                      <>
                        <span>{previewData.display_label}</span>
                        {previewData.external_user_id && (
                          <small className="muted" style={{ marginLeft: 6 }}>
                            #{previewData.external_user_id}
                          </small>
                        )}
                        {previewData.already_activated && (
                          <span className="badge good" style={{ marginLeft: 8 }}>
                            已激活过（再次提交将全量增量同步）
                          </span>
                        )}
                      </>
                    ) : (
                      <span>未自动探测到外部账号 ID，可填写下方人工标识名继续导入</span>
                    )}
                  </div>
                  <div className="ops-promoter-meta">
                    <span className="ops-chip">可导入业务账号：{previewData.account_count ?? 0} 个</span>
                    {previewData.post_count !== null && previewData.post_count !== undefined && (
                      <span className="ops-chip">作品快照：{previewData.post_count} 条</span>
                    )}
                    {previewData.verify_count !== null && previewData.verify_count !== undefined && (
                      <span className="ops-chip">视频号诊断：{previewData.verify_count} 条</span>
                    )}
                    {Object.entries(previewData.platform_breakdown || {}).map(([pName, cnt]) => (
                      <span className="ops-chip" key={pName}>
                        {pName} × {String(cnt)}
                      </span>
                    ))}
                  </div>
                  {(previewData.sample_accounts || []).length > 0 && (
                    <small className="muted">
                      示例账号：
                      {(previewData.sample_accounts || [])
                        .slice(0, 6)
                        .map((sa: Row) => `${sa.nickname}(${sa.platform_name})`)
                        .join("、")}
                    </small>
                  )}
                </div>
              </div>
            )}

            {previewData && !previewData.detected && (
              <Field label="手动标识名称（未自动探测到账号ID时必填）">
                <input name="manual_name" placeholder="例如：运营公用主机渠道推广器" required />
              </Field>
            )}

            <Field label="渠道推广器备注名（可选，将作为主展示名称）">
              <input name="remark" maxLength={80} placeholder="例如：上海运营主号A" />
            </Field>
            <Field label="脱敏手机号（可选，仅保留前3后4位如 138****5678）">
              <input name="masked_mobile" maxLength={20} placeholder="例如：138****5678" />
            </Field>
            {canShareManage && (
              <Field label="归属范围">
                <select
                  name="owner_scope"
                  defaultValue={
                    previewData?.node_scope === "client_local" ? "personal" : "team_shared"
                  }
                >
                  <option value="team_shared">团队共享（团队长/管理员可认领分配）</option>
                  <option value="personal">个人专属</option>
                </select>
              </Field>
            )}
            <div style={{ display: "grid", gap: 6, marginBottom: 14 }}>
              <label className="check-row">
                <input type="checkbox" name="import_posts" defaultChecked />
                同时导入作品快照（仅读渠道推广器本地缓存）
              </label>
              <label className="check-row">
                <input type="checkbox" name="import_verify" defaultChecked />
                同时导入视频号最近诊断摘要
              </label>
              <label className="check-row">
                <input type="checkbox" name="import_stats" defaultChecked />
                同时导入账号粉丝等统计快照
              </label>
            </div>
            <button className="primary" disabled={busy || previewLoading || !activateNodeId}>
              确认激活并导入全部业务账号
            </button>
          </form>
        </Modal>
      )}

      {editPromoter && (
        <Modal
          title={`编辑渠道推广器信息 · ${editPromoter.display_label}`}
          onClose={() => setEditPromoter(null)}
        >
          <form onSubmit={submitEditPromoter}>
            <Field label="备注名（优先展示，留空则自动使用昵称/账号名）">
              <input
                name="remark"
                defaultValue={editPromoter.remark || ""}
                maxLength={80}
                placeholder="例如：北京主控台"
              />
            </Field>
            <Field label="脱敏手机号（仅存脱敏后文本）">
              <input
                name="masked_mobile"
                defaultValue={editPromoter.masked_mobile || ""}
                maxLength={20}
                placeholder="138****5678"
              />
            </Field>
            {canShareManage && (
              <Field label="归属范围">
                <select name="owner_scope" defaultValue={editPromoter.owner_scope || "team_shared"}>
                  <option value="team_shared">团队共享</option>
                  <option value="personal">个人专属</option>
                </select>
              </Field>
            )}
            <button className="primary">保存更改</button>
          </form>
        </Modal>
      )}

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
            {nonRevokedNodes.length > 0 && (
              <Field label="执行节点（可选）">
                <select name="node_id" defaultValue="">
                  <option value="">自动按系统默认模式选择</option>
                  {nonRevokedNodes.map((n: Row) => (
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
                      const res = await post("/ops/accounts/sync", {
                        node_id: challenge.node_id,
                        source: "web_iframe",
                      });
                      setAccounts(res.accounts || []);
                      setLoginModal(false);
                      load();
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
        <Modal title={`从渠道推广器移除账号 · ${removeTarget.nickname}`} onClose={() => setRemoveTarget(null)}>
          <Notice>
            此操作仅从渠道推广器软件中移除对该账号的托管，不会注销抖音/小红书/视频号平台账号本身；但渠道推广器桌面端内该账号的统计与发布记录会被清除，获客空间历史作品与审计记录仍会保留。
          </Notice>
          <form onSubmit={submitRemoveAccount}>
            <Field label={`请输入账号昵称「${removeTarget.nickname}」以确认移除`}>
              <input name="confirm_nickname" required placeholder={removeTarget.nickname} />
            </Field>
            <label className="check-row">
              <input type="checkbox" name="cancel_pending" />
              若存在排队中的待发任务，同时取消后再移除
            </label>
            <button className="primary">确认从渠道推广器移除</button>
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
