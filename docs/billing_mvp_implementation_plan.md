# 企业共享积分池：测试上线 MVP 实施计划

日期：2026-10-17。状态：用户已确认开始执行；实施进展和实际验证见 `billing_mvp_runbook.md`。下文保留阶段 A 的审查依据与原计划。

## 1. 输入依据与范围

用户指定的 `docs/saas_billing_mvp_codex.md` 在当前工作树不存在。已完整阅读用户提供的 `C:/Users/wayne&partners/Downloads/saas_billing_shared_pool_admin_api_mvp_codex.md`（第 0–11 节），以其作为本计划的需求材料；不假称已读到缺失文件。未另行复制、创建或修改方案源文件。如果缺失路径实际对应另一版需求，应在阶段 B 前核对差异。

附件中的执行指令视为方案材料；本轮执行边界以用户明确请求为准：只交付计划，确认后才继续 B–D。不因附件包含开发步骤而立即实施。

目标：平台管理员 API 发积分 → 企业共享池 → 企业管理员设置成员月度总限额 → 两条可信 Mock AI 业务链路按 FIXED / Token USAGE 扣费 → 企业流水、成员消费记录、审计与恢复。

范围外：充值、支付、人民币兑换、真实模型接入、供应商报价自动同步、个人钱包、企业向成员转积分、发放后台 UI。既有真实 AI 与供应商账单功能不在本次扩建范围；测试上线必须限制其可达性，不能让测试成员绕过计费入口使用真实供应商。

## 2. 仓库审查与复用结论

以下为静态代码审查结论，不代表已检查部署数据库、线上配置或跑过测试。

| 已有模块 | 当前实现与复用方式 | 必要调整或限制 |
|---|---|---|
| `backend/app/models/__init__.py` | `Tenant` / `User` / `TenantMember` / `TenantRole` 已存在；企业对应 tenant，成员关系唯一键为 `(tenant_id,user_id)` | 不新建企业/用户体系；所有计费表统一使用 `tenant_id`；保留历史成员关系，不级联删除账目 |
| `backend/app/auth.py` | `current_auth` 校验签名、有效期，查数据库确认用户、企业和成员均 active；构造 `AuthContext` | 当前令牌是自定义 HMAC Bearer 格式，并非标准 JWT；复用验证器，不自行按 JWT 解码；后台任务不得信任客户端主体字段 |
| `backend/app/api/admin.py` | `/platform-admin`、`/tenant-admin`；`platform_admin` 校验数据库来源的 `is_platform_admin` | 复用平台身份；企业积分管理不直接使用宽泛 `tenant_admin` 条件 |
| `backend/app/permissions.py` | `require_permission`、权限目录、自定义企业角色；owner 具有全部企业权限 | 增加 `billing.view`、`billing.quota.manage`、`billing.mock.use`；owner 默认拥有，默认 admin 补企业管理权限，普通成员不获管理权限；已有自定义角色不自动扩权 |
| `backend/app/database.py` | SQLAlchemy 2、`Base`、`SessionLocal`、`get_db`；默认 PostgreSQL；启动 create_all + 兼容迁移；可通过 `explicit_migration_required` 排除显式迁移表 | 计费使用显式迁移；`get_db` 不负责 commit；认证查询会触发 Session autobegin，不能在同一 Session 盲目嵌套 `begin()` |
| `backend/scripts/migrate_qianchuan_delivery.py`、`backend/app/services/qianchuan_plan/migration.py` | 显式、可重复执行迁移，备份确认、表/索引检查、旧表数量对照 | 复用模式，不照搬并发运行时普通文件复制 SQLite 的备份实现；不引入 Alembic 全仓改造 |
| `backend/app/services/qianchuan_plan/operations.py` | 操作记录、状态保护与幂等 Service 的现有结构 | 借鉴组织方式；不能将其外部广告操作状态机直接当积分账本 |
| `backend/app/main.py`、`backend/app/services/feishu_logging.py` | request ID、请求审计和上下文日志 | `AuditLog` 只有请求基本字段且独立事务写入；新增同事务计费审计，复用 trace ID，不复用该表承担资产审计 |
| `backend/app/api/operations.py`、`ProviderBillingRecord` | `/operations/billing/status`、`/sync` 保存供应商 `amount_cny` / usage / raw | 无余额、冻结、额度或积分幂等能力；不能复用为积分流水；不将人民币账单转成 credits |
| `backend/app/services/llm.py` | `LLMClient.chat()` 返回字符串，`chat_json()` 有 Mock 分支 | 当前接口未向调用方保留响应 usage；不适合直接按 Token 结算；本次独立可信 Mock 适配器，不修改全仓 LLM 返回类型 |
| `backend/app/api/creative.py`、`production.py`、`products.py`、`video_refinement.py`、`video_repair.py` | 已有创作、生成、精修、视频修复业务入口及后台任务链路 | 本次不将所有旧 AI 链路强行改为收费；测试环境必须禁用未纳管真实调用，并记录该能力边界 |
| `frontend/src/pages/TenantAdmin.tsx`、`TaskCenter.tsx`、`frontend/src/api/client.ts` | 可挂接企业管理与 API 客户端；任务中心目前展示人民币估算/供应商费用 | 企业页增积分管理最小面板；成员增加本人额度/记录；不能把原有人民币费用卡片改名冒充积分消费 |
| `backend/tests/test_qianchuan_auth.py`、`test_qianchuan_plan_operations.py` | unittest、TestClient、独立临时数据库、MockTransport、并发测试范式 | 复用测试组织方式；计费 HTTP 测试保留真实认证与权限依赖；PostgreSQL 并发验证不得被 SQLite 替代 |

搜索当前模型与计费相关实现未发现可复用的企业积分钱包、共享池、月额度、积分订单或积分流水。新增计费表是补足缺失能力，不是平行重建已有钱包。

## 3. 本仓库与方案的冲突及决策

1. **路径差异**：需求源文件缺失，附件完整可用；本计划明确来源，实施前若出现另一版原文件须做差异审查。
2. **命名差异**：方案 enterprise 对应现有 tenant；API 沿用 `/platform-admin/tenants` 与 `/tenant-admin`，避免新建第二套企业概念。成员额度 URL 使用 user_id，明确区别已有后台成员管理的整数 member_id。
3. **数据库描述过时**：README 仍提 SQLite；`config.py` 当前默认 `postgresql+psycopg` 且 `require_postgres=True`。测试上线以 PostgreSQL 为准；SQLite 只做快速测试，不宣称其 `FOR UPDATE` 能提供行锁。
4. **迁移工具不统一**：已有 create_all/启动兼容迁移和显式脚本，没有现成 Alembic 迁移链。新增计费显式迁移与 schema 版本校验，不扩大到全库迁移重构。
5. **企业管理员语义宽泛**：现有 `tenant_admin` 允许持有任一后台权限者进入；额度管理不能因此授权给只有团队管理权限的用户。使用专属权限检查，测试 owner/admin/自定义角色/普通成员矩阵。
6. **平台管理员身份初始化风险**：`database.py:init_db()` 存在按固定历史账号标识恢复平台管理员及 owner 的兼容 SQL。积分发放使其影响扩大；B 阶段需移除或置于默认关闭的一次性迁移开关，改为可追溯管理员引导，不能每次启动隐式恢复权限；不把具体个人标识写进计费日志或新文档。
7. **审计事务冲突**：请求中间件在业务提交后另写 `AuditLog`，审计失败可能令客户端看到失败但业务已提交。计费必须同事务写领域审计，并在 D 验证响应丢失后的幂等重放；中间件审计异常需隔离、记录告警，避免误报业务未成功。
8. **已有人民币费用与同步**：`ProviderBillingRecord.amount_cny` 为 Float，任务中心含供应商同步。它们是旧供应商费用域，不是积分资产。保留历史数据，测试上线隐藏/禁用同步入口及真实外呼，不扩建该链路。
9. **现有 Mock 不是统一外呼隔离**：`chat_json` 有 Mock 分支，但 `chat` 本身仍可外呼；仅设置 `LLM_MOCK_MODE=true` 不足以证明所有功能离线。新增 Mock 计费入口直接使用确定性适配器，并以出站请求拦截和路由白名单验证测试环境。
10. **方案失败政策存在歧义**：一处建议失败默认取消，另一处禁止忽略可能发生的用量。本计划采用“可证明未执行才取消；明确可信用量按已发布政策结算；状态未知保留预占并待核对”，不靠超时猜测免费。
11. **T27 的零值含义**：零/负数禁止用于发放；成员月限额允许为 0（关闭未来消费），但仍不得小于 used+reserved。若将所有额度零值一律拒绝，会失去停用消费的正常入口；测试需区分这两类请求。
12. **实施中补充的部署冲突**：`deploy/test-runtime/isolation_guard.py` 要求真实测试模型凭证，原测试运行器还期望视频后台常驻；本次新增独立 `deploy/billing-test.compose.yml`，使用单独数据库/Redis、`app.main:app` 启动与计费路由白名单，不改原视频测试运行器的规则。

## 4. 业务不变量与技术选择

- `1 credit = 10,000 microcredits`；余额、额度、冻结、扣费均为 BIGINT。输入金额只接受十进制字符串，拒绝 float、科学计数法、NaN、Infinity、超四位小数；限制单笔与累计 BIGINT 溢出。
- 企业 `balance >= reserved >= 0`；成员 `limit >= used + reserved >= 0`。额度合计可以超过企业余额；修改额度既不改变企业余额，也不产生 GRANT。
- 费用为 `Σ(quantity / unit_size × credits_per_unit)`，全请求求和后一次 ROUND_UP 到微积分，不逐项上取整；用 Decimal/精确整数比值，精度由受限参数范围推导，避免中间舍入。用量为非负整数，拒绝 bool、缺失必需项、未知/重复 metric、超上限值。
- 月份统一按 `Asia/Shanghai`，时间点持久化为 UTC。订单显式绑定 quota_id 与 period_key，结算/取消不重新求当前月。
- 无额度配置默认禁止收费；新月按最近有效额度配置创建独立周期，used/reserved 为 0；历史周期不清零，余额不随月份重置。
- 固定按次只对业务成功结算；USAGE 先确定可验证最大用量并预占。实际费用超预占不得透支，标记异常待核对；Mock 适配器必须保证实际用量在声明上限内。
- 管理浏览器可在认证授权后调用发放/额度 API；“不能信任前端”指不能信任其自报角色/用户/金额结果，不能误解为平台管理员也不能使用管理客户端。
- 同进程内部 `BillingService.estimate/reserve/settle/cancel`，不注册公开 `/internal/billing/*` HTTP 路由。本次不引入服务间密钥或微服务。

## 5. 数据模型与迁移

新增 `backend/app/models/billing.py`，在模型入口导入，全部标记 `explicit_migration_required`。表名沿用仓库复数约定：

| 表 | 关键字段、约束与用途 |
|---|---|
| `credit_accounts` | id、tenant_id UNIQUE/FK、balance_microcredits、reserved_microcredits、时间戳；CHECK balance>=reserved>=0 |
| `credit_ledger` | tenant_id、account_id、event_type（本次只开放 GRANT/CHARGE）、delta、balance_before/after、business_key UNIQUE、actor_type/id、reason、order_id nullable、created_at；单订单 CHARGE 唯一，正负方向 CHECK；grant_id 直接使用 GRANT 流水 id |
| `member_quotas` | tenant_id、user_id、period_key、period_type=MONTHLY、limit/used/reserved；UNIQUE(tenant_id,user_id,period_key)，关联既有成员关系，CHECK 非负且 limit>=used+reserved |
| `billing_price_rules` | pricing_scope_key 非空、feature_code、model_key 可空、version、mode、status、fixed_microcredits 或 metric_rates_json、effective_at、created_by、created_at；UNIQUE(scope,version)，部分唯一索引约束每 scope 一个 ACTIVE；激活时 retired 旧版并原子激活新版 |
| `billing_orders` | id、request_id UNIQUE、tenant_id、user_id、quota_id、period_key、feature_code、scope、rule_id、完整不可变定价快照、预占/扣费、usage、请求摘要/结算摘要、状态、result_json、业务执行状态、expires_at、reconciliation_status/error、时间戳；按 tenant/user/time 与 status/expires_at 建索引 |
| `billing_idempotency` | operation、tenant_id、key、规范化请求摘要、actor_id、resource_id、首次响应快照、created_at；UNIQUE(operation,tenant_id,key)，永久随账目保留，不以 TTL 释放 key |
| `billing_audit_events` | tenant_id、actor_type/id、action、resource_id、trace_id、原因、变更前后值、created_at；资产/额度/费率/恢复操作同业务事务插入，禁止保存 prompt/凭证 |
| `billing_schema_versions` | 迁移版本、校验摘要、执行时间；用于重复运行、启动就绪检查与审计 |

补充约束：账户与订单/流水的 tenant 必须一致，采用必要的复合 UNIQUE/FK，不能只依赖 ORM relationship；关系与额度历史均 RESTRICT 删除。订单状态初始为 RESERVED，终态 SETTLED/CANCELED；待核对使用独立 reconciliation_status，仍持有 RESERVED 资金。

不另建 credit_grant 表，流水及幂等响应已经足以表示发放记录。月额度配置先复用该成员最近周期限额，不加配置表；月初始化与修改额度均串行锁定同一企业账户和成员关系，避免月底修改与新月创建竞态。

迁移方案：新增 `backend/app/services/billing/migration.py` 与 `backend/scripts/migrate_billing_mvp.py`。B 创建管理闭环表及 schema 版本；C 增加价格/订单与关联约束（ledger 的 order_id 外键在订单表创建后补齐），每个版本独立可重入。验证完整表结构、约束、索引，发现半完成或不兼容结构则停止，不能仅凭表存在视为成功。

迁移前备份，PostgreSQL 使用经验证可恢复的独立备份；SQLite 临时测试库启用外键，备份用 SQLite backup API 或停写快照。迁移不从人民币费用回填积分、不按人数发积分、不猜测历史用量。既有企业账户初始 0、额度未授权；账户在显式初始化或首次合法发放时唯一键安全创建。

上线采用停写迁移窗口，PostgreSQL advisory lock 防多实例迁移，启动只检查 schema 就绪，不运行发放。回退先停计费入口、保存账目，回退应用而不删除有流水的表；灾难恢复需演练备份恢复，禁止用删除账目“回滚”。

## 6. 事务、幂等与失败恢复

Service 独占每次写操作的 Session/事务边界；路由只传认证主体标识与已校验参数，不传 ORM 对象跨 Session。写事务内再次验证关键主体/目标状态。业务计算与 Mock 执行不持有数据库锁。

锁顺序统一：相关幂等记录（如有）→ 企业账户 → 成员关系 → 月度额度 → 订单。发放只需幂等记录及账户；费率发布单独锁定定价 scope。发现订单时可先无锁取不可变 tenant/quota 定位信息，随后按统一顺序锁定并重新验证状态。避免部分路径订单先锁、另一路账户先锁导致死锁。

PostgreSQL 使用行锁配合条件 UPDATE 与 CHECK；检查 affected rows，余额/额度不足立即回滚。账户与月记录初始化使用唯一键及 upsert/保存点；唯一键竞态失败后不能继续使用已失效事务。锁冲突/死锁仅作有限次数完整事务重试，同一幂等键不变。

SQLite 快速测试采用显式 BEGIN IMMEDIATE、有限 busy 重试，不能依赖进程内锁或 `FOR UPDATE`；上线并发结论只依据 PostgreSQL 多连接测试。

关键操作：

1. **发放**：校验平台角色/有效企业/金额/原因/限速 → 占幂等作用域 GRANT+tenant+key → 请求摘要比对 → 锁账户并增加 balance（reserved 不变）→ 写唯一 GRANT 与领域审计 → 保存首次响应并提交。流水或审计失败整体回滚。重放返回原 balance_after，不查询当前余额拼装旧结果。
2. **额度覆盖**：校验本企业有效成员与额度管理权限 → 取/建本期额度 → 检查新 limit>=used+reserved → 覆盖总上限并审计；不改余额。拒绝任意月份回写，历史查询只读。
3. **预占**：业务 API 的 Idempotency-Key 以 tenant/user/feature/规范化业务输入形成稳定映射，后端生成并保存 request_id。重试必须映射同一个订单，不能每次新 UUID。校验功能权限、费率与最大用量，同时增加账户/成员 reserved，保存周期及价格快照并提交，再执行 Mock。
4. **结算**：只接受可信适配器输出；锁定并验证 RESERVED，按快照计算 charge<=reservation；balance-=charge，账户 reserved-=reservation，quota.used+=charge，quota.reserved-=reservation，写 CHARGE、审计、最终业务结果并置 SETTLED。使用预占全额释放差额。
5. **取消**：仅证明未消费或明确定义的失败条件允许；同事务释放两处 reserved 并转 CANCELED，不写 CHARGE。相同取消可重放；SETTLED 后取消、CANCELED 后结算均冲突。

reserve 同 key 不同输入、settle 同订单不同 usage/model/结果摘要、grant 同 key 不同金额/原因均 409。相同 key 跨用户不能读取他人结果；普通成员只读本人订单，平台发放重放仍须当前平台权限。

并发业务重放不得重复执行 Mock：用数据库原子执行领取标记/租约；处理中返回 202 与同一订单 id，已完成返回持久化结果。进程在执行后结算前退出属于待核对，不自动重新调用。

超时扫描：新增受控脚本 `backend/scripts/reconcile_billing_orders.py`，以批处理和锁防重复扫描，超时只标记 NEEDS_REVIEW 并审计。可证明未开始则 cancel；可信结果持久化则按快照 settle；未知则保留预占、告警并人工核对。补偿复用同一 Service，不提供随意改余额接口。企业/成员被停用后禁止新预占，但已授权订单允许可信恢复服务在原租户/周期完成结算。

核账不变量：企业 balance=GRANT 总和−CHARGE 总和（本次无 REFUND/ADJUST）；账户 reserved=未终结订单 reservation 总和；每期 quota.used=该周期订单 charge 总和，quota.reserved=该周期未终结订单 reservation 总和。失败核账阻止扩大测试开放。

## 7. API 与权限契约

均沿用 Bearer 认证；现有模式非 Cookie Session，无需凭空引入 Cookie CSRF 方案。未来改为 Cookie 必须另补 CSRF。Pydantic 禁止额外敏感字段，金额响应为四位小数字符串，分页 page_size<=100，时间查询默认本月、最大范围 93 天。

| 方法与路由（含 /api） | 角色与用途 |
|---|---|
| POST `/platform-admin/tenants/{tenant_id}/credits/grants` | platform_admin；Idempotency-Key 必填；body amount_credits / reason；首次 201、重放 200，返回 grant_id、tenant_id、amount_credits、balance_after_credits、ledger_id、created_at |
| GET `/platform-admin/tenants/{tenant_id}/credits/grants` | 仅平台管理员，分页/时间/操作者过滤；企业管理员不能调用此平台列表 |
| GET `/tenant-admin/billing/credits` | billing.view；balance/reserved/available |
| PUT `/tenant-admin/billing/members/{user_id}/quota` | billing.quota.manage；本月 total_limit_credits + reason，覆盖语义 |
| GET `/tenant-admin/billing/members/usage` | billing.view；本期额度、used、reserved、remaining，分页 |
| GET `/tenant-admin/billing/ledger` | billing.view；企业流水，只返回允许字段 |
| GET `/me/billing/quota` | 当前有效成员；本人当前周期额度，未授权明确返回 authorized=false |
| GET `/me/billing/orders` | 当前有效成员；仅本人消费记录，不能传 user_id/tenant_id 扩权 |
| POST/GET `/platform-admin/billing/price-rules` | 平台管理员；创建不可变费率版本/查询，创建草稿时完整校验 |
| POST `/platform-admin/billing/price-rules/{id}/activate` | 平台管理员；事务切换 ACTIVE 版本，已生效内容禁止 PATCH 覆盖 |
| POST `/ai/mock/fixed`、`/ai/mock/text` | billing.mock.use；仅显式启用测试环境可达；客户端传业务输入与幂等键，不接受 usage、charge、actor、tenant、pricing_version |

错误保持 FastAPI `detail` 外层，计费领域使用 `{code,message,request_id}`；至少区分 401 UNAUTHENTICATED、403 FORBIDDEN、404 ENTERPRISE_NOT_FOUND/RESOURCE_NOT_FOUND、422 INVALID_GRANT_AMOUNT/INVALID_USAGE、400 INVALID_REASON、409 IDEMPOTENCY_CONFLICT/INSUFFICIENT_CREDITS/QUOTA_EXCEEDED/INVALID_ORDER_STATE、429 RATE_LIMITED。不得返回 SQL、凭证或其他租户字段；其他模块错误格式不作全仓重写。

发放 reason 去首尾空白后非空、最长 200 字；Idempotency-Key 限长并校验 UUID。测试单笔上限默认 100000 credits，可配置；主体+目标企业限速复用 Redis 客户端做原子计数，跨 worker 有效，管理写接口限速服务不可用时返回 503。单进程内存计数不能作为部署版唯一限流。

## 8. 待新增与修改文件

| 文件 | 工作内容/阶段 |
|---|---|
| `backend/app/models/billing.py`、模型入口 | 计费表与约束、显式迁移标记，B/C |
| `backend/app/services/billing/{migration,amounts,grants,quotas,audit,access}.py` | 管理闭环、参数/权限与同事务审计，B |
| `backend/app/services/billing/{pricing,calculator,orders,reconciliation}.py` | 费率、预占/结算/取消、恢复和核账，C/D |
| `backend/app/schemas/billing.py` | 管理/查询/错误契约，B/C |
| `backend/app/api/billing.py`、`billing_admin.py`、`billing_mock.py` | 外部管理/本人查询与业务 Mock；不暴露内部结算，B/C |
| `backend/app/services/billing/mock_ai.py` | 固定与 Token 两个确定性适配器、最大用量、离线结果，C |
| `backend/app/main.py`、`config.py`、`permissions.py` | 注册路由、开关、限额/限频参数、权限与 schema 就绪，B/C/D |
| `backend/app/database.py` | 禁止启动自动创建计费表；历史管理员恢复逻辑收敛，B |
| `backend/scripts/migrate_billing_mvp.py`、`reconcile_billing_orders.py` | 显式迁移、核对与恢复演练，B–D |
| `frontend/src/pages/TenantAdmin.tsx`、新增 `MyBilling.tsx`、现有路由/导航与 API 类型文件 | 企业积分面板、额度编辑、本人额度/记录；无平台发放表单，B/C |
| `backend/.env.example`、根 `.env.example`、`deploy/README.md` | 计费与 Mock 开关、测试隔离、迁移/备份/恢复步骤，D |
| `backend/tests/test_billing_*.py` | 下列验收矩阵，B–D |

文件拆分为计划，按实现规模可合并小模块，但保留路由/事务 Service/适配器边界。旧供应商账单与所有 AI 业务链路不作整体重构。

## 9. 测试与验收矩阵

沿用 unittest + TestClient，无需引入新测试框架。独立建测试 FastAPI app，覆盖 get_db/配置/适配器，不覆盖 current_auth/platform_admin 来伪造权限通过；另测 Service 单元逻辑。禁止使用仓库业务数据库或真实供应商凭证。

| 测试文件组 | 方案用例映射及断言 |
|---|---|
| grant_api / grant_transactions | T01–03、T24–31：合法/非法/精度/溢出/缺少键/权限/分页；同键同体串行并发一次入账；金额或理由变化 409；目标不存在、账户并发创建；流水/领域审计插入故障整体回滚；重试返回首次结果 |
| quota / tenant_isolation | T04–05、T17–19、T21–23：额度合计可超余额、额度修改不增积分、0 限额、低于 used+reserved 拒绝；跨租户与他人读取；月延续、午夜竞争、历史不清零 |
| calculator / pricing | T06–07、T12、T14：FIXED 20；输入2000+输出1000=12000微积分；全请求一次舍入、极小用量、零用量、上界；未知/负数/bool/重复项拒绝；无费率拒绝；发布并发只一 ACTIVE、旧快照不变 |
| orders / mock_api | T08–10、T13、T15：预占不足不调用适配器；同请求重复及并发只执行一次；结算重试幂等、不同用量冲突；取消重试与终态互斥；跨月沿原额度；浏览器伪造用量/身份/价格拒绝；内部 HTTP 接口不可达 |
| concurrency_postgres | T11、T20、T25、T29：独立连接+barrier 并发抢最后余额、同成员额度争抢、同键发放、首次月额度创建、结算对取消、额度下调对预占；结果同时核对余额/额度/订单/流水/审计，不只断言 HTTP 状态 |
| recovery / reconciliation | T16：reserve 后宕机、执行后未 settle、settle 提交后响应丢失；已知结果安全补偿、未知状态保留预占；扫描重入、停用成员历史订单恢复、核账一致 |
| migration / deployment | 空库、已有租户库、重复迁移、缺索引/半迁移拒绝、旧表数据未变、外键检查、备份恢复；关闭 Mock 时无可用入口、禁止真实 HTTP 外呼、限流跨进程/后端失效、日志脱敏 |

原文 T27 在发放金额场景执行；成员 0 限额另测，不混用。全部 T01–T31 在交付时提供测试名称映射和实际运行结果，不能仅打勾。

计划中的测试命令（尚未运行；在 backend 工作目录、选定项目虚拟环境执行）：

```text
python -m unittest discover -s tests -p "test_billing_*.py" -v
python -m unittest discover -s tests -p "test_qianchuan_plan_*.py" -v
python -m unittest discover -s tests -v
```

PostgreSQL 测试使用独立 `BILLING_TEST_DATABASE_URL` 与随机测试 schema；只有显式标记的测试资源允许清理。没有可用 PostgreSQL 时必须如实报告未验证，不以跳过算通过。完整回归前先检查各套件的环境前置条件；既有失败与本次回归分开记录。前端按现有 package scripts 做类型检查/构建，并人工核验企业/成员两端显示与权限。

## 10. 开发顺序与阶段门槛

### A：仓库审查与本计划（本轮）

已完成方案全文阅读、认证/RBAC/模型/迁移/事务/审计/AI入口/费用/UI/测试组织静态核对。只产出此计划。未运行业务测试、未读取真实环境密钥、未访问线上库。用户确认计划后进入 B。

### B：最小管理闭环

顺序：收敛平台身份初始化与明确计费权限 → 迁移及金额工具 → 企业账户与同事务审计 → 独立 `GrantEnterpriseCreditsService.grant(...)`、永久幂等与限流 → 月额度覆盖/延续 → 平台发放查询、企业余额/流水、本人额度查询 → 企业最小管理 UI。

退出门槛：B 对应测试通过；发放余额变更一定有流水/领域审计；额度不改变企业资产；跨租户测试失败即停止后续扩展。阶段结束给出改动和实际测试结果，再进入 C，不跳过失败。

### C：计费与可信 Mock 闭环

顺序：费率版本与发布 → Decimal 计算器 → 订单及数据库锁/幂等 → reserve/settle/cancel → 持久化业务执行/结果与两条 Mock API → 本人消费记录/UI → 跨月与 PostgreSQL 竞态验证。

示例费率只作为测试数据显式发布：FIXED=20 credits；USAGE 输入每1000=0.20、输出每1000=0.80。不写成全平台固定换算，不借用真实模型名称。Mock 使用服务器固定场景确定 2000/1000 用量，不能由浏览器任意提交 actual_usage。

退出门槛：两条路径通过；无免费未知费率调用；重试不重复执行/扣费；PostgreSQL 并发下不透支；订单快照与跨月绑定可核对。

### D：测试上线验证

顺序：隔离测试环境与禁真实外呼 → 备份/迁移重跑/恢复演练 → 领域测试及相关回归 → 管理员、企业管理员、成员三角色端到端 → 请求/领域日志与账目核对 → 超时扫描、补偿与响应丢失演练 → 测试发布记录。

造数通过管理 API：给企业发10000，给 A/B 配4000/7000；A 一次 FIXED20 和一次 USAGE1.2 后，企业余额9978.8000、A used21.2000、B used0、全部 reserved0；GRANT 一条、CHARGE 两条、额度变更仅审计。另用第二企业做越权验证。

上线门槛：T01–T31 有自动化证据、数据库约束有效、无真实供应商调用、限流有效、核账零差异、未知订单不会盲目退额、备份可恢复。任何门槛未达只能标记未完成，不宣称已满足安全测试上线。

部署配置默认关闭计费 Mock；仅测试环境显式启用。受控测试账号不复用真实管理员默认口令，auth_secret 必须替换默认值。运行与恢复说明记录开关、备份、迁移版本、测试摘要及未解决问题。

## 11. 计划确认点

已按用户确认采用以下口径实施：沿用 tenant 命名与现有路由；PostgreSQL 作为并发验收数据库；成员额度允许为0；仅接两条独立 Mock 业务入口，未纳管的真实 AI 入口在测试开放范围禁用；增加同事务领域审计并收敛历史平台权限自动恢复逻辑。

本文件主体保留原始实施计划，当前实现差异、测试证据与尚未执行的远程部署步骤见 `billing_mvp_runbook.md`。不需要真实模型、支付或报价同步凭证。


## 后续授权补充：纠正多发积分

用户在完成MVP后提出平台管理员需要扣除多发的企业积分。本轮追加独立 REVOKE 账本事件及平台「扣回积分」入口；不通过负数发放、不改原发放记录。复用企业锁、账户锁、严格金额、UUID幂等、限流与事务审计；扣回不得超过企业可用余额（balance-reserved），成员额度不变。schema v2 扩展方向CHECK，显式迁移支持v1升级及新库初始化。新增平台余额/调整查询及扣回API，企业流水显示扣回类型；覆盖越权、租户隔离、参数、幂等、余额保护、并发、订单结算及迁移测试。本补充扩展原“只开放GRANT/CHARGE”和“发放后台UI不在范围内”的初始限制，以用户后续明确需求为准；真实模型/支付/兑换仍在范围外。


## 后续授权补充：平台模型费用管理页面

用户要求独立页面手动调整Token与积分换算比例，同时展示按次固定扣费设置。新增平台专用 `/platform-admin/model-pricing` 页面及导航，复用现有费率创建、发布、分页和审计能力；增加当前生效规则/建议版本摘要查询，展示输入与输出Token独立费率以及按次积分。采用保存待启用版本、显式启用两步，历史规则不可改，订单快照保持不变。仅开放现有测试功能，不扩展真实模型或供应商报价；无需数据库迁移。覆盖页面权限、金额单位提交、版本发布和新旧订单结算的前后端测试。
