# 讨论写作指南

## 用途

使用 `docs/discussions/` 来记录多轮澄清，处理含糊的工作。

## 适用场景

- PM 只能提供部分确认
- 原型含义不清
- 仍然存在多种合理的需求解释
- 否则开发者会直接从原始文件里推断领域规则

## 应包含内容

- 正在讨论的源文件
- 未解决的问题
- 候选解释
- 已确认的决定
- 阻塞实现的未决项

## 规则

讨论文件用于澄清，不是最终事实。把已落定的结论移到 `docs/requirements/`、`docs/design/` 或 `docs/architecture/`。

## 文件名建议

优先使用带日期的文件名：

- `docs/discussions/YYYY-MM-DD-topic.md`
