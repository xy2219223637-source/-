# 技能索引

本目录用于可复用的提示和工作流 playbook。

这些不是一次性的聊天消息，而是可复用的仓库记忆。

技能主要应捕捉可复用的工作方法、审查方法或审计方法。不要把技能当成需求事实、设计事实或架构事实的替代品。

技能库并不是吸引子。若不通过 `AGENTS.md`、`docs/index.md`、当前需求和 owner 文档进行路由，一个庞大的技能库通常会退化成结构化 vibe coding。

这些提示是复制项目的通用默认值。复制模板后，必须根据项目真实的 owner 文档、受保护区域、验证栈、命名约定、已知失败模式和误报容忍度对它们进行定制。

## 技能路由规则

在选择技能之前：

1. 先阅读相关需求和 owner 文档。
2. 用 `AGENTS.md` 对任务类型分类。
3. 按工作方法匹配技能，而不是只看业务标签。
4. 如果多个技能都可能适用，在实现前让独立子代理或评审者选择。
5. 如果没有任何现成技能明显适用，记录 `Skill: none`，并按正常的文档驱动工作流继续。
6. 对于非平凡计划，每个依赖技能的阶段或条目都应在计划中记录技能选择依据和审查结果。

不要拿宽泛的业务场景技能去替代项目专属的 owner 文档。如果某个场景反复出现，先检查是否是路由、owner 文档或计划指引缺失。只有当可复用的方法已经足够稳定时，才创建新技能。

## 技能注册表

| 技能 | 何时使用 | 何时不要使用 | 所需输入 | 预期输出 |
| ---- | -------- | ------------ | -------- | -------- |
| `document-audit-prompt.md` | 需求、设计或架构文档可能不完整或不一致 | 任务非常简单且局部 | 目标文档路径、相关输入或 owner 文档 | 审计发现和修订目标 |
| `plan-audit-prompt.md` | 非平凡计划已准备好在实现前接受挑战 | 还没有计划 | 计划文件、相关需求和 owner 文档 | 带具体问题的通过/失败审计 |
| `closure-audit-prompt.md` | 实现声称已完成，需要独立收尾审查 | 工作仍在进行中 | 计划、验证证据、相关变更文档 | 收尾结论和剩余缺口 |
| `requirement-gap-retrospective-prompt.md` | 已落地的工作仍未达到预期，且需求管线需要诊断 | 需求仍在起草中 | 原始输入、需求/讨论文档、交付结果 | 回顾发现和流程修正 |
| `multi-dimensional-audit-prompt.md` | 高风险工作需要同时从多个维度挑战 | 单对象审计已经足够 | 相关需求/owner 文档、计划或变更区域、验证证据 | 按维度分组的发现 |
| `open-ended-audit-prompt.md` | 可能存在超出常规清单的隐藏问题 | 工作只需要狭义结构化审计 | 相关需求/owner 文档、如有则含计划、日志、live 变更代码 | 对抗性发现和未知风险记录 |
| `deep-audit-prompts.md` | 需要对代码库进行20维度全面深度审计 | 只需要单维度或浅层审计 | 相关需求/owner 文档、AGENTS.md、架构文档 | 20维度审计发现汇总报告 |
| `code-quality-audit-prompt.md` | 审核代码质量，关注实际实施质量 | 任务与代码质量无关 | 目标代码区域、owner 文档、质量标准 | 代码质量问题和改进建议 |
| `diff-standards-and-spec-review-prompt.md` | 检查与仓库标准和原始规范的差异 | 没有可参照的标准或规范 | 变更代码、规范文档、标准要求 | 标准偏差和合规建议 |
| `unit-test-logic-and-contract-coverage-audit-prompt.md` | 审核单元测试是否真正保护稳定契约 | 测试覆盖已经充分验证 | 测试文件、被测代码、契约定义 | 测试覆盖缺口和强化建议 |

## 起始技能

- `document-audit-prompt.md`
- `plan-audit-prompt.md`
- `closure-audit-prompt.md`
- `requirement-gap-retrospective-prompt.md`
- `multi-dimensional-audit-prompt.md`
- `open-ended-audit-prompt.md`
- `deep-audit-prompts.md`
- `code-quality-audit-prompt.md`
- `diff-standards-and-spec-review-prompt.md`
- `unit-test-logic-and-contract-coverage-audit-prompt.md`
