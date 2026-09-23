# 千川第三方接口只读实测记录（2026-09-22）

测试时间：2026-09-22 16:07–16:09（北京时间）。测试对象：当前项目配置的千川应用中唯一一条 `active` 连接、其唯一有效测试广告账户（ID 脱敏：`****6263`），授权对象类型 `PLATFORM_ROLE_SHOP_ACCOUNT`，账户类型实查 `SHOP_STAR`。使用连接现存、当时未到期的访问令牌；未刷新 Token，未上传素材，未创建/修改/删除投放，也未修改数据库。素材相关查询使用连接所有者自己的已核验上传记录。

测试脚本：`backend/scripts/qianchuan_probe_readonly.py`。脚本只输出接口路径对应的测试名、HTTP 状态、平台 `code`、`request_id`、列表长度/字段存在性；不输出令牌、密钥、完整广告账户 ID、素材 ID、原始报表行。判断“可用”须区分 `HTTP 200` 与平台 `code=0`；SDK 存在不等于该应用有权限。

| 实测第三方接口 | 请求的核心条件 | 本次实测 | 可得出的结论 |
| --- | --- | --- | --- |
| `GET /open_api/oauth2/advertiser/get/` | 当前 access_token | `HTTP 200/code=0`，授权对象 1 个 | 当前令牌可查询授权对象；不能据此推断全部接口权限 |
| `GET /open_api/v1.0/qianchuan/shop/advertiser/list/` | 授权店铺 ID、分页 | `200/0`，映射广告账户 1 个 | 当前店铺授权可发现测试广告账户 |
| `GET /open_api/v1.0/qianchuan/advertiser/type/get/` | `advertiser_ids` JSON 整数数组 | `200/0`，`ecp_type=SHOP_STAR` | 账户类型接口和本次参数有效 |
| `GET /open_api/v1.0/qianchuan/video/get/` | 测试账户、本人素材 `material_id`、分页 | 首次 `200/0` 命中 1 条；复测一次 `200/40100`；随即单独重试 `200/0` 再次命中 1 条 | 接口并非失效，但出现一次非零业务码；`40100` 的具体原因尚未验证，不能擅自归因于频控或权限 |
| `GET /open_api/v1.0/qianchuan/report/uni_promotion/config/get/` | `OVERALL_ROI_PRODUCT_MATERIAL` | `200/40000`，项目识别为当前账户暂无乘方投放能力 | 是此账户主题能力不足，不是配置接口整体下线 |
| 同上 | `SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO` | `200/0`；具备 `material_id` 维度，返回项目白名单中的 18 个指标 | 当前账户可取全域商品视频素材报表配置 |
| `GET /open_api/v1.0/qianchuan/report/uni_promotion/data/get/` | 全域主题、本人素材 ID、近 7 天 | `200/0`，`rows=[]` | 接口和查询参数可调用；无匹配行，不能验证指标实际值、单位和归因，也不能判定从未投流 |
| 同上 | 全域主题、本人素材 ID、近 30 天 | `200/0`，`rows=[]` | 扩大到页面支持的 30 天仍无行；不能把空结果解释为接口无效 |
| 候选 `GET /open_api/v1.0/qianchuan/report/video_user_lose/get/` | 当前账户、本人素材、近 7 天、5 个逐秒字段 | 两次均 `HTTP 200/code=40002` | **此轮测试的 `fields` 被发送成逗号分隔字符串，与官方 Java SDK 实际使用的 JSON 数组编码不一致；不能据此判定权限被拒或接口不可用。**该接口尚未接入项目运行时代码 |

本次逐秒请求的 `request_id`：`20260922160712D1C396F0AF8583A2DD9F`、`20260922160801053F12E53AB1272F185E`，可交千川开放平台核对具体拒绝原因。视频素材查询偶发 `40100` 的 `request_id`：`202609221607592AFA0C25FE5D1FB44DB4`。这些编号不是凭证。

未测接口及原因：`POST /open_api/oauth2/access_token/` 需新授权码；`POST /open_api/oauth2/refresh_token/` 会更换 Token；`POST /open_api/2/file/video/ad/` 会真实上传素材。本次按“只读有效性探测”范围不调用。代理商账户发现和工作台账户发现与该测试连接的店铺角色不匹配，不能拿店铺账号假装验证这两个分支；需要对应真实角色样本。旧素材报表接口已不在运行时代码，本次未重测。

**阶段结论：**当前测试账号的授权发现、账户映射、账户类型、素材查询及全域汇总报表查询链路实际可调用；但没有取得非空投放报表行。逐秒接口在参数编码未与官方 SDK 对齐的两次请求中得到 `40002`，**其权限及有效性尚未完成合格实测**。探测脚本现已改为 JSON 数组编码；当前访问令牌已过期，本次未擅自刷新授权，因此尚未重新请求。若目标是展示真正的投放指标，应提供在近 30 天有投放记录且可归属本人的素材；若目标是开放逐秒曲线，应先在有效授权下按官方编码复测，再视结果凭 `request_id` 向平台排查。切勿用固定 2/3/5/10 秒播放率伪造逐秒曲线。

相关官方契约：[全域报表配置](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/api/QianchuanReportUniPromotionConfigGetV10Api.java)、[全域报表数据](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/api/QianchuanReportUniPromotionDataGetV10Api.java)、[逐秒接口](https://raw.githubusercontent.com/oceanengine/ad_open_sdk_java/main/src/main/java/com/bytedance/ads/api/QianchuanReportVideoUserLoseGetV10Api.java)。接口契约本身不代表当前应用已获权限。
