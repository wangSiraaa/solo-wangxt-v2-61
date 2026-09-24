"""SQLAlchemy Core 表定义与引擎。

PostgreSQL（容器默认）与 SQLite（测试/本地）共用同一套声明；
JSON 列用可移植的 JSON 类型。
"""
from __future__ import annotations

import json

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings

metadata = MetaData()

ships = Table(
    "ships", metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(128), nullable=False),
    Column("vessel_type", String(32), nullable=False),
    Column("draft_m", Float, nullable=False),
    Column("length_m", Float),
)

pilots = Table(
    "pilots", metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(128), nullable=False),
    Column("grade", String(2), nullable=False),
    Column("work_windows", JSON, nullable=False),   # [[lo_min, hi_min], ...]
)

boats = Table(
    "boats", metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(128), nullable=False),
    Column("capacity", Integer, nullable=False, default=1),
    Column("work_windows", JSON, nullable=False),
)

tide_samples = Table(
    "tide_samples", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("station", String(32), nullable=False),
    Column("t_minute", Integer, nullable=False),
    Column("level_m", Float, nullable=False),
    UniqueConstraint("station", "t_minute", name="uq_tide_station_time"),
)

plan_requests = Table(
    "plan_requests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("idem_key", String(128), nullable=False, unique=True),
    Column("status", String(16), nullable=False, default="draft"),  # draft/locked
    Column("requested_at", DateTime, server_default=func.now()),
    Column("solution", JSON, nullable=False),
    Column("revision_no", Integer, nullable=False, default=0),
)

plan_tasks = Table(
    "plan_tasks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", Integer, ForeignKey("plan_requests.id"), nullable=False),
    Column("task_ref", String(64), nullable=False),
    Column("ship_id", String(32), nullable=False),
    Column("location", String(16), nullable=False),
    Column("start_minute", Integer),
    Column("duration", Integer, nullable=False),
    Column("earliest", Integer, nullable=False),
    Column("latest_start", Integer, nullable=False),
    Column("committed", Integer, nullable=False, default=0),
    Column("pilot_id", String(32)),
    Column("boat_id", String(64)),
    Column("state", String(16), nullable=False, default="provisional"),
    # provisional / committed / unserved / cancelled
    UniqueConstraint("request_id", "task_ref", name="uq_request_taskref"),
)

revisions = Table(
    "revisions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", Integer, ForeignKey("plan_requests.id"), nullable=False),
    Column("revision_no", Integer, nullable=False),
    Column("kind", String(16), nullable=False),       # cancel/add/reschedule
    Column("task_ref", String(64), nullable=False),
    Column("payload", JSON),
    Column("created_at", DateTime, server_default=func.now()),
)


def make_engine(url: str | None = None):
    url = url or settings.database_url
    connect_args = {}
    kwargs = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in url:
            # 让 TestClient 的多个连接共享同一个内存库
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, future=True, connect_args=connect_args,
                           json_serializer=lambda o: json.dumps(o, ensure_ascii=False),
                           **kwargs)
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)


def create_all(eng=None):
    metadata.create_all(eng or engine)


def get_session() -> Session:
    return SessionLocal()
