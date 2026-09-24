"""站钟（虚构时钟）与分钟偏移量之间的转换。

排班内部统一使用相对基准日的整数分钟偏移量，API 层再翻译成 ISO 字符串。
所有时间均为离线虚构时间，不带时区，不用于真实航行决策。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.config import settings

_BASE = datetime.strptime(settings.base_date, "%Y-%m-%d")


def base_dt() -> datetime:
    return _BASE


def to_dt(minute: int) -> datetime:
    return _BASE + timedelta(minutes=int(minute))


def to_iso(minute: int) -> str:
    return to_dt(minute).isoformat()


def to_minute(dt: datetime) -> int:
    if dt.tzinfo is not None:
        raise ValueError("本服务只接受不带时区的站钟时间")
    delta = dt - _BASE
    return int(delta.total_seconds() // 60)


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
