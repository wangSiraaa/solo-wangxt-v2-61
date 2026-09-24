"""SQLAlchemy 数据模型（PostgreSQL 生产库；测试可使用 SQLite）。"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Ship(Base):
    __tablename__ = "ships"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    draft_meters: Mapped[float] = mapped_column()


class Pilot(Base):
    __tablename__ = "pilots"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    grade: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    windows: Mapped[list["WorkWindow"]] = relationship(
        back_populates="pilot", cascade="all, delete-orphan"
    )


class WorkWindow(Base):
    """引航员可工作时段（分钟偏移，end 可超过 48h 以表达跨夜班）。"""

    __tablename__ = "work_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pilot_id: Mapped[str] = mapped_column(ForeignKey("pilots.id"))
    start_minute: Mapped[int] = mapped_column(Integer)
    end_minute: Mapped[int] = mapped_column(Integer)

    pilot: Mapped[Pilot] = relationship(back_populates="windows")


class Boat(Base):
    __tablename__ = "boats"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Declaration(Base):
    """船舶申报作业。"""

    __tablename__ = "declarations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
    ship_id: Mapped[str] = mapped_column(ForeignKey("ships.id"))
    direction: Mapped[str] = mapped_column(String(8))  # inbound / outbound
    duration_minutes: Mapped[int] = mapped_column(Integer)
    requested_start_minute: Mapped[int] = mapped_column(Integer)
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending / scheduled / conflict
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[str] = mapped_column(String(32))


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # draft / locked / superseded
    revision_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id"), nullable=True
    )
    boat_capacity: Mapped[int] = mapped_column(Integer)
    boat_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(32))
    locked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id"), nullable=True
    )

    tasks: Mapped[list["PlanTask"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    conflicts: Mapped[list["ConflictRow"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class PlanTask(Base):
    """同一申报可在多个计划版本（含已被取代的历史版本）中出现；
    “每个申报只有一个有效（locked）任务”由 active 标志上的
    部分唯一索引 + 服务层在锁定时共同强制。
    """

    __tablename__ = "plan_tasks"
    __table_args__ = (
        Index(
            "uq_active_task_declaration",
            "declaration_id",
            unique=True,
            postgresql_where="active",
            sqlite_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"))
    pilot_id: Mapped[str] = mapped_column(ForeignKey("pilots.id"))
    start_minute: Mapped[int] = mapped_column(Integer)
    end_minute: Mapped[int] = mapped_column(Integer)
    embark_start_minute: Mapped[int] = mapped_column(Integer)
    disembark_end_minute: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=False)

    plan: Mapped[Plan] = relationship(back_populates="tasks")


class ConflictRow(Base):
    __tablename__ = "conflict_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"))
    # no_qualified_pilot / no_tide_window / no_pilot_window / pilot_busy / boat_capacity
    reason: Mapped[str] = mapped_column(String(32))
    # qualification / tide / window / pilot / boat
    resource_kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blocking_declaration_id: Mapped[int | None] = mapped_column(
        ForeignKey("declarations.id"), nullable=True
    )
    window_start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(String(256), default="")

    plan: Mapped[Plan] = relationship(back_populates="conflicts")
