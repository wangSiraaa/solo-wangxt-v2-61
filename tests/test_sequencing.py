"""顺序约束不只是区间相交：开始间隔 < 离船+转场所需时间同样不可行。"""
from app.model import BundleInput, JobInput, ResourceInput, solve

# 同一引航员/艇在 ANCH 做完 60 分钟任务后赶到 INB：
# 需要 开始间隔 >= 作业60 + 离船10 + ANCH→INB航行15 = 85 分钟。
# 两个作业时间区间本身可以完全不重叠（间隔 >= 60），但仍可能转场不过来。


def _bundle(start_gap):
    s1 = 500
    s2 = s1 + start_gap
    j1 = JobInput(ref="J1", location="ANCH", duration=60, earliest=s1,
                  latest_start=s1, tide_windows=[(s1, s1)],
                  eligible_pilots=["P1"], eligible_boat_units=["B#0"],
                  forced=True)
    j2 = JobInput(ref="J2", location="INB", duration=60, earliest=s2,
                  latest_start=s2, tide_windows=[(s2, s2)],
                  eligible_pilots=["P1"], eligible_boat_units=["B#0"],
                  forced=True)
    return BundleInput(
        jobs=[j1, j2],
        pilots=[ResourceInput(id="P1", windows=[(0, 2000)])],
        boat_units=[ResourceInput(id="B#0", windows=[(0, 2000)])],
        horizon=2160,
    )


def test_non_overlapping_but_too_tight_is_infeasible():
    # 开始间隔 70 分钟：区间不重叠（>60），但小于离船+转场所需 85 分钟
    res = solve(_bundle(70))
    assert res.relaxed is True
    # 只有一个资源（1 引航员 + 1 艇单元），最多落实一条
    assert len(res.assignments) == 1
    assert set(res.unserved) == {"J1", "J2"} - {a.ref for a in res.assignments}
    assert res.unserved_forced


def test_enough_gap_both_jobs_served():
    res = solve(_bundle(90))
    assert res.feasible and not res.relaxed
    assert res.unserved == []
    assert [a.ref for a in res.assignments] == ["J1", "J2"]
    a1, a2 = res.assignments
    assert a2.start - a1.start >= 60 + 10 + 15   # 作业 + 离船 + ANCH→INB
