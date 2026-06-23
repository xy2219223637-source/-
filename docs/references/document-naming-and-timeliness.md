# 文档命名与时效性

## 用途

本指南区分稳定 owner 文档和时效性强的流程记录。

对于小型和中型项目，这样可以让仓库更容易导航，同时又不必把每个文件都套进同一种命名风格。

## 两类文件

### 1. 稳定 Owner 文档

这些文档描述当前支持的基线，通常应使用不带日期的稳定名称。

适合使用稳定名称的目录：

- `docs/process/`
- `docs/architecture/`
- `docs/design/`
- `docs/references/`
- `docs/skills/`
- 像 `docs/requirements/product-scope.md` 和 `docs/requirements/mvp.md` 这样的长期需求基线文件

示例：

- `docs/design/app-overview.md`
- `docs/architecture/system-baseline.md`
- `docs/process/application-development-workflow.md`

规则：

- 这些文件应当就地更新
- 不要因为内容变了就新建一个带日期的版本

### 2. 时效性记录

这些文档记录执行历史、调查上下文或带日期的决定。

这些文件通常应在路径或文件名里包含日期。

适合使用带日期命名的目录：

- `docs/logs/`
- `docs/testing/`
- `docs/discussions/`
- `docs/analysis/`
- `docs/audits/`
- `docs/retrospectives/`
- 大多数一次性的需求综合文件和实现计划

## 推荐路径约定

### 日志

- `docs/logs/YYYY/MM-DD.md`

### 测试记录

- `docs/testing/YYYY/MM-DD.md`

### 讨论

- `docs/discussions/YYYY-MM-DD-topic.md`

示例：

- `docs/discussions/2026-05-21-user-management-scope.md`
- `docs/discussions/2026-05-21-order-status-rules.md`
- `docs/discussions/2026-05-21-prototype-gap-checkout-flow.md`

### 分析

- `docs/analysis/YYYY-MM-DD-topic.md`

示例：

- `docs/analysis/2026-05-21-menu-structure-options.md`
- `docs/analysis/2026-05-21-prototype-feasibility-review.md`
- `docs/analysis/2026-05-21-auth-strategy-comparison.md`

### 审计

- `docs/audits/YYYY-MM-DD-<kind>-<topic>.md`

示例：

- `docs/audits/2026-05-21-document-audit-user-management.md`
- `docs/audits/2026-05-21-closure-audit-order-list.md`

### 回顾

- `docs/retrospectives/YYYY-MM-DD-topic.md`

示例：

- `docs/retrospectives/2026-05-21-checkout-prototype-gap.md`
- `docs/retrospectives/2026-05-21-pm-handoff-missing-analysis.md`

### 计划

对于小型和中型项目，优先使用简单的带日期计划名：

- `docs/plans/YYYY-MM-DD-topic-plan.md`

示例：

- `docs/plans/2026-05-21-user-list-plan.md`
- `docs/plans/2026-05-21-role-permission-alignment-plan.md`

如果项目后来积累了很多计划，需要更强的索引，也可以加数字前缀：

- `docs/plans/NNN-YYYY-MM-DD-topic-plan.md`

示例：

- `docs/plans/012-2026-05-21-user-list-plan.md`
- `docs/plans/013-2026-05-21-checkout-validation-plan.md`

### 一次性需求综合文件

如果文件是一次性切片而不是稳定基线文件，优先使用带日期的名字：

- `docs/requirements/YYYY-MM-DD-feature-name.md`

示例：

- `docs/requirements/2026-05-21-user-management.md`
- `docs/requirements/2026-05-21-order-refund-flow.md`
- `docs/requirements/2026-05-21-dashboard-homepage.md`

## Bug 笔记

bug 笔记是历史记录，但通常按问题身份而不是按日期来引用。

对于小型和中型项目，下面两种都可以：

- `docs/bugs/01-short-bug-name.md`
- `docs/bugs/YYYY-MM-DD-short-bug-name.md`

示例：

- `docs/bugs/01-order-status-double-submit.md`
- `docs/bugs/2026-05-21-login-token-refresh-loop.md`

建议：

- 如果 bug 笔记会变成长期库，优先使用编号文件名
- 如果 bug 笔记数量不多，主要服务本地团队记忆，使用日期文件名也可以

## 简单经验法则

- 如果文件回答的是“当前支持的基线是什么？” -> 用稳定名称
- 如果文件回答的是“这一轮 / 这一天 / 这次调查里发生了什么？” -> 用带日期的名称

## 快速复制集

把这些当作可直接复制的模式：

```text
docs/logs/2026/05-21.md
docs/testing/2026/05-21.md
docs/discussions/2026-05-21-user-management-scope.md
docs/analysis/2026-05-21-auth-strategy-comparison.md
docs/audits/2026-05-21-document-audit-user-management.md
docs/plans/2026-05-21-user-list-plan.md
docs/requirements/2026-05-21-order-refund-flow.md
docs/retrospectives/2026-05-21-checkout-prototype-gap.md
docs/bugs/01-order-status-double-submit.md
```
