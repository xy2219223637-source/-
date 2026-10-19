# 模型费用 MVP API 规范草案

Status: needs-triage

此文件定义拟议契约，不代表接口已实现。对应机器契约为 [openapi.json](openapi.json)。业务边界与四类图见 [spec.md](spec.md)，已确认决策见 [interview.md](interview.md)。规范的结构版本不是费率版本。

## 1. 通用约定与兼容性

- API 基址 `/api`；JSON 字段沿用仓库 `snake_case`；认证沿用现有 `Authorization: Bearer …`，不更换登录协议。
- 所有新增接口受 `billing_enabled` 控制；禁用返回 `404 BILLING_DISABLED`。先认证，再授权，再查对象；同租户内不存在或越权对象统一 404。
- 下列目标接口共 7 个操作，其中 3 个直接升级现有 `/platform-admin/billing/price-rules` 资源。用户明确旧 mock/版本管理需要优化，不保留旧价格请求契约：前后端同批切换，移除 mock 编辑和版本操作。额度发放/撤回、成员额度及旧订单查询保持原契约；历史费率、订单、流水仍保留。
- 客户端不提交供应商地址、密钥、成本、已用 Token、结算金额或任意租户 ID；这些字段若出现在新接口请求体中返回 422。模型清单来自服务端运行配置的脱敏投影，不提供在线新增凭据功能。
- `model_key` 是对规范化供应商标识和精确模型 ID 的稳定 SHA-256 前 32 位小写 hex；同一供应商同模型仅一项，不按能力拆价格；遇到截断碰撞拒绝注册并报错，不合并。供应商标识取固定适配器名及服务配置槽，不能由外部 URL 或客户端提供。
- endpoint ID 与已核实厂商模型名分别记录，无法核实后者时为 null，不由命名猜价格。
- 所有金额、费率使用十进制字符串；积分最多四位小数，精确整数/Decimal 运算；禁止浮点扣账。时间为 RFC 3339 UTC。
- 分页参数 `page=1`、`page_size=20`，上限 100；响应 `{items,total,page,page_size}`。模型按 model_key 升序；订单按 created_at 降序、id 降序。
- 新接口的错误统一为 `{detail:{code,message,request_id}}`，覆盖认证、鉴权、请求校验及业务异常；局部处理新增路由，不全局改写旧接口错误。

## 2. 接口清单

以下路径相对于 `/api`。

| 方法与路径 | 权限 | 请求 | 成功响应 |
|---|---|---|---|
| GET `/platform-admin/billing/price-rules` | platform_admin | page、page_size，可选 model_type | 200 ModelPage，替换旧版本列表响应 |
| GET `/platform-admin/billing/price-rules/{model_key}` | platform_admin | 路径 model_key | 200 Model |
| PUT `/platform-admin/billing/price-rules/{model_key}` | platform_admin | `Idempotency-Key` UUID；完整 PricingInput | 200 Pricing，不产生模型调用 |
| GET `/me/billing/model-orders` | 当前有效成员，仅本人本租户 | page、page_size | 200 ModelOrderPage |
| GET `/me/billing/model-orders/{order_id}` | 当前有效成员，仅本人本租户 | 路径 order_id | 200 ModelOrder |
| GET `/tenant-admin/billing/model-orders` | `billing.view`，仅当前租户 | page、page_size | 200 ModelOrderPage |
| GET `/tenant-admin/billing/model-orders/{order_id}` | `billing.view`，仅当前租户 | 路径 order_id | 200 ModelOrder |

本轮不增加浏览器上的付费“测试”按钮或公开 settle/usage/refund 接口。最小真实测试由有界脚本执行，使用专用测试成员身份和与业务相同的计费服务，不将平台管理员跨租户身份隐式当作企业成员。

强制顺序：先用 mock 跑通完整的数据与计费流程，通过后再做真实模型测试。mock 仍使用同一 model_key 与当前费率、预占、折算、结算、查询契约，仅替换厂商/存储传输；不是仅返回固定成功字符串。旧 mock_fixed/mock_text 计价入口可以被优化替换，但 mock 验证能力必须保留且先执行。

旧 `POST /platform-admin/billing/price-rules`、`GET /platform-admin/billing/price-rules/summary`、`POST /platform-admin/billing/price-rules/{price_id}/activate` 及 `POST /ai/mock/{kind}` 在已认证、已授权情况下返回 `410 LEGACY_ENDPOINT_RETIRED`，不写入、不调用厂商。`summary` 的静态 tombstone 路由须先于 model_key 动态路由匹配。旧 version、feature_code 或 mock 请求体发到新 PUT 返回 422；mock_fixed/mock_text 不出现在新费率清单。旧测试调整为历史兼容读取及停用校验，而非要求继续提供旧消费功能。计费关闭仍先返回 BILLING_DISABLED；身份错误仍先返回 401/403。

## 3. 模型清单响应

```json
{
  "model_key": "abcdefabcdefabcdefabcdefabcdefab",
  "provider": "example_provider",
  "model_id": "example-model-id",
  "provider_model_name": null,
  "model_types": ["TEXT", "VISION"],
  "connection_status": "NOT_TESTED",
  "configured": true,
  "supported_usage_modes": ["REAL_TOKENS"],
  "supported_raw_metrics": [],
  "pricing": null
}
```

示例 ID、费率及折算值只用于解释契约，不是生产配置。`model_types` 的全集为 TEXT、VISION、IMAGE、EMBEDDING、TTS、ASR、VIDEO。相同模型多类型展示同一当前价格。

`connection_status` 为 NOT_IMPLEMENTED、NOT_CONFIGURED、NOT_TESTED、PASSED、FAILED。它只表示接入验证，不能替代订单结算状态。视频没有真实调用实现时始终 NOT_IMPLEMENTED，不计入本轮通过项。PASSED 必须有可追溯的当前运行配置测试证据；配置变化则失效为 NOT_TESTED。凭据只在进程内判断是否变化，不保存其值或可离线验证密钥的散列。

`supported_usage_modes` 与 `supported_raw_metrics` 由已核实的适配器协议声明。不能因配置中写有某模型名就认为它支持真实 Token 或某种折算来源。未核实用量契约时可为空，禁止保存可消费价格或发起正式消费；可在明确授权后进行独立接入探测并标注计费未通过。

## 4. 当前价格：两种互斥结构

真实 Token：

```json
{
  "usage_mode": "REAL_TOKENS",
  "metrics": [
    {"metric": "input_tokens", "unit_size": 1000, "credits_per_unit": "1.0000"},
    {"metric": "output_tokens", "unit_size": 1000, "credits_per_unit": "3.0000"}
  ],
  "conversion": null
}
```

折算 Token：

```json
{
  "usage_mode": "FOLDED_TOKENS",
  "metrics": [
    {"metric": "converted_tokens", "unit_size": 1000, "credits_per_unit": "2.0000"}
  ],
  "conversion": {
    "source_metric": "image_count",
    "source_unit_size": 1,
    "tokens_per_unit": 1000
  }
}
```

校验规则：

1. JSON 额外字段拒绝；不接受 version、status、fixed_credits、model_id 或 provider 修改。
2. metrics 数量 1–2，metric 不重复。REAL_TOKENS 只允许厂商可核对的 input_tokens/output_tokens；纯输入模型仅一项；所列项必须与适配器的可计费方向完全一致。厂商无输出 Token 的嵌入调用不能以缺字段冒充输出 0。
3. FOLDED_TOKENS 恰好一个 converted_tokens 费率，恰好一个 conversion；source_metric 必须在该模型 supported_raw_metrics 中。MVP 一模型只选择一种模式，不在运行时悄悄切换规则。
4. source_metric 支持 image_count、audio_milliseconds、character_count；只开放实际可取证的一项。字符口径为发送给厂商的原始文本 Unicode code point 数，不修剪或按 UTF-16 长度计数；时长使用实际送入/产出音频的可核验毫秒数；图片使用有效返回图片数量。厂商有不同协议时先完成适配器证据登记。
5. source_unit_size 仅允许 1、10、100、1000、10000、100000、1000000；tokens_per_unit 为 1–10^9 整数。这样折算可用至多六位小数精确表示，无中间四舍五入。
6. 计量数量和 Token 上限为 10^9；每个 unit_size 为 1–10^9 整数；credits_per_unit 长度最多 24 且是大于零、最多四位小数的十进制字符串；最终金额不得溢出 int64 微积分。
7. 折算数量 `Q × tokens_per_unit / source_unit_size`；积分 `Σ(Token数量 × credits_per_unit / unit_size)`；只在最终总额向上取整至 0.0001 积分。原始用量、精确折算数量及规则均进入订单依据。
8. 允许确认的零用量，缺失值为 null；两者严格区分。
9. 图片分辨率/质量、音频资源等影响折算含义的规格来自该模型服务端已核实允许集合；请求不符合当前计价规格时在调用前拒绝 PRICE_SPEC_MISMATCH，不套用其他规格的折算。此次不做规格价格矩阵。

PUT 为完整替换当前配置，保存即对新请求生效，无草稿、启用、退休或历史版本管理。更新与创建订单读取必须在相同模型价格锁内串行化。订单复制当前完整费率和折算规则，不在结算时重新读取当前配置。

幂等键复用现有 billing_idempotency：operation 固定 MODEL_PRICE_PUT，唯一范围为当前租户＋operation＋UUID；操作者、model_key 和规范化 body 均写入 request_digest 并校验，同键异模型或异 body 返回 409，另一操作者不得读取原响应。永久随审计数据保留，不能用短 TTL 导致旧请求重新覆盖新配置。同键同体返回首次保存的 Pricing（即使后续另一次保存已改价），设置 `Idempotency-Replayed: true`；未提交重复请求返回 409 REQUEST_IN_PROGRESS，不重复写入。一个新键意味着一个新管理意图，MVP 并发不同键采用数据库串行后的最后写入生效，页面提示保存后刷新核对。幂等元数据不作为费率版本提供查询。

## 5. 用量、成本与订单

```json
{
  "id": "fedcbafedcbafedcbafedcbafedcbafe",
  "request_id": "abcdefab-cdef-4abc-8def-abcdefabcdef",
  "tenant_id": "example-tenant",
  "user_id": "example-member",
  "model_key": "abcdefabcdefabcdefabcdefabcdefab",
  "provider": "example_provider",
  "model_id": "example-model-id",
  "execution_mode": "REAL",
  "status": "SETTLED",
  "reconciliation_status": "RESOLVED",
  "usage": {
    "source": "FOLDED_TOKENS",
    "input_tokens": null,
    "output_tokens": null,
    "converted_tokens": "1000",
    "raw_metric": "image_count",
    "raw_quantity": 1
  },
  "supplier_cost": {"status": "UNKNOWN", "amount": null, "currency": null},
  "charged_credits": "2.0000",
  "reserved_credits": "0.0000",
  "pricing_snapshot": {
    "usage_mode": "FOLDED_TOKENS",
    "metrics": [{"metric": "converted_tokens", "unit_size": 1000, "credits_per_unit": "2.0000"}],
    "conversion": {"source_metric": "image_count", "source_unit_size": 1, "tokens_per_unit": 1000}
  },
  "provider_request_id": null,
  "created_at": "2026-10-19T00:00:00Z"
}
```

- UNKNOWN 成本的 amount、currency 必须为 null；KNOWN 时金额为非负十进制字符串、币种为厂商声明的大写三字母代码，并有内部证据引用。没有成本时 UI 显示未知，而非 0 或估算。请求成功不证明金额已知。
- execution_mode 由服务端执行上下文决定，返回 MOCK 或 REAL；请求体不能设置。MOCK 成本为 `{status:"NOT_INCURRED",amount:null,currency:null}`，UI 显示“模拟，无厂商费用”；REAL 禁止 NOT_INCURRED，拿不到实际金额仍 UNKNOWN。mock 与真实 Token 来源是两个维度，mock 可模拟真实 Token 格式，但不得展示为厂商实测用量。
- REAL_TOKENS 用量仅填实际适用方向，折算字段为 null；FOLDED_TOKENS 填原始依据和 converted_tokens，真实 Token 字段为 null；UNKNOWN 所有数量均为 null。若保存的计费模式依赖真实用量而厂商未返回，进入 UNKNOWN，不自动改用折算。
- 状态继续使用 RESERVED、SETTLED、CANCELED；未知用量为 RESERVED＋NEEDS_REVIEW，保留预占，charged_credits 为尚未结算的 0.0000，不代表确认免费。前端必须连同状态显示。
- 模型任务是否成功与账务状态分开记录；模型成功但未知用量不得展示为“计费通过”。provider_request_id 不包含 token 或完整厂商响应。
- 请求、订单及流水只有一个稳定业务意图标识。供应商调用前持久化 STARTED，重放返回同订单，不重复发起外部调用；不确定超时不能当作已证明无消耗。
- 平台费用页内显示模型配置；订单用量与成本在现有成员消费页及企业消费页增加模型消费列表/详情，通过新增 model-orders 接口读取，保留旧 mock 数据区分。不新建无关导航。

## 6. 业务适配器内部契约（不是公开 API）

业务入口保持现有请求及领域权限，服务端构造 `BillingContext(tenant_id,user_id,business_key,feature_code,execution_mode)`；不可从供应商响应或客户端请求直接接受结算身份/执行模式。内部 `execute_metered(context,model_key,bounded_request)` 返回 `MeteredOutcome(result,order_id,execution_status,usage,supplier_cost,provider_request_id)`。MOCK 与 REAL 共用计费服务，模式在发起时保存到不可变订单依据；禁止业务失败后自动切换模式。

启用真实计费时：先校验原业务权限和模型规则，再持久化预占/业务键/快照，原子认领后仅一次调用。`business_key` 必须是已持久化的业务实体＋操作＋子调用序号（包括合法新一轮生成），不能用时间戳或每次重试新生成随机数；同键不同业务内容冲突。不同租户相同外部请求键在内部转换成不同唯一 request_id，保留原始键映射，不改变旧 mock 全局 request_id 契约。

一个工作流多次真实调用分别建子订单；既有 batch embedding 和 chat_json 修复重试均按实际外部调用次数登记。无 BillingContext 的受计费调用拒绝，不能默认归入平台租户。启用前必须列出并接好全部当前活跃适配器调用点，未接好的路径阻止消费并列入缺口，不允许绕开账本。

最大用量来自服务端限制：文本输出上限、输入可证明上界、图片数量、最长音频或字符数。无法建立可靠上界时在调用前拒绝 USAGE_BOUND_UNKNOWN，不猜预占；真实测试例外也须先给出经确认的有界请求，不能通过改账本绕过。

原有测试隔离层不整体关闭。CLI 可在同数据库安全配置中调用该服务而不开放业务 HTTP 白名单；运行进程不得隐式启用 workers、自动恢复或数据库初始化。若后续需要 HTTP 联调，单独提出精确路径白名单与授权，不把所有业务路由放开。

验证脚本默认仅 dry-run；`--execute --mode mock` 执行 MOCK 全链路，不访问厂商或真实存储；`--execute --mode real --mock-report <path>` 才可能真实调用。REAL 入口必须验证 mock 报告的代码/契约摘要、逐场景通过记录及测试环境，校验失败即拒绝。报告由执行器产生，不能仅凭手填 `passed:true` 放行。真实调用次数文件先原子持久化占用，再发出请求；mock 不消耗真实预算。

mock-gate.json 至少包含 task_id、run_id、UTC 时间、源码/契约摘要、目标测试环境脱敏标识、纳入模型能力清单、每场景订单及流水引用、断言结果和汇总。成功/失败/未知/超预占/幂等/调价/不足额/回滚/越权均通过才可进入 REAL，缺项或 skipped 不算通过。不记录 prompt、音频原文、密钥或业务身份明文。

## 7. HTTP 错误与调用约束

| HTTP | code | 语义 |
|---|---|---|
| 401 | UNAUTHENTICATED | 未登录/认证失效 |
| 403 | FORBIDDEN | 非平台管理员或缺权限 |
| 404 | BILLING_DISABLED / RESOURCE_NOT_FOUND | 计费禁用或对象不可见 |
| 409 | IDEMPOTENCY_CONFLICT / REQUEST_IN_PROGRESS | 同键异体或并发保存未完成 |
| 410 | LEGACY_ENDPOINT_RETIRED | 旧 mock 消费及版本管理操作已停用 |
| 422 | VALIDATION_ERROR / INVALID_PRICE | 类型、额外字段、数值或计价模式错误 |
| 422 | MODEL_NOT_CONFIGURED / MODEL_NOT_IMPLEMENTED | 配置不完整或适配器未实现 |
| 422 | UNPRICED_MODEL / PRICE_SPEC_MISMATCH / USAGE_BOUND_UNKNOWN | 无价、规格不匹配、无法建立预占上界 |
| 413 | REQUEST_TOO_LARGE | 新管理请求体超过 16 KiB |
| 503 | SERVICE_UNAVAILABLE | 管理服务/必要存储暂不可用 |

额度不足、超预占及用量未知继续由内部计费异常映射至各业务既有错误通道，并保留订单待核对状态；新增查询接口始终返回订单状态，不能把有订单的未知消耗伪装成“无记录”。API 错误不透出原始供应商响应、签名 URL、SQL 或堆栈。

## 8. 契约验收

1. OpenAPI 的全部 $ref、示例、方法、路径参数及错误响应一致；实现后 FastAPI 导出的七个目标操作及四个停用操作与本契约逐项比对。此次 price-rules 列表为有意破坏性升级，费用页须同批改；其他旧 API 不扩大改形状。
2. 覆盖额外字段、重复 metric、负数/零费率、溢出、bool 冒充整数、错误折算来源、空 metrics、不适用方向、同模型多类型及供应商同名模型。
3. 覆盖鉴权三角色、跨租户隐藏、本人订单限制、分页与稳定排序、无价不调用、业务权限不被计费权限替代。
4. 覆盖 PUT 幂等重放及冲突、并发不同键最后写入、价格更新与订单快照竞争、UNKNOWN/null 与真实零的区别。
5. 先 MOCK 覆盖成功但计费待核对、失败且证明无消耗、网络不确定等完整链路；未通过报告时 REAL 拒绝。通过后才验证实际模型和账务，三次限制重启后保持、SDK/业务隐式重试和计数落盘失败不调用；mock 记录不计入真实三次上限。

限制：本文件尚未将任意厂商协议、模型价格或最低规格当作已核实事实；首次实现及真实调用前必须登记证据与配置值。没有真实值的模型保持未配置，不能据此宣称所有模型计费已经通过。
