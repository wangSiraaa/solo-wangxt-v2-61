"""离线虚构潮汐：确定性正弦曲线 + 10 分钟采样点 + 线性插值。

数据完全虚构，仅用于计算示例通航窗口，不可用于真实航行决策。
"""
from __future__ import annotations

from app.config import settings

# 虚构潮汐参数（周期 12 小时，峰值恰好落在基准日 00:00，天然形成跨午夜潮窗）
TIDE_MEAN_CM = 300
TIDE_AMPLITUDE_CM = 250
TIDE_PERIOD_MINUTES = 720


def tide_height_at(minute: int) -> float:
    """任意分钟时刻的虚构潮高（厘米）。"""
    import math

    phase = 2.0 * math.pi * float(minute) / TIDE_PERIOD_MINUTES
    return TIDE_MEAN_CM + TIDE_AMPLITUDE_CM * math.cos(phase)


def sample_points(horizon: int | None = None) -> list[tuple[int, float]]:
    """返回固定间隔的离线采样点。"""
    horizon = horizon or settings.horizon_minutes
    step = settings.tide_sample_minutes
    return [(m, tide_height_at(m)) for m in range(0, horizon + 1, step)]


def tide_windows(required_cm: int, horizon: int | None = None) -> list[tuple[int, int]]:
    """计算满足最低潮高的闭区间窗口 [start, end]（整数分钟）。

    在 10 分钟采样网格之间做线性插值求过阈时刻并就近取整；
    区间端点两侧各向外取整，保证窗口对取整后的作业窗口是“安全”的
    （端点按闭区间处理，即作业可恰好始于窗口起点）。
    """
    import math

    horizon = horizon or settings.horizon_minutes
    if required_cm <= TIDE_MEAN_CM - TIDE_AMPLITUDE_CM:
        return [(0, horizon)]

    points = sample_points(horizon)
    windows: list[tuple[int, int]] = []
    # 首个采样点已在阈值之上时（例如潮峰恰好落在 0 点），窗口从 0 开始
    open_start: int | None = 0 if points[0][1] >= required_cm else None

    def crossing(m1: int, h1: float, m2: int, h2: float) -> float:
        if h2 == h1:
            return float(m1)
        return m1 + (required_cm - h1) * (m2 - m1) / (h2 - h1)

    for (m1, h1), (m2, h2) in zip(points, points[1:]):
        above1 = h1 >= required_cm
        above2 = h2 >= required_cm
        if not above1 and above2:  # 上升穿阈
            x = crossing(m1, h1, m2, h2)
            open_start = int(math.ceil(x - 1e-9))
        elif above1 and not above2:  # 下降穿阈
            x = crossing(m1, h1, m2, h2)
            end = int(math.floor(x + 1e-9))
            if open_start is not None:
                windows.append((open_start, end))
            open_start = None
        # 端点恰好相等时 above 判定为 True，自然并入窗口

    if open_start is not None:
        windows.append((open_start, horizon))
    return windows


def intersect_windows(
    a: list[tuple[int, int]], b: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """两组闭区间窗口求交。"""
    out: list[tuple[int, int]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out
