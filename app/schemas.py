from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        raise ValueError("请使用不带时区的站钟时间")
    return dt.replace(microsecond=0)


# ---------- 申报 ----------


class DeclarationIn(BaseModel):
    idempotency_key: str | None = Field(
        default=None, max_length=128, description="重复提交时使用，保证幂等"
    )
    ship_id: str = Field(max_length=16)
    direction: str = Field(pattern="^(inbound|outbound)$")
    duration_minutes: int = Field(gt=0, le=600)
    requested_start: datetime
    committed: bool = False

    @field_validator("requested_start")
    @classmethod
    def _naive(cls, v: datetime) -> datetime:
        return _naive(v)


class DeclarationOut(BaseModel):
    id: int
    idempotency_key: str | None
    ship_id: str
    direction: str
    duration_minutes: int
    requested_start: str
    requested_start_minute: int
    committed: bool
    status: str


# ---------- 资源 ----------


class ShipOut(BaseModel):
    id: str
    name: str
    draft_meters: float
    required_grade: int
    required_tide_cm: int


class WorkWindowOut(BaseModel):
    start: str
    end: str
    start_minute: int
    end_minute: int


class PilotOut(BaseModel):
    id: str
    name: str
    grade: int
    active: bool
    work_windows: list[WorkWindowOut]


class BoatOut(BaseModel):
    id: str
    name: str
    active: bool


class TideWindowOut(BaseModel):
    start: str
    end: str
    start_minute: int
    end_minute: int


class TidePointOut(BaseModel):
    minute: int
    time: str
    height_cm: float


# ---------- 计划 ----------


class PlanBuildIn(BaseModel):
    declaration_ids: list[int] = Field(min_length=1)
    # 仅在指定船数时生效；缺省使用全部可用接送艇
    boat_ids: list[str] | None = None


class RevisionIn(BaseModel):
    declaration_ids: list[int] | None = Field(
        default=None, description="本次显式修订纳入的申报；缺省沿用原计划"
    )
    boat_ids: list[str] | None = None


class TaskOut(BaseModel):
    declaration_id: int
    ship_id: str
    pilot_id: str
    start: str
    end: str
    start_minute: int
    end_minute: int
    embark_start: str
    embark_start_minute: int
    disembark_end: str
    disembark_end_minute: int
    delay_minutes: int


class ConflictOut(BaseModel):
    declaration_id: int
    reason: str
    resource_kind: str
    resource_id: str | None = None
    blocking_declaration_id: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    window_start_minute: int | None = None
    window_end_minute: int | None = None
    detail: str


class PlanOut(BaseModel):
    id: int
    status: str
    revision_of_id: int | None = None
    superseded_by_id: int | None = None
    boat_capacity: int
    boat_ids: list[str]
    created_at: str
    locked_at: str | None = None
    tasks: list[TaskOut]
    conflicts: list[ConflictOut]
