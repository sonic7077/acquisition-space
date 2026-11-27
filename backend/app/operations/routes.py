"""Unified /api/ops/* routes across server-connected and client-local modes."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .service import VcatOperationsService
from .transport import RelayEventBus


class PairCodeInput(BaseModel):
    node_scope: str = Field(default="client_local")
    label: str = Field(default="", max_length=60)


class AgentDeployPlanInput(BaseModel):
    os_key: str = Field(default="macos_arm64", max_length=32)
    node_scope: str = Field(default="client_local")
    label: str = Field(default="", max_length=60)
    download_url: str | None = Field(default=None, max_length=1000)
    server_url: str | None = Field(default=None, max_length=500)
    poll_wait: float = Field(default=25.0, ge=0.0, le=30.0)


class UpdateNodeInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SwitchExecutorInput(BaseModel):
    mode: str | None = None
    default_node_id: str | None = None


class BindAccountInput(BaseModel):
    ip_id: str = Field(min_length=1)


class LoginLinkInput(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    node_id: str | None = None


class DispatchScheduleInput(BaseModel):
    account_id: str = Field(min_length=1)
    mode: str = Field(default="publish")
    content_type: str = Field(default="video")
    schedule_owner: str = Field(default="server")
    platform_options: dict | None = None


class ConfirmPublishedInput(BaseModel):
    evidence_url: str = Field(min_length=1, max_length=1000)


class ToggleMonitorInput(BaseModel):
    enabled: bool
    node_id: str | None = None


class RemoveAccountInput(BaseModel):
    confirm: bool = False
    confirm_nickname: str = Field(default="", max_length=120)
    cancel_pending: bool = False


class PublishDraftInput(BaseModel):
    confirm: bool = False
    schedule_owner: str = Field(default="server")
    planned_at: str | None = None
    platform_options: dict | None = None


class RepublishAttemptInput(BaseModel):
    mode: str | None = None
    schedule_owner: str | None = None


class DraftReplyInput(BaseModel):
    account_id: str = Field(min_length=1)
    comment_id: str | None = None
    session_id: str | None = None
    requirement: str = Field(default="", max_length=500)


class CommentReplyInput(BaseModel):
    account_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=1000)


class CommentActionInput(BaseModel):
    account_id: str = Field(min_length=1)
    cancel: bool = False


class SendMessageInput(BaseModel):
    account_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)


class ConfirmIdInput(BaseModel):
    confirm: bool = False
    node_id: str | None = None


class HidePunishInput(BaseModel):
    account_id: str = Field(min_length=1)
    export_id: str = Field(min_length=1)
    confirm: bool = False


class PostVisibilityInput(BaseModel):
    visible: int
    confirm: bool = False


class AccountRemarkInput(BaseModel):
    remark: str = Field(default="", max_length=200)


class DedupeAccountsInput(BaseModel):
    confirm: bool = False
    uids: list[str] | None = None
    node_id: str | None = None


def register_operations(app, authenticated_user):
    if not hasattr(app.state, "relay_bus"):
        app.state.relay_bus = RelayEventBus()
    service = VcatOperationsService(app)
    app.state.ops_service = service

    def actor_dep(request: Request):
        with app.state.Session() as db:
            return authenticated_user(request, db)

    @app.get("/api/ops/nodes")
    def list_nodes(actor=Depends(actor_dep)):
        return service.list_nodes(actor)

    @app.post("/api/ops/executor/switch")
    def switch_executor(data: SwitchExecutorInput, actor=Depends(actor_dep)):
        kwargs: dict[str, str | None] = {}
        if "mode" in data.model_fields_set:
            kwargs["mode"] = data.mode
        if "default_node_id" in data.model_fields_set:
            kwargs["default_node_id"] = data.default_node_id
        return service.switch_executor(actor, **kwargs)

    @app.post("/api/ops/nodes/pair-code")
    def create_pair_code(data: PairCodeInput, actor=Depends(actor_dep)):
        return service.create_pair_code(actor, data.node_scope, data.label)

    @app.patch("/api/ops/nodes/{node_id}")
    def rename_node(node_id: str, data: UpdateNodeInput, actor=Depends(actor_dep)):
        return service.rename_node(actor, node_id, data.name)

    @app.post("/api/ops/nodes/{node_id}/probe")
    def probe_node(node_id: str, actor=Depends(actor_dep)):
        return service.probe_node(actor, node_id)

    @app.post("/api/ops/nodes/{node_id}/revoke")
    def revoke_node(node_id: str, actor=Depends(actor_dep)):
        return service.revoke_node(actor, node_id)

    @app.delete("/api/ops/nodes/{node_id}")
    def delete_node(node_id: str, actor=Depends(actor_dep)):
        return service.delete_node(actor, node_id)

    @app.get("/api/ops/agent/deploy-config")
    def get_agent_deploy_config(request: Request, actor=Depends(actor_dep)):
        base_url = str(request.base_url).rstrip("/")
        return service.get_agent_deploy_config(actor, base_url)

    @app.post("/api/ops/agent/deploy-plan")
    def create_agent_deploy_plan(data: AgentDeployPlanInput, request: Request, actor=Depends(actor_dep)):
        base_url = (data.server_url or str(request.base_url)).rstrip("/")
        return service.create_agent_deploy_plan(
            actor,
            os_key=data.os_key,
            node_scope=data.node_scope,
            label=data.label,
            download_url=data.download_url,
            server_url=base_url,
            poll_wait=data.poll_wait,
        )

    @app.get("/api/ops/agent/package")
    def download_agent_package(os: str = "macos_arm64"):
        from .agent_installer import build_agent_package
        try:
            filename, media_type, content = build_agent_package(os)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/ops/agent/local-start")
    def start_local_agent(actor=Depends(actor_dep)):
        return service.try_local_agent_start(actor)

    @app.get("/api/ops/accounts")
    def list_accounts(ip_id: str = "", include_removed: bool = False, actor=Depends(actor_dep)):
        return service.list_accounts(actor, ip_id=ip_id, include_removed=include_removed)

    @app.post("/api/ops/accounts/sync")
    def sync_accounts(data: dict | None = None, actor=Depends(actor_dep)):
        return service.sync_accounts(actor, node_id=(data or {}).get("node_id"))

    @app.get("/api/ops/accounts/duplicates")
    def list_duplicate_accounts(node_id: str | None = None, actor=Depends(actor_dep)):
        return service.list_duplicate_accounts(actor, node_id=node_id)

    @app.post("/api/ops/accounts/dedupe")
    def dedupe_accounts(data: DedupeAccountsInput, actor=Depends(actor_dep)):
        return service.dedupe_accounts(actor, confirm=data.confirm, uids=data.uids, node_id=data.node_id)

    @app.post("/api/ops/accounts/{account_id}/bind")
    def bind_account(account_id: str, data: BindAccountInput, actor=Depends(actor_dep)):
        return service.bind_account(actor, account_id, data.ip_id)

    @app.post("/api/ops/accounts/{account_id}/unbind")
    def unbind_account(account_id: str, actor=Depends(actor_dep)):
        return service.unbind_account(actor, account_id)

    @app.post("/api/ops/accounts/{account_id}/remove")
    def remove_account(account_id: str, data: RemoveAccountInput, actor=Depends(actor_dep)):
        return service.remove_account(
            actor,
            account_id,
            confirm=data.confirm,
            confirm_nickname=data.confirm_nickname,
            cancel_pending=data.cancel_pending,
        )

    @app.post("/api/ops/accounts/{account_id}/remark")
    def set_account_remark(account_id: str, data: AccountRemarkInput, actor=Depends(actor_dep)):
        return service.set_account_remark(actor, account_id, data.remark)

    @app.post("/api/ops/accounts/{account_id}/verify")
    def verify_account(account_id: str, actor=Depends(actor_dep)):
        return service.verify_account(actor, account_id)

    @app.post("/api/ops/accounts/{account_id}/verify-link")
    def create_verify_link(account_id: str, data: dict | None = None, actor=Depends(actor_dep)):
        return service.create_verify_link_challenge(actor, account_id, target_id=(data or {}).get("target_id"))

    @app.post("/api/ops/login-links")
    def create_login_link(data: LoginLinkInput, actor=Depends(actor_dep)):
        return service.create_login_link_challenge(actor, data.platform, node_id=data.node_id)

    @app.get("/api/ops/auth-challenges")
    def list_challenges(actor=Depends(actor_dep)):
        return service.list_challenges(actor)

    @app.get("/api/ops/auth-challenges/{challenge_id}/qr")
    def challenge_qr(challenge_id: str, actor=Depends(actor_dep)):
        png = service.challenge_qr_png(actor, challenge_id)
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/ops/auth-challenges/{challenge_id}/complete")
    def complete_challenge(challenge_id: str, actor=Depends(actor_dep)):
        return service.complete_challenge(actor, challenge_id)

    @app.post("/api/ops/auth-challenges/{challenge_id}/cancel")
    def cancel_challenge(challenge_id: str, actor=Depends(actor_dep)):
        return service.cancel_challenge(actor, challenge_id)

    @app.get("/api/ops/publish-options/{platform}")
    def publish_options(platform: str, actor=Depends(actor_dep)):
        return service.get_publish_options(actor, platform)

    @app.post("/api/ops/schedules/{schedule_id}/dispatch")
    def dispatch_schedule(schedule_id: str, data: DispatchScheduleInput, actor=Depends(actor_dep)):
        return service.dispatch_schedule(
            actor,
            schedule_id,
            account_id=data.account_id,
            mode=data.mode,
            content_type=data.content_type,
            schedule_owner=data.schedule_owner,
            platform_options=data.platform_options,
        )

    @app.get("/api/ops/publish-tasks")
    def list_publish_tasks(ip_id: str = "", actor=Depends(actor_dep)):
        return service.list_attempts(actor, ip_id=ip_id)

    @app.post("/api/ops/publish-tasks/reconcile-active")
    def reconcile_active_attempts(ip_id: str = "", actor=Depends(actor_dep)):
        return service.reconcile_active_attempts(actor, ip_id=ip_id)

    @app.post("/api/ops/publish-tasks/{attempt_id}/reconcile")
    def reconcile_attempt(attempt_id: str, actor=Depends(actor_dep)):
        return service.reconcile_attempt(actor, attempt_id)

    @app.post("/api/ops/publish-tasks/{attempt_id}/cancel")
    def cancel_attempt(attempt_id: str, actor=Depends(actor_dep)):
        return service.cancel_attempt(actor, attempt_id)

    @app.post("/api/ops/publish-tasks/{attempt_id}/retry")
    def retry_attempt(attempt_id: str, actor=Depends(actor_dep)):
        return service.retry_attempt(actor, attempt_id)

    @app.post("/api/ops/publish-tasks/{attempt_id}/republish")
    def republish_attempt(attempt_id: str, data: RepublishAttemptInput | None = None, actor=Depends(actor_dep)):
        return service.republish_attempt(
            actor,
            attempt_id,
            mode=data.mode if data else None,
            schedule_owner=data.schedule_owner if data else None,
        )

    @app.post("/api/ops/publish-tasks/{attempt_id}/confirm-published")
    def confirm_wechat_published(attempt_id: str, data: ConfirmPublishedInput, actor=Depends(actor_dep)):
        return service.confirm_wechat_published(actor, attempt_id, data.evidence_url)

    @app.get("/api/ops/drafts")
    def list_drafts(ip_id: str = "", sync_remote: bool = False, actor=Depends(actor_dep)):
        return service.list_drafts(actor, ip_id=ip_id, sync_remote=sync_remote)

    @app.post("/api/ops/publish-tasks/{attempt_id}/publish-draft")
    def publish_draft(attempt_id: str, data: PublishDraftInput, actor=Depends(actor_dep)):
        return service.publish_draft(
            actor,
            attempt_id,
            confirm=data.confirm,
            schedule_owner=data.schedule_owner,
            planned_at=data.planned_at,
            platform_options=data.platform_options,
        )

    @app.post("/api/ops/accounts/{account_id}/stats/refresh")
    def refresh_stats(account_id: str, actor=Depends(actor_dep)):
        return service.refresh_account_stats(actor, account_id)

    @app.get("/api/ops/stats/accounts")
    def account_stats(ip_id: str = "", actor=Depends(actor_dep)):
        return service.list_account_stats(actor, ip_id=ip_id)

    @app.post("/api/ops/accounts/{account_id}/posts/sync")
    def sync_posts(account_id: str, actor=Depends(actor_dep)):
        return service.sync_posts(actor, account_id)

    @app.get("/api/ops/posts")
    def list_posts(ip_id: str = "", actor=Depends(actor_dep)):
        return service.list_posts(actor, ip_id=ip_id)

    @app.get("/api/ops/posts/trend")
    def post_trend(account_id: str | None = None, start_date: str | None = None, end_date: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.get_post_trend(actor, account_id=account_id, start_date=start_date, end_date=end_date)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"daily": [], "notice": exc.detail}
            raise

    @app.get("/api/ops/posts/{post_id}/daily")
    def post_daily(post_id: str, start_date: str | None = None, end_date: str | None = None, actor=Depends(actor_dep)):
        return service.get_post_daily(actor, post_id, start_date=start_date, end_date=end_date)

    @app.post("/api/ops/posts/{post_id}/visibility")
    def update_post_visibility(post_id: str, data: PostVisibilityInput, actor=Depends(actor_dep)):
        return service.update_post_visibility(actor, post_id, visible=data.visible, confirm=data.confirm)

    @app.get("/api/ops/comments")
    def list_comments(account_id: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.list_comments(actor, account_id=account_id)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"list": [], "total": 0, "allow_interaction_write": False, "notice": exc.detail}
            raise

    @app.get("/api/ops/comments/summary")
    def comment_summary(node_id: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.get_comment_summary(actor, node_id=node_id)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"notice": exc.detail}
            raise

    @app.post("/api/ops/interactions/draft-reply")
    def draft_reply(data: DraftReplyInput, actor=Depends(actor_dep)):
        return service.draft_interaction_reply(
            actor,
            data.account_id,
            comment_id=data.comment_id,
            session_id=data.session_id,
            requirement=data.requirement,
        )

    @app.post("/api/ops/comments/{comment_id}/reply")
    def reply_comment(comment_id: str, data: CommentReplyInput, actor=Depends(actor_dep)):
        return service.reply_comment(actor, comment_id, data.account_id, data.content)

    @app.post("/api/ops/comments/{comment_id}/like")
    def like_comment(comment_id: str, data: CommentActionInput, actor=Depends(actor_dep)):
        return service.like_comment(actor, comment_id, data.account_id, cancel=data.cancel)

    @app.post("/api/ops/comments/{comment_id}/top")
    def top_comment(comment_id: str, data: CommentActionInput, actor=Depends(actor_dep)):
        return service.top_comment(actor, comment_id, data.account_id)

    @app.get("/api/ops/messages/sessions")
    def list_message_sessions(account_id: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.list_message_sessions(actor, account_id=account_id)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"list": [], "total": 0, "allow_interaction_write": False, "notice": exc.detail}
            raise

    @app.get("/api/ops/messages/sessions/{session_id}/messages")
    def session_messages(session_id: str, account_id: str, refresh: bool = False, actor=Depends(actor_dep)):
        return service.get_session_messages(actor, session_id, account_id, refresh=refresh)

    @app.post("/api/ops/messages/sessions/{session_id}/send")
    def send_message(session_id: str, data: SendMessageInput, actor=Depends(actor_dep)):
        return service.send_session_message(actor, session_id, data.account_id, data.text)

    @app.get("/api/ops/monitors")
    def list_monitors(node_id: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.list_monitors(actor, node_id=node_id)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"list": [], "notice": exc.detail}
            raise

    @app.post("/api/ops/monitors/{monitor_id}/toggle")
    def toggle_monitor(monitor_id: int, data: ToggleMonitorInput, actor=Depends(actor_dep)):
        return service.toggle_monitor(actor, monitor_id, data.enabled, node_id=data.node_id)

    @app.post("/api/ops/monitors/{monitor_id}/run")
    def run_monitor(monitor_id: int, data: ConfirmIdInput, actor=Depends(actor_dep)):
        return service.run_monitor(actor, monitor_id, confirm=data.confirm, node_id=data.node_id)

    @app.get("/api/ops/monitors/flow-report")
    def flow_report(account_id: str | None = None, platform: str | None = None, node_id: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.get_flow_report(actor, account_id=account_id, platform=platform, node_id=node_id)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"list": [], "notice": exc.detail}
            raise

    @app.get("/api/ops/punish-feeds")
    def punish_feeds(account_id: str | None = None, actor=Depends(actor_dep)):
        try:
            return service.list_punish_feeds(actor, account_id=account_id)
        except HTTPException as exc:
            if exc.status_code in (409, 502):
                return {"list": [], "total": 0, "notice": exc.detail}
            raise

    @app.post("/api/ops/punish-feeds/hide")
    def hide_punish_post(data: HidePunishInput, actor=Depends(actor_dep)):
        return service.hide_punish_post(actor, data.account_id, data.export_id, confirm=data.confirm)
