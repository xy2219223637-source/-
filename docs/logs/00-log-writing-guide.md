# 开发日志写作指南

开发日志条目按日期组织，每天一个文件。

## 目的

每份每日日志都记录了有关以下内容的简短日期注释：

- 添加或更新了哪些文档
- 做出了什么设计决策
- 下一步计划做什么工作
- 小上下文有助于以后记住，但不属于正式的架构文档

## 规则

- **每天一个文件** - 同一天的所有工作都会进入同一个文件
- **追加新条目** - 在文件顶部添加新的 `### YYYY-MM-DD` 部分（按时间顺序倒序）
- **保持条目简短** - 更喜欢要点、主要文档或代码路径的链接
- **不是事实来源** - 这是轻量级上下文，而不是规范架构
- **链接到真实文档** - 引用设计决策时，链接到架构文档或代码路径
- **将日志文件视为仅附加历史记录** - 文件长度本身并不是缺陷；不要仅仅因为每日日志超出了活动文档大小准则而标记它们

## 路径约定

- `docs/logs/{year}/{month}-{day}.md`

## 参赛作品格式```markdown
# Development Log - YYYY-MM-DD

### YYYY-MM-DD

- Brief description of what happened.
- Link to doc or code path: `docs/architecture/flux-core.md` or `packages/bar/src/baz.ts:42`
- Key decision: ...
- Next step: ...
```
## 添加新条目

1、打开`docs/logs/{year}/{month}-{day}.md`（不存在则创建）
2. 在顶部任何现有条目之前添加 `### YYYY-MM-DD` 部分
3. 编写新项目符号
4. 如果当天已有较早的条目，请在空行分隔符后附加