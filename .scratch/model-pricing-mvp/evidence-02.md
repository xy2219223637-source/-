# 02 文本与视觉 MOCK 离线证据

- task_id: model-pricing-mvp-20261019-02
- 时间：2026-10-19 18:09 +08:00；来源：票 02、api-spec.md §§5–8、OpenAPI 四个 model-orders 操作、主线程已确认测试边界及本地提交授权。
- 基准：实施前 e03d588；收尾已并入 integration 8b345da（包含 01 审核修复与其独立 PostgreSQL 证据）。本票没有访问目标 PostgreSQL。
- 恢复入口：当前分支 codex/model-pricing-02；共享接口另存 `D:/原力2222222222/model-pricing-work/metering-interface.md`；后续 03 扩折算，04/05 使用独立适配器。

## 实现与边界

`ModelMeteringMixin` 组合到现有 `BillingOrderService`，共用企业账户、成员限额、预占、订单、唯一 CHARGE 和恢复原语。模型价格锁先于企业锁；每个持久业务意图在当前租户仅有一个 request_id，指纹含发起成员，另一成员不能将同一意图再次扣费。无有效成员/原业务权限/当前适配器计量契约/费率/有证据的上界时不调用。

不可变订单快照包含完整费率 configuration、模型身份、能力、规格、原业务键。可选适配器 `validate_request` 在预占前执行，只做本地校验；公开 pricing_snapshot 仍严格输出 PricingInput。传输返回通用 AdapterOutcome，未绑定文本数据结构，MOCK 成本固定 NOT_INCURRED。任务执行状态与结算状态分开；未知、超时、超用量保留预占，真实零可以正常结算零费用，证明无消耗才取消。

新增本人及企业四个只读模型订单 API，保留原历史订单 API；增加现有 MyBilling 的模型列表、用量来源、模拟模式、预占、待核对和详情。旧公开 mock 已 410，内部 run_mock 也已停用；历史测试通过明确 legacy reserve/claim/record/settle 构造保留原账务不变量，没有删除历史记录或降低断言。

计费启用时，尚未绑定持久发起者的旧 LLM chat/chat_json/chat_vision/embed/transcribe 及独立 video_repair diagnose 在任何 SDK、JSON 回退或素材解析前拒绝。共享调用点清单见外部 callpoint-notes.md：未接通后台工作流、反馈、视频库、RAG 等路径保持明确拒绝；本票没有假称这些业务已完成身份传播。其 SDK 隐式重试不会在此状态下发出；后续 REAL 适配仍需逐次登记和关闭隐式重试。

REAL 当前无条件 MOCK_GATE_REQUIRED，票 06 完成可验证报告与有界计次前不能运行；没有客户端模式切换接口，MOCK 失败不转 REAL。

## 实际检查

| 检查 | 实际结果 |
|---|---|
| 原 backend/.venv/Scripts/python.exe 调 run_refinement_tests.py：test_model_metering.py、test_model_call_guards.py、test_billing_orders.py、test_billing_management.py、test_billing_safety.py、test_model_pricing.py、test_model_pricing_migration.py | 75 项，74 通过、1 跳过，39.979 秒；跳过仅旧专用 Redis 分布式限流测试，缺 BILLING_TEST_REDIS_URL，不能算通过 |
| 新增双能力矩阵后，再运行 test_model_metering.py | 17/17 通过，11.153 秒；TEXT/VISION 均验证回滚、旧快照恢复、重放、新价、对象越权、未知及余额不足 |
| 最后补充模型结算异体重放保护后，test_model_metering.py、test_model_call_guards.py、test_billing_orders.py | 37/37 通过，8.554 秒；模型及历史恢复保持正确，异体结果/用量结算返回 409 |
| Vitest：model-orders.test.tsx、billing.test.tsx、platform-model-pricing.test.tsx | 15/15 通过；本人/企业页面、模拟来源、折算、未知成本、待核对、详情、旧按钮停用、既有额度编辑 |
| TypeScript：tsc --noEmit --project tsconfig.app.json | 退出码 0；未执行打包或启动应用 |
| git diff --check | 退出码 0 |
| 独立合成示例 | 2 个 MOCK 订单与 CHARGE；企业 1000.0000→992.5000，发起成员 +7.5000，另一成员 +0.0000；账本方程通过 |

后端完整日志位于 `D:/原力2222222222/model-pricing-work/ticket02-backend.log`，合成样例见 [evidence-02-sample.json](evidence-02-sample.json)。样例只含订单/流水 ID 的 SHA-256 前 16 位引用、计量及差额；临时数据库结束即清理，不是厂商或真实业务账单。

## 场景定位

- `test_text_success_uses_shared_account_member_and_unique_ledger`：TEXT 2000 输入/1000 输出，费率 1/3 每千 Token，5.0000 积分；订单、账户、本人、其他成员及唯一流水相同引用。
- `test_vision_and_replay_share_model_price_but_never_reinvoke`：相同模型 VISION 1000/500，2.5000；同体重放一次调用，异体冲突。
- `test_reservation_snapshot_survives_price_change_during_transport`：调用期间调价，旧请求 5.0000，新请求 7.0000。
- `test_unknown_timeout_and_over_bound_retain_reservation_no_charge`、`test_proven_no_consumption_cancels_zero_is_distinct_from_unknown`：未知、超时、方向超界、取消及真实零分别断言状态、用量、余额、预占及流水。
- `test_missing_context_permissions_price_bound_and_credits_never_call_transport`：缺上下文、业务权限、价、上界、额度，以及 REAL gate 均在传输前拒绝。
- `test_ledger_storage_fault_rolls_back_then_recovery_uses_persisted_result`、双能力矩阵：数据库 CHARGE 插入故障回滚企业/成员/流水，保留已持久化结果，恢复不再调用。
- `test_concurrent_replay_calls_transport_once_and_returns_same_order`、`test_one_persisted_business_intent_cannot_be_charged_again_by_another_member`：并发同意图只一次；同租户另一成员不能再次消费。
- `test_four_order_queries_enforce_self_tenant_and_domain_permissions`、导出契约/分页检查：四操作、字段集合、错误结构、本人/租户隐藏、排序及必要存储错误脱敏。
- `test_adapter_specification_validation_precedes_reservation_and_is_immutable`、契约失效检查：规格拒绝早于预占；快照不能修改；已有价格不能绕过失效的当前计量契约。
- `test_mock_transport_and_queries_work_with_network_and_storage_forbidden`、`test_model_call_guards.py`：禁止实际同步/异步 HTTP transport、真实 storage；SDK/素材解析无调用。

## TDD 与已纠正的失败

首个 TEXT 用例缺适配器模块失败后实现最小闭环；四查询先 404 后实现。旧内部 mock 停用用例先返回旧 UNPRICED_FEATURE 后改为 410；SDK 防线测试先证实无拒绝后增加入口保护。规格前置校验、同意图跨成员幂等及当前契约失效检查均先出现可重复失败，再修复并转绿。

最终复核另发现通用 settle 入口对模型分支原本忽略传入的异体结果/用量；新增用例先失败，再要求其与已持久化原结果一致，避免恢复接口静默接受不同结算意图，最终 37 项回归通过。

一次测试防网络方法误拦截 Windows TestClient 事件循环的本地 socketpair，导致测试设施失败；改在真实 httpx 同步/异步 transport 边界禁止，保留进程内 ASGI transport。没有放宽供应商传输、删除失败断言或把失败记作通过。

## 风险与后续验证

本票只完成受控 MOCK 离线闭环，不构成 REAL gate 报告或真实接入通过。SQLite 不能证明 PostgreSQL 锁和迁移行为；本票核心场景待主线程按已授权独占临时 schema 方法执行 PostgreSQL 验证。没有测试截图、真实厂商成本、真实模型调用、对象存储或运行配置加载证明。

未知预占有意保留，会占用可用额度；不自动重试厂商或删除流水回退。回滚使用安全构建关闭新增入口并新增 revert 提交，保留历史账务与快照；原 checkout、.env、服务进程、真实业务 schema 均未修改。临时 node_modules 目录联接仅复用已安装依赖，没有安装依赖；由主线程清理工作树时处理。

## 独立审核 P2 修复：订单详情响应乱序

- task_id：model-pricing-mvp-20261019-02-review-fix；时间：2026-10-19 18:18 +08:00。
- 来源：独立审核 `D:/原力2222222222/model-pricing-work/review02.md` 的唯一 P2，以及主线程限定本票组件/测试/证据的修复分派。修复前已快进到 integration e4a9429；其 PostgreSQL 22/22 报告只读保留，本修复不改账务源码。
- 问题：同页先选择 A 再选 B，响应 B→A 时，旧 A 仍通过列表 generation 判断，覆盖 B 的订单计算依据；过期失败也可能覆盖当前错误状态。
- TDD：新增四个受控 Promise 组件场景；原代码 3 失败、4 通过，证实乱序覆盖、过期错误及加载期间不能取消。列表刷新失效场景原已通过，保留为代际回归。完整 red 日志在 `D:/原力2222222222/model-pricing-work/review02-fix-red.log`。
- 修改：详情请求拥有独立递增序号，只允许当前列表代际及最后详情请求同时匹配时写结果、错误或加载状态。关闭详情及列表刷新/切页/模式变化/卸载均使详情序号失效；加载期间提供关闭入口，避免晚到响应重新打开详情。不取消或修改服务器账务。
- 实际验证：`model-orders.test.tsx` 7/7、`billing.test.tsx` 7/7、`platform-model-pricing.test.tsx` 5/5，共 19/19 通过。以当前 app 编译选项对 `src/components/ModelOrders.tsx` 执行单文件 `tsc --noEmit`，退出码 0；`git diff --check` 通过。
- 独立复现逆转：外部 `review02-ui-race-fixed.py` 沿用原审核脚本的实际组件内存编译/受控 Promise，加入最后选择断言。输出 B 先完成显示 B、较早 A 完成后仍显示 B、`B selected last but B detail present: true`，退出码 0。原审核复现脚本保留，没有改写其证据。
- 风险与恢复：纯前端竞态修复；不涉及 03/04 文件、共享计量、数据库、SDK、凭据、进程或远程。回退本修复提交即可恢复此前 UI；无需为此重跑 PostgreSQL 账务。未运行浏览器 E2E，不据组件测试声称浏览器视觉验收通过。
