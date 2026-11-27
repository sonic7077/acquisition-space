import { useEffect, useState, type FormEvent } from "react";
import { Download, Play, Copy, Terminal, ShieldCheck, Activity } from "lucide-react";
import { api, post, type Row } from "../api";
import { Field, Modal, Notice } from "./ui";
import { type Ctx } from "../context";

function detectBrowserOs(): string {
  if (typeof navigator === "undefined") return "macos_arm64";
  const ua = `${navigator.userAgent} ${(navigator as any).platform || ""}`.toLowerCase();
  if (ua.includes("win")) return "windows_x64";
  if (ua.includes("linux")) return "linux_x64";
  if (ua.includes("mac")) {
    return ua.includes("intel") ? "macos_x64" : "macos_arm64";
  }
  return "macos_arm64";
}

type Props = {
  ctx: Ctx;
  onClose: () => void;
  onDone?: () => void;
};

export default function AgentDeployModal({ ctx, onClose, onDone }: Props) {
  const isAdmin = ctx.me?.role === "admin";
  const [tab, setTab] = useState<"install" | "start">("install");
  const [config, setConfig] = useState<Row | null>(null);
  const [osKey, setOsKey] = useState<string>(() => detectBrowserOs());
  const [nodeScope, setNodeScope] = useState<string>(isAdmin ? "server_relay" : "client_local");
  const [label, setLabel] = useState<string>("niekun");
  const [downloadUrl, setDownloadUrl] = useState<string>("");
  const [urlDirty, setUrlDirty] = useState<boolean>(false);
  const [plan, setPlan] = useState<Row | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [localResult, setLocalResult] = useState<Row | null>(null);
  const [healthResult, setHealthResult] = useState<string>("");

  useEffect(() => {
    api<Row>("/ops/agent/deploy-config")
      .then((res) => {
        setConfig(res);
        const eff = res.effective_urls?.[osKey] || "";
        if (!urlDirty) setDownloadUrl(eff);
      })
      .catch(() => {});
  }, []);

  const handleOsChange = (nextOs: string) => {
    setOsKey(nextOs);
    setPlan(null);
    if (!urlDirty && config?.effective_urls?.[nextOs]) {
      setDownloadUrl(config.effective_urls[nextOs]);
    }
  };

  async function generatePlan(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    await ctx.run(async () => {
      const next = await post("/ops/agent/deploy-plan", {
        os_key: osKey,
        node_scope: nodeScope,
        label: label.trim() || "桌面Agent",
        download_url: downloadUrl.trim() || null,
        server_url: window.location.origin,
      });
      setPlan(next);
      onDone?.();
    }, "已生成 Agent 一键部署与自动启动方案");
    setBusy(false);
  }

  const copyText = async (text: string, msg: string) => {
    try {
      await navigator.clipboard.writeText(text);
      ctx.notify(msg);
    } catch {
      ctx.notify("请手动选中下方命令复制");
    }
  };

  const downloadScriptFile = () => {
    if (!plan?.script_content) return;
    const blob = new Blob([plan.script_content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = plan.script_filename || "install-vcat-agent.sh";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const probeLocalHealth = async () => {
    setHealthResult("正在探测 http://127.0.0.1:18899/health ...");
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 1800);
    try {
      const resp = await fetch("http://127.0.0.1:18899/health", { signal: ctrl.signal });
      const data = await resp.json();
      setHealthResult(
        `本机 Agent 在线 · 名称=${data.node_name || "未命名"} · 节点=${data.node_id || "未知"} · 小V猫=${data.mcp_ready ? "就绪" : "未连接"}`,
      );
    } catch {
      setHealthResult("未检测到运行中的本机 Agent（127.0.0.1:18899 无响应）");
    } finally {
      window.clearTimeout(timer);
    }
  };

  const osEntries: [string, Row][] = Object.entries(config?.supported_os || {
    macos_arm64: { label: "macOS (Apple Silicon / M系列)" },
    macos_x64: { label: "macOS (Intel x64)" },
    windows_x64: { label: "Windows 10/11 (x64)" },
    linux_x64: { label: "Linux (x64)" },
  });

  return (
    <Modal title="部署 / 启动小V猫 Agent 服务" onClose={onClose}>
      <div className="deploy-tabs">
        <button
          type="button"
          className={"mode-pill" + (tab === "install" ? " active" : "")}
          onClick={() => setTab("install")}
        >
          <Download size={13} />
          一键下载部署并自动启动
        </button>
        <button
          type="button"
          className={"mode-pill" + (tab === "start" ? " active" : "")}
          onClick={() => setTab("start")}
        >
          <Play size={13} />
          拉起已安装的 Agent 服务
        </button>
      </div>

      {tab === "install" ? (
        <div className="deploy-tab-content">
          <form onSubmit={generatePlan} className="settings-form">
            <div className="field">
              <span>目标操作系统（自动带出对应默认值）</span>
              <div className="mode-switcher" role="group" aria-label="目标操作系统">
                {osEntries.map(([k, meta]) => (
                  <button
                    key={k}
                    type="button"
                    className={"mode-pill" + (osKey === k ? " active" : "")}
                    onClick={() => handleOsChange(k)}
                  >
                    {meta.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="deploy-grid-2">
              <Field label="节点名称">
                <input
                  aria-label="部署节点名称"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  maxLength={60}
                  required
                />
              </Field>
              <Field label="节点归属模式">
                <select
                  aria-label="部署节点归属"
                  value={nodeScope}
                  onChange={(e) => setNodeScope(e.target.value)}
                >
                  <option value="client_local">个人私有 Agent（仅本人电脑可用）</option>
                  {isAdmin && (
                    <option value="server_relay">团队公共 Agent（推荐部署在常开迷你主机）</option>
                  )}
                </select>
              </Field>
            </div>

            <Field label="Agent 下载地址（可自定义覆盖，清空恢复当前系统默认值）">
              <div className="deploy-url-row">
                <input
                  aria-label="Agent下载URL"
                  value={downloadUrl}
                  onChange={(e) => {
                    setDownloadUrl(e.target.value);
                    setUrlDirty(true);
                  }}
                  placeholder={config?.default_urls?.[osKey] || ""}
                />
                <button
                  type="button"
                  onClick={() => {
                    setUrlDirty(false);
                    setDownloadUrl(config?.effective_urls?.[osKey] || config?.default_urls?.[osKey] || "");
                  }}
                >
                  恢复默认
                </button>
              </div>
            </Field>

            <div className="button-row">
              <button className="primary" disabled={busy}>
                <Terminal size={14} />
                生成自动部署与启动方案
              </button>
            </div>
          </form>

          {plan && (
            <div className="schema-preview deploy-plan-box">
              <div className="button-row" style={{ marginBottom: 10 }}>
                <button type="button" className="primary" onClick={downloadScriptFile}>
                  <Download size={14} />
                  下载一键安装启动脚本 ({plan.script_filename})
                </button>
                <button
                  type="button"
                  onClick={() => copyText(plan.one_line_command, "已复制终端一键安装启动命令")}
                >
                  <Copy size={14} />
                  复制终端单行命令
                </button>
                <a className="button-link" href={plan.package_url}>
                  直接下载 Agent 压缩包
                </a>
              </div>
              <p className="muted" style={{ marginBottom: 8 }}>
                脚本执行后将自动完成：下载安装包 → 安装依赖 → 将 <code>ACQ_VCAT_AGENT_HOME</code>、
                <code>ACQ_VCAT_AGENT_BIN</code> 与 <code>VCAT_SERVER_URL</code> 写入环境变量 → 注册系统常驻守护并自动启动运行。
              </p>
              <pre>{plan.one_line_command}</pre>
            </div>
          )}

          <div className="deploy-keepalive-card">
            <strong>
              <ShieldCheck size={14} /> 四层防宕机保活机制（确保服务持续运行不掉线）
            </strong>
            <ul>
              {(config?.keepalive_summary || []).map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <div className="deploy-tab-content">
          <Notice>
            已部署的机器会将启动脚本路径写入环境变量 <code>ACQ_VCAT_AGENT_BIN</code>（及{" "}
            <code>ACQ_VCAT_AGENT_HOME</code> / <code>VCAT_SERVER_URL</code>）。当服务意外停止或未随系统自启时，可直接点击下方按钮尝试唤醒拉起。
          </Notice>

          <div className="button-row" style={{ marginTop: 12 }}>
            <a
              className="button-link primary"
              href={config?.protocol_start_url || "acq-vcat-agent://start"}
            >
              <Play size={14} />
              通过本机协议拉起 Agent
            </a>
            <button
              type="button"
              onClick={() =>
                ctx.run(async () => {
                  const res = await post("/ops/agent/local-start", {});
                  setLocalResult(res);
                  onDone?.();
                })
              }
            >
              尝试通过服务端环境变量拉起（同机场景）
            </button>
            <button type="button" onClick={probeLocalHealth}>
              <Activity size={14} />
              检测本机健康端口 (18899)
            </button>
          </div>

          {localResult && (
            <div className="schema-preview" style={{ marginTop: 12 }}>
              <strong>{localResult.message}</strong>
              <p className="muted">
                启动入口：<code>{localResult.agent_bin}</code>
              </p>
            </div>
          )}
          {healthResult && (
            <p className="muted" style={{ marginTop: 10 }}>
              {healthResult}
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
