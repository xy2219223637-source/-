# 千川素材数据报表接口接入文档

更新日期：2026-09-15。面向本项目后端开发及接口联调。

本文记录官方报表接口的替代方案、项目接入方式和当前账户的真实验证结果。接口可用范围、字段及限制以目标账户实时返回的配置为准。

## 1. 接入范围与替代关系

| 原能力 | 接入方案 | 限制 |
| --- | --- | --- |
| `/qianchuan/report/material/get/` 素材汇总报表 | 使用 `uni_promotion/config/get/` 获取配置，再使用 `uni_promotion/data/get/` 按素材查询 | 新报表有整体消耗、整体成交等口径，不应直接沿用旧指标解释 |
| `/qianchuan/report/video_user_lose/get/` 视频逐秒互动与流失 | 暂无已验证的公开等价替代接口 | 本次检查的四个新报表主题未找到 `h_sec` 或逐秒流失字段 |

新接口可以提供播放数、完播率和 2/3/5/10 秒播放率，但不能据此重建每一秒的流失、点击或互动曲线。自然时间的小时维度也不是视频播放秒数。

旧接口在此前联调中返回 `40002` 权限错误，平台权限页面对应分组不可选择。目前没有确认到官方全面下线公告，不能仅凭上述现象认定所有应用均已无法调用。

## 2. 通用约定

| 项目 | 说明 |
| --- | --- |
| 服务地址 | `https://api.oceanengine.com` |
| 请求方法 | `GET` |
| 鉴权 | HTTP Header：`Access-Token: <已授权账户对应的访问令牌>` |
| 参数位置 | URL Query；本页 JSON 请求示例是编码前的参数对象，不是 GET 请求体 |
| 复合参数编码 | 数组、对象先 JSON 序列化，再交给 HTTP 客户端做 URL 编码；本次实测使用此方式 |
| 成功判断 | 同时检查 HTTP 状态和响应 JSON 的 `code`；HTTP 200 不等于业务成功 |
| 长 ID | 前端、JSON 示例及业务存储使用十进制字符串，避免 JavaScript 数字精度损失；后端按官方参数契约序列化 |
| 时间 | 格式 `YYYY-MM-DD HH:mm:ss`；本次验证使用北京时间业务范围，不附加 `Z`。跨时区部署需显式转换，不直接使用服务器本地时区 |

访问令牌必须与所查询账户的有效授权对应。后端复用现有授权连接、账户归属校验及令牌刷新机制；不能因一个账户验证成功，就假设其他账户也有权限。令牌不放入前端、URL、文档或日志。

### 项目业务接口

前端不直接调用开放平台，也不接收任意账户 ID。成片页面通过已核验成功的上传记录查询：

```text
GET /api/qianchuan/uploads/{upload_id}/report?days=7
```

`days` 允许 1 至 180，页面目前提供近 7 天和近 30 天。后端从上传记录取得租户、投放账户和素材 ID，检查账户连接与令牌状态，然后依次调用配置接口和数据接口。响应将平台指标归一化为 `{label, value}`；`value` 保留平台原始文本，空数据为 `null`。

该接口只允许读取当前租户的上传记录；素材未成功核验返回 409，不存在或跨租户返回 404，平台查询失败返回脱敏后的 502。响应禁止缓存。

2026-09-15 补充：不同投放账户的报表能力不同。服务先查询 `OVERALL_ROI_PRODUCT_MATERIAL` 配置；仅当配置接口明确返回 `40000` 且消息为“当前账户暂无乘方投放能力，无法获取对应数据”时，改查 `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO`。全域主题使用 `material_id`、`roi2_material_video_name` 两个维度，只保留素材 ID 筛选。其他参数或权限错误不会触发主题切换。切换主题及调用数据接口前重新校验授权版本和账户状态。

同一成片上传到多个账户时，页面提供账户选择并显示当前账户名称、ID 和素材 ID；查询不同账户时清空旧报表，防止混用结果。接口返回实际使用的 `data_topic`，不同主题的指标以各自配置为准。

## 3. 获取报表配置

```text
GET /open_api/v1.0/qianchuan/report/uni_promotion/config/get/
```

### 请求参数

| 参数 | 逻辑类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `advertiser_id` | 投放账户 ID | 是 | 当前授权可访问的千川账户 |
| `data_topics` | string[] | 是 | 要查询的报表主题；按 JSON 数组编码 |
| `data_period` | string | 否 | `OVER_ALL_DATA`：乘方期间；`UNI_DATA`：全域期间；`ALL_DATA`：整体数据，默认值 |

配置查询参数示例（这两个主题均已在本次配置响应中返回）：

```json
{
  "advertiser_id": "1876307045577801",
  "data_topics": [
    "OVERALL_ROI_PRODUCT_MATERIAL",
    "SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO"
  ]
}
```

### 响应中需要读取的配置

主题配置位于 `data.custom_config_datas[]`。

| 字段 | 用途 |
| --- | --- |
| `data_topic` | 对应主题 |
| `dimensions[]` | 可用维度，包括 `field`、`name`、`is_required`、`filter_only`、`filterable` 等 |
| `dimensions[].filter_config` | 筛选操作符、枚举及数量限制；`filter_required` 为 true 时必须提供对应筛选 |
| `metrics[]` | 可用指标及其 `field`、`name` 等元数据 |
| `query_limit` | 查询跨度、维度数量等限制 |

`is_required` 表示查询需要的维度；`filter_required` 表示必须提供的筛选条件，两者不能混为一谈。`filter_only` 为 true 的字段只用于筛选，不作为返回维度。

### 已验证主题

| 主题 | 本次配置要求 | 验证程度 |
| --- | --- | --- |
| `OVERALL_ROI_PRODUCT_MATERIAL` | 视频筛选要求 `roi2_material_type_v3` 包含 `"3"`；支持 `material_id` 维度及筛选 | 配置和数据查询均成功；推荐作为商品视频素材接入起点 |
| `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO` | 必须包含 `material_id`、`roi2_material_video_name` 两个维度 | 配置和数据查询均成功 |
| `OVERALL_ROI_LIVE_MATERIAL_VIDEO` | 配置支持素材维度，并要求素材类型筛选 | 仅配置调用成功；直播素材查询尚未实测 |
| `SITE_PROMOTION_POST_DATA_VIDEO` | 除必要维度外，配置要求 `anchor_id`、`aggregate_smart_bid_type`、`ecp_app_id` 等筛选 | 仅配置调用成功；不能直接套用商品素材请求 |

上述主题不是任意互换的别名。应根据素材实际投放场景选主题，再读取该主题配置，不为凑出记录而跨场景盲查。

## 4. 获取报表数据

```text
GET /open_api/v1.0/qianchuan/report/uni_promotion/data/get/
```

### 请求参数

| 参数 | 逻辑类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `advertiser_id` | 投放账户 ID | 是 | 与配置查询及素材所属账户保持一致 |
| `data_topic` | string | 是 | 单个报表主题 |
| `dimensions` | string[] | 是 | 分组/返回维度，须满足主题的必填要求 |
| `metrics` | string[] | 是 | 从该主题配置中选择指标 |
| `filters` | object[] | 是 | 筛选条件，结构见下表；须满足主题的必填筛选 |
| `start_time` | string | 是 | 查询开始时间 |
| `end_time` | string | 是 | 查询结束时间 |
| `order_by` | object[] | 是 | 本次成功请求使用 `[]`；非空对象字段为 `field`、`type`，排序枚举未实测，不在此猜测 |
| `page` | integer | 否 | 页码，默认 1 |
| `page_size` | integer | 否 | 本次使用 10；不将此值解释为平台最大页大小 |
| `data_period` | string | 否 | 枚举同配置接口；SDK 注释限定仅 `adlab_scene=OVERALL_PROJECT` 时支持。本页请求未传入，不作为所有主题的通用筛选 |

筛选对象：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `field` | string | 可筛选字段 |
| `operator` | integer | 使用配置返回的操作符；本次两个字段均为 `7` |
| `values` | string[] | 筛选值；素材类型视频使用 `"3"`，素材 ID 也使用字符串 |

### 已实测的参数对象

以下 ID 与时间来自本次授权联调，复用时必须替换为目标账户、对应素材和业务查询范围。

```json
{
  "advertiser_id": "1876307045577801",
  "data_topic": "OVERALL_ROI_PRODUCT_MATERIAL",
  "dimensions": ["material_id"],
  "metrics": [
    "stat_cost_for_roi2",
    "total_pay_order_gmv_include_coupon_for_roi2",
    "video_play_count_for_roi2_v2"
  ],
  "filters": [
    {"field": "material_id", "operator": 7, "values": ["7685359826476924966"]},
    {"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]}
  ],
  "start_time": "2026-09-08 00:00:00",
  "end_time": "2026-09-14 21:09:22",
  "order_by": [],
  "page": 1,
  "page_size": 10
}
```

若改查 `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO`，本次成功组合为：维度使用 `["material_id", "roi2_material_video_name"]`，筛选仅保留 `material_id`，其余指标相同。

### 实际响应

```json
{
  "code": 0,
  "message": "OK",
  "request_id": "20260914210922AE4495750FD0FFDC6F4E",
  "data": {
    "page_info": {"page": 1, "page_size": 10, "total_number": 0, "total_page": 0},
    "rows": []
  }
}
```

`rows: []` 表示该账户、主题、筛选和时间范围没有返回记录；不能解释为所有指标为 0，也不能证明素材不存在。素材上传成功不会自动产生投放报表记录。

当前尚未取得非空响应，因此不提供虚构的数据行样例。非空行的嵌套结构、指标值类型、金额单位及百分比表示方式，需结合官方字段说明和后续真实响应确认后再编写解析映射。

分页使用返回的 `page_info` 判断是否继续；本次只验证了第一页空结果，多页读取仍需验证。

## 5. 可用指标与展示口径

下表来自 `OVERALL_ROI_PRODUCT_MATERIAL` 的真实配置响应。“配置可用”不等于所有指标组合均已实测；数据查询仅验证了上述三个指标的组合。

| 字段 | 平台显示名称 |
| --- | --- |
| `stat_cost_for_roi2` | 整体消耗 |
| `product_show_count_for_roi2` | 整体展示次数 |
| `product_click_count_for_roi2` | 整体点击次数 |
| `total_pay_order_count_for_roi2` | 整体成交订单数 |
| `total_pay_order_gmv_include_coupon_for_roi2` | 整体成交金额 |
| `total_pay_order_gmv_for_roi2` | 用户实际支付金额 |
| `video_play_count_for_roi2_v2` | 视频播放数 |
| `video_play_finish_rate_for_roi2_v2` | 视频完播率 |
| `video_like_count_for_roi2` | 视频点赞数 |
| `video_comment_count_for_roi2_v2` | 视频评论数 |
| `video_play_duration_2s_rate_for_roi2` | 2秒播放率 |
| `video_play_duration_3s_rate_for_roi2` | 3秒播放率 |
| `video_play_duration_5s_rate_for_roi2` | 5秒播放率 |
| `video_play_duration_10s_rate_for_roi2` | 10秒播放率 |

实现时保留平台字段名、主题、时间范围及原始值。金额用精确十进制处理；未确认单位前不除以 100。未确认百分比表示前不自动乘以 100。整体成交金额与用户实际支付金额不能合并为同一个无口径说明的“成交额”。

本次该主题返回的限制原值如下，单位含义按键名及后续官方说明核对，不推导为固定数据保留期：

```json
{
  "earliest_day": "",
  "max_day": "180",
  "max_day_by_day": "90",
  "max_day_by_hour": "7",
  "max_dimension_limit": "10"
}
```

`material_id`、`roi2_material_type_v3` 的筛选配置均返回 `value_limit: 100`。这些限制是当前主题的配置快照，不硬编码为所有主题的统一上限。本次未验证数据刷新时延、QPS 或日期边界的包含规则，不沿用旧逐秒接口的 T+1 规则。

## 6. Python 最小请求示例

依赖 `httpx`。代码只执行读取请求，不上传素材或创建投放计划。示例通过环境变量接收已有有效令牌；正式集成应改用项目授权服务在内存中提供令牌，不新增持久化明文令牌配置。

```python
import json
import os

import httpx

BASE_URL = "https://api.oceanengine.com"
TOPIC = "OVERALL_ROI_PRODUCT_MATERIAL"


def get_report(client, path, params):
    query = {
        key: json.dumps(value, separators=(",", ":"))
        if isinstance(value, (list, dict)) else value
        for key, value in params.items()
    }
    response = client.get(path, params=query)
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0:
        # 不打印令牌、请求头或未经脱敏的平台消息。
        raise RuntimeError(
            f"report failed: code={body.get('code')}, "
            f"request_id={body.get('request_id')}"
        )
    return body["data"]


def main():
    advertiser_id = os.environ["QC_REPORT_ADVERTISER_ID"]
    material_id = os.environ["QC_REPORT_MATERIAL_ID"]
    with httpx.Client(
        base_url=BASE_URL,
        headers={"Access-Token": os.environ["QC_REPORT_ACCESS_TOKEN"]},
        timeout=httpx.Timeout(30.0, connect=5.0),
        follow_redirects=False,
    ) as client:
        config = get_report(
            client,
            "/open_api/v1.0/qianchuan/report/uni_promotion/config/get/",
            {"advertiser_id": advertiser_id, "data_topics": [TOPIC]},
        )
        topic = next(
            item for item in config["custom_config_datas"]
            if item["data_topic"] == TOPIC
        )
        metrics = [
            "stat_cost_for_roi2",
            "total_pay_order_gmv_include_coupon_for_roi2",
            "video_play_count_for_roi2_v2",
        ]
        available = {item["field"] for item in topic["metrics"]}
        if not set(metrics).issubset(available):
            raise RuntimeError("当前主题配置已变化，请重新核对指标")

        data = get_report(
            client,
            "/open_api/v1.0/qianchuan/report/uni_promotion/data/get/",
            {
                "advertiser_id": advertiser_id,
                "data_topic": TOPIC,
                "dimensions": ["material_id"],
                "metrics": metrics,
                "filters": [
                    {"field": "material_id", "operator": 7, "values": [material_id]},
                    {"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]},
                ],
                "start_time": os.environ["QC_REPORT_START_TIME"],
                "end_time": os.environ["QC_REPORT_END_TIME"],
                "order_by": [],
                "page": 1,
                "page_size": 10,
            },
        )
        print(json.dumps({
            "page_info": data.get("page_info"),
            "row_count": len(data.get("rows", [])),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

所需环境变量为 `QC_REPORT_ACCESS_TOKEN`、`QC_REPORT_ADVERTISER_ID`、`QC_REPORT_MATERIAL_ID`、`QC_REPORT_START_TIME`、`QC_REPORT_END_TIME`。时间变量按第 2 节格式填写。示例仅查询一页，演示已验证的字段组合；它不是包含配置全量校验、分页、缓存和重试的生产客户端。

## 7. 异常与空数据处理

| 情况 | 处理方式 |
| --- | --- |
| HTTP 成功且 `code=0`，有行 | 按主题及字段契约解析；保留查询范围和口径 |
| HTTP 成功且 `code=0`，空行 | 显示“当前查询范围暂无投放数据”，保留素材上传成功状态 |
| `40002` 权限错误 | 检查具体接口、应用能力和账户授权；记录脱敏错误及 `request_id`，不循环重试或直接认定接口下线 |
| 非零业务码，指向参数错误 | 核对当前主题配置、必填维度/筛选、JSON 编码及查询跨度 |
| 明确的令牌失效错误 | 走现有刷新/重新授权流程；不要把所有权限错误都当成令牌过期 |
| 超时、网络错误或服务端暂时失败 | 只读请求可有限重试；持续失败应显示查询失败，不能伪装成空数据 |
| 逐秒数据不可用 | 显示能力暂不可用；不能用示例曲线或 2/3/5/10 秒指标插值冒充真实逐秒数据 |

空结果排查顺序：账户和素材 ID 是否匹配 → 是否确有对应投放 → 报表主题是否匹配场景 → 时间和筛选是否正确 → 数据是否已产出。不要通过创建付费投放来自动“修复”空结果。

## 8. 验证记录与后续接入

2026-09-14 21:09（北京时间），使用现有有效授权、成片 52 已上传素材进行了只读查询：

| 项目 | 结果 |
| --- | --- |
| 投放账户 | `1876307045577801` |
| 素材 ID | `7685359826476924966` |
| 查询开始 | `2026-09-08 00:00:00` |
| `OVERALL_ROI_PRODUCT_MATERIAL` | HTTP 200，`code=0`，`rows=[]` |
| `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO` | HTTP 200，`code=0`，`rows=[]` |
| 查询权限 | 上述两个主题在本次授权下均可调用 |
| 尚未验证 | 非空行解析、指标单位/归因解释、多页读取、直播数据查询、数据刷新时延 |

当前已完成：复用账户授权与上传记录中的素材 ID → 实时读取主题配置 → 查询素材汇总 → 用真实状态替换页面示例数据。取得首个真实非空响应后，还需确认金额、比率及归因口径，再增加格式化展示；逐秒功能保持明确的不可用状态。

本项目业务层继续执行租户、账户归属及连接状态校验，前端不能任意传入其他租户的 `advertiser_id`。本次没有新增数据库表或后台定时任务；数据按页面请求实时读取，不持久化报表响应。

## 9. 来源与证据

### 2026-09-15 持续 40000 的定位和复验

前端默认选择最新成功上传记录 `74752703-80ce-4fd9-ae7b-c955ac5701b5`，对应 `chzi`（`1874638901296263`）；此前人工联调使用的是较早的 `28fd6e24-5049-4100-b1d9-54193965868a`，对应良昱好物甄选铺（`1876307045577801`）。两者虽然素材 ID 相同，账户报表能力不同。前者在乘方配置接口稳定返回“当前账户暂无乘方投放能力，无法获取对应数据”，不能归因于偶发错误或未刷新页面。

修复后，通过实际前端代理地址 `http://127.0.0.1:5173/api/qianchuan/uploads/{upload_id}/report` 复验：

| 账户 | 实际主题 | 近 7 天 / 近 30 天 |
| --- | --- | --- |
| chzi | `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO` | 均 HTTP 200、`no_data`，18 个配置指标 |
| 良昱好物甄选铺 | `OVERALL_ROI_PRODUCT_MATERIAL` | 均 HTTP 200、`no_data`，17 个配置指标 |

`no_data` 仅说明所选账户、素材、主题和时间范围无匹配记录，不能据此断言账户没有投放。18 项上传/报表专项测试及 TypeScript、Vite 构建通过。新增回归覆盖明确能力错误的主题切换、其他错误不切换、切换期间授权停用即终止。

### 官方契约和早期联调快照

- [巨量引擎官方 Go SDK 接口列表](https://github.com/oceanengine/ad_open_sdk_go)：包含配置、数据及旧逐秒接口路径；SDK 中存在路径不等于该接口对所有应用开放。
- [官方 Java SDK：报表配置接口](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/api/QianchuanReportUniPromotionConfigGetV10Api.java)。
- [官方 Java SDK：报表数据接口](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/api/QianchuanReportUniPromotionDataGetV10Api.java)。
- [本地配置响应快照](../output/qianchuan-reports/replacement-config-20260914.json)：字段、筛选及查询限制的实测来源。
- [本地数据查询记录](../output/qianchuan-reports/replacement-data-20260914.json)：两个主题的请求参数和成功空响应。
- [素材上传验证记录](07-QIANCHUAN-UPLOAD-VERIFICATION.md)：素材 ID 与成片的来源关系。

`output/` 下的证据属于本地联调产物，可能不随仓库分发；本文已保留必要参数和真实响应，交接时可另行提供脱敏快照。

### 报表缓存与刷新

- 报表数据与已验证的主题配置均缓存 3600 秒，从成功查询完成起计时；命中缓存不会续期。成功的空数据也缓存，失败结果不缓存。
- 数据按租户、授权绑定、账户、素材、凭证版本、查询天数和北京时间日期隔离；近 7 天与近 30 天分别缓存，跨日后重新查询以更新日期范围。配置按租户、授权绑定、账户及凭证版本复用，缓存包含已确认适用的主题。
- 相同查询的并发请求共用一次平台调用。读取缓存前和等待完成后均重新检查授权；重新授权/凭证版本变化后不复用旧缓存。
- 打开页面、切换账户/范围和手动刷新均遵守缓存，不提供绕过缓存的刷新。没有后台定时刷新，到期后在下次访问时重新查询。
- 响应新增 `queried_at`（带时区的上次成功查询时间）、`cache_expires_at`、`cache_status`（`miss` 本次查询、`hit` 缓存、`joined` 合并请求）。这不是平台的数据统计更新时间。
- 前端显示上述时间及来源，并合并正在进行的相同请求；刷新上传记录不再先清空列表，避免报表组件重建后重查。
- 使用单后端进程内存缓存，最多保存 512 项（含配置），容量不足时淘汰最近最少使用项。重启清空；多实例间不共享。没有增加数据库表或队列服务。

### 查看千川日志

重启后端后，千川业务日志以 INFO 级别同时输出到控制台（标准错误流）和 `backend/logs/qianchuan.log`（UTF-8）。目录相对后端代码位置固定，不受启动目录影响；首次写日志时创建文件。正常后端入口和 `qianchuan_auth_live.py` 均会启用。

保留规则：单文件达到约 10 MiB 时轮转，保留 5 个历史文件（`qianchuan.log.1` 最新，`.5` 最旧）和 1 个当前文件，总量约 60 MiB。继续轮转时自动删除最旧备份；按大小而非天数清理。`backend/logs/` 整个目录被 Git 忽略，包括轮转文件。

Docker 内路径为 `/app/logs/qianchuan.log`。目前没有挂载日志卷，容器重建后文件不保证保留；控制台日志仍可通过 Docker 查看。此文件轮转配置适用于当前单后端进程，多进程部署时应改用集中日志收集。

Docker 部署在项目目录执行：

```bash
docker compose logs -f --tail=100 backend
```

- `api_request`：每次调用千川平台结束时记录 method、path、outcome、http_status、provider_code、error、request_id、duration_ms。失败请求为 WARNING；未收到 HTTP 响应时状态为 `-`。
- 授权、账户同步和上传状态变化沿用业务事件日志，可按 session、connection 或 upload ID 关联。
- `report_fallback`：因账户能力限制切换报表主题；`report_finished`：查询主题、天数、行数及 `available`/`no_data`；`report_failed`：报表失败分类及平台请求 ID。
- 默认不打印请求参数、请求头、请求体、视频内容、平台完整响应或原始异常文本。平台 request_id 经过格式及已知凭证检查后才会记录。

需要查看素材接口的第三方返回值时，在 `backend/.env` 设置 `QIANCHUAN_LOG_MATERIAL_RESPONSE=true` 并重启后端，之后重新触发查询。搜索 `material_response` 可查看平台 JSON 中的 `code`、`message`、`request_id`、`data`（包含素材列表、指标及分页信息）。只对素材上传、视频素材查询、报表配置及报表数据四个接口启用，授权/Token 接口始终不记录响应内容。

响应预览会遮盖敏感字段、已知凭证及 URL 查询参数；超过长度、列表数量或深度限制时标记 `truncated`。每条响应预览最多 16000 字符，不是无损原始报文。非 JSON 响应仅记录省略原因。该开关默认关闭，结束排查后可改回 `false`；文件沿用上述轮转规则。

这里的 `api_request` 是后端调用千川平台的日志。联调入口仍关闭 Uvicorn HTTP 访问日志，授权回调 URL 仍受日志过滤保护。
