import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  Lightbulb,
  Users,
  FileCheck,
  Clapperboard,
  CalendarDays,
  ChartNoAxesCombined,
  MessageSquare,
  BookOpen,
  BrainCircuit,
  Settings,
  LogOut,
  Check,
  ChevronRight,
  RefreshCw,
  PanelLeftClose,
  UserCheck,
  ShieldAlert,
  Send,
  Video,
  MessagesSquare,
  Activity,
} from "lucide-react";
import { api, post, type Row } from "./api";

import { ROLE, Empty, Card } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Knowledge from "./pages/Knowledge";
import Writers from "./pages/Writers";
import { type WriterPreference } from "./pages/MaterialWriterDialog";
import SettingsPage from "./pages/SettingsPage";
import UsersPage from "./pages/UsersPage";
import Leads from "./pages/Leads";
import Topics from "./pages/Topics";
import Drafts from "./pages/Drafts";
import Assets from "./pages/Assets";
import Schedules from "./pages/Schedules";
import Reports from "./pages/Reports";
import Authors from "./pages/Authors";
import Help from "./pages/Help";
import OpsAccounts from "./pages/ops/OpsAccounts";
import OpsChallenges from "./pages/ops/OpsChallenges";
import OpsTasks from "./pages/ops/OpsTasks";
import OpsPosts from "./pages/ops/OpsPosts";
import OpsInteractions from "./pages/ops/OpsInteractions";
import OpsMonitors from "./pages/ops/OpsMonitors";
const GROUPS = [
  { name: "", items: [["dashboard", "工作台", LayoutDashboard]] },
  {
    name: "内容生产",
    items: [
      ["materials", "选题库", Lightbulb],
      ["authors", "对标博主", Users],
      ["drafts", "审稿", FileCheck],
      ["assets", "拍摄上传", Clapperboard],
      ["schedules", "发布排期", CalendarDays],
    ],
  },
  {
    name: "运营",
    items: [
      ["ops-accounts", "账号与授权", UserCheck],
      ["ops-challenges", "授权中心", ShieldAlert],
      ["ops-tasks", "发布任务", Send],
      ["ops-posts", "作品与数据", Video],
      ["ops-interactions", "评论与私信", MessagesSquare],
      ["ops-monitors", "监控与告警", Activity],
    ],
  },
  {
    name: "结果",
    items: [
      ["reports", "数据复盘", ChartNoAxesCombined],
      ["leads", "客户线索", MessageSquare],
    ],
  },
  {
    name: "内容基础",
    items: [
      ["knowledge", "知识库", BookOpen],
      ["writers", "IP与写手", BrainCircuit],
      ["help", "使用说明", BookOpen],
    ],
  },
  {
    name: "管理",
    items: [
      ["users", "用户管理", Users],
      ["settings", "系统设置", Settings],
    ],
  },
];
const ALL = GROUPS.flatMap((g) => g.items);
const getError = (e: unknown) => (e instanceof Error ? e.message : "操作失败");
function currentPage() {
  const hash = location.hash.slice(1);
  if (hash === "needs") return "leads";
  return ALL.some(([id]) => id === hash) ? hash : "dashboard";
}
export default function App() {
  const [me, setMe] = useState<Row | null>(null),
    [ready, setReady] = useState(false),
    [page, setPage] = useState(currentPage),
    [ips, setIps] = useState<Row[]>([]),
    [ip, setIp] = useState(""),
    [preferredWriter, setPreferredWriter] = useState<WriterPreference | null>(null),
    [selectedDraftId, setSelectedDraftId] = useState<string>(),
    [selectedScheduleId, setSelectedScheduleId] = useState<string>(),
    [version, setVersion] = useState(0),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [collapsed, setCollapsed] = useState(false);
  const refresh = () => setVersion((v) => v + 1);
  const notify = (s: string) => {
    setToast(s);
    setTimeout(() => setToast(""), 4500);
  };
  const run = async (action: () => Promise<any>, success?: string) => {
    setError("");
    try {
      const result = await action();
      if (success) notify(success);
      refresh();
      return result;
    } catch (e) {
      setError(getError(e));
      return null;
    }
  };
  useEffect(() => {
    api("/auth/me")
      .then(setMe)
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);
  useEffect(() => {
    if (me)
      api("/ips")
        .then(setIps)
        .catch((e) => setError(getError(e)));
  }, [me, version]);
  useEffect(() => {
    const cb = () => {
      if (location.hash === "#needs") history.replaceState(null, "", "#leads");
      setPage(currentPage());
    };
    cb();
    window.addEventListener("hashchange", cb);
    return () => window.removeEventListener("hashchange", cb);
  }, []);
  useEffect(() => {
    if (page !== "materials") setPreferredWriter(null);
  }, [page]);
  const go = (next: string, selectedId?: string) => {
    setSelectedDraftId(next === "drafts" ? selectedId : undefined);
    setSelectedScheduleId(next === "ops-tasks" ? selectedId : undefined);
    if ((next === "drafts" || next === "ops-tasks") && selectedId) setIp("");
    location.hash = next;
    setPage(next);
    setError("");
  };
  if (!ready)
    return (
      <div className="login-wrap">
        <p>正在连接本地工作台…</p>
      </div>
    );
  if (!me)
    return (
      <Login
        onLogin={(u) => {
          setMe(u);
        }}
      />
    );
  const title = (ALL.find((r) => r[0] === page)?.[1] as string) || "工作台";
  const group = GROUPS.find((g) => g.items.some((i) => i[0] === page))?.name;
  const ctx = { me, ips, ip, version, run, go, notify };
  const noAdmin = ["settings", "users"].includes(page) && me.role !== "admin";
  return (
    <div className={"shell " + (collapsed ? "collapsed" : "")}>
      <aside>
        <a className="brand" href="#dashboard">
          <span className="brand-icon">获</span>
          <div>
            获客空间<span>内容驱动 · 持续获客</span>
          </div>
        </a>
        <div className="workspace-label">
          团队工作空间 <span>LOCAL</span>
        </div>
        <nav>
          {GROUPS.map((g) => (
            <div className="nav-group" key={g.name}>
              {g.name && <div className="nav-label">{g.name}</div>}
              {g.items
                .filter(
                  (i) =>
                    !["users", "settings"].includes(i[0] as string) ||
                    me.role === "admin",
                )
                .map(([id, label, Icon]) => (
                  <button
                    className={page === id ? "active" : ""}
                    key={id as string}
                    aria-label={label as string}
                    title={label as string}
                    onClick={() => go(id as string)}
                  >
                    <Icon size={18} />
                    <span>{label as string}</span>
                    {page === id && <ChevronRight size={14} />}
                  </button>
                ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="dot" /> 本地数据已持久保存
          <small>自动化状态请查看工作台</small>
        </div>
      </aside>
      <div className="main-wrap">
        <header>
          <div className="breadcrumb">
            <button
              className="icon-button"
              onClick={() => setCollapsed(!collapsed)}
              aria-label="折叠导航"
            >
              <PanelLeftClose size={18} />
            </button>
            <span>{group || "总览"}</span>
            <ChevronRight size={14} />
            <strong>{title}</strong>
          </div>
          <div className="header-right">
            {page !== "writers" && <select
              aria-label="筛选IP"
              value={ip}
              onChange={(e) => { setIp(e.target.value); setPreferredWriter(null); }}
            >
              <option value="">全部授权IP</option>
              {ips.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                </option>
              ))}
            </select>}
            <button
              className="icon-button"
              onClick={refresh}
              title="刷新本地数据"
              aria-label="刷新"
            >
              <RefreshCw size={17} />
            </button>
            <span className="header-divider" />
            <div className="avatar">{me.name.slice(0, 1)}</div>
            <div className="user-summary">
              {me.name}
              <small>{ROLE[me.role]}</small>
            </div>
            <button
              className="icon-button"
              title="退出"
              onClick={() =>
                run(async () => {
                  await post("/auth/logout", {});
                  setMe(null);
                  setIps([]);
                  setIp("");
                  setPreferredWriter(null);
                })
              }
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        <main>
          <div className="page-title">
            <div>
              {page !== "writers" && <p className="eyebrow">YOUR CONTENT GROWTH WORKSPACE</p>}
              <h1>{title}</h1>
              <p>
                {(
                  {
                    dashboard: "把灵感变成内容，把内容变成客户。",
                    leads: "按来源查看评论和咨询，从有效需求直接创建选题。",
                    knowledge: "让已有知识成为写手的可靠依据。",
                    settings: "统一管理模型、密钥与外部服务。",
                    writers: "结合写手资料、基础 Skill 和知识库，生成可用于素材改写的专属 Skill。",
                  } as Row
                )[page] || "每一步有记录，让团队协作更清晰。"}
              </p>
            </div>
            {page !== "writers" && <div className="page-date">
              {new Date().toLocaleDateString("zh-CN", {
                month: "long",
                day: "numeric",
                weekday: "long",
              })}
            </div>}
          </div>
          {error && (
            <div role="alert" className="error-banner">
              {error}
              <button onClick={() => setError("")}>关闭</button>
            </div>
          )}
          {noAdmin ? (
            <Card>
              <Empty
                title="仅管理员可访问"
                body="此页面管理系统配置和用户权限。"
              />
            </Card>
          ) : page === "dashboard" ? (
            <Dashboard {...ctx} />
          ) : page === "materials" ? (
            <Topics {...ctx} preferredWriter={preferredWriter} />
          ) : page === "authors" ? (
            <Authors {...ctx} />
          ) : page === "help" ? (
            <Help {...ctx} />
          ) : page === "leads" ? (
            <Leads {...ctx} />
          ) : page === "knowledge" ? (
            <Knowledge {...ctx} />
          ) : page === "writers" ? (
            <Writers {...ctx} onRewrite={(ipId, writerId) => {
              setIp("");
              setPreferredWriter({ipId, writerId});
              go("materials");
            }} />
          ) : page === "settings" ? (
            <SettingsPage {...ctx} />
          ) : page === "users" ? (
            <UsersPage {...ctx} />
          ) : page === "drafts" ? (
            <Drafts key={selectedDraftId} {...ctx} initialDraftId={selectedDraftId} />
          ) : page === "assets" ? (
            <Assets key={ip} {...ctx} />
          ) : page === "schedules" ? (
            <Schedules {...ctx} />
          ) : page === "ops-accounts" ? (
            <OpsAccounts {...ctx} />
          ) : page === "ops-challenges" ? (
            <OpsChallenges {...ctx} />
          ) : page === "ops-tasks" ? (
            <OpsTasks key={selectedScheduleId || "ops-tasks"} {...ctx} initialScheduleId={selectedScheduleId} />
          ) : page === "ops-posts" ? (
            <OpsPosts {...ctx} />
          ) : page === "ops-interactions" ? (
            <OpsInteractions {...ctx} />
          ) : page === "ops-monitors" ? (
            <OpsMonitors {...ctx} />
          ) : page === "reports" ? (
            <Reports {...ctx} />
          ) : (
            <Dashboard {...ctx} />
          )}
          <footer>
            获客空间 · 让知识持续转化为内容价值 <span>本地工作空间 / v0.1</span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Check size={18} />
          {toast}
        </div>
      )}
    </div>
  );
}
