from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import SessionLocal, engine
from app.models.tables import Base
from app.routers import plans, resources, tides
from app.seed import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 始终通过 db 模块当前绑定的 engine/SessionLocal 建表与播种，
    # 这样测试中替换内存库后启动事件仍作用于正确的数据库
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="引航站排班 API（离线虚构数据）",
    description=(
        "船舶申报、潮汐通航窗口、引航员资质/工作时段与接送艇容量的统一排班接口。"
        "潮汐及船舶资料均为离线虚构数据，不提供真实航行决策。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(resources.router, tags=["资源与申报"])
app.include_router(tides.router, tags=["潮汐"])
app.include_router(plans.router, tags=["计划"])


@app.get("/", tags=["元信息"])
def root():
    return {
        "service": "pilot-station-scheduling",
        "notice": "离线虚构数据，不用于真实航行决策",
        "docs": "/docs",
    }
