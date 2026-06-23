# 设计文档索引

## 用途

`docs/design/` 存放稳定的应用层 owner 文档。

本目录用于：

- 产品功能基线
- 页面和流程行为
- 角色与权限
- 应用壳行为

跨切面的技术结构请使用 `docs/architecture/`。

## 范围边界

- `docs/requirements/` 负责当前切片“应该构建什么”
- `docs/design/` 负责该切片被接受后的稳定应用层基线
- `docs/architecture/` 负责技术设计和跨功能结构

当某个功能同时依赖业务设计和技术设计时，请把两个关注点分开放在不同文件里，并在必要时互相引用。

## 起始文件

- `app-overview.md`
- `feature-inventory.md`
- `roles-and-permissions.md`
