"""离线虚构基础数据：船舶、引航员、接送艇、36 小时潮位样本。

与 README 声明一致：全部为虚构数据，不用于真实航行决策。
"""
from __future__ import annotations

from sqlalchemy import select

from .clock import HORIZON_MINUTES
from .db import SessionLocal, boats, create_all, engine, pilots, ships, tide_samples
from .tides import STATION_NAME, generate_tide_samples

SEED_SHIPS = [
    {"id": "PACIFIC", "name": "MV PACIFIC STAR",
     "vessel_type": "bulk", "draft_m": 4.0, "length_m": 270.0},
    {"id": "RIVERHAWK", "name": "MV RIVERHAWK",
     "vessel_type": "general", "draft_m": 3.0, "length_m": 180.0},
    {"id": "COASTER", "name": "MV COASTAL BREEZE",
     "vessel_type": "container", "draft_m": 2.5, "length_m": 150.0},
]

# work_windows 为相对 EPOCH（2026-09-24 12:00Z）的整数分钟。
SEED_PILOTS = [
    # 晚班（覆盖 23:00 跨午夜潮窗）
    {"id": "P001", "name": "陈岭（虚构）", "grade": "A",
     "work_windows": [[480, 960]]},   # 20:00 – 次日 04:00
    {"id": "P002", "name": "林舟（虚构）", "grade": "B",
     "work_windows": [[480, 960]]},
    # 白班（覆盖次日 13:00 潮窗）
    {"id": "P003", "name": "海岚（虚构）", "grade": "A",
     "work_windows": [[1080, 1680]]},  # 次日 06:00 – 16:00
]

# capacity=1 即全港同一时刻只允许一个接送艇靠泊作业窗口
SEED_BOATS = [
    {"id": "B01", "name": "引航艇 蓝鲸号（虚构）", "capacity": 1,
     "work_windows": [[0, HORIZON_MINUTES]]},
    {"id": "B02", "name": "引航艇 海鸥号（虚构）", "capacity": 1,
     "work_windows": [[0, HORIZON_MINUTES]]},
]


def seed(session=None) -> None:
    own = session is None
    session = session or SessionLocal()
    try:
        if session.execute(select(ships.c.id).limit(1)).first() is None:
            session.execute(ships.insert(), SEED_SHIPS)
        if session.execute(select(pilots.c.id).limit(1)).first() is None:
            session.execute(pilots.insert(), SEED_PILOTS)
        if session.execute(select(boats.c.id).limit(1)).first() is None:
            session.execute(boats.insert(), SEED_BOATS)
        if session.execute(select(tide_samples.c.id).limit(1)).first() is None:
            session.execute(tide_samples.insert(), generate_tide_samples())
        session.commit()
    finally:
        if own:
            session.close()


def init_db(eng=None) -> None:
    create_all(eng or engine)
    seed()


if __name__ == "__main__":  # pragma: no cover
    init_db()
    print(f"seeded: ships={len(SEED_SHIPS)} pilots={len(SEED_PILOTS)} "
          f"boats={len(SEED_BOATS)} station={STATION_NAME}")
