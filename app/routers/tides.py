from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.domain.rules import draft_requirement
from app.domain.tides import sample_points, tide_windows
from app.models.tables import Ship
from app.schemas import TidePointOut, TideWindowOut
from app.serializers import tide_point_out, tide_window_out

router = APIRouter()


@router.get("/tides/points", response_model=list[TidePointOut])
def tide_points():
    return [tide_point_out(m, h) for m, h in sample_points(settings.horizon_minutes)]


@router.get("/tides/windows", response_model=list[TideWindowOut])
def windows_for_tide(required_cm: int | None = None, ship_id: str | None = None,
                     db: Session = Depends(get_session)):
    """按最低潮高（厘米）或船舶吃水规则返回虚构通航窗口。"""
    if required_cm is None:
        if ship_id is None:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "missing_param",
                    "message": "请提供 required_cm 或 ship_id",
                },
            )
        ship = db.get(Ship, ship_id)
        if ship is None:
            return JSONResponse(
                status_code=404,
                content={"error": "ship_not_found", "message": f"船舶 {ship_id} 不存在"},
            )
        _, required_cm = draft_requirement(ship.draft_meters)
    return [
        tide_window_out(w)
        for w in tide_windows(required_cm, settings.horizon_minutes)
    ]
