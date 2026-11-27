"""Persistent storage. JSON documents retain explicit version fields for optimistic writes."""
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, event, String, Text, JSON, Integer, Boolean, Float, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).isoformat()


def business_today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def uid():
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String)
    team_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_ids: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[str | None] = mapped_column(String, nullable=True)


class LoginSession(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    expires_at: Mapped[str] = mapped_column(String)


class Record(Base):
    __tablename__ = 'records'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String, index=True)
    ip_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now)


class ExecutorNode(Base):
    __tablename__ = 'executor_nodes'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, default='vcat')
    node_scope: Mapped[str] = mapped_column(String, index=True)  # server_direct | server_relay | client_local
    owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    transport_mode: Mapped[str] = mapped_column(String)  # direct_http | agent_relay
    mcp_url: Mapped[str | None] = mapped_column(String, nullable=True)
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default='offline')  # online | offline | mcp_error | revoked
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    vcat_version: Mapped[str | None] = mapped_column(String, nullable=True)
    last_heartbeat_at: Mapped[str | None] = mapped_column(String, nullable=True)
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active_promoter_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active_promoter_sub_id: Mapped[str | None] = mapped_column(String, nullable=True)
    active_promoter_label: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_promoter_accounts_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class AgentPairCode(Base):
    __tablename__ = 'agent_pair_codes'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    code_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    node_scope: Mapped[str] = mapped_column(String)  # server_relay | client_local
    owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str] = mapped_column(String, default='')
    expires_at: Mapped[str] = mapped_column(String)
    used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class PromoterAccount(Base):
    __tablename__ = 'promoter_accounts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String, default='vcat', index=True)
    external_user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    external_sub_id: Mapped[str | None] = mapped_column(String, nullable=True)
    nickname: Mapped[str] = mapped_column(String, default='')
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    masked_mobile: Mapped[str | None] = mapped_column(String, nullable=True)
    remark: Mapped[str] = mapped_column(String, default='')
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_id: Mapped[str] = mapped_column(String, index=True)
    owner_scope: Mapped[str] = mapped_column(String, default='team_shared')  # team_shared | personal
    owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    identity_source: Mapped[str] = mapped_column(String, default='local_probe')  # local_probe | manual_fallback
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default='active')  # active | disabled
    activated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    activated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class PlatformAccount(Base):
    __tablename__ = 'platform_accounts'
    __table_args__ = (UniqueConstraint('promoter_account_id', 'vendor_uid', name='uq_platform_account_promoter_vendor'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    node_id: Mapped[str] = mapped_column(String, index=True)
    promoter_account_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    vendor_uid: Mapped[str] = mapped_column(String, index=True)
    platform: Mapped[str] = mapped_column(String, index=True)
    platform_name: Mapped[str] = mapped_column(String, default='')
    nickname: Mapped[str] = mapped_column(String, default='')
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, default='promoter_import')  # promoter_import | web_iframe
    group_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    remark: Mapped[str] = mapped_column(String, default='')
    fans_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ip_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    login_status: Mapped[str] = mapped_column(String, default='未知')
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    verify_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    bound_by: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    removed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class PublishTarget(Base):
    __tablename__ = 'publish_targets'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    schedule_id: Mapped[str] = mapped_column(String, index=True)
    ip_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    draft_id: Mapped[str] = mapped_column(String, index=True)
    draft_version: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[str] = mapped_column(String, index=True)
    platform: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String, default='video')  # video | pics | wechat_draft
    schedule_owner: Mapped[str] = mapped_column(String, default='server')  # server | platform
    approved_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str] = mapped_column(String)
    planned_at_utc: Mapped[str] = mapped_column(String, index=True)
    execution_mode: Mapped[str] = mapped_column(String, default='publish')  # draft | publish | manual
    status: Mapped[str] = mapped_column(String, default='QUEUED', index=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class PublishAttempt(Base):
    __tablename__ = 'publish_attempts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    target_id: Mapped[str] = mapped_column(String, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    state: Mapped[str] = mapped_column(String, default='QUEUED', index=True)
    node_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    lease_until: Mapped[str | None] = mapped_column(String, nullable=True)
    submit_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_local_id: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_draft_id: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_batch_name: Mapped[str | None] = mapped_column(String, nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_status: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_status: Mapped[str] = mapped_column(String, default='pending')
    verification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    verify_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class AgentCommand(Base):
    __tablename__ = 'agent_commands'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    action_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    node_id: Mapped[str] = mapped_column(String, index=True)
    tool_name: Mapped[str] = mapped_column(String)
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict)
    media_refs_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default='queued', index=True)
    lease_until: Mapped[str | None] = mapped_column(String, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AuthChallenge(Base):
    __tablename__ = 'auth_challenges'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    node_id: Mapped[str] = mapped_column(String, index=True)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String)
    challenge_type: Mapped[str] = mapped_column(String)  # new_account_iframe | channels_face_verify
    status: Mapped[str] = mapped_column(String, default='WAITING_SCAN', index=True)
    vendor_link_id: Mapped[str | None] = mapped_column(String, nullable=True)
    encrypted_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[str] = mapped_column(String)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class PublishedPost(Base):
    __tablename__ = 'published_posts'
    __table_args__ = (UniqueConstraint('platform', 'vendor_uid', 'vendor_post_id', name='uq_post_platform_uid_pid'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    platform: Mapped[str] = mapped_column(String, index=True)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ip_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    vendor_uid: Mapped[str] = mapped_column(String, index=True)
    vendor_post_id: Mapped[str] = mapped_column(String, index=True)
    draft_id: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, default='')
    desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str | None] = mapped_column(String, nullable=True)
    publish_status: Mapped[str] = mapped_column(String, default='published')
    post_time_utc: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[str] = mapped_column(String, default=now)
    created_at: Mapped[str] = mapped_column(String, default=now)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class MetricSnapshot(Base):
    __tablename__ = 'metric_snapshots'
    __table_args__ = (UniqueConstraint('subject_type', 'subject_id', 'observed_date', 'metric_type', name='uq_metric_subject_date_type'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    subject_type: Mapped[str] = mapped_column(String, index=True)  # account | post
    subject_id: Mapped[str] = mapped_column(String, index=True)
    ip_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    observed_date: Mapped[str] = mapped_column(String, index=True)
    metric_type: Mapped[str] = mapped_column(String, default='cumulative')
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forwards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorites: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fans_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fans_add: Mapped[int | None] = mapped_column(Integer, nullable=True)
    full_view_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_view_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, default=now, onupdate=now)


class VcatActionLog(Base):
    __tablename__ = 'vcat_action_logs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String)
    tool_name: Mapped[str] = mapped_column(String)
    request_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_status: Mapped[str] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=now)


def database(path):
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 30})
    @event.listens_for(engine, 'connect')
    def configure(dbapi, _):
        dbapi.execute('PRAGMA journal_mode=WAL')
        dbapi.execute('PRAGMA foreign_keys=ON')
        dbapi.execute('PRAGMA busy_timeout=30000')
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, column, col_type in (
            ('executor_nodes', 'revoked_at', 'TEXT'),
            ('executor_nodes', 'provider', "TEXT DEFAULT 'vcat'"),
            ('executor_nodes', 'active_promoter_user_id', 'TEXT'),
            ('executor_nodes', 'active_promoter_sub_id', 'TEXT'),
            ('executor_nodes', 'active_promoter_label', 'TEXT'),
            ('executor_nodes', 'detected_promoter_accounts_json', "JSON DEFAULT '[]'"),
            ('platform_accounts', 'removed_at', 'TEXT'),
            ('platform_accounts', 'promoter_account_id', 'TEXT'),
            ('platform_accounts', 'source', "TEXT DEFAULT 'promoter_import'"),
            ('platform_accounts', 'group_name', 'TEXT'),
            ('platform_accounts', 'tags_json', "JSON DEFAULT '[]'"),
            ('platform_accounts', 'remark', "TEXT DEFAULT ''"),
            ('platform_accounts', 'fans_total', 'INTEGER'),
            ('auth_challenges', 'vendor_link_id', 'TEXT'),
            ('publish_attempts', 'vendor_batch_name', 'TEXT'),
            ('publish_attempts', 'remote_status', 'TEXT'),
            ('publish_attempts', 'verification_status', "TEXT DEFAULT 'pending'"),
            ('publish_attempts', 'verification_detail', 'TEXT'),
            ('publish_attempts', 'last_verified_at', 'TEXT'),
            ('publish_attempts', 'verify_attempts', 'INTEGER DEFAULT 0'),
        ):
            cols = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info({table})').fetchall()}
            if column not in cols:
                conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
    return engine, sessionmaker(engine, expire_on_commit=False)


def as_record(row):
    return {**row.payload, 'id': row.id, 'ip_id': row.ip_id, 'created_at': row.created_at, 'updated_at': row.updated_at}
