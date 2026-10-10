# 千川 Phase 4B：Operation 保护层

日期：2026-09-24

## Goal

先实现真实投放前必须具备的轻量保护：请求幂等、版本 CAS、终态防覆盖和结果不确定时禁止重复创建。本阶段不实现 worker，也不调用千川平台。

## Context

- 本地计划草稿已经可以安全创建和显式更新。
- 千川 create 请求超时不能被解释为创建失败；直接重发可能创建重复计划并产生预算风险。
- 当前只需要建立状态保护边界，不需要提前引入队列、定时任务或多 worker 调度。
- 旁路模型已经具有 `operation_version`、owner 级幂等唯一键及操作审计字段，不需要新增表。

## Scope

新增内部 `QianchuanPlanOperationService`，提供四类纯状态操作：

1. `reserve_submit`：将 `draft` 原子占位为 `submitting`；
2. `record_submit_result`：记录确定创建、确定未创建或结果不确定；
3. `reserve_uncertain_recheck`：只允许把 `result_uncertain` 占位为查询型 `syncing`；
4. `record_recheck_result`：记录已找到、确定不存在或仍无法确定。

本阶段不包含：

- 对外提交 API；
- 千川 create 或 query 网络请求；
- 自动重试；
- worker、队列、定时器或任务抢占循环；
- 生产旁路表迁移；
- 页面提交按钮。

## Interfaces

保护层是内部 Python 服务，不注册 FastAPI 路由：

```text
reserve_submit(plan_id, operation_version, idempotency_key, confirmed)
record_submit_result(operation_id, outcome)
reserve_uncertain_recheck(plan_id, operation_version, idempotency_key, confirmed)
record_recheck_result(operation_id, outcome)
```

允许的状态变化：

```text
draft --reserve_submit--> submitting
submitting --created--> submitted
submitting --not_created--> failed
submitting --uncertain--> result_uncertain

result_uncertain --reserve_uncertain_recheck--> syncing
syncing --found--> submitted
syncing --confirmed_absent--> failed
syncing --inconclusive--> result_uncertain
```

不存在从 `result_uncertain` 返回 create 的自动路径。

## Constraints

- 幂等键在 `tenant_id + created_by` 范围内唯一；相同请求重复调用返回同一 operation。
- 相同幂等键用于其他计划、版本或操作类型时返回 409。
- 占位使用计划状态与 `operation_version` 的条件更新，更新行数不是 1 即回滚 operation。
- 每次成功占位只增加一次 `operation_version`。
- `confirmed` 必须严格为 `true`。
- 非本人计划按 404 隐藏。
- 写开关关闭时不能创建新的 operation。
- 已结束 operation 接收相同结果时幂等返回；相反结果不能覆盖终态。
- 确认创建成功必须提供合法平台计划 ID。
- 写开关紧急关闭后，已经在途的结果仍允许安全落账，避免丢失平台真实结果。
- request snapshot 不包含 access token、refresh token 或密文。
- 本服务不持有平台 client，因此无法产生真实网络调用。

## Tests

新增 `backend/tests/test_qianchuan_plan_operations.py`，覆盖：

- 相同幂等键重复提交只创建一个 operation；
- 不同幂等键不能重复占位同一版本；
- 旧版本和未确认请求被拒绝；
- owner 幂等键不能跨操作类型复用；
- 写开关和用户归属在落 operation 前生效；
- 确认创建成功必须携带平台计划 ID；
- 终态相同结果可安全重放，相反结果不可覆盖；
- uncertain 禁止再次 create，只允许 query-only recheck；
- 回查 found 可落为 submitted；
- 回查 inconclusive 保持 uncertain。

Phase 4B 初始验证结果：专项 7 项、千川全量 120 项通过。后续接入 debug-only mock executor 后，保护与 executor 联合专项扩充为 10 项；最新全量结果记录在 `21-QIANCHUAN-PHASE4C-MOCK-EXECUTOR.md`。

## Definition of Done

- [x] owner 级幂等键保护完成。
- [x] 状态加版本的 CAS 占位完成。
- [x] CAS 失败时 operation 与计划状态一起回滚。
- [x] 终态重复回调幂等，相反结果防覆盖。
- [x] `result_uncertain` 禁止自动重发 create。
- [x] uncertain 仅能进入 `reconcile_create` 查询型回查。
- [x] 保护层没有平台 client、提交路由或 worker。
- [x] 真实写和 worker 开关保持关闭。
- [ ] 接入官方查询适配器。
- [x] 接入无网络、无调度的 debug-only mock executor。
- [ ] 接入独立 worker。
- [ ] 在备份和回滚验证后执行生产旁路表迁移。

## 下一步

mock executor 已验证只消费成功占位的 operation。下一阶段再决定是否需要独立 worker，并验证关闭 worker 不影响素材上传服务；之后才把 `reconcile_create` 接到官方只读查询接口。在能够可靠按本地 operation 匹配平台计划以前，不接真实 create。
