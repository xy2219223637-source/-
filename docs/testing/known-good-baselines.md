# 已知良好基线

## 用途

记录最近一次已验证的项目状态，这样后续 AI 会话就能判断某个失败是新问题还是既有问题。

这个文件保持轻量。只记录有意义的基线，不要记录每一次本地命令。

## 基线记录

| 日期 | 来源 | Git 状态 | 范围 | 通过的命令 | 已知失败 | 证据 | 备注 |
| ---- | ---- | -------- | ---- | ---------- | -------- | ---- | ---- |
| `<YYYY-MM-DD>` | `<local | CI>` | `<commit | dirty working tree>` | `<full / package / feature>` | `<commands>` | `<none | failing commands>` | `<log/test link>` | `<notes>` |

## 何时更新

在以下情况更新本文件：

- 经过一次有意义的变更后，完整的 typecheck / build / lint / test 验证通过
- 之前失败的命令变绿了，且值得被记住
- 团队有意接受了一个已知失败命令，并把它记录为已知失败而不是已通过命令

## 规则

不要在命令实际上没有在当前仓库状态下运行过时，就把它标记为通过。

`Commands Passed` 里只能放通过的命令。已接受的失败要放在 `Known Failures` 里，并附上原因和证据。

如果基线来自 dirty working tree，就必须在 `Notes` 里写出变更文件，或者链接到一条说明这些文件的带日期日志/测试记录。

`full` 表示 `docs/context/project-context.md` 里配置的所有真实验证命令。明确标记为 `none` 的命令不算在内，但应注明。
