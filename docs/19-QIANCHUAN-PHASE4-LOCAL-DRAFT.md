# 千川 Phase 4A：本地计划草稿

日期：2026-09-23

## Goal

在不调用千川计划写接口、不启动计划 worker 的前提下，建立一个可验证、可更新且严格归属于当前用户的本地计划草稿入口，为后续 mock 提交状态机提供安全起点。

## Context

- 只读目录链路已经能够读取当前广告账号授权的抖音号和可投商品。
- 成片素材上传记录已经保存 `material_id`，但“素材上传成功”不等于“计划已创建”。
- 计划旁路表必须显式迁移，不能由应用启动过程隐式建表。
- 当前环境保持 `QIANCHUAN_PLAN_WRITE_ENABLED=false` 和 `QIANCHUAN_PLAN_WORKER_ENABLED=false`。

## Scope

本阶段包含：

- 创建本地草稿；
- 通过 `draft_id` 显式更新当前用户已有草稿；
- 校验用户、租户、connection、binding、upload、video asset、`material_id` 和凭证版本；
- 通过平台 GET 接口精确回查抖音号与商品；
- 只保存已验证的名称快照和不含 Token 的请求快照；
- 平台只读调用结束后，再次读取并校验可变归属链。

本阶段不包含：

- 千川计划 create、enable、pause 等平台写请求；
- operation 入队、重试、回查和独立 worker；
- 前端计划创建表单；
- 生产数据库迁移；
- 对预算单位和 ROI 平台规则作未经官方接口验证的承诺。

## Interfaces

### API

`POST /api/qianchuan/plans/drafts`

- 不传 `draft_id`：创建新草稿；
- 传 `draft_id`：只更新当前用户、当前租户且仍为 `draft`、尚无 `platform_ad_id` 的草稿；
- 返回 `QianchuanDeliveryPlan`；
- 响应头包含 `Cache-Control: no-store` 和 `Referrer-Policy: no-referrer`。

### Platform reads

一次有效请求最多进行两类只读调用：

1. 精确选择授权抖音号，并验证短视频带货权限；
2. 通过 `filter.product_ids` 和 `aweme_id` 精确选择可投商品。

两类查询都有分页上限、重复 ID 检测和游标/页码前进检测。没有任何平台 POST 写调用。

## Constraints

- 总开关和计划开关关闭时返回 404；写开关关闭时返回 409。
- 写开关开启但旁路表未显式迁移时返回 503，且不得先访问平台。
- 非本人数据按 404 隐藏；失败素材、失效授权和不完整归属链在平台调用前拒绝。
- 平台验证期间 connection、credential version、binding、upload 或 `material_id` 发生变化时返回 409，不落草稿。
- 不允许通过更新接口覆盖已提交计划。
- 草稿快照不得保存 access token、refresh token 或密文。
- `budget_minor` 的 `1..100000000` 与 `roi_goal` 的 `(0,1000]` 只是本地防滥用边界；平台单位和最终可接受范围必须在提交接口接入前再核实。
- 真实写开关保持关闭，生产迁移必须在备份和回滚方案就绪后单独执行。

## Tests

新增 `backend/tests/test_qianchuan_plan_drafts.py`，覆盖：

- 创建与显式更新只产生两次 mock GET，且只保存验证后的快照；
- 写开关关闭、素材失败、非法字段和不存在的草稿在平台调用前停止；
- 跨用户访问返回 404；
- 不可用抖音号或商品不落库；
- 平台读取期间凭证版本变化时中止；
- 快照中不包含 access token。

验证结果：

- 草稿专项：6 项通过；
- 千川全量回归：113 项通过，耗时 184.350 秒；
- Python 编译检查和 `git diff --check` 通过。

## Definition of Done

- [x] 草稿服务与原素材上传服务分离。
- [x] 草稿 API 使用独立 DTO 和旁路表模型。
- [x] 所有外部调用均为 mock GET，测试未发送真实千川写请求。
- [x] 归属链在平台读取前后均被校验。
- [x] 创建和更新均受用户、租户、功能开关和显式迁移约束。
- [x] 千川现有 113 项回归全部通过。
- [x] 真实计划写开关与 worker 开关保持关闭。
- [ ] 生产数据库完成可回滚的显式迁移。
- [ ] operation 幂等状态机、mock 提交、结果不确定回查和独立 worker 完成。

## 下一步

operation 幂等状态机、CAS 占位和结果不确定保护已在 Phase 4B 完成，详见 `20-QIANCHUAN-PHASE4B-OPERATION-GUARDS.md`。下一阶段再评估独立 worker；只有当查询型回查适配器、数据库迁移回滚和官方计划写接口字段核验全部完成后，才讨论开启真实写入。
