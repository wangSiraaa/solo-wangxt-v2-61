"""虚构领域常量：地点、离船/转场时间、吃水规则。

这些都是离线虚构数据，只用于排班演示，不反映任何真实港口。
"""
from __future__ import annotations

# 地点编码
BASE = "STATION"        # 引航基地 / 接送艇停泊点
ANCHORAGE = "ANCH"      # 检疫锚地
INBOUND_PILOT = "INB"   # 进口引航登轮点
OUTBOUND_PILOT = "OUT"  # 出口引航离轮点
BERTH = "BERTH"         # 港区泊位

LOCATIONS = [BASE, ANCHORAGE, INBOUND_PILOT, OUTBOUND_PILOT, BERTH]

# 引航员在任务结束地点离船所需固定时间（分钟）
DISEMBARK_MINUTES = 10
# 接送艇到达作业地点后的靠泊/接人服务时间（分钟）
BOAT_SERVICE_MINUTES = 10

# 地点之间的航行时间（分钟，虚构、对称）。二元组顺序无关。
_TRAVEL_RAW = {
    ("STATION", "ANCH"): 20,
    ("ANCH", "INB"): 15,
    ("ANCH", "OUT"): 25,
    ("ANCH", "BERTH"): 35,
    ("STATION", "INB"): 15,
    ("STATION", "OUT"): 15,
    ("STATION", "BERTH"): 30,
    ("INB", "OUT"): 20,
    ("INB", "BERTH"): 25,
    ("OUT", "BERTH"): 20,
}
_TRAVEL = {tuple(sorted(k)): v for k, v in _TRAVEL_RAW.items()}


def travel_minutes(a: str, b: str) -> int:
    """两个地点间的接送艇/引航员转场航行时间（虚构值）。"""
    if a == b:
        return 0
    if a not in LOCATIONS or b not in LOCATIONS:
        raise ValueError(f"unknown location: {a!r} or {b!r}")
    return _TRAVEL[tuple(sorted((a, b)))]


# 虚构海图基准水深（m），用于把吃水换算成所需潮位。
CHART_DATUM_DEPTH_M = 2.5
# 富余水深 UKC（m）。
UKC_M = 0.5


def required_tide_level(draft_m: float) -> float:
    """示例吃水规则：最低所需潮位 = 吃水 + 富余水深 - 基准水深。

    即 draft 4.0m 时需要潮位 >= 2.0m。
    """
    return draft_m + UKC_M - CHART_DATUM_DEPTH_M


# 资质等级
GRADE_A = "A"
GRADE_B = "B"

# 满足船舶所需资质（吃水或船长达到任一高门槛即需 A 级）。
DRAFT_GRADE_A_THRESHOLD = 4.0
LOA_GRADE_A_THRESHOLD = 250.0


def required_grade(draft_m: float, length_m: float | None) -> str:
    if draft_m >= DRAFT_GRADE_A_THRESHOLD or (length_m or 0.0) >= LOA_GRADE_A_THRESHOLD:
        return GRADE_A
    return GRADE_B


def grade_satisfies(pilot_grade: str, required: str) -> bool:
    """A 级可覆盖 B 级任务。"""
    if required == GRADE_A:
        return pilot_grade == GRADE_A
    return pilot_grade in (GRADE_A, GRADE_B)
