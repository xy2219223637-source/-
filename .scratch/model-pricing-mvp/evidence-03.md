# 03 图像 MOCK 折算闭环证据

- task_id: model-pricing-mvp-20261019-03
- 时间：2026-10-19T18:19:31+08:00；来源：用户 implement-spec 与并发 agent 授权、票03、API规范4–6、主线程放行02依赖。
- checkpoint：在专属 worktree `D:/原力2222222222/model-pricing-work/ticket-03` 由46f0b2c开始，完成前已快进合入最新集成e4a9429；分支codex/model-pricing-03。
- 意图：图片有效原始用量到折算 Token、企业积分、成员用量和流水共用原账务；不把图片数量写成厂商真实 Token。

## 最小变更与规则依据

1. `backend/app/services/billing/metering.py`：FOLDED 接受且只接受当前转换规则的原始计量项；验证非负整数和10^9边界，使用Fraction精确计算转换数量及积分，只在最终微积分向上取整。converted_tokens以最多六位小数的精确字符串展示，input_tokens/output_tokens为null。基于api-spec.md第4节规则5–8。
2. `backend/app/services/billing/image_adapter.py`：MOCK专用传输替身；服务端固定合成规格mock-image:8x8:local，1–16张边界；请求规格、尺寸、质量、数量不匹配时经02前置validate_request在预占前拒绝。生成bytes留在内存，PNG解码/尺寸验证后计有效数量；结果带图片索引、SHA256与本地PNG Base64证据，坏图和不符规格产物单独标记。8x8/local是本地fixture规格，未宣称厂商最低规格。
3. 已确认空列表表示完整响应的有效图片数0，任务FAILED但计量已知，SETTLED 0；部分有效图任务FAILED、结果PARTIAL，可按确认的有效数量结算；usage_confirmed=False表示证据不完整，usage=None，保留预占NEEDS_REVIEW。不是按请求count补量，也不是空/失败一概免费；KnownNoUsage才取消释放。基于api-spec.md真实零和UNKNOWN区分以及票03异常项。
4. `backend/app/services/image_generation.py`：ArkSeedream、Apilio、LocalDemo三个generate入口在get_storage、下载或模型请求之前调用共享拒绝防线；billing_enabled时旧路径无可信计费意图一律拒绝。原creative/director调用点均经过这些provider，具体清单见外部callpoint-notes.md图像条目；这些入口未接通前保持fail-closed。
5. 不修改registry、API和通用UI；在03独立测试中以真实registry结构注入同模型IMAGE用量声明。厂商真实协议未核实，默认运行配置不凭空登记image_count。内部execute_metered是本票受控业务入口，HTTP侧完整验证管理费率和四个订单查询。

## 实际验证

执行工具：PowerShell exec_command；后端复用原checkout既有venv解释器，在本worktree运行隔离bootstrap。bootstrap在导入数据库前禁.env，设置临时SQLite，关闭初始化/恢复/workers，fixture只写临时合成身份和账务。未安装依赖。

```powershell
& 'D:/原力2222222222/企业与个人额度审核/backend/.venv/Scripts/python.exe' backend/scripts/run_refinement_tests.py test_model_image_metering.py test_model_metering.py test_model_call_guards.py test_model_pricing.py test_billing_orders.py
```

结果：65项全部通过，无skipped；其中13项图像专属、其余52项为文本视觉/管理费率/无上下文SDK/旧订单相关回归。日志`D:/原力2222222222/model-pricing-work/ticket03-backend.log`。

| 图像验收 | 独立已执行断言 |
|---|---|
| 精确转换/末端取整 | 3张÷10=0.3折算Token；0.3×0.0001=0.00003积分，最终0.0001；企业999.9999、成员已用0.0001；百万分之一比例得到0.000003字符串 |
| 全本地有效图片 | Pillow实际解码3张8x8 PNG；同步/异步httpx.send与get_storage均设失败替身，执行成功；原始有效数3由订单API回读 |
| 部分产物 | 2有效+1坏图+1错误尺寸，仅结算2张；每10张1Token、每Token1积分时扣0.2000，任务FAILED/结果PARTIAL |
| 空/未知 | 完整空响应有效数0且失败；响应证据不全保持所有用量null、预占0.3000、NEEDS_REVIEW，不以请求3补计量 |
| 提前拒绝 | 缺/错误规格、错误尺寸、错误质量、bool数量、无足够上界、折算超10^9、成员额度不足均未创建订单或调用生成 |
| 重放/快照 | 首请求预占后调价，旧订单0.3000，新订单0.6000；重放不执行adapter且返回原结果；企业扣0.9000=成员增0.9000=两条流水 |
| 超界/异常 | 实际4图超过3图预占保留NEEDS_REVIEW；负数/浮点/bool/缺字段/错误方向/超原始上限不被接受；确定无消耗取消、超时保留 |
| 管理与查询 | PUT保存/列表回显完整图片转换规则；拒绝非声明字符来源及真实Token模式；本人/企业四接口回读一致 |
| 旧路径 | Ark、Apilio、LocalDemo均在模型和存储传输前返回BILLING_CONTEXT_REQUIRED |

TDD关键红绿日志：`ticket03-red-01.log`→`green-01.log`（FOLDED未实现→精确计算）；`red-02.log`→`green-02.log`（本地适配器缺失→本地PNG）；`red-03.log`→`green-03.log`（规格未拒绝→前置拒绝）；`red-04.log`→`green-04.log`（受控部分/空输出缺失→明确用量）；`red-05.log`→`green-05.log`（旧provider触及存储→上下文先拒绝）。日志均位于外部model-pricing-work目录。初版测试URL笔误已修正；没有把失败或跳过算通过。

```powershell
# frontend，node_modules仅junction复用已安装依赖
& './node_modules/.bin/vitest.cmd' run --config vitest.config.ts tests/model-image-metering.test.tsx tests/model-orders.test.tsx tests/platform-model-pricing.test.tsx
& './node_modules/.bin/tsc.cmd' --noEmit -p tsconfig.app.json
```

组件12/12通过（4项图像专属）：图像完整费率保存回显/仅声明来源、本人和企业原始图数及精确转换字符串/MOCK/积分/快照、未知用量预占展示。TypeScript退出0。日志`D:/原力2222222222/model-pricing-work/ticket03-frontend.log`。

## 本地提交危险操作标志与恢复

- 目标/范围：只暂存本票适配器、FOLDED扩展、图像拒绝防线、专属测试和本票文档；不推送，不改原checkout、环境、凭据或业务schema。
- 风险：本地Git写入；MOCK计费开启后旧图片业务无context会被拒绝，这是未接通入口的明确防线。生产/运行服务未切换到本工作树。
- 预检查：65/65后端、12/12组件、tsc通过；提交前复核git diff --check及暂存清单；主线程共享map由merger追加context pointer，避免03/04并行写冲突。
- 备份/checkpoint：集成e4a9429及现有git-operations.md记录；本分支提交作为恢复点。无需数据库恢复，因为本票未写目标数据库。
- 执行路径：明确文件暂存→每命令临时Git身份本地提交→主线程merger合入；本agent不push。
- 回退：未合入时保留分支即可；已合入通过新增revert提交回退，不reset原checkout、不删账；自动重启/自动回退均关闭。
- 失败保护/下一步：提交失败保留专属worktree和上述日志；主线程集成并集中运行已授权独占PostgreSQL schema验证，之后再进入06整体MOCK门禁。真实模型、真实对象存储调用数均为0，本票不放行REAL。

## 完成前集成复核

- 2026-10-19T18:20:54+08:00：本票实现提交cc375c7后，以75e8119合入最新集成ca1f017（02订单详情UI竞态修复）；无冲突，后端未受影响。第一次merge因未携带每命令身份被Git拒绝，未改变文件；补上相同临时身份后完成，无全局身份修改。
- 重新运行上述三文件组件测试，16/16全部通过（新增02竞态回归4项），日志ticket03-frontend.log已更新。最终复核TypeScript和git diff --check后追加文档提交；65项后端结果仍对应完全相同的后端代码。
