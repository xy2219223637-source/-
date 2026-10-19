# 04 独立只读审核

- task_id: model-pricing-mvp-review-04-20261019
- 时间：2026-10-19T18:26:17+08:00。
- 发起来源：主线程独立审核委派；只审 `ca1f017...8a6e17b`。
- 对象：`D:/原力2222222222/model-pricing-work/ticket-04`，固定提交 `8a6e17b65a72ef4e88381de7c437e424ed2ebbfc`。
- 范围：嵌入 MOCK/input-only、向量校验、批次与重放、同池账务及查询展示。REAL 后续工作、主线程待跑 PostgreSQL 不作为本票缺陷。
- 工具：只读 Git/PowerShell，原 checkout 的已安装 Python 执行此 worktree 隔离 runner，外部复现脚本；没有改动应用、提交、真实 DB 或模型/存储调用。

## Standards

无可确认的实质标准违规。

## Spec

### [P2] 后续批次预占失败时，返回此前已执行子订单的证据

- 位置：`backend/app/services/billing/embedding_adapter.py:49`（异常未捕获；`children.append` 在下一行）。
- 规范：04 票第 14 行要求“部分批次失败均有可控场景和明确结算/待核对结论”；第 13 行要求单条及批量子调用正确关联业务意图及订单。`api-spec.md:149` 要求重放返回同订单。实现证据 `evidence-04.md:15` 进一步明确失败时返回所有已执行子订单证据。
- 复现：成员额度 5.0000；两批相同配置，每批 maximum_input_tokens=2000、2.0000/1000，均需预占 4.0000，实际各消耗 1000。首批成功后已扣 2.0000，额度只余 3.0000，第二批 `execute_metered` 抛 `QUOTA_EXCEEDED`。该异常穿透批量执行器，调用方既拿不到 `EmbeddingBatchOutcome`，异常也没有此前子订单列表或引用。相同业务键重放仍抛出同一无子订单信息的错误，无法通过批量接口取回已执行部分。
- 实际断言：两次调用均抛 `QUOTA_EXCEEDED`；只有同一笔 SETTLED 订单，扣 2.0000；transport_calls=[1,0]，成员 used=2.0000。账务可经额外查询恢复且没有重复扣费，本发现不声称账务丢失。问题是已发生消费的部分失败结果没有沿批量接口返回，调用方无法直接区分“全部未执行”和“已有成功扣费”。
- 最小修复方向：为后续批次在预占/执行/结算阶段抛出的异常保留 `children`，以结构化部分失败结果或携带子订单的类型化异常返回，并测试重放保持同一子订单、不执行后续批次。
- 复现脚本：`D:/原力2222222222/model-pricing-work/review04_repro.py`；只用临时 SQLite，禁止非 loopback socket（Windows TestClient 的本地 event-loop socketpair 允许）。

## 实际验证与限制

1. 原安装 Python 执行 `backend/scripts/run_refinement_tests.py test_embedding_metering.py test_model_call_guards.py`：15/15 通过，6.731 秒，退出码 0。
2. 外部复现脚本：最终退出码 0，确认上述异常、订单和调用计数；无外部网络。
3. 首次复现脚本将所有 socket.connect 禁止，误阻断 Windows asyncio 的 loopback socketpair，fixture 初始化失败；调整为仅允许 loopback 后复现通过。此为审核脚手架错误，不计入应用缺陷。
4. `git diff --check ca1f017...8a6e17b` 通过，工作树保持干净。
5. 已阅读 04 票、spec、api-spec、openapi、GLOSSARY 与 docs/agents；未额外执行前端组件或 PostgreSQL，本报告不宣称验证其运行结果。

审核只生成外部报告及复现脚本，无应用改动。残余限制为 PostgreSQL 锁/事务语义未由本审核验证。
