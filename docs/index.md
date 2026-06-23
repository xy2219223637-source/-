# `<project-name>` 文档索引

## 用途

这个 `docs/` 树是 `<project-name>` 的持久记忆和路由入口。

- 在进行工作流、需求、设计或实现变更前，先从这里开始
- 优先选择最能回答当前问题的最小文件
- 把持久结论写进文件里，而不要只放在聊天里

## 路由权威

这个文件是顶层文档路由器。

- `docs/index.md` 负责导航和目录职责
- `AGENTS.md` 负责代理工作流规则和执行预期
- `docs/design/` 和 `docs/architecture/` 负责稳定的项目吸引子

## 先读这些

| 如果你需要... | 先读这个 | 然后再读 |
| --- | --- | --- |
| 理解强制性的 AI 上下文和当前项目状态 | `docs/context/README.md` | `docs/context/project-context.md`, `docs/context/ai-autonomy-policy.md`, `docs/context/codebase-map.md` |
| 理解轻量默认开发工作流 | `docs/process/application-development-workflow.md` | `AGENTS.md` |
| 选择下一个 AI 可执行工作项 | `docs/backlog/README.md` | `docs/context/ai-autonomy-policy.md`、当前需求和 owner 文档 |
| 读取原始 PM、原型、文章或卡片集输入 | `docs/input/README.md` | `docs/input/` 下的当前文件 |
| 阅读说明性质的方法论文章 | `docs/articles/README.md` | `docs/articles/` 下相关文章 |
| 澄清含糊需求 | `docs/discussions/README.md` | `docs/requirements/00-requirement-synthesis-guide.md` |
| 在编码前路由任务 | `AGENTS.md` | `docs/skills/README.md`、相关 owner 文档，以及 `docs/plans/00-plan-authoring-and-execution-guide.md` |
| 判断某个现有技能是否适用 | `docs/skills/README.md` | 相关 owner 文档和当前需求 |
| 理解项目目标和产品形态 | `docs/architecture/project-vision.md` | `docs/design/app-overview.md` |
| 理解当前应用层基线 | `docs/design/app-overview.md` | `docs/design/feature-inventory.md`, `docs/design/roles-and-permissions.md` |
| 理解当前技术基线 | `docs/architecture/system-baseline.md` | `docs/architecture/module-boundaries.md` |
| 理解 owner 文档优先级与事实来源边界 | `docs/context/source-of-truth-and-precedence.md` | 相关 owner 文档 |
| 开始或审查非平凡实现 | `AGENTS.md` | `docs/skills/README.md`, `docs/plans/00-plan-authoring-and-execution-guide.md`，当前计划，以及 `docs/audits/00-audit-execution-guide.md` |
| 审查审计工作流或必需的计划/收尾审计 | `docs/audits/00-audit-execution-guide.md` | `docs/skills/` 里的相关提示 |
| 理解哪些文档应使用带日期的文件名而不是稳定名称 | `docs/references/document-naming-and-timeliness.md` | 目标目录中的相关指南 |
| 快速复制新日期文档的推荐命名模式 | `docs/references/document-naming-and-timeliness.md` | `Quick Copy Set` 小节 |
| 复制现成的日期文档骨架 | `docs/examples/README.md` | 重命名最接近的 `.example.md` 文件 |
| 查看一个真实的小功能示例流程 | `docs/examples/complete-small-app-walkthrough.md` | 然后从 `docs/examples/` 复制最接近的骨架 |
| 诊断 e2e 测试失败（Playwright） | `docs/references/playwright-e2e-guide.md` | `playwright.config.ts` |
| 检查变更后需要更新哪些文档 | `docs/references/maintenance-checklist.md` | `docs/design/` 或 `docs/architecture/` 中最相关的文件 |
| 查看最近的实现历史 | `docs/logs/index.md` | 最近的带日期日志文件 |
| 查找过去的细微回归 | `docs/bugs/00-bug-fix-note-writing-guide.md` | `docs/bugs/` 中相关文件 |
| 记录或审查探索性/手动测试 | `docs/testing/index.md` | 相关的带日期测试记录 |
| 检查最新已知的验证基线状态 | `docs/testing/known-good-baselines.md` | 最近的测试或日志记录 |
| 审查取舍或开放设计调查 | `docs/analysis/README.md` | 相关分析记录 |
| 审查可复用的工程经验 | `docs/lessons/README.md` | 相关编号经验条目 |
| 阅读可直接实现的需求 | `docs/requirements/README.md` | 当前需求文件 |
| 审查已落地结果为何仍未达到预期 | `docs/retrospectives/README.md` | 相关回顾记录 |

## 推荐默认路径

对于大多数小型和中型项目，默认路径如下：

1. `docs/context/`
2. 选择工作时查看 `docs/backlog/`
3. `docs/input/`
4. `docs/requirements/`
5. `docs/design/` 和 `docs/architecture/`
6. 路由任务并选择可复用技能
7. 当满足规划触发条件时，进入 `docs/plans/`
8. 需要计划/收尾审计证据时，使用 `docs/audits/`
9. `docs/logs/`
10. 需要时使用 `docs/bugs/`

只有在任务复杂度或歧义确实需要时，才使用 `docs/discussions/`、额外的 `docs/testing/` 记录、`docs/skills/`、`docs/analysis/` 和 `docs/retrospectives/`。

## 技能路由

| 如果任务是... | 先读这个 | 然后决定 |
| --- | --- | --- |
| 含糊的需求 | `docs/requirements/00-requirement-synthesis-guide.md` | 是否需要先写需求文件或讨论文件 |
| 非平凡实现 | `AGENTS.md` | 按阶段或条目判断需要哪些技能，然后使用 `docs/plans/00-plan-authoring-and-execution-guide.md` |
| 文档、计划或收尾验证 | `docs/skills/README.md` | 哪个审计提示或评审技能适用 |
| 重复出现的已知方法或审查模式 | 相关 owner 文档 | 现有技能是否适用，或者是否应创建新技能 |

技能负责选择工作方法，它们不能替代需求、设计、架构或 owner 文档路由。

## 目录职责

- `docs/process/` - 工作流和操作流程文档
- `docs/context/` - 强制性的 AI 上下文、owner 优先级和项目级约定
- `docs/backlog/` - 按优先级排列的候选工作和 AI 可执行下一步
- `docs/input/` - 原始外部输入和复制来的源材料
- `docs/discussions/` - 含糊需求的澄清记录和未决问题
- `docs/requirements/` - 综合后的、可直接实现的需求文档
- `docs/design/` - 稳定的应用层功能和业务流程 owner 文档
- `docs/architecture/` - 稳定的技术基线和模块边界文档
- `docs/lessons/` - 从重复问题和恢复中提炼出的持久工程经验
- `docs/references/` - 稳定的查阅指南和维护辅助材料
- `docs/articles/` - 面向外部的 methodology 和说明性文章
- `docs/examples/` - 带日期工作文档的可复制小骨架
- `docs/plans/` - 带收尾标准的执行计划
- `docs/audits/` - 审计方法和审计记录，包括已创建计划所需的计划/收尾审计证据
- `docs/skills/` - 可选的可复用 AI 提示和审计/评审 playbook
- `docs/logs/` - 带日期的实现记忆
- `docs/testing/` - 可选的探索性和手动测试记录
- `docs/bugs/` - 复杂回归历史和根因记录
- `docs/analysis/` - 可选的调查、比较和设计取舍
- `docs/retrospectives/` - 可选的实现后差距分析和流程改进
- `docs/archive/` - 由人工决定移入的非活动文档；保留以便历史参考

## 核心原则

用文件保存持久真相。

- 输入记录来源
- 上下文记录强制性的项目规则和事实来源优先级
- 待办池记录优先级最高的下一步和自治标签
- 讨论记录哪里不清楚
- 需求记录应该构建什么
- 设计和架构记录必须始终成立的内容
- 事实来源优先级说明哪个工件在某个问题上胜出
- 计划记录一个非平凡切片如何收尾
- 审计记录如何挑战这些说法
- 日志、测试和 bug 记录保存证明和记忆
- 回顾解释为什么上一次迭代仍然没有达到目标

## 命名规则

- 稳定的 owner 文档保持稳定名称
- 时效性强的记录通常应包含日期
- 参见 `docs/references/document-naming-and-timeliness.md`
