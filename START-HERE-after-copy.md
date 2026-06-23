# 复制后从这里开始

把这个清单在复制模板到新项目后立刻执行。

在让 AI 实现功能之前，先完成这些。

## 第一次让 AI 编码之前的必做项

- [ ] 替换 `<project-name>` 和其他占位符。
- [ ] 在 `docs/context/project-context.md` 中填写真实的项目身份、当前工作和验证命令。
- [ ] 在 `docs/context/ai-autonomy-policy.md` 中填写真实的受保护区域和 AI 自治默认值。
- [ ] 在 `docs/context/codebase-map.md` 中填写真实入口点、常见变更路径和脆弱文件。
- [ ] 在 `docs/backlog/README.md` 中填入第一批已就绪或已阻塞的工作项。
- [ ] 在 `docs/context/project-context.md` 中把 `Documentation freshness` 设为 `fresh`、`partially stale`、`stale` 或 `unknown`。
- [ ] 在 `docs/context/ai-autonomy-policy.md` 中设置评审可用性。
- [ ] 在 `docs/context/project-context.md` 中选择第一份当前需求文件并记录。
- [ ] 确保当前需求包含具体的验收标准。
- [ ] 在 `docs/context/project-context.md` 中选择第一份当前 owner 文档并记录。
- [ ] 确保验证命令对这个仓库来说是真实命令。

如果只是一个本地低风险编辑，不必为了把 backlog 或 codebase map 写得很漂亮而卡住，只要当前需求 / owner 文档含义清晰，并且验证命令是真实的即可。受保护区域、过时文档、缺少验证或用户可见行为不清楚，仍然会阻止编码。

## 逐步补全

这些内容在需要时尽快补齐，不必为了第一小片功能就先写得很完美。

- [ ] 在 `docs/architecture/project-vision.md` 中写长期产品方向和非目标。
- [ ] 在 `docs/architecture/system-baseline.md` 中写真实技术栈和模型/数据库来源。
- [ ] 在 `docs/design/app-overview.md` 中写当前应用界面、角色和核心工作流。
- [ ] 在 `docs/requirements/product-scope.md` 和 `docs/requirements/mvp.md` 中写当前里程碑范围。
- [ ] 当真实命令通过后，在 `docs/testing/known-good-baselines.md` 中添加第一条已知良好验证记录。
- [ ] 通过勾选 `docs/context/project-context.md` 中的复选框，决定哪些可选层是启用的。
- [ ] 删除或忽略暂时不会维护的可选目录。

## 编码前最低要求

- [ ] 当前需求有具体的验收标准。
- [ ] 当前 owner 文档已列在 `docs/context/project-context.md`。
- [ ] AI 自治级别是 `implement`，或者工作明确是 `plan-first`，且只会写计划。
- [ ] 受保护区域占位符已替换成真实条目或明确的 `none`。
- [ ] 除非第一项工作是研究或基线对齐，否则文档新鲜度不是 `stale` 或 `unknown`。
- [ ] 验证命令是真实命令。
- [ ] 原始输入、需求、owner 文档和 live code 之间的任何冲突都已解决，或者明确阻塞。

## 不要开始，如果

- `docs/context/project-context.md` 还是空的。
- 验证命令还是占位符。
- 受保护区域占位符还在，而且任务会触及认证、权限、支付、数据删除、模型/schema、部署或外部集成。
- 当前需求是 `none`。
- AI 自治是 `ask-first`、`research-only` 或 `blocked`，而人工决策尚未改变它。
- 文档新鲜度是 `stale` 或 `unknown`，并且任务会改变产品行为而不是做审计或基线对齐。
- 当前需求不够清楚，以至于实现时必须猜用户可见行为。
- 任务会改变数据库/API/认证/集成行为，但没有识别出 owner 文档或模型来源。
