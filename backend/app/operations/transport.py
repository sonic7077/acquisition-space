"""Unified MCP command contract and dual-mode transport router."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Literal, Protocol
import urllib.parse

import httpx
from sqlalchemy import select

from ..db import AgentCommand, ExecutorNode, Record, now, uid

ALLOWED_TOOLS = {
    # Read-only tools
    "vcat_list_accounts",
    "vcat_get_overview",
    "vcat_get_account_stats",
    "vcat_get_publish_options",
    "vcat_list_publish_tasks",
    "vcat_get_publish_task_stat",
    "vcat_list_drafts",
    "vcat_list_posts",
    "vcat_get_post",
    "vcat_get_post_daily",
    "vcat_get_post_stat",
    "vcat_get_post_trend",
    "vcat_get_post_sync",
    "vcat_list_post_sync_records",
    "vcat_list_monitor_tasks",
    "vcat_get_flow_report",
    "vcat_get_comment_summary",
    "vcat_list_comments",
    "vcat_get_unread_messages",
    "vcat_list_message_sessions",
    "vcat_get_session_messages",
    "vcat_list_groups",
    "vcat_list_tags",
    "vcat_list_login_links",
    "vcat_list_duplicate_accounts",
    "vcat_list_verify_results",
    "vcat_get_verify_result",
    "vcat_list_punish_feeds",
    "vcat_get_verify_auto_setting",
    "vcat_list_verify_logs",
    "vcat_get_verify_links",
    "vcat_draft_reply",
    # Controlled write tools
    "vcat_create_login_link",
    "vcat_delete_login_link",
    "vcat_remove_account",
    "vcat_set_account_remark",
    "vcat_dedupe_accounts",
    "vcat_verify_account",
    "vcat_verify_accounts",
    "vcat_refresh_account_stats",
    "vcat_start_post_sync",
    "vcat_create_draft",
    "vcat_create_publish_task",
    "vcat_publish_draft",
    "vcat_cancel_publish_task",
    "vcat_set_publish_priority",
    "vcat_reply_comment",
    "vcat_like_comment",
    "vcat_set_top_comment",
    "vcat_send_message",
    "vcat_set_monitor_status",
    "vcat_run_monitor_task",
    "vcat_hide_punish_post",
    "vcat_create_update_visible_single_task",
}

SENSITIVE_KEY_RE = re.compile(r"(token|cookie|secret|password|auth|sharecode|encrypted_url)", re.I)


def clean_metric_number(value: Any, *, as_float: bool = False) -> int | float | None:
    """Convert Xiaovmao sentinel values like -1 or empty strings into None."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        num = float(value) if as_float else int(float(value))
    except (ValueError, TypeError):
        return None
    if num < 0:
        return None
    return num


def redact_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return value
    parts = [p for p in parsed.path.split("/") if p]
    if ("eternal-login" in parts or "verify-helper" in parts) and parts:
        parts[-1] = "<REDACTED>"
    path = ("/" + "/".join(parts)) if parts else parsed.path
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = "&".join(f"{k}=<REDACTED>" for k, _ in pairs)
    frag = "<REDACTED>" if parsed.fragment else ""
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, frag))


def sanitize_summary(obj: Any, key_hint: str = "") -> Any:
    if isinstance(obj, dict):
        return {k: sanitize_summary(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_summary(item, key_hint) for item in obj]
    if isinstance(obj, str):
        if "vcat_mcp_" in obj or SENSITIVE_KEY_RE.search(key_hint):
            return "<REDACTED>"
        if obj.startswith(("http://", "https://")):
            return redact_url(obj)
        return re.sub(r"https?://[^\s\"'<>]+", lambda m: redact_url(m.group(0)), obj)
    return obj


@dataclass(frozen=True)
class MediaRef:
    asset_id: str
    sha256: str
    filename: str
    local_server_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "sha256": self.sha256,
            "filename": self.filename,
        }


@dataclass(frozen=True)
class VcatActionCommand:
    action_id: str
    node_id: str
    tool_name: str
    arguments: dict[str, Any]
    media_refs: list[MediaRef] = field(default_factory=list)
    timeout_sec: int = 60
    requires_rate_limit: bool = True
    async_only: bool = False


@dataclass(frozen=True)
class VcatActionResult:
    action_id: str
    ok: bool
    status: Literal["SUCCEEDED", "FAILED", "UNKNOWN", "NODE_OFFLINE", "TIMEOUT", "QUEUED"]
    payload: Any = None
    error_message: str | None = None
    executed_at: str = field(default_factory=now)


class McpExecutionTransport(Protocol):
    def execute(self, cmd: VcatActionCommand) -> VcatActionResult: ...


def _parse_sse_or_json(content_type: str, text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    if "text/event-stream" in (content_type or ""):
        objs = []
        for line in text.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw:
                    objs.append(json.loads(raw))
        return objs[-1] if objs else {}
    return json.loads(text)


def extract_tool_payload(result: dict[str, Any]) -> Any:
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    items = result.get("content") or []
    parsed: list[Any] = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "text":
            raw = item.get("text", "")
            try:
                parsed.append(json.loads(raw))
            except Exception:
                parsed.append(raw)
        else:
            parsed.append(item)
    if len(parsed) == 1:
        return parsed[0]
    return parsed


class StreamableHttpMcpClient:
    """Thread-safe Streamable HTTP MCP client supporting SSE + Mcp-Session-Id."""

    _rate_lock = threading.Lock()
    _last_call_ts = 0.0

    def __init__(self, mcp_url: str, token: str, min_interval_sec: float = 0.0) -> None:
        self.mcp_url = mcp_url
        self.token = token
        self.min_interval_sec = min_interval_sec
        self.session_id: str | None = None
        self.req_id = 0

    def _post(self, client: httpx.Client, payload: dict[str, Any], notification: bool = False) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        resp = client.post(self.mcp_url, headers=headers, json=payload)
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        resp.raise_for_status()
        if notification:
            return {}
        return _parse_sse_or_json(resp.headers.get("Content-Type", ""), resp.text)

    def initialize(self, client: httpx.Client) -> dict[str, Any]:
        self.req_id += 1
        res = self._post(
            client,
            {
                "jsonrpc": "2.0",
                "id": self.req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "acquisition-space", "version": "0.1.0"},
                },
            },
        )
        self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
        return res.get("result") or {}

    def list_tools(self, timeout_sec: int = 15) -> list[dict[str, Any]]:
        with httpx.Client(timeout=timeout_sec, follow_redirects=False, trust_env=False) as client:
            self.initialize(client)
            tools: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                self.req_id += 1
                params = {"cursor": cursor} if cursor else {}
                res = self._post(
                    client,
                    {"jsonrpc": "2.0", "id": self.req_id, "method": "tools/list", "params": params},
                )
                result = res.get("result") or {}
                tools.extend(result.get("tools") or [])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            return tools

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_sec: int = 60,
        respect_rate_limit: bool = True,
    ) -> tuple[bool, Any, str | None]:
        if tool_name not in ALLOWED_TOOLS:
            raise ValueError(f"Tool not in whitelist: {tool_name}")
        if respect_rate_limit and self.min_interval_sec > 0:
            with self._rate_lock:
                elapsed = time.monotonic() - StreamableHttpMcpClient._last_call_ts
                if StreamableHttpMcpClient._last_call_ts > 0 and elapsed < self.min_interval_sec:
                    time.sleep(self.min_interval_sec - elapsed)
                StreamableHttpMcpClient._last_call_ts = time.monotonic()

        with httpx.Client(timeout=timeout_sec, follow_redirects=False, trust_env=False) as client:
            self.initialize(client)
            self.req_id += 1
            res = self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": self.req_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
            )
        if "error" in res:
            err = res["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return False, err, str(msg)
        result = res.get("result") or {}
        payload = extract_tool_payload(result)
        is_err = bool(result.get("isError"))
        err_msg = str(payload) if is_err else None
        return (not is_err), payload, err_msg


def materialize_local_media(staging_dir: Path, media_refs: list[MediaRef]) -> dict[str, str]:
    """Verify SHA-256 and expose assets with original extensions for Xiaovmao local path validation."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for ref in media_refs:
        if not ref.local_server_path:
            raise FileNotFoundError(f"Missing local asset path for {ref.asset_id}")
        src = Path(ref.local_server_path)
        if not src.is_file():
            raise FileNotFoundError(f"Asset file missing: {ref.asset_id}")
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        if digest != ref.sha256:
            raise ValueError(f"SHA-256 mismatch for asset {ref.asset_id}")
        ext = Path(ref.filename or "").suffix.lower() or ".bin"
        dst = (staging_dir / f"{ref.asset_id}_{ref.sha256[:12]}{ext}").resolve()
        if not dst.exists():
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        mapping[f"$ASSET:{ref.asset_id}"] = str(dst)
    return mapping


def replace_asset_placeholders(value: Any, path_map: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: replace_asset_placeholders(v, path_map) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_asset_placeholders(v, path_map) for v in value]
    if isinstance(value, str) and value in path_map:
        return path_map[value]
    return value


class DirectHttpMcpTransport:
    """Mode 1A: Backend and Xiaovmao run on the same host (direct 127.0.0.1 MCP)."""

    def __init__(self, mcp_url: str, token: str, staging_dir: Path, min_interval_sec: float = 0.0) -> None:
        self.mcp_url = mcp_url
        self.token = token
        self.staging_dir = staging_dir
        self.min_interval_sec = min_interval_sec

    def execute(self, cmd: VcatActionCommand) -> VcatActionResult:
        if cmd.tool_name not in ALLOWED_TOOLS:
            return VcatActionResult(cmd.action_id, False, "FAILED", error_message=f"不允许调用未授权工具: {cmd.tool_name}")
        try:
            path_map = materialize_local_media(self.staging_dir, cmd.media_refs)
            args = replace_asset_placeholders(cmd.arguments, path_map)
        except Exception as exc:
            return VcatActionResult(cmd.action_id, False, "FAILED", error_message=f"媒体预处理失败: {exc}")

        client = StreamableHttpMcpClient(self.mcp_url, self.token, min_interval_sec=self.min_interval_sec)
        try:
            ok, payload, err_msg = client.call_tool(
                cmd.tool_name,
                args,
                timeout_sec=cmd.timeout_sec,
                respect_rate_limit=cmd.requires_rate_limit,
            )
            return VcatActionResult(
                action_id=cmd.action_id,
                ok=ok,
                status="SUCCEEDED" if ok else "FAILED",
                payload=payload,
                error_message=err_msg,
            )
        except httpx.TimeoutException as exc:
            return VcatActionResult(cmd.action_id, False, "TIMEOUT", error_message=f"MCP调用超时: {exc}")
        except httpx.ConnectError as exc:
            return VcatActionResult(cmd.action_id, False, "NODE_OFFLINE", error_message=f"无法连接小V猫MCP: {exc}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return VcatActionResult(cmd.action_id, False, "NODE_OFFLINE", error_message="小V猫MCP令牌无效或未开启服务")
            return VcatActionResult(cmd.action_id, False, "UNKNOWN", error_message=f"MCP返回HTTP {exc.response.status_code}")
        except Exception as exc:
            return VcatActionResult(cmd.action_id, False, "UNKNOWN", error_message=str(exc))


class RelayEventBus:
    """In-memory per-node and per-command wakeup bus for HTTP long-polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cmd_conds: dict[str, threading.Condition] = {}
        self._cmd_versions: dict[str, int] = {}
        self._active_pollers: dict[str, int] = {}
        self._res_conds: dict[str, threading.Condition] = {}
        self._ready_results: set[str] = set()

    def _cmd_cond(self, node_id: str) -> threading.Condition:
        with self._lock:
            cond = self._cmd_conds.get(node_id)
            if cond is None:
                cond = threading.Condition(self._lock)
                self._cmd_conds[node_id] = cond
            return cond

    def current_command_version(self, node_id: str) -> int:
        with self._lock:
            return self._cmd_versions.get(node_id, 0)

    def notify_command(self, node_id: str) -> None:
        with self._lock:
            self._cmd_versions[node_id] = self._cmd_versions.get(node_id, 0) + 1
            cond = self._cmd_conds.get(node_id)
            if cond is not None:
                cond.notify_all()

    def wait_for_command(self, node_id: str, since_version: int, timeout_sec: float) -> bool:
        if timeout_sec <= 0:
            return self.current_command_version(node_id) > since_version
        cond = self._cmd_cond(node_id)
        with cond:
            if self._cmd_versions.get(node_id, 0) > since_version:
                return True
            cond.wait(timeout=timeout_sec)
            return self._cmd_versions.get(node_id, 0) > since_version

    @contextmanager
    def track_poller(self, node_id: str):
        with self._lock:
            self._active_pollers[node_id] = self._active_pollers.get(node_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                cur = self._active_pollers.get(node_id, 0) - 1
                if cur <= 0:
                    self._active_pollers.pop(node_id, None)
                else:
                    self._active_pollers[node_id] = cur

    def is_long_polling(self, node_id: str) -> bool:
        with self._lock:
            return self._active_pollers.get(node_id, 0) > 0

    def notify_result(self, action_id: str) -> None:
        with self._lock:
            self._ready_results.add(action_id)
            cond = self._res_conds.get(action_id)
            if cond is not None:
                cond.notify_all()

    def wait_for_result(self, action_id: str, timeout_sec: float) -> bool:
        with self._lock:
            if action_id in self._ready_results:
                self._ready_results.discard(action_id)
                self._res_conds.pop(action_id, None)
                return True
            if timeout_sec <= 0:
                return False
            cond = self._res_conds.get(action_id)
            if cond is None:
                cond = threading.Condition(self._lock)
                self._res_conds[action_id] = cond
            cond.wait(timeout=timeout_sec)
            ready = action_id in self._ready_results
            if ready:
                self._ready_results.discard(action_id)
                self._res_conds.pop(action_id, None)
            return ready

    def clear_result_waiter(self, action_id: str) -> None:
        with self._lock:
            self._ready_results.discard(action_id)
            self._res_conds.pop(action_id, None)


class AgentRelayMcpTransport:
    """Mode 1B (server_relay) & Mode 2 (client_local): Persist command for outbound agent."""

    def __init__(
        self,
        Session,
        node_id: str,
        wait_timeout_sec: float = 20.0,
        relay_bus: RelayEventBus | None = None,
    ) -> None:
        self.Session = Session
        self.node_id = node_id
        self.wait_timeout_sec = wait_timeout_sec
        self.relay_bus = relay_bus

    def _is_node_online(self, db, node: ExecutorNode | None) -> bool:
        if not node or node.status == "revoked" or node.status == "mcp_error":
            return False
        if self.relay_bus and self.relay_bus.is_long_polling(node.id):
            return True
        if node.status != "online" or not node.last_heartbeat_at:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        return node.last_heartbeat_at >= cutoff

    def execute(self, cmd: VcatActionCommand) -> VcatActionResult:
        if cmd.tool_name not in ALLOWED_TOOLS:
            return VcatActionResult(cmd.action_id, False, "FAILED", error_message=f"不允许调用未授权工具: {cmd.tool_name}")

        with self.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            node = db.get(ExecutorNode, self.node_id)
            if not self._is_node_online(db, node):
                db.commit()
                return VcatActionResult(
                    action_id=cmd.action_id,
                    ok=False,
                    status="NODE_OFFLINE",
                    error_message="执行节点不在线或小V猫未就绪，请先确认节点在线",
                )
            existing = db.scalar(select(AgentCommand).where(AgentCommand.action_id == cmd.action_id))
            if not existing:
                existing = AgentCommand(
                    id=uid(),
                    action_id=cmd.action_id,
                    node_id=self.node_id,
                    tool_name=cmd.tool_name,
                    arguments_json=cmd.arguments,
                    media_refs_json=[m.to_dict() for m in cmd.media_refs],
                    status="queued",
                )
                db.add(existing)
            db.commit()

        if self.relay_bus:
            self.relay_bus.notify_command(self.node_id)

        if cmd.async_only:
            return VcatActionResult(action_id=cmd.action_id, ok=True, status="QUEUED")

        deadline = time.monotonic() + min(float(cmd.timeout_sec), self.wait_timeout_sec)
        try:
            while time.monotonic() < deadline:
                with self.Session() as db:
                    row = db.scalar(select(AgentCommand).where(AgentCommand.action_id == cmd.action_id))
                    if row and row.status in ("succeeded", "failed", "unknown"):
                        res = row.result_json or {}
                        status_map = {"succeeded": "SUCCEEDED", "failed": "FAILED", "unknown": "UNKNOWN"}
                        return VcatActionResult(
                            action_id=cmd.action_id,
                            ok=bool(res.get("ok", row.status == "succeeded")),
                            status=status_map.get(row.status, "UNKNOWN"),
                            payload=res.get("payload"),
                            error_message=res.get("error_message"),
                            executed_at=row.finished_at or now(),
                        )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self.relay_bus:
                    self.relay_bus.wait_for_result(cmd.action_id, timeout_sec=min(0.5, remaining))
                else:
                    time.sleep(min(0.15, remaining))
        finally:
            if self.relay_bus:
                self.relay_bus.clear_result_waiter(cmd.action_id)

        return VcatActionResult(
            action_id=cmd.action_id,
            ok=False,
            status="TIMEOUT",
            error_message="等待执行代理回执超时，请稍后刷新或核对任务状态",
        )


class TransportRouter:
    """Resolve node and concrete McpExecutionTransport uniformly across both modes."""

    def __init__(self, app) -> None:
        self.app = app

    def _is_node_ready(self, node: ExecutorNode | None) -> bool:
        if not node or node.status == "revoked" or node.status == "mcp_error":
            return False
        if node.transport_mode == "direct_http":
            return bool(node.token_encrypted) and node.status == "online"
        bus: RelayEventBus | None = getattr(self.app.state, "relay_bus", None)
        if bus and bus.is_long_polling(node.id):
            return True
        if node.status != "online" or not node.last_heartbeat_at:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        return node.last_heartbeat_at >= cutoff

    def ensure_direct_node(self, db) -> ExecutorNode:
        settings_row = db.get(Record, "system-settings")
        settings = settings_row.payload if settings_row else {}
        ex = settings.get("executor", {})
        mcp_url = (ex.get("base_url") or "http://127.0.0.1:3199/mcp").strip()
        tok_enc = ex.get("token_encrypted")
        node = db.get(ExecutorNode, "server-direct-default")
        if not node:
            node = ExecutorNode(
                id="server-direct-default",
                name="本地直连兜底小V猫",
                node_scope="server_direct",
                owner_user_id=None,
                transport_mode="direct_http",
                mcp_url=mcp_url,
                token_encrypted=tok_enc,
                status="online" if tok_enc else "offline",
            )
            db.add(node)
            db.flush()
        else:
            node.mcp_url = mcp_url
            if tok_enc:
                node.token_encrypted = tok_enc
                if node.status == "offline":
                    node.status = "online"
            else:
                node.token_encrypted = None
                node.status = "offline"
        return node

    def resolve_node(self, db, *, node_id: str | None = None, actor=None) -> ExecutorNode:
        settings_row = db.get(Record, "system-settings")
        settings = settings_row.payload if settings_row else {}
        ex = settings.get("executor", {})
        default_mode = ex.get("mode", "manual")
        default_node_id = (ex.get("default_node_id") or "").strip()

        if node_id:
            if node_id == "server-direct-default":
                self.ensure_direct_node(db)
            node = db.get(ExecutorNode, node_id)
            if not node:
                raise ValueError("执行节点不存在")
            if node.status == "revoked":
                raise ValueError("执行节点已吊销，请重新配对")
            if node.node_scope == "client_local" and actor is not None:
                if actor.role != "admin" and node.owner_user_id != actor.id:
                    raise PermissionError("只能操作绑定到本人账号的本机小V猫节点")
            return node

        if default_mode == "server_direct":
            node = self.ensure_direct_node(db)
            if not node.token_encrypted:
                raise ValueError("服务器直连模式未配置小V猫 MCP 令牌")
            return node

        if default_mode == "client_local":
            if not actor:
                raise ValueError("前端本机模式需要明确操作者")
            node = db.scalar(
                select(ExecutorNode)
                .where(
                    ExecutorNode.node_scope == "client_local",
                    ExecutorNode.owner_user_id == actor.id,
                    ExecutorNode.status != "revoked",
                )
                .order_by(ExecutorNode.last_heartbeat_at.desc())
            )
            if not node:
                raise ValueError("当前账号尚未配对本机小V猫代理")
            return node

        if default_mode in ("server_relay", "hybrid"):
            if default_node_id:
                if default_node_id == "server-direct-default":
                    self.ensure_direct_node(db)
                dn = db.get(ExecutorNode, default_node_id)
                if dn and dn.status != "revoked":
                    can_use = (
                        dn.node_scope in ("server_relay", "server_direct")
                        or (actor and actor.role == "admin")
                        or (dn.node_scope == "client_local" and actor and dn.owner_user_id == actor.id)
                    )
                    usable = dn.transport_mode != "direct_http" or bool(dn.token_encrypted)
                    if can_use and usable and (default_mode == "server_relay" or self._is_node_ready(dn)):
                        return dn

            relay_nodes = list(
                db.scalars(
                    select(ExecutorNode)
                    .where(
                        ExecutorNode.node_scope == "server_relay",
                        ExecutorNode.status != "revoked",
                    )
                    .order_by(ExecutorNode.last_heartbeat_at.desc())
                )
            )
            if default_mode == "server_relay":
                if relay_nodes:
                    return relay_nodes[0]
                direct_node = self.ensure_direct_node(db)
                if direct_node.token_encrypted:
                    return direct_node
            else:
                for rn in relay_nodes:
                    if self._is_node_ready(rn):
                        return rn
                if actor:
                    client_nodes = list(
                        db.scalars(
                            select(ExecutorNode)
                            .where(
                                ExecutorNode.node_scope == "client_local",
                                ExecutorNode.owner_user_id == actor.id,
                                ExecutorNode.status != "revoked",
                            )
                            .order_by(ExecutorNode.last_heartbeat_at.desc())
                        )
                    )
                    for cn in client_nodes:
                        if self._is_node_ready(cn):
                            return cn
                direct_node = self.ensure_direct_node(db)
                if self._is_node_ready(direct_node):
                    return direct_node
                if relay_nodes:
                    return relay_nodes[0]
                if actor:
                    cn = db.scalar(
                        select(ExecutorNode)
                        .where(
                            ExecutorNode.node_scope == "client_local",
                            ExecutorNode.owner_user_id == actor.id,
                            ExecutorNode.status != "revoked",
                        )
                        .order_by(ExecutorNode.last_heartbeat_at.desc())
                    )
                    if cn:
                        return cn

        raise ValueError("当前未启用可执行的小V猫节点，请先在系统设置中配置执行器模式")

    def transport_for(self, db, node: ExecutorNode, *, sync_wait_sec: float = 20.0) -> McpExecutionTransport:
        if node.transport_mode == "direct_http":
            token = self.app.state.secrets.decrypt(node.token_encrypted or "")
            if not token:
                raise ValueError("执行节点缺少 MCP 访问令牌")
            interval = float(os.environ.get("VCAT_MCP_MIN_INTERVAL", "0"))
            return DirectHttpMcpTransport(
                mcp_url=node.mcp_url or "http://127.0.0.1:3199/mcp",
                token=token,
                staging_dir=self.app.state.data_dir / "vcat_staging",
                min_interval_sec=interval,
            )
        return AgentRelayMcpTransport(
            self.app.state.Session,
            node.id,
            wait_timeout_sec=sync_wait_sec,
            relay_bus=getattr(self.app.state, "relay_bus", None),
        )
