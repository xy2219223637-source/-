# PostgreSQL v2 → v3 合成迁移验证

- task_id: model-pricing-mvp-20261019-pg-migration
- 范围：仅用户批准的 `test_billing_` + UUID 本轮独占 schema。原 checkout、原配置、现有业务 schema、业务服务不修改。
- 成功批次：`pg-migration-20261019-100548`（UTC；新加坡时间 2026-10-19 18:05:48 至 18:06:01）。集成应用代码由主线程固定为 f1e9f95 的票01迁移实现；脚本未修改应用代码。
- 实际执行：`pg-migration-run.py`，退出码 0，22 项定向检查全部通过。无真实厂商、对象存储或后台服务调用。
- 合成旧数据由原 checkout 旧 v2 `OrderTests`/`BillingFixture` 创建：5 订单（已结算固定/用量、已取消、未执行预占、执行不明预占）、2 旧费率、3 流水、17 审计、1 幂等、1 账户、2 成员额度。旧 v2 自身 verify 通过。
- 升级前行值保存在成功批次 `v2-synthetic-backup.json`；SHA-256 为 `2f011b60a1165b4cd329be04c90a4bfeab802732bf014c53767c5bac1269477b`。仅为合成 fixture 保护，不是业务数据库备份。

## 实际通过的行为

1. `protect_model_identity` 注入异常发生在新版表/字段及订单触发器 DDL 之后；迁移事务完整回滚。旧数据、v2 结构、checksum、触发器均恢复，新模型表不存在。
2. 成功升级返回 `upgraded` / version 3；除版本记录外，所有旧表的每个旧列行值逐值一致。订单新增字段为 LEGACY / NULL / NULL，没有伪造模型身份。
3. 新版完整 verify 通过；重复迁移返回 already_current，表行值无变化。
4. 新旧价格来源均空、同时存在都由数据库 CHECK 拒绝（23514）；仅模型来源接受，在事务中回滚测试行。
5. 已结算/取消终态、未决订单快照/execution_mode、订单删除、旧价格、流水、审计、幂等、模型身份改写/删除均被原生触发器拒绝（P0001）。
6. 人为损坏 checksum 被完整 verify 拒绝，事务回滚后 verify 恢复。
7. 新版服务读取 5 个历史订单；旧待执行预占可继续结算，前后账本与成员额度核对均平衡。
8. 成功批次结束精确清理 schema；`pg-migration-postcheck.json` 再次使用原 checkout 旧 v2 verify 只读核验现有业务库通过，版本仍 2 / checksum `abe98f12938384f7b4a52bcdcd58fa610d00573ed87ce968dfb2d6f17f66d79e`。全部四轮登记名称逐一查询均不存在。

## 保留的失败与保护证据

| 批次（UTC） | 失败原因 | 影响与最终状态 |
| --- | --- | --- |
| 100506 | 外部 runner 用 SQLite memory bootstrap，现有 database.py 的 max_overflow 对 SingletonThreadPool 不适用 | 发生于加载迁移模块，尚未连接测试库创建 schema；工具输出保留完整 traceback，登记 schema 后查不存在 |
| 100514 | 子进程 stderr 的 Windows 编码被 UTF-8 解码失败，runner 再拼接 None 产生 TypeError | 父进程完整失败上下文在 failure.log；原始子进程 stderr 因解码失败未完整保存，不冒称已有完整子进程日志。本轮 schema 已精确清理且后查不存在 |
| 100532 | 子进程继承父进程 bootstrap DATABASE_URL，PG 断言拒绝 SQLite | seed.log / failure.log 完整保留；PG 断言在建 fixture 前中止。空独占 schema 精确清理且后查不存在 |
| 100548 | 修正外部 bootstrap、UTF-8 子进程、移除继承 DATABASE_URL 后重跑 | 22 项通过，schema 已精确清理 |

修正只涉及外部 runner，没有放宽应用测试、修改应用、扩大写库范围、安装依赖或重启服务。

## 限制

此结果证明 PostgreSQL 合成独占 schema 的迁移、原子回滚与关键保护，不证明现有业务库已升级、生产迁移、`pg_dump`/`pg_restore` 业务备份恢复演练或真实模型门禁。当前 PATH 未发现 pg_dump，未安装工具。业务库前后版本与 checksum 一致且旧 verify 通过；没有对业务行做逐行导出或写入。
