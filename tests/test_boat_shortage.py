"""合格引航员充足、但接送艇容量不足：第二条任务无法落实，冲突必须指向艇。"""
from app import conflicts
from app.db import boats
from app.domain import required_tide_level
from app.model import BundleInput, JobInput, ResourceInput, solve
from app.tides import feasible_start_windows, generate_tide_samples
from sqlalchemy import delete


def _grid():
    return {r["t_minute"]: r["level_m"] for r in generate_tide_samples()}


def _two_job_bundle(capacity_units):
    grid = _grid()
    # 次日 13:00 高潮窗：把申报收窄到唯一可行开始点 24:00（t=1440），
    # 两条任务同时刻必须并行，单艇无法靠转场串行化解
    windows = feasible_start_windows(
        grid, draft_m=4.0, duration_minutes=60,
        earliest=1440, latest_start=1440,
    )
    assert windows == [(1440, 1440)], "测试前置：该点应是唯一可行开始点"

    pilots = [
        ResourceInput(id="P001", windows=[(1400, 1700)]),
        ResourceInput(id="P002", windows=[(1400, 1700)]),
    ]
    boats = [ResourceInput(id=f"B01#{i}", windows=[(0, 2160)])
             for i in range(capacity_units)]
    jobs = [
        JobInput(ref="J1", location="ANCH", duration=60, earliest=1440,
                latest_start=1440, tide_windows=windows,
                eligible_pilots=["P001", "P002"],
                eligible_boat_units=[b.id for b in boats], forced=True),
        JobInput(ref="J2", location="INB", duration=60, earliest=1440,
                latest_start=1440, tide_windows=windows,
                eligible_pilots=["P001", "P002"],
                eligible_boat_units=[b.id for b in boats], forced=True),
    ]
    return BundleInput(jobs=jobs, pilots=pilots, boat_units=boats,
                       horizon=2160), windows


def test_two_boats_suffice():
    bundle, _ = _two_job_bundle(capacity_units=2)
    res = solve(bundle)
    assert res.feasible
    assert res.unserved == []
    assert len(res.assignments) == 2
    assert {a.boat_unit_id for a in res.assignments} == {"B01#0", "B01#1"}
    assert {a.pilot_id for a in res.assignments} == {"P001", "P002"}


def test_single_boat_cannot_serve_two_jobs_within_tide_window():
    bundle, windows = _two_job_bundle(capacity_units=1)
    res = solve(bundle)

    # 两条都是承诺任务：第一阶段不可行 → 放宽重解，恰好落实一条
    assert res.feasible and res.relaxed
    assert len(res.assignments) == 1
    assert len(res.unserved) == 1
    unserved_ref = res.unserved[0]
    served = res.assignments[0]
    assert unserved_ref in ("J1", "J2") and served.ref != unserved_ref
    assert res.unserved_forced == [unserved_ref]

    # --- 冲突解释：瓶颈是艇而不是人 ------------------------------------
    job_by_ref = {j.ref: j for j in bundle.jobs}
    j = job_by_ref[unserved_ref]
    # 已服务任务只占用其实际指派到的资源
    schedules_p: dict[str, list] = {}
    schedules_b: dict[str, list] = {}
    for a in res.assignments:
        aj = job_by_ref[a.ref]
        schedules_p.setdefault(a.pilot_id, []).append(
            (a.ref, aj.location, a.start, aj.duration, False))
        schedules_b.setdefault(a.boat_unit_id, []).append(
            (a.ref, aj.location, a.start, aj.duration, False))

    c = conflicts.explain(
        j.ref, j.tide_windows, j.eligible_pilots, j.eligible_boat_units,
        schedules_p, schedules_b,
        {"P001": [(1400, 1700)], "P002": [(1400, 1700)]},
        {"B01#0": [(0, 2160)]},
        j.location, j.duration, 2160,
    )
    # 至少一名合格引航员空闲（瓶颈不是人）
    blocked_pilots = {p.resource_id for p in c.pilots}
    assert blocked_pilots != {"P001", "P002"}
    # 唯一艇单元报告被占用的时间段（容量瓶颈）
    assert len(c.boat_units) == 1
    assert c.boat_units[0].resource_id == "B01#0"
    assert c.boat_units[0].blocked_windows
    assert "接送艇" in c.note


def test_api_plenty_pilots_but_single_boat_blocks_second_declaration(client, session):
    """HTTP 层：晚班有两名合格引航员；移除 B02 后只剩 1 个艇单元，
    两条同一时刻的申报只能落实一条，冲突解释指向艇资源。"""
    session.execute(delete(boats).where(boats.c.id == "B02"))
    session.commit()

    payload = {
        "tasks": [
            {"task_ref": "X1", "ship_id": "COASTER", "location": "ANCH",
             "duration_minutes": 60,
             "earliest_start": "2026-09-24T23:00:00Z",
             "latest_start": "2026-09-24T23:00:00Z", "committed": True},
            {"task_ref": "X2", "ship_id": "COASTER", "location": "ANCH",
             "duration_minutes": 60,
             "earliest_start": "2026-09-24T23:00:00Z",
             "latest_start": "2026-09-24T23:00:00Z", "committed": True},
        ]
    }
    r = client.post("/plans", json=payload, headers={"Idempotency-Key": "boat-1"})
    assert r.status_code == 201
    body = r.json()
    assert len(body["assignments"]) == 1
    assert len(body["unserved"]) == 1
    served = body["assignments"][0]
    blocked = body["unserved"][0]["conflict"]

    # 两名合格引航员都在班：不能把原因归为“所有引航员不可用”
    assert not (
        {p["resource_id"] for p in blocked["pilots"]} >= {"P001", "P002"}
    )
    # 唯一艇单元 B01#0 报告与已落实任务在同一时段的占用
    boat_ids = {b["resource_id"] for b in blocked["boat_units"]}
    assert boat_ids == {"B01#0"}
    assert blocked["boat_units"][0]["blocked_windows"][0]["blocking_task_ref"] == served["task_ref"]
    assert "接送艇" in blocked["note"]
