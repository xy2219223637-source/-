# 功能计划示例

> 目标文件：`docs/plans/2026-05-21-user-list-plan.md`

> 计划状态：planned
> 最近审阅：2026-05-21
> 来源：`docs/requirements/2026-05-21-user-management.md`
> 审计：必需

## 当前基线

- 当前 live 状态
- 当前差距

## 目标

- 落地一个诚实的结果面

## 非目标

- 本计划刻意排除的工作

## 任务路线

- 类型：`implementation-only change`
- Owner 文档：`docs/design/app-overview.md`
- 技能选择依据：`plan-audit-prompt.md` 和 `closure-audit-prompt.md` 作为评审方法；交付阶段使用 `Skill: none`

## 基础设施与配置前置条件

- 除了已有基线外，没有其他基础设施前置条件

## 执行计划

### 阶段 1 - 落地核心切片

状态：planned
目标：`apps/...`、`packages/...`、`docs/...`
技能：`none`

- 项目类型：`Add | Proof`
- 前置条件：无

- [ ] 实现核心行为
  - 技能：`none`
- [ ] 补充或更新证明
  - 技能：`none`

退出标准：

- [ ] 行为已落地
- [ ] 文档已更新，或者明确为 `No owner-doc update required`
- [ ] `docs/logs/` 已更新

## 计划审计

- 状态：pending
- 评审者 / 代理：`<independent reviewer or subagent>`
- 证据：`<audit file or task id>`

## 收尾门槛

- [ ] 范围内行为已完成
- [ ] 相关文档已对齐
- [ ] 验证已运行（请注明哪些命令；如果是视觉/UX 域，请按需定制）
- [ ] 没有把范围内项目降级为延后/后续
- [ ] 在实现前已通过计划审计
- [ ] 文本一致性已验证：状态、阶段、门槛和日志彼此一致
- [ ] 收尾审计是独立的
- [ ] 收尾证据存在于文件中

## 已裁定但延后

### <item name>

- 分类：`watch-only residual`
- 为什么不阻塞收尾：<reason>
- 需要后继项：`no`

## 收尾

状态说明：只有在计划审计和收尾审计都通过后才算完成。

收尾审计证据：

- 评审者 / 代理：`<independent reviewer or subagent>`
- 证据：`<task id / log link / audit file>`

后续：

- <non-blocking follow-up items only>
