# 千川接口接入与有效性审计

审计日期：2026-09-21  
审计范围：项目运行时代码实际调用的巨量引擎/巨量千川开放平台接口，不包含项目自身的 `/api/qianchuan/*` 业务接口。

## 1. 结论

项目当前运行时代码共接入 **11 个**千川/巨量引擎开放平台 JSON API，另使用一个浏览器授权入口。11 个 API 当前仍能在巨量引擎官方 Java SDK `main` 分支的接口清单中找到，不能判定为已从官方 SDK 删除。

但是，“SDK 中仍有接口”只证明契约仍被发布，不等于当前应用、当前账户一定具备调用权限。本项目的实际验证程度如下：

- 8 个接口已有真实非空或真实业务成功证据：A01、A02、A03、A04、A06、A07、A08、A10。
- A09 的接口调用真实成功，但目标工作台返回空账户列表；只能证明接口和参数有效，不能证明该工作台确有可用千川账户。
- A11 的接口调用真实成功，但目前真实响应均为 `rows=[]`；只能证明查询链路有效，非空行、指标单位和归因口径仍未完成真实验证。
- A05 只有官方 SDK 契约与 Mock 测试，缺少真实千川代理商账户样本，不能宣称真实业务可用。

两个曾考虑使用的旧报表接口没有进入当前运行时代码：

- `/open_api/v1.0/qianchuan/report/material/get/`：当前应用联调时返回权限错误，已由 A10 + A11 替代。官方 SDK 仍列出该路径，所以不能表述为“全平台下线”，只能表述为“当前应用不可用且项目已停用”。
- `/open_api/v1.0/qianchuan/report/video_user_lose/get/`：当前应用无可选权限，也没有验证到公开等价替代；项目明确不提供逐秒流失曲线。官方 SDK 仍列出该路径，同样不能据此断言全平台下线。

## 2. 有效性分级

| 编号 | 方法与路径 | 项目用途 | 当前判断 | 最近证据 |
| --- | --- | --- | --- | --- |
| A01 | `POST /open_api/oauth2/access_token/` | 授权码换 Token | **真实有效** | 2026-09-14 完成真实回调、Token 交换和加密保存；官方 SDK 当前仍列出 |
| A02 | `GET /open_api/oauth2/advertiser/get/` | 获取授权对象/角色 | **真实有效** | 2026-09-18 日志 `HTTP 200 + code=0`；官方 SDK 当前仍列出 |
| A03 | `POST /open_api/oauth2/refresh_token/` | 刷新 Token | **真实有效** | 2026-09-18 日志 `HTTP 200 + code=0`；官方 SDK 当前仍列出 |
| A04 | `GET /open_api/v1.0/qianchuan/shop/advertiser/list/` | 店铺映射投放账户 | **真实有效** | 2026-09-18 日志 `HTTP 200 + code=0`，同步 1 个账户；官方 SDK 当前仍列出 |
| A05 | `GET /open_api/2/agent/advertiser/select/` | 代理商查询客户投放账户 | **契约存在，真实未验** | 官方 SDK 当前仍列出；项目 Mock 覆盖分页和 `company_ids`，无真实代理商样本 |
| A06 | `POST /open_api/2/file/video/ad/` | 上传 MP4 视频素材 | **真实有效** | 2026-09-14 真实上传成功并取得素材 ID、视频 ID；官方 SDK 当前仍列出 |
| A07 | `GET /open_api/v1.0/qianchuan/video/get/` | 上传前权限检查、上传后素材核验 | **真实有效** | 2026-09-14 用签名和素材 ID 实查成功；官方 SDK 当前仍列出 |
| A08 | `GET /open_api/v1.0/qianchuan/advertiser/type/get/` | 校验投放账户类型 | **真实有效，但参数严格** | 单值参数曾返回 `40000`；改为 JSON 整数数组后真实同步得到 `COMMON_STAR`；官方 SDK当前仍列出 |
| A09 | `GET /open_api/v3.0/customer_center/account/list/` | 工作台查询千川子账户 | **接口有效，业务结果为空** | 真实调用 `code=0`，但 `accounts=[]`；官方 SDK 当前仍列出 |
| A10 | `GET /open_api/v1.0/qianchuan/report/uni_promotion/config/get/` | 查询账户可用报表主题、维度、指标与限制 | **真实有效，能力随账户变化** | 2026-09-18：乘方主题返回明确能力错误，切换全域主题后 `code=0`；官方 SDK 当前仍列出 |
| A11 | `GET /open_api/v1.0/qianchuan/report/uni_promotion/data/get/` | 按素材查询汇总投放数据 | **接口有效，非空数据未验** | 2026-09-18 多次 `HTTP 200 + code=0 + rows=[]`；官方 SDK 当前仍列出 |

> 判断标准：必须同时看 HTTP 状态与 JSON `code`。`HTTP 200` 但 `code != 0` 仍是业务失败。`rows=[]` 或 `accounts=[]` 是成功空结果，不是接口失效。

## 3. 通用响应字段

除网络或非 JSON 异常外，客户端按统一包络处理：

| 字段 | 中文名 | 类型 | 用途 |
| --- | --- | --- | --- |
| `code` | 平台业务码 | integer | `0` 才表示业务成功；非零码进入错误分类 |
| `message` | 平台说明 | string | 辅助区分参数、权限、账户能力等错误；不直接原样返回前端 |
| `request_id` | 平台请求编号 | string | 联调、日志追踪和平台工单定位 |
| `data` | 业务数据 | object | 各接口的实际输出；项目要求必须为对象 |

以下各节只列出当前官方契约或项目真实/Mock 响应中可确认的字段，并标出项目实际读取项。未被当前项目消费的动态字段不应擅自赋予业务口径。

## 4. 各接口输出参数

### A01 获取授权凭证

`POST /open_api/oauth2/access_token/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `access_token` | 访问令牌 | string | 加密保存，用于调用账户、素材和报表接口 |
| `refresh_token` | 刷新令牌 | string | 加密保存，用于 A03 |
| `expires_in` | 访问令牌有效秒数 | integer | 计算访问令牌过期时间；到期前 5 分钟触发刷新 |
| `refresh_token_expires_in` | 刷新令牌有效秒数 | integer | 计算刷新令牌过期时间；过期后要求重新授权 |

项目要求上述四项全部存在、类型正确且有效期大于 0；缺失或类型错误会拒绝保存。

### A02 获取授权对象

`GET /open_api/oauth2/advertiser/get/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `list[]` | 授权对象列表 | object[] | 遍历用户授权给应用的对象 |
| `list[].account_id` | 授权对象 ID | integer/string | 作为店铺、代理商、工作台或直接广告账户的源 ID |
| `list[].account_name` | 授权对象名称 | string | 本地显示和账户快照 |
| `list[].account_type` | 授权对象类型 | string | 路由到不同解析分支；当前支持 `PLATFORM_ROLE_SHOP_ACCOUNT`、`PLATFORM_ROLE_QIANCHUAN_AGENT`、`CUSTOMER_ADMIN`、`CUSTOMER_OPERATOR`、`ADVERTISER` |
| `list[].is_valid` | 授权是否有效 | boolean | 只有严格等于 `true` 才继续绑定 |
| `list[].company_list[]` | 代理商获授权公司范围 | object[] | 仅代理商分支使用 |
| `list[].company_list[].customer_company_id` | 客户公司 ID | integer/string | 生成 A05 的重复 `company_ids` 查询参数 |

### A03 刷新授权凭证

`POST /open_api/oauth2/refresh_token/`

输出字段与 A01 相同。平台返回的新 Token 对会整体替换旧 Token，并增加本地 `credential_version`，避免并发刷新覆盖重新授权产生的新凭证。

### A04 查询店铺关联投放账户

`GET /open_api/v1.0/qianchuan/shop/advertiser/list/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `adv_id_list[]` | 店铺关联广告账户列表 | object[] | 当前真实/新契约优先读取的账户列表 |
| `adv_id_list[].adv_id` | 投放账户 ID | integer/string | 保存为 `advertiser_id` |
| `adv_id_list[].adv_name` | 投放账户名称 | string | 保存为 `advertiser_name` |
| `list[]` | 旧形态账户 ID 列表 | integer[] | 兼容项目早期契约；只有 `adv_id_list` 不存在时读取 |
| `page_info.page` | 当前页 | integer | 分页完整性校验 |
| `page_info.total_page` | 总页数 | integer | 决定是否继续读取；项目最多 100 页 |

### A05 代理商查询投放账户

`GET /open_api/2/agent/advertiser/select/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `advertiser_ids[]` | 可操作投放账户 ID 列表 | integer[] | 保存候选千川账户 |
| `list[]` | 兼容账户 ID 列表 | integer[] | 当 `advertiser_ids` 不是列表时兼容读取 |
| `page_info.page` | 当前页 | integer | 分页校验 |
| `page_info.total_page` | 总页数 | integer | 完整分页 |

风险：该接口目前没有真实代理商账户回参。`company_ids` 的重复查询参数编码来自官方 SDK 契约，仍需真实样本确认返回字段和账户权限。

### A06 上传视频素材

`POST /open_api/2/file/video/ad/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `material_id` | 素材 ID | integer | **项目实际读取并立即持久化**；用于 A07 核验和 A11 报表筛选 |
| `video_id` | 视频 ID | string | **项目实际读取并持久化**；也可用于 A07 查询 |
| `video_signature` | 视频签名 | string | 平台返回的素材签名；当前项目上传前自行计算 MD5，响应字段只记录在原始日志 |
| `video_cover_id` | 视频封面 ID | string | 官方契约字段；当前项目不使用 |
| `video_url` | 视频地址 | string | 官方契约字段；当前项目不使用 |
| `duration` | 视频时长 | number | 官方契约字段；当前项目不使用 |
| `width` / `height` | 视频宽/高 | integer | 官方契约字段；当前项目不使用 |
| `size` | 视频大小 | integer | 官方契约字段；当前项目不使用 |

`material_id` 与 `video_id` 至少要有一个有效值，否则任务转入待核验状态，不能把响应直接判为成功。

### A07 查询视频素材

`GET /open_api/v1.0/qianchuan/video/get/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `list[]` | 视频素材列表 | object[] | 按素材 ID、视频 ID 或 MD5 签名查找目标素材 |
| `list[].material_id` | 素材 ID | integer | **项目实际读取**；核对并覆盖持久化素材 ID |
| `list[].id` | 视频 ID | string | **项目实际读取**；注意查询接口字段叫 `id`，不是 A06 的 `video_id` |
| `list[].signature` | 视频签名 | string | **项目实际读取**；上传前查重/权限验证和上传后匹配 |
| `list[].filename` | 文件名 | string | 官方契约字段；当前项目不使用 |
| `list[].create_time` | 创建时间 | string | 官方契约字段；当前项目不使用 |
| `list[].duration` | 时长 | number | 官方契约字段；当前项目不使用 |
| `list[].width` / `height` | 宽/高 | integer | 官方契约字段；当前项目不使用 |
| `list[].size` / `bit_rate` | 大小/码率 | integer | 官方契约字段；当前项目不使用 |
| `list[].format` | 视频格式 | string | 官方契约字段；当前项目不使用 |
| `list[].image_mode` | 素材样式 | enum | 官方契约字段；当前项目不使用 |
| `list[].is_ai_create` | 是否 AI 生成 | boolean | 官方契约字段；当前项目不使用 |
| `list[].poster_url` / `url` | 封面地址/视频地址 | string | 官方契约字段；当前项目不使用 |
| `list[].source` | 素材来源 | enum | 官方契约字段；当前项目不使用 |
| `list[].tags[]` | 标签 | string[] | 官方契约字段；当前项目不使用 |
| `page_info.page` | 当前页 | integer | 分页校验 |
| `page_info.total_page` | 总页数 | integer | 完整分页 |

### A08 查询千川账户类型

`GET /open_api/v1.0/qianchuan/advertiser/type/get/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `list[]` | 账户类型列表 | object[] | 查找与请求账户完全匹配的一项 |
| `list[].advertiser_id` | 投放账户 ID | integer/string | 校验响应确属目标账户 |
| `list[].ecp_type` | 电商账户类型 | string | 仅接受 `SHOP`、`SHOP_STAR`、`COMMON_STAR`；其他值拒绝绑定 |

注意：请求参数 `advertiser_ids` 必须是 JSON **整数数组**。项目曾因发送单值而得到 `40000` 参数错误；这不是接口下线。

### A09 查询工作台下的千川账户

`GET /open_api/v3.0/customer_center/account/list/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `accounts[]` | 工作台账户列表 | object[] | 读取工作台可访问的账户 |
| `accounts[].account_id` | 账户 ID | integer/string | 保存为候选 `advertiser_id` |
| `accounts[].account_name` | 账户名称 | string | 本地显示 |
| `accounts[].account_type` | 账户类型 | string | 项目要求严格为 `QIANCHUAN`，否则拒绝更新绑定 |
| `page_info.page` | 当前页 | integer | 分页校验 |
| `page_info.page_size` | 每页数量 | integer | 返回分页元数据 |
| `page_info.total_number` | 总记录数 | integer | 判断工作台是否确有账户 |
| `page_info.total_page` | 总页数 | integer | 完整分页 |

真实回参为 `accounts=[]`、`total_number=0`、`total_page=0`。这表示当前授权工作台没有返回可访问的千川账户，不代表接口失效。

### A10 查询全域推广报表配置

`GET /open_api/v1.0/qianchuan/report/uni_promotion/config/get/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `custom_config_datas[]` | 主题配置列表 | object[] | 查找与请求 `data_topic` 完全匹配的主题 |
| `custom_config_datas[].data_topic` | 报表主题 | string | 决定维度、筛选和指标口径 |
| `custom_config_datas[].dimensions[]` | 可用维度配置 | object[] | 校验 `material_id`、素材名称、素材类型等字段是否可用 |
| `dimensions[].field` | 维度字段名 | string | 组装 A11 的 `dimensions` 和 `filters` |
| `dimensions[].name` | 维度中文名 | string | 展示/诊断 |
| `dimensions[].is_required` | 是否必选维度 | boolean | 判断查询必须携带的维度 |
| `dimensions[].filter_only` | 是否仅用于筛选 | boolean | 防止把筛选字段误当返回维度 |
| `dimensions[].filterable` | 是否可筛选 | boolean | 判断能否放入 `filters` |
| `dimensions[].filter_config` | 筛选配置 | object | 操作符、枚举、必填和数量限制 |
| `custom_config_datas[].metrics[]` | 可用指标配置 | object[] | 与项目指标白名单取交集 |
| `metrics[].field` | 指标字段名 | string | A11 请求和响应映射键 |
| `metrics[].name` | 指标显示名称 | string | 前端 `label` 的真实来源 |
| `custom_config_datas[].query_limit` | 查询限制 | object | 最大时间跨度、维度数量等；不同主题可能不同 |

项目先查 `OVERALL_ROI_PRODUCT_MATERIAL`；仅在平台明确返回“当前账户暂无乘方投放能力”时，切换到 `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO`。该 `40000` 是账户能力差异，不应统一标记为接口失效。

### A11 查询全域推广报表数据

`GET /open_api/v1.0/qianchuan/report/uni_promotion/data/get/`

| `data` 字段 | 中文名 | 类型 | 项目用途 |
| --- | --- | --- | --- |
| `page_info.page` | 当前页 | integer | 分页元数据 |
| `page_info.page_size` | 每页数量 | integer | 分页元数据 |
| `page_info.total_number` | 总记录数 | integer | 判断是否有数据 |
| `page_info.total_page` | 总页数 | integer | 项目按单素材聚合，要求不超过 1 页 |
| `rows[]` | 报表数据行 | object[] | 空数组表示当前账户、素材、主题和时间范围暂无匹配记录 |
| `rows[].dimensions` | 维度值映射 | object | 必须含匹配的 `material_id`；全域主题还可能含 `roi2_material_video_name` |
| `rows[].metrics` | 指标值映射 | object | 键为 A10 选出的指标字段，值保留为字符串或 `null` |

项目当前会从 A10 配置中选择以下指标；中文名称以 A10 当前账户实时返回为准：

| 字段 | 当前中文名/用途 |
| --- | --- |
| `stat_cost_for_roi2` | 整体消耗 |
| `product_show_count_for_roi2` | 整体展示次数 |
| `product_click_count_for_roi2` | 整体点击次数 |
| `product_cvr_rate_for_roi2` | 商品转化率；名称以实时配置为准 |
| `total_pay_order_count_for_roi2` | 整体成交订单数 |
| `total_pay_order_gmv_include_coupon_for_roi2` | 整体成交金额 |
| `total_pay_order_gmv_for_roi2` | 用户实际支付金额 |
| `total_cost_per_pay_order_for_roi2` | 成交订单成本；名称以实时配置为准 |
| `product_cpc_for_roi2` | 商品点击成本；名称以实时配置为准 |
| `total_prepay_and_pay_settle_roi2_1h` | 1 小时预付/结算 ROI；口径以实时配置为准 |
| `video_play_count_for_roi2_v2` | 视频播放数 |
| `video_play_finish_rate_for_roi2_v2` | 视频完播率 |
| `video_like_count_for_roi2` | 视频点赞数 |
| `video_comment_count_for_roi2_v2` | 视频评论数 |
| `video_play_duration_2s_rate_for_roi2` | 2 秒播放率 |
| `video_play_duration_3s_rate_for_roi2` | 3 秒播放率 |
| `video_play_duration_5s_rate_for_roi2` | 5 秒播放率 |
| `video_play_duration_10s_rate_for_roi2` | 10 秒播放率 |

当前真实 A11 响应只有成功空结果，尚不能确认金额单位、比率表示和归因口径。项目因此保留平台原始值，不自行除以 100 或乘以 100。

## 5. 浏览器授权入口

项目还会打开 `https://qianchuan.jinritemai.com/openapi/qc/audit/oauth.html` 完成用户授权。它是浏览器页面入口，不是上述 JSON API。项目在链接上追加/校验 `app_id`、`state`、`redirect_uri`、可选 `scope`，并保留平台配置的 `material_auth`、`rid` 等参数。真实授权页和回调已验证。

## 6. 代码与证据位置

- 运行时客户端：`backend/app/services/qianchuan/client.py`
- OAuth 与账户同步：`backend/app/services/qianchuan/service.py`
- 素材上传与核验：`backend/app/services/qianchuan/uploads.py`
- 报表适配：`backend/app/services/qianchuan/reports.py`
- 真实授权记录：`docs/06-QIANCHUAN-AUTH-VERIFICATION.md`
- 真实上传记录：`docs/07-QIANCHUAN-UPLOAD-VERIFICATION.md`
- 真实报表记录：`docs/08-QIANCHUAN-REPORT-API.md`
- 最近千川调用日志：`backend/logs/qianchuan.log`
- 固定官方 SDK 契约快照：`output/qianchuan-sdk-contract/`
- 官方 Java SDK 当前接口清单：<https://github.com/oceanengine/ad_open_sdk_java/blob/main/README.md>

## 7. 后续验证优先级

1. 用真实千川代理商账号验证 A05，确认 `company_ids` 编码、分页和实际返回字段。
2. 为 A09 选择确实绑定千川账户的工作台，取得非空 `accounts[]` 样本。
3. 对已经产生投放数据的素材调用 A11，取得非空 `rows[]`，再确认金额、比率、归因和数据刷新时延。
4. 定期对官方 SDK `main` 分支和真实只读调用做双重检查；不能只用 SDK 清单判断接口可用。
5. 不恢复旧逐秒流失接口，也不使用 2/3/5/10 秒播放率插值伪造逐秒曲线。
