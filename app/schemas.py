"""API 请求/响应模型（Pydantic v2）。时间一律 ISO8601 UTC 字符串。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TaskIn(BaseModel):
    task_ref: str = Field(..., description="本次请求内唯一的任务引用")
    ship_id: str
    location: str = Field(..., description="作业地点：ANCH/INB/OUT/BERTH")
    duration_minutes: int = Field(..., gt=0)
    earliest_start: str = Field(..., description="ISO8601，期望最早开始")
    latest_start: str = Field(..., description="ISO8601，期望最晚开始")
    committed: bool = False


class PlanCreate(BaseModel):
    note: str | None = None
    tasks: list[TaskIn]


class BlockedWindowOut(BaseModel):
    start: str
    end: str
    blocking_task_ref: str | None = None


class ResourceConflictOut(BaseModel):
    resource_id: str
    blocked_windows: list[BlockedWindowOut]


class TaskConflictOut(BaseModel):
    task_ref: str
    tide_windows: list[list[str]]
    pilots: list[ResourceConflictOut]
    boat_units: list[ResourceConflictOut]
    note: str


class AssignmentOut(BaseModel):
    task_ref: str
    ship_id: str
    pilot_id: str
    boat_id: str
    boat_unit_id: str
    start: str
    end: str
    delay_minutes: int
    committed: bool


class UnservedOut(BaseModel):
    task_ref: str
    committed: bool
    conflict: TaskConflictOut


class PlanOut(BaseModel):
    plan_id: int
    idem_key: str
    status: Literal["draft", "locked"]
    reused: bool = False
    revision_no: int = 0
    assignments: list[AssignmentOut]
    unserved: list[UnservedOut]
    total_delay_minutes: int
    relaxed: bool
    note: str | None = None


# ----- 修订 -----------------------------------------------------------------

class RevisionTaskIn(BaseModel):
    ship_id: str
    location: str
    duration_minutes: int = Field(..., gt=0)
    earliest_start: str
    latest_start: str
    committed: bool = False


class RevisionIn(BaseModel):
    kind: Literal["cancel", "add", "reschedule"]
    task_ref: str
    task: RevisionTaskIn | None = None       # add / reschedule 必填


class RevisionResultOut(BaseModel):
    plan_id: int
    revision_no: int
    kind: str
    task_ref: str
    applied: bool
    assignments: list[AssignmentOut]
    unserved: list[UnservedOut]
    conflicts: list[dict] = []
    total_delay_minutes: int
    note: str | None = None
