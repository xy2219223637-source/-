# 差异标准和规范审查提示

> **定位**：这不是全仓库深度审核，也不是开放式对抗性审核；它针对围绕一个明显的差异固定点，对当前变更做双轴复核：`Standards`轴检查是否符合仓库规范与所有者文档，`Spec`轴检查是否兑现来源计划/规范/问题/分析文档要求。
> **前提**：执行前必须先阅读`docs/index.md`、`AGENTS.md`、相关所有者文档；如果审核目标关联`docs/plans/*.md`，还必须阅读`docs/plans/00-plan-authoring-and-execution-guide.md`；如果命中历史重复重新打开的设计问题，还必须回查`docs/references/reopened-design-decisions-and-audit-adjudications.md`。
> **适用场景**：回顾某个分支、某次PR、某个定点后续的工作、某个计划的实现结果，判断“代码是否按本标准仓库实现了它本该实现的东西”。

---

## 与 Deep Audit 的区别

本提示词不替代 `docs/skills/deep-audit-prompts.md`。

区别如下：

1. **深度审计** 针对实时回购的结构性问题质量，按维度进行广泛或深层次的挖掘式审查。
2. **本提示词**针对一个明显的差异，重点回答两件事：
   -此次是否违反了本仓库编码标准？
   - 最近是否真正实现了来源规范/计划，而不仅仅是“看起来做了差不多的东西”？
3.如果发现是仓库长期结构性问题，可在审核里指出，但不要把本提示词扩写成一次全仓库深度审核。

---

## 输入要求

执行提示词时，必须先明确输入三个：

1. **定点**
   - 用于计算 diff 的基线，如 `main`、 某个分支、 某个提交、 某个 tag、`HEAD~N`。
   - 默认比较方式使用三点 diff：`git diff <fixed-point>...HEAD`。

2. **标准来源**
   - 本仓库中描述“应如何实现”的文档与规则来源。
   - 至少包括：`AGENTS.md`、`docs/index.md`、相关所有者文档。
   - 必要时补充：`docs/skills/react19-best-practices-review.md`、`docs/references/reopened-design-decisions-and-audit-adjudications.md`、`docs/references/audit-tooling.md`。

3. **规格来源**
   - 本次本来应该兑现的需求来源。
   - 优先级：
     1. 用户明确指定的 `docs/plans/*.md` / `docs/analysis/*.md` / 其他规范文件
     2. 提交/分支 名显式引用的计划或分析文档
     3. 若没有正式规范，则明确记为 `no explicit spec source`

---

## 输出落盘规则

结果必须落盘到`docs/analysis/`，不允许只停留在对话输出里。

默认使用单文件：

- `docs/analysis/YYYY-MM-DD-diff-standards-and-spec-review-<short-tag>.md`

规则：

1. 文件名必须带 `YYYY-MM-DD` 日期。
2. `<short-tag>` 使用中文 kebab-case 百年回顾对象。
3. 同一天多次执行时，必须通过 `<short-tag>` 或递增后缀区分。
4.最终回复时给出必须保存路径。

---

## 标准轴

`Standards` 轴只检查**当前diff是否违反了仓库标准**，而不是重做一次全仓库风格巡检。

优先检查：

1. 是否违背所有者文档描述的合同/所有者边界。
2.是否违反 `AGENTS.md` 中的编码约定、React 19规则、UI组件使用规则、渲染器契约规则。
3. 是否引入了本仓库已明确不接受的情景模式、兼容层污染、双状态、跨层所有者尚未。
4. 是否违反现有的 lint/audit/check 脚本覆盖的硬规则。
5. 是否重新开放`docs/references/reopened-design-decisions-and-audit-adjudications.md`中已经裁定的问题，但没有给出新的证据。

不宜在 `Standards` 轴报告：

1.纯个人代码风格偏好。
2. 工具已稳定覆盖、且当前 diff 没有绕过该检查的机械问题。
3. 与当前 diff 无关的老问题。

---

## 规格轴

`Spec` 轴检查**当前 diff 是否真正实现了来源 spec / plan 的要求**。

优先检查：

1.规范要求的行为是否取消、只做了一半、或只做了表面接口。
2. diff 是否引入了spec未要求的范围蠕变。
3. diff 外观是否实现了要求，但真实语义与规格/计划不一致。
4. 对计划类文档，是否兑现了计划的关键项目、证明义务、所有者文档更新、重点验证。
5. 如果规范本身含糊不清，是否至少能够指出“这里无法继续做规范轴判断，因为来源文档没有明确”。

不宜在 `Spec` 轴报告：

1.与规范无关的纯结构洁癖问题。
2.spec本身没有要求、也没有隐含需要的“顺手优化”。
3.只因为现实方式不同于审稿人个人偏好就判定“不符合规范”。

---

## 执行步骤

1. 固定回顾基线，记录`git diff <fixed-point>...HEAD`和`git log <fixed-point>..HEAD --oneline`。
2.收集标准来源。
3. 确认规格来源；若没有，明确记为 `no explicit spec source`。
4.先看diff，再回查owner docs/spec，不要只看文档做空审。
5.分别分布`Standards` 和 `Spec` 两个部分发现；两轴不要混写。
6. 如果某条同时发现两轴，还要分别说明它为什么既是标准违约，又是规格兑现问题。
7. 如果某个轴没有找到，明确写 `No findings`，不要省略。

---

## 每条发现必须包含的字段

每条发现至少包含：

1. **严重性**：`P0` / `P1` / `P2` / `P3`
2. **位置**：文件路径 + 行号范围，必要时补充 commit / hunk 上下文
3. **什么**：问题是什么
4. **为什么重要**：为什么值得关注
5. **来源**：
   - `Standards` 轴：引用具体标准来源
   - `Spec` 轴：引用具体规格/计划段落
6. **Fix Direction**: 一句话说明修复方向

---

## 输出结构

推荐保存为以下结构：```text
# Diff Standards And Spec Review

## Scope
- Fixed point
- Review object
- Standards sources
- Spec source

## Standards
- Findings ordered by severity
- Or `No findings`

## Spec
- Findings ordered by severity
- Or `No findings`

## Overall Assessment
- 两轴各有多少发现
- 最严重的单个问题是什么
- 若 spec source 缺失，明确说明 spec 轴结论受限
```

---

# # 输出限制

1. 不要把两轴混成一个总问题池。
2. 不要把全仓库老问题塞进这次 diff review。
3. 不要把 tooling 已经稳定保证且当前 diff 未绕过的机械问题当主要发现重复报告。
4. 不要把“spec 没写清楚”擅自脑补成 reviewer 自己的需求。
5. 不要为了凑发现数量保留低置信度猜测；如果不确定，就明确写不确定。

---

# # 可直接复用的提示词正文

```text
请对当前 nop-chaos-flux 仓库执行一次“diff standards and spec review”。

目标：围绕一个明确的 fixed point，分别从 `Standards` 和 `Spec` 两个轴审查当前 diff。

执行前先阅读：
1. docs/index.md
2. AGENTS.md
3. 相关 owner docs
4. 若命中 React 19 / renderer / UI 规范，再读 docs/skills/react19-best-practices-review.md
5. 若命中历史重复 reopened 的设计问题，再读 docs/references/reopened-design-decisions-and-audit-adjudications.md
6. 若 spec source 是 docs/plans/*.md，再读 docs/plans/00-plan-authoring-and-execution-guide.md

要求：
1. 先固定 review 基线，使用 `git diff <fixed-point>...HEAD` 和 `git log <fixed-point>..HEAD --oneline`。
2. 明确列出 standards sources 和 spec source。
3. `Standards` 轴只检查当前 diff 是否违反仓库标准。
4. `Spec` 轴只检查当前 diff 是否兑现来源 spec / plan，是否有缺失、半实现、scope creep、或实现错误。
5. 两轴分开输出，不要混写。
6. 每条发现都必须包含：Severity、Location、What、Why it matters、Source、Fix direction。
7. 若某一轴无发现，明确写 `No findings`。
8. 结果必须保存到 `docs/analysis/YYYY-MM-DD-diff-standards-and-spec-review-<short-tag>.md`。

输出结构：

# Diff Standards And Spec Review

## Scope
- Fixed point
- Review object
- Standards sources
- Spec source

## Standards
- Findings ordered by severity, or `No findings`

## Spec
- Findings ordered by severity, or `No findings`

## Overall Assessment
- 两轴发现数量
- 最严重问题
- 如 spec source 缺失，说明 spec 轴受限
```
