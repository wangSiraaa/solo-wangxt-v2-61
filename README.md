# 引航站排班 API（离线虚构数据）

> **免责声明**：本服务的潮汐表、船舶资料与吃水规则全部是**离线虚构数据**，
> 仅用于排班 API 与 OR-Tools 约束建模的演示，**不提供任何真实航行决策支持**。

把三件事放进同一个排班模型：

1. **船舶申报**（吃水、作业时长、期望起止时间、是否已承诺）；
2. **潮汐条件**（示例吃水规则 + 潮位区间 → 允许通航窗口）；
3. **引航员接送安排**（资质等级、可工作时段、接送艇容量）。

所有约束在同一个 CP-SAT 模型里**求交**；引航员上一任务结束后计入
**离船时间 + 地点间转场时间**，接送艇同样计入航行时间，而不是只判断两个
作业时间是否重叠。

- 求解目标：先保证已承诺（committed）任务全部落实，再最小化总延误；
- 落实不了的任务返回**冲突资源与时间段**（潮窗 / 引航员 / 接送艇）；
- 计划 `draft`（草稿）→ `locked`（正式锁定）；锁定后**只允许显式修订**
  （cancel / add / reschedule），重复确认幂等，不会产生第二个有效任务。

## 技术栈

FastAPI · OR-Tools CP-SAT · SQLAlchemy Core · PostgreSQL（容器默认）/ SQLite（测试）。

## 快速开始（Docker + PostgreSQL）

```bash
docker compose up --build
# API: http://localhost:8000  文档: /docs
```

compose 会自动建表并写入离线虚构基础数据（船舶 / 引航员 / 接送艇 / 36 小时潮位）。

## 本地运行

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="sqlite:///./pilot.db"     # 可选，默认就是本地 sqlite
python -m app.bootstrap                        # 建表 + 写入虚构数据
uvicorn app.main:app --reload
```

## 请求样例

```bash
# 跨午夜潮窗：吃水 4.0m 的散货船，期望 20:00 起，只有 23:30 前后的潮窗满足
curl -s localhost:8000/plans \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-cross-midnight-001' \
  -d @examples/plan_cross_midnight.json | jq

# 两条船同一潮窗（人员够、艇也够的对照样例）
curl -s localhost:8000/plans \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-two-ships-001' \
  -d @examples/plan_two_ships.json | jq
```

确认 / 修订流程：

```bash
PID=<上一步返回的 plan_id>

# 正式锁定（重复调用幂等，不增加第二个有效任务）
curl -s -X POST localhost:8000/plans/$PID/confirm | jq
curl -s -X POST localhost:8000/plans/$PID/confirm | jq   # reused=true

# 锁定后只能走显式修订
curl -s -X POST localhost:8000/plans/$PID/revisions \
  -H 'Content-Type: application/json' -d @examples/revision.json | jq
```

## 确定性测试

```bash
pytest -q
```

覆盖：

| 测试 | 覆盖点 |
| --- | --- |
| `test_tide_cross_midnight.py` | 跨午夜潮窗：作业必须落在 23:30 前后的潮窗且跨过 00:00，解唯一确定 |
| `test_boat_shortage.py` | 合格引航员充足但接送艇不足：第二条任务无法落实，冲突指向艇资源而非人员 |
| `test_sequencing.py` | 时间不重叠但间隙小于「离船+转场」时仍判冲突 |
| `test_api_lifecycle.py` | 重复提交 / 重复确认幂等、锁定后拒绝非修订写入、显式修订的成功与 409 |

求解器固定 `workers=1, random_seed=42`，时间轴采用 10 分钟离散网格，
保证同输入同输出。

## 关键建模约定（虚构规则）

- 潮位每 10 分钟采样一次；要求 `潮位 >= 吃水 + 富余水深(0.5m) - 海图基准水深(2.5m)`，
  即 `潮位 >= 吃水 - 2.0m`；作业整个区间必须落在满足潮位的连续窗口内。
- 引航员资质：吃水 ≥ 4.0m 或船长 ≥ 250m 需 A 级，否则 B 级；A 可覆盖 B。
- 引航员执行相邻任务需满足
  `下一任务开始 >= 上一任务结束 + 离船10分钟 + 两端地点间航行时间`。
- 接送艇靠泊接人服务时长 10 分钟；同一艇单元连续服务需
  `下一靠泊开始 >= 上一靠泊开始 + 地点间航行时间 + 10分钟`；
  容量按并行艇单元建模（capacity=1 即同一时刻全港只能有 1 个接艇靠泊窗口；
  艇在 10 分钟送引航员登轮后即离开，不等作业结束）。
- 时间全部为 UTC ISO8601，求解内部换算为相对固定起点
  `2026-09-24T12:00:00Z` 的整数分钟。
