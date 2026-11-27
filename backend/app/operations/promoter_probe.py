"""Read-only local channel-promoter identity probe (without reading passwords, tokens, or raw phone numbers)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

_RAW_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_MASKED_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d\*{4}\d{4})(?!\d)")


def mask_mobile_text(raw: Any) -> str | None:
    """Extract and mask Chinese mainland mobile numbers (keep first 3 and last 4 digits)."""
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
    """Return (safe_username, masked_mobile) ensuring raw phone numbers never leak."""
    if raw is None or isinstance(raw, bool):
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    masked = mask_mobile_text(text)
    if masked and _RAW_MOBILE_RE.fullmatch(text):
        return None, masked
    if masked:
        redacted = _RAW_MOBILE_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", text)
        return redacted, masked
    return text, None


def build_promoter_display_label(
    *,
    remark: str | None = "",
    nickname: str | None = "",
    username: str | None = "",
    masked_mobile: str | None = "",
    external_user_id: str | None = "",
) -> str:
    """Build human-readable promoter label prioritizing remark/nickname/username/masked_mobile over raw ID."""
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
    if ext:
        return f"渠道推广器 #{ext}"
    return "未命名渠道推广器"


def _read_vcat_user_db(db_path: Path) -> dict[str, Any] | None:
    """Read only non-sensitive identity metadata from a single vcat.db using read-only SQLite URI."""
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
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


def _detect_vcat_state(data_home: Path | str | None = None) -> dict[str, Any]:
    empty = {"detected": False, "provider": "vcat", "active_account": None, "known_accounts": []}
    raw_home = data_home or os.environ.get("PROMOTER_DATA_HOME") or os.environ.get("VCAT_HOME")
    if not raw_home and os.environ.get("PYTEST_CURRENT_TEST"):
        return empty
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
        info = _read_vcat_user_db(db_file)
        if not info:
            continue
        key = (info["external_user_id"], info.get("external_sub_id") or "")
        known_by_key[key] = info

    if not known_by_key and not active_uid:
        return empty

    known_accounts: list[dict[str, Any]] = []
    active_account: dict[str, Any] | None = None
    for info in known_by_key.values():
        is_active = bool(active_uid and info["external_user_id"] == active_uid)
        item = {
            "external_user_id": info["external_user_id"],
            "external_sub_id": info.get("external_sub_id"),
            "nickname": info["nickname"],
            "username": info.get("username"),
            "masked_mobile": info.get("masked_mobile"),
            "display_label": info["display_label"],
            "is_active": is_active,
        }
        known_accounts.append(item)
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
        "provider": "vcat",
        "active_account": active_account,
        "known_accounts": known_accounts,
    }


def detect_local_promoter_state(
    provider: str = "vcat",
    data_home: Path | str | None = None,
) -> dict[str, Any]:
    """Safely detect local channel-promoter account identity without reading credentials."""
    clean_provider = (provider or "vcat").strip().lower() or "vcat"
    if clean_provider == "vcat":
        return _detect_vcat_state(data_home=data_home)
    return {"detected": False, "provider": clean_provider, "active_account": None, "known_accounts": []}


def find_promoter_account(db, *, provider: str, external_user_id: str, external_sub_id: str | None = None):
    """Locate an existing PromoterAccount by (provider, external_user_id, external_sub_id)."""
    from sqlalchemy import select
    from ..db import PromoterAccount

    stmt = select(PromoterAccount).where(
        PromoterAccount.provider == provider,
        PromoterAccount.external_user_id == external_user_id,
    )
    if external_sub_id is None:
        stmt = stmt.where(PromoterAccount.external_sub_id.is_(None))
    else:
        stmt = stmt.where(PromoterAccount.external_sub_id == external_sub_id)
    return db.scalar(stmt)


def apply_promoter_identity_to_node(db, node, identity: dict[str, Any] | None) -> dict[str, Any]:
    """Persist detected promoter identity on ExecutorNode and sync/migrate matching PromoterAccount."""
    from sqlalchemy import select
    from ..db import PlatformAccount, now

    if not isinstance(identity, dict) or not identity.get("detected"):
        return {
            "promoter_activated": False,
            "promoter_account_id": None,
            "promoter_account_label": node.active_promoter_label,
            "activation_url": f"/#ops-accounts?activate_node={node.id}" if node.active_promoter_user_id else None,
        }
    provider = str(identity.get("provider") or "vcat").strip().lower() or "vcat"
    active = identity.get("active_account") if isinstance(identity.get("active_account"), dict) else None
    known = identity.get("known_accounts") if isinstance(identity.get("known_accounts"), list) else []
    node.provider = provider
    sanitized_known: list[dict[str, Any]] = []
    for item in known:
        if not isinstance(item, dict):
            continue
        euid = str(item.get("external_user_id") or "").strip()
        if not euid:
            continue
        esub = str(item.get("external_sub_id")).strip() if item.get("external_sub_id") not in (None, "") else None
        mm = mask_mobile_text(item.get("masked_mobile"))
        nick = str(item.get("nickname") or "").strip()
        uname = str(item.get("username") or "").strip() or None
        lbl = str(item.get("display_label") or "").strip() or build_promoter_display_label(
            nickname=nick, username=uname, masked_mobile=mm, external_user_id=euid
        )
        sanitized_known.append(
            {
                "external_user_id": euid,
                "external_sub_id": esub,
                "nickname": nick or lbl,
                "username": uname,
                "masked_mobile": mm,
                "display_label": lbl,
                "is_active": bool(item.get("is_active")),
            }
        )
    node.detected_promoter_accounts_json = sanitized_known

    if not active or not str(active.get("external_user_id") or "").strip():
        node.active_promoter_user_id = None
        node.active_promoter_sub_id = None
        node.active_promoter_label = None
        return {
            "promoter_activated": False,
            "promoter_account_id": None,
            "promoter_account_label": None,
            "activation_url": None,
        }

    ext_uid = str(active.get("external_user_id")).strip()
    ext_sub = str(active.get("external_sub_id")).strip() if active.get("external_sub_id") not in (None, "") else None
    masked_mobile = mask_mobile_text(active.get("masked_mobile"))
    nickname = str(active.get("nickname") or "").strip()
    username = str(active.get("username") or "").strip() or None
    display_label = str(active.get("display_label") or "").strip() or build_promoter_display_label(
        nickname=nickname, username=username, masked_mobile=masked_mobile, external_user_id=ext_uid
    )
    node.active_promoter_user_id = ext_uid
    node.active_promoter_sub_id = ext_sub
    node.active_promoter_label = display_label

    promoter = find_promoter_account(db, provider=provider, external_user_id=ext_uid, external_sub_id=ext_sub)
    if promoter and promoter.status == "active":
        if nickname:
            promoter.nickname = nickname
        if username is not None:
            promoter.username = username
        if masked_mobile and not promoter.masked_mobile:
            promoter.masked_mobile = masked_mobile
        if active.get("avatar"):
            promoter.avatar = str(active.get("avatar"))
        if isinstance(active.get("write_modules"), dict):
            promoter.capabilities_json = {str(k): bool(v) for k, v in active["write_modules"].items()}
        if promoter.node_id != node.id:
            promoter.node_id = node.id
            for acc in db.scalars(select(PlatformAccount).where(PlatformAccount.promoter_account_id == promoter.id)):
                acc.node_id = node.id
        promoter.last_seen_at = now()
        promoter.updated_at = now()
        resolved_label = build_promoter_display_label(
            remark=promoter.remark,
            nickname=promoter.nickname,
            username=promoter.username,
            masked_mobile=promoter.masked_mobile,
            external_user_id=promoter.external_user_id,
        )
        return {
            "promoter_activated": True,
            "promoter_account_id": promoter.id,
            "promoter_account_label": resolved_label,
            "activation_url": None,
        }

    return {
        "promoter_activated": False,
        "promoter_account_id": promoter.id if promoter else None,
        "promoter_account_label": display_label,
        "activation_url": f"/#ops-accounts?activate_node={node.id}",
    }
