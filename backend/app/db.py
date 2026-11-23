"""Persistent storage. JSON documents retain explicit version fields for optimistic writes."""
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, event, String, Text, JSON, Integer, Boolean
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


def database(path):
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 30})
    @event.listens_for(engine, 'connect')
    def configure(dbapi, _):
        dbapi.execute('PRAGMA journal_mode=WAL')
        dbapi.execute('PRAGMA foreign_keys=ON')
        dbapi.execute('PRAGMA busy_timeout=30000')
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def as_record(row):
    return {**row.payload, 'id': row.id, 'ip_id': row.ip_id, 'created_at': row.created_at, 'updated_at': row.updated_at}
