#!/usr/bin/env bash
# 端到端请求样例（离线虚构数据，不用于真实航行决策）
# 用法: BASE=http://localhost:8000 examples/requests.sh
set -euo pipefail
BASE="${BASE:-http://localhost:8000}"
j() { python3 -m json.tool; }

echo "== 0. 基础资料 =="
curl -s "$BASE/ships" | j
curl -s "$BASE/pilots" | j
curl -s "$BASE/boats" | j

echo "== 1. 跨午夜潮窗：SH-04（等级3 / 需 450cm）00:30 申报 120 分钟 =="
curl -s "$BASE/tides/windows?ship_id=SH-04" | j
D1=$(curl -s -X POST "$BASE/declarations" -H 'Content-Type: application/json' \
  -d '{"idempotency_key":"DEMO-1","ship_id":"SH-04","direction":"inbound",
       "duration_minutes":120,"requested_start":"2026-09-25T00:30:00","committed":false}' \
  | tee /dev/stderr | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

echo "== 2. 合格人员充足但接送艇不足：两条承诺作业同锚 23:20 =="
D2=$(curl -s -X POST "$BASE/declarations" -H 'Content-Type: application/json' \
  -d '{"idempotency_key":"DEMO-2","ship_id":"SH-03","direction":"inbound",
       "duration_minutes":60,"requested_start":"2026-09-24T23:20:00","committed":true}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
D3=$(curl -s -X POST "$BASE/declarations" -H 'Content-Type: application/json' \
  -d '{"idempotency_key":"DEMO-3","ship_id":"SH-05","direction":"inbound",
       "duration_minutes":60,"requested_start":"2026-09-24T23:20:00","committed":true}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

echo "-- 单艇：一条满足，另一条返回 boat_capacity 冲突（含挡路资源与时间段）--"
P1=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' \
  -d "{\"declaration_ids\":[$D2,$D3],\"boat_ids\":[\"B-01\"]}" \
  | tee /dev/stderr | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

echo "-- 双艇：两条都满足、零延误 --"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' \
  -d "{\"declaration_ids\":[$D2,$D3],\"boat_ids\":[\"B-01\",\"B-02\"]}" | j

echo "== 3. 跨午夜作业出计划 =="
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' \
  -d "{\"declaration_ids\":[$D1]}" | j

echo "== 4. 正式计划锁定（重复确认幂等，不产生第二个有效任务）=="
curl -s -X POST "$BASE/plans/$P1/confirm" | j
curl -s -X POST "$BASE/plans/$P1/confirm" | j

echo "== 5. 锁定后只能显式修订 =="
curl -s -X POST "$BASE/plans/$P1/revisions" -H 'Content-Type: application/json' \
  -d '{"boat_ids":["B-01","B-02"]}' | j
