"""Unified Xiaovmao operations service across server-connected and client-local modes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from ..db import (
    AgentPairCode,
    AuthChallenge,
    ExecutorNode,
    MetricSnapshot,
    PlatformAccount,
    PublishAttempt,
    PublishTarget,
    PublishedPost,
    Record,
    User,
    VcatActionLog,
    business_today,
    now,
    uid,
)
from ..knowledge import verified_document
from ..security import active_ip, approver, can_ip, require_ip
from ..writing import draft_checks
from .qr import render_qr_png
from .transport import (
    MediaRef,
    StreamableHttpMcpClient,
    TransportRouter,
    VcatActionCommand,
    VcatActionResult,
    clean_metric_number,
    sanitize_summary,
)
from .wechat_draft import push_official_account_draft

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DAILY_LIMIT_PER_ACCOUNT = 3
ACTIVE_DAILY_ATTEMPT_STATES = {
    "SUBMITTING",
    "ACCEPTED",
    "PUBLISHING",
    "PLATFORM_SCHEDULED",
    "REVIEWING",
    "DRAFT_PUSHED",
    "PUBLISHED",
    "UNKNOWN",
}
PLATFORM_CODE_MAP = {
    "抖音": "Douyin",
    "小红书": "XiaoHongShu",
    "视频号": "Channels",
    "哔哩哔哩": "Bili",
    "B站": "Bili",
    "公众号": "GongZhongHao",
}
PLATFORM_CN_MAP = {
    "Douyin": "抖音",
    "XiaoHongShu": "小红书",
    "Channels": "视频号",
    "Bili": "哔哩哔哩",
    "GongZhongHao": "公众号",
}


def _to_shanghai_schedule(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")


def _shanghai_day_of(utc_iso: str) -> str:
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SHANGHAI_TZ).date().isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _node_dict(n: ExecutorNode) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    effective_status = n.status
    if n.status == "revoked":
        effective_status = "revoked"
    elif n.transport_mode == "agent_relay" and (not n.last_heartbeat_at or n.last_heartbeat_at < cutoff):
        effective_status = "offline"
    return {
        "id": n.id,
        "name": n.name,
        "node_scope": n.node_scope,
        "owner_user_id": n.owner_user_id,
        "transport_mode": n.transport_mode,
        "mcp_url": n.mcp_url,
        "token_configured": bool(n.token_encrypted),
        "status": effective_status,
        "revoked_at": n.revoked_at,
        "vcat_version": n.vcat_version,
        "last_heartbeat_at": n.last_heartbeat_at,
        "capabilities": n.capabilities_json or {},
        "updated_at": n.updated_at,
    }


def _account_dict(a: PlatformAccount, node: ExecutorNode | None = None) -> dict[str, Any]:
    return {
        "id": a.id,
        "node_id": a.node_id,
        "node_scope": node.node_scope if node else None,
        "node_name": node.name if node else None,
        "vendor_uid": a.vendor_uid,
        "platform": a.platform,
        "platform_name": a.platform_name or PLATFORM_CN_MAP.get(a.platform, a.platform),
        "nickname": a.nickname,
        "avatar": a.avatar,
        "team_id": a.team_id,
        "ip_id": a.ip_id,
        "login_status": a.login_status,
        "is_verified": a.is_verified,
        "last_verified_at": a.last_verified_at,
        "verify_summary": a.verify_summary_json or {},
        "active": a.active,
        "removed_at": a.removed_at,
        "updated_at": a.updated_at,
    }


def _attempt_dict(att: PublishAttempt, target: PublishTarget | None = None, acc: PlatformAccount | None = None) -> dict[str, Any]:
    snap = (target.approved_snapshot_json or {}) if target else {}
    return {
        "id": att.id,
        "target_id": att.target_id,
        "schedule_id": target.schedule_id if target else None,
        "ip_id": target.ip_id if target else None,
        "draft_id": target.draft_id if target else None,
        "draft_version": target.draft_version if target else None,
        "account_id": target.account_id if target else None,
        "account_nickname": acc.nickname if acc else None,
        "platform": target.platform if target else None,
        "content_type": target.content_type if target else None,
        "schedule_owner": target.schedule_owner if target else None,
        "execution_mode": target.execution_mode if target else None,
        "planned_at_utc": target.planned_at_utc if target else None,
        "title": snap.get("title") if target else None,
        "video_asset_id": snap.get("video_asset_id"),
        "cover_asset_id": snap.get("cover_asset_id"),
        "attempt_no": att.attempt_no,
        "idempotency_key": att.idempotency_key,
        "state": att.state,
        "node_id": att.node_id,
        "submit_started_at": att.submit_started_at,
        "vendor_local_id": att.vendor_local_id,
        "vendor_draft_id": att.vendor_draft_id,
        "vendor_batch_name": att.vendor_batch_name,
        "platform_post_id": att.platform_post_id,
        "evidence_url": att.evidence_url,
        "remote_status": att.remote_status,
        "verification_status": att.verification_status or "pending",
        "verification_detail": att.verification_detail,
        "last_verified_at": att.last_verified_at,
        "verify_attempts": att.verify_attempts or 0,
        "error_class": att.error_class,
        "error_message": att.error_message,
        "version": att.version,
        "updated_at": att.updated_at,
    }


def _extract_remote_error(item: dict[str, Any]) -> str:
    keys = (
        "error",
        "errorMsg",
        "errorMessage",
        "errMsg",
        "failReason",
        "rejectReason",
        "reason",
        "statusDesc",
        "resultMsg",
        "message",
        "msg",
        "detail",
    )
    for k in keys:
        val = item.get(k)
        if val is None:
            continue
        if isinstance(val, str):
            clean = val.strip()
            if clean:
                return clean
        elif isinstance(val, dict):
            nested = _extract_remote_error(val)
            if nested:
                return nested
            return json.dumps(val, ensure_ascii=False)
        elif isinstance(val, list):
            parts = [str(x).strip() for x in val if x is not None and str(x).strip()]
            if parts:
                return "；".join(parts)
        else:
            s = str(val).strip()
            if s:
                return s
    return ""


def _match_remote_publish_task(
    tasks: list[dict[str, Any]],
    att: PublishAttempt,
    target: PublishTarget | None,
) -> dict[str, Any] | None:
    if att.vendor_local_id:
        lid = str(att.vendor_local_id)
        for item in tasks:
            if not isinstance(item, dict):
                continue
            cand = item.get("localId") or item.get("id") or item.get("taskId")
            if cand is not None and str(cand) == lid:
                return item
    if att.vendor_batch_name:
        bname = str(att.vendor_batch_name)
        for item in tasks:
            if not isinstance(item, dict):
                continue
            cand = item.get("batchSource") or item.get("batchName")
            if cand is not None and str(cand) == bname:
                return item
    if not att.vendor_local_id and target:
        title = str((target.approved_snapshot_json or {}).get("title") or "").strip()
        if title:
            for item in tasks:
                if not isinstance(item, dict):
                    continue
                if str(item.get("title") or "").strip() == title:
                    return item
    return None


def _apply_remote_publish_task(
    att: PublishAttempt,
    target: PublishTarget,
    matched: dict[str, Any],
) -> None:
    st = str(
        matched.get("status")
        or matched.get("statusText")
        or matched.get("state")
        or matched.get("publishStatus")
        or ""
    ).strip()
    st_low = st.lower()
    post_id = (
        matched.get("postId")
        or matched.get("workId")
        or matched.get("exportId")
        or matched.get("feedId")
        or matched.get("itemId")
    )
    url = (
        matched.get("url")
        or matched.get("workUrl")
        or matched.get("publishUrl")
        or matched.get("shareUrl")
    )
    err_text = _extract_remote_error(matched)
    cand_local_id = matched.get("localId") or matched.get("id") or matched.get("taskId")
    if not att.vendor_local_id and cand_local_id:
        att.vendor_local_id = str(cand_local_id)

    att.remote_status = st or "unknown"
    att.last_verified_at = now()
    att.verify_attempts = (att.verify_attempts or 0) + 1

    if "cancel" in st_low or "取消" in st:
        att.state = "CANCELLED"
        target.status = "CANCELLED"
        att.verification_status = "confirmed_failed"
        att.verification_detail = "平台/小V猫发布任务已取消"
        return

    if (
        any(k in st_low for k in ("fail", "reject", "error"))
        or any(k in st for k in ("失败", "拒绝", "驳回", "违规", "异常", "不通过"))
        or (not st_low and bool(err_text))
    ):
        reason = err_text or "平台返回发布失败（请检查视频格式、标题文案或账号登录状态）"
        att.state = "REJECTED"
        target.status = "REJECTED"
        att.error_class = "PLATFORM_REJECTED"
        att.error_message = reason
        att.verification_status = "confirmed_failed"
        att.verification_detail = f"平台确认发布失败：{reason}"
        return

    if any(k in st_low for k in ("success", "published", "succeed")) or any(k in st for k in ("成功", "已发布")):
        if target.schedule_owner == "platform" and target.planned_at_utc > now() and not post_id and not url:
            att.state = "PLATFORM_SCHEDULED"
            target.status = "PLATFORM_SCHEDULED"
            att.verification_status = "confirmed_success"
            att.verification_detail = "已确认平台定时发布任务创建成功"
        else:
            att.state = "PUBLISHED"
            target.status = "PUBLISHED"
            if post_id:
                att.platform_post_id = str(post_id)
            if url:
                att.evidence_url = str(url)
            att.error_class = None
            att.error_message = None
            att.verification_status = "confirmed_success"
            att.verification_detail = "已查询确认发布成功"
        return

    if "review" in st_low or "audit" in st_low or "审核" in st:
        att.state = "REVIEWING"
        target.status = "REVIEWING"
        att.verification_status = "verifying"
        att.verification_detail = "视频已上传，平台审核中，系统持续查询确认结果"
        return

    if "localtiming" in st_low or "timing" in st_low or "定时" in st:
        att.state = "PLATFORM_SCHEDULED"
        target.status = "PLATFORM_SCHEDULED"
        att.verification_status = "confirmed_success"
        att.verification_detail = "已进入定时发布队列，等待到点发布"
        return

    if "pause" in st_low or "暂停" in st:
        att.state = "ACCEPTED"
        target.status = "ACCEPTED"
        att.verification_status = "verifying"
        att.verification_detail = "小V猫发布队列已暂停，请在小V猫客户端恢复"
        return

    if (
        "publish" in st_low
        or "upload" in st_low
        or "process" in st_low
        or "run" in st_low
        or "发布中" in st
        or "上传中" in st
    ):
        att.state = "PUBLISHING"
        target.status = "PUBLISHING"
        att.verification_status = "verifying"
        att.verification_detail = "正在上传视频并向平台提交，等待平台答复..."
        return

    att.state = "ACCEPTED"
    target.status = "ACCEPTED"
    att.verification_status = "verifying"
    att.verification_detail = "任务已进入小V猫发布队列，等待执行上传与平台确认..."


class VcatOperationsService:
    """Single business service shared by API routes and independent publish worker."""

    def __init__(self, app) -> None:
        self.app = app
        self.Session = app.state.Session
        self.router = TransportRouter(app)

    def _settings(self, db) -> dict[str, Any]:
        row = db.get(Record, "system-settings")
        return row.payload if row else {}

    def _log_action(
        self,
        db,
        *,
        actor_id: str | None,
        node_id: str | None,
        account_id: str | None,
        action_type: str,
        tool_name: str,
        arguments: dict[str, Any],
        res: VcatActionResult,
        duration_ms: int,
    ) -> None:
        db.add(
            VcatActionLog(
                id=uid(),
                actor_id=actor_id,
                node_id=node_id,
                account_id=account_id,
                action_type=action_type,
                tool_name=tool_name,
                request_summary_json=sanitize_summary(arguments),
                result_status=res.status,
                error_message=sanitize_summary(res.error_message) if res.error_message else None,
                duration_ms=duration_ms,
            )
        )

    def _execute_mcp(
        self,
        *,
        actor: User | None,
        node_id: str | None,
        account_id: str | None,
        action_type: str,
        tool_name: str,
        arguments: dict[str, Any],
        media_refs: list[MediaRef] | None = None,
        requires_rate_limit: bool = True,
        sync_wait_sec: float = 20.0,
        timeout_sec: int = 60,
        action_id: str | None = None,
    ) -> tuple[ExecutorNode, VcatActionResult]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                node = self.router.resolve_node(db, node_id=node_id, actor=actor)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            transport = self.router.transport_for(db, node, sync_wait_sec=sync_wait_sec)
            resolved_node_id = node.id
            db.commit()

        cmd = VcatActionCommand(
            action_id=action_id or uid(),
            node_id=resolved_node_id,
            tool_name=tool_name,
            arguments=arguments,
            media_refs=media_refs or [],
            timeout_sec=timeout_sec,
            requires_rate_limit=requires_rate_limit,
        )
        t0 = time.monotonic()
        res = transport.execute(cmd)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            node_row = db.get(ExecutorNode, resolved_node_id)
            if node_row and node_row.transport_mode == "direct_http":
                node_row.status = "offline" if res.status == "NODE_OFFLINE" else "online"
            self._log_action(
                db,
                actor_id=actor.id if actor else None,
                node_id=resolved_node_id,
                account_id=account_id,
                action_type=action_type,
                tool_name=tool_name,
                arguments=arguments,
                res=res,
                duration_ms=elapsed_ms,
            )
            db.commit()
            return node_row or node, res

    # ---------- Nodes & Pairing ----------

    def list_nodes(self, actor: User) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            self.router.ensure_direct_node(db)
            settings = self._settings(db)
            ex = settings.get("executor", {})
            rows = list(db.scalars(select(ExecutorNode).order_by(ExecutorNode.created_at.desc())))
            db.commit()

        visible_nodes = []
        for n in rows:
            if actor.role == "admin" or (n.node_scope == "client_local" and n.owner_user_id == actor.id):
                visible_nodes.append(_node_dict(n))
        return {
            "mode": ex.get("mode", "manual"),
            "allow_client_local": bool(ex.get("allow_client_local", True)),
            "allow_interaction_write": bool(ex.get("allow_interaction_write", False)),
            "catchup_minutes": int(ex.get("catchup_minutes", 30)),
            "preflight_minutes": int(ex.get("preflight_minutes", 30)),
            "nodes": visible_nodes,
        }

    def create_pair_code(self, actor: User, node_scope: str, label: str = "") -> dict[str, Any]:
        if node_scope not in ("server_relay", "client_local"):
            raise HTTPException(422, "配对节点类型仅支持 server_relay 或 client_local")
        with self.Session() as db:
            ex = self._settings(db).get("executor", {})
            if node_scope == "server_relay" and actor.role != "admin":
                raise HTTPException(403, "仅管理员可创建服务器执行机配对码")
            if node_scope == "client_local" and not ex.get("allow_client_local", True) and actor.role != "admin":
                raise HTTPException(403, "管理员未启用前端本机小V猫模式")

            raw_code = "vcat_pair_" + secrets.token_urlsafe(18)
            expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = AgentPairCode(
                id=uid(),
                code_hash=hashlib.sha256(raw_code.encode()).hexdigest(),
                node_scope=node_scope,
                owner_user_id=actor.id if node_scope == "client_local" else None,
                label=(label or ("前端本机代理" if node_scope == "client_local" else "托管执行机"))[:60],
                expires_at=expires,
                created_by=actor.id,
            )
            db.add(row)
            db.commit()
        agent_mode = "client_local" if node_scope == "client_local" else "server_managed"
        return {
            "pair_code": raw_code,
            "node_scope": node_scope,
            "expires_at": expires,
            "command_hint": f"python3 executor/vcat_agent.py --mode {agent_mode} --server <获客空间URL> --pair-token {raw_code}",
        }

    def probe_node(self, actor: User, node_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                node = self.router.resolve_node(db, node_id=node_id, actor=actor)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            db.commit()
            if node.transport_mode == "direct_http":
                token = self.app.state.secrets.decrypt(node.token_encrypted or "")
                if not token:
                    raise HTTPException(422, "节点未配置 MCP 访问令牌")
                client = StreamableHttpMcpClient(node.mcp_url or "http://127.0.0.1:3199/mcp", token)
                try:
                    tools = client.list_tools(timeout_sec=10)
                except Exception as exc:
                    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    n = db.get(ExecutorNode, node.id)
                    if n:
                        n.status = "mcp_error"
                        n.updated_at = now()
                    db.commit()
                    raise HTTPException(502, f"探测小V猫 MCP 失败: {exc}") from None
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                n = db.get(ExecutorNode, node.id)
                n.status = "online"
                n.last_heartbeat_at = now()
                n.vcat_version = "3.1.0"
                n.capabilities_json = {"tool_count": len(tools), "tool_names": [t.get("name") for t in tools[:30]]}
                db.commit()
                return {"node": _node_dict(n), "tool_count": len(tools)}

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="probe_node",
            tool_name="vcat_list_accounts",
            arguments={"page": 1, "pageSize": 1},
            requires_rate_limit=False,
        )
        with self.Session() as db:
            n = db.get(ExecutorNode, node_id)
            return {"node": _node_dict(n) if n else None, "ok": res.ok, "error": res.error_message}

    def _can_manage_node(self, actor: User, node: ExecutorNode) -> bool:
        return actor.role == "admin" or (node.node_scope == "client_local" and node.owner_user_id == actor.id)

    def revoke_node(self, actor: User, node_id: str) -> dict[str, Any]:
        if node_id == "server-direct-default":
            raise HTTPException(422, "内置直连节点不可吊销，请通过「系统设置 → 发布执行器」切换模式停用")
        from ..db import AgentCommand

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            node = db.get(ExecutorNode, node_id)
            if not node:
                raise HTTPException(404, "执行节点不存在")
            if not self._can_manage_node(actor, node):
                raise HTTPException(403, "无权吊销该执行节点")
            node.secret_hash = None
            node.status = "revoked"
            node.revoked_at = now()
            node.updated_at = now()
            for cmd in db.scalars(
                select(AgentCommand).where(AgentCommand.node_id == node.id, AgentCommand.status.in_(("queued", "leased")))
            ):
                cmd.status = "expired"
                cmd.finished_at = now()
                cmd.updated_at = now()
            for att in db.scalars(
                select(PublishAttempt).where(PublishAttempt.node_id == node.id, PublishAttempt.state == "QUEUED")
            ):
                att.state = "WAITING_CLIENT_ONLINE"
                att.error_class = "NODE_REVOKED"
                att.error_message = "执行节点已吊销，请重新配对或改派账号"
                att.updated_at = now()
                t = db.get(PublishTarget, att.target_id)
                if t:
                    t.status = "WAITING_CLIENT_ONLINE"
            self._log_action(
                db,
                actor_id=actor.id,
                node_id=node.id,
                account_id=None,
                action_type="revoke_node",
                tool_name="local_node_revoke",
                arguments={"node_id": node.id},
                res=VcatActionResult(action_id=uid(), ok=True, status="SUCCEEDED", payload={"revoked": True}),
                duration_ms=0,
            )
            db.commit()
            return _node_dict(node)

    def delete_node(self, actor: User, node_id: str) -> dict[str, Any]:
        if node_id == "server-direct-default":
            raise HTTPException(422, "内置直连节点不可移除")
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            node = db.get(ExecutorNode, node_id)
            if not node:
                raise HTTPException(404, "执行节点不存在")
            if not self._can_manage_node(actor, node):
                raise HTTPException(403, "无权移除该执行节点")
            eff = _node_dict(node)["status"]
            if eff not in ("revoked", "offline"):
                raise HTTPException(409, "在线节点请先执行「吊销下线」后再移除")
            active_accs = list(
                db.scalars(select(PlatformAccount).where(PlatformAccount.node_id == node.id, PlatformAccount.active.is_(True)))
            )
            if active_accs:
                raise HTTPException(409, f"该节点下仍有 {len(active_accs)} 个活跃绑定账号，请先移除账号后再删节点")
            self._log_action(
                db,
                actor_id=actor.id,
                node_id=node.id,
                account_id=None,
                action_type="delete_node",
                tool_name="local_node_delete",
                arguments={"node_id": node.id},
                res=VcatActionResult(action_id=uid(), ok=True, status="SUCCEEDED", payload={"deleted": True}),
                duration_ms=0,
            )
            db.delete(node)
            db.commit()
            return {"ok": True, "deleted_node_id": node_id}

    # ---------- Accounts & Binding ----------

    def _can_access_account(self, db, actor: User, acc: PlatformAccount, node: ExecutorNode | None) -> bool:
        if actor.role == "admin":
            return True
        if acc.ip_id:
            return can_ip(db, actor, acc.ip_id)
        if node and node.node_scope == "client_local" and node.owner_user_id == actor.id:
            return True
        if acc.bound_by == actor.id:
            return True
        return False

    def list_accounts(self, actor: User, ip_id: str = "", include_removed: bool = False) -> list[dict[str, Any]]:
        with self.Session() as db:
            if ip_id:
                require_ip(db, actor, ip_id)
            nodes = {n.id: n for n in db.scalars(select(ExecutorNode))}
            stmt = select(PlatformAccount)
            if not include_removed:
                stmt = stmt.where(PlatformAccount.active.is_(True))
            rows = list(db.scalars(stmt.order_by(PlatformAccount.updated_at.desc())))
            result = []
            for a in rows:
                if ip_id and a.ip_id != ip_id:
                    continue
                if self._can_access_account(db, actor, a, nodes.get(a.node_id)):
                    result.append(_account_dict(a, nodes.get(a.node_id)))
            return result

    def sync_accounts(self, actor: User, node_id: str | None = None) -> dict[str, Any]:
        node, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="sync_accounts",
            tool_name="vcat_list_accounts",
            arguments={"page": 1, "pageSize": 100},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "同步账号失败")
        items = []
        if isinstance(res.payload, dict):
            items = res.payload.get("list") or res.payload.get("accounts") or []
        returned_uids: set[str] = set()
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            synced = 0
            for raw in items:
                if not isinstance(raw, dict) or not raw.get("uid"):
                    continue
                v_uid = str(raw["uid"])
                returned_uids.add(v_uid)
                existing = db.scalar(
                    select(PlatformAccount).where(
                        PlatformAccount.node_id == node.id, PlatformAccount.vendor_uid == v_uid
                    )
                )
                plat_code = str(raw.get("platform") or "")
                plat_name = str(raw.get("platformName") or PLATFORM_CN_MAP.get(plat_code, plat_code))
                if existing:
                    existing.platform = plat_code or existing.platform
                    existing.platform_name = plat_name or existing.platform_name
                    existing.nickname = str(raw.get("nickname") or existing.nickname)
                    existing.avatar = raw.get("avatar") or existing.avatar
                    existing.login_status = str(raw.get("loginStatus") or existing.login_status)
                    existing.active = True
                    existing.removed_at = None
                    existing.updated_at = now()
                else:
                    db.add(
                        PlatformAccount(
                            id=uid(),
                            node_id=node.id,
                            vendor_uid=v_uid,
                            platform=plat_code,
                            platform_name=plat_name,
                            nickname=str(raw.get("nickname") or v_uid),
                            avatar=raw.get("avatar"),
                            login_status=str(raw.get("loginStatus") or "未知"),
                            bound_by=actor.id if node.node_scope == "client_local" else None,
                        )
                    )
                synced += 1

            removed_count = 0
            total_remote = res.payload.get("total") if isinstance(res.payload, dict) else None
            is_complete = isinstance(items, list) and len(items) < 100 and (total_remote is None or int(total_remote) <= len(items))
            if is_complete:
                for stale in db.scalars(
                    select(PlatformAccount).where(PlatformAccount.node_id == node.id, PlatformAccount.active.is_(True))
                ):
                    if stale.vendor_uid not in returned_uids:
                        stale.active = False
                        stale.removed_at = now()
                        stale.login_status = "已在小V猫移除"
                        stale.ip_id = None
                        stale.team_id = None
                        stale.updated_at = now()
                        removed_count += 1
            db.commit()
        return {"synced": synced, "removed_count": removed_count, "accounts": self.list_accounts(actor)}

    def bind_account(self, actor: User, account_id: str, ip_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            acc = db.get(PlatformAccount, account_id)
            if not acc or not acc.active:
                raise HTTPException(404, "平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if actor.role not in ("admin", "leader") and not (node and node.node_scope == "client_local" and node.owner_user_id == actor.id):
                raise HTTPException(403, "仅管理员、团队长或本机节点所有者可绑定账号归属")
            require_ip(db, actor, ip_id)
            ip = db.get(Record, ip_id)
            if not active_ip(ip):
                raise HTTPException(404, "IP不存在")
            acc.ip_id = ip_id
            acc.team_id = ip.payload.get("team_id")
            acc.bound_by = actor.id
            acc.updated_at = now()
            db.commit()
            return _account_dict(acc, node)

    def unbind_account(self, actor: User, account_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            acc = db.get(PlatformAccount, account_id)
            if not acc or not acc.active:
                raise HTTPException(404, "平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if actor.role not in ("admin", "leader") and not (node and node.node_scope == "client_local" and node.owner_user_id == actor.id):
                raise HTTPException(403, "仅管理员、团队长或本机节点所有者可解绑账号归属")
            acc.ip_id = None
            acc.team_id = None
            acc.updated_at = now()
            self._log_action(
                db,
                actor_id=actor.id,
                node_id=acc.node_id,
                account_id=acc.id,
                action_type="unbind_account",
                tool_name="local_account_unbind",
                arguments={"account_id": acc.id},
                res=VcatActionResult(action_id=uid(), ok=True, status="SUCCEEDED", payload={"unbound": True}),
                duration_ms=0,
            )
            db.commit()
            return _account_dict(acc, node)

    def remove_account(
        self,
        actor: User,
        account_id: str,
        *,
        confirm: bool = False,
        confirm_nickname: str = "",
        cancel_pending: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(422, "从小V猫移除账号必须显式确认 confirm=true")
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            acc = db.get(PlatformAccount, account_id)
            if not acc:
                raise HTTPException(404, "平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if actor.role != "admin" and not (node and node.node_scope == "client_local" and node.owner_user_id == actor.id):
                raise HTTPException(403, "仅管理员或本机节点所有者可从小V猫移除账号")
            if not acc.active:
                raise HTTPException(409, "该账号已从小V猫移除")
            if (confirm_nickname or "").strip() != acc.nickname.strip():
                raise HTTPException(422, "确认昵称与账号昵称不一致，已阻止移除")

            target_ids = [t.id for t in db.scalars(select(PublishTarget).where(PublishTarget.account_id == acc.id))]
            if target_ids:
                active_attempts = list(db.scalars(select(PublishAttempt).where(PublishAttempt.target_id.in_(target_ids))))
                in_flight = [a for a in active_attempts if a.state in ("SUBMITTING", "ACCEPTED", "PUBLISHING", "REVIEWING", "RECONCILING")]
                if in_flight:
                    raise HTTPException(409, f"账号仍有 {len(in_flight)} 条正在提交或已受理的发布任务，请先等待完成或取消")
                pending = [a for a in active_attempts if a.state in ("QUEUED", "WAITING_CLIENT_ONLINE")]
                if pending and not cancel_pending:
                    raise HTTPException(409, f"账号仍有 {len(pending)} 条待发任务，请先取消或勾选「同时取消待发任务」")
                for a in pending:
                    a.state = "CANCELLED"
                    a.error_class = "ACCOUNT_REMOVED"
                    a.error_message = "从小V猫移除账号时联动取消待发任务"
                    a.updated_at = now()
                    t = db.get(PublishTarget, a.target_id)
                    if t:
                        t.status = "CANCELLED"
            vendor_uid = acc.vendor_uid
            node_id = acc.node_id
            db.commit()

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="remove_account",
            tool_name="vcat_remove_account",
            arguments={"uid": vendor_uid, "onlyRemoveFromSubUser": False},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "从小V猫移除账号失败")

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            acc = db.get(PlatformAccount, account_id)
            node = db.get(ExecutorNode, acc.node_id)
            acc.active = False
            acc.removed_at = now()
            acc.login_status = "已从小V猫移除"
            acc.ip_id = None
            acc.team_id = None
            acc.updated_at = now()
            db.commit()
            return _account_dict(acc, node)

    # ---------- Auth Challenges & Verification ----------

    def _challenge_dict(self, c: AuthChallenge, *, include_url: bool = False) -> dict[str, Any]:
        d = {
            "id": c.id,
            "node_id": c.node_id,
            "account_id": c.account_id,
            "target_id": c.target_id,
            "platform": c.platform,
            "platform_name": PLATFORM_CN_MAP.get(c.platform, c.platform),
            "challenge_type": c.challenge_type,
            "status": c.status,
            "vendor_link_id": c.vendor_link_id,
            "expires_at": c.expires_at,
            "created_by": c.created_by,
            "created_at": c.created_at,
            "qr_endpoint": f"/api/ops/auth-challenges/{c.id}/qr",
        }
        if include_url and c.encrypted_url and c.status not in ("EXPIRED", "CANCELLED"):
            d["iframe_url"] = self.app.state.secrets.decrypt(c.encrypted_url)
        return d

    def create_login_link_challenge(self, actor: User, platform: str, node_id: str | None = None) -> dict[str, Any]:
        plat_code = PLATFORM_CODE_MAP.get(platform, platform)
        if plat_code == "GongZhongHao":
            raise HTTPException(422, "公众号暂不支持小V猫上号链接，请在系统设置→微信生态配置公众号凭证")
        node, create_res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="create_login_link",
            tool_name="vcat_create_login_link",
            arguments={"platform": platform},
            requires_rate_limit=True,
        )
        if not create_res.ok:
            raise HTTPException(502, create_res.error_message or "生成上号链接失败")

        _, list_res = self._execute_mcp(
            actor=actor,
            node_id=node.id,
            account_id=None,
            action_type="list_login_links",
            tool_name="vcat_list_login_links",
            arguments={},
            requires_rate_limit=False,
        )
        if not list_res.ok or not isinstance(list_res.payload, dict):
            raise HTTPException(502, list_res.error_message or "读取上号链接列表失败")
        items = list_res.payload.get("list") or []
        matched = [
            x
            for x in items
            if isinstance(x, dict) and x.get("url") and (x.get("platform") == plat_code or x.get("platformName") == platform)
        ]
        if not matched:
            raise HTTPException(502, "已创建上号链接但未能从列表回读链接地址")
        matched.sort(key=lambda x: (str(x.get("createTime") or ""), int(x.get("id") or 0)), reverse=True)
        chosen = matched[0]
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            ch = AuthChallenge(
                id=uid(),
                node_id=node.id,
                account_id=None,
                platform=plat_code,
                challenge_type="new_account_iframe",
                status="WAITING_SCAN",
                vendor_link_id=str(chosen.get("id") or ""),
                encrypted_url=self.app.state.secrets.encrypt(str(chosen["url"])),
                expires_at=expires,
                created_by=actor.id,
            )
            db.add(ch)
            db.commit()
            return self._challenge_dict(ch, include_url=True)

    def create_verify_link_challenge(self, actor: User, account_id: str, target_id: str | None = None) -> dict[str, Any]:
        with self.Session() as db:
            acc = db.get(PlatformAccount, account_id)
            if not acc:
                raise HTTPException(404, "平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的访问权限")
            if acc.platform != "Channels":
                raise HTTPException(422, f"{acc.platform_name} 不支持云端扫脸验证链接，当前仅视频号支持")
            vendor_uid = acc.vendor_uid
            node_id = acc.node_id

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="get_verify_links",
            tool_name="vcat_get_verify_links",
            arguments={"uids": [vendor_uid]},
            requires_rate_limit=False,
        )
        if not res.ok or not isinstance(res.payload, dict):
            raise HTTPException(502, res.error_message or "获取视频号验证链接失败")
        entries = res.payload.get("list") or []
        entry = entries[0] if entries else None
        if not entry or not entry.get("url"):
            raise HTTPException(502, "未返回有效的视频号验证链接")

        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            ch = AuthChallenge(
                id=uid(),
                node_id=node_id,
                account_id=account_id,
                target_id=target_id,
                platform="Channels",
                challenge_type="channels_face_verify",
                status="WAITING_SCAN",
                encrypted_url=self.app.state.secrets.encrypt(str(entry["url"])),
                expires_at=expires,
                created_by=actor.id,
            )
            db.add(ch)
            db.commit()
            return self._challenge_dict(ch, include_url=False)

    def list_challenges(self, actor: User) -> list[dict[str, Any]]:
        with self.Session() as db:
            accounts = {a.id: a for a in db.scalars(select(PlatformAccount))}
            nodes = {n.id: n for n in db.scalars(select(ExecutorNode))}
            rows = list(db.scalars(select(AuthChallenge).order_by(AuthChallenge.created_at.desc()).limit(50)))
            result = []
            for c in rows:
                acc = accounts.get(c.account_id) if c.account_id else None
                node = nodes.get(c.node_id)
                allowed = False
                if actor.role == "admin" or c.created_by == actor.id:
                    allowed = True
                elif acc and self._can_access_account(db, actor, acc, node):
                    allowed = True
                if allowed:
                    result.append(self._challenge_dict(c, include_url=(c.challenge_type == "new_account_iframe")))
            return result

    def challenge_qr_png(self, actor: User, challenge_id: str) -> bytes:
        with self.Session() as db:
            c = db.get(AuthChallenge, challenge_id)
            if not c:
                raise HTTPException(404, "授权挑战不存在")
            acc = db.get(PlatformAccount, c.account_id) if c.account_id else None
            node = db.get(ExecutorNode, c.node_id)
            allowed = actor.role == "admin" or c.created_by == actor.id or (acc and self._can_access_account(db, actor, acc, node))
            if not allowed:
                raise HTTPException(403, "无权访问此授权二维码")
            if c.expires_at < now() or c.status in ("EXPIRED", "CANCELLED"):
                raise HTTPException(409, "授权链接已过期，请重新生成")
            url = self.app.state.secrets.decrypt(c.encrypted_url or "")
            if not url:
                raise HTTPException(404, "授权链接缺失")
        return render_qr_png(url, scale=8, border=4)

    def _cleanup_login_link(self, actor: User, c: AuthChallenge) -> bool:
        if c.challenge_type != "new_account_iframe" or not c.vendor_link_id:
            return False
        lid: Any = int(c.vendor_link_id) if str(c.vendor_link_id).isdigit() else str(c.vendor_link_id)
        try:
            _, res = self._execute_mcp(
                actor=actor,
                node_id=c.node_id,
                account_id=c.account_id,
                action_type="delete_login_link",
                tool_name="vcat_delete_login_link",
                arguments={"id": lid},
                requires_rate_limit=False,
            )
            return res.ok
        except Exception:
            return False

    def complete_challenge(self, actor: User, challenge_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            c = db.get(AuthChallenge, challenge_id)
            if not c:
                raise HTTPException(404, "授权挑战不存在")
            if actor.role != "admin" and c.created_by != actor.id:
                acc = db.get(PlatformAccount, c.account_id) if c.account_id else None
                node = db.get(ExecutorNode, c.node_id)
                if not (acc and self._can_access_account(db, actor, acc, node)):
                    raise HTTPException(403, "无权更新此挑战")
            c.status = "VERIFIED"
            c.completed_by = actor.id
            c.updated_at = now()
            db.commit()
        self._cleanup_login_link(actor, c)
        with self.Session() as db:
            return self._challenge_dict(db.get(AuthChallenge, challenge_id), include_url=False)

    def cancel_challenge(self, actor: User, challenge_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            c = db.get(AuthChallenge, challenge_id)
            if not c:
                raise HTTPException(404, "授权挑战不存在")
            if actor.role != "admin" and c.created_by != actor.id:
                acc = db.get(PlatformAccount, c.account_id) if c.account_id else None
                node = db.get(ExecutorNode, c.node_id)
                if not (acc and self._can_access_account(db, actor, acc, node)):
                    raise HTTPException(403, "无权取消此挑战")
            if c.status == "VERIFIED":
                raise HTTPException(409, "已完成验证的待办不可取消")
            c.status = "CANCELLED"
            c.completed_by = actor.id
            c.updated_at = now()
            db.commit()
        link_deleted = self._cleanup_login_link(actor, c)
        with self.Session() as db:
            d = self._challenge_dict(db.get(AuthChallenge, challenge_id), include_url=False)
            d["link_deleted"] = link_deleted
            return d

    def verify_account(self, actor: User, account_id: str) -> dict[str, Any]:
        with self.Session() as db:
            acc = db.get(PlatformAccount, account_id)
            if not acc:
                raise HTTPException(404, "平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的访问权限")
            if acc.platform != "Channels":
                raise HTTPException(422, f"{acc.platform_name} 暂不支持账号诊断，当前仅视频号支持")
            vendor_uid = acc.vendor_uid
            node_id = acc.node_id

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="verify_account",
            tool_name="vcat_verify_account",
            arguments={"uid": vendor_uid, "closeFace": False},
            requires_rate_limit=True,
        )
        if not res.ok or not isinstance(res.payload, dict):
            raise HTTPException(502, res.error_message or "账号诊断失败")
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            acc = db.get(PlatformAccount, account_id)
            acc.is_verified = bool(res.payload.get("verified", True))
            acc.last_verified_at = now()
            acc.verify_summary_json = res.payload
            acc.updated_at = now()
            db.commit()
            return _account_dict(acc, db.get(ExecutorNode, acc.node_id))

    # ---------- Publish Options, Dispatch & Attempts ----------

    def get_publish_options(self, actor: User, platform: str) -> dict[str, Any]:
        plat_code = PLATFORM_CODE_MAP.get(platform, platform)
        if plat_code == "GongZhongHao":
            return {
                "platform": "GongZhongHao",
                "platformName": "公众号",
                "mcp_supported": False,
                "first_phase_mode": "draft_box_only",
                "fields": [
                    {"name": "title", "description": "文章/草稿标题（最多64字）"},
                    {"name": "content", "description": "正文内容（将推送至公众号草稿箱，状态标记为草稿态）"},
                ],
                "notes": [
                    "实测小V猫 3.1.0 MCP 暂不支持公众号发布。",
                    "首期仅推送到公众号草稿箱并标为 DRAFT_PUSHED，由人工在微信公众平台后台群发并回填公开链接。",
                ],
            }
        _, res = self._execute_mcp(
            actor=actor,
            node_id=None,
            account_id=None,
            action_type="get_publish_options",
            tool_name="vcat_get_publish_options",
            arguments={"platform": platform},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "查询平台发布参数失败")
        return {"mcp_supported": True, **(res.payload if isinstance(res.payload, dict) else {"raw": res.payload})}

    def _validate_draft_ready(self, db, draft: Record, expected_version: int) -> str | None:
        if not draft or draft.kind != "drafts":
            return "稿件不存在"
        p = draft.payload
        if p.get("approved_version") != expected_version or p.get("version") != expected_version:
            return "稿件批准版本已变更或失效，请重新审核"
        rules = self._settings(db).get("rules", {})
        if any(not c["passed"] and c["blocking"] for c in draft_checks(db, p, rules)):
            return "稿件不再满足审稿阻塞规则"
        for src_id in set(p.get("source_ids", [])) | {x["id"] for x in p.get("sources", [])}:
            src = db.get(Record, src_id)
            if not verified_document(src):
                return "引用知识已失效或过期"
        return None

    def _check_daily_limit(self, db, account_id: str, planned_at_utc: str, *, exclude_attempt_id: str | None = None) -> None:
        target_day = _shanghai_day_of(planned_at_utc)
        targets = {t.id: t for t in db.scalars(select(PublishTarget).where(PublishTarget.account_id == account_id))}
        if not targets:
            return
        used = 0
        for att in db.scalars(select(PublishAttempt).where(PublishAttempt.target_id.in_(list(targets.keys())))):
            if exclude_attempt_id and att.id == exclude_attempt_id:
                continue
            t = targets[att.target_id]
            if _shanghai_day_of(t.planned_at_utc) == target_day and att.state in ACTIVE_DAILY_ATTEMPT_STATES:
                used += 1
        if used >= DAILY_LIMIT_PER_ACCOUNT:
            raise HTTPException(409, f"该账号在 {target_day}（北京时间）已达每日 {DAILY_LIMIT_PER_ACCOUNT} 条发布上限")

    def dispatch_schedule(
        self,
        actor: User,
        schedule_id: str,
        *,
        account_id: str,
        mode: str = "publish",
        content_type: str = "video",
        schedule_owner: str = "server",
        platform_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode not in ("draft", "publish"):
            raise HTTPException(422, "执行模式仅支持 draft 或 publish")
        if schedule_owner not in ("server", "platform"):
            raise HTTPException(422, "定时归属仅支持 server 或 platform")

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            sched = db.get(Record, schedule_id)
            if not sched or sched.kind != "schedules":
                raise HTTPException(404, "发布排期不存在")
            require_ip(db, actor, sched.ip_id)
            sp = sched.payload
            if sp.get("status") in ("cancelled", "needs_review"):
                raise HTTPException(409, "当前排期状态不允许派发")

            acc = db.get(PlatformAccount, account_id)
            if not acc or not acc.active:
                raise HTTPException(404, "目标平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此平台账号的操作权限")
            if acc.ip_id and sched.ip_id and acc.ip_id != sched.ip_id:
                raise HTTPException(403, "平台账号与排期所属 IP 不一致")
            if PLATFORM_CODE_MAP.get(sp.get("platform")) != acc.platform:
                raise HTTPException(422, "平台账号与排期的发布平台不一致")

            draft = db.get(Record, sp["draft_id"])
            err = self._validate_draft_ready(db, draft, int(sp["version"]))
            if err:
                raise HTTPException(409, err)

            asset = db.get(Record, sp.get("asset_id") or "")
            if acc.platform != "GongZhongHao" and (not asset or asset.kind != "assets" or asset.payload.get("draft_version") != draft.payload["version"]):
                raise HTTPException(409, "排期对应的视频资产缺失或版本不匹配")

            cover_asset = next(
                (
                    a
                    for a in db.scalars(select(Record).where(Record.kind == "assets"))
                    if a.payload.get("draft_id") == draft.id
                    and a.payload.get("draft_version") == draft.payload["version"]
                    and a.payload.get("type") == "cover"
                ),
                None,
            )

            planned_utc = datetime.fromisoformat(sp["planned_at"]).astimezone(timezone.utc).isoformat()
            self._check_daily_limit(db, acc.id, planned_utc)

            snapshot = {
                "title": draft.payload.get("title", ""),
                "desc": draft.payload.get("body", ""),
                "video_asset_id": asset.id if asset else None,
                "video_hash": asset.payload.get("hash") if asset else None,
                "video_filename": asset.payload.get("filename") if asset else None,
                "cover_asset_id": cover_asset.id if cover_asset else None,
                "cover_hash": cover_asset.payload.get("hash") if cover_asset else None,
                "cover_filename": cover_asset.payload.get("filename") if cover_asset else None,
                "platform_options": platform_options or {},
            }
            snap_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            idem_key = hashlib.sha256(f"{schedule_id}|{acc.id}|{snap_hash}|{mode}".encode()).hexdigest()

            existing_attempt = db.scalar(select(PublishAttempt).where(PublishAttempt.idempotency_key == idem_key))
            if existing_attempt:
                t = db.get(PublishTarget, existing_attempt.target_id)
                db.commit()
                return _attempt_dict(existing_attempt, t, acc)

            target = PublishTarget(
                id=uid(),
                schedule_id=schedule_id,
                ip_id=sched.ip_id,
                draft_id=draft.id,
                draft_version=int(sp["version"]),
                account_id=acc.id,
                platform=acc.platform,
                content_type="wechat_draft" if acc.platform == "GongZhongHao" else content_type,
                schedule_owner=schedule_owner,
                approved_snapshot_json=snapshot,
                snapshot_hash=snap_hash,
                planned_at_utc=planned_utc,
                execution_mode="draft" if acc.platform == "GongZhongHao" else mode,
                status="QUEUED",
                created_by=actor.id,
            )
            attempt = PublishAttempt(
                id=uid(),
                target_id=target.id,
                attempt_no=1,
                idempotency_key=idem_key,
                state="QUEUED",
                node_id=acc.node_id,
            )
            db.add(target)
            db.add(attempt)
            db.commit()
            attempt_id = attempt.id
            is_wechat = acc.platform == "GongZhongHao"

        if is_wechat or mode == "draft" or schedule_owner == "platform" or planned_utc <= now():
            return self.execute_attempt(attempt_id, actor=actor)
        return self.get_attempt(actor, attempt_id)

    def execute_attempt(self, attempt_id: str, *, actor: User | None = None) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "执行记录不存在")
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if att.state in ("SUBMITTING", "UNKNOWN", "PUBLISHED", "ACCEPTED", "PUBLISHING", "REVIEWING", "DRAFT_PUSHED"):
                db.commit()
                return _attempt_dict(att, target, acc)

            draft = db.get(Record, target.draft_id)
            err = self._validate_draft_ready(db, draft, target.draft_version)
            if err:
                att.state = "CANCELLED"
                att.error_class = "DRAFT_INVALID"
                att.error_message = err
                target.status = "NEEDS_REVIEW"
                db.commit()
                return _attempt_dict(att, target, acc)

            if target.platform == "GongZhongHao":
                wechat_cfg = self._settings(db).get("wechat", {})
                app_id = wechat_cfg.get("app_id", "")
                app_secret = self.app.state.secrets.decrypt(wechat_cfg.get("app_secret_encrypted", ""))
                snap = target.approved_snapshot_json or {}
                cover_path = (self.app.state.data_dir / "assets" / snap["cover_asset_id"]) if snap.get("cover_asset_id") else None
                att.state = "SUBMITTING"
                att.submit_started_at = now()
                db.commit()
                wres = push_official_account_draft(
                    app_id=app_id,
                    app_secret=app_secret,
                    title=snap.get("title", ""),
                    body=snap.get("desc", ""),
                    cover_path=cover_path,
                )
                with self.Session() as db2:
                    db2.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    a2 = db2.get(PublishAttempt, attempt_id)
                    t2 = db2.get(PublishTarget, a2.target_id)
                    if wres.ok:
                        a2.state = "DRAFT_PUSHED"
                        a2.vendor_draft_id = wres.media_id or f"manual-{a2.id[:8]}"
                        a2.error_message = None if wres.mode == "official_api" else "已登记为公众号草稿待人工后台群发（未配置公众号API密钥）"
                        a2.verification_status = "confirmed_success"
                        a2.verification_detail = (
                            "已推送至公众号草稿箱，待人工后台群发并回填链接"
                            if wres.mode == "official_api"
                            else "已登记为公众号草稿待人工后台群发（未配置公众号API密钥）"
                        )
                        a2.last_verified_at = now()
                        t2.status = "DRAFT_PUSHED"
                    else:
                        a2.state = "RETRYABLE_FAILED"
                        a2.error_class = "WECHAT_DRAFT_ERROR"
                        a2.error_message = wres.error_message
                        a2.verification_status = "confirmed_failed"
                        a2.verification_detail = f"公众号草稿推送失败：{wres.error_message}"
                        a2.last_verified_at = now()
                    a2.updated_at = now()
                    db2.commit()
                    return _attempt_dict(a2, t2, db2.get(PlatformAccount, t2.account_id))

            # Check node availability before SUBMITTING when client_local
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
            if node and node.node_scope == "client_local" and (node.status != "online" or not node.last_heartbeat_at or node.last_heartbeat_at < cutoff):
                att.state = "WAITING_CLIENT_ONLINE"
                att.error_class = "CLIENT_OFFLINE"
                att.error_message = "操作者本机小V猫不在线，已挂起等待上线后一键继续"
                att.verification_status = "confirmed_failed"
                att.verification_detail = att.error_message
                target.status = "WAITING_CLIENT_ONLINE"
                db.commit()
                return _attempt_dict(att, target, acc)

            att.state = "SUBMITTING"
            att.submit_started_at = now()
            att.lease_until = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
            att.updated_at = now()
            snap = target.approved_snapshot_json or {}
            media_refs = []
            if snap.get("video_asset_id"):
                media_refs.append(
                    MediaRef(
                        asset_id=snap["video_asset_id"],
                        sha256=snap["video_hash"],
                        filename=snap.get("video_filename") or "video.mp4",
                        local_server_path=str(self.app.state.data_dir / "assets" / snap["video_asset_id"]),
                    )
                )
            if snap.get("cover_asset_id"):
                media_refs.append(
                    MediaRef(
                        asset_id=snap["cover_asset_id"],
                        sha256=snap["cover_hash"],
                        filename=snap.get("cover_filename") or "cover.png",
                        local_server_path=str(self.app.state.data_dir / "assets" / snap["cover_asset_id"]),
                    )
                )

            args: dict[str, Any] = {
                "uid": acc.vendor_uid,
                "title": snap.get("title", ""),
                "desc": snap.get("desc", ""),
            }
            if snap.get("video_asset_id"):
                args["videoPath"] = f"$ASSET:{snap['video_asset_id']}"
            if snap.get("cover_asset_id"):
                args["cover"] = f"$ASSET:{snap['cover_asset_id']}"
            if snap.get("platform_options"):
                args["platformOptions"] = snap["platform_options"]
            if target.schedule_owner == "platform":
                args["scheduleType"] = "timing"
                args["scheduleTime"] = _to_shanghai_schedule(target.planned_at_utc)
            else:
                args["scheduleType"] = "now"

            tool_name = "vcat_create_draft" if target.execution_mode == "draft" else "vcat_create_publish_task"
            if tool_name == "vcat_create_draft":
                args["draftTitle"] = f"{snap.get('title', '草稿')[:30]}-{target.id[:6]}"
            else:
                batch_name = f"acq-{att.id[:8]}-{att.attempt_no}"
                att.vendor_batch_name = batch_name
                args["batchName"] = batch_name
                args["start"] = True
            attempt_no = att.attempt_no
            db.commit()

        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="execute_publish",
            tool_name=tool_name,
            arguments=args,
            media_refs=media_refs,
            requires_rate_limit=True,
            sync_wait_sec=45.0,
            timeout_sec=120,
            action_id=f"attempt-{attempt_id}-{attempt_no}",
        )

        verify_kind: str | None = None
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            att.lease_until = None
            att.updated_at = now()
            if res.ok:
                payload = res.payload if isinstance(res.payload, dict) else {}
                if tool_name == "vcat_create_draft":
                    att.state = "DRAFT_PUSHED"
                    att.vendor_draft_id = str(payload.get("draftId") or payload.get("id") or "")
                    att.verification_status = "verifying"
                    att.verification_detail = "草稿已提交小V猫，正在回读草稿箱确认..."
                    target.status = "DRAFT_PUSHED"
                    verify_kind = "draft"
                else:
                    att.vendor_local_id = str(payload.get("localId") or payload.get("id") or payload.get("taskId") or "")
                    att.state = "PLATFORM_SCHEDULED" if target.schedule_owner == "platform" else "PUBLISHING"
                    att.verification_status = "verifying"
                    att.verification_detail = "已提交小V猫，正在查询确认平台发布结果..."
                    target.status = att.state
                    verify_kind = "publish"
            elif res.status == "NODE_OFFLINE":
                att.state = "WAITING_CLIENT_ONLINE" if (node and node.node_scope == "client_local") else "RETRYABLE_FAILED"
                att.error_class = "NODE_OFFLINE"
                att.error_message = res.error_message
                att.verification_status = "confirmed_failed"
                att.verification_detail = res.error_message
                target.status = att.state
            elif res.status in ("TIMEOUT", "UNKNOWN"):
                att.state = "UNKNOWN"
                att.error_class = res.status
                att.error_message = res.error_message or "提交响应超时，结果未知，请先核对任务列表，禁止直接重发"
                att.verification_status = "verifying"
                att.verification_detail = "提交超时，系统将自动核对小V猫任务列表确认结果"
                target.status = "UNKNOWN"
            else:
                att.state = "REJECTED"
                target.status = "REJECTED"
                att.error_class = "DRAFT_REJECTED" if tool_name == "vcat_create_draft" else "PLATFORM_REJECTED"
                att.error_message = res.error_message or "提交被平台或小V猫拒绝"
                att.verification_status = "confirmed_failed"
                att.last_verified_at = now()
                att.verification_detail = f"提交失败：{att.error_message}"
            db.commit()
            result_dict = _attempt_dict(att, target, acc)

        if verify_kind == "draft":
            return self._verify_draft_attempt(attempt_id, actor=actor, raise_on_error=False)
        if verify_kind == "publish":
            return self._reconcile_publish_attempt_internal(attempt_id, actor=actor, raise_on_error=False)
        return result_dict

    def list_attempts(self, actor: User, ip_id: str = "") -> list[dict[str, Any]]:
        with self.Session() as db:
            if ip_id:
                require_ip(db, actor, ip_id)
            targets = {t.id: t for t in db.scalars(select(PublishTarget))}
            accounts = {a.id: a for a in db.scalars(select(PlatformAccount))}
            nodes = {n.id: n for n in db.scalars(select(ExecutorNode))}
            attempts = list(db.scalars(select(PublishAttempt).order_by(PublishAttempt.updated_at.desc())))
            out = []
            for att in attempts:
                t = targets.get(att.target_id)
                acc = accounts.get(t.account_id) if t else None
                if ip_id and (not t or t.ip_id != ip_id):
                    continue
                if actor.role == "admin" or (t and t.ip_id and can_ip(db, actor, t.ip_id)) or (acc and self._can_access_account(db, actor, acc, nodes.get(acc.node_id))):
                    out.append(_attempt_dict(att, t, acc))
            return out

    def get_attempt(self, actor: User, attempt_id: str) -> dict[str, Any]:
        with self.Session() as db:
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "任务不存在")
            t = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, t.account_id) if t else None
            node = db.get(ExecutorNode, acc.node_id) if acc else None
            if actor.role != "admin" and not (t and t.ip_id and can_ip(db, actor, t.ip_id)) and not (acc and self._can_access_account(db, actor, acc, node)):
                raise HTTPException(403, "无权查看该发布任务")
            return _attempt_dict(att, t, acc)

    def _verify_draft_attempt(
        self,
        attempt_id: str,
        *,
        actor: User | None = None,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        with self.Session() as db:
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "草稿任务不存在")
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if actor is not None and not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if target.platform == "GongZhongHao":
                return _attempt_dict(att, target, acc)
            draft_id_str = str(att.vendor_draft_id or "").strip()
            node_id = acc.node_id
            account_id = acc.id

        args: dict[str, Any] = {"draftId": int(draft_id_str)} if draft_id_str.isdigit() else {"page": 1, "pageSize": 50}
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="verify_draft",
            tool_name="vcat_list_drafts",
            arguments=args,
            requires_rate_limit=False,
        )
        if not res.ok:
            if raise_on_error:
                raise HTTPException(502, res.error_message or "回读小V猫草稿箱失败")
            with self.Session() as db:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                att = db.get(PublishAttempt, attempt_id)
                target = db.get(PublishTarget, att.target_id)
                acc = db.get(PlatformAccount, target.account_id)
                att.verification_status = "unverified"
                att.last_verified_at = now()
                att.verify_attempts = (att.verify_attempts or 0) + 1
                att.verification_detail = f"草稿 #{draft_id_str or '-'} 已提交，但回读确认暂不可用（可点击核对状态重试）"
                att.updated_at = now()
                db.commit()
                return _attempt_dict(att, target, acc)

        matched: dict[str, Any] | None = None
        payload = res.payload
        if isinstance(payload, dict):
            direct_id = payload.get("draftId") or payload.get("id")
            if direct_id is not None and str(direct_id) == draft_id_str:
                matched = payload
            elif isinstance(payload.get("draft"), dict):
                cand = payload["draft"]
                cid = cand.get("draftId") or cand.get("id")
                if cid is not None and str(cid) == draft_id_str:
                    matched = cand
            if matched is None:
                items = payload.get("list") or payload.get("drafts") or []
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        cid = item.get("draftId") or item.get("id")
                        if draft_id_str and cid is not None and str(cid) == draft_id_str:
                            matched = item
                            break
        elif isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                cid = item.get("draftId") or item.get("id")
                if draft_id_str and cid is not None and str(cid) == draft_id_str:
                    matched = item
                    break

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            att.last_verified_at = now()
            att.verify_attempts = (att.verify_attempts or 0) + 1
            if matched:
                err_text = _extract_remote_error(matched)
                if err_text:
                    att.state = "REJECTED"
                    target.status = "REJECTED"
                    att.remote_status = "draft_rejected"
                    att.verification_status = "confirmed_failed"
                    att.error_class = "DRAFT_REJECTED"
                    att.error_message = err_text
                    att.verification_detail = f"草稿校验失败：{err_text}"
                else:
                    att.state = "DRAFT_PUSHED"
                    target.status = "DRAFT_PUSHED"
                    att.remote_status = "draft_verified"
                    att.verification_status = "confirmed_success"
                    att.error_class = None
                    att.error_message = None
                    att.verification_detail = f"已回读确认草稿 #{draft_id_str} 已保存在小V猫草稿箱"
            else:
                att.state = "REJECTED"
                target.status = "REJECTED"
                att.remote_status = "draft_missing"
                att.verification_status = "confirmed_failed"
                att.error_class = "DRAFT_VERIFY_FAILED"
                att.error_message = f"草稿 #{draft_id_str or '-'} 未在小V猫草稿箱中查到，可能推送失败，请检查内容或重试"
                att.verification_detail = att.error_message
            att.updated_at = now()
            db.commit()
            return _attempt_dict(att, target, acc)

    def _reconcile_publish_attempt_internal(
        self,
        attempt_id: str,
        *,
        actor: User | None = None,
        raise_on_error: bool = True,
    ) -> dict[str, Any]:
        with self.Session() as db:
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "发布任务不存在")
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if actor is not None and not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if target.execution_mode == "draft" and att.vendor_draft_id and not att.vendor_local_id:
                is_draft_only = True
            else:
                is_draft_only = False
            vendor_uid = acc.vendor_uid
            node_id = acc.node_id
            account_id = acc.id

        if is_draft_only:
            return self._verify_draft_attempt(attempt_id, actor=actor, raise_on_error=raise_on_error)

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="reconcile_publish",
            tool_name="vcat_list_publish_tasks",
            arguments={"uid": vendor_uid, "page": 1, "pageSize": 50},
            requires_rate_limit=False,
        )
        if not res.ok or not isinstance(res.payload, dict):
            if raise_on_error:
                raise HTTPException(502, res.error_message or "查询小V猫发布任务列表失败")
            with self.Session() as db:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                att = db.get(PublishAttempt, attempt_id)
                target = db.get(PublishTarget, att.target_id)
                acc = db.get(PlatformAccount, target.account_id)
                att.verification_status = "unverified"
                att.last_verified_at = now()
                att.verify_attempts = (att.verify_attempts or 0) + 1
                att.verification_detail = f"暂无法连接执行节点核对状态：{res.error_message or '网络异常'}"
                att.updated_at = now()
                db.commit()
                return _attempt_dict(att, target, acc)

        tasks = res.payload.get("list") or res.payload.get("tasks") or res.payload.get("items") or []
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            matched = _match_remote_publish_task(tasks if isinstance(tasks, list) else [], att, target)
            if matched:
                _apply_remote_publish_task(att, target, matched)
            else:
                att.last_verified_at = now()
                att.verify_attempts = (att.verify_attempts or 0) + 1
                if att.state in ("PUBLISHING", "ACCEPTED") and att.verify_attempts <= 3:
                    att.verification_status = "verifying"
                else:
                    att.verification_status = "unverified"
                att.verification_detail = "暂未在小V猫发布任务列表中查到该任务，请稍后再次核对"
            att.updated_at = now()
            db.commit()
            return _attempt_dict(att, target, acc)

    def reconcile_attempt(self, actor: User, attempt_id: str) -> dict[str, Any]:
        return self._reconcile_publish_attempt_internal(attempt_id, actor=actor, raise_on_error=True)

    def reconcile_active_attempts(
        self,
        actor: User | None = None,
        *,
        ip_id: str = "",
        min_interval_sec: float = 2.0,
        limit: int = 5,
    ) -> dict[str, Any]:
        with self.Session() as db:
            if actor is not None and ip_id:
                require_ip(db, actor, ip_id)
            targets = {t.id: t for t in db.scalars(select(PublishTarget))}
            accounts = {a.id: a for a in db.scalars(select(PlatformAccount))}
            nodes = {n.id: n for n in db.scalars(select(ExecutorNode))}
            candidates = list(
                db.scalars(
                    select(PublishAttempt)
                    .where(
                        PublishAttempt.state.in_(
                            ("ACCEPTED", "PUBLISHING", "REVIEWING", "UNKNOWN", "PLATFORM_SCHEDULED")
                        )
                    )
                    .order_by(PublishAttempt.updated_at.asc())
                )
            )
            now_dt = datetime.now(timezone.utc)
            picked_ids: list[str] = []
            for att in candidates:
                t = targets.get(att.target_id)
                if not t or t.platform == "GongZhongHao":
                    continue
                if att.state == "PLATFORM_SCHEDULED" and (att.verification_status or "pending") not in ("pending", "verifying"):
                    continue
                if (att.verify_attempts or 0) >= 120:
                    continue
                if actor is not None and ip_id and t.ip_id != ip_id:
                    continue
                acc = accounts.get(t.account_id)
                if actor is not None:
                    allowed = (
                        actor.role == "admin"
                        or (t.ip_id and can_ip(db, actor, t.ip_id))
                        or (acc and self._can_access_account(db, actor, acc, nodes.get(acc.node_id)))
                    )
                    if not allowed:
                        continue
                last_dt = _parse_iso(att.last_verified_at)
                if last_dt is not None:
                    wait_sec = (
                        min_interval_sec
                        if actor is not None
                        else min(30.0, max(min_interval_sec, 3.0 * ((att.verify_attempts or 0) + 1)))
                    )
                    if (now_dt - last_dt).total_seconds() < wait_sec:
                        continue
                picked_ids.append(att.id)
                if len(picked_ids) >= limit:
                    break

        reconciled = 0
        for att_id in picked_ids:
            try:
                self._reconcile_publish_attempt_internal(att_id, actor=actor, raise_on_error=False)
                reconciled += 1
            except Exception:
                pass
        return {
            "reconciled": reconciled,
            "items": self.list_attempts(actor, ip_id=ip_id) if actor is not None else [],
        }

    def republish_attempt(
        self,
        actor: User,
        attempt_id: str,
        *,
        mode: str | None = None,
        schedule_owner: str | None = None,
    ) -> dict[str, Any]:
        if mode is not None and mode not in ("draft", "publish"):
            raise HTTPException(422, "执行模式仅支持 draft 或 publish")
        if schedule_owner is not None and schedule_owner not in ("server", "platform"):
            raise HTTPException(422, "定时归属仅支持 server 或 platform")

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            src = db.get(PublishAttempt, attempt_id)
            if not src:
                raise HTTPException(404, "发布任务不存在")
            target = db.get(PublishTarget, src.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if not acc.active:
                raise HTTPException(409, "目标平台账号已从小V猫移除")
            if src.state not in ("REJECTED", "RETRYABLE_FAILED", "WAITING_CLIENT_ONLINE", "MISSED", "CANCELLED"):
                raise HTTPException(409, f"当前状态 {src.state} 不允许再次发布（结果未知请先核对状态）")

            draft = db.get(Record, target.draft_id)
            if not draft or draft.kind != "drafts":
                raise HTTPException(404, "关联稿件不存在")
            cur_ver = int(draft.payload["version"])
            err = self._validate_draft_ready(db, draft, cur_ver)
            if err:
                raise HTTPException(409, err)

            old_snap = target.approved_snapshot_json or {}
            video_asset: Record | None = None
            cover_asset: Record | None = None
            if target.platform != "GongZhongHao":
                all_assets = list(
                    db.scalars(
                        select(Record)
                        .where(Record.kind == "assets")
                        .order_by(Record.created_at.desc())
                    )
                )
                video_asset = next(
                    (
                        a
                        for a in all_assets
                        if a.payload.get("draft_id") == draft.id
                        and a.payload.get("draft_version") == cur_ver
                        and a.payload.get("type") == "video"
                    ),
                    None,
                )
                if not video_asset and old_snap.get("video_asset_id"):
                    cand = db.get(Record, old_snap["video_asset_id"])
                    if (
                        cand
                        and cand.kind == "assets"
                        and cand.payload.get("type") == "video"
                        and (self.app.state.data_dir / "assets" / cand.id).is_file()
                    ):
                        video_asset = cand
                if not video_asset:
                    raise HTTPException(409, "未找到可用视频资产，请先前往「拍摄上传」上传视频")

                cover_asset = next(
                    (
                        a
                        for a in all_assets
                        if a.payload.get("draft_id") == draft.id
                        and a.payload.get("draft_version") == cur_ver
                        and a.payload.get("type") == "cover"
                    ),
                    None,
                )
                if not cover_asset and old_snap.get("cover_asset_id"):
                    cand_cov = db.get(Record, old_snap["cover_asset_id"])
                    if (
                        cand_cov
                        and cand_cov.kind == "assets"
                        and cand_cov.payload.get("type") == "cover"
                        and (self.app.state.data_dir / "assets" / cand_cov.id).is_file()
                    ):
                        cover_asset = cand_cov

            next_mode = "draft" if target.platform == "GongZhongHao" else (mode or target.execution_mode)
            next_owner = schedule_owner or target.schedule_owner
            planned_utc = target.planned_at_utc
            if next_owner == "server" and planned_utc < now():
                planned_utc = now()
            self._check_daily_limit(db, acc.id, planned_utc, exclude_attempt_id=src.id)

            fresh_snapshot = {
                "title": draft.payload.get("title", ""),
                "desc": draft.payload.get("body", ""),
                "video_asset_id": video_asset.id if video_asset else None,
                "video_hash": video_asset.payload.get("hash") if video_asset else None,
                "video_filename": video_asset.payload.get("filename") if video_asset else None,
                "cover_asset_id": cover_asset.id if cover_asset else None,
                "cover_hash": cover_asset.payload.get("hash") if cover_asset else None,
                "cover_filename": cover_asset.payload.get("filename") if cover_asset else None,
                "platform_options": old_snap.get("platform_options") or {},
            }
            fresh_hash = hashlib.sha256(
                json.dumps(fresh_snapshot, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()

            target.draft_version = cur_ver
            target.approved_snapshot_json = fresh_snapshot
            target.snapshot_hash = fresh_hash
            target.execution_mode = next_mode
            target.schedule_owner = next_owner
            target.planned_at_utc = planned_utc
            target.status = "QUEUED"
            target.updated_at = now()

            sched = db.get(Record, target.schedule_id)
            if sched and sched.kind == "schedules":
                sp = dict(sched.payload or {})
                changed = (
                    sp.get("status") == "needs_review"
                    or sp.get("version") != cur_ver
                    or sp.get("title") != draft.payload.get("title", "")
                    or (video_asset and sp.get("asset_id") != video_asset.id)
                )
                if changed:
                    if sp.get("status") == "needs_review":
                        sp["status"] = "planned"
                    sp["version"] = cur_ver
                    sp["title"] = draft.payload.get("title", "")
                    if video_asset:
                        sp["asset_id"] = video_asset.id
                    sched.payload = sp
                    sched.updated_at = now()

            max_no = max(
                (a.attempt_no for a in db.scalars(select(PublishAttempt).where(PublishAttempt.target_id == target.id))),
                default=src.attempt_no,
            )
            new_no = max_no + 1
            new_att = PublishAttempt(
                id=uid(),
                target_id=target.id,
                attempt_no=new_no,
                idempotency_key=f"republish:{target.id}:{new_no}:{uid()[:8]}",
                state="QUEUED",
                node_id=acc.node_id,
                verification_status="pending",
            )
            db.add(new_att)
            db.commit()
            new_att_id = new_att.id

        return self.execute_attempt(new_att_id, actor=actor)

    def cancel_attempt(self, actor: User, attempt_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "发布任务不存在")
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if att.state in ("QUEUED", "WAITING_CLIENT_ONLINE", "RETRYABLE_FAILED", "MISSED", "DRAFT_PUSHED"):
                att.state = "CANCELLED"
                target.status = "CANCELLED"
                att.updated_at = now()
                db.commit()
                return _attempt_dict(att, target, acc)
            if att.state == "PUBLISHED":
                raise HTTPException(409, "已发表作品不能通过取消任务撤回")
            if not att.vendor_local_id:
                raise HTTPException(409, "未知远端任务ID，请先核对任务状态")
            local_id = att.vendor_local_id
            node_id = acc.node_id
            account_id = acc.id
            db.commit()

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="cancel_publish",
            tool_name="vcat_cancel_publish_task",
            arguments={"localId": local_id},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "取消远端任务失败")
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            target = db.get(PublishTarget, att.target_id)
            att.state = "CANCELLED"
            target.status = "CANCELLED"
            att.updated_at = now()
            db.commit()
            return _attempt_dict(att, target, db.get(PlatformAccount, target.account_id))

    def retry_attempt(self, actor: User, attempt_id: str) -> dict[str, Any]:
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "发布任务不存在")
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if att.state not in ("RETRYABLE_FAILED", "WAITING_CLIENT_ONLINE"):
                raise HTTPException(409, f"状态 {att.state} 禁止重试（结果未知或已提交任务须先核对）")
            att.attempt_no += 1
            att.state = "QUEUED"
            att.error_class = None
            att.error_message = None
            att.updated_at = now()
            db.commit()
        return self.execute_attempt(attempt_id, actor=actor)

    def confirm_wechat_published(self, actor: User, attempt_id: str, evidence_url: str) -> dict[str, Any]:
        parsed = urlparse((evidence_url or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(422, "请填写有效的公众号公开文章 URL 作为发表凭证")
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            att = db.get(PublishAttempt, attempt_id)
            if not att:
                raise HTTPException(404, "发布任务不存在")
            target = db.get(PublishTarget, att.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if att.state != "DRAFT_PUSHED":
                raise HTTPException(409, "仅草稿态记录可确认群发")
            att.state = "PUBLISHED"
            att.evidence_url = evidence_url.strip()
            target.status = "PUBLISHED"
            att.updated_at = now()
            db.commit()
            return _attempt_dict(att, target, acc)

    def list_drafts(self, actor: User, ip_id: str = "", sync_remote: bool = False) -> dict[str, Any]:
        local = [t for t in self.list_attempts(actor, ip_id=ip_id) if t.get("state") == "DRAFT_PUSHED"]
        remote_drafts: list[dict[str, Any]] = []
        if sync_remote:
            _, res = self._execute_mcp(
                actor=actor,
                node_id=None,
                account_id=None,
                action_type="list_drafts",
                tool_name="vcat_list_drafts",
                arguments={"page": 1, "pageSize": 50},
                requires_rate_limit=False,
            )
            if res.ok and isinstance(res.payload, dict):
                remote_drafts = res.payload.get("list") or []
        return {"items": local, "remote_drafts": remote_drafts}

    def publish_draft(
        self,
        actor: User,
        attempt_id: str,
        *,
        confirm: bool = False,
        schedule_owner: str = "server",
        planned_at: str | None = None,
        platform_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(422, "草稿转正式发布必须显式确认 confirm=true")
        if schedule_owner not in ("server", "platform"):
            raise HTTPException(422, "定时归属仅支持 server 或 platform")

        promo_key = f"publish-draft:{attempt_id}"
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            src = db.get(PublishAttempt, attempt_id)
            if not src:
                raise HTTPException(404, "草稿任务不存在")
            target = db.get(PublishTarget, src.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            if not acc.active:
                raise HTTPException(409, "目标平台账号已从小V猫移除")
            if target.platform == "GongZhongHao":
                raise HTTPException(422, "公众号不支持经小V猫转正式群发，请在微信公众平台后台群发后登记公开链接")
            if src.state != "DRAFT_PUSHED" or not src.vendor_draft_id:
                raise HTTPException(409, "仅带有远端草稿 ID 的草稿态记录可转正式发布")
            existing_promo = db.scalar(select(PublishAttempt).where(PublishAttempt.idempotency_key == promo_key))
            if existing_promo:
                raise HTTPException(409, "该草稿已发起过转正式发布，请在任务列表查看或核对结果")

            draft = db.get(Record, target.draft_id)
            err = self._validate_draft_ready(db, draft, target.draft_version)
            if err:
                raise HTTPException(409, err)

            planned_utc = target.planned_at_utc
            if planned_at:
                planned_utc = datetime.fromisoformat(planned_at).astimezone(timezone.utc).isoformat()
            self._check_daily_limit(db, acc.id, planned_utc, exclude_attempt_id=attempt_id)

            max_no = max((a.attempt_no for a in db.scalars(select(PublishAttempt).where(PublishAttempt.target_id == target.id))), default=1)
            promo_id = uid()
            promo_no = max_no + 1
            batch_name = f"acq-draft-{promo_id[:8]}-{promo_no}"
            promo = PublishAttempt(
                id=promo_id,
                target_id=target.id,
                attempt_no=promo_no,
                idempotency_key=promo_key,
                state="SUBMITTING",
                node_id=acc.node_id,
                vendor_draft_id=src.vendor_draft_id,
                vendor_batch_name=batch_name,
                verification_status="verifying",
                submit_started_at=now(),
                lease_until=(datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat(),
            )
            target.execution_mode = "publish"
            target.schedule_owner = schedule_owner
            target.planned_at_utc = planned_utc
            target.status = "SUBMITTING"
            db.add(promo)
            db.commit()
            draft_id_arg: Any = int(src.vendor_draft_id) if str(src.vendor_draft_id).isdigit() else src.vendor_draft_id
            args: dict[str, Any] = {
                "draftId": draft_id_arg,
                "uid": acc.vendor_uid,
                "batchName": batch_name,
                "start": True,
            }
            if platform_options:
                args["platformOptions"] = platform_options
            node_id = acc.node_id
            acc_id = acc.id

        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=acc_id,
            action_type="publish_draft",
            tool_name="vcat_publish_draft",
            arguments=args,
            requires_rate_limit=True,
            sync_wait_sec=45.0,
            timeout_sec=120,
            action_id=f"attempt-{promo_id}",
        )

        should_verify = False
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            promo = db.get(PublishAttempt, promo_id)
            target = db.get(PublishTarget, promo.target_id)
            acc = db.get(PlatformAccount, target.account_id)
            node = db.get(ExecutorNode, acc.node_id)
            promo.lease_until = None
            promo.updated_at = now()
            if res.ok:
                payload = res.payload if isinstance(res.payload, dict) else {}
                promo.vendor_local_id = str(payload.get("localId") or payload.get("id") or payload.get("taskId") or "")
                promo.state = "PLATFORM_SCHEDULED" if schedule_owner == "platform" else "PUBLISHING"
                promo.verification_status = "verifying"
                promo.verification_detail = "草稿已转为发布任务，正在查询确认平台结果..."
                target.status = promo.state
                should_verify = True
            elif res.status == "NODE_OFFLINE":
                promo.state = "WAITING_CLIENT_ONLINE" if (node and node.node_scope == "client_local") else "RETRYABLE_FAILED"
                promo.error_class = "NODE_OFFLINE"
                promo.error_message = res.error_message
                promo.verification_status = "confirmed_failed"
                promo.verification_detail = res.error_message
                target.status = promo.state
            elif res.status in ("TIMEOUT", "UNKNOWN"):
                promo.state = "UNKNOWN"
                promo.error_class = res.status
                promo.error_message = res.error_message or "草稿转正式发布状态未知，请核对任务列表"
                promo.verification_status = "verifying"
                promo.verification_detail = "草稿转正式发布超时，系统将自动核对小V猫任务列表确认结果"
                target.status = "UNKNOWN"
            else:
                promo.state = "REJECTED"
                target.status = "REJECTED"
                promo.error_class = "PLATFORM_REJECTED"
                promo.error_message = res.error_message or "草稿发布被拒绝"
                promo.verification_status = "confirmed_failed"
                promo.last_verified_at = now()
                promo.verification_detail = f"草稿发布失败：{promo.error_message}"
            db.commit()
            result_dict = _attempt_dict(promo, target, acc)

        if should_verify:
            return self._reconcile_publish_attempt_internal(promo_id, actor=actor, raise_on_error=False)
        return result_dict

    # ---------- Posts, Stats, Comments, Messages, Monitors ----------

    def refresh_account_stats(self, actor: User, account_id: str) -> dict[str, Any]:
        with self.Session() as db:
            acc = db.get(PlatformAccount, account_id)
            if not acc:
                raise HTTPException(404, "账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "无权刷新该账号")
            vendor_uid, node_id, ip_id = acc.vendor_uid, acc.node_id, acc.ip_id

        _, ref_res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="refresh_account_stats",
            tool_name="vcat_refresh_account_stats",
            arguments={"uids": [vendor_uid]},
            requires_rate_limit=True,
        )
        if not ref_res.ok:
            raise HTTPException(502, ref_res.error_message or "刷新账号统计失败")

        _, get_res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="get_account_stats",
            tool_name="vcat_get_account_stats",
            arguments={"uid": vendor_uid},
            requires_rate_limit=False,
        )
        if not get_res.ok or not isinstance(get_res.payload, dict):
            raise HTTPException(502, get_res.error_message or "读取账号统计失败")
        data = get_res.payload
        total = data.get("total") or {}
        daily = data.get("daily") or []
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            today = business_today()
            snap = db.scalar(
                select(MetricSnapshot).where(
                    MetricSnapshot.subject_type == "account",
                    MetricSnapshot.subject_id == account_id,
                    MetricSnapshot.observed_date == today,
                    MetricSnapshot.metric_type == "cumulative",
                )
            )
            vals = {
                "fans_total": clean_metric_number(total.get("fans")),
                "likes": clean_metric_number(total.get("like")),
                "favorites": clean_metric_number(total.get("favorite")),
            }
            if snap:
                for k, v in vals.items():
                    setattr(snap, k, v)
                snap.updated_at = now()
            else:
                db.add(
                    MetricSnapshot(
                        id=uid(),
                        subject_type="account",
                        subject_id=account_id,
                        ip_id=ip_id,
                        observed_date=today,
                        metric_type="cumulative",
                        **vals,
                    )
                )
            for d in daily:
                if not isinstance(d, dict) or not d.get("date"):
                    continue
                obs = str(d["date"])[:10]
                dsnap = db.scalar(
                    select(MetricSnapshot).where(
                        MetricSnapshot.subject_type == "account",
                        MetricSnapshot.subject_id == account_id,
                        MetricSnapshot.observed_date == obs,
                        MetricSnapshot.metric_type == "daily_delta",
                    )
                )
                dvals = {
                    "views": clean_metric_number(d.get("view")),
                    "likes": clean_metric_number(d.get("like")),
                    "comments": clean_metric_number(d.get("comment")),
                    "forwards": clean_metric_number(d.get("forward")),
                    "favorites": clean_metric_number(d.get("favorite")),
                    "fans_total": clean_metric_number(d.get("fansTotal")),
                    "fans_add": clean_metric_number(d.get("fansAdd")),
                }
                if dsnap:
                    for k, v in dvals.items():
                        setattr(dsnap, k, v)
                    dsnap.updated_at = now()
                else:
                    db.add(
                        MetricSnapshot(
                            id=uid(),
                            subject_type="account",
                            subject_id=account_id,
                            ip_id=ip_id,
                            observed_date=obs,
                            metric_type="daily_delta",
                            **dvals,
                        )
                    )
            db.commit()
        return {"account_id": account_id, "total": total, "daily_count": len(daily)}

    def sync_posts(self, actor: User, account_id: str) -> dict[str, Any]:
        with self.Session() as db:
            acc = db.get(PlatformAccount, account_id)
            if not acc:
                raise HTTPException(404, "账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "无权同步该账号作品")
            vendor_uid, node_id, ip_id = acc.vendor_uid, acc.node_id, acc.ip_id

        _, start_res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="start_post_sync",
            tool_name="vcat_start_post_sync",
            arguments={"uids": [vendor_uid], "postRangeType": "latest", "limit": 20, "daily": True, "maxRun": 1, "errorNotice": False},
            requires_rate_limit=True,
        )
        if not start_res.ok:
            raise HTTPException(502, start_res.error_message or "启动作品同步失败")

        for _ in range(8):
            _, st_res = self._execute_mcp(
                actor=actor,
                node_id=node_id,
                account_id=account_id,
                action_type="poll_post_sync",
                tool_name="vcat_get_post_sync",
                arguments={},
                requires_rate_limit=False,
            )
            if st_res.ok and isinstance(st_res.payload, dict) and int(st_res.payload.get("looping", 0)) == 0:
                break
            time.sleep(1.5)

        _, list_res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="list_posts",
            tool_name="vcat_list_posts",
            arguments={"uids": [vendor_uid], "page": 1, "pageSize": 50},
            requires_rate_limit=False,
        )
        if not list_res.ok or not isinstance(list_res.payload, dict):
            raise HTTPException(502, list_res.error_message or "读取作品列表失败")
        items = list_res.payload.get("list") or []
        upserted = 0
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            today = business_today()
            for p in items:
                if not isinstance(p, dict) or not p.get("postId"):
                    continue
                plat = str(p.get("platform") or acc.platform)
                pid = str(p["postId"])
                row = db.scalar(
                    select(PublishedPost).where(
                        PublishedPost.platform == plat,
                        PublishedPost.vendor_uid == vendor_uid,
                        PublishedPost.vendor_post_id == pid,
                    )
                )
                if not row:
                    row = PublishedPost(
                        id=uid(),
                        platform=plat,
                        account_id=account_id,
                        ip_id=ip_id,
                        vendor_uid=vendor_uid,
                        vendor_post_id=pid,
                    )
                    db.add(row)
                    db.flush()
                row.title = str(p.get("title") or "")
                row.desc = p.get("desc")
                row.cover_url = p.get("cover")
                row.url = p.get("url")
                row.publish_status = str(p.get("publishStatus") or "published")
                row.post_time_utc = str(p.get("postTime") or "")
                row.last_synced_at = now()

                snap = db.scalar(
                    select(MetricSnapshot).where(
                        MetricSnapshot.subject_type == "post",
                        MetricSnapshot.subject_id == row.id,
                        MetricSnapshot.observed_date == today,
                        MetricSnapshot.metric_type == "cumulative",
                    )
                )
                mvals = {
                    "views": clean_metric_number(p.get("view")),
                    "likes": clean_metric_number(p.get("like")),
                    "comments": clean_metric_number(p.get("comment")),
                    "forwards": clean_metric_number(p.get("forward")),
                    "favorites": clean_metric_number(p.get("favorite") if p.get("favorite") is not None else p.get("love")),
                    "fans_add": clean_metric_number(p.get("fansAdd")),
                    "full_view_rate": clean_metric_number(p.get("fullView"), as_float=True),
                    "avg_view_sec": clean_metric_number(p.get("viewTime"), as_float=True),
                }
                if snap:
                    for k, v in mvals.items():
                        setattr(snap, k, v)
                    snap.updated_at = now()
                else:
                    db.add(
                        MetricSnapshot(
                            id=uid(),
                            subject_type="post",
                            subject_id=row.id,
                            ip_id=ip_id,
                            observed_date=today,
                            metric_type="cumulative",
                            **mvals,
                        )
                    )
                upserted += 1
            db.commit()
        return {"synced": upserted, "posts": self.list_posts(actor, ip_id=ip_id or "")}

    def list_posts(self, actor: User, ip_id: str = "") -> list[dict[str, Any]]:
        with self.Session() as db:
            if ip_id:
                require_ip(db, actor, ip_id)
            accounts = {a.id: a for a in db.scalars(select(PlatformAccount))}
            nodes = {n.id: n for n in db.scalars(select(ExecutorNode))}
            snaps = {}
            for s in db.scalars(select(MetricSnapshot).where(MetricSnapshot.subject_type == "post").order_by(MetricSnapshot.observed_date.asc())):
                snaps[s.subject_id] = s
            posts = list(db.scalars(select(PublishedPost).order_by(PublishedPost.last_synced_at.desc())))
            out = []
            for p in posts:
                if ip_id and p.ip_id != ip_id:
                    continue
                acc = accounts.get(p.account_id) if p.account_id else None
                if actor.role == "admin" or (p.ip_id and can_ip(db, actor, p.ip_id)) or (acc and self._can_access_account(db, actor, acc, nodes.get(acc.node_id))):
                    s = snaps.get(p.id)
                    out.append(
                        {
                            "id": p.id,
                            "platform": p.platform,
                            "platform_name": PLATFORM_CN_MAP.get(p.platform, p.platform),
                            "account_id": p.account_id,
                            "account_nickname": acc.nickname if acc else p.vendor_uid,
                            "ip_id": p.ip_id,
                            "vendor_post_id": p.vendor_post_id,
                            "title": p.title,
                            "url": p.url,
                            "publish_status": p.publish_status,
                            "post_time": p.post_time_utc,
                            "views": s.views if s else None,
                            "likes": s.likes if s else None,
                            "comments": s.comments if s else None,
                            "forwards": s.forwards if s else None,
                            "favorites": s.favorites if s else None,
                            "fans_add": s.fans_add if s else None,
                            "full_view_rate": s.full_view_rate if s else None,
                            "avg_view_sec": s.avg_view_sec if s else None,
                        }
                    )
            return out

    def list_account_stats(self, actor: User, ip_id: str = "") -> list[dict[str, Any]]:
        accs = self.list_accounts(actor, ip_id=ip_id)
        acc_ids = [a["id"] for a in accs]
        if not acc_ids:
            return []
        with self.Session() as db:
            latest = {}
            for s in db.scalars(
                select(MetricSnapshot)
                .where(MetricSnapshot.subject_type == "account", MetricSnapshot.subject_id.in_(acc_ids), MetricSnapshot.metric_type == "cumulative")
                .order_by(MetricSnapshot.observed_date.asc())
            ):
                latest[s.subject_id] = s
        for a in accs:
            s = latest.get(a["id"])
            a["stats"] = (
                {
                    "observed_date": s.observed_date,
                    "fans_total": s.fans_total,
                    "likes": s.likes,
                    "favorites": s.favorites,
                }
                if s
                else None
            )
        return accs

    def _require_interaction_write(self, db) -> None:
        ex = self._settings(db).get("executor", {})
        if not bool(ex.get("allow_interaction_write", False)):
            raise HTTPException(403, "当前系统未开启互动外发，请在「系统设置 → 发布执行器」启用后再试")

    def _resolve_accessible_account(self, actor: User, account_id: str) -> tuple[PlatformAccount, ExecutorNode | None]:
        with self.Session() as db:
            acc = db.get(PlatformAccount, account_id)
            if not acc or not acc.active:
                raise HTTPException(404, "平台账号不存在")
            node = db.get(ExecutorNode, acc.node_id)
            if not self._can_access_account(db, actor, acc, node):
                raise HTTPException(403, "没有此账号的操作权限")
            return acc, node

    def list_comments(self, actor: User, account_id: str | None = None) -> dict[str, Any]:
        with self.Session() as db:
            allow_write = bool(self._settings(db).get("executor", {}).get("allow_interaction_write", False))
        accs = self.list_accounts(actor)
        if account_id:
            accs = [a for a in accs if a["id"] == account_id]
        if not accs:
            return {"list": [], "total": 0, "allow_interaction_write": allow_write}
        uid_to_acc = {a["vendor_uid"]: a["id"] for a in accs}
        by_node: dict[str, list[str]] = {}
        for a in accs:
            by_node.setdefault(a["node_id"], []).append(a["vendor_uid"])
        items: list[dict[str, Any]] = []
        for nid, uids in by_node.items():
            _, res = self._execute_mcp(
                actor=actor,
                node_id=nid,
                account_id=account_id,
                action_type="list_comments",
                tool_name="vcat_list_comments",
                arguments={"uids": uids[:20], "page": 1, "pageSize": 30},
                requires_rate_limit=False,
            )
            if res.ok and isinstance(res.payload, dict):
                for raw in res.payload.get("list") or []:
                    if isinstance(raw, dict):
                        v_uid = str(raw.get("uid") or raw.get("accountUid") or "")
                        items.append({**raw, "account_id": raw.get("account_id") or uid_to_acc.get(v_uid) or (account_id or accs[0]["id"])})
        return {"list": items, "total": len(items), "allow_interaction_write": allow_write}

    def get_comment_summary(self, actor: User, node_id: str | None = None) -> dict[str, Any]:
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="comment_summary",
            tool_name="vcat_get_comment_summary",
            arguments={},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "获取评论概况失败")
        return res.payload if isinstance(res.payload, dict) else {"raw": res.payload}

    def list_message_sessions(self, actor: User, account_id: str | None = None) -> dict[str, Any]:
        with self.Session() as db:
            allow_write = bool(self._settings(db).get("executor", {}).get("allow_interaction_write", False))
        accs = self.list_accounts(actor)
        if account_id:
            accs = [a for a in accs if a["id"] == account_id]
        if not accs:
            return {"list": [], "total": 0, "allow_interaction_write": allow_write}
        uid_to_acc = {a["vendor_uid"]: a["id"] for a in accs}
        by_node: dict[str, list[str]] = {}
        for a in accs:
            by_node.setdefault(a["node_id"], []).append(a["vendor_uid"])
        items: list[dict[str, Any]] = []
        for nid, uids in by_node.items():
            _, res = self._execute_mcp(
                actor=actor,
                node_id=nid,
                account_id=account_id,
                action_type="list_message_sessions",
                tool_name="vcat_list_message_sessions",
                arguments={"uids": uids[:20], "page": 1, "pageSize": 30},
                requires_rate_limit=False,
            )
            if res.ok and isinstance(res.payload, dict):
                for raw in res.payload.get("list") or []:
                    if isinstance(raw, dict):
                        v_uid = str(raw.get("uid") or raw.get("accountUid") or "")
                        items.append({**raw, "account_id": raw.get("account_id") or uid_to_acc.get(v_uid) or (account_id or accs[0]["id"])})
        return {"list": items, "total": len(items), "allow_interaction_write": allow_write}

    def get_session_messages(self, actor: User, session_id: str, account_id: str, refresh: bool = False) -> dict[str, Any]:
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="get_session_messages",
            tool_name="vcat_get_session_messages",
            arguments={"sessionId": session_id, "uid": acc.vendor_uid, "limit": 50, "refresh": refresh},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "获取私信聊天记录失败")
        return res.payload if isinstance(res.payload, dict) else {"list": []}

    def draft_interaction_reply(
        self,
        actor: User,
        account_id: str,
        *,
        comment_id: str | None = None,
        session_id: str | None = None,
        requirement: str = "",
    ) -> dict[str, Any]:
        if not comment_id and not session_id:
            raise HTTPException(422, "请指定 comment_id 或 session_id")
        acc, _ = self._resolve_accessible_account(actor, account_id)
        args: dict[str, Any] = {"uid": acc.vendor_uid}
        if comment_id:
            args["commentId"] = comment_id
        if session_id:
            args["sessionId"] = session_id
        if requirement:
            args["requirement"] = requirement
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="draft_reply",
            tool_name="vcat_draft_reply",
            arguments=args,
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "生成 AI 回复草稿失败")
        return res.payload if isinstance(res.payload, dict) else {"draft": str(res.payload or "")}

    def reply_comment(self, actor: User, comment_id: str, account_id: str, content: str) -> dict[str, Any]:
        with self.Session() as db:
            self._require_interaction_write(db)
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="reply_comment",
            tool_name="vcat_reply_comment",
            arguments={"commentId": comment_id, "content": content.strip(), "uid": acc.vendor_uid},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "回复评论失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def like_comment(self, actor: User, comment_id: str, account_id: str, cancel: bool = False) -> dict[str, Any]:
        with self.Session() as db:
            self._require_interaction_write(db)
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="like_comment",
            tool_name="vcat_like_comment",
            arguments={"commentId": comment_id, "uid": acc.vendor_uid, "cancel": cancel},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "评论点赞失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def top_comment(self, actor: User, comment_id: str, account_id: str) -> dict[str, Any]:
        with self.Session() as db:
            self._require_interaction_write(db)
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="top_comment",
            tool_name="vcat_set_top_comment",
            arguments={"commentId": comment_id, "uid": acc.vendor_uid},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "评论置顶失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def send_session_message(self, actor: User, session_id: str, account_id: str, text: str) -> dict[str, Any]:
        with self.Session() as db:
            self._require_interaction_write(db)
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="send_message",
            tool_name="vcat_send_message",
            arguments={"sessionId": session_id, "text": text.strip(), "uid": acc.vendor_uid},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "发送私信失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def list_monitors(self, actor: User, node_id: str | None = None) -> dict[str, Any]:
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="list_monitors",
            tool_name="vcat_list_monitor_tasks",
            arguments={"page": 1, "pageSize": 30},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "查询监控任务失败")
        return res.payload if isinstance(res.payload, dict) else {"list": []}

    def toggle_monitor(self, actor: User, monitor_id: int, enabled: bool, node_id: str | None = None) -> dict[str, Any]:
        if actor.role not in ("admin", "leader"):
            raise HTTPException(403, "仅管理员或团队长可启停监控任务")
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="toggle_monitor",
            tool_name="vcat_set_monitor_status",
            arguments={"id": monitor_id, "enabled": enabled},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "切换监控任务状态失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def run_monitor(self, actor: User, monitor_id: int, confirm: bool = False, node_id: str | None = None) -> dict[str, Any]:
        if actor.role not in ("admin", "leader"):
            raise HTTPException(403, "仅管理员或团队长可手动执行监控任务")
        if not confirm:
            raise HTTPException(422, "手动执行监控任务必须二次确认 confirm=true")
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="run_monitor",
            tool_name="vcat_run_monitor_task",
            arguments={"id": monitor_id},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "执行监控任务失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def get_flow_report(
        self,
        actor: User,
        account_id: str | None = None,
        platform: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"page": 1, "pageSize": 20}
        target_node = node_id
        if account_id:
            acc, _ = self._resolve_accessible_account(actor, account_id)
            args["uid"] = acc.vendor_uid
            target_node = target_node or acc.node_id
        if platform:
            args["platform"] = platform
        _, res = self._execute_mcp(
            actor=actor,
            node_id=target_node,
            account_id=account_id,
            action_type="flow_report",
            tool_name="vcat_get_flow_report",
            arguments=args,
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "读取流速监控报告失败")
        payload = res.payload if isinstance(res.payload, dict) else {"list": []}
        cleaned = []
        for item in payload.get("list") or []:
            if isinstance(item, dict):
                row = dict(item)
                for k in ("view", "views", "delta", "increment", "like", "likes", "comment", "comments"):
                    if k in row:
                        row[k] = clean_metric_number(row[k])
                cleaned.append(row)
        return {**payload, "list": cleaned}

    def list_punish_feeds(self, actor: User, account_id: str | None = None) -> dict[str, Any]:
        accs = self.list_accounts(actor)
        if account_id:
            accs = [a for a in accs if a["id"] == account_id]
        if not accs:
            return {"list": [], "total": 0}
        uid_to_acc = {a["vendor_uid"]: a["id"] for a in accs}
        by_node: dict[str, list[str]] = {}
        for a in accs:
            by_node.setdefault(a["node_id"], []).append(a["vendor_uid"])
        items: list[dict[str, Any]] = []
        for nid, uids in by_node.items():
            args: dict[str, Any] = {"page": 1, "pageSize": 30}
            if len(uids) == 1:
                args["uid"] = uids[0]
            _, res = self._execute_mcp(
                actor=actor,
                node_id=nid,
                account_id=account_id,
                action_type="list_punish_feeds",
                tool_name="vcat_list_punish_feeds",
                arguments=args,
                requires_rate_limit=False,
            )
            if res.ok and isinstance(res.payload, dict):
                for raw in res.payload.get("list") or []:
                    if isinstance(raw, dict):
                        v_uid = str(raw.get("uid") or raw.get("accountUid") or "")
                        if not v_uid or v_uid in uid_to_acc:
                            items.append({**raw, "account_id": uid_to_acc.get(v_uid) or (account_id or accs[0]["id"])})
        return {"list": items, "total": len(items)}

    def hide_punish_post(self, actor: User, account_id: str, export_id: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(422, "隐藏异常作品必须二次确认 confirm=true")
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="hide_punish_post",
            tool_name="vcat_hide_punish_post",
            arguments={"uid": acc.vendor_uid, "exportId": export_id},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "隐藏异常作品失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def get_post_trend(
        self,
        actor: User,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        node_id = None
        if account_id:
            acc, _ = self._resolve_accessible_account(actor, account_id)
            args["uid"] = acc.vendor_uid
            node_id = acc.node_id
        if start_date:
            args["startDate"] = start_date
        if end_date:
            args["endDate"] = end_date
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="post_trend",
            tool_name="vcat_get_post_trend",
            arguments=args,
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "读取作品趋势失败")
        payload = res.payload if isinstance(res.payload, dict) else {}
        daily = []
        for row in payload.get("daily") or payload.get("list") or []:
            if isinstance(row, dict):
                r = dict(row)
                for k in ("view", "views", "like", "likes", "comment", "comments", "forward", "forwards", "favorite", "favorites", "fansAdd"):
                    if k in r:
                        r[k] = clean_metric_number(r[k])
                daily.append(r)
        return {**payload, "daily": daily}

    def get_post_daily(
        self,
        actor: User,
        post_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        with self.Session() as db:
            p = db.get(PublishedPost, post_id)
            if not p:
                raise HTTPException(404, "作品不存在")
            acc = db.get(PlatformAccount, p.account_id) if p.account_id else None
            node = db.get(ExecutorNode, acc.node_id) if acc else None
            if actor.role != "admin" and not (p.ip_id and can_ip(db, actor, p.ip_id)) and not (acc and self._can_access_account(db, actor, acc, node)):
                raise HTTPException(403, "无权查看该作品分日数据")
            args: dict[str, Any] = {"postId": p.vendor_post_id, "uid": p.vendor_uid}
            if start_date:
                args["startDate"] = start_date
            if end_date:
                args["endDate"] = end_date
            node_id = acc.node_id if acc else None
            account_id = p.account_id
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="post_daily",
            tool_name="vcat_get_post_daily",
            arguments=args,
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "读取作品分日数据失败")
        payload = res.payload if isinstance(res.payload, dict) else {"list": []}
        rows = []
        for item in payload.get("list") or payload.get("daily") or []:
            if isinstance(item, dict):
                r = dict(item)
                for k in ("view", "views", "like", "likes", "comment", "comments", "forward", "forwards", "favorite", "favorites", "fansAdd"):
                    if k in r:
                        r[k] = clean_metric_number(r[k])
                rows.append(r)
        return {**payload, "list": rows}

    def update_post_visibility(
        self,
        actor: User,
        post_id: str,
        *,
        visible: int,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(422, "修改作品可见性必须二次确认 confirm=true")
        with self.Session() as db:
            p = db.get(PublishedPost, post_id)
            if not p:
                raise HTTPException(404, "作品不存在")
            acc = db.get(PlatformAccount, p.account_id) if p.account_id else None
            node = db.get(ExecutorNode, acc.node_id) if acc else None
            if actor.role != "admin" and not (p.ip_id and can_ip(db, actor, p.ip_id)) and not (acc and self._can_access_account(db, actor, acc, node)):
                raise HTTPException(403, "无权修改该作品可见性")
            vendor_post_id = p.vendor_post_id
            vendor_uid = p.vendor_uid
            node_id = acc.node_id if acc else None
            account_id = p.account_id
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=account_id,
            action_type="update_post_visibility",
            tool_name="vcat_create_update_visible_single_task",
            arguments={"postIds": [vendor_post_id], "uid": vendor_uid, "visible": int(visible)},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "修改作品可见性失败")
        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            p = db.get(PublishedPost, post_id)
            if p:
                p.visibility = "仅自己可见" if int(visible) == 1 else ("公开" if int(visible) == 0 else str(visible))
                p.updated_at = now()
            db.commit()
        return res.payload if isinstance(res.payload, dict) else {"ok": True, "visibility": visible}

    def set_account_remark(self, actor: User, account_id: str, remark: str) -> dict[str, Any]:
        acc, _ = self._resolve_accessible_account(actor, account_id)
        _, res = self._execute_mcp(
            actor=actor,
            node_id=acc.node_id,
            account_id=acc.id,
            action_type="set_account_remark",
            tool_name="vcat_set_account_remark",
            arguments={"uid": acc.vendor_uid, "remark": remark},
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "设置账号备注失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}

    def list_duplicate_accounts(self, actor: User, node_id: str | None = None) -> dict[str, Any]:
        if actor.role != "admin":
            raise HTTPException(403, "仅管理员可检查重复账号")
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="list_duplicate_accounts",
            tool_name="vcat_list_duplicate_accounts",
            arguments={},
            requires_rate_limit=False,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "检查重复账号失败")
        return res.payload if isinstance(res.payload, dict) else {"groups": []}

    def dedupe_accounts(self, actor: User, *, confirm: bool = False, uids: list[str] | None = None, node_id: str | None = None) -> dict[str, Any]:
        if actor.role != "admin":
            raise HTTPException(403, "仅管理员可执行账号去重")
        if not confirm:
            raise HTTPException(422, "执行账号去重必须二次确认 confirm=true")
        args: dict[str, Any] = {"confirm": True}
        if uids:
            args["uids"] = uids
        _, res = self._execute_mcp(
            actor=actor,
            node_id=node_id,
            account_id=None,
            action_type="dedupe_accounts",
            tool_name="vcat_dedupe_accounts",
            arguments=args,
            requires_rate_limit=True,
        )
        if not res.ok:
            raise HTTPException(502, res.error_message or "账号去重失败")
        return res.payload if isinstance(res.payload, dict) else {"ok": True}
