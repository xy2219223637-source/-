# 架构文档索引

## 用途

`docs/architecture/` 定义 `<project-name>` 的稳定跨切面技术基线。

应用层功能和业务设计请使用 `docs/design/`。跨多个功能的技术结构请使用 `docs/architecture/`。

## 建议阅读顺序

1. `project-vision.md`
2. `system-baseline.md`
3. `module-boundaries.md`
4. 随着项目发展，再阅读更具体的 owner 文档

## Owner 文档规则

- 一个文档只负责一个稳定主题
- 解释当前原因和约束，而不是步骤历史
- 当实现改变了支持中的架构时，请在同一次变更中更新 owner 文档
- 被否决的方案和探索笔记移到 `docs/analysis/`
- 当技术规则是为了支持具体产品行为而存在时，请引用对应的 `docs/design/` 下应用层 owner 文档

## 归属边界

- `docs/design/` 负责应用行为和功能语义
- `docs/architecture/` 负责技术结构和跨切面实现规则
- 如果问题涉及持久化或 schema 事实，那么 model/schema 文件本身才是权威

## 初始 Owner 文档

- `project-vision.md` - 产品与系统意图
- `system-baseline.md` - 当前技术栈和运行时基线
- `module-boundaries.md` - 包、模块、领域的归属边界
