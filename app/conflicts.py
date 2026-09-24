"""未满足任务的冲突解释：指出是潮汐、引航员还是接送艇，在什么时间段卡住。

注意：这里的可行性判断沿用求解器的**完整顺序语义**——不是只看作业区间是否
相交，而是把「离船/靠泊服务 + 地点间转场时间」也算进去。
"""
from __future__ import annotations

from dataclasses import dataclass

from .clock import GRID_MINUTES
from .domain import (
    BASE,
    BOAT_SERVICE_MINUTES,
    DISEMBARK_MINUTES,
    travel_minutes,
)


@dataclass
class BlockInterval:
    start: int
    end: int
    reason_ref: str | None      # 造成占用的任务（工时窗冲突时为 None）


@dataclass
class ResourceConflict:
    resource_id: str
    blocked_windows: list[BlockInterval]


@dataclass
class JobConflict:
    ref: str
    tide_windows: list[tuple[int, int]]
    pilots: list[ResourceConflict]
    boat_units: list[ResourceConflict]
    note: str


def explain(ref, tide_windows, eligible_pilots, eligible_boat_units,
            pilot_schedules, boat_schedules, pilot_work_windows,
            boat_work_windows, location, duration, horizon) -> JobConflict:
    """计算单个未满足任务在候选开始区间上的冲突资源/时间段。

    pilot_schedules / boat_schedules:
        {resource_id: [(ref, location, start, duration, fixed)]}，
        只包含已经落实（含已锁定）的任务。
    *_work_windows: {resource_id: [(lo, hi)]}
    """
    if not tide_windows:
        return JobConflict(ref, [], [], [],
                           "申报时间段内不存在满足吃水规则的连续潮窗")

    pc = [_pilot_conflict(p, tide_windows, pilot_schedules.get(p, []),
                          pilot_work_windows.get(p, []),
                          location, duration, horizon)
          for p in eligible_pilots]
    bc = [_boat_conflict(b, tide_windows, boat_schedules.get(b, []),
                         boat_work_windows.get(b, []),
                         location, duration, horizon)
          for b in eligible_boat_units]
    pc = [c for c in pc if c.blocked_windows]
    bc = [c for c in bc if c.blocked_windows]

    if not eligible_pilots:
        note = "没有资质/工时覆盖该任务的引航员"
    elif not eligible_boat_units:
        note = "没有可工作时段覆盖该任务的接送艇单元"
    elif pc and len(pc) == len(eligible_pilots) and (
            not bc or len(bc) < len(eligible_boat_units)):
        note = "合格引航员全部不可用：或在执行任务（离船+转场后才能接），或不在可工作时段"
    elif bc and len(bc) == len(eligible_boat_units):
        note = "接送艇容量不足：所有艇单元在候选潮窗内都被占用（含航行/靠泊时间）"
    else:
        note = "引航员与接送艇无法在同一潮窗内同时到位"
    return JobConflict(ref, tide_windows, pc, bc, note)


# ---------------------------------------------------------------------------

def _pilot_conflict(pid, windows, schedule, work_windows, location, duration, horizon):
    blocked: list[BlockInterval] = []
    for lo, hi in windows:
        # 工时窗：作业 + 离船必须完整落在某个工作窗内（与求解器一致；
        # 返程航行只要求落在规划时域内）
        need_tail = duration + DISEMBARK_MINUTES
        if not any(
            wlo <= s and s + need_tail <= whi
            for s in range(lo, hi + 1, GRID_MINUTES)
            for wlo, whi in work_windows
        ):
            blocked.append(BlockInterval(lo, hi, None))

        # 已安排任务：间隙不足「上一作业时长+离船+转场」即被挡住
        for ref2, loc2, s2, d2, _fixed in schedule:
            before = s2 - (d2 + DISEMBARK_MINUTES + travel_minutes(loc2, location))
            after = s2 + DISEMBARK_MINUTES + travel_minutes(location, loc2)
            if before <= hi and after >= lo:
                blocked.append(BlockInterval(max(lo, before), min(hi, after), ref2))
    return ResourceConflict(pid, _merge(blocked))


def _boat_conflict(bid, windows, schedule, work_windows, location, duration, horizon):
    blocked: list[BlockInterval] = []
    for lo, hi in windows:
        # 靠泊时刻 s 与 10 分钟接人服务必须落在某个工时窗内
        if not any(
            wlo <= s and s + BOAT_SERVICE_MINUTES <= whi
            for s in range(lo, hi + 1, GRID_MINUTES)
            for wlo, whi in work_windows
        ):
            blocked.append(BlockInterval(lo, hi, None))

        # 已安排的艇服务：s2 靠泊，[s2, s2+服务+航行] 期间不能在本点靠泊；
        # 前向还要给从本点到下一服务点的航行留时间
        for ref2, loc2, s2, _d2, _fixed in schedule:
            before = s2 - (BOAT_SERVICE_MINUTES + travel_minutes(location, loc2))
            after = s2 + BOAT_SERVICE_MINUTES + travel_minutes(loc2, location)
            if before <= hi and after >= lo:
                blocked.append(BlockInterval(max(lo, before), min(hi, after), ref2))
    return ResourceConflict(bid, _merge(blocked))


def _merge(intervals: list[BlockInterval]) -> list[BlockInterval]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda b: (b.start, b.end))
    out = [intervals[0]]
    for b in intervals[1:]:
        last = out[-1]
        if b.start <= last.end + GRID_MINUTES:
            last.end = max(last.end, b.end)
            if b.reason_ref and not last.reason_ref:
                last.reason_ref = b.reason_ref
        else:
            out.append(b)
    return out
