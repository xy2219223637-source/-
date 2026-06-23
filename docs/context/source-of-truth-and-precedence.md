# 事实来源与优先级

## 用途

本文件定义哪种工件回答哪类问题。

使用它可以避免把稳定事实、执行记录和历史上下文混在一起。

## 按问题划分的优先级

### 现在应该构建什么？

主要来源：

- `docs/requirements/`

辅助来源：

- `docs/input/`
- `docs/discussions/`

规则：

- `docs/input/` 保存原始源材料
- `docs/requirements/` 是可实现的解释
- 如果两者不一致，不要悄悄依赖聊天记忆，而要明确更新需求文件

### 当前支持的应用行为是什么？

主要来源：

- `docs/design/`

规则：

- `docs/design/` 负责应用层功能、流程、角色和页面行为
- 功能需求文件可以驱动变化，但稳定的应用行为应该收敛到 `docs/design/` 下的 owner 文档中

### 当前支持的技术结构是什么？

主要来源：

- `docs/architecture/`

规则：

- `docs/architecture/` 负责技术边界、模块职责和跨切面实现规则

### 数据库事实是什么？

主要来源：

- 项目的数据库模型文件

示例：

- `model/`
- schema DSL 文件
- ORM 模型定义

规则：

- 数据库定义由 model/schema 工件负责，而不是由计划文本或说明文档负责
- 文档可以解释意图，但 model 文件才是事实来源

### API 契约事实是什么？

主要来源：

- API schema 文件、OpenAPI/GraphQL 定义、路由定义，或后端契约测试

规则：

- 说明文档可以概括 API 意图，但可执行的或 schema 级别的 API 契约才是最终权威

### 外部集成事实是什么？

主要来源：

- 外部系统的集成契约文档
- 已提交的适配器配置或集成测试

规则：

- 不要仅凭 UI 需求去臆造外部系统行为

### 环境/部署事实是什么？

主要来源：

- 部署清单
- 环境 schema 文件
- 基础设施配置

规则：

- 计划和文档可以描述部署意图，但已提交的部署/配置工件才是运行事实

### 这个切片应该如何执行并收尾？

主要来源：

- `docs/plans/`

规则：

- 计划是执行契约，不是长期 owner 文档

### 执行过程中实际发生了什么？

主要来源：

- `docs/logs/`

辅助来源：

- `docs/testing/`
- `docs/bugs/`
- `docs/audits/`
- `docs/retrospectives/`

### 未来 AI 会话应从重复失败中学到什么？

主要来源：

- `docs/skills/`
- `docs/lessons/`

规则：

- `docs/skills/` 用于可复用提示和 playbook
- `docs/lessons/` 用于可复用的工程教训和警示模式

## 冲突解决

- 如果原始输入和综合需求不一致，请更新 `docs/requirements/`，或者在编码前重新开启澄清。
- 如果需求和 owner 文档不一致，判断该需求是否改变了支持基线；然后明确更新 `docs/design/` 或 `docs/architecture/`。
- 如果 live code 和 owner 文档不一致，要把它视为实现漂移或文档过时；不要悄悄二选一。
- 如果解决冲突会改变用户可见行为、数据/模型形状、API 行为、认证/权限行为或外部集成行为，请停下来并请求确认。
- 如果验证失败，即使实现看起来已经完成，计划也不能关闭。
- 如果 model/schema 文件和说明文档在数据库事实方面不一致，以 model/schema 文件为准；应有意更新说明文档或 model。

## 遗留或过时文档模式

当 `docs/context/project-context.md` 把文档新鲜度标记为 `stale`、`unknown` 或 `partially stale`，并且涉及当前切片时，使用这个模式。

- live code 和可执行契约代表当前行为，但不自动等于期望行为
- 只有在与 live code、需求和人工/产品意图重新验证过之后，owner 文档才是预期的吸引子
- 在修改行为之前，要把每个冲突分类为 `implementation drift`、`doc drift` 或 `intentional legacy behavior`，并写进需求、讨论、分析或计划文件
- 在基线审计或人工确认记录清楚什么应该保留、什么应该改变之前，AI 自治默认是 `research-only` 或 `plan-first`
- 不要在不记录漂移分类的情况下，直接“修正”代码去匹配过时文档，或者把文档改成匹配代码

## 简单经验法则

- 稳定行为和结构属于 owner 文档
- 执行属于计划和日志
- 历史和诊断属于 bugs、审计、测试记录、回顾和经验教训
