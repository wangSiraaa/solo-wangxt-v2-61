"""OR-Tools CP-SAT 排班求解器。

四类约束放在同一个模型里求交：

1. 潮汐/吃水窗口（整个作业区间必须落在某个连续潮窗内，可跨午夜）；
2. 引航员资质等级与可工作时段；
3. 接送艇容量（capacity=C 的艇拆成 C 个并行艇单元）与可工作时段；
4. 顺序约束（带时间维度的资源回路 ``AddCircuit``）：
   - 引航员相邻任务：下一开始 >= 上一结束 + 离船 10 分钟 + 地点间航行；
   - 接送艇相邻服务：下一靠泊 >= 上一靠泊 + 服务 10 分钟 + 地点间航行；
   不是简单判断两个作业时间区间是否相交。

两阶段：先强制 committed 任务全部 present；不可行则放宽为可选重解，
把落实不了的承诺任务交给 conflicts.py 给出冲突资源与时间段。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .domain import BASE, BOAT_SERVICE_MINUTES, DISEMBARK_MINUTES, travel_minutes

UNSERVED_PENALTY = 100_000
MAX_DELAY = 24 * 60


@dataclass
class JobInput:
    ref: str
    location: str
    duration: int                          # 引航作业分钟
    earliest: int
    latest_start: int
    tide_windows: list[tuple[int, int]]
    eligible_pilots: list[str] = field(default_factory=list)
    eligible_boat_units: list[str] = field(default_factory=list)
    forced: bool = False
    fixed_pilot: str | None = None
    fixed_boat: str | None = None
    fixed_start: int | None = None


@dataclass
class ResourceInput:
    id: str
    windows: list[tuple[int, int]]         # 可工作/可出勤时段（闭区间）


@dataclass
class BundleInput:
    jobs: list[JobInput]
    pilots: list[ResourceInput]
    boat_units: list[ResourceInput]
    horizon: int


@dataclass
class Assignment:
    ref: str
    pilot_id: str
    boat_unit_id: str
    start: int
    delay: int
    forced: bool


@dataclass
class SolveResult:
    feasible: bool
    relaxed: bool
    assignments: list[Assignment]
    unserved: list[str]
    unserved_forced: list[str]
    total_delay: int
    structurally_infeasible: list[str]


def solve(bundle: BundleInput) -> SolveResult:
    jobs = bundle.jobs
    structurally = sorted({
        j.ref for j in jobs
        if not j.tide_windows or not j.eligible_pilots or not j.eligible_boat_units
    })
    forced_structural = sorted(
        j.ref for j in jobs if j.forced and j.ref in structurally)

    # 已承诺且结构不可行的任务在任何阶段都不可能落实，直接进入放宽阶段
    result = None if forced_structural else _run(bundle, relax=False)
    if result is not None:
        result.structurally_infeasible = structurally
        return result
    result = _run(bundle, relax=True)
    if result is None:
        return SolveResult(False, True, [], [j.ref for j in jobs],
                           [j.ref for j in jobs if j.forced], 0, structurally)
    result.structurally_infeasible = structurally
    return result


# ---------------------------------------------------------------------------

def _run(bundle: BundleInput, *, relax: bool) -> SolveResult | None:
    m = cp_model.CpModel()
    jobs = bundle.jobs
    horizon = bundle.horizon

    starts, served, delays = {}, {}, {}
    for j in jobs:
        lo = max(0, j.earliest)
        if j.tide_windows:
            lo = max(lo, min(w[0] for w in j.tide_windows))
            hi = min(j.latest_start, max(w[1] for w in j.tide_windows))
        else:
            hi = j.latest_start
        x = m.NewIntVar(lo, min(hi, horizon), f"start_{j.ref}")
        starts[j.ref] = x

        if j.fixed_start is not None:
            m.Add(x == j.fixed_start)
        else:
            y = m.NewBoolVar(f"served_{j.ref}")
            served[j.ref] = y
            if j.forced and not relax:
                m.Add(y == 1)
            if not j.tide_windows or not j.eligible_pilots or not j.eligible_boat_units:
                # 结构不可行：无潮窗或无合格资源，直接禁止服务（两阶段都会落空，
                # 交由冲突解释返回原因），避免空 AtLeastOne 导致整模型无解
                m.Add(y == 0)
            else:
                # 潮窗求交：被服务时开始时间必须落在某个允许开始区间
                tw_bools = []
                for k, (a, b) in enumerate(j.tide_windows):
                    bv = m.NewBoolVar(f"tide_{j.ref}_{k}")
                    m.AddLinearConstraint(x, a, b).OnlyEnforceIf(bv)
                    tw_bools.append(bv)
                m.AddAtLeastOne(tw_bools).OnlyEnforceIf(y)

        d = m.NewIntVar(0, MAX_DELAY, f"delay_{j.ref}")
        m.Add(d >= x - j.earliest)
        delays[j.ref] = d

    presence: dict[tuple[str, str, str], cp_model.IntVar] = {}
    _build_routes(m, "p", bundle, jobs, starts, presence)
    _build_routes(m, "b", bundle, jobs, starts, presence)

    # 每个新任务恰好由一名合格引航员 / 一个艇单元承担（当且仅当被服务）
    for j in jobs:
        if j.fixed_start is not None:
            continue
        ps = [presence[("p", p, j.ref)] for p in j.eligible_pilots]
        bs = [presence[("b", b, j.ref)] for b in j.eligible_boat_units]
        m.Add(sum(ps) == served[j.ref])
        m.Add(sum(bs) == served[j.ref])

    terms = []
    for j in jobs:
        if j.fixed_start is None:
            terms.append(served[j.ref] * -UNSERVED_PENALTY)
            terms.append(delays[j.ref])
    m.Minimize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    assignments, unserved = [], []
    for j in jobs:
        if j.fixed_start is not None:
            assignments.append(Assignment(j.ref, j.fixed_pilot, j.fixed_boat,
                                          j.fixed_start, 0, True))
            continue
        if solver.Value(served[j.ref]) == 1:
            pilot = next(p for p in j.eligible_pilots
                         if solver.Value(presence[("p", p, j.ref)]) == 1)
            boat = next(b for b in j.eligible_boat_units
                        if solver.Value(presence[("b", b, j.ref)]) == 1)
            assignments.append(Assignment(j.ref, pilot, boat,
                                          solver.Value(starts[j.ref]),
                                          solver.Value(delays[j.ref]), j.forced))
        else:
            unserved.append(j.ref)

    forced_ids = {j.ref for j in jobs if j.forced}
    return SolveResult(
        feasible=True,
        relaxed=relax,
        assignments=sorted(assignments, key=lambda a: a.start),
        unserved=sorted(unserved),
        unserved_forced=sorted(r for r in unserved if r in forced_ids),
        total_delay=sum(a.delay for a in assignments),
        structurally_infeasible=[],
    )


# ---------------------------------------------------------------------------

def _build_routes(m, kind, bundle, jobs, starts, presence):
    """为每一名引航员 / 每一个艇单元建立一条带时间维度的回路。"""
    resources = bundle.pilots if kind == "p" else bundle.boat_units

    for res in resources:
        rid = res.id
        token = rid.replace("#", "_")

        # 该资源回路中的节点：fixed 任务恒在；新任务可选
        nodes: list[object] = []
        for j in jobs:
            if j.fixed_start is not None:
                fixed_id = j.fixed_pilot if kind == "p" else j.fixed_boat
                if fixed_id == rid:
                    nodes.append((j, False))
            else:
                eligible = j.eligible_pilots if kind == "p" else j.eligible_boat_units
                if rid in eligible:
                    nodes.append((j, True))
        if not nodes:
            continue

        arcs: list[tuple[int, int, cp_model.IntVar]] = []
        empty = m.NewBoolVar(f"empty_{kind}_{token}")
        arcs.append((0, 0, empty))

        skip_var, member_var = {}, {}
        for i, (j, optional) in enumerate(nodes, start=1):
            if optional:
                sk = m.NewBoolVar(f"skip_{kind}_{token}_{j.ref}")
                arcs.append((i, i, sk))
                pv = m.NewBoolVar(f"present_{kind}_{token}_{j.ref}")
                m.Add(pv + sk == 1)
                skip_var[j.ref] = sk
            else:
                pv = m.NewConstant(1)
            presence[(kind, rid, j.ref)] = pv

            z_list = _window_membership(m, kind, token, res, j, pv,
                                        starts[j.ref])
            member_var[j.ref] = z_list

            # depot -> 首任务
            a_in = m.NewBoolVar(f"din_{kind}_{token}_{j.ref}")
            arcs.append((0, i, a_in))
            first_feas = []
            for k, (wlo, whi) in enumerate(res.windows):
                f = m.NewBoolVar(f"first_{kind}_{token}_{j.ref}_{k}")
                m.Add(f <= a_in)
                m.Add(f <= z_list[k])
                m.Add(starts[j.ref] >= wlo + travel_minutes(BASE, j.location)
                      ).OnlyEnforceIf(f)
                first_feas.append(f)
            m.Add(a_in <= sum(first_feas))

            # 末任务 -> depot：回基地航行只需落在规划时域内
            a_out = m.NewBoolVar(f"dout_{kind}_{token}_{j.ref}")
            arcs.append((i, 0, a_out))
            m.Add(starts[j.ref] + _tail_minutes(kind, j)
                  + travel_minutes(j.location, BASE) <= bundle.horizon
                  ).OnlyEnforceIf(a_out)

        # 任务 -> 任务：离船/服务 + 转场，且两个任务须归属同一工时窗
        for i, (ji, _o1) in enumerate(nodes, start=1):
            for jj_idx, (jn, _o2) in enumerate(nodes, start=1):
                if i == jj_idx:
                    continue
                lag = _inter_lag(kind, ji, jn)
                lit = m.NewBoolVar(f"arc_{kind}_{token}_{ji.ref}_{jn.ref}")
                m.Add(starts[jn.ref] >= starts[ji.ref] + lag).OnlyEnforceIf(lit)
                same_window = []
                for k in range(len(res.windows)):
                    q = m.NewBoolVar(f"samew_{kind}_{token}_{ji.ref}_{jn.ref}_{k}")
                    m.Add(q <= member_var[ji.ref][k])
                    m.Add(q <= member_var[jn.ref][k])
                    same_window.append(q)
                m.Add(lit <= sum(same_window))
                arcs.append((i, jj_idx, lit))

        m.AddCircuit(arcs)


def _tail_minutes(kind, j) -> int:
    if kind == "p":
        return j.duration + DISEMBARK_MINUTES
    return BOAT_SERVICE_MINUTES


def _inter_lag(kind, ji, jn) -> int:
    if kind == "p":
        return ji.duration + DISEMBARK_MINUTES + travel_minutes(ji.location, jn.location)
    return BOAT_SERVICE_MINUTES + travel_minutes(ji.location, jn.location)


def _window_membership(m, kind, token, res, j, present, x):
    """节点 present 时必须归属某个工时窗；作业全程落在该窗内。"""
    z_list = []
    for k, (wlo, whi) in enumerate(res.windows):
        z = m.NewBoolVar(f"win_{kind}_{token}_{j.ref}_{k}")
        tail = _tail_minutes(kind, j)
        m.Add(x >= wlo).OnlyEnforceIf(z)
        m.Add(x + tail <= whi).OnlyEnforceIf(z)
        z_list.append(z)
    m.Add(sum(z_list) >= present)
    return z_list
