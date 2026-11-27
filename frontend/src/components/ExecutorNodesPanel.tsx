import { useEffect, useState } from "react";
import { RefreshCw, Radio, Check, Pencil, Star, Download } from "lucide-react";
import { api, post, type Row } from "../api";
import { Badge, Card } from "./ui";
import AgentDeployModal from "./AgentDeployModal";
import { type Ctx } from "../context";

export const EXECUTOR_MODES: Record<string, string> = {
  server_direct: "本地直连兜底",
  server_relay: "远端托管Agent",
  client_local: "个人本机代理",
  hybrid: "混合路由",
  manual: "仅手工登记",
};

const MODE_ORDER = ["server_direct", "server_relay", "client_local", "hybrid", "manual"];

export function channelLabel(channel?: string): string {
  if (channel === "direct_http") return "本地直连";
  if (channel === "long_poll") return "长轮询 (即时)";
  if (channel === "short_poll") return "短轮询";
  return channel || "未知";
}

export function scopeLabel(scope?: string): string {
  if (scope === "server_direct") return "本地直连兜底";
  if (scope === "server_relay") return "团队公共Agent";
  if (scope === "client_local") return "个人私有Agent";
  return scope || "未知";
}

export function formatHeartbeatAge(ageSec?: number | null): string {
  if (ageSec === null || ageSec === undefined) return "暂无心跳";
  if (ageSec < 60) return `${ageSec}秒前心跳`;
  const mins = Math.floor(ageSec / 60);
  if (mins < 60) return `${mins}分前心跳`;
  return `${Math.floor(mins / 60)}小时前心跳`;
}

type Props = {
  ctx: Ctx;
  nodesInfo?: Row | null;
  onChanged?: (next: Row) => void;
  onActivateNode?: (nodeId: string) => void;
  embedded?: boolean;
};

export default function ExecutorNodesPanel({ ctx, nodesInfo, onChanged, onActivateNode, embedded = false }: Props) {
  const [data, setData] = useState<Row | null>(nodesInfo || null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [deployOpen, setDeployOpen] = useState(false);

  useEffect(() => {
    if (nodesInfo) setData(nodesInfo);
  }, [nodesInfo]);

  const fetchLatest = () => {
    api<Row>("/ops/nodes")
      .then((res) => {
        setData(res);
        onChanged?.(res);
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetchLatest();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        fetchLatest();
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [ctx.version]);

  const nodes: Row[] = data?.nodes || [];
  const currentMode: string = data?.mode || "manual";
  const isAdmin = ctx.me?.role === "admin";

  const canManage = (n: Row) =>
    isAdmin || (n.node_scope === "client_local" && n.owner_user_id === ctx.me?.id);

  const switchMode = (mode: string) =>
    ctx.run(async () => {
      const next = await post("/ops/executor/switch", { mode });
      setData(next);
      onChanged?.(next);
    }, `执行模式已切换为「${EXECUTOR_MODES[mode] || mode}」`);

  const setDefaultNode = (nodeId: string, nodeName: string) =>
    ctx.run(async () => {
      const next = await post("/ops/executor/switch", { default_node_id: nodeId });
      setData(next);
      onChanged?.(next);
    }, `已将「${nodeName}」设为默认执行节点`);

  const submitRename = (nodeId: string) =>
    ctx.run(async () => {
      await api(`/ops/nodes/${nodeId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: draftName }),
      });
      setEditingId(null);
      fetchLatest();
    }, "执行节点名称已更新");

  const body = (
    <div className="exec-panel-body">
      <div className="exec-live-bar">
        <div className="exec-live-left">
          <span className="exec-live-dot" />
          <span>实时状态 · 每3秒自动更新</span>
          {data?.local_fallback_ready ? (
            <span className="badge good">本地兜底就绪</span>
          ) : (
            <span className="badge">本地兜底未配令牌</span>
          )}
        </div>
        <div className="button-row">
          <button type="button" className="primary" onClick={() => setDeployOpen(true)}>
            <Download size={14} />
            部署 / 启动 Agent
          </button>
          <button type="button" className="text-button" onClick={fetchLatest}>
            <RefreshCw size={14} />
            刷新节点状态
          </button>
        </div>
      </div>

      <div className="mode-switcher-row">
        <span className="mode-switcher-label">运行模式快捷切换：</span>
        <div className="mode-switcher">
          {MODE_ORDER.map((m) => (
            <button
              key={m}
              type="button"
              disabled={!isAdmin}
              className={
                "mode-pill" +
                (currentMode === m ? " active" : "") +
                (m === "server_direct" ? " fallback" : "")
              }
              onClick={() => switchMode(m)}
            >
              {currentMode === m && <Check size={12} />}
              {EXECUTOR_MODES[m]}
            </button>
          ))}
        </div>
      </div>

      {currentMode === "server_direct" && data && !data.local_fallback_ready && (
        <p className="muted" style={{ marginBottom: 10 }}>
          提示：当前已选择「本地直连兜底」，请在「系统设置 → 发布执行器」填写本机渠道推广器访问令牌后保存。
        </p>
      )}

      {nodes.length > 0 ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>节点名称</th>
                <th>类型与归属</th>
                <th>当前渠道推广器账号</th>
                <th>实时状态</th>
                <th>通道与队列</th>
                <th>版本</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((n: Row) => (
                <tr key={n.id}>
                  <td>
                    {editingId === n.id ? (
                      <div className="inline-rename">
                        <input
                          aria-label="编辑节点名称"
                          value={draftName}
                          onChange={(e) => setDraftName(e.target.value)}
                          maxLength={80}
                        />
                        <button type="button" className="text-button" onClick={() => submitRename(n.id)}>
                          保存
                        </button>
                        <button type="button" className="text-button" onClick={() => setEditingId(null)}>
                          取消
                        </button>
                      </div>
                    ) : (
                      <div className="exec-node-name">
                        <strong>{n.name}</strong>
                        {n.is_default && (
                          <span className="badge good">
                            <Star size={11} /> 默认节点
                          </span>
                        )}
                      </div>
                    )}
                  </td>
                  <td>{scopeLabel(n.node_scope)}</td>
                  <td>
                    <div className="exec-status-cell">
                      <span>{n.active_promoter_label || "未检测到"}</span>
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                        {n.active_promoter_user_id && (
                          <small className="muted">#{n.active_promoter_user_id}</small>
                        )}
                        {n.active_promoter_user_id && (
                          <span className={`badge ${n.active_promoter_activated ? "good" : "warn"}`}>
                            {n.active_promoter_activated ? "已激活" : "未激活"}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="exec-status-cell">
                      <Badge value={n.status} />
                      <small className="muted">
                        {n.active_poll
                          ? "长轮询连线中"
                          : n.id === "server-direct-default" && !n.token_configured
                            ? "未配置访问令牌"
                            : formatHeartbeatAge(n.heartbeat_age_sec)}
                      </small>
                    </div>
                  </td>
                  <td>
                    <div className="exec-status-cell">
                      <span>{channelLabel(n.channel)}</span>
                      <small className="muted">
                        排队 {n.queued_commands ?? 0} · 执行中 {n.running_commands ?? 0}
                      </small>
                    </div>
                  </td>
                  <td>{n.vcat_version || "—"}</td>
                  <td>
                    <div className="button-row">
                      {n.active_promoter_user_id && !n.active_promoter_activated && n.status === "online" && (
                        <button
                          type="button"
                          className="primary"
                          onClick={() => {
                            if (onActivateNode) {
                              onActivateNode(String(n.id));
                            } else {
                              try {
                                sessionStorage.setItem("pending_activate_node", String(n.id));
                              } catch {
                                // ignore storage errors
                              }
                              ctx.go("ops-accounts");
                            }
                          }}
                        >
                          激活并导入
                        </button>
                      )}
                      {isAdmin && n.status !== "revoked" && !n.is_default && (
                        <button
                          type="button"
                          className="text-button"
                          onClick={() => setDefaultNode(n.id, n.name)}
                        >
                          设为默认
                        </button>
                      )}
                      {canManage(n) && (
                        <button
                          type="button"
                          className="text-button"
                          onClick={() => {
                            setEditingId(n.id);
                            setDraftName(n.name || "");
                          }}
                        >
                          <Pencil size={12} /> 改名
                        </button>
                      )}
                      {canManage(n) && n.status !== "revoked" && (
                        <button
                          type="button"
                          className="text-button"
                          onClick={() =>
                            ctx.run(async () => {
                              await post(`/ops/nodes/${n.id}/probe`, {});
                              fetchLatest();
                            }, "节点连接探测成功")
                          }
                        >
                          探测连接
                        </button>
                      )}
                      {canManage(n) && n.id !== "server-direct-default" && n.status !== "revoked" && (
                        <button
                          type="button"
                          className="text-button"
                          onClick={() =>
                            ctx.run(async () => {
                              await post(`/ops/nodes/${n.id}/revoke`, {});
                              fetchLatest();
                            }, "执行节点已吊销下线")
                          }
                        >
                          吊销下线
                        </button>
                      )}
                      {canManage(n) &&
                        n.id !== "server-direct-default" &&
                        ["revoked", "offline"].includes(n.status) && (
                          <button
                            type="button"
                            className="text-button"
                            onClick={() =>
                              ctx.run(async () => {
                                await api(`/ops/nodes/${n.id}`, { method: "DELETE" });
                                fetchLatest();
                              }, "离线节点已移除")
                            }
                          >
                            移除节点
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
        <p className="muted">暂无执行节点记录，请点击「部署 / 启动 Agent」或生成配对命令。</p>
      )}
      {deployOpen && (
        <AgentDeployModal ctx={ctx} onClose={() => setDeployOpen(false)} onDone={fetchLatest} />
      )}
    </div>
  );

  if (embedded) return body;

  return (
    <Card
      title={`执行节点实时状态 · 在线 ${nodes.filter((n) => n.status === "online").length}/${nodes.length}`}
      subtitle={`当前模式：${EXECUTOR_MODES[currentMode] || currentMode}`}
      action={<Radio size={16} className="muted" />}
    >
      {body}
    </Card>
  );
}
