# 项目上下文

## 用途

把这个文件保持为 AI 代理在开展有价值工作前所需的最短当前快照。

请就地更新，不要创建带日期的副本。

## 项目信息

- 项目名称：
- 产品类型：
- 主要用户：
- 当前里程碑：
- 文档新鲜度：`<fresh | partially stale | stale | unknown>`

## 当前工作

- 当前需求：`docs/requirements/<path-or-none>`
- 当前 owner 文档：`docs/design/<path>` 或 `docs/architecture/<path>`
- 当前计划：`docs/plans/<path-or-none>`
- 当前待办项：`docs/backlog/README.md#<item-or-none>`
- AI 自治级别：`<implement | plan-first | ask-first | research-only | blocked>`
- 当前阻塞项：`<none | describe>`

规则：

- 如果当前需求是 `none`，代理可以帮助创建或澄清需求与上下文，但不得实现产品行为。
- 如果 AI 自治级别不是 `implement`，在修改产品行为前必须遵循 `docs/context/ai-autonomy-policy.md`。
- 如果文档新鲜度是 `stale` 或 `unknown`，代理可以研究、审计并起草对齐文档，但在重新建立基线或由人工确认预期行为之前，不得实现产品行为。
- 如果文档新鲜度是 `partially stale`，代理只可实现那些已经验证需求、owner 文档、codebase map 路由以及受影响代码区域都新鲜的切片；否则应把该切片视为 `plan-first` 或 `research-only`。

## 当前技术基线

- 前端栈：
- 后端栈：
- 数据库/模型来源：

## 验证命令

在开始实现工作之前，把所有占位符替换掉。

| 用途 | 命令 |
| ---- | ---- |
| 安装依赖 | `<fill real command>` |
| 本地运行应用 | `<fill real command>` |
| 类型检查 / 编译检查 | `<fill real command or none>` |
| 构建 | `<fill real command or none>` |
| Lint / 静态检查 | `<fill real command or none>` |
| 单元测试 | `<fill real command or none>` |
| E2E / 集成测试 | `<fill real command or none>` |

## 当前启用的可选层

只勾选这个项目实际维护的可选层。

- [ ] `docs/discussions/`
- [ ] `docs/audits/`
- [ ] `docs/testing/`
- [ ] `docs/skills/`
- [ ] `docs/analysis/`
- [ ] `docs/retrospectives/`
- [ ] `docs/lessons/`

## AI 必须停止的条件

当出现以下情况时，AI 必须停下并等待人工输入再继续：

- 验证命令全部还是占位符，且无法从项目中推断出来
- 任何改动涉及支付或数据删除路径，并且没有现有测试覆盖，也没有 owner 文档描述预期行为

这些都是项目级硬停条件，此外还要遵守 `AGENTS.md`、`docs/context/ai-autonomy-policy.md`、事实来源冲突规则以及强制的计划/收尾审计规则。

对于不会影响用户可见行为、契约、受保护区域或收尾证据的歧义，应把假设写进相关文档，然后按照自治策略继续。把不确定假设明确标出来，方便后续人工复核。

## 给 AI 代理的备注

- 如果这个文件是空的或已过时，请先请求或创建一次上下文更新，再进行大规模实现工作。
- AI 可以基于仓库中的实时证据修正事实上下文，但不能在没有人工确认的情况下放宽自治、移除阻塞、把过时文档标成新鲜，或降低受保护区域级别。
- 不要只根据聊天内容推断当前里程碑或当前计划。
- 当验证命令仍包含 `<fill real command>` 占位符时，不要报告验证成功。
