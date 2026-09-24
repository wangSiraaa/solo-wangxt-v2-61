from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # 虚构的“站钟”基准日，全部排程时刻均为该基准日之后的相对分钟数
    base_date: str = os.environ.get("PLAN_BASE_DATE", "2026-09-24")
    horizon_minutes: int = _int("PLAN_HORIZON_MINUTES", 48 * 60)

    database_url: str = os.environ.get(
        "DATABASE_URL", "sqlite+pysqlite:///./pilot_schedule.db"
    )

    # 上一任务结束后的离船时间（分钟）
    disembark_minutes: int = _int("PILOT_DISEMBARK_MINUTES", 25)
    # 作业开始前的登船接送时间（分钟）
    embark_minutes: int = _int("PILOT_EMBARK_MINUTES", 20)
    # 不同接送点之间的转场时间（分钟，虚构规则）
    transfer_minutes: int = _int("PILOT_TRANSFER_MINUTES", 10)

    # 潮汐离线采样间隔（分钟）
    tide_sample_minutes: int = _int("TIDE_SAMPLE_MINUTES", 10)

    # CP-SAT 确定性求解参数
    solver_time_limit_seconds: float = float(
        os.environ.get("SOLVER_TIME_LIMIT_SECONDS", "15")
    )
    solver_seed: int = _int("SOLVER_SEED", 42)


settings = Settings()
