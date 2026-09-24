"""场景五（补充）：无解任务的结构化冲突原因。

- 无合格引航员（停用全部 3 级引航员后，深吃水任务无法执行）；
- 潮汐要求高于虚构最高潮位（直接构造引擎作业验证 no_tide_window）。
"""
from __future__ import annotations

from app.domain.tides import tide_windows
from app.engine.scheduler import Job, PilotInfo, solve


def test_no_qualified_pilot_conflict(client):
    for pid in ("P-01", "P-02"):
        r = client.patch(f"/pilots/{pid}", params={"active": False})
        assert r.status_code == 200

    decl = client.post(
        "/declarations",
        json={
            "ship_id": "SH-04",
            "direction": "inbound",
            "duration_minutes": 60,
            "requested_start": "2026-09-24T23:20:00",
            "committed": True,
        },
    ).json()

    plan = client.post("/plans", json={"declaration_ids": [decl["id"]]}).json()
    assert plan["tasks"] == []
    assert len(plan["conflicts"]) == 1
    c = plan["conflicts"][0]
    assert c["reason"] == "no_qualified_pilot"
    assert c["resource_kind"] == "qualification"
    assert "等级" in c["detail"]


def test_no_tide_window_conflict():
    # 虚构潮位峰值 550cm；要求 600cm 时整个周期都无窗口
    jobs = [Job(1, 1, 600, "inbound", 60, 600, True)]
    pilots = [PilotInfo("P-09", 1, ((0, 2880),))]
    result = solve(jobs, pilots, {600: tide_windows(600)}, 1)
    assert result.tasks == []
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.reason == "no_tide_window"
    assert c.resource_kind == "tide"
