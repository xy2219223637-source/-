# 待办池

## 用途

用这个文件列出 AI 可以检查或执行的候选工作。

待办池不能替代需求、owner 文档或计划。它只负责帮助选择下一段切片。

## 工作项

| 优先级 | 项目 | 需求 | Owner 文档 | 计划 | 状态 | AI 自治级别 | 阻塞项 | 最近检查 |
| ------ | ---- | ---- | ---------- | ---- | ---- | ----------- | ------ | -------- |
| P0 | `<first slice>` | `docs/requirements/<path>` | `docs/design/<path>` | `docs/plans/<path-or-none>` | `needs-requirement` | `blocked` | `template placeholders not replaced` | `<YYYY-MM-DD>` |

## 就绪性不变量

`ready` 表示以下条件全部成立：

- 需求路径存在，并且有可测试的验收标准
- owner 文档路径存在，并且对这个切片来说不是已知过时
- `docs/context/project-context.md` 中的验证命令是真实命令
- 没有阻塞性的开放问题，或者这些问题已经明确标记为非阻塞
- `docs/context/ai-autonomy-policy.md` 中已配置受保护区域
- 已检查规划触发条件

`Plan: none` 只有在项目确实符合 `docs/plans/00-plan-authoring-and-execution-guide.md` 中的免计划路径时才有效。如果需要计划，则应把 AI 自治级别设为 `plan-first`，直到计划审计通过为止。

代理可以在有证据的前提下，把过时的行从 `ready` 降级为 `needs-*` 或 `blocked`。代理不得在没有人工确认或人工批准的 owner 文档证据的情况下把行升级为 `ready`、把自治级别改成 `implement`，或清除阻塞项。

设置完成后的 ready 示例：

```md
| P0 | User Management first slice | `docs/requirements/2026-05-21-user-management.md` | `docs/design/app-overview.md` | `docs/plans/2026-05-21-user-management-plan.md` | `ready` | `plan-first` | `none` | `2026-05-21` |
```

## 状态值

- `idea` - 还不能实施
- `needs-requirement` - 有原始输入，但还没有可实施的需求
- `needs-design` - 有需求，但缺少或已过时的 owner 文档
- `ready` - AI 可以按自治标签继续
- `in-progress` - 正在实施或规划中
- `blocked` - 必须等阻塞项解决后才能继续
- `done` - 已完成并已验证

## AI 自治值

使用 `docs/context/ai-autonomy-policy.md` 中的值：

- `implement`
- `plan-first`
- `ask-first`
- `research-only`
- `blocked`

## 选择规则

当被要求在没有指定任务的情况下继续时，选择优先级最高、`AI 自治级别` 为 `implement` 且 `阻塞项` 为 `none` 的 `ready` 项。

在实现之前，确认链接的需求、owner 文档、计划字段、自治策略和规划触发条件仍然有效。不要仅凭聊天内容推断就绪状态。

如果表格已经过时，请降级该行，或者先询问后再实现。
