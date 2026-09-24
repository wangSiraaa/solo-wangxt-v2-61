"""场景四：上一任务结束计入离船与转场时间。

只保留一名合格引航员（停用 P-02）。进港（外海接送点）的已承诺作业
锚定 2026-09-24 22:20（1340），时长 90 分钟；出港（港内接送点）作业紧随其后。
不同接送点的最小间隔 = 离船 25 + 转场 10 = 35 分钟，
因此第二作业最早只能在 1465（2026-09-25 00:25）开始——
不能只凭两个作业时间段不重叠来排班。
450cm 跨午夜潮窗 [1334,1546] 与夜班 [1320,1680] 足以容纳这两个作业。
"""
from __future__ import annotations

INBOUND_ANCHOR = "2026-09-24T22:20:00"  # 1340


def _setup_declarations(client, second_anchor: str, committed_second: bool):
    r1 = client.post(
        "/declarations",
        json={
            "ship_id": "SH-03",
            "direction": "inbound",
            "duration_minutes": 90,
            "requested_start": INBOUND_ANCHOR,
            "committed": True,
        },
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/declarations",
        json={
            "ship_id": "SH-03",
            "direction": "outbound",
            "duration_minutes": 60,
            "requested_start": second_anchor,
            "committed": committed_second,
        },
    )
    assert r2.status_code == 201, r2.text
    return r1.json()["id"], r2.json()["id"]


def _only_one_senior_pilot(client):
    r = client.patch("/pilots/P-02", params={"active": False})
    assert r.status_code == 200


def test_setup_between_jobs_enforced(client):
    _only_one_senior_pilot(client)
    a, b = _setup_declarations(
        client, second_anchor=INBOUND_ANCHOR, committed_second=False
    )

    plan = client.post(
        "/plans", json={"declaration_ids": [a, b], "boat_ids": ["B-01", "B-02"]}
    ).json()
    assert plan["conflicts"] == [], plan["conflicts"]
    assert len(plan["tasks"]) == 2

    first = next(t for t in plan["tasks"] if t["declaration_id"] == a)
    second = next(t for t in plan["tasks"] if t["declaration_id"] == b)
    assert first["pilot_id"] == "P-01"
    assert second["pilot_id"] == "P-01"
    assert first["start_minute"] == 1340  # 承诺锚点

    # 1340+90 = 1430 作业结束；加离船 25 + 转场 10 => 1465
    assert second["start_minute"] - first["end_minute"] == 35
    assert second["start_minute"] == 1465
    assert first["disembark_end_minute"] == 1430 + 25
    # 第二作业完整落在跨午夜潮窗内
    assert second["end_minute"] <= 1546
    # 若只判断作业时间相交而不计 setup，第二作业会被错误地排到 1430；这里严格大于
    assert second["start_minute"] > first["end_minute"] + 25  # 同点也至少 25，不同点再加 10


def test_setup_gap_rejects_too_tight_second_job(client):
    """第二作业承诺在 23:20（1400），与首作业间隔不足 35 分钟，必须返回引航员冲突。"""
    _only_one_senior_pilot(client)
    a, b = _setup_declarations(
        client, second_anchor="2026-09-24T23:20:00", committed_second=True
    )

    plan = client.post(
        "/plans", json={"declaration_ids": [a, b], "boat_ids": ["B-01", "B-02"]}
    ).json()
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["declaration_id"] == a
    assert len(plan["conflicts"]) == 1
    c = plan["conflicts"][0]
    assert c["declaration_id"] == b
    assert c["resource_kind"] == "pilot"
    assert c["reason"] == "pilot_busy"
    assert c["blocking_declaration_id"] == a
