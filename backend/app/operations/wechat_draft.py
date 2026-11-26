"""Official WeChat Official Account (公众号) draft-box adapter for Phase 1.

Xiaovmao 3.1.0 MCP explicitly does not support GongZhongHao publishing.
Phase 1 pushes content to the Official Account draft box (via official API when
configured, or explicit manual draft staging) and strictly marks state as
DRAFT_PUSHED until an operator confirms public broadcast with evidence URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import httpx


@dataclass(frozen=True)
class WechatDraftResult:
    ok: bool
    mode: str  # official_api | manual_draft
    media_id: str | None = None
    error_message: str | None = None


def push_official_account_draft(
    *,
    app_id: str,
    app_secret: str,
    title: str,
    body: str,
    cover_path: Path | None,
    author: str = "",
) -> WechatDraftResult:
    if not app_id or not app_secret:
        return WechatDraftResult(
            ok=True,
            mode="manual_draft",
            media_id=None,
            error_message=None,
        )

    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            tok_resp = client.post(
                "https://api.weixin.qq.com/cgi-bin/stable_token",
                json={
                    "grant_type": "client_credential",
                    "appid": app_id,
                    "secret": app_secret,
                    "force_refresh": False,
                },
            )
            tok_resp.raise_for_status()
            tok_data = tok_resp.json()
            access_token = tok_data.get("access_token")
            if not access_token:
                return WechatDraftResult(
                    ok=False,
                    mode="official_api",
                    error_message=f"获取公众号 token 失败: {tok_data.get('errmsg', '未知错误')}",
                )

            thumb_media_id = ""
            if cover_path and cover_path.is_file():
                with cover_path.open("rb") as fp:
                    up_resp = client.post(
                        "https://api.weixin.qq.com/cgi-bin/material/add_material",
                        params={"access_type": "image", "type": "image", "access_token": access_token},
                        files={"media": (cover_path.name, fp, "image/png")},
                    )
                up_resp.raise_for_status()
                up_data = up_resp.json()
                thumb_media_id = up_data.get("media_id", "")
                if not thumb_media_id:
                    return WechatDraftResult(
                        ok=False,
                        mode="official_api",
                        error_message=f"上传公众号封面素材失败: {up_data.get('errmsg', '未知错误')}",
                    )

            html_content = "".join(f"<p>{line}</p>" for line in (body or "").splitlines() if line.strip()) or "<p></p>"
            draft_payload = {
                "articles": [
                    {
                        "title": (title or "未命名草稿")[:64],
                        "author": author[:8],
                        "digest": (body or "")[:120],
                        "content": html_content,
                        **({"thumb_media_id": thumb_media_id} if thumb_media_id else {}),
                    }
                ]
            }
            draft_resp = client.post(
                "https://api.weixin.qq.com/cgi-bin/draft/add",
                params={"access_token": access_token},
                json=draft_payload,
            )
            draft_resp.raise_for_status()
            draft_data = draft_resp.json()
            media_id = draft_data.get("media_id")
            if not media_id:
                return WechatDraftResult(
                    ok=False,
                    mode="official_api",
                    error_message=f"推送公众号草稿箱失败: {draft_data.get('errmsg', '未知错误')}",
                )
            return WechatDraftResult(ok=True, mode="official_api", media_id=str(media_id))
    except Exception as exc:
        return WechatDraftResult(ok=False, mode="official_api", error_message=f"公众号接口调用异常: {exc}")
