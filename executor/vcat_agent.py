#!/usr/bin/env python3
"""Unified Xiaovmao execution agent for both server_managed and client_local modes.

Runs on the machine where Xiaovmao Desktop is installed, polls outbound commands
from acquisition-space, downloads and SHA-256 verifies media assets, invokes
local http://127.0.0.1:3199/mcp with rate limiting, and posts results back.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
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
        min_interval_sec: float = 3.2,
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
        self._last_mcp_ts = 0.0
        self._mcp_session_id: str | None = None
        self._req_id = 0
        self._server_client = server_http_client
        self._mcp_client = mcp_http_client
        self.last_mcp_ready = False

        if not self.node_secret and self.creds_path.is_file():
            saved = json.loads(self.creds_path.read_text(encoding="utf-8"))
            self.node_secret = saved.get("node_secret", "")
            self.node_id = saved.get("node_id", "")

    def _srv_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.node_secret}"}

    def _post_server(self, path: str, json_body: dict[str, Any]) -> httpx.Response:
        if self._server_client is not None:
            return self._server_client.post(path, headers=self._srv_headers(), json=json_body)
        with httpx.Client(base_url=self.server_url, timeout=20, follow_redirects=False, trust_env=False) as c:
            return c.post(path, headers=self._srv_headers(), json=json_body)

    def _get_server(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if self._server_client is not None:
            return self._server_client.get(path, headers=self._srv_headers(), params=params)
        with httpx.Client(base_url=self.server_url, timeout=60, follow_redirects=False, trust_env=False) as c:
            return c.get(path, headers=self._srv_headers(), params=params)

    def register(self, pair_code: str, node_name: str) -> dict[str, Any]:
        if self._server_client is not None:
            resp = self._server_client.post(
                "/internal/agents/register",
                json={"pair_code": pair_code, "node_name": node_name, "vcat_version": "3.1.0"},
            )
        else:
            with httpx.Client(base_url=self.server_url, timeout=20, follow_redirects=False, trust_env=False) as c:
                resp = c.post(
                    "/internal/agents/register",
                    json={"pair_code": pair_code, "node_name": node_name, "vcat_version": "3.1.0"},
                )
        resp.raise_for_status()
        data = resp.json()
        self.node_id = data["node_id"]
        self.node_secret = data["node_secret"]
        self.creds_path.write_text(json.dumps({"node_id": self.node_id, "node_secret": self.node_secret}), encoding="utf-8")
        os.chmod(self.creds_path, 0o600)
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
        resp = self._post_server(
            "/internal/agents/heartbeat",
            {
                "mcp_ready": ready,
                "vcat_version": caps.get("server_version", "3.1.0"),
                "capabilities": caps,
            },
        )
        resp.raise_for_status()
        return resp.json()

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
            res = {"ok": False, "status": "UNKNOWN", "payload": None, "error_message": f"本机MCP超时: {exc}"}
        except Exception as exc:
            res = {"ok": False, "status": "FAILED", "payload": None, "error_message": str(exc)}

        self._save_receipt(action_id, res)
        self._post_server(f"/internal/agents/commands/{action_id}/result", res).raise_for_status()
        return True


def _start_local_health_server(runner: VcatAgentRunner, allowed_origin: str, port: int = 18899) -> ThreadingHTTPServer | None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "agent_online": True,
                    "mode": runner.mode,
                    "node_id": runner.node_id,
                    "mcp_ready": runner.last_mcp_ready,
                }
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


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", default="")
    known, _ = pre.parse_known_args()
    if known.env_file:
        _load_env_file(Path(known.env_file).expanduser())
    else:
        _load_env_file(Path(__file__).resolve().parent / "vcat_agent.env")
        _load_env_file(Path(__file__).resolve().parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Xiaovmao outbound execution agent", parents=[pre])
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
    parser.add_argument("--node-name", default="")
    parser.add_argument(
        "--state-dir",
        default=str(Path(os.environ.get("VCAT_STATE_DIR", str(Path.home() / ".acquisition-vcat-agent"))).expanduser()),
    )
    args = parser.parse_args()

    if not args.server:
        print("请通过 --server 或环境变量 VCAT_SERVER_URL 提供获客空间服务地址", file=sys.stderr)
        return 2
    if not args.mcp_token:
        print("请通过 --mcp-token 或环境变量 VCAT_MCP_TOKEN 提供本机小V猫 MCP 令牌", file=sys.stderr)
        return 2

    runner = VcatAgentRunner(
        server_url=args.server,
        mcp_url=args.mcp_url,
        mcp_token=args.mcp_token,
        state_dir=Path(args.state_dir),
        mode=args.mode,
    )
    if args.pair_token:
        runner.register(args.pair_token, args.node_name or f"{args.mode}-node")
    if not runner.node_secret:
        print("未检测到节点凭据，请传入 --pair-token 完成首次配对", file=sys.stderr)
        return 2

    parsed_srv = urlparse(args.server)
    allowed_origin = f"{parsed_srv.scheme}://{parsed_srv.netloc}" if parsed_srv.scheme and parsed_srv.netloc else ""
    health_srv = _start_local_health_server(runner, allowed_origin) if args.mode == "client_local" else None
    last_hb = 0.0
    try:
        while True:
            if time.monotonic() - last_hb >= 15:
                try:
                    runner.heartbeat_once()
                    last_hb = time.monotonic()
                except Exception:
                    pass
            try:
                worked = runner.poll_once()
            except Exception:
                worked = False
            time.sleep(0.5 if worked else 2.0)
    except KeyboardInterrupt:
        pass
    finally:
        if health_srv:
            health_srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
