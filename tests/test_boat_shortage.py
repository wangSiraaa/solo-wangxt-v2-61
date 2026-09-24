"""场景二：合格引航员充足，但接送艇容量不足。

两个已承诺（committed）作业（SH-03、SH-05 均为等级 3、需 450cm）锚定在
2026-09-24 23:20（1400），该时刻落在跨午夜潮窗 [1334,1546] 内：
- 两名合格引航员（P-01、P-02 均等级 3，夜班 22:00–04:00）可以并行；
- 但两艘作业的登船段 [1380,1400) 完全重合，单艇容量下无法同时接送；
  给两艘艇则全部满足、零延误。
"""
from __future__ import annotations


def _committed_pair(client):
    ids = []
    for ship in ("SH-03", "SH-05"):
        r = client.post(
            "/declarations",
            json={
                "ship_id": ship,
                "direction": "inbound",
                "duration_minutes": 60,
                "requested_start": "2026-09-24T23:20:00",
                "committed": True,
            },
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def test_pilots_sufficient_but_boats_insufficient(client):
    a, b = _committed_pair(client)

    r = client.post(
        "/plans", json={"declaration_ids": [a, b], "boat_ids": ["B-01"]}
    )
    assert r.status_code == 201, r.text
    plan = r.json()

    # 承诺任务优先：一个被安排，另一个必须返回冲突
    assert len(plan["tasks"]) == 1
    assert len(plan["conflicts"]) == 1

    conflict = plan["conflicts"][0]
    assert conflict["resource_kind"] == "boat"
    assert conflict["reason"] == "boat_capacity"
    assert conflict["resource_id"] == "fleet"
    # 冲突必须给出挡路的资源与时间段
    assert conflict["blocking_declaration_id"] in (a, b)
    assert conflict["window_start_minute"] is not None
    assert conflict["window_end_minute"] is not None

    # 已安排的任务锚定在承诺时刻 1400
    task = plan["tasks"][0]
    assert task["start_minute"] == 1400
    assert task["pilot_id"] in ("P-01", "P-02")


def test_same_demand_satisfied_with_two_boats(client):
    a, b = _committed_pair(client)

    r = client.post(
        "/plans", json={"declaration_ids": [a, b], "boat_ids": ["B-01", "B-02"]}
    )
    assert r.status_code == 201, r.text
    plan = r.json()

    assert plan["conflicts"] == []
    assert len(plan["tasks"]) == 2
    pilots = {t["pilot_id"] for t in plan["tasks"]}
    assert pilots == {"P-01", "P-02"}
    for t in plan["tasks"]:
        assert t["start_minute"] == 1400  # 两艇并行，承诺时刻无需延误
