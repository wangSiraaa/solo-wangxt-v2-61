"""排班求解引擎：潮汐通航窗口 ∩ 引航员资质/工作时段 ∩ 接送艇容量。

CP-SAT 模型要点：
- 作业区间必须完整落在“潮汐窗口 ∩ 该引航员可工作时段”的某个窗口内；
- 同一引航员相邻作业之间计入 离船时间 + 转场时间（序列相关 setup）；
- 接送艇用累计容量约束：每个作业的登船/离船两段各占 1 个艇位；
- 目标优先级：已承诺任务必保（硬约束，不可行则放松诊断）→
  尽量多安排未承诺任务 → 最小化相对申报时刻的延误。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from app.config import settings
from app.domain.rules import pilot_qualified, setup_minutes
from app.domain.tides import intersect_windows


@dataclass(frozen=True)
class Job:
    declaration_id: int
    required_grade: int
    required_tide_cm: int
    direction: str
    duration: int
    requested_start: int
    committed: bool


@dataclass(frozen=True)
class PilotInfo:
    id: str
    grade: int
    windows: tuple[tuple[int, int], ...]


@dataclass
class SolvedTask:
    declaration_id: int
    pilot_id: str
    start: int
    end: int
    embark_start: int
    disembark_end: int
    delay: int


@dataclass
class SolvedConflict:
    declaration_id: int
    reason: str
    resource_kind: str
    resource_id: str | None = None
    blocking_declaration_id: int | None = None
    window_start: int | None = None
    window_end: int | None = None
    detail: str = ""


@dataclass
class SolveResult:
    tasks: list[SolvedTask] = field(default_factory=list)
    conflicts: list[SolvedConflict] = field(default_factory=list)
    assigned: set[int] = field(default_factory=set)


# ---------------------------------------------------------------------------
# 预处理：候选（引航员, 可行作业起始区间）
# ---------------------------------------------------------------------------


def _candidate_slots(
    job: Job, pilot: PilotInfo, tide_windows: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """返回该引航员执行该作业时，允许的作业起始时刻区间。

    作业 [s, s+duration] 必须整体落在 潮汐窗口 ∩ 工作窗口 内。
    """
    if not pilot_qualified(pilot.grade, job.required_grade):
        return []
    valid = intersect_windows(tide_windows, list(pilot.windows))
    slots: list[tuple[int, int]] = []
    for lo, hi in valid:
        s_lo = lo
        s_hi = hi - job.duration
        if s_lo <= s_hi:
            slots.append((s_lo, s_hi))
    return slots


# ---------------------------------------------------------------------------
# 建模
# ---------------------------------------------------------------------------

# 目标权重：承诺/未承诺任务的在场惩罚必须远大于总延误量
_W_COMMITTED = 10_000_000
_W_OPTIONAL = 1_000_000
_W_DELAY = 100  # 每分钟延误代价
# 同分时优先安排声明序号更小的任务（最大值为任务数，远小于延误权重）
_TIEBASE = 1000


def _build_model(
    jobs: list[Job],
    pilots: list[PilotInfo],
    tide_by_job: list[list[tuple[int, int]]],
    boat_capacity: int,
    force_committed: bool,
):
    m = cp_model.CpModel()
    H = settings.horizon_minutes
    embark = settings.embark_minutes
    disembark = settings.disembark_minutes

    pres: dict[int, cp_model.IntVar] = {}
    starts: dict[int, cp_model.IntVar] = {}
    job_intervals: dict[int, cp_model.IntervalVar] = {}
    embark_intervals: dict[int, cp_model.IntervalVar] = {}
    disembark_intervals: dict[int, cp_model.IntervalVar] = {}
    delays: dict[int, cp_model.IntVar] = {}
    # x[j, p, k]：任务 j 分配给引航员 p 的第 k 个候选窗
    x: dict[tuple[int, int, int], cp_model.IntVar] = {}
    slots_by_pair: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for j, job in enumerate(jobs):
        if job.committed and force_committed:
            pres[j] = m.new_constant(1)
        else:
            w = _W_COMMITTED if job.committed else _W_OPTIONAL
            pv = m.NewBoolVar(f"pres_{j}")
            pres[j] = pv
            _ = w  # 权重在目标函数中使用
        s = m.NewIntVar(0, H, f"s_{j}")
        e = m.NewIntVar(0, H + max(disembark, 0), f"e_{j}")
        m.Add(e == s + job.duration)
        starts[j] = s
        job_intervals[j] = m.NewOptionalIntervalVar(
            s, job.duration, e, pres[j], f"jobiv_{j}"
        )
        # 登船：作业开始前 embark 分钟，区间为 [s-embark, s)
        ek = m.NewIntVar(-embark, H, f"ek_{j}")
        m.Add(ek == s - embark)
        embark_intervals[j] = m.NewOptionalIntervalVar(
            ek, embark, s, pres[j], f"embiv_{j}"
        )
        # 离船：作业结束后 disembark 分钟，区间为 (e, e+disembark]
        de = m.NewIntVar(0, H + disembark, f"de_{j}")
        m.Add(de == e + disembark)
        disembark_intervals[j] = m.NewOptionalIntervalVar(
            e, disembark, de, pres[j], f"disiv_{j}"
        )
        # 与申报时刻的绝对偏移（提前或延误都计入，保证最优时刻唯一稳定：
        # 允许提前时贴申报时刻左侧；存在延误时贴窗口右侧）
        d = m.NewIntVar(0, settings.horizon_minutes, f"d_{j}")
        ge = m.NewBoolVar(f"ge_{j}")
        m.Add(s >= job.requested_start).OnlyEnforceIf(ge)
        m.Add(s < job.requested_start).OnlyEnforceIf(ge.Not())
        m.Add(d == s - job.requested_start).OnlyEnforceIf(ge, pres[j])
        m.Add(d == job.requested_start - s).OnlyEnforceIf(ge.Not(), pres[j])
        m.Add(d == 0).OnlyEnforceIf(pres[j].Not())
        delays[j] = d

        chosen = []
        for p, pilot in enumerate(pilots):
            slots = _candidate_slots(job, pilot, tide_by_job[j])
            if not slots:
                continue
            slots_by_pair[(j, p)] = slots
            for k, (lo, hi) in enumerate(slots):
                b = m.NewBoolVar(f"x_{j}_{p}_{k}")
                x[(j, p, k)] = b
                chosen.append(b)
                m.Add(s >= lo).OnlyEnforceIf(b)
                m.Add(s <= hi).OnlyEnforceIf(b)
        # 任务在场 ⇔ 恰好选中一个（引航员, 窗口）
        m.Add(sum(chosen) == 1).OnlyEnforceIf(pres[j])
        m.Add(sum(chosen) == 0).OnlyEnforceIf(pres[j].Not())

        # 已承诺任务锚定在申报时刻（在场时不得提前/延误）
        if job.committed:
            m.Add(s == job.requested_start).OnlyEnforceIf(pres[j])

    # ---- 同一引航员：作业不重叠 + 序列相关 setup（离船 + 转场）----
    for p, pilot in enumerate(pilots):
        for i, job_i in enumerate(jobs):
            if (i, p) not in slots_by_pair:
                continue
            for j, job_j in enumerate(jobs):
                if j <= i or (j, p) not in slots_by_pair:
                    continue
                gap = setup_minutes(job_i.direction, job_j.direction)
                b_ij = m.NewBoolVar(f"ord_{i}_{j}_{p}")
                b_ji = m.NewBoolVar(f"ord_{j}_{i}_{p}")
                xi = _pair_presence(m, x, i, p)
                xj = _pair_presence(m, x, j, p)
                both = m.NewBoolVar(f"both_{i}_{j}_{p}")
                m.AddBoolAnd([xi, xj]).OnlyEnforceIf(both)
                m.AddBoolOr([xi.Not(), xj.Not()]).OnlyEnforceIf(both.Not())
                # both ⇔ 恰好一个方向
                m.AddBoolOr([b_ij, b_ji]).OnlyEnforceIf(both)
                m.AddBoolAnd([b_ij.Not(), b_ji.Not()]).OnlyEnforceIf(both.Not())
                m.Add(starts[j] >= starts[i] + job_i.duration + gap).OnlyEnforceIf(
                    b_ij
                )
                m.Add(starts[i] >= starts[j] + job_j.duration + gap).OnlyEnforceIf(
                    b_ji
                )

    # ---- 接送艇容量：登船段与离船段都占用艇位 ----
    all_boat_intervals = list(embark_intervals.values()) + list(
        disembark_intervals.values()
    )
    m.AddCumulative(all_boat_intervals, [1] * len(all_boat_intervals), boat_capacity)

    # ---- 目标 ----
    n = len(jobs)
    terms = []
    for j, job in enumerate(jobs):
        w = _W_COMMITTED if job.committed else _W_OPTIONAL
        # 在场优先级最高；同等情况下保留下标更小（申报更早）的任务
        terms.append((w + (n - j) * _TIEBASE, pres[j].Not()))
        terms.append((_W_DELAY, delays[j]))
    m.Minimize(sum(c * var for c, var in terms))

    return m, x, pres, starts, delays


def _pair_presence(m: cp_model.CpModel, x, j: int, p: int) -> cp_model.IntVar:
    keys = [k for (jj, pp, k) in x.keys() if jj == j and pp == p]
    bvars = [x[(j, p, k)] for k in keys]
    v = m.NewBoolVar(f"pair_{j}_{p}")
    m.AddMaxEquality(v, bvars)
    return v


def _solver():
    s = cp_model.CpSolver()
    # 确定性：单线程 + 固定随机种子
    s.parameters.num_search_workers = 1
    s.parameters.random_seed = settings.solver_seed
    s.parameters.max_time_in_seconds = settings.solver_time_limit_seconds
    return s


def solve(
    jobs: list[Job],
    pilots: list[PilotInfo],
    tide_windows_by_req: dict[int, list[tuple[int, int]]],
    boat_capacity: int,
) -> SolveResult:
    """返回排班结果；不可行的任务带结构化冲突说明。"""
    tide_by_job = [tide_windows_by_req[job.required_tide_cm] for job in jobs]

    result = SolveResult()

    def _run(force_committed: bool) -> tuple[str, tuple]:
        m, x, pres, starts, delays = _build_model(
            jobs, pilots, tide_by_job, boat_capacity, force_committed
        )
        solver = _solver()
        status = solver.Solve(m)
        return status, (solver, x, pres, starts, delays)

    status, packed = _run(force_committed=True)
    solver_name = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solver_name, x, pres, starts, delays = packed
    else:
        # 承诺任务硬约束导致整体不可行：放松承诺后再解，用于诊断冲突
        status, packed = _run(force_committed=False)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            solver_name, x, pres, starts, delays = packed
        else:
            # 理论上不会发生（全部可选即空解），保底返回全冲突
            for job in jobs:
                result.conflicts.append(
                    SolvedConflict(
                        job.declaration_id,
                        "no_pilot_window",
                        "window",
                        detail="模型整体不可行",
                    )
                )
            return result

    embark = settings.embark_minutes
    disembark = settings.disembark_minutes
    assignment: dict[int, tuple[str, int]] = {}

    for j, job in enumerate(jobs):
        if solver_name.Value(pres[j]) == 0:
            continue
        for (jj, p, k), b in x.items():
            if jj == j and solver_name.Value(b) == 1:
                assignment[j] = (pilots[p].id, k)
                break
        s = solver_name.Value(starts[j])
        result.assigned.add(job.declaration_id)
        result.tasks.append(
            SolvedTask(
                declaration_id=job.declaration_id,
                pilot_id=assignment[j][0],
                start=s,
                end=s + job.duration,
                embark_start=s - embark,
                disembark_end=s + job.duration + disembark,
                delay=max(0, s - job.requested_start),
            )
        )
    scheduled_by_index = {
        jj: next(
            (t for t in result.tasks if t.declaration_id == jobs[jj].declaration_id),
            None,
        )
        for jj in range(len(jobs))
    }
    for j, job in enumerate(jobs):
        if job.declaration_id in result.assigned:
            continue
        result.conflicts.append(
            _diagnose(
                job,
                jobs,
                pilots,
                tide_by_job[j],
                boat_capacity,
                scheduled_by_index,
            )
        )
    result.tasks.sort(key=lambda t: (t.start, t.declaration_id))
    result.conflicts.sort(key=lambda c: c.declaration_id)
    return result


# ---------------------------------------------------------------------------
# 冲突诊断
# ---------------------------------------------------------------------------


def _diagnose(
    job: Job,
    jobs: list[Job],
    pilots: list[PilotInfo],
    tide_windows: list[tuple[int, int]],
    boat_capacity: int,
    scheduled: dict[int, SolvedTask | None],
) -> SolvedConflict:
    # 1) 无合格引航员
    qualified = [p for p in pilots if pilot_qualified(p.grade, job.required_grade)]
    if not qualified:
        return SolvedConflict(
            job.declaration_id,
            "no_qualified_pilot",
            "qualification",
            detail=f"没有等级 ≥ {job.required_grade} 的引航员",
        )

    # 2) 无满足潮高的通航窗口
    if not tide_windows:
        return SolvedConflict(
            job.declaration_id,
            "no_tide_window",
            "tide",
            detail=f"作业周期内没有潮高 ≥ {job.required_tide_cm}cm 的窗口",
        )

    # 3) 合格引航员的工作时段与“潮汐 ∩ 可容纳作业时长”的窗口无交集
    usable_slots: dict[str, list[tuple[int, int]]] = {}
    for pilot in qualified:
        slots = _candidate_slots(
            job, pilot, tide_windows
        )
        if slots:
            usable_slots[pilot.id] = slots
    if not usable_slots:
        return SolvedConflict(
            job.declaration_id,
            "no_pilot_window",
            "window",
            detail="合格引航员的可工作时段与通航窗口无交集或窗口短于作业时长",
        )

    # 承诺任务只允许锚定在申报时刻：检查该时刻为何不可行
    anchor = job.requested_start
    target_pids = {
        pid for pid, slots in usable_slots.items()
        if any(lo <= anchor <= hi for lo, hi in slots)
    }
    if job.committed:
        if not target_pids:
            first = min(lo for slots in usable_slots.values() for lo, _ in slots)
            last = max(hi for slots in usable_slots.values() for _, hi in slots)
            return SolvedConflict(
                job.declaration_id,
                "anchor_outside_window",
                "window",
                window_start=first,
                window_end=last,
                detail="承诺时刻落在所有合格引航员的通航/工作窗口之外",
            )
        blocker = _blocker_at(
            job, jobs, scheduled, anchor, boat_capacity, target_pids
        )
        if blocker:
            return blocker
        return SolvedConflict(
            job.declaration_id,
            "combined_capacity",
            "boat",
            detail="承诺时刻单资源均有余量，但联合排程不可行（序列 setup 与艇位共同制约）",
        )

    # 未承诺任务：扫描全部可行起始时刻，收集持续挡路的资源
    blocker = _scan_blockers(
        job, jobs, scheduled, usable_slots, boat_capacity
    )
    if blocker:
        return blocker
    return SolvedConflict(
        job.declaration_id,
        "combined_capacity",
        "window",
        detail="存在可行窗口但被引航员序列与接送艇容量联合制约",
    )


def _overlap_half_open(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _pilot_conflicts(
    job: Job,
    start: int,
    pilot_id: str,
    jobs: list[Job],
    scheduled: dict[int, "SolvedTask | None"],
) -> int | None:
    """指定引航员在 start 执行 job 是否与其已排任务冲突（含 setup）。

    返回发生冲突的已排申报 id；None 表示该引航员空闲可用。
    """
    for jj, task in scheduled.items():
        if task is None or task.pilot_id != pilot_id:
            continue
        other = jobs[jj]
        if start >= task.start:
            # job 排在 other 之后：other 的离船+转场必须先完成
            if start < task.end + setup_minutes(other.direction, job.direction):
                return other.declaration_id
        else:
            # job 排在 other 之前
            if task.start < start + job.duration + setup_minutes(
                job.direction, other.direction
            ):
                return other.declaration_id
    return None


def _boat_saturated(
    job: Job,
    start: int,
    boat_capacity: int,
    jobs: list[Job],
    scheduled: dict[int, "SolvedTask | None"],
) -> tuple[int | None, int | None]:
    """检查新作业的登船段/离船段是否会撑破艇容量。

    返回 (饱和时刻, 挡路申报id)；均不饱与时返回 (None, None)。
    """
    embark = settings.embark_minutes
    disembark = settings.disembark_minutes
    new_segments = [
        (start - embark, start),
        (start + job.duration, start + job.duration + disembark),
    ]
    old_segments: list[tuple[int, int, int]] = []
    for jj, task in scheduled.items():
        if task is None:
            continue
        old_segments.append((task.embark_start, task.start, jobs[jj].declaration_id))
        old_segments.append(
            (task.end, task.disembark_end, jobs[jj].declaration_id)
        )

    for n0, n1 in new_segments:
        # 在与新段重叠的区间内扫描旧段并发数
        cuts = {n0, n1}
        for b0, b1, _ in old_segments:
            if _overlap_half_open(n0, n1, b0, b1):
                cuts.add(max(n0, b0))
        for t in sorted(cuts):
            if not (n0 <= t < n1):
                continue
            count = 0
            blocker = None
            for b0, b1, decl_id in old_segments:
                if b0 <= t < b1:
                    count += 1
                    blocker = decl_id
            if count + 1 > boat_capacity:
                return t, blocker
    return None, None


def _blocker_at(
    job: Job,
    jobs: list[Job],
    scheduled: dict[int, "SolvedTask | None"],
    start: int,
    boat_capacity: int,
    eligible_pilot_ids: set[str],
) -> SolvedConflict | None:
    """检查作业在指定开始时刻被哪类资源挡住（引航员优先于艇）。"""
    free_pilot = None
    blocking_decl = None
    blocking_pilot = None
    for pid in sorted(eligible_pilot_ids):
        decl = _pilot_conflicts(job, start, pid, jobs, scheduled)
        if decl is None:
            free_pilot = pid
            break
        blocking_decl = decl
        blocking_pilot = pid

    sat_at, boat_blocker = _boat_saturated(
        job, start, boat_capacity, jobs, scheduled
    )

    if free_pilot is None and blocking_decl is not None:
        return SolvedConflict(
            job.declaration_id,
            "pilot_busy",
            "pilot",
            resource_id=blocking_pilot,
            blocking_declaration_id=blocking_decl,
            window_start=start,
            window_end=start + job.duration,
            detail="所有合格引航员在该时段均有未完成任务（含离船/转场时间）",
        )
    if sat_at is not None:
        return SolvedConflict(
            job.declaration_id,
            "boat_capacity",
            "boat",
            resource_id="fleet",
            blocking_declaration_id=boat_blocker,
            window_start=sat_at,
            window_end=sat_at + 1,
            detail=f"接送艇容量 {boat_capacity} 在该时刻占满（登船/离船段并发）",
        )
    return None


def _scan_blockers(
    job: Job,
    jobs: list[Job],
    scheduled: dict[int, "SolvedTask | None"],
    usable_slots: dict[str, list[tuple[int, int]]],
    boat_capacity: int,
) -> SolvedConflict | None:
    """扫描全部可行起始时刻（10 分钟网格）：

    若每个时刻都被挡住，则返回一个代表性冲突；否则返回 None。
    """
    points: set[int] = set()
    for slots in usable_slots.values():
        for lo, hi in slots:
            points.update(range(lo, hi + 1, 10))
            points.add(hi)
    points.add(job.requested_start)

    example: SolvedConflict | None = None
    for s in sorted(points):
        # 只把在该时刻确实能执行作业的引航员计入“合格且在岗”集合
        eligible = {
            pid
            for pid, slots in usable_slots.items()
            if any(lo <= s <= hi for lo, hi in slots)
        }
        if not eligible:
            continue
        blocker = _blocker_at(job, jobs, scheduled, s, boat_capacity, eligible)
        if blocker is None:
            return None
        if example is None:
            example = blocker
    return example

