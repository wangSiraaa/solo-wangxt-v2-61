"""测试夹具：内存 SQLite + 确定性 CP-SAT 参数，每个用例重置基础数据。

所有 app.* 模块都在 fixture 内部、环境变量设定之后才首次导入，
保证配置（基准日、接送时间、求解种子等）对测试完全确定。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def client(monkeypatch):
    # 先固定环境，再首次导入应用模块
    monkeypatch.setenv("PLAN_BASE_DATE", "2026-09-24")
    monkeypatch.setenv("PLAN_HORIZON_MINUTES", str(48 * 60))
    monkeypatch.setenv("PILOT_DISEMBARK_MINUTES", "25")
    monkeypatch.setenv("PILOT_EMBARK_MINUTES", "20")
    monkeypatch.setenv("PILOT_TRANSFER_MINUTES", "10")
    monkeypatch.setenv("TIDE_SAMPLE_MINUTES", "10")
    monkeypatch.setenv("SOLVER_SEED", "42")
    monkeypatch.setenv("SOLVER_TIME_LIMIT_SECONDS", "15")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # 每个用例一个独立内存库；StaticPool 保证同一连接被复用
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    import app.db as db_module

    db_module.engine = engine
    db_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

    from app.models.tables import Base
    from app.seed import seed

    Base.metadata.create_all(engine)
    session = db_module.SessionLocal()
    seed(session)
    session.close()

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session

    def _override():
        db = db_module.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
