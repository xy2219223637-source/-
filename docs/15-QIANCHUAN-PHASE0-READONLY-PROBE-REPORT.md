# 千川全域推广 Phase 0：只读能力探测报告

更新时间：2026-09-23

## 当前结论

Phase 0 的离线部分和账号尾号 `7801` 的首轮真实只读探测已完成。官方 GET 契约已固化到只读探测脚本，探测器具备显式连接与账号选择、输出脱敏、结构校验和统一能力结论。真实写链路继续保持禁止。

## 官方契约快照

以下结论来自巨量引擎官方 Java SDK 1.1.95；SDK 存在接口不代表当前应用已获权限，也不代表当前账号适用。

| 能力 | GET 路径 | 已确认的最小请求 | 关键响应结构 | 真实账号状态 |
| --- | --- | --- | --- | --- |
| 授权抖音号 | `/open_api/v1.0/qianchuan/aweme/authorized/get/` | `advertiser_id, page, page_size` | `aweme_id_list, page_info` | 调用成功，当前无样本 |
| 可投商品 | `/open_api/v1.0/qianchuan/product/available/get/` | `advertiser_id, page, page_size` | `product_list, page_info` | 调用成功，当前无样本 |
| 全域计划列表 | `/open_api/v1.0/qianchuan/uni_promotion/list/` | `advertiser_id, start_time, end_time, marketing_goal, fields` | `ad_list, page_info` | 参数契约确认；当前无样本 |
| 全域计划详情 | `/open_api/v1.0/qianchuan/uni_promotion/ad/detail/` | `advertiser_id, ad_id` | 只记录顶层键，不落原始正文 | 待有样本计划后探测 |
| 全域计划素材 | `/open_api/v1.0/qianchuan/uni_promotion/ad/material/get/` | `advertiser_id, ad_id, filtering` | 只记录列表数量和分页键 | 待有样本计划后探测 |
| 素材关联计划 | `/open_api/v1.0/qianchuan/material/ad/get/` | `advertiser_id, material_id, material_type, marketing_scene, marketing_goal, start_time, end_time, fields` | `ad_list, page_info` | FEED/SEARCH/SHOPPING_MALL 均成功，当前无样本 |

已确认计划列表的 `marketing_goal` 只有 `VIDEO_PROM_GOODS`、`LIVE_PROM_GOODS`；素材关联计划的 `marketing_scene` 为 `FEED`、`SEARCH`、`SHOPPING_MALL`。探测器会逐项测试，不把某一场景的空数据扩大解释为接口不可用。

## 已落地代码

- `backend/scripts/qianchuan_probe_readonly.py`
  - 新增 `--connection-id`、`--binding-id`，多连接或多账号时拒绝猜测目标。
  - 新增 `--plan-only`，仅调用全域旁路的只读 GET 接口。
  - 本人素材查询增加 `binding_id` 限定，旧授权素材不会被新授权误选。
  - 输出仅包含路径、请求字段名、HTTP 状态、业务 code、request_id、响应键、行数、分页键和能力结论。
  - 不输出 Token、完整广告账号 ID、素材 ID、计划 ID、原始行或请求参数值。
- `backend/app/config.py`
  - `QIANCHUAN_PLAN_ENABLED=false`
  - `QIANCHUAN_PLAN_WRITE_ENABLED=false`
  - `QIANCHUAN_PLAN_WORKER_ENABLED=false`
- `backend/app/services/qianchuan/client.py`
  - 只增加新 GET 路径的中文诊断名称，不改变现有请求或错误处理。

## 能力结论规则

| 结论 | 判定 |
| --- | --- |
| `verified` | HTTP 与业务调用成功，响应结构符合契约，且样本列表非空 |
| `permission_missing` | 平台明确返回当前授权无权操作目标账号 |
| `account_not_applicable` | 已有明确账户类型证据证明接口不适用 |
| `no_sample_data` | 调用成功且结构正确，但当前时间窗或目标没有样本 |
| `contract_uncertain` | 参数、响应结构、网络结果或平台拒绝原因仍无法唯一解释 |

`no_sample_data` 不等于 `verified`。普通 `provider_rejected` 也不会武断归类为缺权限，因为它仍可能是请求参数或账号适用性问题。

## 离线验证结果

```text
新增探测测试：5 passed
全部千川后端回归：95 passed，145.438s
Python 静态编译：passed
真实平台请求：已执行，只读 GET
真实平台写请求：0
```

测试覆盖：只发 GET、默认三开关关闭、敏感值不出现在输出、空列表不被判为 `verified`、无效 UUID 在发请求前失败。

## 真实探测门

真实只读探测必须由用户明确指定本地 `connection_id` 与 `binding_id` 后执行：

```powershell
cd D:\project_work\video_cut\backend
.\.venv\Scripts\python.exe scripts\qianchuan_probe_readonly.py `
  --plan-only `
  --connection-id <本地连接 UUID> `
  --binding-id <本地账号绑定 UUID>
```

执行完成后，将脱敏 JSON Lines 保存为 Phase 0 证据，再决定哪些能力可以进入运行时代码。未取得真实 `verified` 前，不创建计划表写服务、不接真实写接口、不启动计划 worker。
