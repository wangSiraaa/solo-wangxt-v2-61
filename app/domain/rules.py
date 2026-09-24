"""离线虚构的业务规则：吃水等级、所需潮高、引航员资质与转场时间。

仅用于演示排班求交逻辑，不构成真实航行建议。
"""
from __future__ import annotations

from app.config import settings

# 虚构吃水分档 -> (引航员最低等级, 所需最低潮高(厘米，相对虚构基准面))
DRAFT_TIERS: tuple[tuple[float, int, int], ...] = (
    # (吃水上限米, 等级, 潮高)
    (10.0, 1, 0),
    (13.0, 2, 300),
    (16.0, 3, 450),
)

# 超过最大分档吃水时使用的最高要求
MAX_GRADE = 3
MAX_TIDE = 450


def draft_requirement(draft_meters: float) -> tuple[int, int]:
    """按示例吃水规则返回（所需引航员等级, 所需最低潮高厘米）。"""
    for upper, grade, tide in DRAFT_TIERS:
        if draft_meters <= upper:
            return grade, tide
    return MAX_GRADE, MAX_TIDE


def pilot_qualified(pilot_grade: int, required_grade: int) -> bool:
    return pilot_grade >= required_grade


# 虚构的接送点代码
GROUND_SEA = "sea"   # 外海接送点
GROUND_PORT = "port"  # 港内接送点


def ground_for_direction(direction: str) -> str:
    if direction == "inbound":
        return GROUND_SEA  # 进港：外海登船
    if direction == "outbound":
        return GROUND_PORT  # 出港：港内登船
    raise ValueError(f"未知方向: {direction}")


def setup_minutes(prev_direction: str, next_direction: str) -> int:
    """同一引航员上一任务结束到下一任务开始之间的最小间隔。

    间隔 = 离船时间 + （接送点不同时的）转场时间。
    """
    prev_ground = ground_for_direction(prev_direction)
    next_ground = ground_for_direction(next_direction)
    gap = settings.disembark_minutes
    if prev_ground != next_ground:
        gap += settings.transfer_minutes
    return gap
