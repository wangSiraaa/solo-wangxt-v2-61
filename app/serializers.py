"""ORM 对象 → API schema 的序列化。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.rules import draft_requirement
from app.domain.timeutil import to_iso
from app.models.tables import (
    Boat,
    ConflictRow,
    Declaration,
    Pilot,
    Plan,
    PlanTask,
    Ship,
)
from app.schemas import (
    BoatOut,
    ConflictOut,
    DeclarationOut,
    PilotOut,
    PlanOut,
    ShipOut,
    TaskOut,
    TidePointOut,
    TideWindowOut,
    WorkWindowOut,
)


def ship_out(s: Ship) -> ShipOut:
    grade, tide = draft_requirement(s.draft_meters)
    return ShipOut(
        id=s.id,
        name=s.name,
        draft_meters=s.draft_meters,
        required_grade=grade,
        required_tide_cm=tide,
    )


def pilot_out(p: Pilot) -> PilotOut:
    return PilotOut(
        id=p.id,
        name=p.name,
        grade=p.grade,
        active=p.active,
        work_windows=[
            WorkWindowOut(
                start=to_iso(w.start_minute),
                end=to_iso(w.end_minute),
                start_minute=w.start_minute,
                end_minute=w.end_minute,
            )
            for w in sorted(p.windows, key=lambda w: w.start_minute)
        ],
    )


def boat_out(b: Boat) -> BoatOut:
    return BoatOut(id=b.id, name=b.name, active=b.active)


def declaration_out(d: Declaration) -> DeclarationOut:
    return DeclarationOut(
        id=d.id,
        idempotency_key=d.idempotency_key,
        ship_id=d.ship_id,
        direction=d.direction,
        duration_minutes=d.duration_minutes,
        requested_start=to_iso(d.requested_start_minute),
        requested_start_minute=d.requested_start_minute,
        committed=d.committed,
        status=d.status,
    )


def tide_window_out(w: tuple[int, int]) -> TideWindowOut:
    return TideWindowOut(
        start=to_iso(w[0]),
        end=to_iso(w[1]),
        start_minute=w[0],
        end_minute=w[1],
    )


def tide_point_out(minute: int, height: float) -> TidePointOut:
    return TidePointOut(minute=minute, time=to_iso(minute), height_cm=round(height, 2))


def task_out(db: Session, t: PlanTask, requested: int) -> TaskOut:
    return TaskOut(
        declaration_id=t.declaration_id,
        ship_id=db.get(Declaration, t.declaration_id).ship_id,
        pilot_id=t.pilot_id,
        start=to_iso(t.start_minute),
        end=to_iso(t.end_minute),
        start_minute=t.start_minute,
        end_minute=t.end_minute,
        embark_start=to_iso(t.embark_start_minute),
        embark_start_minute=t.embark_start_minute,
        disembark_end=to_iso(t.disembark_end_minute),
        disembark_end_minute=t.disembark_end_minute,
        delay_minutes=max(0, t.start_minute - requested),
    )


def conflict_out(c: ConflictRow) -> ConflictOut:
    return ConflictOut(
        declaration_id=c.declaration_id,
        reason=c.reason,
        resource_kind=c.resource_kind,
        resource_id=c.resource_id,
        blocking_declaration_id=c.blocking_declaration_id,
        window_start=to_iso(c.window_start_minute) if c.window_start_minute is not None else None,
        window_end=to_iso(c.window_end_minute) if c.window_end_minute is not None else None,
        window_start_minute=c.window_start_minute,
        window_end_minute=c.window_end_minute,
        detail=c.detail,
    )


def plan_out(db: Session, p: Plan) -> PlanOut:
    requested = {
        t.declaration_id: db.get(Declaration, t.declaration_id).requested_start_minute
        for t in p.tasks
    }
    return PlanOut(
        id=p.id,
        status=p.status,
        revision_of_id=p.revision_of_id,
        superseded_by_id=p.superseded_by_id,
        boat_capacity=p.boat_capacity,
        boat_ids=list(p.boat_ids_json or []),
        created_at=p.created_at,
        locked_at=p.locked_at,
        tasks=[task_out(db, t, requested[t.declaration_id]) for t in
               sorted(p.tasks, key=lambda t: (t.start_minute, t.declaration_id))],
        conflicts=[conflict_out(c) for c in sorted(p.conflicts, key=lambda c: c.declaration_id)],
    )
