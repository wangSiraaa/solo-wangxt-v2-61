"""离线虚构潮汐数据与吃水潮窗计算。

潮位曲线为虚构半日潮：
    level(t) = 1.0 + 1.5 * cos(2π (t - 高潮相位) / 745)
高潮峰约在每日 23:40 与次日 12:05（UTC），峰值 2.5m；低潮 -0.5m。

仅在 10 分钟网格上取值：作业区间内经过的每个网格点都必须满足
``潮位 >= 所需潮位``，据此得到允许的作业「开始时间区间」。
"""
from __future__ import annotations

import math

from .clock import GRID_MINUTES, HORIZON_MINUTES
from .domain import required_tide_level

# 虚构潮汐参数
_MEAN_LEVEL_M = 1.0
_AMPLITUDE_M = 1.5
_TIDE_PERIOD_MIN = 745
# 第一个高潮峰：2026-09-24 23:40Z = EPOCH 后 700 分钟
# （窗中心跨过午夜，满足深吃水的开始窗约 23:10–00:50Z）
_FIRST_HIGH_TIDE_MIN = 700

STATION_NAME = "FICTIONAL-HARBOR-A"


def tide_level_at(t_minute: int) -> float:
    phase = 2.0 * math.pi * (t_minute - _FIRST_HIGH_TIDE_MIN) / _TIDE_PERIOD_MIN
    return _MEAN_LEVEL_M + _AMPLITUDE_M * math.cos(phase)


def generate_tide_samples() -> list[dict]:
    """生成数据库种子使用的离散潮位样本（10 分钟一点）。"""
    rows = []
    t = 0
    while t <= HORIZON_MINUTES:
        rows.append(
            {
                "station": STATION_NAME,
                "t_minute": t,
                "level_m": round(tide_level_at(t), 4),
            }
        )
        t += GRID_MINUTES
    return rows


def samples_to_grid(rows) -> dict[int, float]:
    return {int(r["t_minute"]): float(r["level_m"]) for r in rows}


def feasible_start_windows(
    samples: dict[int, float],
    draft_m: float,
    duration_minutes: int,
    *,
    earliest: int = 0,
    latest_start: int | None = None,
) -> list[tuple[int, int]]:
    """返回允许的作业开始时间区间列表（闭区间，网格分钟）。

    把所有满足 ``潮位 >= 吃水规则所需潮位`` 的连续网格点合并成潮窗，
    再要求整个作业区间 ``[start, start+duration]`` 落在窗内，最后与
    申报期望时间 ``[earliest, latest_start]`` 求交。
    """
    level_req = required_tide_level(draft_m)
    duration = (duration_minutes // GRID_MINUTES) * GRID_MINUTES
    duration = max(duration, GRID_MINUTES)
    hi = HORIZON_MINUTES - duration if latest_start is None else latest_start
    hi = min(hi, HORIZON_MINUTES - duration)
    lo = max(earliest, 0)

    windows: list[tuple[int, int]] = []
    run_start: int | None = None
    t = 0
    while t <= HORIZON_MINUTES:
        ok = samples.get(t, -99.0) + 1e-9 >= level_req
        if ok and run_start is None:
            run_start = t
        if (not ok or t == HORIZON_MINUTES) and run_start is not None:
            run_end = t if ok else t - GRID_MINUTES
            # 作业起点必须满足 run_start <= s 且 s+duration <= run_end
            wlo = max(lo, run_start)
            whi = min(hi, run_end - duration)
            if wlo <= whi:
                windows.append((wlo, whi))
            run_start = None
        t += GRID_MINUTES
    return windows
