#!/usr/bin/env python3
"""Unified channel-promoter execution agent for both server_managed and client_local modes.

Runs on the machine where the local channel promoter desktop client is installed,
polls outbound commands from acquisition-space, downloads and SHA-256 verifies
media assets, invokes local http://127.0.0.1:3199/mcp with rate limiting,
and posts results back.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

ALLOWED_AGENT_TOOLS = {
    "vcat_list_accounts",
    "vcat_get_account_stats",
    "vcat_get_publish_options",
    "vcat_list_publish_tasks",
    "vcat_list_drafts",
    "vcat_list_posts",
    "vcat_get_post_trend",
    "vcat_get_post_daily",
    "vcat_list_comments",
    "vcat_get_comment_summary",
    "vcat_list_message_sessions",
    "vcat_get_session_messages",
    "vcat_draft_reply",
    "vcat_list_monitor_tasks",
    "vcat_get_flow_report",
    "vcat_list_punish_feeds",
    "vcat_list_verify_results",
    "vcat_list_login_links",
    "vcat_get_verify_links",
    "vcat_list_duplicate_accounts",
    "vcat_create_login_link",
    "vcat_delete_login_link",
    "vcat_remove_account",
    "vcat_set_account_remark",
    "vcat_dedupe_accounts",
    "vcat_verify_account",
    "vcat_refresh_account_stats",
    "vcat_start_post_sync",
    "vcat_get_post_sync",
    "vcat_create_draft",
    "vcat_create_publish_task",
    "vcat_publish_draft",
    "vcat_cancel_publish_task",
    "vcat_reply_comment",
    "vcat_like_comment",
    "vcat_set_top_comment",
    "vcat_send_message",
    "vcat_set_monitor_status",
    "vcat_run_monitor_task",
    "vcat_hide_punish_post",
    "vcat_create_update_visible_single_task",
}

_RAW_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_MASKED_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d\*{4}\d{4})(?!\d)")


def mask_mobile_text(raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    if not text:
        return None
    m_raw = _RAW_MOBILE_RE.search(text)
    if m_raw:
        return f"{m_raw.group(1)}****{m_raw.group(3)}"
    m_masked = _MASKED_MOBILE_RE.search(text)
    if m_masked:
        return m_masked.group(1)
    return None


def _sanitize_username_or_mobile(raw: Any) -> tuple[str | None, str | None]:
    if raw is None or isinstance(raw, bool):
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    masked = mask_mobile_text(text)
    if masked and _RAW_MOBILE_RE.fullmatch(text):
        return None, masked
    if masked:
        return _RAW_MOBILE_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", text), masked
    return text, None


def build_promoter_display_label(
    *,
    remark: str | None = "",
    nickname: str | None = "",
    username: str | None = "",
    masked_mobile: str | None = "",
    external_user_id: str | None = "",
) -> str:
    clean_remark = (remark or "").strip()
    if clean_remark:
        return clean_remark
    clean_nick = _RAW_MOBILE_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", (nickname or "").strip())
    clean_user, extracted_mobile = _sanitize_username_or_mobile(username)
    clean_mobile = mask_mobile_text(masked_mobile) or extracted_mobile
    if clean_nick:
        if clean_mobile and clean_mobile not in clean_nick:
            return f"{clean_nick}（{clean_mobile}）"
        return clean_nick
    if clean_user:
        if clean_mobile and clean_mobile not in clean_user:
            return f"{clean_user}（{clean_mobile}）"
        return clean_user
    if clean_mobile:
        return clean_mobile
    ext = (external_user_id or "").strip()
    return f"渠道推广器 #{ext}" if ext else "未命名渠道推广器"


def _read_local_user_db(db_path: Path) -> dict[str, Any] | None:
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1.0)
    except Exception:
        return None
    try:
        cur = con.cursor()
        cols = {str(r[1]) for r in cur.execute("PRAGMA table_info(user)").fetchall()}
        if not cols or "user_id" not in cols:
            return None
        want = [c for c in ("user_id", "sub_id", "name", "nickname", "avatar", "lastLogin", "phone", "mobile") if c in cols]
        row = cur.execute(f"SELECT {', '.join(want)} FROM user LIMIT 1").fetchone()
        if not row:
            return None
        data = dict(zip(want, row))
        ext_uid = str(data.get("user_id") or "").strip()
        if not ext_uid:
            return None
        sub_val = data.get("sub_id")
        ext_sub = str(sub_val).strip() if sub_val not in (None, "") else None
        raw_nick = str(data.get("nickname") or "").strip()
        safe_nick = _RAW_MOBILE_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", raw_nick)
        safe_username, mobile_from_name = _sanitize_username_or_mobile(data.get("name"))
        masked_mobile = (
            mask_mobile_text(data.get("phone"))
            or mask_mobile_text(data.get("mobile"))
            or mask_mobile_text(raw_nick)
            or mobile_from_name
        )
        display_label = build_promoter_display_label(
            nickname=safe_nick,
            username=safe_username,
            masked_mobile=masked_mobile,
            external_user_id=ext_uid,
        )
        return {
            "external_user_id": ext_uid,
            "external_sub_id": ext_sub,
            "nickname": safe_nick or safe_username or f"渠道推广器 #{ext_uid}",
            "username": safe_username,
            "masked_mobile": masked_mobile,
            "display_label": display_label,
            "avatar": str(data.get("avatar") or "") or None,
            "last_login": str(data.get("lastLogin") or "") or None,
        }
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass


def detect_local_promoter_state(provider: str = "vcat", data_home: Path | str | None = None) -> dict[str, Any]:
    clean_provider = (provider or "vcat").strip().lower() or "vcat"
    empty = {"detected": False, "provider": clean_provider, "active_account": None, "known_accounts": []}
    if clean_provider != "vcat":
        return empty
    raw_home = data_home or os.environ.get("PROMOTER_DATA_HOME") or os.environ.get("VCAT_HOME")
    base = Path(raw_home).expanduser() if raw_home else (Path.home() / ".vcat")
    if not base.is_dir():
        return empty

    cfg: dict[str, Any] = {}
    cfg_path = base / "local.config"
    if cfg_path.is_file():
        try:
            parsed = json.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(parsed, dict):
                cfg = parsed
        except Exception:
            cfg = {}

    mcp_setting = cfg.get("mcp-setting") if isinstance(cfg.get("mcp-setting"), dict) else {}
    active_uid_raw = mcp_setting.get("userId") or cfg.get("Login_default_id")
    active_uid = str(active_uid_raw).strip() if active_uid_raw not in (None, "") else None
    write_modules = mcp_setting.get("writeModules") if isinstance(mcp_setting.get("writeModules"), dict) else {}
    service_enabled = bool(mcp_setting.get("enabled", False))
    service_port = int(mcp_setting.get("port") or 3199) if str(mcp_setting.get("port") or "").isdigit() else 3199

    known_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        entries = []
    for child in entries:
        if not child.is_dir():
            continue
        db_file = child / "vcat.db"
        if not db_file.is_file():
            continue
        info = _read_local_user_db(db_file)
        if info:
            known_by_key[(info["external_user_id"], info.get("external_sub_id") or "")] = info

    if not known_by_key and not active_uid:
        return empty

    known_accounts: list[dict[str, Any]] = []
    active_account: dict[str, Any] | None = None
    for info in known_by_key.values():
        is_active = bool(active_uid and info["external_user_id"] == active_uid)
        known_accounts.append(
            {
                "external_user_id": info["external_user_id"],
                "external_sub_id": info.get("external_sub_id"),
                "nickname": info["nickname"],
                "username": info.get("username"),
                "masked_mobile": info.get("masked_mobile"),
                "display_label": info["display_label"],
                "is_active": is_active,
            }
        )
        if is_active and active_account is None:
            active_account = {
                **info,
                "service_enabled": service_enabled,
                "service_port": service_port,
                "write_modules": {str(k): bool(v) for k, v in write_modules.items()},
            }

    if active_account is None and active_uid:
        label = build_promoter_display_label(external_user_id=active_uid)
        active_account = {
            "external_user_id": active_uid,
            "external_sub_id": None,
            "nickname": label,
            "username": None,
            "masked_mobile": None,
            "display_label": label,
            "avatar": None,
            "last_login": None,
            "service_enabled": service_enabled,
            "service_port": service_port,
            "write_modules": {str(k): bool(v) for k, v in write_modules.items()},
        }
        known_accounts.insert(
            0,
            {
                "external_user_id": active_uid,
                "external_sub_id": None,
                "nickname": label,
                "username": None,
                "masked_mobile": None,
                "display_label": label,
                "is_active": True,
            },
        )

    known_accounts.sort(key=lambda x: (not x["is_active"], str(x.get("display_label") or ""), str(x["external_user_id"])))
    return {
        "detected": bool(active_account or known_accounts),
        "provider": clean_provider,
        "active_account": active_account,
        "known_accounts": known_accounts,
    }


def _parse_mcp_response(content_type: str, text: str) -> dict[str, Any]:
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


def _extract_mcp_payload(result: dict[str, Any]) -> Any:
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    out: list[Any] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            txt = item.get("text", "")
            try:
                out.append(json.loads(txt))
            except Exception:
                out.append(txt)
        else:
            out.append(item)
    return out[0] if len(out) == 1 else out


def _replace_placeholders(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _replace_placeholders(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholders(x, mapping) for x in obj]
    if isinstance(obj, str) and obj in mapping:
        return mapping[obj]
    return obj


class VcatAgentRunner:
    """Reusable outbound runner usable from CLI or automated integration tests."""

    def __init__(
        self,
        *,
        server_url: str,
        mcp_url: str,
        mcp_token: str,
        state_dir: Path,
        mode: str = "client_local",
        node_secret: str = "",
        node_id: str = "",
        node_name: str = "",
        explicit_node_name: bool = False,
        min_interval_sec: float = 3.2,
        poll_wait: float | None = None,
        provider: str = "vcat",
        data_home: Path | str | None = None,
        server_http_client: httpx.Client | None = None,
        mcp_http_client: httpx.Client | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.mcp_url = mcp_url
        self.mcp_token = mcp_token
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.state_dir / "media_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.receipts_path = self.state_dir / "receipts.json"
        self.creds_path = self.state_dir / "node_credentials.json"
        self.mode = mode
        self.node_secret = node_secret
        self.node_id = node_id
        self.min_interval_sec = min_interval_sec
        self.poll_wait = (0.0 if server_http_client is not None else 25.0) if poll_wait is None else max(0.0, float(poll_wait))
        self.provider = (provider or "vcat").strip().lower() or "vcat"
        self.data_home = Path(data_home).expanduser() if data_home else None
        self._last_mcp_ts = 0.0
        self._mcp_session_id: str | None = None
        self._req_id = 0
        self._server_client = server_http_client
        self._mcp_client = mcp_http_client
        self.last_mcp_ready = False
        self.last_promoter_identity: dict[str, Any] = {"detected": False, "provider": self.provider, "active_account": None, "known_accounts": []}
        self.promoter_activated: bool = False
        self.promoter_account_id: str | None = None
        self.promoter_account_label: str | None = None
        self.activation_url: str | None = None
        self._last_logged_promoter_sig: tuple[Any, ...] | None = None

        saved: dict[str, Any] = {}
        if self.creds_path.is_file():
            try:
                saved = json.loads(self.creds_path.read_text(encoding="utf-8"))
            except Exception:
                saved = {}
        if not self.node_secret and saved:
            self.node_secret = saved.get("node_secret", "")
            self.node_id = saved.get("node_id", "")

        saved_name = str(saved.get("node_name") or "").strip()
        saved_configured = str(saved.get("configured_node_name") or "").strip()
        configured = str(node_name or "").strip()
        self.configured_node_name = configured or saved_configured
        if configured and (explicit_node_name or configured != saved_configured):
            self.node_name = configured
            self._pending_name_sync = True
        else:
            self.node_name = saved_name or configured
            self._pending_name_sync = False

    def _save_credentials(self) -> None:
        if not self.node_id and not self.node_secret:
            return
        self.creds_path.write_text(
            json.dumps(
                {
                    "node_id": self.node_id,
                    "node_secret": self.node_secret,
                    "node_name": self.node_name,
                    "configured_node_name": self.configured_node_name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.chmod(self.creds_path, 0o600)

    def _srv_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.node_secret}"}

    def _post_server(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 20.0,
    ) -> httpx.Response:
        if self._server_client is not None:
            return self._server_client.post(path, headers=self._srv_headers(), json=json_body, params=params)
        with httpx.Client(base_url=self.server_url, timeout=timeout, follow_redirects=False, trust_env=False) as c:
            return c.post(path, headers=self._srv_headers(), json=json_body, params=params)

    def _get_server(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if self._server_client is not None:
            return self._server_client.get(path, headers=self._srv_headers(), params=params)
        with httpx.Client(base_url=self.server_url, timeout=60, follow_redirects=False, trust_env=False) as c:
            return c.get(path, headers=self._srv_headers(), params=params)

    def probe_promoter_identity(self) -> dict[str, Any]:
        identity = detect_local_promoter_state(provider=self.provider, data_home=self.data_home)
        self.last_promoter_identity = identity
        return identity

    def _sync_promoter_response(self, data: dict[str, Any], logger: logging.Logger | None = None) -> None:
        self.promoter_activated = bool(data.get("promoter_activated", False))
        self.promoter_account_id = data.get("promoter_account_id")
        self.promoter_account_label = data.get("promoter_account_label")
        self.activation_url = data.get("activation_url")
        active = self.last_promoter_identity.get("active_account") if isinstance(self.last_promoter_identity, dict) else None
        ext_uid = active.get("external_user_id") if isinstance(active, dict) else None
        label = self.promoter_account_label or (active.get("display_label") if isinstance(active, dict) else None)
        sig = (ext_uid, label, self.promoter_activated)
        if ext_uid and sig != self._last_logged_promoter_sig:
            self._last_logged_promoter_sig = sig
            msg = (
                f"当前渠道推广器账号：{label}（ID: {ext_uid}）· "
                + ("已激活" if self.promoter_activated else f"尚未在获客空间激活，请前往 {self.server_url}{self.activation_url or '/#ops-accounts'} 点击激活并导入")
            )
            if logger:
                logger.info(msg)
            else:
                print(msg, flush=True)

    def register(self, pair_code: str, node_name: str) -> dict[str, Any]:
        identity = self.probe_promoter_identity()
        reg_body = {
            "pair_code": pair_code,
            "node_name": node_name,
            "vcat_version": "3.1.0",
            "promoter_identity": identity,
        }
        if self._server_client is not None:
            resp = self._server_client.post("/internal/agents/register", json=reg_body)
        else:
            with httpx.Client(base_url=self.server_url, timeout=20, follow_redirects=False, trust_env=False) as c:
                resp = c.post("/internal/agents/register", json=reg_body)
        resp.raise_for_status()
        data = resp.json()
        self.node_id = data["node_id"]
        self.node_secret = data["node_secret"]
        self.node_name = str(data.get("node_name") or node_name).strip()
        self.configured_node_name = str(node_name or self.node_name).strip()
        self._pending_name_sync = False
        self._sync_promoter_response(data)
        self._save_credentials()
        return data

    def _mcp_post(self, payload: dict[str, Any], notification: bool = False, timeout: float = 30.0) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.mcp_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id
        if self._mcp_client is not None:
            resp = self._mcp_client.post(self.mcp_url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as c:
                resp = c.post(self.mcp_url, headers=headers, json=payload)
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self._mcp_session_id = sid
        resp.raise_for_status()
        if notification:
            return {}
        return _parse_mcp_response(resp.headers.get("Content-Type", ""), resp.text)

    def probe_local_mcp(self) -> tuple[bool, dict[str, Any]]:
        try:
            self._req_id += 1
            init = self._mcp_post(
                {
                    "jsonrpc": "2.0",
                    "id": self._req_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "vcat-agent", "version": "0.1.0"},
                    },
                }
            )
            self._mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
            self._req_id += 1
            tl = self._mcp_post({"jsonrpc": "2.0", "id": self._req_id, "method": "tools/list", "params": {}})
            tools = (tl.get("result") or {}).get("tools") or []
            self.last_mcp_ready = True
            info = (init.get("result") or {}).get("serverInfo") or {}
            return True, {"tool_count": len(tools), "server_version": info.get("version", "3.1.0")}
        except Exception:
            self.last_mcp_ready = False
            return False, {}

    def heartbeat_once(self) -> dict[str, Any]:
        ready, caps = self.probe_local_mcp()
        identity = self.probe_promoter_identity()
        caps = {
            **caps,
            "channel": "long_poll" if self.poll_wait > 0 else "short_poll",
            "os_platform": platform.system().lower(),
            "pid": os.getpid(),
            "agent_home": str(Path(__file__).resolve().parent),
        }
        payload: dict[str, Any] = {
            "mcp_ready": ready,
            "vcat_version": caps.get("server_version", "3.1.0"),
            "capabilities": caps,
            "promoter_identity": identity,
        }
        if self._pending_name_sync and self.node_name:
            payload["node_name"] = self.node_name
        resp = self._post_server("/internal/agents/heartbeat", payload)
        resp.raise_for_status()
        data = resp.json()
        srv_name = str(data.get("node_name") or "").strip()
        changed = False
        if self._pending_name_sync:
            self._pending_name_sync = False
            changed = True
        if srv_name and srv_name != self.node_name:
            self.node_name = srv_name
            changed = True
        self._sync_promoter_response(data, logger=logging.getLogger("vcat_agent") if logging.getLogger("vcat_agent").handlers else None)
        if changed and self.node_secret:
            self._save_credentials()
        return data

    def _load_receipts(self) -> dict[str, Any]:
        if not self.receipts_path.is_file():
            return {}
        try:
            return json.loads(self.receipts_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_receipt(self, action_id: str, result: dict[str, Any]) -> None:
        receipts = self._load_receipts()
        receipts[action_id] = {**result, "saved_at": datetime.now(timezone.utc).isoformat()}
        self.receipts_path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8")

    def _stage_media(self, action_id: str, media_refs: list[dict[str, Any]]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for ref in media_refs:
            asset_id = str(ref["asset_id"])
            expected = str(ref["sha256"])
            ext = Path(str(ref.get("filename") or "")).suffix.lower() or ".bin"
            target = (self.cache_dir / f"{asset_id}_{expected[:12]}{ext}").resolve()
            if target.is_file():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest == expected:
                    mapping[f"$ASSET:{asset_id}"] = str(target)
                    continue
            resp = self._get_server(f"/internal/agents/assets/{asset_id}", params={"action_id": action_id})
            resp.raise_for_status()
            digest = hashlib.sha256(resp.content).hexdigest()
            if digest != expected:
                raise ValueError(f"媒体文件校验失败(SHA-256不匹配): {asset_id}")
            target.write_bytes(resp.content)
            mapping[f"$ASSET:{asset_id}"] = str(target)
        return mapping

    def poll_once(self) -> bool:
        if self.poll_wait > 0:
            resp = self._post_server(
                "/internal/agents/poll",
                {},
                params={"wait": self.poll_wait},
                timeout=max(30.0, self.poll_wait + 15.0),
            )
        else:
            resp = self._post_server("/internal/agents/poll", {})
        resp.raise_for_status()
        cmd = resp.json().get("command")
        if not cmd:
            return False

        action_id = str(cmd["action_id"])
        receipts = self._load_receipts()
        if action_id in receipts:
            self._post_server(f"/internal/agents/commands/{action_id}/result", receipts[action_id]).raise_for_status()
            return True

        tool_name = str(cmd["tool_name"])
        if tool_name not in ALLOWED_AGENT_TOOLS:
            res = {"ok": False, "status": "FAILED", "payload": None, "error_message": f"拒绝执行未授权工具: {tool_name}"}
            self._save_receipt(action_id, res)
            self._post_server(f"/internal/agents/commands/{action_id}/result", res).raise_for_status()
            return True

        try:
            media_map = self._stage_media(action_id, cmd.get("media_refs") or [])
            args = _replace_placeholders(cmd.get("arguments") or {}, media_map)
            elapsed = time.monotonic() - self._last_mcp_ts
            if self._last_mcp_ts > 0 and elapsed < self.min_interval_sec:
                time.sleep(self.min_interval_sec - elapsed)
            self._last_mcp_ts = time.monotonic()
            self.probe_local_mcp()
            self._req_id += 1
            call_timeout = 120.0 if tool_name in ("vcat_create_publish_task", "vcat_create_draft", "vcat_publish_draft") else 30.0
            mcp_res = self._mcp_post(
                {
                    "jsonrpc": "2.0",
                    "id": self._req_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": args},
                },
                timeout=call_timeout,
            )
            if "error" in mcp_res:
                res = {"ok": False, "status": "FAILED", "payload": mcp_res["error"], "error_message": str(mcp_res["error"])}
            else:
                r = mcp_res.get("result") or {}
                payload = _extract_mcp_payload(r)
                is_err = bool(r.get("isError"))
                res = {
                    "ok": not is_err,
                    "status": "FAILED" if is_err else "SUCCEEDED",
                    "payload": payload,
                    "error_message": str(payload) if is_err else None,
                }
        except httpx.TimeoutException as exc:
            res = {"ok": False, "status": "UNKNOWN", "payload": None, "error_message": f"本机渠道推广器服务超时: {exc}"}
        except Exception as exc:
            res = {"ok": False, "status": "FAILED", "payload": None, "error_message": str(exc)}

        self._save_receipt(action_id, res)
        self._post_server(f"/internal/agents/commands/{action_id}/result", res).raise_for_status()
        return True


def _build_local_status_html(runner: VcatAgentRunner) -> str:
    ident = runner.last_promoter_identity if isinstance(runner.last_promoter_identity, dict) else {}
    active = ident.get("active_account") if isinstance(ident.get("active_account"), dict) else None
    known = ident.get("known_accounts") if isinstance(ident.get("known_accounts"), list) else []
    display_label = (
        runner.promoter_account_label
        or (active.get("display_label") if active else None)
        or "未检测到登录中的渠道推广器账号"
    )
    ext_uid = (active.get("external_user_id") if active else None) or ""
    act_href = f"{runner.server_url}{runner.activation_url}" if runner.activation_url else f"{runner.server_url}/#ops-accounts"
    banner = (
        '<div class="banner ok">当前渠道推广器账号已在获客空间激活，可正常同步与执行任务。</div>'
        if runner.promoter_activated
        else (
            f'<div class="banner warn"><strong>检测到未激活的渠道推广器账号：</strong>'
            f'{html.escape(display_label)}'
            f' <a class="btn" href="{html.escape(act_href)}" target="_blank" rel="noreferrer">前往获客空间激活并导入 →</a></div>'
            if ext_uid
            else '<div class="banner">未在本机探测到渠道推广器账号元数据，可在获客空间页面手动添加节点并导入。</div>'
        )
    )
    known_rows = "".join(
        f"<li><strong>{html.escape(str(item.get('display_label') or item.get('nickname') or '未命名'))}</strong>"
        f" <small>(ID: {html.escape(str(item.get('external_user_id') or '-'))})</small>"
        f"{' <span class=\"tag\">当前登录</span>' if item.get('is_active') else ''}</li>"
        for item in known
        if isinstance(item, dict)
    ) or "<li>暂未发现本机历史渠道推广器账号</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>本机渠道推广器代理状态</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:760px;margin:32px auto;padding:0 16px;color:#1e293b;background:#f8fafc}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:16px}}
.banner{{padding:12px 14px;border-radius:10px;border:1px solid #cbd5e1;background:#f1f5f9;margin-bottom:16px;line-height:1.6}}
.banner.warn{{border-color:#f59e0b;background:#fffbeb;color:#92400e}}
.banner.ok{{border-color:#34d399;background:#ecfdf5;color:#065f46}}
.btn{{display:inline-block;margin-left:10px;padding:6px 12px;border-radius:8px;background:#2563eb;color:#fff;text-decoration:none;font-weight:600}}
.tag{{display:inline-block;padding:2px 6px;border-radius:6px;background:#dbeafe;color:#1d4ed8;font-size:12px}}
small{{color:#64748b}}
</style></head><body>
<h2>本机渠道推广器代理状态</h2>
{banner}
<div class="card">
  <p><strong>代理节点：</strong>{html.escape(runner.node_name or '未命名')} <small>({html.escape(runner.node_id or '-')})</small></p>
  <p><strong>本地服务状态：</strong>{'就绪' if runner.last_mcp_ready else '未连接或未启用'}</p>
  <p><strong>当前渠道推广器账号：</strong>{html.escape(display_label)}{f' <small>(ID: {html.escape(ext_uid)})</small>' if ext_uid else ''}</p>
  <p><strong>获客空间地址：</strong><a href="{html.escape(runner.server_url)}" target="_blank" rel="noreferrer">{html.escape(runner.server_url)}</a></p>
</div>
<div class="card">
  <h3>本机已发现的渠道推广器账号（{len(known)}）</h3>
  <ul>{known_rows}</ul>
</div>
</body></html>"""


def _start_local_health_server(runner: VcatAgentRunner, allowed_origin: str, port: int = 18899) -> ThreadingHTTPServer | None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/":
                body = _build_local_status_html(runner).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            ident = runner.last_promoter_identity if isinstance(runner.last_promoter_identity, dict) else {}
            active = ident.get("active_account") if isinstance(ident.get("active_account"), dict) else None
            known = ident.get("known_accounts") if isinstance(ident.get("known_accounts"), list) else []
            body = json.dumps(
                {
                    "agent_online": True,
                    "mode": runner.mode,
                    "node_id": runner.node_id,
                    "node_name": runner.node_name,
                    "mcp_ready": runner.last_mcp_ready,
                    "promoter_provider": ident.get("provider", runner.provider),
                    "promoter_display_label": runner.promoter_account_label or (active.get("display_label") if active else None),
                    "promoter_nickname": active.get("nickname") if active else None,
                    "promoter_masked_mobile": active.get("masked_mobile") if active else None,
                    "promoter_external_user_id": active.get("external_user_id") if active else None,
                    "promoter_activated": runner.promoter_activated,
                    "promoter_account_id": runner.promoter_account_id,
                    "activation_url": runner.activation_url,
                    "known_promoter_count": len(known),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            if allowed_origin:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv
    except OSError:
        return None


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if key:
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            os.environ.setdefault(key, val)


def persist_agent_env(state_dir: Path, server_url: str, *, sync_user_env: bool = True) -> dict[str, str]:
    """Persist self binary/home paths & server URL into state env file and user OS env."""
    state_dir = Path(state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    agent_home = str(Path(__file__).resolve().parent)
    is_win = platform.system().lower().startswith("win")
    default_bin = state_dir / ("start-agent.cmd" if is_win else "start-agent.sh")
    agent_bin = str(default_bin.resolve() if default_bin.exists() else Path(__file__).resolve())
    python_bin = str(Path(sys.executable).resolve())
    clean_server = server_url.strip().rstrip("/")

    updates = {
        "VCAT_STATE_DIR": str(state_dir),
        "ACQ_VCAT_AGENT_HOME": agent_home,
        "ACQ_VCAT_AGENT_BIN": agent_bin,
        "ACQ_VCAT_AGENT_PYTHON": python_bin,
    }
    if clean_server:
        updates["VCAT_SERVER_URL"] = clean_server

    env_path = state_dir / "vcat_agent.env"
    existing_lines: list[str] = []
    seen: set[str] = set()
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    existing_lines.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            existing_lines.append(raw_line)
    for k, v in updates.items():
        if k not in seen:
            existing_lines.append(f"{k}={v}")
    env_path.write_text("\n".join(existing_lines).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    for k, v in updates.items():
        os.environ[k] = v

    if sync_user_env and os.environ.get("ACQ_SKIP_USER_ENV") != "1":
        if is_win:
            for k in ("ACQ_VCAT_AGENT_HOME", "ACQ_VCAT_AGENT_BIN", "ACQ_VCAT_AGENT_PYTHON", "VCAT_SERVER_URL"):
                if k in updates:
                    try:
                        subprocess.run(["setx", k, updates[k]], check=False, capture_output=True)
                    except Exception:
                        pass
        else:
            sys_name = platform.system().lower()
            rc_path = Path.home() / (".zshrc" if sys_name == "darwin" else ".bashrc")
            try:
                text = rc_path.read_text(encoding="utf-8") if rc_path.is_file() else ""
                block = "\n".join(
                    [
                        "# >>> acquisition-vcat-agent env >>>",
                        f'export ACQ_VCAT_AGENT_HOME="{agent_home}"',
                        f'export ACQ_VCAT_AGENT_BIN="{agent_bin}"',
                        f'export ACQ_VCAT_AGENT_PYTHON="{python_bin}"',
                        f'export VCAT_SERVER_URL="{updates.get("VCAT_SERVER_URL", "")}"',
                        "# <<< acquisition-vcat-agent env <<<",
                    ]
                )
                pattern = re.compile(
                    r"# >>> acquisition-vcat-agent env >>>.*?# <<< acquisition-vcat-agent env <<<\n?",
                    re.S,
                )
                cleaned = pattern.sub("", text).rstrip()
                rc_path.write_text((cleaned + "\n\n" + block + "\n") if cleaned else (block + "\n"), encoding="utf-8")
            except Exception:
                pass
            if sys_name == "darwin":
                for k in ("ACQ_VCAT_AGENT_HOME", "ACQ_VCAT_AGENT_BIN", "ACQ_VCAT_AGENT_PYTHON", "VCAT_SERVER_URL"):
                    if k in updates:
                        try:
                            subprocess.run(["launchctl", "setenv", k, updates[k]], check=False, capture_output=True)
                        except Exception:
                            pass
    return updates


def acquire_instance_lock(state_dir: Path):
    """Ensure only one active vcat_agent daemon runs per state directory."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "agent.lock"
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt  # type: ignore

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise RuntimeError(f"检测到同一目录下已有运行中的 vcat_agent 实例 ({lock_path})") from exc
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def cleanup_media_cache(cache_dir: Path, ttl_days: float = 7.0) -> int:
    """Delete staged media files older than ttl_days to avoid disk exhaustion."""
    cache_dir = Path(cache_dir)
    if ttl_days <= 0 or not cache_dir.is_dir():
        return 0
    cutoff = time.time() - ttl_days * 86400.0
    removed = 0
    for item in cache_dir.iterdir():
        if not item.is_file():
            continue
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _configure_logger(state_dir: Path) -> logging.Logger:
    logs_dir = Path(state_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("vcat_agent")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(logs_dir / "vcat_agent.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    return logger


def _start_watchdog(last_tick_ref: list[float], timeout_sec: float, logger: logging.Logger) -> None:
    def _watch() -> None:
        while True:
            time.sleep(15.0)
            if time.monotonic() - last_tick_ref[0] > timeout_sec:
                logger.error("主循环超过 %.0fs 未响应，触发自愈退出交由系统守护重启", timeout_sec)
                os._exit(1)

    t = threading.Thread(target=_watch, daemon=True, name="agent-watchdog")
    t.start()


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", default="")
    known, _ = pre.parse_known_args()
    if known.env_file:
        _load_env_file(Path(known.env_file).expanduser())
    else:
        _load_env_file(Path(__file__).resolve().parent / "vcat_agent.env")
        _load_env_file(Path(__file__).resolve().parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Channel-promoter outbound execution agent", parents=[pre])
    parser.add_argument(
        "--mode",
        choices=["server_managed", "client_local"],
        default=os.environ.get("VCAT_AGENT_MODE", "client_local"),
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("VCAT_SERVER_URL", ""),
        help="acquisition-space server base URL",
    )
    parser.add_argument("--mcp-url", default=os.environ.get("VCAT_MCP_URL", "http://127.0.0.1:3199/mcp"))
    parser.add_argument("--mcp-token", default=os.environ.get("VCAT_MCP_TOKEN", ""))
    parser.add_argument("--pair-token", default="")
    parser.add_argument("--register-only", action="store_true", help="Register credentials, persist env, and exit")
    parser.add_argument("--node-name", default=os.environ.get("VCAT_NODE_NAME", ""))
    parser.add_argument("--provider", default=os.environ.get("PROMOTER_PROVIDER", "vcat"))
    parser.add_argument("--data-home", "--vcat-home", dest="data_home", default=os.environ.get("PROMOTER_DATA_HOME", os.environ.get("VCAT_HOME", "")))
    parser.add_argument("--poll-wait", type=float, default=float(os.environ.get("VCAT_POLL_WAIT", "25")))
    parser.add_argument(
        "--state-dir",
        default=str(Path(os.environ.get("VCAT_STATE_DIR", str(Path.home() / ".acquisition-vcat-agent"))).expanduser()),
    )
    args = parser.parse_args()

    if not args.server:
        print("请通过 --server 或环境变量 VCAT_SERVER_URL 提供获客空间服务地址", file=sys.stderr)
        return 2

    state_dir = Path(args.state_dir).expanduser()
    persist_agent_env(state_dir, args.server, sync_user_env=True)

    if not args.mcp_token and not (args.register_only and args.pair_token):
        print("请通过 --mcp-token 或环境变量 VCAT_MCP_TOKEN 提供本机渠道推广器服务令牌", file=sys.stderr)
        return 2

    cli_node_name_provided = any(arg == "--node-name" or arg.startswith("--node-name=") for arg in sys.argv[1:])
    runner = VcatAgentRunner(
        server_url=args.server,
        mcp_url=args.mcp_url,
        mcp_token=args.mcp_token,
        state_dir=state_dir,
        mode=args.mode,
        node_name=(args.node_name or "").strip(),
        explicit_node_name=cli_node_name_provided,
        poll_wait=args.poll_wait,
        provider=args.provider,
        data_home=args.data_home or None,
    )
    if args.pair_token:
        runner.register(args.pair_token, (args.node_name or "").strip() or "niekun")
    if args.register_only:
        return 0
    if not runner.node_secret:
        print("未检测到节点凭据，请传入 --pair-token 完成首次配对", file=sys.stderr)
        return 2

    try:
        lock_fh = acquire_instance_lock(state_dir)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    logger = _configure_logger(state_dir)
    ttl_days = float(os.environ.get("VCAT_MEDIA_CACHE_TTL_DAYS", "7"))
    cleanup_media_cache(runner.cache_dir, ttl_days=ttl_days)

    parsed_srv = urlparse(args.server)
    allowed_origin = f"{parsed_srv.scheme}://{parsed_srv.netloc}" if parsed_srv.scheme and parsed_srv.netloc else ""
    health_srv = _start_local_health_server(runner, allowed_origin)
    logger.info(
        "vcat_agent 已启动 | node_name=%s | node_id=%s | mode=%s | server=%s",
        runner.node_name or "未命名",
        runner.node_id,
        runner.mode,
        runner.server_url,
    )
    last_hb = 0.0
    last_cache_gc = time.monotonic()
    last_tick = [time.monotonic()]
    _start_watchdog(last_tick, max(120.0, float(args.poll_wait) * 3.0), logger)
    fail_streak = 0
    try:
        while True:
            last_tick[0] = time.monotonic()
            if time.monotonic() - last_cache_gc >= 21600:
                cleanup_media_cache(runner.cache_dir, ttl_days=ttl_days)
                last_cache_gc = time.monotonic()
            if time.monotonic() - last_hb >= 15:
                try:
                    runner.heartbeat_once()
                    last_hb = time.monotonic()
                except Exception as exc:
                    logger.warning("心跳上报异常: %s", exc)
            try:
                worked = runner.poll_once()
                fail_streak = 0
            except Exception as exc:
                logger.warning("指令轮询异常: %s", exc)
                worked = False
                fail_streak += 1
            if fail_streak > 0:
                time.sleep(min(10.0, float(2 ** min(fail_streak - 1, 3))))
            elif runner.poll_wait > 0:
                time.sleep(0.05)
            else:
                time.sleep(0.5 if worked else 2.0)
    except KeyboardInterrupt:
        pass
    finally:
        if health_srv:
            health_srv.shutdown()
        try:
            lock_fh.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
