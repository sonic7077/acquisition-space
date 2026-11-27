import { type ReactNode } from "react";
import { FolderOpen, X, ShieldCheck } from "lucide-react";
import { type Row } from "../api";
export const ROLE: Row = { admin: "管理员", leader: "团队长", member: "队员" };
export const STATE: Row = {
  pending: "待审核",
  expired: "证据失效",
  writing: "待写",
  verified: "已核验",
  template: "模板资料",
  archived: "已归档",
  new: "新记录",
  approved: "已批准",
  returned: "已退回",
  planned: "已排期",
  staged: "待人工发表",
  published: "已发表",
  unknown: "结果未知",
  cancelled: "已取消",
  needs_review: "需重新审核",
  candidate: "候选",
  adopted: "已采纳",
  excluded: "已排除",
  queued: "排队中",
  running: "生成中",
  failed: "失败",
  completed: "已完成",
  partial: "部分完成",
  confirmed: "已确认",
  unprepared: "未准备",
  producing: "制作中",
  review: "待审",
  dependency: "待依赖",
  manual: "待人工发表",
  skipped: "已跳过",
  QUEUED: "排队中",
  PREFLIGHT: "预检中",
  STAGING: "媒体同步中",
  SUBMITTING: "提交中",
  ACCEPTED: "平台已受理",
  PUBLISHING: "上传发布中",
  PLATFORM_SCHEDULED: "平台定时中",
  REVIEWING: "审核中",
  DRAFT_PUSHED: "已推草稿箱",
  PUBLISHED: "已发表",
  REJECTED: "平台拒绝",
  UNKNOWN: "结果未知",
  RETRYABLE_FAILED: "失败可重试",
  MISSED: "错过排期",
  CANCELLED: "已取消",
  WAITING_AUTH: "等待扫码验证",
  WAITING_CLIENT_ONLINE: "等待本机上线",
  WAITING_SCAN: "待扫码",
  VERIFIED: "已验证",
  EXPIRED: "已过期",
  online: "在线",
  offline: "离线",
  mcp_error: "MCP异常",
  revoked: "已吊销",
};

export function Badge({ value }: { value: string }) {
  return (
    <span
      className={
        "badge " +
        ([
          "verified",
          "approved",
          "published",
          "adopted",
          "completed",
          "confirmed",
          "PUBLISHED",
          "VERIFIED",
          "online",
          "登录有效",
        ].includes(value)
          ? "good"
          : [
              "failed",
              "needs_review",
              "unknown",
              "returned",
              "UNKNOWN",
              "REJECTED",
              "RETRYABLE_FAILED",
              "MISSED",
              "EXPIRED",
              "mcp_error",
              "offline",
              "revoked",
              "已从小V猫移除",
              "已在小V猫移除",
              "登录失效",
            ].includes(value)
            ? "bad"
            : "")
      }
    >
      {STATE[value] || value}
    </span>
  );
}
export function Empty({
  title = "暂无数据",
  body = "从新增或导入开始，建立你的内容资产。",
  children,
}: {
  title?: string;
  body?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <FolderOpen size={30} />
      </div>
      <h3>{title}</h3>
      <p>{body}</p>
      {children}
    </div>
  );
}
export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={"card " + className}>
      {title && (
        <div className="card-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-head">
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="关闭">
            <X size={20} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice">
      <ShieldCheck size={17} />
      <span>{children}</span>
    </div>
  );
}
export function fmt(v: string) {
  return v
    ? new Date(v).toLocaleString("zh-CN", {
        hour12: false,
        timeZone: "Asia/Shanghai",
      })
    : "—";
}

export const getError = (e: unknown) =>
  e instanceof Error ? e.message : "操作失败";
