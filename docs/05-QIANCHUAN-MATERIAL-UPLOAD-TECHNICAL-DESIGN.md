# 巨量千川素材上传技术设计

> 状态：授权、账户解析、素材上传与核验已实现；验收结果见对应验证记录  
> 基线：`feature/sucai_upload` / `ca8496a`  
> 场景：个人或小团队先验证流程；日期：2026-09-14  
> 本文是实施设计，不代表平台权限、真实授权或上传已经验收通过。

授权阶段记录见 [授权闭环验证记录](06-QIANCHUAN-AUTH-VERIFICATION.md)，上传阶段记录见 [素材上传验证记录](07-QIANCHUAN-UPLOAD-VERIFICATION.md)。现已建立四张表并实现九个 API。

## 1. 评审结论与首版边界

主链路成立，但原稿把账户管理、任务与素材映射、配置和扩容设计铺得过早。首版收敛为：

~~~text
成片详情 → 官方授权 → 识别店铺/代理商 → 选择千川账户
        → 后台上传视频 → 查询确认入库 → 在原页面展示结果
~~~

保留三种授权来源的解析：店铺、代理商、直接广告账户。以现有 `VideoAsset(asset_kind="final_output")` 为源，每次选择一个视频和一个目标账户。只上传素材；图片、批量推送、计划、预算和报表不纳入本次。

| 原方案 | 本次决定 | 原因 |
| --- | --- | --- |
| 5 张表，任务和映射分别保存远端 ID | 4 张表，上传记录同时保存结果映射 | 首版二者生命周期一致，避免双写和冲突 |
| 独立账户管理页、结果页、上传弹窗 | 原成片页 + 上传弹窗 + 轻量授权结果页 | 授权、刷新账户、重新授权、停用连接放在弹窗内 |
| 12 个内部接口 | 9 个接口 | 合并账户/连接查询，统一成片上传记录查询，去掉默认账户写接口 |
| 19 个环境配置项 | 8 个部署配置项，其余作为内部默认值 | 同源地址、单并发、必须核验不需要开关 |
| 5 个服务模块 | 客户端、业务服务、Worker 共 3 个模块 | 暂不拆独立加密服务和账户解析层 |
| Redis 锁、多副本和迁移框架扩展 | 复用 PostgreSQL，单后端进程、一个 Worker | 首版无需增加基础设施或改造通用任务框架 |
| 独立业务审计表、取消任务、租户默认账户 | 后置 | 本次用上传记录和脱敏事件完成追踪 |
| state、Token 加密、租户检查、幂等、异常恢复 | 保留并修正 | 这些直接关系到账户归属、凭据和重复上传 |

首版验收：从真实授权到一条短 MP4 在目标千川素材库可查，刷新页面不丢状态，重复点击不重复发起上传，授权失效有明确提示，超时或重启不会盲目重传。店铺、代理商、直接账户各自记录验收状态；没有相应测试账号的分支只能标记“Mock 通过、真实待验”。

部署边界是一个应用进程、一个千川 Worker，上传并发固定为 1。这是功能验证方案，不承诺高并发吞吐；队列等待时间也不等于上传时间。多个团队同时批量使用或增加后端副本前，重新设计领取、租约、限流和容量。

## 2. 核验依据与仍待确认的接口

已比对现有代码及巨量引擎官方 Java SDK 1.1.95，固定版本见[官方源码](https://github.com/oceanengine/ad_open_sdk_java/tree/6122a5fe2096ddc673f1e7a553bc5d3437645025)。SDK 可证明路径、字段和枚举存在，不能证明当前应用已获权，也不能证明任意广告账户都支持千川上传。

| 编号 | 接口 | 使用方式 |
| --- | --- | --- |
| A01 | `POST /open_api/oauth2/access_token/` | `app_id + secret + auth_code`，当前请求模型没有 `redirect_uri` |
| A02 | `GET /open_api/oauth2/advertiser/get/` | `access_token` 位于查询参数；读取授权对象列表 |
| A03 | `POST /open_api/oauth2/refresh_token/` | 按响应保存新 Token 对及各自有效期 |
| A04 | `GET /open_api/v1.0/qianchuan/shop/advertiser/list/` | 输入 `shop_id`，读取完整分页 |
| A05 | `GET /open_api/2/agent/advertiser/select/` | 支持 `advertiser_id`、`company_ids` 及分页；代理身份传参需真实样本确认 |
| A06 | `POST /open_api/2/file/video/ad/` | multipart 文件上传 |
| A07 | `GET /open_api/v1.0/qianchuan/video/get/` | 按目标账户及 `signatures/material_ids/video_ids` 查询 |
| A08 | `GET /open_api/v1.0/qianchuan/advertiser/type/get/` | 查询千川账户类型；不能单独证明上传权限 |
| A09 | `GET /open_api/v3.0/customer_center/account/list/` | 工作台账户 ID + `filter={"account_type":"QIANCHUAN"}`，完整分页读取 `accounts` |

账户解析规则：

- A02 使用 `account_id/account_name/account_type/is_valid`；不依赖已废弃的 `advertiser_role` 等字段。
- A04 明细字段为 `adv_id_list[].adv_id/adv_name`，并兼容仅返回 ID 的 `list`；A02 授权公司的 ID 字段为 `company_list[].customer_company_id`。A08 返回 `list[].advertiser_id/ecp_type`，当前对 `AGENT` 类型保守拒绝选择，需真实样本确认其用途。
- `PLATFORM_ROLE_SHOP_ACCOUNT` 走 A04；已确认的千川代理类型走 A05。普通 `AGENT` 等类型只有在官方说明或真实样本确认后才加入分支，不能全部当成千川代理。
- `CUSTOMER_ADMIN/CUSTOMER_OPERATOR` 是工作台管理身份，走 A09 查询其下千川账户；只接受明确的 `account_type=QIANCHUAN`，再经 A08 核验类型。不得把工作台自身 ID 当成投放账户。本次真实工作台返回零账户，只验证了该分支的空结果，非空结果仍由契约测试覆盖。
- A02 的 `company_list` 为空表示该代理商下所有业务账户可访问，非空则必须限制客户范围；缺字段不能擅自等同于空列表。A05 的参数映射、响应形态及分页在真实代理商验收前固定为契约样本。
- A05 返回的一般广告账户、A02 直接返回的 `ADVERTISER`，都要进一步确认千川类型和授权来源；A08 只是类型检查。没有可靠类型/范围证据时不允许选择，不能靠试传来猜。
- 应用还必须获批 A06/A07 所需权限。账户查询成功不等于上传获权；权限错误必须停下并说明原因。
- 所有分页成功后才在一个事务内替换该连接的绑定快照。途中失败保留历史记录并阻止使用未确认的新范围；不把半页结果当完整授权。
- 重新授权明确传已有 `connection_id`，更新指定连接；新建授权另建连接。不根据账户名称猜测“同一登录账号”并自动合并。
- 平台 ID 在数据库和前端边界统一使用十进制字符串，避免 JavaScript 大整数精度丢失。

A06 携带 `advertiser_id`、`upload_type=UPLOAD_BY_FILE`、`video_file`、`video_signature`，以及文件名和真实的 `is_aigc` 标记。读取 `material_id/video_id/video_signature` 等结果。现有 `content_hash` 是 SHA-256，上传签名单独按平台契约计算 MD5。

原稿的“500 MB 上限”和“180 秒超时”不是已确认的平台承诺。首轮采用小体积 MP4；支持格式、体积、时长、接口超时、上传和查询权限由开发者后台当前文档及真实样本决定。A06 SDK 注释出现 10 秒超时说明，不能仅增加客户端超时就声称支持大文件。此前引用的官方网页未完整读取，不能作为这些限制已确认的依据。

## 3. 最小实现结构

~~~text
React 成片详情/上传弹窗
        │ 创建授权、选账户、创建任务、轮询结果
        ▼
FastAPI API ────────── PostgreSQL（4 张表）
        │                        ▲
        │ OAuth 回调换 Token      │ 领取、保存状态
        ▼                        │
巨量引擎 ◀──────── 单个千川 Worker
                    账户同步 → Token 刷新 → 取文件 → 上传 → 核验
~~~

复用现有登录、`tenant_id`、PostgreSQL、OSS 和 `httpx`。新增一个小型 Worker；现有 `production_queue.py` 与创意生成流程耦合，且重启时直接重排 running 任务，不适合直接承载“可能已经上传成功”的任务。无需为此改造全项目队列。

账户同步接口只写入数据库待同步标记；所有 A02-A08 调用由同一 Worker 串行执行，A01 是回调内唯一的平台调用。空闲使用可中断的定时等待扫描数据库，不新增 Redis 依赖。

数据库短事务领取任务，使用 `WHERE status=queued` 条件更新避免重复领取；执行网络调用前提交事务。首版部署必须遵守单进程限制，不能把这一条件更新宣传为完整的多副本恢复机制。

新增代码落点：

~~~text
backend/app/api/qianchuan.py
backend/app/services/qianchuan/client.py    # 明确封装 A01-A08、错误分类及日志脱敏
backend/app/services/qianchuan/service.py   # OAuth、加密函数、账户解析、幂等入队
backend/app/services/qianchuan/worker.py    # 同步、刷新、上传与恢复
frontend/src/components/QianchuanUploadModal.tsx
frontend/src/pages/QianchuanOAuthResult.tsx
~~~

修改现有 models、config、main、API 类型、App 路由及 `FinalVideoDetail.tsx`。新增表沿用当前 `create_all`；若后续改变已部署表结构，再为实际变更提供版本化迁移，不在本次先引入全项目迁移工程。

## 4. 授权回调与配置

### 4.1 回调地址和开发者后台

候选公网回调：

~~~text
https://waynepartners.xyz/api/qianchuan/oauth/callback
~~~

该域名来自仓库 `deploy/DEPLOY_HANDOFF.md`，本次没有验证线上部署和应用后台登记状态。落地时确认其确实对应本实例，再在开发者后台与 `QIANCHUAN_REDIRECT_URI` 填入完全一致的值，包括协议、域名、路径和末尾斜杠。

授权地址、参数格式及 scope 从当前应用后台核对；`material_auth` 是否需要取决于获批接口权限，不能只凭“自有视频”判断必然不需要。配置支持官方基础地址或完整授权链接；仅接收 `app_id/state/scope/redirect_uri/material_auth/rid`，保留明确配置的平台参数，重新生成每次会话的 state，拒绝重复参数和配置冲突。不增加部署开关。实际授权范围及 rid 与回调的对应关系须在真实平台确认。

首版复用已具备的稳定 HTTPS 入口，不强制搭建独立测试环境。本地先用 Mock；真实联调使用平台认可且浏览器可访问的登记地址。平台是否允许 localhost/隧道应现场核对，本文不把工程建议写成平台禁令。若以后增加测试环境，再分离凭据、数据库和回调。

部署配置只保留：

~~~env
QIANCHUAN_ENABLED=false
QIANCHUAN_APP_ID=
QIANCHUAN_APP_SECRET=
QIANCHUAN_AUTHORIZATION_URL=<应用后台确认的官方授权地址>
QIANCHUAN_REDIRECT_URI=https://waynepartners.xyz/api/qianchuan/oauth/callback
QIANCHUAN_SCOPE=<按后台要求填写>
QIANCHUAN_TOKEN_ENCRYPTION_KEY=
QIANCHUAN_UPLOAD_MAX_MB=<完成平台限制确认后设置的本系统上限>
~~~

API 主机固定为官方地址；前端使用回调地址的同源 origin。state 有效期 10 分钟、上传并发 1、安全重试最多 3 次总尝试、上传后必须核验，作为内部默认值。HTTP 客户端必须有连接/读写/总耗时边界，按接口分别设置并通过真实样本验证，不暴露一组尚未验证的超时旋钮。

Token 用成熟加密库的认证加密方案保存，使用独立密钥，不能复用 `AUTH_SECRET`。密文包含格式版本；本次不建密钥管理服务。密钥稳定保存于服务端配置，重启不重新生成；轮换时应重加密存量 Token 或要求重新授权。

启用时检查必需配置、固定回调路径、官方授权主机及密钥格式；公开域名必须 HTTPS，仅允许下述显式本地开发例外。无效时只关闭千川入口/API/Worker，原应用正常启动；健康接口报告 `qianchuan=invalid_configuration` 和缺失项名称，不返回秘密。禁用时无需千川凭据。

### 4.1.1 本地测试回调地址

沿用仓库现有前端入口，浏览器通过前端代理访问回调，保证登录、Cookie 和授权结果页使用同一个 origin：

| 本地运行方式 | 浏览器入口 | `QIANCHUAN_REDIRECT_URI` |
| --- | --- | --- |
| Vite 开发，首选 | `http://127.0.0.1:5173` | `http://127.0.0.1:5173/api/qianchuan/oauth/callback` |
| Docker Compose，默认端口 | `http://127.0.0.1:8080` | `http://127.0.0.1:8080/api/qianchuan/oauth/callback` |

Vite 已在 `frontend/vite.config.ts` 将 `/api` 转发到 `http://127.0.0.1:8000`；Docker 前端 Nginx 已转发到 `http://backend:8000`。后端 `:8000` 可单独探测 API，但不作为浏览器授权回调配置，否则按同源规则生成的授权结果页也会回到后端端口。

Vite 本地开发时，在后端实际读取的 `backend/.env` 中覆盖以下值；Docker 使用 Compose 指定的环境文件，并把端口改为 8080：

~~~env
DEBUG=true
QIANCHUAN_REDIRECT_URI=http://127.0.0.1:5173/api/qianchuan/oauth/callback
~~~

复用项目已有 `DEBUG` 设置，不新增千川配置项。HTTP 例外只在 `DEBUG=true` 且配置 origin 精确属于 `http://127.0.0.1:5173`、`http://localhost:5173`、`http://127.0.0.1:8080`、`http://localhost:8080` 时启用；路径仍固定，禁止查询串、fragment 或 userinfo。生产部署 `DEBUG=false`，即使请求伪造 Host/代理头也不能触发例外。Vite 启动时使用 `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`，避免端口被占用后悄悄换端口，造成回调与页面不一致。

本地 HTTP 模式使用 `qc_oauth_<session_id>` Cookie，保留 `HttpOnly; SameSite=Lax; Path=/`、10 分钟有效期和 nonce 哈希校验，不设置 `Secure` 或 `Domain`。HTTPS 模式仍使用 `__Host-` 前缀及 Secure，不提供生产降级开关；`__Host-` 的属性限制见 [MDN Set-Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#cookie_prefixes)。切换 Cookie 名称时，创建、读取和清理必须使用同一套规则。测试中不要混用 `localhost` 与 `127.0.0.1`；若选择 localhost，则入口、回调配置和浏览器登录均统一为 localhost。

区分两种验证方式：

- **本地 Mock**：由测试启动入口注入平台假客户端、测试凭据和允许列表内的模拟授权页，只验证回调处理与页面流程；真实平台客户端不得加载真实凭据。常规 `QIANCHUAN_ENABLED=false` 会关闭功能，不能直接据此验证回调。测试入口可覆盖功能开关及授权主机校验，生产入口不能启用这些覆盖。
- **本地真实授权**：只有开发者后台接受上述完整 HTTP loopback 地址时，才可将它登记为真实回调；本次尚未确认平台是否允许。浏览器须与本地服务处于同一台电脑。若后台要求 HTTPS 域名，使用已获准的 HTTPS 开发域名/隧道，将整个前端入口（含 `/api`）代理到本机 5173 或 8080，不能只代理回调路径。从该 HTTPS 域名登录并发起授权，回调和结果页也使用该域名，Cookie 恢复 HTTPS 策略。使用 Vite 时按实际版本配置允许的开发域名，不能无条件放开所有 Host。

本地验收：无参数访问配置的回调应命中 FastAPI 并返回受控 400；正常 Mock 会话应校验 Cookie、只换一次 Token，并 303 回跳同端口结果页；缺 Cookie、重复回调、混用主机名应拒绝；关闭 DEBUG 后应拒绝 HTTP 回调配置。这些回调行为已由接口测试覆盖；隔离的浏览器 Mock 验证通过，见验证记录。

### 4.2 回调处理契约

浏览器跳转回调不会携带前端 localStorage 中的 Bearer Token；因此回调是公开路由。仅有随机 state 并不足够，还要绑定发起授权的浏览器，否则转发授权链接可能把他人的授权关联到错误的租户。这个要求来自 [OAuth 安全最佳实践 RFC 9700 §4.7](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.7)。

1. owner 登录后调用创建授权会话接口。服务端生成随机 state 和独立的浏览器 nonce，各至少 32 字节。
2. 数据库保存二者哈希、发起用户、租户、目标连接及原成片 ID；发起用户和成片均须属于有效租户。回跳上下文用资源 ID，不接收任意 return URL。
3. HTTPS 模式将 nonce 放入本次会话专属 Cookie：`__Host-qc_oauth_<session_id>`，`Secure; HttpOnly; SameSite=Lax; Path=/`，不设置 Domain，有效期 10 分钟；本地 HTTP 只使用 4.1.1 的限定例外。采用同一浏览器顶层跳转完成授权。
4. 用标准 URL 编码器生成授权 URL；其中回调只来自固定配置。前端或 Host/X-Forwarded-* 头不能覆盖它。
5. 回调验证 state、Cookie、有效期及原发起用户仍有 owner 权限，再用条件更新原子消费会话。缺失或无效返回受控 400，禁止调用 A01。
6. 平台取消或报错时保存脱敏结果；合法 auth_code 仅调用一次 A01，立即加密落库。若请求可能已送达但结果未知，标记需要重新授权，不自动重放授权码。
7. Token 落库与连接“待同步”标记同事务提交，Worker 后续解析账户。回调不等待全部账户分页。
8. 返回 303 到固定 `/qianchuan/oauth/result?session_id=<随机ID>`，清理 nonce Cookie。结果页带本系统登录态查询，必须是原用户和租户；登录过期时先登录再查询。
9. 响应设置 `Cache-Control: no-store` 和 `Referrer-Policy: no-referrer`；不携带 Token、授权码或原始平台错误，不加载第三方资源。成功状态以数据库为准。

重新授权替换指定连接的凭据并使旧绑定暂不可用，待完整同步后启用新范围；历史上传结果保留。同一连接只保留最新的待处理授权会话；提交凭据时再次校验当前授权会话 ID，旧回调即使已开始换 Token，也不能覆盖新授权。

### 4.3 代理和日志的最小改动

仓库记录的链路是：浏览器 HTTPS → 宿主机 Nginx → 127.0.0.1:8080 → 容器 Nginx → FastAPI。现有 `/api/` 已代理到后端，回调不得落入 SPA fallback。容器内 `X-Forwarded-Proto $scheme` 会变成 http，不能假定它保留了外层 HTTPS；本功能固定使用配置 origin，并按配置环境决定 Cookie 策略，不依赖这一头生成地址或降低 Cookie 保护。

两层 Nginx 和 Uvicorn 都要对回调日志做处理：access log 只留路径或对该路径关闭；同时检查错误日志、异常堆栈和 HTTP 客户端日志，避免查询串或请求正文泄露。A02 本身也在 URL 中携带 Token，不能打印完整平台 URL。只关 Nginx access log 不能算完成脱敏。

复用现有 `AuditLog` 的路径/状态/耗时记录，并增加脱敏结构化业务事件（actor、tenant、connection/upload ID、平台 request ID、错误分类），不新增审计表。采用服务端字段白名单，不直接记录平台完整响应。

后端不直接暴露公网；涉及代理身份判断时只信任已知代理。Nginx 的 320 MB 请求体限制作用于浏览器到本系统的上传，不是后端从 OSS 取已有文件再传平台的大小上限，无需因此扩大 Nginx 限制。

## 5. 四张表与并发边界

所有查询和关联同时校验 `tenant_id`。owner 管理授权；owner/member 查看本租户账户和创建上传。公开回调通过会话绑定归属，不能接受传入的用户或租户 ID。

| 表 | 最小职责与关键字段 |
| --- | --- |
| `qianchuan_oauth_sessions` | 随机 ID、tenant/user、state_hash、browser_nonce_hash、目标 connection、原成片 ID、状态、有效期、消费时间、脱敏错误 |
| `qianchuan_connections` | tenant/creator、当前授权会话 ID、加密 Token 对和有效期、credential_version、待同步/有效/同步失败/需重授权/停用状态、授权范围快照、最近同步时间 |
| `qianchuan_account_bindings` | tenant/connection、来源账户 ID/类型及授权公司范围、advertiser_id/名称/类型、有效标记 |
| `qianchuan_uploads` | tenant/actor、video_asset_id、binding_id、advertiser_id、源 SHA-256、上传 MD5、名称/AIGC、状态/阶段、重试计数及下次执行时间、远端素材 ID/视频 ID/元信息、错误及平台 request ID、时间戳 |

会话只需 `pending/processing/completed/failed`：取消、过期、交换结果不明用 error_code 区分。completed 表示凭据已保存，账户是否就绪由连接状态决定；页面不能显示“已可上传”直到同步成功。重启遇到 processing 会话，已有持久化连接结果则恢复查询，否则要求重新授权。

绑定唯一键为 `(tenant_id, connection_id, source_account_id, advertiser_id)`。一个授权可解析出多个账户；弹窗按来源分组，明确选择目标账户，不做默认账户写入。

上传唯一键为 `(tenant_id, video_asset_id, source_content_hash, advertiser_id)`。上传记录即本地素材到远端素材的映射，远端 ID 一收到就保存，不能等查询成功才写。两个本地素材即使复用了同一远端 material_id，也允许各自保留关联，因此不对 `(advertiser_id, material_id)` 再加唯一约束。

源内容变化或目标 advertiser 变化才新建记录。同一请求返回已有记录；同一幂等键但文件名/AIGC 不一致时明确返回已有参数或冲突，不静默声称修改成功。绑定失效后仍保留历史；重试可指定本租户同一 advertiser 的新 binding，必须重新校验当前授权，不能借重试更换目标账户或源内容。

Token 刷新只由单个 Worker 执行，临期时按平台有效期刷新，并原子保存新 Token 对。回调和停用动作会递增 `credential_version`；刷新结果只在版本未变时落库，防止旧刷新覆盖刚完成的重新授权。明确失效或刷新结果不确定时停止新调用、提示重新授权，不无限重用旧 Refresh Token。

## 6. 上传状态与失败恢复

创建任务的 HTTP 请求只做数据库归属、源状态、元信息和幂等检查，返回 202；真正下载文件、哈希和平台调用放到 Worker，避免“后台上传”的创建接口先阻塞几分钟。

Worker 重新检查源/账户有效性，从可信存储对象获得任务专用临时文件，校验实际大小和 SHA-256，再计算 MD5。不能接受浏览器提供的文件路径/OSS Key/Token/advertiser_id，也不能让其他任务的缓存清理删除正在传输的文件。传输结束释放文件并清理临时目录。

| 状态 | 含义及后续动作 |
| --- | --- |
| `queued` | 等待 Worker；符合安全重试条件时带 next_attempt_at |
| `running` | 处理中，phase 仅用于区分 preparing/uploading；进入 uploading 必须先落库 |
| `uploaded` | A06 明确返回成功，远端 ID 已保存；等待或继续 A07 核验 |
| `succeeded` | A07 在目标账户确认对应素材存在 |
| `unknown` | A06 结果不确定；只自动查询核对，禁止自动再调 A06 |
| `failed` | 明确未成功或本地校验失败；按错误分类决定能否重试 |

失败处理按“是否可能产生平台副作用”区分：

- 文件缺失/版本变化、参数错误、账户无权：明确失败，提示具体修复方式。
- 能确认请求未发送，或平台明确拒绝且允许重试：最多 3 次总尝试，间隔 5s/30s 加抖动；不笼统把所有 5xx 都当作可重传。
- 超时、断连或进程在 uploading 阶段中断：进入 unknown，按目标账户和 MD5 查询全部相关分页。不能用过窄时间窗漏掉平台复用的旧素材。
- A06 已成功但 A07 超时/暂不可见/无查询权限：保留 uploaded 和远端 ID，展示“已上传，待核验”及原因，只重试查询。
- 查询采用有限次数退避；查不到不等于上传失败。耗尽查询后暂停并供人工核验；只有用户明确接受可能重复的风险并选择重传，才允许重新发送 A06，并记录操作人。
- 重启：preparing 可重排；uploading 转 unknown；uploaded 只继续查询；账户同步重新执行完整只读同步。不得无条件把全部任务改回 queued。
- 停用连接或重新授权会阻止新的平台调用；已经发送的上传不能保证撤回，因此仍保存其返回结果。

不做上传百分比承诺、任务取消或自动删除远端素材。前端显示排队/处理阶段及确定结果；页面可见且有可自动推进任务时轮询，隐藏或进入待人工处理时暂停。

## 7. 九个内部接口与交互

前缀为 `/api/qianchuan`。除回调外均需要本系统登录态。

| 方法与路径 | 说明 |
| --- | --- |
| `POST /oauth/sessions` | owner 发起新授权或指定 connection 重新授权，设置浏览器 Cookie |
| `GET /oauth/callback` | 校验绑定、一次性换 Token、固定回跳 |
| `GET /oauth/sessions/{id}` | 原发起用户查询会话及关联账户同步状态 |
| `GET /accounts` | 一次返回本租户连接状态及按来源分组的可选账户，不返回秘密 |
| `POST /connections/{id}/sync` | owner 请求完整同步，只写待处理标记 |
| `DELETE /connections/{id}` | owner 在本系统停用，保留历史；不等同于平台撤权 |
| `POST /uploads` | 提交 video_asset_id、binding_id、名称、is_aigc，幂等入队 |
| `GET /uploads?video_asset_id=...` | 返回本成片任务及结果，兼作轮询和历史查询 |
| `POST /uploads/{id}/retry` | 明确失败时安全重试；uploaded/unknown 默认只核验；人工重传需显式确认 |

原成片页按钮打开弹窗：无授权则引导绑定，有授权则按来源选择账户；owner 可刷新、重新授权、停用连接。member 未获可用连接时提示联系 owner。小团队需要的管理动作保留，暂不增加侧栏菜单和独立管理页。

提交后在原成片详情展示目标账户、状态、千川 material_id/video_id 和时间。新增轻量授权结果页只负责读会话、显示失败原因或等待账户同步，完成后回到原成片。当前详情中的投放示例数据不作为本次上传成功的证明。

## 8. 验收与实施顺序

### 8.1 自动化验收（开发后执行）

平台请求用可注入的 `httpx.MockTransport` 等替代，测试行为而非复制实现：

| 场景 | 必须观察到的结果 |
| --- | --- |
| state 缺失、过期、重复、并发；Cookie 缺失/跨浏览器；owner 已失权 | 只有有效归属的一次回调调用 A01，其余受控拒绝 |
| 回调 URL、取消、交换超时、结果页越权查询 | 固定同源回跳，错误脱敏，授权码不重放，跨用户不可读 |
| 本地 5173/8080 回调、HTTP Cookie、端口/主机名变化、DEBUG 关闭 | 正常流经前端代理并同源回跳；Cookie 归属不匹配或非开发 HTTP 配置拒绝 |
| 三类授权对象、未知类型、多页结果、company_list 缺失/为空/非空、部分失败 | 类型不猜测，范围不扩大，完整同步才生效 |
| A01/A03 加密、临期刷新、回调与刷新竞争 | 无明文 Token；旧版本不能覆盖新授权 |
| 并发重复创建、源内容变化、不同目标账户、同远端素材关联不同本地素材 | 相同键只有一条记录；合法独立记录不冲突 |
| 上传实际文件、MD5、源 SHA-256、AIGC、体积和授权归属 | 字节和参数匹配；越权/变更/超限不发上传 |
| 上传丢响应、查不到、平台 5xx、已上传但无查询权限 | 不自动重传不明结果；已有远端 ID 不丢失 |
| 在 preparing/uploading/uploaded 各阶段重启、停用连接 | 分阶段恢复，停用阻止新调用，保留已发生结果 |
| API、日志、配置失败及前端状态 | 不泄露秘密；千川关闭不影响主应用；类型检查与构建通过 |

### 8.2 真实验收（依赖应用配置和测试账户）

1. 先确认 A05 的身份/授权范围、A06/A07 权限及媒体限制；缺少条件的分支单列待验，不能用 Mock 代替平台证明。
2. 登记固定回调，在功能实现并部署后，无参数访问应命中 FastAPI 返回受控 400；检查 TLS、代理、Cookie、回跳和各层日志。当前尚无该路由，不应期待现在就返回 400。
3. 真实完成可用账户类型的授权，对比后台可见账户；撤权/重新授权后确认范围变化。
4. 上传一条有权使用、专门命名的短 MP4，在官方素材库按目标账户确认；同时检查重复点击、Token 刷新和页面刷新后的结果。
5. 通过受控故障验证上传丢响应/重启先核验；确认另一个租户无法读取或使用这些数据。记录测试素材 ID，便于后续人工清理。

实施顺序：先锁定真实接口契约与回调配置 → 做授权和账户选择 → 接一条视频的持久化上传与核验 → 完成上述测试。已实现持久化上传与核验，真实权限和样本结果以验证记录为准。

后续只在需求出现时引入：独立账户页、批量/图片上传、任务取消、独立 Worker、多应用配置和密钥轮换管理。扩容方案届时依据实际队列等待、失败率、文件大小与吞吐测量确定，PostgreSQL 已是本项目现有基础设施。

## 9. 关键来源

- [授权 Token 请求模型](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/model/Oauth2AccessTokenRequest.java)
- [授权对象及代理公司范围模型](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/model/Oauth2AdvertiserGetResponseDataListInner.java)
- [代理账户查询参数](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/api/AgentAdvertiserSelectV2Api.java)
- [上传视频接口](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/api/FileVideoAdV2Api.java)
- [千川素材核验过滤条件](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/model/QianchuanVideoGetV10Filtering.java)
- [千川账户类型查询](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/api/QianchuanAdvertiserTypeGetV10Api.java)
- [OAuth 安全最佳实践](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.7)
