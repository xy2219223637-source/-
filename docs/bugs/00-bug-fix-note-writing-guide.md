# Bug 修复记录指南

## 用途

`docs/bugs/` 用于记录非显而易见的回归、细微根因，以及会影响未来评审的修复。

目标不是重复完整 diff，而是保留：哪里出错了、怎么发现的、为什么会发生、以及如何防止再次发生。

## 何时写 Bug 记录

当至少满足以下任一条件时，写一条：

- bug 的根因不明显
- bug 跨越了模块或包边界
- 表面上看像一回事，但其实由另一层导致
- 修复增加或修改了回归测试
- 未来的重构很容易把同样的问题重新引入

不要给每个很小的错别字或一行修复都写一条。

## 必需章节

每条 bug 记录都应包含以下章节。每个章节保持简短。

### 1. 问题

用 2-5 行描述观察到的症状。包括：坏了什么、在哪里坏的、最小可复现行为，以及影响或严重度（受影响用户、数据完整性、影响范围）。

### 复现

描述所需的环境状态和触发动作，方便后续读者复现。包括准确步骤、任何前置条件（例如“必须以管理员身份登录”“需要 2000 个并发请求”），以及如适用时的最小复现脚本。

### 2. 诊断方法

这一节是强制的。描述问题是如何被定位出来的，不只是最终根因。

包括：

- 为什么诊断困难
- 先检查了什么，为什么先查它
- 测试了哪些假设并排除了哪些假设（如果诊断过程不简单，这是必须的）
- 什么直接证据确认了真正原因

如果诊断很直接（一个明确的烟雾弹指标或日志），请明确说明，并解释为什么不需要迭代，例如：“崩溃堆栈正好指向我们代码里的 return 语句（第 2 帧是我们的代码）”。否则，至少给出一条被排除的替代路径。

### 3. 根因

用 1-3 个要点解释真实原因。提到实际涉及的模块或子系统。如果 bug 有多个原因，请分别列出。

**边界规则：** 诊断方法描述的是调查过程和证据链。根因描述的是从系统内部机制角度解释为什么会发生这个 bug。这两部分不应重复同一信息。对于调查过程本身就直接揭示了机制的 bug，两部分可以合并为 `*Diagnosis & Root Cause`，并说明为什么这样处理。

### 4. 修复

从设计意图角度解释解决方案，而不是逐行描述代码改动。包括改了什么、在哪改、为什么这能解决根因。

### 5. 测试

列出新增或更新的回归覆盖。包括测试文件路径、它保护什么，以及测试层级（unit / component / integration / e2e）。如果只有 e2e 覆盖，请解释为什么更低层级不可行。

如果自动化测试不现实（竞态、时序、第三方依赖），请记录人工验证流程，并明确说明为什么没有添加自动化测试。

### 6. 受影响工件

列出被改动的代码文件、配置文件、部署清单或基础设施定义。使用 `path/to/file.ts:line-numbers` 格式，并简要说明。不要贴大段 diff。

### 7. 未来重构注意事项

添加 1-3 个要点，描述未来变更时要小心别破坏什么。要写出具体的代码模式和具体的错误场景，例如：“如果有人换了连接池库，新驱动可能依赖 `finalize()` 而不是显式 `close()`”。

### 8. 预防缺口（可选）

添加 1-2 个要点，说明是什么评审、测试或流程步骤缺失，才让这个 bug 走到了线上。这里说的是系统性缺口，不是个人责任。

## 推荐模板

```md
# 0X 简短 bug 标题

## Problem

- what broke
- where it broke
- minimal visible symptom
- impact or severity

## Reproduction

- environment and preconditions
- triggering steps
- minimal reproduction script if applicable

## Diagnostic Method

- diagnosis difficulty (why this was hard, or "straightforward")
- investigation path (what was checked first)
- rejected hypotheses (if diagnosis was non-trivial)
- decisive evidence that confirmed the issue

## Root Cause

- actual cause 1
- actual cause 2
- (may merge with Diagnostic Method as `*Diagnosis & Root Cause` if the evidence trail IS the mechanism)

## Fix

- main change 1
- main change 2

## Tests

- `path/to/test-file` - what it verifies (level: unit/component/integration/e2e)
- if no automated test: manual verification steps and reason

## Affected Artifacts

- `path/to/file:lines` - annotation

## Notes For Future Refactors

- risk or invariant 1 (concrete pattern + concrete mistake scenario)
- risk or invariant 2

## Prevention Gap (optional)

- what review/test/process step was missing
```

## 文件名建议

对于小型和中型项目，以下两种都可以：

- 编号：`docs/bugs/01-short-bug-name.md`
- 带日期：`docs/bugs/YYYY-MM-DD-short-bug-name.md`

如果你预计 bug 记录会变成长期参考库，优先使用编号文件名。

## 校验清单

在提交 bug 记录前，检查：

- [ ] Problem 包含影响或严重度
- [ ] Reproduction 包含环境、触发方式和最小步骤
- [ ] Diagnostic Method 描述的是调查过程，而不只是原因
- [ ] Diagnostic Method 包含被排除的假设（或者明确说明“straightforward”并给出原因）
- [ ] Root Cause 指明了具体模块或子系统；或者已合并的部分带有注释说明
- [ ] Fix 解释的是为什么这个改动有效，而不只是改了什么
- [ ] Tests 包含测试层级（unit/component/integration/e2e），或者在没有自动化测试时给出了明确原因和人工验证
- [ ] Notes For Future Refactors 指明了具体模式和具体错误场景

## 其他规则

- 每个非平凡 bug 修复都应增加或更新自动化测试覆盖。
- 如果创建了 bug 记录，请附上测试证据路径或验证证据。
- 用 `docs/architecture/` 记录当前设计事实，用 `docs/bugs/` 记住重要失败及其修复原因。
