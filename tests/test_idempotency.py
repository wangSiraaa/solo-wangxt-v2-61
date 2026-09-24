"""场景三：同一申报重复确认不增加第二个有效任务。

- 申报本身用 idempotency_key 幂等；
- 计划确认接口重复调用不会产生第二条 plan_task；
- 锁定后再用同一申报建计划会被 409 拒绝，必须走显式修订。
"""
from __future__ import annotations


def _declare(client):
    return client.post(
        "/declarations",
        json={
            "idempotency_key": "DECL-IDEM-001",
            "ship_id": "SH-01",
            "direction": "inbound",
            "duration_minutes": 60,
            "requested_start": "2026-09-24T09:00:00",
            "committed": False,
        },
    )


def test_declaration_idempotency(client):
    r1 = _declare(client)
    r2 = _declare(client)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    listing = client.get("/declarations").json()
    assert len([d for d in listing if d["idempotency_key"] == "DECL-IDEM-001"]) == 1


def test_repeated_confirm_does_not_duplicate_task(client):
    decl_id = _declare(client).json()["id"]

    plan_id = client.post(
        "/plans", json={"declaration_ids": [decl_id]}
    ).json()["id"]

    c1 = client.post(f"/plans/{plan_id}/confirm")
    c2 = client.post(f"/plans/{plan_id}/confirm")
    c3 = client.post(f"/plans/{plan_id}/confirm")
    assert c1.status_code == 200
    assert c2.status_code == 200
    assert c3.status_code == 200
    assert c1.json()["status"] == "locked"
    assert c2.json()["id"] == plan_id
    assert c3.json()["id"] == plan_id

    tasks = client.get(f"/plans/{plan_id}").json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["declaration_id"] == decl_id

    # 全局只有一条针对该申报的任务
    all_plans = client.get("/plans").json()
    task_count = sum(
        1 for p in all_plans for t in p["tasks"] if t["declaration_id"] == decl_id
    )
    assert task_count == 1


def test_locked_declaration_rejected_without_revision(client):
    decl_id = _declare(client).json()["id"]
    plan_id = client.post(
        "/plans", json={"declaration_ids": [decl_id]}
    ).json()["id"]
    client.post(f"/plans/{plan_id}/confirm")

    # 锁定后再建包含同一申报的计划 -> 409
    again = client.post("/plans", json={"declaration_ids": [decl_id]})
    assert again.status_code == 409
    assert again.json()["error"] == "declaration_already_scheduled"


def test_explicit_revision_creates_new_version(client):
    decl_id = _declare(client).json()["id"]
    plan_id = client.post(
        "/plans", json={"declaration_ids": [decl_id]}
    ).json()["id"]
    client.post(f"/plans/{plan_id}/confirm")

    # 草稿不能“修订”，只有 locked 计划可以
    # 对 locked 计划发起显式修订
    rev = client.post(f"/plans/{plan_id}/revisions", json={})
    assert rev.status_code == 201, rev.text
    child = rev.json()
    assert child["revision_of_id"] == plan_id
    assert child["status"] == "draft"
    assert len(child["tasks"]) == 1

    # 确认修订前，旧版仍 locked
    assert client.get(f"/plans/{plan_id}").json()["status"] == "locked"

    # 修订接口重复确认同样幂等
    rc1 = client.post(f"/plans/{child['id']}/revisions/confirm")
    rc2 = client.post(f"/plans/{child['id']}/revisions/confirm")
    assert rc1.status_code == 200 and rc2.status_code == 200

    parent = client.get(f"/plans/{plan_id}").json()
    child_fresh = client.get(f"/plans/{child['id']}").json()
    assert parent["status"] == "superseded"
    assert parent["superseded_by_id"] == child["id"]
    assert child_fresh["status"] == "locked"
    # 该申报全局仍只有一个有效（locked）任务
    locked_tasks = [
        t
        for p in client.get("/plans").json()
        if p["status"] == "locked"
        for t in p["tasks"]
        if t["declaration_id"] == decl_id
    ]
    assert len(locked_tasks) == 1
