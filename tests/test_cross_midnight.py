"""场景一：跨午夜潮窗。

深吃水 SH-04（等级 3、需要 450cm）在第 2 天 00:30（1470）申报 120 分钟进港。
跨午夜潮窗为 [1334,1546]（22:14–01:46），00:30 起 120 分钟无法整段落窗，
因此作业必须提前到 23:46（1426）开始、01:46 结束——作业区间跨越午夜 1440。
"""
from __future__ import annotations

# 450cm 跨午夜潮窗
W_LO, W_HI = 1334, 1546


def _declare(client, **kw):
    payload = {
        "ship_id": "SH-04",
        "direction": "inbound",
        "duration_minutes": 120,
        "requested_start": "2026-09-25T00:30:00",
        "committed": False,
    }
    payload.update(kw)
    r = client.post("/declarations", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_tide_windows_cross_midnight(client):
    r = client.get("/tides/windows", params={"required_cm": 450})
    windows = r.json()
    crossing = [w for w in windows if w["start_minute"] < 1440 <= w["end_minute"]]
    assert len(crossing) == 1
    w = crossing[0]
    assert w["start"] == "2026-09-24T22:14:00"
    assert w["end"] == "2026-09-25T01:46:00"

    # 通过船舶吃水规则查询应得到同样的潮高要求
    by_ship = client.get("/tides/windows", params={"ship_id": "SH-04"}).json()
    assert by_ship == windows


def test_job_scheduled_across_midnight(client):
    decl_id = _declare(client)

    r = client.post("/plans", json={"declaration_ids": [decl_id]})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["status"] == "draft"
    assert len(plan["tasks"]) == 1
    assert plan["conflicts"] == []

    task = plan["tasks"][0]
    assert task["pilot_id"] in ("P-01", "P-02")
    # 作业区间跨午夜
    assert task["start_minute"] < 1440 < task["end_minute"]
    # 完整落在跨夜潮窗 [1334,1546] 内，并尽量贴近 00:30（1470）的申报时刻：
    # 最晚可行开始 = 1546-120 = 1426
    assert task["start_minute"] >= W_LO
    assert task["end_minute"] <= W_HI
    assert task["start_minute"] == W_HI - 120
    assert task["delay_minutes"] == 0  # 提前不算延误
    # 登船段 / 离船段与作业区间的衔接
    assert task["embark_start_minute"] == 1426 - 20
    assert task["disembark_end_minute"] == 1426 + 120 + 25
