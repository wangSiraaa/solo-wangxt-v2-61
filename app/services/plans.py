"""计划编排：读取申报/资源 → 调用 CP-SAT → 持久化任务与冲突。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.rules import draft_requirement
from app.domain.tides import tide_windows
from app.domain.timeutil import now_iso
from app.engine.scheduler import Job, PilotInfo, solve
from app.models.tables import (
    Boat,
    ConflictRow,
    Declaration,
    Pilot,
    Plan,
    PlanTask,
    Ship,
)


class PlanError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _load_jobs(
    db: Session, declaration_ids: list[int], allowed_decl_ids: set[int] | None = None
) -> list[Job]:
    allowed_decl_ids = allowed_decl_ids or set()
    jobs: list[Job] = []
    for decl_id in declaration_ids:
        decl = db.get(Declaration, decl_id)
        if decl is None:
            raise PlanError("declaration_not_found", f"申报 {decl_id} 不存在", 404)
        if decl.status == "scheduled" and decl_id not in allowed_decl_ids:
            raise PlanError(
                "declaration_already_scheduled",
                f"申报 {decl_id} 已被锁定计划占用；请使用显式修订",
                409,
            )
        ship = db.get(Ship, decl.ship_id)
        grade, tide_cm = draft_requirement(ship.draft_meters)
        jobs.append(
            Job(
                declaration_id=decl.id,
                required_grade=grade,
                required_tide_cm=tide_cm,
                direction=decl.direction,
                duration=decl.duration_minutes,
                requested_start=decl.requested_start_minute,
                committed=decl.committed,
            )
        )
    return jobs


def _load_pilots(db: Session) -> list[PilotInfo]:
    pilots: list[PilotInfo] = []
    for pilot in db.scalars(select(Pilot).where(Pilot.active.is_(True))).all():
        windows = tuple(
            sorted(
                (w.start_minute, w.end_minute)
                for w in pilot.windows
            )
        )
        pilots.append(PilotInfo(id=pilot.id, grade=pilot.grade, windows=windows))
    pilots.sort(key=lambda p: p.id)
    return pilots


def _resolve_boats(db: Session, boat_ids: list[str] | None) -> tuple[list[str], int]:
    if boat_ids is None:
        boats = db.scalars(select(Boat).where(Boat.active.is_(True))).all()
        ids = sorted(b.id for b in boats)
    else:
        ids = sorted(set(boat_ids))
        for bid in ids:
            boat = db.get(Boat, bid)
            if boat is None or not boat.active:
                raise PlanError("boat_not_found", f"接送艇 {bid} 不可用", 404)
    if not ids:
        raise PlanError("no_boat", "没有可用接送艇", 400)
    return ids, len(ids)


def _tide_map(jobs: list[Job]) -> dict[int, list[tuple[int, int]]]:
    reqs = {j.required_tide_cm for j in jobs}
    return {req: tide_windows(req, settings.horizon_minutes) for req in reqs}


def create_plan(
    db: Session, declaration_ids: list[int], boat_ids: list[str] | None
) -> Plan:
    ids = sorted(set(declaration_ids))
    if len(ids) != len(declaration_ids):
        raise PlanError("duplicate_declaration", "申报列表存在重复", 422)

    jobs = _load_jobs(db, ids)
    pilots = _load_pilots(db)
    boat_id_list, capacity = _resolve_boats(db, boat_ids)
    result = solve(jobs, pilots, _tide_map(jobs), capacity)

    plan = Plan(
        status="draft",
        boat_capacity=capacity,
        boat_ids_json=boat_id_list,
        created_at=now_iso(),
    )
    db.add(plan)
    db.flush()
    _persist_result(db, plan, result)
    db.commit()
    db.refresh(plan)
    return plan


def _persist_result(db: Session, plan: Plan, result) -> None:
    for t in result.tasks:
        db.add(
            PlanTask(
                plan_id=plan.id,
                declaration_id=t.declaration_id,
                pilot_id=t.pilot_id,
                start_minute=t.start,
                end_minute=t.end,
                embark_start_minute=t.embark_start,
                disembark_end_minute=t.disembark_end,
            )
        )
    for c in result.conflicts:
        db.add(
            ConflictRow(
                plan_id=plan.id,
                declaration_id=c.declaration_id,
                reason=c.reason,
                resource_kind=c.resource_kind,
                resource_id=c.resource_id,
                blocking_declaration_id=c.blocking_declaration_id,
                window_start_minute=c.window_start,
                window_end_minute=c.window_end,
                detail=c.detail,
            )
        )


def confirm_plan(db: Session, plan_id: int) -> Plan:
    """确认计划是幂等的：重复确认不会生成第二个有效任务。"""
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise PlanError("plan_not_found", f"计划 {plan_id} 不存在", 404)
    if plan.status == "superseded":
        raise PlanError("plan_superseded", "该计划已被修订版本取代", 409)
    if plan.status == "locked":
        return plan  # 幂等：已锁定直接返回

    decl_ids = [t.declaration_id for t in plan.tasks]
    # 最终防线：确认时再查是否已有其他有效（active）任务占用同一申报
    parent: Plan | None = None
    parent_task_ids: set[int] = set()
    if plan.revision_of_id is not None:
        parent = db.get(Plan, plan.revision_of_id)
        if parent is not None:
            # 修订确认：先解除旧版任务的 active，再在同一事务中锁定新版
            for t in parent.tasks:
                t.active = False
            parent_task_ids = {t.id for t in parent.tasks}
    occupied_q = select(PlanTask.declaration_id).where(
        PlanTask.active.is_(True), PlanTask.declaration_id.in_(decl_ids or [-1])
    )
    if parent_task_ids:
        occupied_q = occupied_q.where(PlanTask.id.notin_(parent_task_ids))
    occupied = db.scalars(occupied_q).all()
    if occupied:
        # 恢复旧版任务的 active（不整体回滚，保持旧计划锁定状态）
        if parent is not None:
            for t in parent.tasks:
                t.active = True
        raise PlanError(
            "declaration_already_scheduled",
            f"申报 {sorted(occupied)} 已被其他锁定计划占用",
            409,
        )

    plan.status = "locked"
    plan.locked_at = now_iso()
    for t in plan.tasks:
        t.active = True
        decl = db.get(Declaration, t.declaration_id)
        decl.status = "scheduled"
    for c in plan.conflicts:
        decl = db.get(Declaration, c.declaration_id)
        if decl.status == "pending":
            decl.status = "conflict"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise PlanError(
            "declaration_already_scheduled",
            "并发确认冲突：该申报已有其他有效任务",
            409,
        )
    db.refresh(plan)
    return plan


def revise_plan(
    db,
    plan_id: int,
    declaration_ids: list[int] | None,
    boat_ids: list[str] | None,
) -> Plan:
    """正式（locked）计划只允许通过显式修订产生新版本。"""
    parent = db.get(Plan, plan_id)
    if parent is None:
        raise PlanError("plan_not_found", f"计划 {plan_id} 不存在", 404)
    if parent.status != "locked":
        raise PlanError(
            "plan_not_locked",
            "只有已锁定的正式计划可以修订；草稿可直接重建",
            409,
        )

    if declaration_ids is None:
        # 沿用原计划纳入的申报（含冲突申报）
        ids = sorted(
            {t.declaration_id for t in parent.tasks}
            | {c.declaration_id for c in parent.conflicts}
        )
    else:
        ids = sorted(set(declaration_ids))

    allowed = {t.declaration_id for t in parent.tasks}
    jobs = _load_jobs(db, ids, allowed_decl_ids=allowed)
    pilots = _load_pilots(db)
    boat_id_list = boat_ids if boat_ids is not None else list(parent.boat_ids_json)
    boat_id_list, capacity = _resolve_boats(db, boat_id_list)
    result = solve(jobs, pilots, _tide_map(jobs), capacity)

    child = Plan(
        status="draft",
        revision_of_id=parent.id,
        boat_capacity=capacity,
        boat_ids_json=boat_id_list,
        created_at=now_iso(),
    )
    db.add(child)
    db.flush()
    _persist_result(db, child, result)
    db.commit()
    db.refresh(child)
    return child


def confirm_revision(db: Session, plan_id: int) -> Plan:
    """确认修订版：新版本锁定，旧版本标记 superseded 并释放其申报状态。"""
    plan = confirm_plan(db, plan_id)
    if plan.revision_of_id is None:
        return plan

    parent = db.get(Plan, plan.revision_of_id)
    if parent is not None:
        # confirm_plan 已先把旧版任务的 active 置 False
        if parent.status == "locked":
            parent.status = "superseded"
            parent.superseded_by_id = plan.id
            old_decl_ids = {t.declaration_id for t in parent.tasks}
            new_decl_ids = {t.declaration_id for t in plan.tasks}
            # 旧版有而新版没有的任务：申报退回 pending
            for decl_id in old_decl_ids - new_decl_ids:
                decl = db.get(Declaration, decl_id)
                if decl is not None:
                    decl.status = "pending"
            db.commit()
            db.refresh(plan)
    return plan
