# Codex 项目执行文档：千川商品视频全域推广旁路闭环 MVP

状态：待实施。重写日期：2026-09-23。

本文严格按 **Task = Goal + Context + Scope + Interfaces + Constraints + Tests + Definition of Done** 组织，是 Codex 开发、联调、发布与验收本功能的唯一任务边界。

本文中的“计划”仅指**商品视频全域推广计划**。首版不支持普通投放、直播全域、乘方、搜索广告、品牌广告或自动投放。

核心架构要求是：在现有千川授权、广告账号同步、成片上传、素材核验和素材汇总报表之外，建立一条可独立关闭和回滚的计划旁路。旁路允许复用经过校验的个人授权、广告账号和成功上传素材，但不得修改原上传流程的状态语义、接口响应、后台调度或报表口径。

```text
原流程：个人授权 → 账号同步 → 成片上传 → 素材核验 → 素材汇总报表
                                           │
                                           │ 只读引用 succeeded upload
                                           ▼
旁路流程：能力探测 → 本地草稿 → 幂等提交 → 平台回查 → 手动启停 → 计划报表
```

关闭全部计划功能开关后，原授权、上传和素材报表流程必须与开发前保持一致。

---

## Task

在现有“个人绑定千川账号 → 成片上传千川素材库 → 素材汇总报表”能力上，以**独立表、独立服务、独立 worker、独立 DTO、分层功能开关**的旁路架构补齐商品视频全域推广最小闭环：

```text
个人登录
  → 绑定或更新本人千川授权
  → 选择本人广告账号
  → 只读查询该账号的授权抖音号和可投商品
  → 选择本人已上传且平台可用的一条视频素材
  → 创建本地计划草稿
  → 二次确认并幂等提交一条全域计划
  → 从平台回查计划 ID、审核状态和投放状态
  → 本人手动启用或暂停
  → 查询计划数据、素材汇总数据及素材关联计划
```

Codex 必须按以下顺序执行：

1. 保持现有千川测试基线全绿。
2. 先完成只读能力探测和第三方契约固化。
3. 再新增旁路表、服务、接口和只读页面。
4. 写链路先只用 mock 验证，所有真实写开关默认关闭。
5. 只有当前测试账户的关键能力全部通过能力门，且用户当次明确指定测试账户、预算上限并再次确认后，才允许进行真实创建、启用或暂停。

SDK 中存在接口类不等于当前应用拥有权限，也不等于当前广告账户适用。未经真实只读探测确认的第三方写参数、枚举、金额单位和状态映射不得进入真实写链路。

---

## Goal

1. **原流程零破坏**：关闭计划功能开关后，现有 OAuth、账号同步、素材上传、素材核验、素材报表及其 API/DTO 行为不变。
2. **个人隔离**：每个登录用户只能使用本人千川连接、binding、上传记录、计划、操作日志、探测结果和缓存；同企业其他用户及 owner 默认不能读取或操作。
3. **单连接规则明确**：同一用户默认复用一条未删除连接；停用连接允许重新授权复用，解绑删除后不得复活，下一次授权创建新连接。
4. **最小计划模型**：一条本地计划只关联一个广告账号、一个商品、一个抖音号和一条成功上传的主视频素材。
5. **平台状态为真源**：本地计划只是平台计划的镜像、任务和审计记录；创建返回成功不等于审核通过或正在投放。
6. **安全写入**：创建、启用和暂停必须二次确认、幂等、防重复点击、记录审计；网络结果不确定时先查询而不是盲目重放。
7. **数据口径分离**：计划报表按本人平台计划 ID 查询；素材报表继续表示广告账号与素材维度的汇总，二者不得互相冒充。
8. **独立调度**：计划任务不进入现有素材上传 worker；计划外部调用阻塞或失败不得拖慢素材上传和核验。
9. **可灰度、可回退**：只读能力、真实写能力和计划 worker 分别受后端开关控制；关闭开关即可停止新功能而不删除历史数据。
10. **可诊断**：保存脱敏请求快照、平台业务 code、request_id、原始平台状态和错误分类，不记录 Token、授权码、Cookie 或完整敏感请求。

---

## Context

### 当前已存在且必须保留的能力

- 个人 OAuth 授权、连接复用、账号同步、重新授权、停用和解绑已经存在。
- 同一用户默认复用一条未删除的 `qianchuan_connections`；`disabled` 连接可重新授权，`deleted` 连接不可恢复。
- 本地解绑接口为 `POST /api/qianchuan/connections/{id}/remove`，只删除本地授权关系，不宣称撤销平台授权。
- 同一 `advertiser_id` 的多个授权来源在展示层去重。
- 成片上传使用 `/open_api/2/file/video/ad/`，随后用 `/open_api/v1.0/qianchuan/video/get/` 核验平台素材。
- 本地已有 `qianchuan_connections`、`qianchuan_account_bindings`、`qianchuan_uploads`。
- 当前素材报表使用：
  - `GET /open_api/v1.0/qianchuan/report/uni_promotion/config/get/`
  - `GET /open_api/v1.0/qianchuan/report/uni_promotion/data/get/`
- 当前成片详情已有“投放分析”、广告账号选择、上传记录和素材报表。
- 现有 `QianchuanUpload`、`QianchuanMaterialReport` 响应结构和接口路径属于兼容契约。
- 当前千川后端测试基线为 90 项通过；后续开发不得把历史失败当作正常状态。

### 当前实现边界

- `backend/app/api/qianchuan.py` 负责授权、连接、上传和素材报表入口。
- `backend/app/services/qianchuan/worker.py` 使用单独线程处理连接同步与素材上传。
- `backend/app/services/qianchuan/client.py` 提供共享 HTTP、业务 code 检查、request_id 提取、分页和脱敏日志能力。
- 前端 `QianchuanAnalysis`、`QianchuanReportPanel`、`QianchuanUploadHistory` 分别处理素材投放分析、素材汇总报表和上传历史。
- 当前数据库使用 SQLAlchemy 模型和轻量兼容迁移；`Base.metadata.create_all()` 只适合创建缺失表，不能替代生产迁移、回滚和历史数据审计。

### 当前缺口

- 未验证当前应用与测试账户能否读取授权抖音号、可投商品、全域计划、计划详情、计划素材、计划报表和素材关联计划。
- 未接入全域计划草稿、提交、回查、启用、暂停和操作日志。
- 现有素材报表可能包含同一广告账号内其他计划对相同素材的贡献，不能视为本人计划数据。
- 尚无计划任务的独立调度、幂等领取、操作版本和结果不确定恢复机制。
- 尚无只读、真实写和 worker 三层功能开关。
- 尚无计划专用 DTO 和前端组件边界。

### 关键认识

- “素材上传成功”只表示素材进入平台素材库并通过技术核验，不表示计划审核通过或正在产生消耗。
- “一人一个可复用连接”不表示一个人只能管理一个广告账号；一条 OAuth 连接可以解析出多个本人有权操作的广告账号。
- 独立服务不等于复制 Token、HTTP 和错误处理。计划服务应复用底层认证与 HTTP 能力，但业务 URL、状态和 DTO 必须隔离。
- 旁路表可以引用旧连接、binding、上传和成片，但不得修改旧记录的状态来表达计划结果。

---

## Scope

### In scope

#### Phase 0：基线与只读能力探测

1. 保持全部现有千川后端测试通过，记录前端测试和生产构建基线。
2. 扩展或新增只读探测脚本，不接入真实写按钮，不创建或修改平台计划。
3. 对本人指定的测试广告账号探测：授权抖音号、可投商品、计划列表、已有计划详情与素材、计划报表、素材关联计划、应用权限和分页行为。
4. 探测输出仅包含接口路径、脱敏请求摘要、HTTP 状态、业务 code、request_id、响应键、分页摘要和能力结论。
5. 探测结论统一为：`verified`、`permission_missing`、`account_not_applicable`、`no_sample_data`、`contract_uncertain`。
6. 只有 `verified` 能力可以进入相应运行时代码；写接口仅固化官方模型和应用权限，不在 Phase 0 发送真实写请求。

#### Phase 1：功能开关与旁路骨架

新增后端配置，全部默认关闭：

```env
QIANCHUAN_PLAN_ENABLED=false
QIANCHUAN_PLAN_WRITE_ENABLED=false
QIANCHUAN_PLAN_WORKER_ENABLED=false
```

| 开关 | 作用 |
| --- | --- |
| `QIANCHUAN_PLAN_ENABLED` | 开启计划只读接口、能力状态和计划页面 |
| `QIANCHUAN_PLAN_WRITE_ENABLED` | 允许接受创建、启用、暂停请求 |
| `QIANCHUAN_PLAN_WORKER_ENABLED` | 允许后台领取和执行计划任务 |

要求：

- 后端开关是最终安全边界；前端隐藏按钮不能替代后端校验。
- 三个开关关闭时不启动计划 worker，不进行计划平台请求，不改变现有千川页面行为。
- 开关状态通过独立能力接口返回，前端不能自行推断。
- 写操作还必须同时通过账户能力门、本人权限、二次确认、幂等键和操作版本校验。

#### Phase 2：独立数据模型与显式迁移

新增以下表或等价实现，不修改 `qianchuan_uploads.status` 的语义。

##### `qianchuan_delivery_plans`

| 字段 | 用途 |
| --- | --- |
| `id` | 本地 UUID |
| `tenant_id`, `created_by` | 企业与不可变个人归属 |
| `connection_id`, `binding_id`, `advertiser_id` | 个人授权、账号关系和账号快照 |
| `credential_version` | 创建或最近操作时的凭证版本 |
| `video_asset_id`, `upload_id`, `material_id` | 成片、上传任务和平台素材关系 |
| `product_id`, `product_name` | 千川商品 ID 与展示快照 |
| `aweme_id`, `aweme_name` | 授权抖音号 ID 与展示快照 |
| `name`, `marketing_goal` | 计划名称和经验证的全域目标 |
| `budget_minor` | 规范化最小金额单位，使用整数 |
| `roi_goal` | Decimal 语义，不用二进制浮点计算 |
| `platform_ad_id` | 平台计划 ID，数据库与项目 API 均按字符串保存 |
| `local_status` | 本地任务状态 |
| `platform_status` | 平台原始状态 |
| `review_status`, `delivery_status` | 映射后的审核和投放状态 |
| `last_operation`, `operation_version` | 幂等和乐观并发控制 |
| `provider_request_id` | 平台请求编号 |
| `error_code`, `error_message` | 脱敏错误 |
| `request_snapshot` | 脱敏后的最终创建请求快照 |
| `created_at`, `updated_at`, `last_synced_at` | 时间字段 |

##### `qianchuan_plan_operations`

记录 `create/enable/pause/sync` 操作，至少包含计划、操作人、前后状态、唯一幂等键、请求版本、业务结果、结果确定性、平台 code、request_id、脱敏错误及时间。失败、超时、进程退出或结果不确定时也必须留痕。

##### `qianchuan_capability_probes`

建议保存 owner、connection、binding、advertiser、凭证版本、capability、结论、HTTP 状态、业务 code、request_id、脱敏响应结构摘要、探测时间和失效时间。连接停用、解绑、重新授权或凭证版本变化后，旧能力结论必须失效。

##### 唯一性与迁移

- `(advertiser_id, platform_ad_id)` 在非空平台计划 ID 范围内唯一。
- `qianchuan_plan_operations.idempotency_key` 在明确业务域内唯一。
- 为 `tenant_id + created_by + video_asset_id`、`local_status + updated_at`、`binding_id` 建必要索引。
- connection、binding、upload、成片、商品和抖音号必须构成不可跨用户拼接的完整归属链。
- 新增显式、可重入迁移脚本，例如 `backend/scripts/migrate_qianchuan_delivery.py`。
- 生产迁移不得依赖应用启动时隐式重建旧表；迁移前备份，迁移后核对表、索引、约束和旧表行数。
- 回滚默认保留审计表，只关闭功能开关并回退应用，不自动删除历史。

#### Phase 3：独立后端服务与只读接口

建议新增：

```text
backend/app/api/qianchuan_plans.py
backend/app/schemas/qianchuan_plans.py
backend/app/services/qianchuan_plan/access.py
backend/app/services/qianchuan_plan/catalog.py
backend/app/services/qianchuan_plan/client.py
backend/app/services/qianchuan_plan/plans.py
backend/app/services/qianchuan_plan/reports.py
backend/app/services/qianchuan_plan/worker.py
```

职责：

- `QianchuanPlanAccess`：统一校验用户、租户、连接、binding、upload、成片和凭证版本。
- `QianchuanCatalogService`：商品、抖音号、能力结果和短时缓存。
- `QianchuanPlanService`：草稿、提交请求、列表、详情、同步和启停状态机。
- `QianchuanPlanReportService`：计划报表与素材关联计划，不修改素材报表服务。
- `QianchuanPlanClient`：组合现有底层 `QianchuanClient`，集中封装计划 URL、序列化和响应验证。
- `QianchuanPlanWorker`：独立领取计划任务，不进入现有上传 worker 循环。

计划模块可以复用 Token 解密、凭证版本、底层 HTTP、业务 code 检查、request_id、安全分页和脱敏日志；不得复用或修改上传状态、上传重试次数、素材报表 DTO、现有 worker 循环和“上传成功即计划成功”的任何逻辑。

当前进度（2026-09-24）：`access.py`、`catalog.py`、`client.py`、独立 DTO、两个目录只读接口、`plans.py` 的本地草稿、`operations.py` 的内部保护状态机及显式 debug-only `mock_executor.py` 已落地；前端产品演示已收缩为“一条视频、一条新商品全域计划、一次点击”的成功路径。创建配置已从投放分析常驻区域移入“推送投流”弹窗；完成后将 Mock 计划回传当前页面，由投放分析区常驻的计划下拉框筛选状态和计划口径演示数据。点击“创建并投流”后，浏览器自动演示上传素材、创建计划、平台审核、启用投放和投放中，不调用后端计划写接口、不持久化且刷新后重置，详见 `22-QIANCHUAN-PHASE5-MOCK-DEMO.md`。提交路由、计划列表/报表路由、`reports.py`、`worker.py`、平台真实写入和启停尚未落地。保护层已经实现 owner 级幂等键、`operation_version` CAS、终态防覆盖，以及 `result_uncertain` 只能进入 `reconcile_create` 查询型回查、不能再次 create。mock executor 只消费已占位 operation，将测试预设结果交还保护层；没有 HTTP client、路由、循环、队列或调度器。草稿写入前会精确回查所选抖音号与商品，并在平台只读调用后再次校验完整归属链和凭证版本。抖音号官方接口没有名称关键词参数，因此关键词检索必须先在 100 页安全上限内完整读取、校验总数和重复 ID，再本地过滤分页；商品关键词由平台 `filter.product_name` 执行。当前生产旁路表尚未执行显式迁移，`QIANCHUAN_PLAN_WRITE_ENABLED=false`、`QIANCHUAN_PLAN_WORKER_ENABLED=false` 保持不变。

#### Phase 4：独立计划 worker 与 mock 写链路

`QianchuanPlanWorker` 只处理 `submitting`、`syncing`、`result_uncertain` 和需要受控刷新且未达到重试上限的计划。

任务领取必须：

- 使用数据库原子条件更新或等价 CAS；
- 条件包含计划 ID、预期状态和 `operation_version`；
- 更新行数不是 1 时不得执行外部调用；
- 同一个 operation 不能被两个 worker 重复执行。

每次外部调用前复验：用户与内容权限、connection active、binding 有效、凭证版本未变化、upload 归属一致、upload 为 `succeeded` 且 material_id 未变化、operation 未成功或被新版本替代。

可能已经发送平台写请求后出现超时、断连或无效响应时标记 `result_uncertain`。下一步只能先查询平台，不得自动重复 create；无法可靠匹配时保持人工处理状态。

Phase 4 的所有写链路自动化测试只能使用 mock，真实写开关保持关闭。

#### Phase 5：独立 DTO 和前端旁路组件

建议新增：

```text
frontend/src/api/qianchuanPlans.ts
frontend/src/types/qianchuanPlans.ts
frontend/src/components/qianchuan-plans/QianchuanPlanSection.tsx
frontend/src/components/qianchuan-plans/QianchuanPlanCreateDialog.tsx
frontend/src/components/qianchuan-plans/QianchuanPlanList.tsx
frontend/src/components/qianchuan-plans/QianchuanPlanReportPanel.tsx
frontend/src/components/qianchuan-plans/QianchuanPlanStatus.tsx
```

新增独立 DTO：`QianchuanPlanCapabilities`、`QianchuanAwemeOption`、`QianchuanProductOption`、`QianchuanDeliveryPlan`、`QianchuanPlanOperation`、`QianchuanPlanReport`。

现有 `QianchuanAccounts`、`QianchuanUpload`、`QianchuanMaterialReport` 不得重命名、删除字段或改变字段类型。

```text
投放分析
  ├─ 账号选择
  ├─ 计划区域（新旁路，可整体隐藏）
  │    ├─ 单视频创建并投流
  │    ├─ 上传/创建/审核/启用进度
  │    └─ 投放中状态与演示数据
  ├─ 素材汇总数据（原组件）
  └─ 上传历史（原组件）
```

交互要求：

- 广告账号来源为本人有效账号，不再只依赖当前成片上传历史推断。
- 创建入口只有在相同 binding 下存在成功上传素材、关键能力为 `verified` 且开关开启时可用。
- 切换账号必须清空不兼容的商品、抖音号、计划、上传和报表选择。
- 确认页完整展示账号、商品、抖音号、素材 ID、预算、ROI 和真实消耗警告。
- 写请求返回 `202` 后显示处理中，不能乐观显示已创建、投放中或已暂停。
- 计划数据和素材汇总数据分别展示口径；计划组件失败不得使原素材报表和上传历史不可用。
- 产品演示只覆盖单视频、单新商品全域计划成功路径；用户点击一次“创建并投流”，Mock 状态机自动推进五个步骤。
- 已有计划追加视频、多视频、批量计划、模板、定时和创意分配继续保持 out of scope。

#### Phase 6：受控真实验收

只有后端和前端测试、生产构建、迁移预演、能力门、测试账号、预算上限和本次人工授权全部满足后才可执行。

真实验收按“创建一次 → 回查 → 启用一次 → 回查 → 暂停一次 → 回查”执行，保留 request_id、时间、平台计划 ID 和截图。不得批量创建或用重复点击测试真实幂等。

### Out of scope

- 普通投放、直播全域、乘方、搜索广告和品牌广告。
- 多商品、多素材、素材自动优选、程序化创意和批量计划。
- 自动预算、自动出价、自动 ROI 调整、自动启停和定时策略。
- 创建后修改预算、ROI、定向、排期或创意。
- 删除平台计划、删除平台素材、自动替换素材。
- 自动撤销千川平台 OAuth 授权；本地解绑不冒充平台撤权。
- 把素材上传状态写成计划审核或投放状态。
- 用素材汇总报表冒充单个计划报表。
- 用固定秒数播放率伪造逐秒互动和流失数据。
- 用 mock 页面、本地假计划或示例指标绕过能力门。
- 在本任务中重构整个现有授权与上传模块。

---

## Interfaces

### A. 项目内部 HTTP 接口

所有接口要求登录态、成片内容权限和本人归属校验。访问他人 connection、binding、upload、plan、operation、商品、抖音号或报表统一返回 404。

#### 能力与目录

| 方法与路径 | 请求 | 响应/行为 |
| --- | --- | --- |
| `GET /api/qianchuan/plan-capabilities` | `binding_id?` | 返回三层开关和本人账号能力结论，不返回敏感探测正文 |
| `GET /api/qianchuan/accounts/{binding_id}/awemes` | `keyword?, page?, page_size?` | 本人账号授权抖音号；ID 为字符串 |
| `GET /api/qianchuan/accounts/{binding_id}/products` | `aweme_id?, keyword?, cursor?, page_size?` | 当前全域场景可投商品；ID 为字符串 |

#### 计划

| 方法与路径 | 请求 | 响应/行为 |
| --- | --- | --- |
| `POST /api/qianchuan/plans/drafts` | `video_asset_id, upload_id, binding_id, product_id, aweme_id, name, budget, roi_goal` | 创建或更新本人本地草稿，不调用平台 |
| `POST /api/qianchuan/plans/{id}/submit` | `operation_version, idempotency_key, confirmed=true` | HTTP 202；进入独立计划队列 |
| `GET /api/qianchuan/plans` | `video_asset_id, binding_id?, status?, page?, page_size?` | 本人成片相关计划列表 |
| `GET /api/qianchuan/plans/{id}` | 无 | 本人计划详情、平台状态、状态映射、快照和可执行动作 |
| `POST /api/qianchuan/plans/{id}/sync` | `operation_version?, idempotency_key?` | HTTP 202；幂等刷新平台状态 |
| `POST /api/qianchuan/plans/{id}/status` | `action: ENABLE\|PAUSE, operation_version, idempotency_key, confirmed=true` | HTTP 202；后台执行后回查 |
| `GET /api/qianchuan/plans/{id}/report` | `days=7\|30` | 计划级指标、查询时间、缓存状态和口径 |
| `GET /api/qianchuan/plans/{id}/operations` | `page?, page_size?` | 本人计划脱敏操作历史 |

#### 素材关联与兼容接口

| 方法与路径 | 请求 | 响应/行为 |
| --- | --- | --- |
| `GET /api/qianchuan/uploads/{upload_id}/plans` | `days?, page?, page_size?` | 本人账号内该素材关联计划 |
| `GET /api/qianchuan/uploads/{upload_id}/report` | 保持现有接口 | 保持 `QianchuanMaterialReport` 和素材汇总口径 |
| `GET /api/qianchuan/uploads` | 保持现有接口 | 保持 `QianchuanUpload[]` 结构 |

### B. 后端服务接口

```python
class QianchuanPlanAccess:
    def require_binding(...): ...
    def require_upload_chain(...): ...
    def require_plan(...): ...
    def recheck_before_provider_call(...): ...

class QianchuanCatalogService:
    def capabilities(...): ...
    def awemes(...): ...
    def products(...): ...

class QianchuanPlanService:
    def create_draft(...): ...
    def request_submit(...): ...
    def request_sync(...): ...
    def request_status_change(...): ...
    def list(...): ...
    def detail(...): ...

class QianchuanPlanReportService:
    def plan_report(...): ...
    def material_plans(...): ...

class QianchuanPlanClient:
    def authorized_awemes(...): ...
    def available_products(...): ...
    def list_plans(...): ...
    def plan_detail(...): ...
    def create_plan(...): ...
    def update_plan_status(...): ...
    def plan_report(...): ...
    def material_plans(...): ...
```

业务服务不得散写第三方 URL、完整平台 JSON、任意枚举或金额转换。

### C. 千川第三方接口候选与能力门

| 能力 | 第三方接口候选 | 实施门槛 |
| --- | --- | --- |
| 授权抖音号 | `GET /open_api/v1.0/qianchuan/aweme/authorized/get/` | 确认抖音号类型、字段和分页 |
| 可投商品 | `GET /open_api/v1.0/qianchuan/product/available/get/` | 确认账户适用性、场景枚举、状态和分页/游标 |
| 创建全域计划 | `POST /open_api/v1.0/qianchuan/uni_aweme/ad/create/` | 权限、请求模型、素材结构、预算/ROI 单位和创建后状态全部固化 |
| 计划列表 | `GET /open_api/v1.0/qianchuan/uni_promotion/list/` | 确认时间窗、目标、fields、状态字段和过滤枚举 |
| 计划详情 | `GET /open_api/v1.0/qianchuan/uni_promotion/ad/detail/` 或探测确认路径 | Phase 0 确定准确路径和字段 |
| 计划素材 | `GET /open_api/v1.0/qianchuan/aweme/uni_promotion/ad/material/get/` 或探测确认路径 | 确认 ad_id 过滤和素材字段 |
| 手动启停 | `POST /open_api/v1.0/qianchuan/uni_promotion/ad/status/update/` | 确认 opt_status、部分失败和回查方式 |
| 计划报表 | 计划列表或探测确认的专用报表接口 | 必须证明能按 ad_id 精确归属 |
| 素材关联计划 | `GET /open_api/v1.0/qianchuan/material/ad/get/` | 确认场景、目标、fields 和分页 |
| 素材汇总报表 | 已接入 config/data 接口 | 不改运行时契约，只补口径文案 |

创建请求中的 `delivery_setting`、`creative_setting`、`marketing_goal`、素材字段、预算/ROI 单位和枚举必须由当前官方模型、控制台文档和真实错误反馈共同固化。实际响应与 SDK 不一致时，以当前账户的官方文档和真实响应为准。

### D. 状态模型

| 维度 | 状态 |
| --- | --- |
| 本地任务 | `draft/submitting/submitted/syncing/failed/result_uncertain` |
| 审核 | `unknown/pending/approved/rejected` |
| 投放 | `unknown/not_started/enabled/paused/ended/unavailable` |
| 操作 | `pending/running/succeeded/failed/result_uncertain/superseded` |

平台原始状态必须原样保存。未知枚举显示“未知平台状态”，保存原值并阻止依赖已知状态的危险操作，不得默认映射为成功、启用或暂停。

### E. 功能开关判定

只读接口：

```text
QIANCHUAN_ENABLED
AND QIANCHUAN_PLAN_ENABLED
AND 本人连接有效
```

真实写请求：

```text
QIANCHUAN_ENABLED
AND QIANCHUAN_PLAN_ENABLED
AND QIANCHUAN_PLAN_WRITE_ENABLED
AND 对应 capability == verified
AND 本人归属链有效
AND confirmed == true
AND idempotency_key 有效
AND operation_version 匹配
```

后台执行还必须增加：

```text
QIANCHUAN_PLAN_WORKER_ENABLED
AND 任务已原子领取
AND 外部调用前复验通过
```

---

## Constraints

1. **旁路隔离**：计划功能不得改变现有 connection、binding、upload 和素材报表字段的业务含义。
2. **原路由稳定**：现有授权、连接、上传和素材报表路由保持路径与响应兼容；新增计划路由放入独立模块。
3. **个人归属**：计划、操作、目录、探测、缓存和 worker 均绑定 `created_by`；相同广告账号 ID 不允许跨用户共享 Token、计划或缓存。
4. **连接生命周期**：`disabled` 可被本人重新授权复用；`deleted` 不可恢复；删除后的新授权创建新 connection 和 binding。
5. **素材前置条件**：计划只能引用本人 `qianchuan_uploads.status=succeeded`、存在 `material_id`、binding 有效且广告账号一致的记录。
6. **单素材限制**：MVP 只接受一个 `upload_id`；拒绝外部 material_id、多素材和跨账号素材。
7. **状态独立**：计划状态只写入计划表和操作表，不回写 `QianchuanUpload.status`。
8. **worker 隔离**：计划任务不得进入现有 worker；计划阻塞、异常或关闭不能影响素材上传 worker。
9. **资金安全**：预算有服务端上下限、最小单位转换和确认页；真实创建和启停必须获得当次明确授权。
10. **安全默认值**：全部计划功能开关默认关闭；发布代码不得自动启动真实写任务。
11. **幂等**：创建、启用和暂停使用唯一幂等键与操作版本；重复请求不能产生第二次平台写入。
12. **结果不确定**：可能已发送写请求后发生超时，不得自动重复；必须先查询平台或进入人工处理。
13. **状态真源**：前端只能依据平台回查结果展示审核和投放状态。
14. **DTO 稳定**：现有上传和素材报表 DTO 不得重命名、删除字段或改变类型；计划 DTO 独立定义。
15. **迁移安全**：生产迁移必须备份、可重入、可验证；不得在启动时无备份地重建旧表或修改旧唯一约束。
16. **审计保留**：失败、超时、结果不确定和被替代操作均保留；回滚不自动删除历史。
17. **错误分类**：区分权限不足、账户不适用、参数错误、审核拒绝、余额/预算、限流、网络结果不确定和未知状态。
18. **敏感信息**：日志、错误、快照和响应不得包含 Token、Refresh Token、授权码、Cookie、密钥或完整敏感请求。
19. **分页安全**：目录、计划和素材关联计划实现完整分页/游标、最大页数和重复页保护。
20. **数值精度**：平台 ID 为字符串；金额为整数最小单位；ROI 使用 Decimal；禁止 JavaScript 大整数和二进制浮点误差。
21. **缓存隔离**：缓存键包含 owner、connection/binding、advertiser、凭证版本、条件和日期；命中后也复验权限。
22. **并发安全**：worker 领取和全部写操作以状态及 `operation_version` 做 CAS；旧任务不能覆盖新状态。
23. **真实写授权**：自动化测试只能 mock；真实创建、启用、暂停不得仅凭开关开启而自动执行。
24. **不伪造数据**：无数据、无权限、不适用、审核中、失败和结果不确定必须分开表达。
25. **可回退**：紧急回退顺序为关闭写开关、关闭 worker 开关、关闭计划总开关；不得删除数据库历史。

---

## Tests

### 1. 开发前基线

- 现有全部 `test_qianchuan*.py` 通过。
- `npm run test:lineage` 和 `npm run build` 通过。
- 每个阶段重复执行并记录测试数量和结果。

### 2. Phase 0 只读探测

- 使用本人明确允许测试的广告账号。
- 探测抖音号、商品、计划列表、计划详情/素材、计划报表和素材关联计划。
- HTTP 状态与业务 code 分开记录，不保存 Token 或完整原始响应。
- `rows=[]` 只能标记 `no_sample_data`，不能证明指标口径。
- 商品或抖音号能力不适用时，写链路停在 capability gate。

### 3. 功能开关

- 三个计划开关关闭时，计划 worker 不启动，计划写请求被拒绝，原功能正常。
- 只开 `PLAN_ENABLED` 时只读可用、写接口拒绝。
- 开写开关但关闭 worker 时，可创建本地草稿和待处理 operation，不能调用平台写接口。
- worker 开启但 capability 非 `verified` 时不得调用平台。
- 前端以服务端能力响应为准。

### 4. 迁移

- 新数据库能创建旁路表和索引。
- 旧 SQLite 备份后可迁移，重复执行无副作用。
- PostgreSQL 在备份和显式确认后迁移，不能由启动流程隐式执行破坏性变更。
- 迁移前后旧千川三张表的行数和关键约束不变。
- 迁移失败可停止发布，旧版本仍能启动。

### 5. 权限矩阵

至少包含同租户用户 A/B 和跨租户用户 C：

- A 不能读取或操作 B 的 connection、binding、upload、plan、operation、商品、抖音号和报表。
- 企业 owner 不能默认读取成员的个人千川计划。
- 构造他人 UUID、相同 advertiser_id 或 material_id 不能绕过归属校验。
- 不可见资源统一返回 404。
- 停用、解绑、重新授权、用户停用和权限变化后，旧任务与缓存立即失效。

### 6. 草稿与创建校验

- 拒绝未完成、失败、待核验和缺少 material_id 的 upload。
- 拒绝跨用户、跨 binding、跨广告账号和跨租户组合。
- 拒绝多个素材、外部 material_id、任意平台 JSON 和前端自定义枚举。
- 拒绝无效商品、无授权抖音号、预算越界和 ROI 格式错误。
- 合法输入可更新本人草稿，但不得覆盖已提交计划。

### 7. 幂等与并发

- 双击 submit 只产生一个 operation 和一个平台 create 调用。
- 同一幂等键返回同一结果；不同请求不能复用相同 key。
- 旧 `operation_version` 返回冲突，不能覆盖新状态。
- 两个 worker 同时领取时只有一个成功。
- 超时后进入 `result_uncertain`，后续先查询而不是重复 create。
- 平台返回后、数据库提交前进程退出时，可恢复而不创建第二条计划。

### 8. 状态与启停

- 创建响应 code=0 后仍需回查，未回查前不显示投放中。
- 未知平台枚举显示未知状态并阻止危险操作。
- 只有合法平台状态允许启用或暂停。
- 重复相同操作幂等，相反操作使用更新后的版本。
- 部分失败、限流、余额不足、审核拒绝和业务 code 非 0 均正确保存。
- 写操作完成后必须回查并以平台结果更新状态。

### 9. worker 隔离

- 计划调用阻塞时，原素材上传任务仍能处理。
- 计划 worker 异常退出不停止上传 worker。
- 关闭计划 worker 后不领取新任务。
- 连续上传不会饿死计划任务，连续计划轮询不会拖慢上传。
- 多进程部署依靠数据库 CAS 防止重复领取。

### 10. 报表与缓存

- 计划报表按本人 platform_ad_id 精确过滤。
- 素材报表保持账号、素材和时间窗汇总口径。
- 切换计划只改变计划报表，不改变素材汇总归属。
- `rows=[]` 返回 `no_data`，指标缺失而不是伪造零值。
- 缓存键包含 owner、binding、凭证版本和条件。
- 缓存命中后连接停用、删除或凭证变化仍拒绝返回。

### 11. 前端

- 计划总开关关闭时，原投放分析结构和交互不变。
- 未绑定、授权失效、无商品、无抖音号、素材未上传和能力未验证时文案正确。
- 切换账号清空商品、抖音号、计划和报表选择，不显示旧异步响应。
- 确认页展示账号、商品、抖音号、素材 ID、预算、ROI 和消耗警告。
- 双击提交、启用和暂停只发一个请求，处理中按钮禁用。
- 计划状态与上传状态分开展示。
- 计划组件失败不影响素材报表和上传历史。
- 计划数据和素材汇总数据有明确口径文案。
- 现有测试、全部新增测试和生产构建通过。

### 12. 回归命令

```powershell
# backend
$env:UV_CACHE_DIR='D:\project_work\video_cut\.uv-cache'
uv run --python '.\.venv\Scripts\python.exe' --no-project python -m unittest discover -s tests -p "test_qianchuan*.py" -q

# frontend
cd frontend
npm run test:lineage
npm run build
```

应新增：

```text
backend/tests/test_qianchuan_plan_access.py
backend/tests/test_qianchuan_plan_catalog.py
backend/tests/test_qianchuan_plan_migration.py
backend/tests/test_qianchuan_plan_service.py
backend/tests/test_qianchuan_plan_worker.py
backend/tests/test_qianchuan_plan_reports.py
frontend/tests/qianchuan-plans.test.tsx
```

### 13. 真实账户验收

1. 本人授权后读到指定广告账号。
2. 读到至少一个授权抖音号和一个可投商品。
3. 上传测试视频并获得 material_id。
4. 确认关键 capability 均为 `verified`。
5. 人工确认最低合规预算，只创建一条测试计划。
6. 平台只出现一条计划，本地保存 platform_ad_id。
7. 回查审核与投放状态。
8. 审核允许后，分别获得授权执行一次启用和一次暂停，每次操作后回查。
9. 查询计划指标、素材指标和素材关联计划；无数据时准确显示无数据。
10. 用户 B 无法看到或操作用户 A 的连接、上传、计划、操作或报表。
11. 关闭全部计划开关，确认原授权、上传、核验和素材报表仍正常。

---

## Definition of Done

- [ ] 开发前后现有千川回归测试全部通过，没有把旧流程失败当作已知问题保留。
- [ ] Phase 0 报告列出候选接口的准确请求、响应结构、权限、账户适用性、分页和 request_id；没有猜测字段进入运行时代码。
- [ ] 三个计划开关默认关闭并通过全部组合测试。
- [ ] 旁路表通过显式迁移创建；迁移在备份数据库上演练，可重入、可验证，不改变旧表数据语义。
- [ ] 计划、操作和探测模型具有个人归属、凭证版本、审计、错误和必要唯一约束。
- [ ] 计划路由、服务、client 适配器、worker 和 DTO 与现有上传链路分离。
- [ ] 计划 worker 阻塞、失败或关闭不会影响连接同步、素材上传和素材核验。
- [ ] 用户只能用本人连接、账号、商品、抖音号和成功上传素材创建计划；双用户及跨租户越权测试通过。
- [ ] 创建、启用和暂停具备二次确认、幂等键、操作版本、原子领取、审计和结果不确定恢复。
- [ ] 平台计划 ID、脱敏请求快照、原始状态、审核状态、投放状态、错误和 request_id 可追踪。
- [ ] 未知平台状态和结果不确定不会显示为成功，也不会触发盲目重试。
- [ ] 成片详情可按本人广告账号和计划切换；计划数据、素材汇总数据和上传历史互相隔离。
- [ ] 现有 `QianchuanUpload`、`QianchuanMaterialReport`、上传接口和素材报表接口保持兼容。
- [ ] 页面明确区分上传完成、计划草稿、提交中、计划已创建、审核中、审核拒绝、已启用、已暂停、无数据、权限不足和结果不确定。
- [ ] 后端计划测试、现有千川测试、前端测试和生产构建全部通过。
- [ ] 日志、数据库快照和错误响应不含 Token、Refresh Token、授权码、Cookie、密钥或完整敏感请求。
- [ ] 真实测试账户完成一次受控的创建 → 回查 → 启用 → 回查 → 暂停 → 回查 → 计划/素材数据查询闭环，并保留证据。
- [ ] 关闭三层计划开关后，原授权、账号同步、素材上传、素材核验和素材报表仍正常。

若创建接口权限、可投商品、授权抖音号、计划回查或状态更新任一关键能力未通过真实能力门，本任务只能标记为 **blocked at capability gate**，不能用 mock 页面、本地假计划或示例数据声明完成。

---

## 官方契约证据

- 巨量引擎官方 Java SDK：<https://github.com/oceanengine/ad_open_sdk_java>
- 全域计划创建：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanUniAwemeAdCreateV10Api.java>
- 全域计划状态更新：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanUniPromotionAdStatusUpdateV10Api.java>
- 可投商品：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanProductAvailableGetV10Api.java>
- 授权抖音号：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanAwemeAuthorizedGetV10Api.java>
- 全域计划列表：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanUniPromotionListV10Api.java>
- 全域计划详情：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanUniPromotionAdDetailV10Api.java>
- 素材关联计划：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/src/main/java/com/bytedance/ads/api/QianchuanMaterialAdGetV10Api.java>
