"""API 生命周期：重复提交/重复确认幂等；锁定后只能显式修订。"""


CROSS_MIDNIGHT = {
    "note": "cross midnight",
    "tasks": [{
        "task_ref": "DECL-PAC-001",
        "ship_id": "PACIFIC",
        "location": "ANCH",
        "duration_minutes": 60,
        "earliest_start": "2026-09-24T20:00:00Z",
        "latest_start": "2026-09-25T00:30:00Z",
        "committed": True,
    }],
}


def _submit(client, key, payload=None):
    return client.post("/plans", json=payload or CROSS_MIDNIGHT,
                       headers={"Idempotency-Key": key})


def test_repeat_submit_same_idempotency_key_reuses_plan(client):
    r1 = _submit(client, "idem-1")
    assert r1.status_code == 201
    first = r1.json()
    assert first["reused"] is False

    r2 = _submit(client, "idem-1")
    assert r2.status_code == 200
    second = r2.json()
    assert second["reused"] is True
    assert second["plan_id"] == first["plan_id"]


def test_confirm_is_idempotent_and_does_not_add_second_effective_task(client):
    plan = _submit(client, "idem-confirm").json()
    pid = plan["plan_id"]

    c1 = client.post(f"/plans/{pid}/confirm")
    assert c1.status_code == 200
    body1 = c1.json()
    assert body1["status"] == "locked" and body1["reused"] is False

    # 同一请求重复确认：幂等复用，不增加第二个有效任务
    c2 = client.post(f"/plans/{pid}/confirm")
    body2 = c2.json()
    assert body2["reused"] is True
    assert body2["status"] == "locked"
    assert len(body2["assignments"]) == len(body1["assignments"]) == 1
    assert [a["task_ref"] for a in body2["assignments"]] == ["DECL-PAC-001"]

    # 原始申报再提交（同 key）也仍是同一个计划
    again = _submit(client, "idem-confirm")
    assert again.json()["plan_id"] == pid
    assert len(again.json()["assignments"]) == 1


def test_draft_can_be_re_submitted_but_locked_plan_only_revised(client):
    plan = _submit(client, "idem-lock").json()
    pid = plan["plan_id"]

    # 未锁定：修订接口拒绝
    r = client.post(f"/plans/{pid}/revisions", json={
        "kind": "cancel", "task_ref": "DECL-PAC-001"})
    assert r.status_code == 400

    client.post(f"/plans/{pid}/confirm")

    # 锁定后显式取消成功
    r = client.post(f"/plans/{pid}/revisions", json={
        "kind": "cancel", "task_ref": "DECL-PAC-001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["revision_no"] == 1
    assert body["assignments"] == []

    # 再取消一次被拒绝
    r = client.post(f"/plans/{pid}/revisions", json={
        "kind": "cancel", "task_ref": "DECL-PAC-001"})
    assert r.status_code == 400


def test_reschedule_revision_moves_task_to_next_high_tide(client):
    plan = _submit(client, "idem-rev").json()
    pid = plan["plan_id"]
    client.post(f"/plans/{pid}/confirm")

    rev = {
        "kind": "reschedule",
        "task_ref": "DECL-PAC-001",
        "task": {
            "ship_id": "PACIFIC", "location": "ANCH",
            "duration_minutes": 60,
            "earliest_start": "2026-09-25T09:00:00Z",
            "latest_start": "2026-09-25T13:00:00Z",
            "committed": True,
        },
    }
    r = client.post(f"/plans/{pid}/revisions", json=rev)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True and body["revision_no"] == 1
    assert len(body["assignments"]) == 1
    a = body["assignments"][0]
    assert "2026-09-25T" in a["start"]
    assert a["pilot_id"] == "P003"   # 白班 A 级
    assert a["boat_id"] in ("B01", "B02")


def test_revision_rejected_when_committed_task_becomes_infeasible(client):
    # 锁定两条跨午夜的承诺任务，再尝试把其中一条 reschedule 到同一时刻
    payload = {
        "tasks": [
            {"task_ref": "T-A", "ship_id": "PACIFIC", "location": "ANCH",
             "duration_minutes": 60,
             "earliest_start": "2026-09-24T21:00:00Z",
             "latest_start": "2026-09-25T01:00:00Z", "committed": True},
            {"task_ref": "T-B", "ship_id": "PACIFIC", "location": "INB",
             "duration_minutes": 60,
             "earliest_start": "2026-09-24T21:00:00Z",
             "latest_start": "2026-09-25T01:00:00Z", "committed": True},
        ]
    }
    pid = _submit(client, "idem-409", payload).json()["plan_id"]
    client.post(f"/plans/{pid}/confirm")

    # 把 T-B 改到与 T-A 完全同一时刻同一地点，且窗口只有那一点
    rev = {
        "kind": "reschedule",
        "task_ref": "T-B",
        "task": {
            "ship_id": "PACIFIC", "location": "ANCH",
            "duration_minutes": 60,
            "earliest_start": "2026-09-24T23:00:00Z",
            "latest_start": "2026-09-24T23:00:00Z",
            "committed": True,
        },
    }
    r = client.post(f"/plans/{pid}/revisions", json=rev)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["applied"] is False
    # 被拒绝后原计划不动（revision_no 不增长）
    plan = client.get(f"/plans/{pid}").json()
    assert plan["revision_no"] == 0
    assert len(plan["assignments"]) == 2
