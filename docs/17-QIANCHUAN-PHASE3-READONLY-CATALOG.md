# 千川全域推广 Phase 3：旁路只读目录

更新时间：2026-09-23

## 本阶段结果

- 新增独立 `QianchuanPlanClient`，组合现有底层 HTTP 客户端，但不复用上传服务、上传状态机或上传 worker。
- 新增本人 binding 下的授权抖音号接口：`GET /api/qianchuan/accounts/{binding_id}/awemes`。
- 新增本人 binding 下的可投商品接口：`GET /api/qianchuan/accounts/{binding_id}/products`。
- 新增独立分页 DTO，所有平台抖音号、商品和广告账号 ID 对外统一返回字符串，避免 JavaScript 大整数精度损失。
- 两个接口均校验租户、本人 connection、有效 binding、active connection、Token 剩余有效期和内容权限。
- 总开关关闭、Token 即将过期或资源属于他人时，不向平台发送请求。
- 平台缺少列表、分页字段，返回错误类型、重复抖音号或分页总数变化时 fail-closed，不向前端透传原始响应。
- 响应使用 `Cache-Control: no-store` 与 `Referrer-Policy: no-referrer`。

## 官方契约差异

授权抖音号接口只提供 `marketing_goal` 过滤，不提供名称关键词过滤。为避免只搜索当前页导致漏项，带关键词时执行：

1. 以每页 100 条读取全部授权抖音号；
2. 最多读取 100 页；
3. 校验页码、页大小、总页数、总条数稳定且 ID 不重复；
4. 完整读取后按名称或展示抖音号过滤；
5. 对过滤结果执行本地分页。

可投商品接口支持 `filter.product_name` 和 cursor，因此关键词过滤交给平台，服务只校验并返回安全字段。首版固定 `campaign_scene=VIDEO_PROM_GOODS`，不允许前端提交任意场景枚举。

## 当前安全边界

- `QIANCHUAN_PLAN_ENABLED=false`、`QIANCHUAN_PLAN_WRITE_ENABLED=false`、`QIANCHUAN_PLAN_WORKER_ENABLED=false` 仍为默认值。
- 本阶段只增加 GET 调用，不会创建、修改、启停或删除平台计划。
- 不会自动刷新即将过期的 Token；要求先走现有账户同步/重新授权流程，避免目录读取暗中改变 credential version。
- 不缓存目录结果，因此当前不存在跨 owner 缓存污染；短时缓存若后续加入，缓存键必须包含 owner、binding、advertiser 和 credential version。
- 生产 PostgreSQL 旁路迁移尚未执行。

## 验证结果

```text
Phase 3 目录专项：9 passed
全部千川回归：107 passed，179.175s
真实平台写请求：0
生产数据库 DDL：0
```

覆盖场景包括：开关关闭零平台调用、跨用户 binding 返回 404、即将过期 Token 零平台调用、抖音号跨页关键词检索、大整数 ID 字符串化、商品 filter/cursor 透传、畸形平台响应拒绝、旧能力门和旧账号接口保持兼容。

## 未完成项

本阶段不能宣称推广闭环完成。后续仍需按顺序实现：

1. 本地计划草稿与完整归属链校验；
2. mock 写链路和 operation 幂等/CAS；
3. 独立计划 worker；
4. 真实创建能力探测及 capability gate；
5. 创建后回查、启停、计划级报表和素材关联计划；
6. 前端计划工作台；
7. 备份后的生产显式迁移与小流量灰度。
