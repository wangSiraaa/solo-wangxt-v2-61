"""排班服务层：申报数据 → 求解 Bundle → 持久化 / 锁定 / 显式修订。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, tides
from .conflicts import JobConflict, ResourceConflict, explain
from .db import boats, plan_requests, plan_tasks, pilots, revisions, ships, tide_samples
from .domain import (
    BASE,
    BOAT_SERVICE_MINUTES,
    DISEMBARK_MINUTES,
    LOCATIONS,
    grade_satisfies,
    required_grade,
    travel_minutes,
)
from .model import (
    Assignment,
    BundleInput,
    JobInput,
    ResourceInput,
    SolveResult,
    solve,
)


class PlanError(ValueError):
    """400 类输入错误。"""


class ConflictError(Exception):
    """409：显式修订导致承诺任务无法落实。"""

    def __init__(self, result_out: dict):
        self.result_out = result_out


# ---------------------------------------------------------------------------

def _grid(session: Session) -> dict[int, float]:
    rows = session.execute(
        select(tide_samples.c.t_minute, tide_samples.c.level_m)
    ).all()
    return {int(r[0]): float(r[1]) for r in rows}


def _boat_units(session: Session) -> tuple[list[ResourceInput], dict[str, tuple[int, int]]]:
    out, meta = [], {}
    for b in session.execute(select(boats)).mappings():
        for i in range(b["capacity"]):
            uid = f"{b['id']}#{i}"
            out.append(ResourceInput(id=uid, windows=[tuple(w) for w in b["work_windows"]]))
            meta[uid] = (b["id"], i)
    return out, meta


def _pilot_resources(session: Session) -> list[ResourceInput]:
    return [
        ResourceInput(id=p["id"], windows=[tuple(w) for w in p["work_windows"]])
        for p in session.execute(select(pilots)).mappings()
    ]


def _covers(work_windows, tide_windows, location, duration, kind) -> bool:
    """资源是否能在某潮窗候选点 + 某工时窗内完整覆盖该任务。"""
    tail = duration + DISEMBARK_MINUTES if kind == "p" else BOAT_SERVICE_MINUTES
    for a, b in tide_windows:
        for s in range(a, b + 1, clock.GRID_MINUTES):
            for wlo, whi in work_windows:
                if (s >= wlo + travel_minutes(BASE, location)
                        and s + tail <= whi):
                    return True
    return False


def _new_job(session, grid, spec, pilot_res, boat_res) -> JobInput:
    ship = session.execute(
        select(ships).where(ships.c.id == spec["ship_id"])
    ).mappings().first()
    if ship is None:
        raise PlanError(f"未知船舶: {spec['ship_id']}")
    location = spec["location"]
    if location not in LOCATIONS or location == "STATION":
        raise PlanError(f"非法作业地点: {location}")
    duration = clock.snap_up(int(spec["duration_minutes"]))
    earliest = clock.snap_down(clock.to_minutes(spec["earliest_start"]))
    latest_start = clock.snap_down(clock.to_minutes(spec["latest_start"]))
    if earliest > latest_start:
        raise PlanError(f"任务 {spec['task_ref']} 期望时间上下界颠倒")

    windows = tides.feasible_start_windows(
        grid, float(ship["draft_m"]), duration,
        earliest=earliest, latest_start=latest_start,
    )
    need = required_grade(float(ship["draft_m"]), ship["length_m"])

    elig_p = [
        r.id for r in pilot_res
        if _pilot_grade_ok(session, r.id, need)
        and _covers(r.windows, windows, location, duration, "p")
    ]
    elig_b = [r.id for r in boat_res
              if _covers(r.windows, windows, location, duration, "b")]

    return JobInput(
        ref=spec["task_ref"], location=location, duration=duration,
        earliest=earliest, latest_start=latest_start, tide_windows=windows,
        eligible_pilots=elig_p, eligible_boat_units=elig_b,
        forced=bool(spec.get("committed")),
    )


def _pilot_grade_ok(session, pid, need_grade) -> bool:
    row = session.execute(select(pilots.c.grade).where(pilots.c.id == pid)).first()
    return row is not None and grade_satisfies(row[0], need_grade)


# ---------------------------------------------------------------------------

def _fixed_jobs(session: Session, *, exclude_request=None) -> list[JobInput]:
    """其它已锁定计划中已承诺落实的任务 → 本请求必须避让的固定障碍。"""
    q = (
        select(plan_tasks, plan_requests.c.status)
        .join(plan_requests, plan_tasks.c.request_id == plan_requests.c.id)
        .where(plan_tasks.c.state == "committed",
               plan_tasks.c.start_minute.is_not(None))
    )
    if exclude_request is not None:
        q = q.where(plan_tasks.c.request_id != exclude_request)
    jobs = []
    for row in session.execute(q).mappings():
        jobs.append(JobInput(
            ref=f"LOCK-{row['request_id']}-{row['task_ref']}",
            location=row["location"], duration=row["duration"],
            earliest=0, latest_start=clock.HORIZON_MINUTES, tide_windows=[],
            forced=True, fixed_pilot=row["pilot_id"],
            fixed_boat=row["boat_id"], fixed_start=row["start_minute"],
        ))
    return jobs


def _request_job_rows(session: Session, request_id: int):
    return session.execute(
        select(plan_tasks).where(plan_tasks.c.request_id == request_id)
    ).mappings().all()


def _build_and_solve(session: Session, new_specs: list[dict], *,
                     exclude_request=None):
    grid = _grid(session)
    pilot_res = _pilot_resources(session)
    boat_res, _ = _boat_units(session)

    jobs = _fixed_jobs(session, exclude_request=exclude_request)
    new_jobs = [_new_job(session, grid, s, pilot_res, boat_res) for s in new_specs]
    jobs.extend(new_jobs)

    bundle = BundleInput(jobs=jobs, pilots=pilot_res, boat_units=boat_res,
                         horizon=clock.HORIZON_MINUTES)
    result = solve(bundle)
    return result, jobs, new_jobs, pilot_res, boat_res


# ---------------------------------------------------------------------------

def _explain_unserved(session, result, all_jobs, new_jobs, pilot_res, boat_res):
    """all_jobs 包含其它请求的已锁定固定任务；new_jobs 是本次申报任务。"""
    new_by_ref = {j.ref: j for j in new_jobs}
    p_sched: dict[str, list] = {}
    b_sched: dict[str, list] = {}
    job_by_ref = {j.ref: j for j in all_jobs}
    for a in result.assignments:
        if a.ref not in job_by_ref:
            continue
        j = job_by_ref[a.ref]
        p_sched.setdefault(a.pilot_id, []).append(
            (a.ref, j.location, a.start, j.duration, a.ref.startswith("LOCK-")))
        b_sched.setdefault(a.boat_unit_id, []).append(
            (a.ref, j.location, a.start, BOAT_SERVICE_MINUTES,
             a.ref.startswith("LOCK-")))

    p_windows = {r.id: r.windows for r in pilot_res}
    b_windows = {r.id: r.windows for r in boat_res}

    out = []
    for j in new_jobs:
        if j.ref not in result.unserved:
            continue
        c = explain(
            j.ref, j.tide_windows, j.eligible_pilots, j.eligible_boat_units,
            p_sched, b_sched, p_windows, b_windows,
            j.location, j.duration, clock.HORIZON_MINUTES,
        )
        out.append((j, c))
    return out


def _conflict_to_json(c: JobConflict) -> dict:
    def rc(r: ResourceConflict):
        return {
            "resource_id": r.resource_id,
            "blocked_windows": [
                {"start": b.start, "end": b.end, "blocking_task_ref": b.reason_ref}
                for b in r.blocked_windows
            ],
        }
    return {
        "task_ref": c.ref,
        "tide_windows": [list(w) for w in c.tide_windows],
        "pilots": [rc(p) for p in c.pilots],
        "boat_units": [rc(b) for b in c.boat_units],
        "note": c.note,
    }


def create_plan(session: Session, idem_key: str, specs: list[dict], note=None) -> tuple[dict, bool]:
    existing = session.execute(
        select(plan_requests).where(plan_requests.c.idem_key == idem_key)
    ).mappings().first()
    if existing is not None:
        return format_plan(session, existing["id"]), True

    if not specs:
        raise PlanError("tasks 不能为空")
    refs = [s["task_ref"] for s in specs]
    if len(set(refs)) != len(refs):
        raise PlanError("task_ref 在请求内重复")

    result, all_jobs, new_jobs, pilot_res, boat_res = _build_and_solve(session, specs)
    explained = _explain_unserved(session, result, all_jobs, new_jobs,
                                  pilot_res, boat_res)

    locked_refs = {j.ref for j in all_jobs if j.ref.startswith("LOCK-")}
    sol = _solution_json(result, all_jobs, explained, note,
                         exclude_refs=locked_refs)
    res = session.execute(plan_requests.insert().values(
        idem_key=idem_key, status="draft", solution=sol, revision_no=0))
    request_id = res.inserted_primary_key[0]
    for spec in specs:
        j = next(x for x in new_jobs if x.ref == spec["task_ref"])
        a = by_ref_get(result, j.ref)
        session.execute(plan_tasks.insert().values(
            request_id=request_id, task_ref=j.ref, ship_id=spec["ship_id"],
            location=j.location, start_minute=a.start if a else None,
            duration=j.duration, earliest=j.earliest,
            latest_start=j.latest_start, committed=1 if j.forced else 0,
            pilot_id=a.pilot_id if a else None,
            boat_id=a.boat_unit_id if a else None,
            state="provisional" if a else "unserved",
        ))
    session.commit()
    return format_plan(session, request_id), False


def by_ref_get(result: SolveResult, ref: str) -> Assignment | None:
    return next((a for a in result.assignments if a.ref == ref), None)


def _solution_json(result, all_jobs, explained, note, exclude_refs=None) -> dict:
    exclude_refs = exclude_refs or set()
    return {
        "total_delay": result.total_delay,
        "relaxed": result.relaxed,
        "note": note,
        "assignments": [
            {
                "task_ref": a.ref, "start": a.start, "delay": a.delay,
                "pilot_id": a.pilot_id, "boat_unit_id": a.boat_unit_id,
                "committed": a.forced,
            }
            for a in result.assignments if a.ref not in exclude_refs
        ],
        "unserved": [
            {
                "task_ref": j.ref, "committed": j.forced,
                "conflict": _conflict_to_json(c),
            }
            for j, c in explained
        ],
        "structurally_infeasible": result.structurally_infeasible,
    }


# ---------------------------------------------------------------------------

def confirm_plan(session: Session, request_id: int) -> tuple[dict, bool]:
    row = session.execute(
        select(plan_requests).where(plan_requests.c.id == request_id)
    ).mappings().first()
    if row is None:
        raise PlanError("计划不存在")
    if row["status"] == "locked":
        return format_plan(session, request_id), True

    session.execute(
        plan_requests.update().where(plan_requests.c.id == request_id)
        .values(status="locked")
    )
    # 只有落实了的任务转为正式承诺；未落实的保持 unserved
    session.execute(
        plan_tasks.update()
        .where(plan_tasks.c.request_id == request_id,
               plan_tasks.c.start_minute.is_not(None))
        .values(state="committed")
    )
    session.commit()
    return format_plan(session, request_id), False


# ---------------------------------------------------------------------------

def revise_plan(session: Session, request_id: int, body: dict) -> dict:
    row = session.execute(
        select(plan_requests).where(plan_requests.c.id == request_id)
    ).mappings().first()
    if row is None:
        raise PlanError("计划不存在")
    if row["status"] != "locked":
        raise PlanError("计划尚未锁定：草稿请重新提交，锁定后才可显式修订")

    kind, ref = body["kind"], body["task_ref"]
    existing = session.execute(
        select(plan_tasks)
        .where(plan_tasks.c.request_id == request_id,
               plan_tasks.c.task_ref == ref)
    ).mappings().first()

    if kind == "cancel":
        if existing is None or existing["state"] == "cancelled":
            raise PlanError(f"任务 {ref} 不存在或已取消")
    elif kind == "add":
        if existing is not None and existing["state"] != "cancelled":
            raise PlanError(f"任务 {ref} 已存在；reschedule 请用 reschedule")
        if not body.get("task"):
            raise PlanError("add 修订需要 task")
    elif kind == "reschedule":
        if existing is None or existing["state"] == "cancelled":
            raise PlanError(f"任务 {ref} 不存在或已取消，无法 reschedule")
        if not body.get("task"):
            raise PlanError("reschedule 修订需要 task")
    else:  # pragma: no cover
        raise PlanError(f"未知修订类型 {kind}")

    # 以当前活动任务快照构建重解输入（其它请求的锁定任务在 _fixed_jobs 内加入）
    current = [r for r in _request_job_rows(session, request_id)
               if r["state"] != "cancelled" and r["task_ref"] != ref]
    fixed, specs = [], []
    for r in current:
        if r["state"] == "committed" and r["start_minute"] is not None:
            fixed.append(JobInput(
                ref=f"LOCK-{request_id}-{r['task_ref']}",
                location=r["location"], duration=r["duration"],
                earliest=0, latest_start=clock.HORIZON_MINUTES, tide_windows=[],
                forced=True, fixed_pilot=r["pilot_id"], fixed_boat=r["boat_id"],
                fixed_start=r["start_minute"],
            ))
        else:
            specs.append(_row_spec(r))

    pending_row = None
    if kind == "add":
        specs.append(body["task"] | {"task_ref": ref})
    elif kind == "reschedule":
        specs.append(body["task"] | {"task_ref": ref})
    elif kind == "cancel":
        pass

    grid = _grid(session)
    pilot_res = _pilot_resources(session)
    boat_res, _ = _boat_units(session)
    extra_fixed = _fixed_jobs(session, exclude_request=request_id)
    new_jobs = [_new_job(session, grid, s, pilot_res, boat_res) for s in specs]
    all_jobs = fixed + extra_fixed + new_jobs
    bundle = BundleInput(jobs=all_jobs, pilots=pilot_res,
                         boat_units=boat_res, horizon=clock.HORIZON_MINUTES)
    result = solve(bundle)

    if result.unserved_forced:
        explained = _explain_unserved(session, result, all_jobs, new_jobs,
                                      pilot_res, boat_res)
        out = _revision_output(session, request_id, row["revision_no"], kind, ref,
                               applied=False,
                               conflicts=[
                                   {
                                       "task_ref": j.ref,
                                       "committed": j.forced,
                                       "conflict": _format_conflict(
                                           _conflict_to_json(c)),
                                   }
                                   for j, c in explained
                               ],
                               note="修订被拒绝：存在已承诺任务无法落实")
        raise ConflictError(out)

    # ---- 落库（原子提交）--------------------------------------------------
    next_no = row["revision_no"] + 1
    if kind == "cancel":
        session.execute(
            plan_tasks.update()
            .where(plan_tasks.c.request_id == request_id,
                   plan_tasks.c.task_ref == ref)
            .values(state="cancelled", start_minute=None, pilot_id=None,
                    boat_id=None)
        )
    else:
        j = next(x for x in new_jobs if x.ref == ref)
        a = by_ref_get(result, ref)
        if kind == "add":
            session.execute(plan_tasks.insert().values(
                request_id=request_id, task_ref=ref,
                ship_id=body["task"]["ship_id"], location=j.location,
                start_minute=a.start if a else None, duration=j.duration,
                earliest=j.earliest, latest_start=j.latest_start,
                committed=1 if j.forced else 0,
                pilot_id=a.pilot_id if a else None,
                boat_id=a.boat_unit_id if a else None,
                state="committed" if (a and j.forced) else ("provisional" if a else "unserved"),
            ))
        else:
            state = "committed" if a else "unserved"
            session.execute(
                plan_tasks.update()
                .where(plan_tasks.c.request_id == request_id,
                       plan_tasks.c.task_ref == ref)
                .values(ship_id=body["task"]["ship_id"], location=j.location,
                        duration=j.duration, earliest=j.earliest,
                        latest_start=j.latest_start,
                        committed=1 if j.forced else 0,
                        start_minute=a.start if a else None,
                        pilot_id=a.pilot_id if a else None,
                        boat_id=a.boat_unit_id if a else None, state=state)
            )

    # 重解后同步所有非固定任务的位置
    for r in current:
        if r["state"] == "committed" and r["start_minute"] is not None:
            continue
        refname = r["task_ref"]
        a = by_ref_get(result, refname)
        if a:
            session.execute(
                plan_tasks.update()
                .where(plan_tasks.c.request_id == request_id,
                       plan_tasks.c.task_ref == refname)
                .values(start_minute=a.start, pilot_id=a.pilot_id,
                        boat_id=a.boat_unit_id,
                        state="committed" if r["committed"] else "provisional")
            )
        else:
            session.execute(
                plan_tasks.update()
                .where(plan_tasks.c.request_id == request_id,
                       plan_tasks.c.task_ref == refname)
                .values(start_minute=None, pilot_id=None, boat_id=None,
                        state="unserved")
            )

    explained = _explain_unserved(session, result, all_jobs, new_jobs,
                                  pilot_res, boat_res)
    other_locked = {j.ref for j in extra_fixed}
    sol = _solution_json(result, all_jobs, explained,
                         note=f"revision {next_no}: {kind} {ref}",
                         exclude_refs=other_locked)
    session.execute(
        plan_requests.update().where(plan_requests.c.id == request_id)
        .values(solution=sol, revision_no=next_no)
    )
    session.execute(revisions.insert().values(
        request_id=request_id, revision_no=next_no, kind=kind,
        task_ref=ref, payload=body,
    ))
    session.commit()
    return _revision_output(session, request_id, next_no, kind, ref, applied=True)


def _row_spec(r) -> dict:
    ship = r["ship_id"]
    return {
        "task_ref": r["task_ref"], "ship_id": ship, "location": r["location"],
        "duration_minutes": r["duration"],
        "earliest_start": clock.to_iso(r["earliest"]),
        "latest_start": clock.to_iso(r["latest_start"]),
        "committed": bool(r["committed"]),
    }


def _revision_output(session, request_id, rev_no, kind, ref, applied, note=None,
                     conflicts=None) -> dict:
    plan = format_plan(session, request_id)
    return {
        "plan_id": request_id,
        "revision_no": rev_no,
        "kind": kind,
        "task_ref": ref,
        "applied": applied,
        "assignments": plan["assignments"],
        "unserved": plan["unserved"],
        "conflicts": conflicts or [],
        "total_delay_minutes": plan["total_delay_minutes"],
        "note": note,
    }


# ---------------------------------------------------------------------------

def format_plan(session: Session, request_id: int, *, solution_override=None) -> dict:
    row = session.execute(
        select(plan_requests).where(plan_requests.c.id == request_id)
    ).mappings().first()
    if row is None:
        raise PlanError("计划不存在")
    sol = solution_override or row["solution"]
    tasks = {r["task_ref"]: r for r in _request_job_rows(session, request_id)}

    assignments = []
    for a in sol["assignments"]:
        r = tasks.get(a["task_ref"])
        if r is None:
            continue
        boat_unit = a["boat_unit_id"]
        boat_id = boat_unit.split("#")[0]
        assignments.append({
            "task_ref": a["task_ref"], "ship_id": r["ship_id"],
            "pilot_id": a["pilot_id"], "boat_id": boat_id,
            "boat_unit_id": boat_unit,
            "start": clock.to_iso(a["start"]),
            "end": clock.to_iso(a["start"] + r["duration"]),
            "delay_minutes": a["delay"], "committed": bool(a["committed"]),
        })

    unserved = []
    for u in sol["unserved"]:
        unserved.append({
            "task_ref": u["task_ref"], "committed": bool(u["committed"]),
            "conflict": _format_conflict(u["conflict"]),
        })

    return {
        "plan_id": request_id, "idem_key": row["idem_key"],
        "status": row["status"], "revision_no": row["revision_no"],
        "assignments": assignments, "unserved": unserved,
        "total_delay_minutes": sol["total_delay"],
        "relaxed": sol["relaxed"], "note": sol.get("note"),
    }


def _format_conflict(c: dict) -> dict:
    def w(b):
        return {
            "start": clock.to_iso(b["start"]), "end": clock.to_iso(b["end"]),
            "blocking_task_ref": b.get("blocking_task_ref"),
        }
    return {
        "task_ref": c["task_ref"],
        "tide_windows": [[clock.to_iso(a), clock.to_iso(b + clock.GRID_MINUTES)]
                         for a, b in c["tide_windows"]],
        "pilots": [{"resource_id": p["resource_id"],
                    "blocked_windows": [w(b) for b in p["blocked_windows"]]}
                   for p in c["pilots"]],
        "boat_units": [{"resource_id": b["resource_id"],
                        "blocked_windows": [w(x) for x in b["blocked_windows"]]}
                       for b in c["boat_units"]],
        "note": c["note"],
    }
