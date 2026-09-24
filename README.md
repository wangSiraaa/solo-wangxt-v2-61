# 引航站排班 API（离线虚构数据）

把 **船舶申报、潮汐通航窗口、引航员资质/可工作时段、接送艇容量** 放进同一个排班
接口。后端为 FastAPI + OR-Tools（CP-SAT）+ PostgreSQL（测试用 SQLite）。

> ⚠️ 潮汐曲线、船舶资料、吃水规则与接送时间全部是**离线虚构数据**，
> 本服务不提供真实航行决策。

## 领域规则（均为示例）

- **吃水分档**（`app/domain/rules.py`）：吃水 ≤10m → 等级 1 / 潮高 ≥0cm；
  ≤13m → 等级 2 / ≥300cm；≤16m → 等级 3 / ≥450cm。
- **潮汐**（`app/domain/tides.py`）：周期 12 小时的确定性正弦曲线，10 分钟采样，
  线性插值得到满足最低潮高的闭区间窗口。潮峰落在基准日 00:00，天然形成
  **跨午夜潮窗**。
- **通航窗口** = 潮汐窗口 ∩ 引航员工作时段，作业必须**整体**落在某个窗口内。
- **离船与转场**：同一引航员相邻作业之间强制
  `离船 25 分钟 +（进港/出港接送点不同时）转场 10 分钟`，
  而不是只判断两个作业时间段是否相交。进港在外海点登船、出港在港内点登船。
- **接送艇容量**：每个作业的登船段（作业前 20 分钟）和离船段（作业后 25 分钟）
  各占 1 个艇位，全部接送段上用 cumulative 容量约束。
- **目标优先级**：
  1. 已承诺（`committed=true`）任务锚定申报时刻、优先保证在场；
  2. 尽量多安排未承诺任务；
  3. 最小化相对申报时刻的偏移（延误）。
- 无法满足的申报返回**冲突资源与时间段**：
  `no_qualified_pilot` / `no_tide_window` / `no_pilot_window` /
  `anchor_outside_window` / `pilot_busy` / `boat_capacity` 等，
  含挡路的申报、引航员/艇队与起止分钟。
- **锁定与修订**：计划创建后为 `draft`，确认后变 `locked`（正式）。
  正式计划不可直接改，只能 `POST /plans/{id}/revisions` 生成新版本；
  新版本确认后旧版本转为 `superseded`。确认接口与申报接口均**幂等**。

## 运行

### Docker Compose（PostgreSQL）

```bash
docker compose up --build
# http://localhost:8000/docs  （启动时自动建表并播种虚构数据）
```

### 本地（SQLite）

```bash
pip install -r requirements.txt
DATABASE_URL="sqlite+pysqlite:///./pilot_schedule.db" \
  uvicorn app.main:app --reload
```

端到端请求样例：

```bash
bash examples/requests.sh
```

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/declarations` | 船舶申报（`idempotency_key` 幂等） |
| GET  | `/tides/windows?ship_id=SH-04` 或 `?required_cm=450` | 虚构通航窗口 |
| GET  | `/tides/points` | 潮汐采样点 |
| GET  | `/ships` `/pilots` `/boats` | 虚构基础资料 |
| PATCH | `/pilots/{id}?active=false` | 停用引航员 |
| POST | `/plans` | 对一组申报求解，返回任务 + 冲突（草稿） |
| POST | `/plans/{id}/confirm` | 锁定正式计划（重复调用幂等） |
| POST | `/plans/{id}/revisions` | 对锁定计划做显式修订，生成新草稿 |
| POST | `/plans/{id}/revisions/confirm` | 确认修订（旧版转 superseded） |

### 申报样例

```json
{
  "idempotency_key": "DECL-DEMO-0001",
  "ship_id": "SH-04",
  "direction": "inbound",
  "duration_minutes": 120,
  "requested_start": "2026-09-25T00:30:00",
  "committed": false
}
```

### 计划冲突响应样例（艇位不足）

```json
{
  "declaration_id": 2,
  "reason": "boat_capacity",
  "resource_kind": "boat",
  "resource_id": "fleet",
  "blocking_declaration_id": 1,
  "window_start": "2026-09-24T23:00:00",
  "window_end": "2026-09-24T23:01:00",
  "window_start_minute": 1380,
  "window_end_minute": 1381,
  "detail": "接送艇容量 1 在该时刻占满（登船/离船段并发）"
}
```

## 确定性测试

CP-SAT 固定单线程 + 随机种子（`SOLVER_SEED=42`），时间均为相对固定基准日
`2026-09-24` 的整数分钟偏移。

```bash
python3 -m pytest -q
```

覆盖场景：

- `test_cross_midnight.py`：**跨午夜潮窗**——450cm 窗 [1334,1546]
  （22:14–次日 01:46），00:30 起 120 分钟的作业提前到 23:46 开始、跨过午夜；
- `test_boat_shortage.py`：**合格人员充足但接送艇不足**——两名 3 级引航员都
  空闲，单艇时一条承诺作业被挡并返回艇位冲突，双艇时两条零延误并行；
- `test_idempotency.py`：**同一请求重复确认不增加第二个有效任务**——申报幂等键、
  计划重复确认、锁定后再排被 409 拒绝、显式修订后旧版 `superseded` 且全局只有
  一个 locked 任务；
- `test_pilot_setup.py`：**离船 + 转场时间**——同引航员两作业间隔被强制为
  25 + 10 = 35 分钟；承诺间隔不足时返回 `pilot_busy`（含挡路申报与时间段）。
