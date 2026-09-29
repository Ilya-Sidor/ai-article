"""Relational store: users, sessions, project access, jobs, LLM request log.

``AI_ARTICLE_DATABASE_URL`` selects the database: PostgreSQL in production
(``postgresql+psycopg://…``), SQLite for local development (default,
``data/app.db``). Project documents stay in the encrypted object store.
"""
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text, create_engine,
                        event)

from . import config

metadata = MetaData()


def utcnow():
    return datetime.now(timezone.utc)


users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(320), unique=True, nullable=False),
    Column("password_hash", String(300), nullable=False),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("totp_secret", Text),  # sealed with the master key
    Column("totp_enabled", Boolean, nullable=False, default=False),
    Column("totp_last_counter", Integer),
    Column("failed_logins", Integer, nullable=False, default=0),
    Column("locked_until", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("last_seen", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

memberships = Table(
    "memberships", metadata,
    Column("project_id", String(32), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(16), nullable=False, default="owner"),  # owner | editor | commenter (FR-0.6)
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

jobs = Table(
    "jobs", metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", String(32)),
    Column("label", String(200), nullable=False),
    Column("method", String(8), nullable=False),
    Column("path", String(500), nullable=False),
    Column("body", Text),
    Column("content_type", String(200)),
    Column("status", String(16), nullable=False, default="queued"),  # queued | running | done | error
    Column("status_code", Integer),
    Column("result", Text),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)

llm_requests = Table(
    "llm_requests", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("project_id", String(64), index=True),
    Column("purpose", String(80), nullable=False),
    Column("model", String(80), nullable=False),
    Column("chars", Integer, nullable=False),
)

invites = Table(
    "invites", metadata,  # one-time registration links issued by an administrator
    Column("token_hash", String(64), primary_key=True),
    Column("email", String(320)),  # optional: the link then works only for this address
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    Column("used_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
)

app_settings = Table(
    "app_settings", metadata,
    Column("key", String(100), primary_key=True),
    Column("value", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

_engine = None
_lock = threading.Lock()


def url():
    return os.environ.get("AI_ARTICLE_DATABASE_URL") or f"sqlite:///{config.DATA_DIR / 'app.db'}"


def engine():
    global _engine
    with _lock:
        if _engine is None:
            u = url()
            if u.startswith("sqlite"):
                config.DATA_DIR.mkdir(parents=True, exist_ok=True)
                _engine = create_engine(u, connect_args={"check_same_thread": False, "timeout": 30})

                @event.listens_for(_engine, "connect")
                def _pragmas(conn, _):
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA journal_mode=WAL")
            else:
                _engine = create_engine(u, pool_pre_ping=True)
            metadata.create_all(_engine)
        return _engine


def reset():
    """Drop the cached engine (tests switch databases)."""
    global _engine
    from . import settings
    settings.invalidate()
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
