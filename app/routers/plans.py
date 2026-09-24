from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.tables import Plan
from app.schemas import PlanBuildIn, PlanOut, RevisionIn
from app.serializers import plan_out
from app.services.plans import (
    PlanError,
    confirm_plan,
    confirm_revision,
    create_plan,
    revise_plan,
)

router = APIRouter()


def _error(exc: PlanError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": str(exc)},
    )


@router.post("/plans", response_model=PlanOut, status_code=201)
def post_plan(payload: PlanBuildIn, db: Session = Depends(get_session)):
    try:
        plan = create_plan(db, payload.declaration_ids, payload.boat_ids)
    except PlanError as exc:
        return _error(exc)
    return plan_out(db, plan)


@router.get("/plans", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_session)):
    rows = db.query(Plan).order_by(Plan.id).all()
    return [plan_out(db, p) for p in rows]


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_session)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        return JSONResponse(
            status_code=404,
            content={"error": "plan_not_found", "message": f"计划 {plan_id} 不存在"},
        )
    return plan_out(db, plan)


@router.post("/plans/{plan_id}/confirm", response_model=PlanOut)
def post_confirm(plan_id: int, db: Session = Depends(get_session)):
    """确认（锁定）计划。重复确认同一计划是幂等的。"""
    try:
        plan = confirm_plan(db, plan_id)
    except PlanError as exc:
        return _error(exc)
    return plan_out(db, plan)


@router.post("/plans/{plan_id}/revisions", response_model=PlanOut, status_code=201)
def post_revision(plan_id: int, payload: RevisionIn, db: Session = Depends(get_session)):
    """正式（已锁定）计划只允许通过本接口显式修订，生成新的草稿版本。"""
    try:
        child = revise_plan(db, plan_id, payload.declaration_ids, payload.boat_ids)
    except PlanError as exc:
        return _error(exc)
    return plan_out(db, child)


@router.post("/plans/{plan_id}/revisions/confirm", response_model=PlanOut)
def post_revision_confirm(plan_id: int, db: Session = Depends(get_session)):
    """确认修订草稿：新版本锁定，旧版本转为 superseded。"""
    try:
        plan = confirm_revision(db, plan_id)
    except PlanError as exc:
        return _error(exc)
    return plan_out(db, plan)
