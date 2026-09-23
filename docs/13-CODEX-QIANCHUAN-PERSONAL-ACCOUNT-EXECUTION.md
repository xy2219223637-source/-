# Codex 项目执行文档：千川个人授权与投放数据隔离

状态：实施中。编写日期：2026-09-22。本文按 **Task = Goal + Context + Scope + Interfaces + Constraints + Tests + Definition of Done** 组织，可作为后续 Codex 实施与验收的输入。它不是完成声明；个人授权和本地隔离已开始落地，真实报表归属仍未验证。

## Task

将当前按企业/租户共享的千川授权、广告账号、素材上传和素材报表，改为**按登录用户归属与授权**；所有有成片访问权的用户均可绑定自己的千川账号，并可管理本人连接。成片仍属企业内容空间。成片详情“投放分析”沿用现有页面字段和接口响应结构，但仅呈现本人有权访问的数据。

执行顺序：先做后端身份与数据边界、迁移和安全测试，再改前端绑定入口/管理体验，最后以真实账户验证报表口径。不得把前端过滤当作权限隔离完成。

## Goal

1. 用户可以自行绑定、同步、重新授权、停用和解绑自己的千川连接，无需企业 owner 代操作；企业 owner 也不能默认管理成员的连接。
2. 同企业用户 A/B 即使能访问同一成片，也只能看到各自授权的账号、素材推送状态、上传历史、素材 ID 和可归属报表；构造他人的资源 ID 不能越权读取或操作。
3. 保持成片详情“投放分析”现有展示数据结构、报表字段与前端 DTO 兼容；账号切换同步切换状态、上传记录和报表。顶部不再展示千川账号/状态。
4. 用词区分“素材上传成功”和“实际投放”。不能把素材入库、报表空行或固定 2/3/5/10 秒播放率解释成已投流或逐秒流失。
5. 停用/解绑后，旧 Token、后台任务和报表缓存不得绕过新权限状态继续获取千川数据。

## Context

- 已完成的前端布局：`frontend/src/pages/FinalVideoDetail.tsx` 已把顶部状态及上传记录移入“投放分析”；`frontend/src/components/QianchuanAnalysis.tsx` 按接口返回的广告账号切换状态、记录、报表。**这不代表数据已按个人隔离。**
- 当前后端 `backend/app/services/qianchuan/service.py` 的连接列表主要按 `tenant_id` 查询，管理资格与企业 owner 关联；`frontend/src/components/QianchuanUploadModal.tsx` 的“添加官方授权”受 `can_manage` 控制。
- `backend/app/services/qianchuan/uploads.py` 的上传列表和 binding 查找、`reports.py` 的报表查找仍主要按企业范围。现有上传去重键是 `(tenant_id, video_asset_id, source_content_hash, advertiser_id)`，可能跨个人复用任务。模型在 `backend/app/models/__init__.py`，目前使用 `backend/app/database.py` 的轻量兼容迁移；实施时先审查实际数据库与部署方式，不得仅依赖新建表自动修改旧表。
- 当前报表来自按需拉取的千川全域报表，不是 Webhook。已有真实请求 `code=0` 但测试样本 `rows=[]`，非空指标、单位和归因口径尚未验证；逐秒候选接口还不能声明可用。详见 `docs/09-QIANCHUAN-INTERFACE-AUDIT.md`、`docs/10-QIANCHUAN-PERSONAL-ACCOUNT-ARCHITECTURE-AND-API.md`、`docs/12-QIANCHUAN-BUSINESS-MODEL-V1.md`。
- 关键业务边界：同一广告账号或同一素材可能由多人/多个计划使用。当前“广告账号 + 素材 + 时间窗”的上游汇总，不能天然拆出“谁投的”。个人连接隔离与上游统计归属是两项不同工作。

## Scope

### In scope

1. **数据归属与迁移**：明确连接的不可变 `owner_user_id`（可经现有 `created_by` 迁移/规范）；binding、上传、缓存和任务能追溯该 owner。处理旧数据归属与索引/唯一约束；无法可靠归属的旧记录先隔离，不得默认开放给全企业。迁移需可重复运行、可验证、可回滚，并在生产执行前备份。
2. **授权生命周期**：所有合格登录用户可新建本人 OAuth 会话；重新授权仅限本人连接；OAuth state、浏览器 nonce、会话发起人、连接 owner 和凭证版本一致。本人可同步、停用、解绑；停用为本地禁用，解绑为本地移除，不宣称千川平台撤权。
3. **统一服务端鉴权**：`/accounts`、连接操作、上传创建/列表/重试、报表读取和后台任务，都校验 `tenant_id + owner_user_id + connection_id/binding_id`、用户与租户状态、成片内容权限。无法访问的对象按统一 404 处理，避免泄露存在性；缓存命中同样鉴权。
4. **任务和缓存边界**：上传幂等范围纳入个人归属及连接，防止同账号跨人复用；任务领取及每次上游请求前复验连接状态/凭证版本；停用、解绑、重授权使旧任务与缓存失效。报表缓存键纳入 owner，令牌不入响应或日志。
5. **前端体验**：千川绑定入口对所有能访问成片的用户可见；连接列表和管理动作仅针对本人。投放分析只消费后端过滤后的账号/上传记录；无本人绑定、授权失效、上传中、上传失败、已上传未投流、报表无匹配行等状态有准确文案。保留现有 DTO/报表字段。
6. **真实数据口径**：验证报表字段在有实际投放的账户下是否非空及单位/归因；若无法证明素材汇总仅属本人，不得标成“我的投放数据”。严格隐私模式下，应阻止无法按个人/计划归属的汇总展示，并给出清晰不可用原因。

### Out of scope

- 新建广告计划、自动投流、预算/出价操作。
- 未验证的逐秒曲线接入或用固定时长播放率伪造逐秒数据。
- 企业管理员隐式接管个人 Token、历史报表、成员账号；若需要交接/审计，另开有明确授权与审计的任务。
- 改动成片、商品、本地分类标签的既有企业内容权限。

## Interfaces

### 项目内部接口：保持路径、请求和响应字段兼容，收紧可见集合及权限

| 接口 | 输入/现有响应形态 | 执行要求 |
| --- | --- | --- |
| `POST /api/qianchuan/oauth/sessions` | `video_asset_id?`, `connection_id?` → 会话 ID、授权 URL | 新绑定向合格用户开放；`connection_id` 只允许本人重新授权 |
| `GET /api/qianchuan/oauth/callback`、`GET /oauth/sessions/{id}` | 平台回调 `state/auth_code`；会话状态 | 只接受有效本人会话，旧回调不得覆盖停用/新凭证；会话查询仅发起人 |
| `GET /api/qianchuan/accounts` | `configuration, can_manage, connections[].accounts[]` | 仅返回本人连接；`can_manage` 不再等于企业 owner 权限；敏感 Token 不返回 |
| `POST /api/qianchuan/connections/{id}/sync` | HTTP 202 | 仅本人有效连接 |
| `DELETE /api/qianchuan/connections/{id}` | HTTP 204 | 本人本地停用并清理凭证、失效 binding/任务 |
| `POST /api/qianchuan/connections/{id}/remove` | HTTP 204 | 本人本地解绑；不承诺远端撤权 |
| `POST /api/qianchuan/uploads` | `video_asset_id,binding_id,filename?,is_aigc` → 现有 `QianchuanUpload` | 成片可访问、binding 属本人且连接有效；个人级幂等 |
| `GET /api/qianchuan/uploads?video_asset_id=N` | 现有 `QianchuanUpload[]` | 只列本人上传；列表不泄露他人账号/素材存在性 |
| `POST /api/qianchuan/uploads/{id}/retry` | 现有 `QianchuanUpload` | 仅本人、状态允许且连接仍有效 |
| `GET /api/qianchuan/uploads/{id}/report?days=N` | `upload_id,advertiser_id,material_id,data_topic,start_time,end_time,queried_at,cache_expires_at,cache_status,status,metrics,per_second` | 仅本人；查询前与缓存命中后均验权；无法证明统计归属时不返回冒充个人表现的数值 |

上表中 OAuth callback 是**授权码回调**，不是投放数据推送回调。千川第三方接口、参数与已验证状态沿用 `docs/10-QIANCHUAN-PERSONAL-ACCOUNT-ARCHITECTURE-AND-API.md`，不得凭 SDK 定义推断应用已获权限。第三方响应必须同时检查 HTTP 状态和业务 `code`，保存排障用 `request_id`，但不记录 Token。

## Constraints

1. **安全优先于兼容便利**：前端隐藏无效；后端每个读写入口均须限制本人。禁止以 `advertiser_id`、`video_asset_id` 或企业 owner 身份代替个人连接归属校验。
2. **数据结构稳定**：现有页面报表的指标键、DTO 字段和接口路径不作破坏性变更。若严格隐私导致无法返回个人报表，用明确受控错误及 UI 不可用态，不得把混合汇总塞入现有 `metrics`。新增字段须可选并提供兼容测试。
3. **迁移不丢数据**：先审计现有 connection/binding/upload 归属及实际唯一索引，再设计迁移。对冲突、孤儿记录、无法追溯 owner 的行给出隔离/人工处理清单；不静默转给企业 owner，不直接清空历史。
4. **令牌与并发**：凭证只在服务端加密保存；旧 OAuth 回调、旧刷新请求、运行中任务不得复活已停用/解绑连接。事务与凭证版本检查需覆盖并发竞态。
5. **口径真实**：素材上传成功不等于已投流；`rows=[]` 只表示当前查询窗无匹配行；逐秒接口未验证前保持不可用。不能展示示例数据冒充千川实数。
6. **外部状态**：测试默认使用 mock 和现有只读样本；未经单独确认，不对真实千川账号执行上传、解绑、撤权、创建计划等写操作。生产迁移需备份、预演与明确发布窗口。

## Tests

### 自动化测试（必须新增/更新）

1. **双用户权限矩阵**：同租户 A/B 均有成片权限，分别授权同一/不同广告账号；A 的 `/accounts`、上传列表只含 A，B 同理。A 直接传 B 的连接、binding、upload UUID 请求同步/停用/解绑/上传/重试/报表均不能成功；企业 owner 身份也不例外。无绑定用户仍可发起本人授权。
2. **跨租户与内容权限**：其他租户用户、失去成片访问权用户、停用用户不可访问资源；响应与日志不泄露账户名称、素材 ID、Token 或他人资源存在性。
3. **生命周期竞态**：停用/解绑/重新授权前后，旧 OAuth 回调、旧后台任务、旧凭证刷新、旧报表缓存均不能继续提供数据或复活连接；本人新授权可正常使用。
4. **幂等/迁移**：相同用户相同成片/账号可按业务键复用任务；不同用户即使同账号同成片也不得共用任务。历史连接可追溯者准确归属，无法追溯者隔离；迁移重复执行无副作用，并验证旧索引/新索引兼容。
5. **前端**：绑定入口对普通合格用户可见；管理按钮只指向本人连接；账号切换同步切换状态、素材 ID、报表、记录；空状态、失败、无数据和不可归属报表文案正确。不能仅用前端 mock 证明后端隔离。
6. **报表口径**：mock 验证 `no_data` 与未授权/不可归属的区别；真实只读账号验证非空指标、单位、时间窗和归因。共享素材被他人计划复投的反例不得显示为本人专属数据。

建议运行：在 `backend` 目录执行 `python -m unittest discover -s tests -p "test_qianchuan*.py" -q`；在 `frontend` 目录执行 `npm run test:lineage` 与 `npm run build`。补充迁移演练、API 权限集成测试和双用户手工验收记录；全部结果附在执行报告中。旧 SQLite 库迁移会自动生成相邻备份；旧 PostgreSQL 库须先备份，再显式运行 `python -m scripts.migrate_qianchuan_personal --postgres-backup-confirmed`，不得让启动流程隐式更改旧约束。

## Definition of Done

- [ ] 业务归属、旧数据迁移、唯一约束和回滚方案有代码及测试，生产迁移前完成备份/预演；无法归属的旧记录不对任何普通用户开放。
- [ ] 普通合格用户能完成本人 OAuth、账号同步、推送；本人能停用、解绑、重新授权；其他人（含企业 owner）不能管理其连接。
- [ ] 所有项目内接口、缓存和异步任务通过双用户/跨租户越权测试；直接请求他人资源返回一致的不可见结果，无敏感数据泄露。
- [ ] 成片详情的页面响应字段保持兼容，账号切换时仅显示本人数据；“上传成功”“投流”“无数据”“不可归属”文案不混淆。
- [ ] 共享广告账号/素材的混合汇总不被展示为个人投放表现；若缺计划归属能力，产品以不可用态明确说明，不以“任务完成”掩盖该缺口。
- [ ] 后端与前端自动化测试、构建、迁移演练通过；执行报告列出测试命令/结果、未验证的第三方能力、残余风险和真实账号只读验证证据。

若项目要求“个人投放指标必须非空展示”作为完成标准，则**计划归属接口能力及真实数据验证是前置条件**。在此能力未确认前，可完成个人授权和本地数据隔离，但不得宣称严格的个人投放数据隔离已全部完成。
