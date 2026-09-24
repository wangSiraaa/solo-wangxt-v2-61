import os

import pytest

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402

from app import seed as seed_module  # noqa: E402
from app.db import SessionLocal, create_all, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """每个测试一个全新的内存库并写入虚构种子数据。"""
    create_all(engine)
    seed_module.seed()
    yield
    from app.db import metadata
    metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
