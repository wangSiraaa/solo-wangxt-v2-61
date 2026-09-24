"""船舶申报服务（幂等创建）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.timeutil import now_iso, to_minute
from app.models.tables import Declaration, Ship
from app.schemas import DeclarationIn


class DeclError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def create_declaration(db: Session, payload: DeclarationIn) -> Declaration:
    if payload.idempotency_key:
        existing = db.scalars(
            select(Declaration).where(
                Declaration.idempotency_key == payload.idempotency_key
            )
        ).first()
        if existing is not None:
            return existing  # 幂等：相同键直接返回既有申报

    ship = db.get(Ship, payload.ship_id)
    if ship is None:
        raise DeclError("ship_not_found", f"船舶 {payload.ship_id} 不存在", 404)

    minute = to_minute(payload.requested_start)
    if minute < 0 or minute > 48 * 60:
        raise DeclError(
            "outside_horizon", "申报时刻超出排班周期（基准日后 0–48 小时）", 422
        )

    decl = Declaration(
        idempotency_key=payload.idempotency_key,
        ship_id=payload.ship_id,
        direction=payload.direction,
        duration_minutes=payload.duration_minutes,
        requested_start_minute=minute,
        committed=payload.committed,
        status="pending",
        created_at=now_iso(),
    )
    db.add(decl)
    db.commit()
    db.refresh(decl)
    return decl
