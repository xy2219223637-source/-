# 01 当前模型费率实现与实际验证

task_id: model-pricing-mvp-20261019-01

时间：2026-10-19 17:49 +08:00。来源：票01、api-spec.md、openapi.json、schema-draft.sql及当前用户授权。独立worktree=`D:/原力2222222222/model-pricing-work/ticket-01`，分支 `codex/model-pricing-01`；已快进并入集成文档 `bcf7a02`。原运行应用文件、端口、进程和目标数据库均未改动。

实现：`registry.py` 从 settings 投影脱敏身份，按固定适配器＋配置槽标识去重，ASR复用现有auto路由解析，视频保持未实现。`UsageContract` 只接受带证据引用的服务端声明，默认不宣称任何真实协议验证成功。`model_pricing.py` 提供当前费率清单/详情/完整保存，永久操作级幂等，PG事务advisory锁串行价格及订单读取；旧HTTP版本及mock入口返回410，旧引擎保留供历史恢复测试。`model_pricing_errors.py` 局部封装401/403/422/413/503与request_id。前端同页使用REAL/FOLDED形状，空价格和折算比例不填假默认值，失败重试复用同一保存幂等键，成功后刷新核对。

迁移：v3保留旧schema的checksum解释（SQLite v2=`41513f518c64c9fc6a2f9d407ff8d2f7b976f7b1c330d25ee6958b65959d0833`，PostgreSQL v2=`abe98f12938384f7b4a52bcdcd58fa610d00573ed87ce968dfb2d6f17f66d79e`）；新增当前模型价格和订单来源字段，不删除历史。`backend/tests/fixtures/billing_v2*.sql` 来自基线3deddd8的临时合成测试数据，不是目标库导出。恢复草案见 migration-v3.md。

TDD实际红绿：首次清单测试红（BillingService缺少当前价格接口）→实现后绿；保存重放测试红（Pricing缺少model_key）→修正为model_key/configuration/updated_at后绿；费用页新组件5测试首次4失败/1通过（旧页面仍读版本契约）→换当前模型页面后5通过。迁移合成历史初次红（SQLite外键延迟计数与Windows句柄）→完整foreign_key_check后恢复立即外键约束并关闭句柄后2通过。后续边界/API/回归按实际逐步补充，不声称每个断言均单独经历过红轮。

实际命令与结果（Python路径使用原backend现有venv，只读取依赖）：

```
python scripts/run_refinement_tests.py 'test_model_pricing*.py' test_billing_management.py test_billing_orders.py test_billing_safety.py
# Ran 53 tests in 8.922s; OK (skipped=1)
node node_modules/vitest/vitest.mjs run --config vitest.config.ts tests/platform-model-pricing.test.tsx tests/billing.test.tsx
# 2 files passed; 12 tests passed
node node_modules/typescript/bin/tsc --noEmit --jsx react-jsx --target es2022 --module esnext --moduleResolution bundler --esModuleInterop --skipLibCheck src/pages/PlatformModelPricing.tsx
# exit 0
git diff --check
# exit 0
```

后端53项包括当前费率11、迁移2、管理13、历史订单18、安全9。安全9中Redis跨进程1项因无专用URL跳过，不计通过；没有运行PostgreSQL测试或访问远程服务。测试覆盖保存刷新、原始响应重放不覆盖后续改价、同键异模型/异体、金额/类型/方向/折算来源、三角色权限、分页、并发保存、审计回滚、历史查询和不可变性、请求大小及存储错误脱敏。旧测试的真实账务断言全部保留；停用HTTP预期改410，历史服务测试仍验证旧账本能力。

限制：SQLite串行化不证明PG并发竞争；PG try-lock/DDL/备份恢复代码尚未在目标环境执行。当前文件配置对应目标库只读预检查来源为主线程 `D:/原力2222222222/model-pricing-work/read-only-preflight.json`，environment_ref=`d06030ee0880a2db13dbabbb32c24354388bfb885bdd1b6fdee9526557db9e2d`，不证明运行进程配置一致。未联网、未安装依赖、未写.env、未调用厂商或真实存储、未重启、未运行E2E/全量构建。前端node_modules仅为原依赖目录junction。完整MOCK闭环和REAL验收属于后续票。

下一入口：02继承 `ModelPricingService` 与 `BillingOrder` v3字段，订单读取费率先取得相同 `model_price_lock(db,model_key)` 后再获取身份/租户锁，并复制完整模型身份与configuration，结算不得重新读当前费率。新能力必须使用可追溯的server-side `UsageContract`，不能硬编码未经核实厂商价格或比例。
