# 吸引子引导工程模板

这个模板是一个面向 AI 辅助产品开发的轻量级应用层项目脚手架。

它适用于普通业务应用，例如管理系统、门户、工作流应用、仪表盘、内部工具，以及 CRUD 密集型领域产品。

它面向已经具备技术栈的小型和中型项目。

它不是一个 starter app，也不包含生成好的产品代码。它的目的，是为仓库提供足够持久的结构，让人和 AI 可以共享需求、owner 文档基线、计划、验证和项目记忆，而不引入沉重的流程开销。

## AGE 的含义

AGE 的全称是 **Attractor-Guided Engineering**。

AGE 从一个问题开始：

当人类和 AI 随时间改动这个仓库时，它应该持续向什么方向收敛？

在这个模板里，吸引子指的是应用项目在快速 AI 辅助迭代中应持续回归的稳定产品、设计和架构结构。

对于应用项目，吸引子由一小组持久的 owner 文件承载：

- `docs/context/` - 强制性的项目上下文和事实来源规则
- `docs/backlog/` - 按优先级排列的候选工作和 AI 可执行的下一步
- `docs/requirements/` - 可直接实现的需求解释
- `docs/design/` - 稳定的应用层行为和功能 owner 文档
- `docs/architecture/` - 稳定的技术结构和模块边界

计划、测试、审计、日志、bug 和验证都不是吸引子本身。它们是工程支架：帮助证明某次变更确实把仓库推向吸引子，而不只是完成清单的局部控制。

如果某次变更与 owner 文档基线冲突、只把变化藏在计划里，或者让后续会话无法从仓库文件中恢复当前事实，那么即使测试通过，它仍然不符合 AGE。

## AGE 不只是支架工程

先支架后吸引子的工程会问：

- 如何约束 AI？
- 如何验证输出？
- 如何审计并记住发生过什么？

AGE 先问的是另一个问题：

- 这个项目应该持续回归什么稳定结构？

计划、测试、审计、日志、bug 记录和 CI 等支架，只有在吸引子存在之后才有意义。它们本身并不定义正确性，而是相对于 owner 文档基线去度量、纠正并保存仓库轨迹。

## AGE 不是规格驱动开发

当行为变化需要组织成结构化规格增量时，规格驱动工作流很有用。

AGE 不会强迫所有事实都走同一套 spec/change/archive 工作流，也不会把一棵规格树当成唯一的事实来源。

在 AGE 中：

- 需求回答现在应该构建什么
- 设计文档回答当前支持的应用行为
- 架构文档回答当前支持的技术结构
- 计划回答一个非平凡切片如何收尾
- 测试和审计挑战“已完成”这一说法
- 日志、bug 和测试记录保存轨迹记忆

规格仍然可以有用，但它们只是众多支架中的一种，不是顶层组织模型。

## AGE 不是技能库

可复用技能可以帮助执行重复工作方法，但它们不是吸引子，也不能替代项目专属路由。

一个庞大的技能库如果没有路由，通常会退化成结构化 vibe coding：AI 虽然能快速执行熟悉模式，但仍然需要人工修正来决定哪些 owner 文档重要、哪个技能适用、以及需要什么证明。

在 AGE 中，技能是方法选择器。它们必须通过以下内容来路由：

- `AGENTS.md`
- `docs/index.md`
- 当前需求
- owner 文档
- 计划里的技能选择记录

如果技能选择有歧义，应该由独立子代理或评审者先选。对于非平凡计划，每个阶段或条目都应记录 `Skill: <name>` 或 `Skill: none`。

## 本模板的范围

本仓库是面向应用层项目的 AGE 模板。

AGE 本身比这个模板更宽。框架级项目也可以使用 AGE，但它们需要根据自己的吸引子，定制专属的 owner 文档、指南、路由规则、审计提示、验证策略和评审提示。

更深层的框架级 AGE 实践示例包括：

- [`nop-chaos-flux`](https://gitee.com/canonical-entropy/nop-chaos-flux) - 前端框架和低代码运行时实践，包含 owner 文档优先级、计划收尾、审计、bug、日志和 AGE 方法论
- [`nop-entropy`](https://gitee.com/canonical-entropy/nop-entropy) - 后端框架实践，拥有自己的规范文档和开发过程记忆

不要把这个应用模板原封不动地复制到框架核心仓库里，并认为这样就足够了。应把它当作概念起点，然后为框架定义自己的吸引子和指南。

## 这个模板是怎么来的

这个模板是从现有 Nop 项目中使用的轻量 AI 开发工作流中提炼出来，并适配到小型和中型应用团队的。

主要参考包括：

- [`nop-chaos-flux`](https://gitee.com/canonical-entropy/nop-chaos-flux) - owner 文档优先级、计划收尾、审计、bug、日志和 AGE 方法论
- [`nop-chaos-next`](https://gitee.com/canonical-entropy/nop-chaos-next) - 应用层 `design/`、`input/`、`logs/`、`bugs`、`skills/` 和轻量项目文档实践
- [`nop-entropy`](https://gitee.com/canonical-entropy/nop-entropy) - 规范性 AI 面向文档与开发过程记忆的区分

这个模板经过了多轮评审简化：

1. 从核心 AGE 的 file-in/file-out 思路开始。
2. 按职责拆分原始输入、需求、应用设计、架构、计划、日志、bug 和经验。
3. 为时效性记录增加带日期命名规则。
4. 为常见带日期文档补充可直接复制的示例。
5. 把强制性的 AI 上下文从 `docs/references/` 移到 `docs/context/`，因为如果不是入口路径的一部分，代理就不太可靠地读取参考材料。
6. 增加明确的 AI 自治、待办池、代码库地图和已知良好基线钩子，让 AI 能够在不重新探索整个仓库的情况下选择安全的下一步。
7. 把回顾、技能、测试记录和分析等高级层保持为可选项，这样模板仍然适用于小型和中型项目。
8. 对已创建的计划强制执行计划审计和收尾审计。
9. 在计划中明确技能选择，避免可复用技能替代项目专属路由。

独立评审认为这个模板方向有价值，但也提醒了两个风险：文档表演主义和流程过多。当前版本通过保留轻量默认路径并把可选层显式化，来处理这两个风险。

## 第一次使用

复制模板后，先从这里开始：

- `START-HERE-after-copy.md`

在 Day 0 清单没有完成到足以支持第一小片之前，不要让 AI 去实现功能。

最重要的准备步骤，是在 `docs/context/project-context.md` 中填入真实的当前工作和真实的验证命令。

## 这个模板解决什么问题

很多团队现在会做两种事之一：

- 把一大段需求 dump 到聊天里，然后让 AI 直接生成整个系统
- 只靠部分笔记和薄弱历史记录进行松散的 vibe coding

这两种做法通常都会滑向演示级输出。

这个模板把仓库变成一个持久的执行面，并提供轻量默认路径：

1. 原始输入收集
2. 需求综合
3. 设计基线
4. 路由任务并在需要时选择可复用技能
5. 当满足规划触发条件时写执行计划
6. 实现
7. 验证
8. 收尾

对于已创建的计划，计划审计和收尾审计是默认控制回路的一部分。对于更含糊或更有风险的工作，这个模板还提供可选的文档审计、多维审计、开放式审计、回顾、技能提炼和经验层。

## 这个模板包含什么

- `AGENTS.md` - AI 代理的应用层操作契约
- `START-HERE-after-copy.md` - 复制模板后的 Day 0 清单
- `docs/index.md` - 文档路由器和目录归属基线
- `docs/articles/` - 面向外部的中文方法论文章
- `docs/context/` - 强制性的 AI 上下文、事实来源优先级和项目级约定
- `docs/backlog/` - 按优先级排列的候选工作和 AI 自治标签
- `docs/process/` - 轻量级开发工作流
- `docs/input/` - 原始 PM、原型、卡片集、文章和外部材料
- `docs/discussions/` - 含糊需求的多轮澄清记录
- `docs/requirements/` - 经过提炼的、可直接实现的需求文件
- `docs/design/` - 稳定的应用层 owner 文档，涵盖功能、角色、流程和界面
- `docs/architecture/` - 跨切面的技术基线和模块边界
- `docs/lessons/` - 从重复失败和恢复中提炼出的持久经验
- `docs/plans/` - 带收尾规则和技能选择记录的执行计划
- `docs/audits/` - 审计记录和审计方法，包括已创建计划所需的计划/收尾审计证据
- `docs/skills/` - 可选的可复用提示、评审手册和审计提示模板；复制后的项目应按本地 owner 文档和风险区域进行定制
- `docs/logs/` - 每日开发日志指南和起始索引
- `docs/testing/` - 手动和自动测试记录指南
- `docs/testing/known-good-baselines.md` - 最新的有意义验证基线
- `docs/bugs/` - 复杂回归和根因记录指南
- `docs/analysis/` - 研究和设计调查笔记
- `docs/retrospectives/` - 可选的实现后差距分析和流程改进笔记
-  `docs/user.md`   帮助判断用户的认知边界

## 默认最小设置

对于大多数小型和中型项目，开始编码第一个小片时，你通常只需要这些：

- `AGENTS.md`
- `docs/index.md`
- `docs/context/`
- `docs/backlog/`
- `docs/process/application-development-workflow.md`
- `docs/input/`
- `docs/requirements/`
- `docs/design/`
- `docs/architecture/`

基于触发条件的目录：

- `docs/plans/`，当规划触发条件满足时
- `docs/audits/`，当需要存放计划或收尾审计证据时
- `docs/logs/`，当有真实变更落地时
- `docs/testing/`，当手动或探索性证明有意义时
- `docs/bugs/`，当修复了非显而易见的 bug 或需要保留记忆时
- `docs/skills/`，当某种可复用方法已经稳定到可以复用时

已创建的计划在实现前需要计划审计，在完成前需要收尾审计。

除此之外的一切都属于可选内容，只应在项目复杂度确实值得时使用。

这个模板中附带的审计提示和技能都是通用默认值。复制后，你必须把它们调整为真实项目的受保护区域、owner 文档结构、部署模型、验证栈、命名约定、已知失败模式和误报容忍度。

## 稳定文件与带日期文件

这个模板沿用了现有系统中的同样基本拆分：

- 稳定 owner 文档保持稳定文件名
- 时效性强的流程记录通常带日期

示例：

- 稳定：`docs/design/app-overview.md`，`docs/architecture/system-baseline.md`
- 带日期：`docs/analysis/2026-05-21-topic.md`、`docs/discussions/2026-05-21-topic.md`、`docs/plans/2026-05-21-topic-plan.md`
- 按年份组织的日常记录：`docs/logs/YYYY/MM-DD.md`、`docs/testing/YYYY/MM-DD.md`

参见 `docs/references/document-naming-and-timeliness.md`。

## 核心原则

不要只靠聊天来推进重要工作。

- 原始信息应写入 `docs/input/`
- 强制性的上下文和 owner 优先级应写入 `docs/context/`
- 优先级最高的下一步应写入 `docs/backlog/`
- 不清楚的地方应写入 `docs/discussions/`
- 已经落定的需求应写入 `docs/requirements/`
- 稳定的设计决策应写入 `docs/design/` 和 `docs/architecture/`
- 执行控制应写入 `docs/plans/`
- 证明和历史应写入 `docs/logs/`、`docs/testing/` 和 `docs/bugs/`
- 流程改进应转化为 `docs/skills/`、`docs/lessons/` 或 `docs/retrospectives/`

## 如何开始一个新项目

1. 把这个模板复制到新的仓库根目录。
2. 完成 `START-HERE-after-copy.md`。
3. 把 PM 笔记、原型链接、卡片集文档、文章摘录和外部参考放进 `docs/input/`。
4. 如果输入仍然含糊，在实现前把澄清记录到 `docs/discussions/`。
5. 在让 AI 编码之前，把已落定的范围转换成 `docs/requirements/`。

## 推荐执行模式

1. 在 `docs/input/` 中收集原始材料。
2. 如有需要，在 `docs/discussions/` 中澄清歧义。
3. 在 `docs/requirements/` 中综合出可直接实现的需求。
4. 在 `docs/design/` 中更新稳定应用设计，在 `docs/architecture/` 中更新技术基线，并在需要时让它们互相引用，而不要把两种关注点混进同一个文件。
5. 路由任务并选择可复用技能。
6. 对于会改变契约、数据/模型行为、认证、集成、跨模块行为、跨越多个会话，或者不是非常小的低风险编辑的工作，在 `docs/plans/` 下创建计划。
7. 如果技能选择有歧义，在实现前用独立子代理或评审者来选。
8. 对相关阶段或条目记录 `Skill: <name>` 或 `Skill: none`。
9. 在实现前审计计划。
10. 实现最小完整切片。
11. 运行验证。
12. 对已创建计划执行收尾审计。
13. 更新日志和任何受影响的文档。

在需要时使用这些内容：

- `docs/audits/` 用于文档审计和已存档的计划/收尾审计记录
- `docs/testing/` 用于手动或探索性证明
- `docs/retrospectives/` 用于原型与实现实质偏离时
- `docs/skills/` 用于某种方法重复多次、足够值得复用时
- `docs/lessons/` 用于一条可复用经验应该比单个 bug 或回顾活得更久时

如果重复错误模式不断重现，不要只写纯文字笔记。应考虑逐步把它们提升为可复用审计提示、检查清单、启发式脚本、静态检查、lint 规则、CI 守卫或 codemod，并调到适合被复制项目真实约定、误报容忍度和验证模型的程度。

## 非目标

- 这个模板不规定固定的前端/后端框架。
- 这个模板不包含生成好的应用代码。
- 这个模板不替代你的包管理器、lint、测试或 CI 配置。
- 这个模板不假设规格驱动开发是唯一有效的工件工作流。
- 这个模板不是框架核心仓库的通用 AGE 模板；框架项目需要自己的领域专属 owner 文档和指南。
- 这个模板不是通用技能库。技能必须通过项目路由和 owner 文档选择。

## 延伸阅读

- `docs/articles/from-spec-driven-development-to-attractor-guided-engineering.md`
- `docs/articles/attractor-before-harness-ai-large-scale-development-methodology.md`
- `docs/articles/README.md`

## 许可证

MIT



