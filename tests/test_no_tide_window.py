"""无可用潮窗（结构不可行）：任务无法落实，冲突明确指向潮汐而非人员/艇。"""


def test_no_tide_window_unserved_with_tide_reason(client):
    # 2026-09-25 04:00–05:00Z 正处于低潮谷附近，4.0m 吃水无任何潮窗
    payload = {
        "tasks": [{
            "task_ref": "DECL-NO-TIDE",
            "ship_id": "PACIFIC",
            "location": "ANCH",
            "duration_minutes": 60,
            "earliest_start": "2026-09-25T04:00:00Z",
            "latest_start": "2026-09-25T05:00:00Z",
            "committed": True,
        }]
    }
    r = client.post("/plans", json=payload, headers={"Idempotency-Key": "no-tide"})
    assert r.status_code == 201
    body = r.json()
    assert body["assignments"] == []
    assert len(body["unserved"]) == 1
    u = body["unserved"][0]
    assert u["task_ref"] == "DECL-NO-TIDE" and u["committed"] is True
    c = u["conflict"]
    assert c["tide_windows"] == []
    assert "潮窗" in c["note"]
    # 计划仍可重复获取且可锁定（锁定只落实可行任务，不生成幽灵任务）
    pid = body["plan_id"]
    locked = client.post(f"/plans/{pid}/confirm").json()
    assert locked["status"] == "locked"
    assert locked["assignments"] == []
    assert len(locked["unserved"]) == 1
