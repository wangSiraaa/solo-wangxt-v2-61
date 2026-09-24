"""跨午夜潮窗：作业必须落在跨过 00:00Z 的高潮窗内，且延误最小。"""
from datetime import datetime

from app import clock
from app.domain import required_tide_level
from app.tides import feasible_start_windows, tide_level_at


def _grid():
    from app.tides import generate_tide_samples
    return {r["t_minute"]: r["level_m"] for r in generate_tide_samples()}


def test_tide_curve_has_a_high_tide_window_crossing_midnight():
    # 午夜 = EPOCH(12:00Z) 后 720 分；其前后潮位应满足 draft=4.0 的要求
    req = required_tide_level(4.0)
    assert tide_level_at(720) > req          # 00:00Z 潮位足够
    assert tide_level_at(480) < req          # 20:00Z 潮位不够（不能提前开始）
    assert tide_level_at(960) < req          # 04:00Z 已过窗


def test_feasible_windows_computed_for_long_job_cross_midnight():
    grid = _grid()
    # 120 分钟作业：22:10 才能进窗，作业到 00:10，必然跨过午夜
    windows = feasible_start_windows(
        grid, draft_m=4.0, duration_minutes=120,
        earliest=clock.to_minutes("2026-09-24T20:00:00Z"),
        latest_start=clock.to_minutes("2026-09-25T00:30:00Z"),
    )
    assert windows
    lo, hi = windows[0]
    for s in range(lo, hi + 1, clock.GRID_MINUTES):
        for t in range(s, s + 120 + 1, clock.GRID_MINUTES):
            assert grid[t] + 1e-9 >= required_tide_level(4.0)
    # 最早可行开始 + 120 分钟也已越过 00:00Z
    assert lo + 120 >= 720 > lo


def test_api_plans_crossing_midnight(client):
    payload = {
        "note": "cross midnight",
        "tasks": [{
            "task_ref": "DECL-PAC-001",
            "ship_id": "PACIFIC",
            "location": "ANCH",
            "duration_minutes": 120,
            "earliest_start": "2026-09-24T20:00:00Z",
            "latest_start": "2026-09-25T00:30:00Z",
            "committed": True,
        }],
    }
    r = client.post("/plans", json=payload, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["unserved"] == []
    assert len(body["assignments"]) == 1
    a = body["assignments"][0]

    start = datetime.fromisoformat(a["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(a["end"].replace("Z", "+00:00"))
    # 作业区间跨过 00:00Z
    assert start < datetime.fromisoformat("2026-09-25T00:00:00+00:00") <= end
    # 期望 20:00 起但那时潮位不够 → 必然延误到潮窗
    assert a["delay_minutes"] > 0
    grid = _grid()
    for t in range(clock.to_minutes(a["start"]),
                   clock.to_minutes(a["end"]) + 1, clock.GRID_MINUTES):
        assert grid[t] + 1e-9 >= required_tide_level(4.0)
    assert a["pilot_id"] == "P001"       # 只有 A 级晚班引航员合格
