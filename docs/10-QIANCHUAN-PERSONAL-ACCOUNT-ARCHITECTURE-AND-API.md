# 千川个人账户业务架构与页面接口契约（设计稿）

核对日期：2026-09-22。范围：当前项目的千川授权、账户、成片推送、素材报表页面。本文是**待实施设计**，不是已上线能力。平台接口的“SDK 有定义”“真实请求成功”“返回非空业务数据”分开标记。

## 1. 业务决定与边界

1. 一个千川授权连接属于**发起授权的登录用户**。企业/租户只决定成片、商品等内容能否访问，不赋予企业内其他人查看或操作该用户的千川账户、素材推送和投放数据的权利。一个广告账户可以被多人分别授权；他们的授权连接与可见性互相隔离。是否允许同一人重复绑定同一广告账户需定去重规则，不能用企业级唯一约束误伤。
2. 所有有权进入成片页的登录用户都能看到“绑定千川”入口并发起**自己的**授权；连接列表、投放账户选择、同步、重新授权、停用、解绑，只能作用于本人连接。企业 owner 不自动接管别人的连接。
3. “停用”是本地禁用：清除本地令牌、作废绑定与待执行任务；“解绑/删除”是本地移除：不保证撤销千川平台侧授权。若需要远端撤销，必须另有平台契约和实际验证，不能把本地删除写成平台撤权。“重新授权”启动本人连接的新 OAuth 会话，旧会话不得覆盖新凭证。
4. 成片本身仍按既有企业权限可见；千川状态、上传历史、账户名称/ID、素材 ID、报表和缓存只对该授权连接的本人可见。前端过滤只是体验，**后端所有列表、详情、重试、报表、异步任务均须校验** `tenant_id + owner_user_id + connection_id/binding_id`。资源不存在或非本人统一返回 404，避免枚举。
5. `上传成功` 仅表示视频进入千川素材库且核验通过，**不等于**创建广告或发生消耗。当前页面“已投流账户”若由 `upload.status=succeeded` 推出，文案和语义都有误。建议分别展示“已上传到千川的账户”和“有报表数据的账户”；`rows=[]` 表示指定时间窗内无匹配行，不能推断素材从未投流。
6. 当用户离职/失去企业内容权限、连接停用或解绑时，后续平台请求应停止，Token 不能继续被后台任务使用。历史记录保留用于审计，但仅原归属用户在仍有内容权限时可见；解绑后报表实时拉取不可用。若业务要支持企业交接、管理员审计、历史缓存留存时长，应单独定义例外授权，而不是隐式放开本人的账户数据。

**重要边界：个人绑定保证本项目不把 B 的连接/任务/缓存显示给 A，但不能自动把同一个千川广告账户的上游统计按“操作者”拆分。**当前报表只按广告账户 + 素材 ID + 时间窗查询；若 A、B 都被授权操作同一广告账户，或 A 的素材被 B 的计划复用，千川返回的该素材汇总可能包含双方投放贡献。要严格实现“我不能看到别人投的数据”，须进一步定义按计划/广告创建者归属的明细接口及可用维度，并以这些计划 ID 查询/汇总；在该能力验证前，只能承诺**本地记录和入口隔离**，不能承诺共享广告账户内的上游投放指标隔离。最稳妥的运营规则是个人专用广告账户，或不在共享账户展示无法归属的汇总报表。

### 推荐数据归属与迁移

现有 `QianchuanConnection.created_by` 可迁移/明确为不可随意变更的 `owner_user_id`；`QianchuanAccountBinding` 归属由连接唯一确定，建议显式存 `owner_user_id` 并校验一致；`QianchuanUpload.created_by` 是操作人，不能取代账户归属，另存或通过 binding 追溯 `owner_user_id`。所有查询和缓存键含 owner。异步任务领取后及每次平台请求前复验所有权、连接状态、凭证版本。数据库唯一键当前在企业级对 `(video_asset_id, source_content_hash, advertiser_id)` 去重；个人方案需加入 owner/connection，避免另一人的推送任务被复用。迁移既有记录时以连接 `created_by` 为归属，无法追溯的记录先隔离、人工核实，不允许自动给企业全部人看。

## 2. 当前实现与目标差异

| 页面/服务 | 现在 | 目标（返回字段不变） |
| --- | --- | --- |
| 推送弹窗“添加官方授权” | `data.can_manage` 仅企业 `owner` 为真，其他用户看不到；同步/停用/解绑同样受限 | 所有可使用成片的用户可发起自己的绑定；列表中的操作只针对本人连接；`can_manage` 改为当前用户是否能管理自己的连接，不再代表企业 owner |
| `GET /api/qianchuan/accounts` | 按 `tenant_id` 返回全企业连接与账户 | 保持 `configuration/can_manage/connections[].accounts[]` 结构，仅返回本人连接 |
| `GET /api/qianchuan/uploads` | 按企业+成片列出所有上传 | 保持 `QianchuanUpload[]` 结构，仅列本人连接的上传；详情页顶部状态、选择器、历史因此仅显示本人 |
| 上传/重试/报表详情 | 能按企业找到 binding/upload，缺少本人连接校验 | 写入和读取均验证 owner；直接构造他人的 `binding_id`/`upload_id` 也拿不到数据 |
| 报表缓存和后台任务 | 缓存键以企业、binding、advertiser、素材、凭证版本为主 | 在认证前后和缓存命中后复验 owner；缓存键加 owner；禁用/解绑/转权使旧任务失效 |

依据：`backend/app/services/qianchuan/service.py`、`uploads.py`、`reports.py`，`frontend/src/components/QianchuanUploadModal.tsx`、`frontend/src/pages/FinalVideoDetail.tsx`。现有推送弹窗内已经有停用和本地移除按钮，主要问题是个人用户看不到且服务按企业授权，不是接口完全消失。

## 3. 当前页面数据从哪里来、能否取得

| 页面数据 | 直接来源 | 千川上游来源 | 实证与限制 |
| --- | --- | --- | --- |
| 成片、视频文件、商品及本地分类标签
| 项目自身数据库 | 无 | 千川授权不应成为本地分类标签加载前置条件；该数据不属于千川广告账户隐私 |
| 本人绑定账户、授权状态、名称、类型 | 本地 connection/binding | OAuth 换 Token、授权对象、店铺/代理商/工作台账户发现、账户类型 | 主路径有真实成功；工作台接口成功但返回空；代理商路径缺真实样本。账户列表必须本地按本人过滤 |
| 成片推送状态、素材 ID、视频 ID | 本地上传任务 | 视频上传和视频素材查询 | 已真实上传并核验；只能证明入库，不证明投放 |
| 投放表现、视频质量、互动汇总 | 本地报表适配层 | 全域推广报表配置 + 报表数据 | 真实 `code=0`，但样本 `rows=[]`；非空值、金额单位、归因口径未验证。字段是否可用取决于该广告账户的配置 |
| 逐秒点击、关注、评论、点赞、流失 | 当前页面固定显示不可用 | 候选旧接口 `video_user_lose/get` | 官方 SDK 仍有接口与字段契约；旧应用实测 `code=40002` 无权限，新应用当前未完成带本人有效授权的非空实测。不能声称“已可返回”，也不能声称“全平台下线” |

**回调辨析：**当前 `GET /api/qianchuan/oauth/callback` 只是浏览器授权码回调；上传状态由本地后台任务记录，投放数据是按需**拉取** `uni_promotion/data/get`。当前代码没有千川“报表推送回调/Webhook”消费链路，不能把回调当成投放指标来源。

## 4. 项目自身接口（拟维持前端数据结构）

以下路径均以 `/api/qianchuan` 为前缀。所有登录接口还需既有成片/企业内容权限；凡涉及连接、binding、上传的操作再校验本人账户归属。响应无 Token、密钥、其他用户账户。

| 方法/路径 | 请求 | 主要响应 | 条件与目标权限 |
| --- | --- | --- | --- |
| `POST /oauth/sessions` | JSON `video_asset_id?` 正整数、`connection_id?` UUID | `session_id`,`authorization_url` 等现有会话字段；HttpOnly nonce cookie | 新绑定向所有合格用户开放；重新授权时 `connection_id` 必须本人所有；同源和 state/nonce/过期校验 |
| `GET /oauth/callback` | 平台重定向 `state` + `auth_code` 或 `error` | 303 跳至授权结果页 | 只接受本人发起且未过期会话；刷新/停用竞态不得覆盖新凭证；不是报表回调 |
| `GET /oauth/sessions/{id}` | 路径 UUID | `session_id,status,error_code,message,connection_id,connection_status,connection_error,return_path` | 仅发起人可查 |
| `GET /accounts` | 无 | `configuration,can_manage,connections:[{id,status,error,warnings,last_synced_at,accounts:[{id,advertiser_id,advertiser_name,source_account_id,source_account_name,source_account_type}]}]` | `connections` 只含本人，保留原字段/类型；可绑定入口不应因本人暂无账户而隐藏 |
| `POST /connections/{id}/sync` | 路径 UUID | `{"status":"accepted"}`，HTTP 202 | 仅本人；连接有效且可同步 |
| `DELETE /connections/{id}` | 路径 UUID | HTTP 204 | 仅本人；本地停用并清令牌、使绑定及任务失效，不宣称远端撤权 |
| `POST /connections/{id}/remove` | 路径 UUID | HTTP 204 | 仅本人；本地解绑/删除，保留受限历史记录；当前路径不变以兼容前端 |
| `POST /uploads` | JSON `video_asset_id` 正整数、`binding_id` UUID、`filename?`、`is_aigc` 布尔 | 原 `QianchuanUpload`，HTTP 202 | 成片可访问、binding 属本人且连接 active；幂等范围含本人/连接 |
| `GET /uploads?video_asset_id=N` | 成片 ID | `QianchuanUpload[]`：`id,video_asset_id,binding_id,advertiser_id,advertiser_name,filename,is_aigc,status,phase,material_id,video_id,error_message,created_at,updated_at,auto_pending,can_retry,can_verify` | 仅本人上传；不能泄露其他人的账户或存在性 |
| `POST /uploads/{id}/retry` | 上传 UUID | 原 `QianchuanUpload`，HTTP 202 | 仅本人且状态允许重试、连接仍有效 |
| `GET /uploads/{id}/report?days=N` | `days` 1–180，页面用 7/30 | `upload_id,advertiser_id,material_id,data_topic,start_time,end_time,queried_at,cache_expires_at,cache_status,status,metrics:{field:{label,value}},per_second` | 仅本人且上传已核验、凭证有效；`status=no_data` 时指标为 `null`，不造零；缓存 1 小时，命中也复验权限 |

上述 `QianchuanUpload` 字段与 `frontend/src/api/client.ts`、后端 `UploadService.view()` 一致；本设计不添加破坏性字段。建议客户端将“账号选择”与“我的授权”放在同一可发现入口，但不要求改变页面报表的数据结构。

## 5. 千川第三方接口契约与验证状态

所有开放平台 JSON 响应先检查 HTTP，再检查 `code===0`；通用包络 `code`（业务码）、`message`（说明）、`request_id`（排障）、`data`（业务对象）。下表列出项目消费的关键回参；动态可选字段以账户实际配置为准，不能凭 SDK 推断该应用有权限。`access_token` 经授权头或接口要求传递，不能输出到页面和日志。

| 方法/路径 | 关键传参 | 关键回参 `data` 与用途 | 前置条件/现状 |
| --- | --- | --- | --- |
| 浏览器 OAuth 授权入口 | `app_id`, `redirect_uri`, `state` 等；用户在千川确认 | 重定向 `auth_code,state` | 用户需有目标账户的可授权操作范围；本地 `state` 与本人会话绑定；该入口不是 JSON API |
| `POST /open_api/oauth2/access_token/` | `app_id,secret,auth_code` | `access_token,refresh_token,expires_in,refresh_token_expires_in`；令牌加密保存 | 真实成功；授权码一次性、短有效期；Token 不给浏览器 |
| `POST /open_api/oauth2/refresh_token/` | `app_id,secret,refresh_token` | 同上；替换旧令牌并更新凭证版本 | 真实成功；过期/撤权需本人重新授权 |
| `GET /open_api/oauth2/advertiser/get/` | `access_token` | `list[].account_id,account_name,account_type,is_valid,company_list[].customer_company_id`；识别授权对象 | 真实成功；不同授权角色走不同账户发现分支 |
| `GET /open_api/v1.0/qianchuan/shop/advertiser/list/` | `shop_id,page,page_size` | `adv_id_list[].adv_id,adv_name,page_info`；店铺映射广告账户 | 真实成功；仅店铺授权分支 |
| `GET /open_api/2/agent/advertiser/select/` | `advertiser_id,company_ids`（可重复）,`page,page_size` | `advertiser_ids[]/list[],page_info`；代理商可操作账户 | SDK 有契约、Mock 通过，缺真实代理商样本；不能认定该分支业务可用 |
| `GET /open_api/v3.0/customer_center/account/list/` | `account_id,filter={"account_type":"QIANCHUAN"},page,page_size` | `accounts[].account_id,account_name,account_type,page_info` | 真实 `code=0` 但 `accounts=[]`；该工作台未发现可用千川子账户 |
| `GET /open_api/v1.0/qianchuan/advertiser/type/get/` | `advertiser_ids` 为 JSON **整数数组** | `list[].advertiser_id,ecp_type`；校验为 `SHOP/SHOP_STAR/COMMON_STAR` | 真实成功；单值参数曾报 `40000` |
| `POST /open_api/2/file/video/ad/` | multipart `advertiser_id,upload_type=UPLOAD_BY_FILE,filename,video_signature,is_aigc,video_file` | `material_id,video_id`；保存素材标识；另有 `duration,width,height,...` 可选 | 真实成功；必须对本人绑定账户操作；仅表示素材入库 |
| `GET /open_api/v1.0/qianchuan/video/get/` | `advertiser_id,filtering` JSON（`material_ids`/`video_ids`/`signatures`）、分页 | `list[].material_id,id,signature,...,page_info`；查重及上传后核验 | 真实成功；账户权限、素材匹配均需校验 |
| `GET /open_api/v1.0/qianchuan/report/uni_promotion/config/get/` | `advertiser_id,data_topics` JSON 数组；可选 `data_period` | `custom_config_datas[].data_topic,dimensions[],metrics[],query_limit`；确认可筛维度、显示名、指标和时间限制 | 真实成功；当前先试 `OVERALL_ROI_PRODUCT_MATERIAL`，仅在明确无乘方能力时退到 `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO` |
| `GET /open_api/v1.0/qianchuan/report/uni_promotion/data/get/` | `advertiser_id,data_topic,dimensions,metrics,filters,start_time,end_time,order_by,page,page_size`；数组/对象按接口序列化 | `rows[].dimensions.material_id,rows[].metrics.{field},page_info`；按账户/素材/时间窗拉取汇总 | 真实 `code=0,rows=[]`；查询链路有效，但非空数据、指标单位及归因未实测；需账户开通相应主题且有匹配投放数据 |
| 候选 `GET /open_api/v1.0/qianchuan/report/video_user_lose/get/` | `advertiser_id,fields`（SDK 为 csv）、`start_date,end_date,filtering={material_id}` | `data.list[]` 候选包含 `h_sec` 秒位、`fields.click_cnt,dy_follow,dy_comment,dy_like,user_lose_cnt` | **未接入当前运行时代码**；旧应用实测 `HTTP 200/code=40002` 无账户/接口权限，新应用及非空回参未验；不可据 SDK 契约承诺可用 |

详细第三方输出字段、真实证据与状态见现有 `docs/09-QIANCHUAN-INTERFACE-AUDIT.md`；完整报表查询限制见 `docs/08-QIANCHUAN-REPORT-API.md`。旧 `/qianchuan/report/material/get/` 当前应用权限失败，运行时代码已不用；同样不能据此断言全平台下线。

### 报表实际组装条件与当前页面指标

配置接口需返回 `material_id` 等必要维度/筛选能力；数据接口按 `material_id` 精确过滤。乘方主题另筛 `roi2_material_type_v3=3`；全域主题多取 `roi2_material_video_name` 维度。项目以配置返回的指标字段与本地白名单取交集，故不同账户页面卡片可不同。响应行的 `dimensions.material_id` 必须与上传记录相同，不能只凭素材 ID 跨账户合并。

| 页面组 | 页面读取的指标字段 | 用途与注意 |
| --- | --- | --- |
| 投放表现 | `stat_cost_for_roi2`, `total_pay_order_gmv_include_coupon_for_roi2`, `total_pay_order_count_for_roi2`, `total_prepay_and_pay_settle_roi2_1h`, `product_show_count_for_roi2`, `product_click_count_for_roi2`, `product_cvr_rate_for_roi2`, `product_cpc_for_roi2` | 消耗、成交、ROI、商品展示/点击/转化及点击成本；中文名称取配置 `metrics[].name`；金额单位和归因口径待非空样本核对 |
| 视频播放质量 | `video_play_count_for_roi2_v2`, `video_play_duration_2s_rate_for_roi2`, `video_play_duration_3s_rate_for_roi2`, `video_play_duration_5s_rate_for_roi2`, `video_play_duration_10s_rate_for_roi2`, `video_play_finish_rate_for_roi2_v2` | 播放量、固定 2/3/5/10 秒播放率、完播率；**不能插值成逐秒曲线** |
| 互动表现 | `video_like_count_for_roi2`, `video_comment_count_for_roi2_v2` | 汇总点赞/评论，不是每秒数据 |

服务端还允许 `total_pay_order_gmv_for_roi2`,`total_cost_per_pay_order_for_roi2`，但当前页面分组没有展示。原始数值目前作为字符串透传，`null` 表示无回参/无匹配行，不主动乘 100、换算元/分或当作零。

## 6. 可用性验收与待决定问题

1. 两名同企业用户分别授权同一广告账户：各自 `/accounts`、`/uploads`、成片详情顶部、报表只见自己的连接和记录；直接请求对方 UUID 一律 404；企业 owner 也不得例外。未绑定者仍见绑定入口。
2. 本人停用、解绑、重新授权；旧会话/后台任务/报表缓存不得恢复已停用连接或继续调用旧 Token；解绑前后历史保留但不跨人可见。上传失败重试也按本人校验。
3. 用真实拥有投放记录、且本人已授权的账户检查报表配置及 `rows` 非空，逐字段校验展示名称、单位、时间窗、归因；`rows=[]` 展示“该时间窗暂无匹配报表”，不写“未投流”。
   共享广告账户另做反例测试：A 的素材经 B 的计划复投时，若平台报表无法按计划/操作者筛选，不得把整账户的素材汇总标成“A 的投放表现”。
4. 逐秒候选接口必须在**新应用+本人有效授权+确有投放数据的素材**下单独探测，记录 HTTP/业务码、`request_id`、权限配置、非空秒位和各字段口径。若仍 `40002`，产品保持“不可用”；不应造示例曲线。

**仍需产品确认：**同一人的同一广告账户允许几条连接？解绑后本人可否看已缓存的历史报表？离职后本人历史由谁依法查看/导出？“已投流”判断是“有报表行”还是“消耗大于 0”？是否允许多人授权同一广告账户并展示不可按操作者拆分的汇总数据？这些选择会影响去重、缓存和文案，但不应阻碍先做最小权限隔离。

## 官方契约与本地证据

- 巨量引擎官方 Java SDK：[千川全域报表配置 API](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/api/QianchuanReportUniPromotionConfigGetV10Api.java)、[报表数据 API](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/api/QianchuanReportUniPromotionDataGetV10Api.java)。SDK 提供请求契约，不代表本应用有权限或有非空数据。
- 官方 SDK：[视频逐秒互动流失 API](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/api/QianchuanReportVideoUserLoseGetV10Api.java)、[逐秒字段模型](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/model/QianchuanReportVideoUserLoseGetV10ResponseDataListInnerFields.java)、[筛选模型](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/model/QianchuanReportVideoUserLoseGetV10Filtering.java)。
- 项目实测与实现：`docs/08-QIANCHUAN-REPORT-API.md`、`docs/09-QIANCHUAN-INTERFACE-AUDIT.md`、`backend/app/services/qianchuan/`、`frontend/src/components/QianchuanReportPanel.tsx`。
