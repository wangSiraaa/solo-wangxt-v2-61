"""统一时间轴：所有求解内部时间 = 相对固定起点的整数分钟（UTC）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 固定规划起点。跨午夜窗口即相对此起点跨越 720 分（00:00Z）。
EPOCH = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
# 规划时长 36 小时，覆盖第二个日高潮峰（09-25 13:00 前后）。
HORIZON_MINUTES = 36 * 60
# 时间离散网格（分钟），保证确定性求解。
GRID_MINUTES = 10


def to_minutes(ts: str | datetime) -> int:
    """ISO8601 字符串（或 datetime）→ 相对 EPOCH 的整数分钟。"""
    dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt.astimezone(timezone.utc) - EPOCH).total_seconds() // 60)


def to_iso(minute: int) -> str:
    """整数分钟 → 带 Z 的 UTC ISO8601 字符串。"""
    return (EPOCH + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ")


def snap_down(minute: int) -> int:
    return (minute // GRID_MINUTES) * GRID_MINUTES


def snap_up(minute: int) -> int:
    return ((minute + GRID_MINUTES - 1) // GRID_MINUTES) * GRID_MINUTES


def within_horizon(minute: int) -> bool:
    return 0 <= minute <= HORIZON_MINUTES
