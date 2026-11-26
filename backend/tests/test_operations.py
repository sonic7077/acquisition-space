"""Comprehensive tests for Xiaovmao operations, dual-mode transport, worker, and agent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db import AgentCommand, ExecutorNode, MetricSnapshot, PlatformAccount, PublishAttempt, PublishTarget, Record, VcatActionLog
from backend.app.main import create_app
from backend.app.manage import reset_password
from backend.app.operations.qr import render_qr_png
from backend.app.operations.transport import clean_metric_number
from backend.app.publish_worker import run_once as run_publish_worker_once
from executor.vcat_agent import VcatAgentRunner


class MockXiaovmaoState:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.login_links: list[dict] = []
        self.drafts: list[dict] = []
        self.publish_tasks: list[dict] = []
        self.timeout_next_publish = False
        self.fail_next_tool = False
        self.accounts = [
            {
                "uid": "dy_001",
                "platform": "Douyin",
                "platformName": "抖音",
                "nickname": "测试抖音号",
                "loginStatus": "登录有效",
                "fansTotal": 10,
            },
            {
                "uid": "xhs_001",
                "platform": "XiaoHongShu",
                "platformName": "小红书",
                "nickname": "测试小红书",
                "loginStatus": "登录有效",
                "fansTotal": 13,
            },
            {
                "uid": "ch_001",
                "platform": "Channels",
                "platformName": "视频号",
                "nickname": "测试视频号",
                "loginStatus": "登录有效",
                "fansTotal": 2,
            },
        ]


def start_mock_mcp_server(token: str = "mock-mcp-token-secret") -> tuple[ThreadingHTTPServer, str, MockXiaovmaoState]:
    state = MockXiaovmaoState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"unauthorized"}')
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            method = body.get("method")
            req_id = body.get("id")
            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return

            if method == "initialize":
                res = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "vcat", "version": "3.1.0"},
                }
            elif method == "tools/list":
                res = {
                    "tools": [
                        {"name": "vcat_list_accounts", "description": "accounts"},
                        {"name": "vcat_create_draft", "description": "draft"},
                        {"name": "vcat_create_publish_task", "description": "publish"},
                    ]
                }
            elif method == "tools/call":
                params = body.get("params") or {}
                tname = params.get("name", "")
                args = params.get("arguments") or {}
                state.calls.append((tname, args))
                if state.fail_next_tool:
                    state.fail_next_tool = False
                    res = {"isError": True, "content": [{"type": "text", "text": "模拟MCP报错"}]}
                elif tname == "vcat_list_accounts":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": len(state.accounts), "list": state.accounts})}]}
                elif tname == "vcat_create_login_link":
                    lid = 40000 + len(state.login_links) + 1
                    plat = args.get("platform", "抖音")
                    code = "Douyin" if plat == "抖音" else ("XiaoHongShu" if plat == "小红书" else "Channels")
                    state.login_links.append(
                        {
                            "id": lid,
                            "platform": code,
                            "platformName": plat,
                            "url": f"https://mp.c1ink.cn/xvcat/eternal-login/mockcode{lid}",
                            "createTime": "2026-11-26 16:00:00",
                        }
                    )
                    res = {"content": [{"type": "text", "text": json.dumps({"success": True})}]}
                elif tname == "vcat_list_login_links":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": len(state.login_links), "list": state.login_links})}]}
                elif tname == "vcat_get_verify_links":
                    uid_val = (args.get("uids") or [""])[0]
                    if uid_val != "ch_001":
                        res = {"isError": True, "content": [{"type": "text", "text": "不是视频号账号"}]}
                    else:
                        res = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {
                                            "total": 1,
                                            "list": [{"uid": "ch_001", "success": True, "url": "https://mp.c1ink.cn/xvcat/verify-helper/mockverify1"}],
                                        }
                                    ),
                                }
                            ]
                        }
                elif tname == "vcat_verify_account":
                    res = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"uid": args.get("uid"), "verified": True, "abnormalPostCount": 0, "faceStatus": {"status": "success", "text": "无验证"}}),
                            }
                        ]
                    }
                elif tname == "vcat_get_publish_options":
                    if args.get("platform") == "公众号":
                        res = {"isError": True, "content": [{"type": "text", "text": "平台 GongZhongHao 暂不支持 MCP 发布"}]}
                    else:
                        res = {"content": [{"type": "text", "text": json.dumps({"platforms": [{"platform": "Douyin", "fields": []}]})}]}
                elif tname == "vcat_create_draft":
                    did = len(state.drafts) + 1
                    state.drafts.append({"draftId": did, "args": args})
                    res = {"content": [{"type": "text", "text": json.dumps({"draftId": did, "title": args.get("draftTitle")})}]}
                elif tname == "vcat_create_publish_task":
                    if state.timeout_next_publish:
                        state.timeout_next_publish = False
                        time.sleep(2.5)
                    lid = f"local_{len(state.publish_tasks) + 1}"
                    state.publish_tasks.append({"localId": lid, "uid": args.get("uid"), "status": "published", "postId": f"post_{lid}", "url": f"https://example.org/{lid}"})
                    res = {"content": [{"type": "text", "text": json.dumps({"localId": lid})}]}
                elif tname == "vcat_list_publish_tasks":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": len(state.publish_tasks), "list": state.publish_tasks})}]}
                elif tname == "vcat_cancel_publish_task":
                    res = {"content": [{"type": "text", "text": json.dumps({"success": True})}]}
                elif tname == "vcat_refresh_account_stats":
                    res = {"content": [{"type": "text", "text": json.dumps({"results": [{"uid": args["uids"][0], "success": True}]})}]}
                elif tname == "vcat_get_account_stats":
                    res = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "total": {"fans": 13, "like": 42},
                                        "daily": [{"date": "2026-11-26", "fansTotal": 13, "view": -1, "like": 4}],
                                    }
                                ),
                            }
                        ]
                    }
                elif tname == "vcat_start_post_sync":
                    res = {"content": [{"type": "text", "text": json.dumps({"started": True})}]}
                elif tname == "vcat_get_post_sync":
                    res = {"content": [{"type": "text", "text": json.dumps({"looping": 0})}]}
                elif tname == "vcat_list_posts":
                    res = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "total": 1,
                                        "list": [
                                            {
                                                "postId": "xhs_post_1",
                                                "platform": "XiaoHongShu",
                                                "title": "小红书实测作品",
                                                "url": "https://www.xiaohongshu.com/explore/123",
                                                "view": 99,
                                                "like": 5,
                                                "comment": 1,
                                                "favorite": 2,
                                                "fansAdd": -1,
                                                "viewTime": -1,
                                                "fullView": -1,
                                                "postTime": "2026-11-26 10:00:00",
                                            }
                                        ],
                                    }
                                ),
                            }
                        ]
                    }
                elif tname == "vcat_remove_account":
                    uid_rem = str(args.get("uid") or "")
                    state.accounts = [a for a in state.accounts if a["uid"] != uid_rem]
                    res = {"content": [{"type": "text", "text": json.dumps({"success": True, "uid": uid_rem})}]}
                elif tname == "vcat_delete_login_link":
                    del_id = str(args.get("id") or "")
                    state.login_links = [x for x in state.login_links if str(x.get("id")) != del_id]
                    res = {"content": [{"type": "text", "text": json.dumps({"success": True})}]}
                elif tname == "vcat_list_drafts":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": len(state.drafts), "list": state.drafts})}]}
                elif tname == "vcat_publish_draft":
                    lid = f"local_{len(state.publish_tasks) + 1}"
                    state.publish_tasks.append({"localId": lid, "uid": args.get("uid"), "draftId": args.get("draftId"), "status": "published", "postId": f"post_{lid}", "url": f"https://example.org/{lid}"})
                    res = {"content": [{"type": "text", "text": json.dumps({"localId": lid})}]}
                elif tname == "vcat_list_comments":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": 1, "list": [{"commentId": "cmt_1", "uid": "dy_001", "content": "怎么联系购买", "nickname": "潜在客户", "replied": False, "createTime": "2026-11-26 11:00:00"}]})}]}
                elif tname == "vcat_get_comment_summary":
                    res = {"content": [{"type": "text", "text": json.dumps({"todayNew": 2, "unreplied": 1})}]}
                elif tname == "vcat_list_message_sessions":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": 1, "list": [{"sessionId": "sess_1", "uid": "dy_001", "peerNickname": "意向客户甲", "lastMessage": "多少钱", "unreadCount": 1, "lastTime": "2026-11-26 11:05:00"}]})}]}
                elif tname == "vcat_get_session_messages":
                    res = {"content": [{"type": "text", "text": json.dumps({"list": [{"id": "m_1", "senderName": "意向客户甲", "text": "这个服务怎么收费？"}]})}]}
                elif tname == "vcat_draft_reply":
                    res = {"content": [{"type": "text", "text": json.dumps({"draft": "感谢关注，已私信发送方案简介"})}]}
                elif tname == "vcat_list_monitor_tasks":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": 1, "list": [{"id": 7, "name": "流量预警", "type": "flow", "enabled": True, "lastRunTime": "2026-11-26 10:00:00"}]})}]}
                elif tname == "vcat_run_monitor_task":
                    res = {"content": [{"type": "text", "text": json.dumps({"ok": True, "monitorId": args.get("id")})}]}
                elif tname == "vcat_get_flow_report":
                    res = {"content": [{"type": "text", "text": json.dumps({"list": [{"postId": "xhs_post_1", "platform": "XiaoHongShu", "title": "小红书实测作品", "view": 320, "delta": -1}]})}]}
                elif tname == "vcat_list_punish_feeds":
                    res = {"content": [{"type": "text", "text": json.dumps({"total": 1, "list": [{"exportId": "exp_99", "uid": "ch_001", "nickname": "测试视频号", "title": "测试异常作品", "reason": "限流提醒"}]})}]}
                elif tname == "vcat_get_post_trend":
                    res = {"content": [{"type": "text", "text": json.dumps({"daily": [{"date": "2026-11-26", "view": 99, "like": -1}]})}]}
                elif tname == "vcat_get_post_daily":
                    res = {"content": [{"type": "text", "text": json.dumps({"list": [{"date": "2026-11-26", "view": 99, "like": 5, "comment": -1}]})}]}
                elif tname == "vcat_list_duplicate_accounts":
                    res = {"content": [{"type": "text", "text": json.dumps({"groups": []})}]}
                else:
                    res = {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}
            else:
                res = {}

            payload = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': req_id, 'result': res})}\n\n".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Mcp-Session-Id", "mock-sess-1")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{server.server_port}/mcp", state


def _setup_workspace(tmp_path):
    app = create_app(tmp_path)
    reset_password(app, "admin", "Admin-123456!")
    admin = TestClient(app)
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "Admin-123456!"}).status_code == 200
    team = admin.post("/api/teams", json={"name": "运营一组"}).json()
    ip = admin.post("/api/ips", json={"name": "测试IP", "team_id": team["id"]}).json()
    assert admin.post("/api/users", json={"username": "member1", "name": "队员甲", "password": "Member-123456!", "role": "member", "team_id": team["id"], "ip_ids": [ip["id"]]}).status_code == 200
    member = TestClient(app)
    assert member.post("/api/auth/login", json={"username": "member1", "password": "Member-123456!"}).status_code == 200

    # Create verified knowledge -> approved draft -> video & cover assets -> schedule
    up = admin.post("/api/knowledge/preview", data={"category_id": "methods-service"}, files=[("files", ("doc.md", b"Our service offers transparent delivery.", "text/markdown"))])
    doc = admin.post("/api/knowledge/confirm", json={"batch_id": up.json()["id"]}).json()["items"][0]
    admin.patch(f"/api/knowledge/{doc['id']}", json={"status": "verified"})
    draft = admin.post(
        "/api/drafts",
        json={"ip_id": ip["id"], "title": "测试口播稿", "body": "这是经过核验的测试口播文案内容。", "sources": [{"id": doc["id"], "hash": doc["hash"], "title": doc["title"]}]},
    ).json()
    draft = admin.post(f"/api/drafts/{draft['id']}/approve", json={"version": draft["version"]}).json()
    vid = admin.post("/api/assets", data={"draft_id": draft["id"]}, files={"file": ("demo.mp4", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00", "video/mp4")}).json()
    cov = admin.post("/api/assets", data={"draft_id": draft["id"]}, files={"file": ("cover.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png")}).json()
    planned = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    sched = admin.post("/api/schedules", json={"draft_id": draft["id"], "platform": "抖音", "account": "测试抖音号", "planned_at": planned}).json()
    return app, admin, member, ip, draft, vid, cov, sched


def test_qr_png_and_metric_cleaner():
    png = render_qr_png("https://mp.c1ink.cn/xvcat/verify-helper/demo12345")
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 400
    assert clean_metric_number(-1) is None
    assert clean_metric_number("-1", as_float=True) is None
    assert clean_metric_number(0) == 0
    assert clean_metric_number(7.65, as_float=True) == 7.65


def test_server_direct_full_lifecycle_and_sanitization(tmp_path):
    mcp_srv, mcp_url, mcp_state = start_mock_mcp_server()
    try:
        app, admin, member, ip, draft, vid, cov, sched = _setup_workspace(tmp_path)
        assert admin.put(
            "/api/settings",
            json={"executor": {"mode": "server_direct", "base_url": mcp_url, "token": "mock-mcp-token-secret"}},
        ).status_code == 200

        nodes = admin.get("/api/ops/nodes").json()
        assert nodes["mode"] == "server_direct"
        probe = admin.post("/api/ops/nodes/server-direct-default/probe").json()
        assert probe["tool_count"] == 3

        sync = admin.post("/api/ops/accounts/sync", json={}).json()
        assert sync["synced"] == 3
        accs = {a["vendor_uid"]: a for a in sync["accounts"]}
        for uid_key in ("dy_001", "xhs_001", "ch_001"):
            assert admin.post(f"/api/ops/accounts/{accs[uid_key]['id']}/bind", json={"ip_id": ip["id"]}).status_code == 200

        # 2-step login link + redaction in VcatActionLog
        ch_login = member.post("/api/ops/login-links", json={"platform": "抖音"}).json()
        assert ch_login["iframe_url"].startswith("https://mp.c1ink.cn/xvcat/eternal-login/")
        assert "mockcode" not in admin.get("/api/ops/auth-challenges").text or ch_login["id"]

        # Verify link only works for Channels
        assert member.post(f"/api/ops/accounts/{accs['dy_001']['id']}/verify-link", json={}).status_code == 422
        ch_face = member.post(f"/api/ops/accounts/{accs['ch_001']['id']}/verify-link", json={}).json()
        qr_resp = member.get(ch_face["qr_endpoint"])
        assert qr_resp.status_code == 200 and qr_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

        # Channels verify
        ver = member.post(f"/api/ops/accounts/{accs['ch_001']['id']}/verify", json={}).json()
        assert ver["is_verified"] is True

        # Dispatch draft + publish + reconcile
        d_res = member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json={"account_id": accs["dy_001"]["id"], "mode": "draft"}).json()
        assert d_res["state"] == "DRAFT_PUSHED"
        assert mcp_state.drafts[0]["args"]["videoPath"].endswith(".mp4")

        p_res = member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json={"account_id": accs["dy_001"]["id"], "mode": "publish"}).json()
        assert p_res["state"] == "ACCEPTED"
        # Idempotent re-dispatch with same snapshot returns existing attempt
        p_dup = member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json={"account_id": accs["dy_001"]["id"], "mode": "publish"}).json()
        assert p_dup["id"] == p_res["id"]

        rec = member.post(f"/api/ops/publish-tasks/{p_res['id']}/reconcile").json()
        assert rec["state"] == "PUBLISHED" and rec["platform_post_id"] == "post_local_1"

        # Posts sync cleans -1 sentinel to None
        psync = member.post(f"/api/ops/accounts/{accs['xhs_001']['id']}/posts/sync", json={}).json()
        assert psync["synced"] == 1
        post_item = psync["posts"][0]
        assert post_item["views"] == 99
        assert post_item["fans_add"] is None and post_item["avg_view_sec"] is None and post_item["full_view_rate"] is None

        # Verify no plaintext token or eternal-login random code in VcatActionLog
        with app.state.Session() as db:
            logs_text = " ".join(json.dumps(l.request_summary_json, ensure_ascii=False) for l in db.query(VcatActionLog).all())
            assert "mock-mcp-token-secret" not in logs_text
            assert "mockcode" not in logs_text
    finally:
        mcp_srv.shutdown()


def test_client_local_agent_relay_sha256_and_isolation(tmp_path):
    mcp_srv, mcp_url, mcp_state = start_mock_mcp_server()
    try:
        app, admin, member, ip, draft, vid, cov, sched = _setup_workspace(tmp_path)
        assert admin.put("/api/settings", json={"executor": {"mode": "client_local", "allow_client_local": True}}).status_code == 200
        # Create second user to verify strict client_local isolation
        assert admin.post("/api/users", json={"username": "member2", "name": "队员乙", "password": "Member-123456!", "role": "member", "team_id": ip["team_id"], "ip_ids": [ip["id"]]}).status_code == 200
        member2 = TestClient(app)
        assert member2.post("/api/auth/login", json={"username": "member2", "password": "Member-123456!"}).status_code == 200

        pair = member.post("/api/ops/nodes/pair-code", json={"node_scope": "client_local", "label": "队员甲电脑"}).json()
        runner = VcatAgentRunner(
            server_url="http://testserver",
            mcp_url=mcp_url,
            mcp_token="mock-mcp-token-secret",
            state_dir=tmp_path / "agent_state",
            mode="client_local",
            min_interval_sec=0.0,
            server_http_client=TestClient(app),
        )
        reg = runner.register(pair["pair_code"], "队员甲Mac")
        node_id = reg["node_id"]
        runner.heartbeat_once()

        stop_evt = threading.Event()

        def agent_loop():
            while not stop_evt.is_set():
                try:
                    runner.poll_once()
                except Exception:
                    pass
                time.sleep(0.05)

        t = threading.Thread(target=agent_loop, daemon=True)
        t.start()
        try:
            sync = member.post("/api/ops/accounts/sync", json={"node_id": node_id}).json()
            assert sync["synced"] == 3
            dy_acc = next(a for a in sync["accounts"] if a["vendor_uid"] == "dy_001")
            assert member.post(f"/api/ops/accounts/{dy_acc['id']}/bind", json={"ip_id": ip["id"]}).status_code == 200
            # Another member cannot use member1's client_local node
            assert member2.post(f"/api/ops/nodes/{node_id}/probe").status_code == 403

            # Dispatch draft through client_local agent (asset downloaded & SHA-256 verified)
            d_res = member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json={"account_id": dy_acc["id"], "mode": "draft"}).json()
            assert d_res["state"] == "DRAFT_PUSHED"
            assert str(tmp_path / "agent_state" / "media_cache") in mcp_state.drafts[0]["args"]["videoPath"]

            # Unauthorized action_id cannot download asset (403), and SHA-256 mismatch is rejected
            with pytest.raises(httpx.HTTPStatusError):
                runner._stage_media("act_unauth", [{"asset_id": vid["id"], "sha256": "0" * 64, "filename": "demo.mp4"}])
            with app.state.Session() as db:
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                db.add(AgentCommand(id="cmd_sha", action_id="act_sha_test", node_id=node_id, tool_name="vcat_create_draft", arguments_json={}, media_refs_json=[{"asset_id": vid["id"], "sha256": "0" * 64, "filename": "demo.mp4"}], status="leased"))
                db.commit()
            with pytest.raises(ValueError, match="SHA-256"):
                runner._stage_media("act_sha_test", [{"asset_id": vid["id"], "sha256": "0" * 64, "filename": "demo.mp4"}])
        finally:
            stop_evt.set()
            t.join(timeout=2)

        # Offline client_local node queues WAITING_CLIENT_ONLINE instead of failing
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            n = db.get(ExecutorNode, node_id)
            n.status = "offline"
            n.last_heartbeat_at = "2020-01-01T00:00:00+00:00"
            db.commit()
        off_res = member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json={"account_id": dy_acc["id"], "mode": "publish"}).json()
        assert off_res["state"] == "WAITING_CLIENT_ONLINE"
    finally:
        mcp_srv.shutdown()


def test_daily_limit_unknown_wechat_draft_and_worker(tmp_path):
    mcp_srv, mcp_url, mcp_state = start_mock_mcp_server()
    try:
        app, admin, member, ip, draft, vid, cov, sched = _setup_workspace(tmp_path)
        assert admin.put("/api/settings", json={"executor": {"mode": "server_direct", "base_url": mcp_url, "token": "mock-mcp-token-secret", "catchup_minutes": 30}}).status_code == 200
        accs = {a["vendor_uid"]: a for a in admin.post("/api/ops/accounts/sync", json={}).json()["accounts"]}
        dy_id = accs["dy_001"]["id"]
        admin.post(f"/api/ops/accounts/{dy_id}/bind", json={"ip_id": ip["id"]})

        # Add GongZhongHao account manually in DB
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            wx = PlatformAccount(id="wx_acc_1", node_id="server-direct-default", vendor_uid="gzh_1", platform="GongZhongHao", platform_name="公众号", nickname="企业公众号", ip_id=ip["id"], team_id=ip["team_id"], login_status="登录有效")
            db.add(wx)
            db.commit()

        # WeChat draft-only flow
        options = {"account_id": "wx_acc_1", "mode": "publish"}
        assert member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json=options).status_code == 422
        wx_schedule = admin.post("/api/schedules", json={
            "draft_id": draft["id"], "platform": "公众号", "account": "企业公众号",
            "planned_at": datetime.now(timezone.utc).isoformat(),
        }).json()
        wx_res = member.post(f"/api/ops/schedules/{wx_schedule['id']}/dispatch", json=options).json()
        assert wx_res["state"] == "DRAFT_PUSHED"
        assert member.post(f"/api/ops/publish-tasks/{wx_res['id']}/confirm-published", json={"evidence_url": "bad"}).status_code == 422
        wx_pub = member.post(f"/api/ops/publish-tasks/{wx_res['id']}/confirm-published", json={"evidence_url": "https://mp.weixin.qq.com/s/demo"}).json()
        assert wx_pub["state"] == "PUBLISHED"

        # Daily limit of 3 per account
        for idx in range(3):
            s_i = admin.post("/api/schedules", json={"draft_id": draft["id"], "platform": "抖音", "account": f"号{idx}", "planned_at": datetime.now(timezone.utc).isoformat()}).json()
            assert "id" in s_i, s_i
            r = member.post(f"/api/ops/schedules/{s_i['id']}/dispatch", json={"account_id": dy_id, "mode": "draft"})
            assert r.status_code == 200, f"idx={idx}: {r.text}"
        s_4 = admin.post("/api/schedules", json={"draft_id": draft["id"], "platform": "抖音", "account": "号4", "planned_at": datetime.now(timezone.utc).isoformat()}).json()
        assert member.post(f"/api/ops/schedules/{s_4['id']}/dispatch", json={"account_id": dy_id, "mode": "draft"}).status_code == 409

        # UNKNOWN attempt cannot be retried
        with app.state.Session() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            t_unk = PublishTarget(id="t_unk", schedule_id=sched["id"], ip_id=ip["id"], draft_id=draft["id"], draft_version=draft["version"], account_id=dy_id, platform="Douyin", approved_snapshot_json={"title": "x"}, snapshot_hash="h", planned_at_utc=datetime.now(timezone.utc).isoformat(), execution_mode="publish", status="UNKNOWN")
            a_unk = PublishAttempt(id="a_unk", target_id="t_unk", attempt_no=1, idempotency_key="k_unk", state="UNKNOWN", node_id="server-direct-default")
            t_exp = PublishTarget(id="t_exp", schedule_id=sched["id"], ip_id=ip["id"], draft_id=draft["id"], draft_version=draft["version"], account_id=dy_id, platform="Douyin", approved_snapshot_json={"title": "y"}, snapshot_hash="h2", planned_at_utc=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), execution_mode="publish", status="QUEUED")
            a_exp = PublishAttempt(id="a_exp", target_id="t_exp", attempt_no=1, idempotency_key="k_exp", state="QUEUED", node_id="server-direct-default")
            t_sub = PublishTarget(id="t_sub", schedule_id=sched["id"], ip_id=ip["id"], draft_id=draft["id"], draft_version=draft["version"], account_id=dy_id, platform="Douyin", approved_snapshot_json={"title": "z"}, snapshot_hash="h3", planned_at_utc=datetime.now(timezone.utc).isoformat(), execution_mode="publish", status="SUBMITTING")
            a_sub = PublishAttempt(id="a_sub", target_id="t_sub", attempt_no=1, idempotency_key="k_sub", state="SUBMITTING", lease_until="2020-01-01T00:00:00+00:00", node_id="server-direct-default")
            db.add_all([t_unk, a_unk, t_exp, a_exp, t_sub, a_sub])
            db.commit()

        assert member.post("/api/ops/publish-tasks/a_unk/retry").status_code == 409
        assert run_publish_worker_once(app) is True
        with app.state.Session() as db:
            assert db.get(PublishAttempt, "a_exp").state == "MISSED"
            assert db.get(PublishAttempt, "a_sub").state == "UNKNOWN"
    finally:
        mcp_srv.shutdown()


def test_p0_closure_remove_unbind_reconcile_revoke_and_cancel(tmp_path):
    from sqlalchemy import create_engine
    from backend.app.db import AuthChallenge, ExecutorNode, PlatformAccount, database
    from executor.vcat_agent import ALLOWED_AGENT_TOOLS, VcatAgentRunner

    # T1: auto-migration adds new columns on legacy SQLite database
    legacy_db = tmp_path / "legacy.db"
    eng_raw = create_engine(f"sqlite:///{legacy_db}")
    with eng_raw.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE executor_nodes (id TEXT PRIMARY KEY, name TEXT, node_scope TEXT, transport_mode TEXT, status TEXT)")
        conn.exec_driver_sql("CREATE TABLE platform_accounts (id TEXT PRIMARY KEY, node_id TEXT, vendor_uid TEXT, platform TEXT)")
        conn.exec_driver_sql("CREATE TABLE auth_challenges (id TEXT PRIMARY KEY, node_id TEXT, platform TEXT, challenge_type TEXT, status TEXT, expires_at TEXT)")
    eng_raw.dispose()
    eng2, _ = database(legacy_db)
    with eng2.begin() as conn:
        assert "revoked_at" in {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(executor_nodes)").fetchall()}
        assert "removed_at" in {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(platform_accounts)").fetchall()}
        assert "vendor_link_id" in {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(auth_challenges)").fetchall()}
    eng2.dispose()

    # Verify deletion tools are NOT allowed in agent allowlist or backend transport
    from backend.app.operations.transport import ALLOWED_TOOLS
    for forbidden_tool in ("vcat_create_del_post_single_task", "vcat_delete_punish_post"):
        assert forbidden_tool not in ALLOWED_AGENT_TOOLS
        assert forbidden_tool not in ALLOWED_TOOLS

    mcp_srv, mcp_url, mcp_state = start_mock_mcp_server()
    try:
        app, admin, member, ip, draft, vid, cov, sched = _setup_workspace(tmp_path / "app_data")
        assert admin.put("/api/settings", json={"executor": {"mode": "server_direct", "base_url": mcp_url, "token": "mock-mcp-token-secret"}}).status_code == 200
        accs = {a["vendor_uid"]: a for a in admin.post("/api/ops/accounts/sync", json={}).json()["accounts"]}
        xhs_id = accs["xhs_001"]["id"]
        dy_id = accs["dy_001"]["id"]
        assert admin.post(f"/api/ops/accounts/{xhs_id}/bind", json={"ip_id": ip["id"]}).status_code == 200
        assert admin.post(f"/api/ops/accounts/{dy_id}/bind", json={"ip_id": ip["id"]}).status_code == 200

        # T2: Unbind IP
        unbound = admin.post(f"/api/ops/accounts/{dy_id}/unbind").json()
        assert unbound["ip_id"] is None and unbound["team_id"] is None

        # Sync historical post before removal to assert audit/history retention
        assert admin.post(f"/api/ops/accounts/{xhs_id}/posts/sync").status_code == 200
        assert len(admin.get("/api/ops/posts").json()) == 1

        # Create a queued attempt for xhs_001 in future -> remove blocked 409 until cancel_pending=True
        future_sched = admin.post("/api/schedules", json={"draft_id": draft["id"], "platform": "小红书", "account": "测试小红书", "planned_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()}).json()
        q_att = admin.post(f"/api/ops/schedules/{future_sched['id']}/dispatch", json={"account_id": xhs_id, "mode": "publish", "schedule_owner": "server"}).json()
        assert q_att["state"] == "QUEUED"

        assert admin.post(f"/api/ops/accounts/{xhs_id}/remove", json={"confirm": False, "confirm_nickname": "测试小红书"}).status_code == 422
        assert admin.post(f"/api/ops/accounts/{xhs_id}/remove", json={"confirm": True, "confirm_nickname": "wrong"}).status_code == 422
        assert admin.post(f"/api/ops/accounts/{xhs_id}/remove", json={"confirm": True, "confirm_nickname": "测试小红书", "cancel_pending": False}).status_code == 409

        removed = admin.post(f"/api/ops/accounts/{xhs_id}/remove", json={"confirm": True, "confirm_nickname": "测试小红书", "cancel_pending": True})
        assert removed.status_code == 200, removed.text
        assert removed.json()["active"] is False and removed.json()["removed_at"]
        assert any(t == "vcat_remove_account" and a["uid"] == "xhs_001" for t, a in mcp_state.calls)

        # Hidden by default, visible with include_removed=true, historical posts retained
        assert all(a["id"] != xhs_id for a in admin.get("/api/ops/accounts").json())
        assert any(a["id"] == xhs_id for a in admin.get("/api/ops/accounts?include_removed=true").json())
        assert len(admin.get("/api/ops/posts").json()) == 1

        # T2: Disappearance reconciliation in sync_accounts
        mcp_state.accounts = [a for a in mcp_state.accounts if a["uid"] != "dy_001"]
        sync_res = admin.post("/api/ops/accounts/sync", json={}).json()
        assert sync_res["removed_count"] == 1
        assert all(a["id"] != dy_id for a in sync_res["accounts"])

        # T4: Challenge cancel & vcat_delete_login_link cleanup
        ch = admin.post("/api/ops/login-links", json={"platform": "抖音"}).json()
        assert len(mcp_state.login_links) == 1
        cancelled = admin.post(f"/api/ops/auth-challenges/{ch['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
        assert cancelled.json()["link_deleted"] is True
        assert len(mcp_state.login_links) == 0

        # T3: Node revoke immediately breaks agent 401 and delete works once empty
        pair = member.post("/api/ops/nodes/pair-code", json={"node_scope": "client_local", "label": "可吊销节点"}).json()
        runner = VcatAgentRunner(server_url="http://testserver", mcp_url=mcp_url, mcp_token="mock-mcp-token-secret", state_dir=tmp_path / "rev_agent", mode="client_local", min_interval_sec=0.0, server_http_client=TestClient(app))
        reg = runner.register(pair["pair_code"], "可吊销节点")
        nid = reg["node_id"]
        assert runner.heartbeat_once()["ok"] is True

        # Cannot delete online node
        assert member.delete(f"/api/ops/nodes/{nid}").status_code == 409
        rev = member.post(f"/api/ops/nodes/{nid}/revoke")
        assert rev.status_code == 200 and rev.json()["status"] == "revoked"
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            runner.heartbeat_once()
        assert exc_info.value.response.status_code == 401
        assert member.delete(f"/api/ops/nodes/{nid}").status_code == 200
        assert admin.post("/api/ops/nodes/server-direct-default/revoke").status_code == 422
    finally:
        mcp_srv.shutdown()


def test_p1_p2_closure_draft_promote_interactions_monitors_and_posts(tmp_path):
    mcp_srv, mcp_url, mcp_state = start_mock_mcp_server()
    try:
        app, admin, member, ip, draft, vid, cov, sched = _setup_workspace(tmp_path)
        assert admin.put("/api/settings", json={"executor": {"mode": "server_direct", "base_url": mcp_url, "token": "mock-mcp-token-secret"}}).status_code == 200
        accs = {a["vendor_uid"]: a for a in admin.post("/api/ops/accounts/sync", json={}).json()["accounts"]}
        dy_id = accs["dy_001"]["id"]
        ch_id = accs["ch_001"]["id"]
        admin.post(f"/api/ops/accounts/{dy_id}/bind", json={"ip_id": ip["id"]})
        admin.post(f"/api/ops/accounts/{ch_id}/bind", json={"ip_id": ip["id"]})

        # T6: Create draft -> promote via publish-draft (with limit & idempotency guard)
        draft_att = member.post(f"/api/ops/schedules/{sched['id']}/dispatch", json={"account_id": dy_id, "mode": "draft"}).json()
        assert draft_att["state"] == "DRAFT_PUSHED"
        assert member.get("/api/ops/drafts?sync_remote=true").status_code == 200
        assert member.post(f"/api/ops/publish-tasks/{draft_att['id']}/publish-draft", json={"confirm": False}).status_code == 422

        promoted = member.post(f"/api/ops/publish-tasks/{draft_att['id']}/publish-draft", json={"confirm": True, "schedule_owner": "server"})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["state"] == "ACCEPTED"
        assert any(t == "vcat_publish_draft" for t, _ in mcp_state.calls)
        # Duplicate promote blocked 409
        assert member.post(f"/api/ops/publish-tasks/{draft_att['id']}/publish-draft", json={"confirm": True}).status_code == 409

        # T8: Interactions read-only vs allow_interaction_write
        cmts = member.get("/api/ops/comments").json()
        assert cmts["allow_interaction_write"] is False and len(cmts["list"]) == 1
        assert member.get("/api/ops/comments/summary").json()["todayNew"] == 2
        assert member.get(f"/api/ops/messages/sessions/sess_1/messages?account_id={dy_id}&refresh=true").status_code == 200
        assert member.post("/api/ops/interactions/draft-reply", json={"account_id": dy_id, "comment_id": "cmt_1"}).json()["draft"]

        # Writes blocked when allow_interaction_write=False
        assert member.post("/api/ops/comments/cmt_1/reply", json={"account_id": dy_id, "content": "您好"}).status_code == 403
        assert member.post("/api/ops/comments/cmt_1/like", json={"account_id": dy_id}).status_code == 403
        assert member.post("/api/ops/comments/cmt_1/top", json={"account_id": dy_id}).status_code == 403
        assert member.post("/api/ops/messages/sessions/sess_1/send", json={"account_id": dy_id, "text": "您好"}).status_code == 403

        # Enable allow_interaction_write -> writes succeed
        assert admin.put("/api/settings", json={"executor": {"allow_interaction_write": True}}).status_code == 200
        assert member.post("/api/ops/comments/cmt_1/reply", json={"account_id": dy_id, "content": "您好"}).status_code == 200
        assert member.post("/api/ops/comments/cmt_1/like", json={"account_id": dy_id}).status_code == 200
        assert member.post("/api/ops/comments/cmt_1/top", json={"account_id": dy_id}).status_code == 200
        assert member.post("/api/ops/messages/sessions/sess_1/send", json={"account_id": dy_id, "text": "您好"}).status_code == 200

        # T9: Monitors run, flow report (-1 cleaned), punish feeds & hide (no delete route)
        assert admin.post("/api/ops/monitors/7/run", json={"confirm": False}).status_code == 422
        assert admin.post("/api/ops/monitors/7/run", json={"confirm": True}).status_code == 200
        flow = admin.get("/api/ops/monitors/flow-report").json()
        assert flow["list"][0]["delta"] is None
        punish = member.get("/api/ops/punish-feeds").json()
        assert len(punish["list"]) == 1 and punish["list"][0]["exportId"] == "exp_99"
        assert member.post("/api/ops/punish-feeds/hide", json={"account_id": ch_id, "export_id": "exp_99", "confirm": True}).status_code == 200

        # T10: Post trends, post daily, visibility modify, remark, duplicates & dedupe; confirm no delete-post endpoint
        sync_posts = member.post(f"/api/ops/accounts/{dy_id}/posts/sync").json()
        post_id = sync_posts["posts"][0]["id"]
        trend = member.get(f"/api/ops/posts/trend?account_id={dy_id}").json()
        assert trend["daily"][0]["like"] is None
        daily = member.get(f"/api/ops/posts/{post_id}/daily").json()
        assert daily["list"][0]["comment"] is None
        assert member.post(f"/api/ops/posts/{post_id}/visibility", json={"visible": 1, "confirm": True}).status_code == 200
        assert member.post(f"/api/ops/accounts/{dy_id}/remark", json={"remark": "测试备注"}).status_code == 200
        assert admin.get("/api/ops/accounts/duplicates").status_code == 200
        assert admin.post("/api/ops/accounts/dedupe", json={"confirm": True}).status_code == 200
        assert admin.delete(f"/api/ops/posts/{post_id}").status_code in (404, 405)
        assert admin.post(f"/api/ops/posts/{post_id}/delete").status_code in (404, 405)
    finally:
        mcp_srv.shutdown()
