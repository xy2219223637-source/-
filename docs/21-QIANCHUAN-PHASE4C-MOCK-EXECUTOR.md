# 千川 Phase 4C：Debug-only Mock Executor

日期：2026-09-24

## Goal

用一个最小同步 mock executor 验证“operation 已占位 → 执行结果 → 保护状态机落账”的完整链路，同时确保它不能被误当成线上千川执行器。

## Context

- Phase 4B 已实现幂等、CAS、终态防覆盖和 uncertain 查询型回查。
- 当前不需要队列、自动重试或常驻 worker。
- 直接在 API 中模拟成功会污染线上语义，因此 executor 必须保持内部、显式启用和 debug-only。

## Scope

新增：

- `MockExecutionResult`：显式描述测试结果，不做随机推断；
- `MockQianchuanPlanExecutor`：同步消费一个 operation；
- create operation 的 `created / not_created / uncertain` 分派；
- reconcile operation 的 `found / confirmed_absent / inconclusive` 分派；
- mock 执行结果标记 `executor=mock`。

不新增：

- FastAPI 路由；
- 前端按钮；
- HTTP client 或千川网络请求；
- worker、线程、队列、轮询或自动重试；
- 环境变量开关；
- 生产数据库迁移。

## Interfaces

内部调用：

```python
executor = MockQianchuanPlanExecutor(auth_service, allow_mock=True)
executor.execute(db, auth, operation_id, MockExecutionResult(...))
```

构造必须同时满足：

1. 调用方显式传入 `allow_mock=True`；
2. 后端配置 `debug=True`。

任一条件不满足即抛出 `RuntimeError`。

## Constraints

- executor 只能消费已经由 operation 保护层成功占位的记录。
- executor 不创建 operation，不跳过 CAS，也不直接修改计划表。
- create 和 reconcile 的结果集合严格分离，错配返回 422。
- executor 的所有落账都复用 Phase 4B 的终态幂等和防覆盖规则。
- 重放相同 mock 结果返回同一 operation，不重复改变计划版本。
- executor 不持有平台 client，不能发出真实或只读平台请求。
- 不把 mock executor 注入 FastAPI dependency，不注册线上入口。

## Tests

联合专项 `backend/tests/test_qianchuan_plan_operations.py` 当前 10 项，新增覆盖：

- 未显式 opt-in 时拒绝构造；
- 非 debug 环境拒绝构造；
- 已占位 create 可按预设结果完成；
- 相同结果可幂等重放；
- uncertain 经 query-only recheck 后由 found 完成；
- create 结果不能用于 reconcile operation；
- 整个执行过程不增加任何 provider 调用。

## Definition of Done

- [x] mock executor 只处理已占位 operation。
- [x] 显式 opt-in 与 debug 双重限制完成。
- [x] create 与 reconcile 结果严格分派。
- [x] executor 复用现有保护状态机，不直接写计划状态。
- [x] 没有 API、网络、队列、线程或 worker。
- [x] 专项 10 项通过。
- [x] 千川全量回归 123 项通过，耗时 211.078 秒。
- [ ] 独立 worker 决策与隔离测试完成。

## 下一步

先根据实际运行规模决定是否真的需要 worker。若仍为单机人工触发，可以先增加受保护的内部提交 API，但不得注入 mock executor；若需要离线执行，再实现只领取 `pending` operation 的独立 worker，并保持真实平台适配器缺省不可用。
