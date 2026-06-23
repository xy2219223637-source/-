# 代码库地图

## 用途

本文件为 AI 代理提供一个精简的活仓库地图，避免它们反复搜索导入和目录来重新发现结构。

保持它足够新鲜，以便路由常见工作。不要把它写成完整架构文档。

## 入口点

复制模板后请替换占位符。

| 区域 | 路径 | 备注 | 最近验证 | 置信度 |
| ---- | ---- | ---- | -------- | ------ |
| 前端应用 | `<path>` | `<notes>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 后端应用 | `<path>` | `<notes>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 共享代码 | `<path>` | `<notes>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 测试 | `<path>` | `<notes>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 配置 | `<path>` | `<notes>` | `<YYYY-MM-DD>` | `<high | medium | low>` |

## 常见变更路径

| 任务类型 | 从这里开始 | 再检查 | 验证 | 最近验证 | 置信度 |
| -------- | ---------- | ------ | ---- | -------- | ------ |
| 添加页面/界面 | `<path>` | `<path>` | `<command>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 添加 API/处理器 | `<path>` | `<path>` | `<command>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 变更模型/schema | `<path>` | `<path>` | `<command>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 变更权限 | `<path>` | `<path>` | `<command>` | `<YYYY-MM-DD>` | `<high | medium | low>` |
| 修复 UI 行为 | `<path>` | `<path>` | `<command>` | `<YYYY-MM-DD>` | `<high | medium | low>` |

## 大型或脆弱文件

列出代理应谨慎处理的文件，因为它们很大、很核心、是生成产物，或者容易被错误编辑。

| 路径 | 风险 | 推荐做法 |
| ---- | ---- | -------- |
| `<path>` | `<risk>` | `<approach>` |

## 项目特定搜索提示

- 使用文件模式：`<example glob>`
- 使用内容锚点：`<important function/type/component names>`
- 避免编辑生成文件：`<paths or none>`

## 更新规则

当某次变更创建了新的主要入口、移动了常用代码、增加了新的测试位置，或者反复让代理重新发现同一路径时，更新本文件。

如果列表中的路径缺失、占位符仍在，或者 live import 与这张地图冲突，不要把它当作权威。先用 live repo 验证，然后更新地图，或在实现前把该行标为低置信度。

如果 `Last Verified` 对项目节奏来说已经太久、早于重大结构变化，或者任务触及了列表中某条路由的边界，请先验证 live repo 再依赖该行。低置信度行在完成 live 验证后不会阻止低风险工作，但受保护区域、迁移或跨模块工作应先更新该行再实现。
