"""FastAPI 入口：船舶申报排班、计划锁定、显式修订。

仅提供 JSON API，无前端。潮汐与船舶资料均为离线虚构数据。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .db import SessionLocal, boats, pilots, ships, tide_samples
from .schemas import (
    PlanCreate,
    PlanOut,
    RevisionIn,
    RevisionResultOut,
)
from .seed import init_db
from .services import ConflictError, PlanError, confirm_plan, create_plan, format_plan, revise_plan


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="引航站排班 API（离线虚构数据）",
    version="1.0.0",
    lifespan=lifespan,
    description="船舶申报 × 潮汐吃水窗口 × 引航员资质/工时 × 接送艇容量与转场时间 "
                "的统一排班接口。数据均为虚构，不用于真实航行决策。",
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok", "epoch": clock.to_iso(0),
            "horizon_minutes": clock.HORIZON_MINUTES}


@app.get("/reference")
def reference(db: Session = Depends(get_db)):
    """离线虚构基础数据：船舶 / 引航员 / 接送艇 / 潮位样本。"""
    return {
        "ships": [dict(r._mapping) for r in db.execute(select(ships)).all()],
        "pilots": [dict(r._mapping) for r in db.execute(select(pilots)).all()],
        "boats": [dict(r._mapping) for r in db.execute(select(boats)).all()],
        "tide_samples": [
            {"t_minute": r[0], "at": clock.to_iso(r[0]), "level_m": r[1]}
            for r in db.execute(
                select(tide_samples.c.t_minute, tide_samples.c.level_m).order_by(
                    tide_samples.c.t_minute)
            ).all()
        ],
    }


@app.post("/plans", response_model=PlanOut, status_code=201)
def submit_plan(body: PlanCreate, response: Response,
                idempotency_key: str = Header(..., alias="Idempotency-Key"),
                db: Session = Depends(get_db)):
    specs = [t.model_dump() for t in body.tasks]
    try:
        out, reused = create_plan(db, idempotency_key, specs, note=body.note)
    except PlanError as e:
        raise HTTPException(status_code=400, detail=str(e))
    out["reused"] = reused
    if reused:
        response.status_code = 200
    return out


@app.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    try:
        return format_plan(db, plan_id)
    except PlanError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/plans/{plan_id}/confirm", response_model=PlanOut)
def lock_plan(plan_id: int, db: Session = Depends(get_db)):
    """正式锁定。重复调用幂等：reused=true，不会新增第二个有效任务。"""
    try:
        out, reused = confirm_plan(db, plan_id)
    except PlanError as e:
        raise HTTPException(status_code=404, detail=str(e))
    out["reused"] = reused
    return out


@app.post("/plans/{plan_id}/revisions", response_model=RevisionResultOut)
def add_revision(plan_id: int, body: RevisionIn, db: Session = Depends(get_db)):
    """锁定后唯一允许的变更入口：cancel / add / reschedule 显式修订。"""
    payload = body.model_dump()
    try:
        return revise_plan(db, plan_id, payload)
    except PlanError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=e.result_out)
