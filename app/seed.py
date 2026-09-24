"""离线虚构基础数据：船舶、引航员与班次、接送艇。

所有资料均为虚构样例，仅供演示资质/潮汐/艇位的求交与排班逻辑，
不用于真实航行决策。潮汐按确定性公式离线生成（见 domain.tides）。

基础数据以普通数据结构声明，seed() 每次构造全新 ORM 实例，
避免同一组实例被多个会话/SessionFactory 复用时不再发 INSERT。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Boat, Pilot, Ship, WorkWindow

H = 60

# (id, 船名, 吃水米)
SHIP_ROWS: list[tuple[str, str, float]] = [
    ("SH-01", "虚构浅吃水货轮甲", 8.5),
    ("SH-02", "虚构集装箱轮乙", 12.0),
    ("SH-03", "虚构散货轮丙", 14.5),
    ("SH-04", "虚构深吃水油轮丁", 15.5),
    ("SH-05", "虚构大型集装箱轮戊", 15.0),
]

# (id, 姓名, 等级, [(班起分钟, 班止分钟), ...])
PILOT_ROWS: list[tuple[str, str, int, list[tuple[int, int]]]] = [
    (
        "P-01",
        "引航员甲（3 级）",
        3,
        [(22 * H, 28 * H), (46 * H, 52 * H)],
    ),
    (
        "P-02",
        "引航员乙（3 级）",
        3,
        [(22 * H, 28 * H), (46 * H, 52 * H)],
    ),
    (
        "P-03",
        "引航员丙（2 级）",
        2,
        [(6 * H, 18 * H), (30 * H, 42 * H)],
    ),
    (
        "P-04",
        "引航员丁（1 级）",
        1,
        [(8 * H, 20 * H), (32 * H, 44 * H)],
    ),
]

# (id, 船名)
BOAT_ROWS: list[tuple[str, str]] = [
    ("B-01", "接送艇一号"),
    ("B-02", "接送艇二号"),
]


def _build_ships() -> list[Ship]:
    return [Ship(id=sid, name=name, draft_meters=draft)
            for sid, name, draft in SHIP_ROWS]


def _build_pilots() -> list[Pilot]:
    pilots: list[Pilot] = []
    for pid, name, grade, windows in PILOT_ROWS:
        pilots.append(
            Pilot(
                id=pid,
                name=name,
                grade=grade,
                windows=[
                    WorkWindow(start_minute=lo, end_minute=hi) for lo, hi in windows
                ],
            )
        )
    return pilots


def _build_boats() -> list[Boat]:
    return [Boat(id=bid, name=name) for bid, name in BOAT_ROWS]


def seed(db: Session) -> None:
    """幂等写入虚构基础数据（每次构造全新 ORM 实例）。"""
    if db.scalar(select(Ship.id).limit(1)) is None:
        db.add_all(_build_ships())
    if db.scalar(select(Pilot.id).limit(1)) is None:
        db.add_all(_build_pilots())
    if db.scalar(select(Boat.id).limit(1)) is None:
        db.add_all(_build_boats())
    db.commit()
