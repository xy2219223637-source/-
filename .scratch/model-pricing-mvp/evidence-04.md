# 04 嵌入模型 MOCK 证据与恢复点

- task_id：model-pricing-mvp-20261019-04。
- 时间：2026-10-19 18:20:52 +08:00；来源：04 票、spec AC03/AC06–AC08/AC13、api-spec §§4–6，以及主线程的独立写集分派。
- 基准：从 02 集成点 46f0b2c 开始；收尾快进并入最新 integration ca1f017。分支 `codex/model-pricing-04`，独立 worktree `D:/原力2222222222/model-pricing-work/ticket-04`。
- 当前 step：独立适配器、13 项嵌入测试、2 项旧 SDK 拒绝测试、4 项组件测试及类型检查通过；主线程集中 PostgreSQL 验证和独立审核仍待最终登记。
- 恢复入口：本文件、04 票；准备计划在 `D:/原力2222222222/model-pricing-work/ticket04-plan.md`，共享接口在同目录 `metering-interface.md`。

## 实现与公开边界

`embedding_adapter.py` 只声明受控 MOCK 的 EMBEDDING/input_tokens 契约，要求显式传入模拟响应。该声明不注册到生产运行配置，不证明真实厂商协议或最低规格已核实。测试注册使用独立精确模型 `fixture-embedding`，保留现有标准嵌入实际 `openai_compatible:llm` 服务槽；没有改动 registry 或凭据。

请求必须包含非空文本列表、1–65536 的本地模拟维度，以及有依据的输入 Token 上界。该维度范围仅是 MOCK 内存请求限制，不作为厂商支持声明。不依据字符数猜 Token。返回向量必须与请求数量一致，index 唯一完整且非 bool，维度匹配、元素为有限非 bool 数值，按 index 排回输入顺序。无效向量不返回成功结果；存在有效 prompt_tokens 时仍结算已消耗输入，执行状态为 FAILED。缺失/非法用量保持 UNKNOWN 和预占，确认的 0 才结算零，始终不伪造 output_tokens=0。

`execute_embedding_batches` 为每个实际模拟接口批次创建一个稳定子业务键和订单，不按每条向量拆成免费调用。整个批次计划摘要绑定到各子请求，后续批次内容改变会在首个重放订单处冲突。所有批次先本地验证；缺依据的后续请求不能让第一批先消费。已完成、失败、未知的订单重放均复用原结果；失败、未知或超上界时停止后续批次，返回所有已执行子订单证据，调用完成与账务结论分开。

管理及查询 API、通用 MyBilling/ModelOrders、共用账本和 llm.py 均未修改。费用编辑器保存 input-only 配置；服务端拒绝额外输出方向。本人及企业订单 API 断言独立模型身份、输出 null、MOCK 成本 NOT_INCURRED 与越权隐藏。组件断言输入用量、模拟标识、成本、企业余额、成员已用及流水；不把不存在的输出展示为零。

## 实际验证

| 检查 | 结果与证据 |
|---|---|
| 原已安装 Python 调本 worktree 的 `backend/scripts/run_refinement_tests.py test_embedding_metering.py test_model_call_guards.py` | 15/15 通过（13 嵌入 + 2 旁路拒绝），3.070 秒；完整日志 `D:/原力2222222222/model-pricing-work/ticket04-backend.log` |
| Vitest `tests/embedding-orders.test.tsx` | 最新 integration ca1f017 后 4/4 通过，1.38 秒；日志同目录 `ticket04-components.log` |
| `tsc --noEmit -p tsconfig.app.json` | 退出码 0；日志同目录 `ticket04-types.log`；未打包或启动服务 |
| 单条计费账务断言 | 输入 1000、费率 2.0000/1000，扣 2.0000；企业 1000→998，成员已用 0→2，唯一 CHARGE=-2，output_tokens=null，账本方程通过 |
| 双批次账务断言 | 第一批两条向量、总输入 1000，仅一笔 2.0000；第二批一条向量、输入 500，1.0000；两笔唯一 CHARGE，总扣 3.0000；重放不再执行 |
| 部分失败与未知 | 第一批 2.0000 已结算；第二批无效向量+已知 500 输入仍结算 1.0000，后续不调用；缺用量则第二批保留 4.0000 预占，无伪造免费结论 |
| MOCK I/O 禁止 | socket 连接、httpx 同步/异步 send、StorageService.upload/get_storage 在执行及重放期间均设失败防线，仍通过；没有真实模型/存储调用或真实预算写入 |

临时 SQLite 使用仓库既有离线 bootstrap/fixture，与 .env、目标业务库、初始化、恢复和 workers 隔离。以上事务检查不能证明 PostgreSQL 的锁语义。PostgreSQL 由主线程在已授权独占临时 schema 集中执行；本 agent 没有连接或写入该库，不将待做项写成通过。

## TDD 与纠正的失败

先有单条公开计量用例缺模块失败，再补最小适配器；随后向量无效、缺用量、请求前置校验、稳定多批次、超上界停止、全批有界预检分别 red→green。原始 red/green 日志保留在外部目录 `ticket04-red-01.log` 至 `ticket04-red-07.log` 及对应 green 日志（第 7 次的最终 green 汇入 `ticket04-backend.log`）。

首轮测试误用不存在的 `knowledge.manage`，按 permissions.py 改为实际 `knowledge.access`；不是放宽权限。首轮缺模块发生在 fixture 创建后，导致临时 SQLite 的退出清理报占用；将适配器导入移至 fixture 创建前，随后所有正式测试正常退出。超上界新增测试曾插到相邻用例中段，已恢复用例边界；最终保留原单条全部账务断言和新增超界断言，没有删测试或隐瞒失败。

## 本地提交危险操作标志

- 来源/授权：用户允许本次 implement-spec 本地提交及指定中转仓库；本 agent 仅本地提交，不 push。
- 目标/范围：仅 04 适配器、专属后端/组件测试、04 票和本证据；不暂存无关文件，不改 shared metering/orders/registry/llm/API/UI。
- 风险/预检查：Git 写入本地对象和分支；先确认独立 worktree/当前分支、integration 祖先、定向测试、显式暂存清单及 diff-check。每命令临时 Git 身份，无全局配置或密码保存。
- checkpoint/备份：46f0b2c 起点、ca1f017 最新集成；Git 对象与此次本地提交提供恢复点；原 checkout 及备份目录保持原状。
- 执行/恢复：审阅本证据→显式暂存上述文件→本地提交→主线程审查合入；失败保留当前树、完整日志和最后安全状态。恢复先读取本文件及 `git status --short --branch`，继续未完成检查。
- 回退：主线程可在集成前拒绝采用该分支；已采用时通过新增 revert 保留历史。禁止 reset --hard、删除账务、强推。
- 自动重启/回退：均不触发；没有服务、配置、迁移、依赖安装或业务数据写入。目录联接只复用现有 node_modules，由主线程清理该 worktree 时移除。

残余限制：这是受控 MOCK 闭环，不是 REAL gate 报告。真实嵌入、真实成本、真实 SDK 输入上界及业务 RAG 身份传播仍属于后续票；现有缺上下文入口继续拒绝，不能绕过账本。
