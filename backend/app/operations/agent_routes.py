"""Internal agent relay endpoints for both server_relay and client_local agents."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from fastapi import Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import AgentCommand, AgentPairCode, ExecutorNode, Record, now, uid
from .transport import sanitize_summary


class AgentRegisterInput(BaseModel):
    pair_code: str = Field(min_length=1, max_length=200)
    node_name: str = Field(default="小V猫执行节点", max_length=80)
    vcat_version: str = Field(default="3.1.0", max_length=40)


class AgentHeartbeatInput(BaseModel):
    mcp_ready: bool = True
    vcat_version: str = Field(default="3.1.0", max_length=40)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class AgentResultInput(BaseModel):
    ok: bool
    status: str = Field(default="SUCCEEDED")
    payload: Any = None
    error_message: str | None = None


def register_agent_routes(app):
    def agent_auth(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "缺少执行节点凭据")
        raw_secret = authorization[7:].strip()
        secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()
        with app.state.Session() as db:
            node = db.scalar(select(ExecutorNode).where(ExecutorNode.secret_hash == secret_hash))
            if not node or node.status == "revoked" or not node.secret_hash:
                raise HTTPException(401, "执行节点凭据无效或已吊销")
            return node

    @app.post("/internal/agents/register")
    def register_agent(data: AgentRegisterInput):
        code_hash = hashlib.sha256(data.pair_code.strip().encode()).hexdigest()
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            pair = db.scalar(select(AgentPairCode).where(AgentPairCode.code_hash == code_hash))
            if not pair or pair.used_at is not None or pair.expires_at < now():
                raise HTTPException(401, "配对码无效、已使用或已过期")
            raw_secret = "vcat_node_" + secrets.token_urlsafe(32)
            node = ExecutorNode(
                id=uid(),
                name=data.node_name or pair.label,
                node_scope=pair.node_scope,
                owner_user_id=pair.owner_user_id,
                transport_mode="agent_relay",
                secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(),
                status="online",
                vcat_version=data.vcat_version,
                last_heartbeat_at=now(),
            )
            pair.used_at = now()
            db.add(node)
            db.commit()
            return {
                "node_id": node.id,
                "node_scope": node.node_scope,
                "owner_user_id": node.owner_user_id,
                "node_secret": raw_secret,
            }

    @app.post("/internal/agents/heartbeat")
    def heartbeat(data: AgentHeartbeatInput, node: ExecutorNode = Depends(agent_auth)):
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.get(ExecutorNode, node.id)
            row.status = "online" if data.mcp_ready else "mcp_error"
            row.vcat_version = data.vcat_version
            row.last_heartbeat_at = now()
            if data.capabilities:
                row.capabilities_json = data.capabilities
            row.updated_at = now()
            db.commit()
            return {"ok": True, "node_id": row.id, "status": row.status}

    @app.post("/internal/agents/poll")
    def poll_command(node: ExecutorNode = Depends(agent_auth)):
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.scalar(
                select(AgentCommand)
                .where(AgentCommand.node_id == node.id, AgentCommand.status == "queued")
                .order_by(AgentCommand.created_at.asc())
            )
            if not row:
                db.commit()
                return {"command": None}
            row.status = "leased"
            row.lease_until = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
            row.updated_at = now()
            cmd_payload = {
                "action_id": row.action_id,
                "tool_name": row.tool_name,
                "arguments": row.arguments_json,
                "media_refs": row.media_refs_json,
            }
            db.commit()
            return {"command": cmd_payload}

    @app.get("/internal/agents/assets/{asset_id}")
    def download_asset(asset_id: str, action_id: str, node: ExecutorNode = Depends(agent_auth)):
        with app.state.Session() as db:
            cmd = db.scalar(
                select(AgentCommand).where(
                    AgentCommand.action_id == action_id,
                    AgentCommand.node_id == node.id,
                    AgentCommand.status.in_(("queued", "leased")),
                )
            )
            if not cmd:
                raise HTTPException(403, "节点无权下载此资产")
            allowed_ids = {m.get("asset_id") for m in (cmd.media_refs_json or []) if isinstance(m, dict)}
            if asset_id not in allowed_ids:
                raise HTTPException(403, "资产未在指令白名单内")
            asset = db.get(Record, asset_id)
            if not asset or asset.kind != "assets":
                raise HTTPException(404, "资产不存在")
            filename = asset.payload.get("filename") or f"{asset_id}.bin"
        path = app.state.data_dir / "assets" / asset_id
        if not path.is_file():
            raise HTTPException(404, "资产文件缺失")
        return FileResponse(path, filename=filename)

    @app.post("/internal/agents/commands/{action_id}/result")
    def submit_result(action_id: str, data: AgentResultInput, node: ExecutorNode = Depends(agent_auth)):
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.scalar(select(AgentCommand).where(AgentCommand.action_id == action_id, AgentCommand.node_id == node.id))
            if not row:
                raise HTTPException(404, "指令不存在")
            if row.status in ("succeeded", "failed", "unknown"):
                db.commit()
                return {"ok": True, "idempotent": True, "status": row.status}
            normalized = "succeeded" if data.ok else ("unknown" if data.status == "UNKNOWN" else "failed")
            row.status = normalized
            row.result_json = {
                "ok": data.ok,
                "status": data.status,
                "payload": data.payload,
                "error_message": sanitize_summary(data.error_message) if data.error_message else None,
            }
            row.finished_at = now()
            row.updated_at = now()
            db.commit()
            return {"ok": True, "status": row.status}
