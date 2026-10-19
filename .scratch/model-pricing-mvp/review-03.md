# 03 独立只读审核

- task_id: model-pricing-mvp-20261019-review03
- 来源：主线程独立审核委派；只审 03，禁止真实服务、真实数据库、凭据、应用改写及提交。
- 时间：2026-10-19T18:26:17+08:00。
- 对象：`D:/原力2222222222/model-pricing-work/ticket-03`，HEAD `acb1e711bbab946f3c4f8ce4443267047ca7ea99`。
- 基线：`ca1f017`；差异命令 `git diff ca1f017...acb1e711`；核心实现 `cc375c7`。固定对象未改变。

## 结论

本次未发现可复现、可归因于 03 的实质业务、安全、恢复或契约缺陷；无须列出修改建议。

审核依据：票 `issues/03-image-mock-conversion.md`，`api-spec.md:98-103` 的有效图片、比例、数量、精度与规格规则，`spec.md:239-249` 的 AC03、AC05–AC08、AC13，以及 OpenAPI Usage/FOLDED_TOKENS 契约。已读 GLOSSARY、仓库 AGENTS 与 docs/agents 约定。

重点核对：精确比例与最终总额 ceil、converted_tokens 六位内精确字符串、原始数量/转换后数量界限、真实零与未知分离、本地 PNG 解码与规格验证、原始产物数量、部分/空产物、快照及重放、共享账本事务与恢复、旧图像路径调用前拒绝、查询及通用图像展示。

## 实际验证

1. 原 checkout 已安装 Python 解释器 + 该 worktree 的隔离 runner，执行 `test_model_image_metering.py test_model_metering.py test_model_call_guards.py test_model_pricing.py test_billing_orders.py`：65 项通过，36.080 秒，无 skipped。运行前禁 Python 字节码写入；runner 禁 `.env`，关闭初始化/恢复/workers，使用临时 SQLite。日志 `review03-suite.log`。
2. 外部脚本 `review03-extra.py`：2 项通过。独立 Decimal/100 位精度 oracle 核对 10,000 组合法折算与最终积分（包括 0、1、10^9 数量及七种 source_unit_size）；注入 MODEL_SETTLE 审计失败，确认已持久产物/用量保留、企业与成员扣账回滚，随后使用持久结果恢复结算，账户核对通过。日志 `review03-extra.log`。
3. `git diff --check ca1f017...acb1e711` 退出 0；最终 `git status --short` 为空。

## 边界与残余风险

该结论仅覆盖固定 03 的离线 MOCK 代码；未执行 PostgreSQL 并发/触发器核验、前端组件测试或真实供应商验证。前端为源码与已有组件测试断言的只读审阅，不把历史通过结果当作本次执行。

生产 registry 未声明真实图像用量协议且原图像业务入口保持拒绝，是文档明确列出的后续接入边界；本审核不将其解释为已通过 REAL，也不将 05 音频/REAL 后续作为 03 缺陷。

仅在外部 model-pricing-work 写入审核脚本、日志及本报告；未改应用、配置或仓库状态，未使用网络，未启动原应用，未提交。
