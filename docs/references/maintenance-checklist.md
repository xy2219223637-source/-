# 文档维护清单

## 用途

在变更落地后使用本文件，检查仓库记忆是否保持同步。

## 每次非平凡代码变更后都要检查

1. `docs/architecture/` 中相关的 owner 文档
2. `docs/logs/` 中的日常日志
3. 任何受影响的需求、计划、bug 笔记或测试记录
4. 当建立了有意义的完整验证基线时，检查 `docs/testing/known-good-baselines.md`

## 变更触发器

### 架构或边界变更

检查：

- `docs/architecture/system-baseline.md`
- `docs/architecture/module-boundaries.md`
- 如果路由发生变化，再检查 `docs/index.md`

### 产品意图或范围变更

检查：

- 如果源材料本身变化了，则查看 `docs/input/` 中相关文件
- 如果需求解释变化了，则查看 `docs/discussions/` 中相关文件
- `docs/requirements/` 中相关文件
- 如果变更影响长期方向，再检查 `docs/architecture/project-vision.md`

### 应用层功能或流程变更

检查：

- `docs/design/` 中最相关的文件
- 如果用户可见范围变化了，检查 `docs/requirements/`
- 如果需要手动/探索性证明，检查 `docs/testing/`

### 非平凡实现切片

检查：

- 活动计划 `docs/plans/`
- 如果切片包含审计，再检查 `docs/audits/`
- `docs/logs/YYYY/MM-DD.md`
- 如果需要探索性/人工证明，检查 `docs/testing/`

对于已创建的计划，计划审计在实现前必需，收尾审计在完成前必需。

### 细微回归或根因发现

检查：

- `docs/bugs/`
- 如果问题暴露出流程或需求缺口，再检查 `docs/retrospectives/`
- 任何受影响的 owner 文档

## 验证基线

使用 `docs/context/project-context.md` 里的真实项目命令。

如果该文件还留着占位符，先补齐，再宣称验证成功。
