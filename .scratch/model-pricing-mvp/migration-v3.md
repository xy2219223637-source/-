# v3 模型当前费率迁移审阅与恢复

task_id: model-pricing-mvp-20261019-01-migration

此文件是执行草案，**未授权、未应用目标数据库迁移**。代码入口沿用 `backend/scripts/migrate_billing_mvp.py`，正式函数为 `app.services.billing.migration.migrate_billing`。服务启动只验证结构，不自动迁移。

影响：新增 `billing_model_prices`；订单新增 `model_price_id`、`metering_json`、`execution_mode`，允许二选一价格来源；重建订单快照保护并增加模型身份不可变保护；schema 版本从 2 升至 3。旧价格、订单、流水、审计、幂等数据不删除、不改写金额、不回填虚假的模型身份。历史订单标记 LEGACY。价格表身份不可修改或删除，当前配置更新和审计必须同事务提交。

预检查：确认目标库与连接进程实际环境；核对 schema=2、旧 checksum、所有旧触发器/索引/约束，记录旧表行数及订单/流水抽样摘要；核对数据库写权限、磁盘容量、外键完整性、数据库锁定等待和运维窗口。主线程只读报告 `D:/原力2222222222/model-pricing-work/read-only-preflight.json` 记录配置对应库旧版校验成功；该报告不能证明运行进程已经加载同一配置。

备份：独立 `pg_dump --format=custom --file=<受控备份文件> <已确认测试库>`；凭据仅通过现有安全配置传递，不写入命令日志。保留备份清单、大小及SHA-256。先在**单独授权的恢复目标**用 `pg_restore --dbname=<恢复目标> --no-owner --no-acl <备份文件>` 验证，禁止覆盖源库。验证 schema=2、旧checksum、触发器、行数、订单/流水金额及账本平衡；记录恢复耗时和检查结果。

执行前：取得目标环境写入授权并更新危险操作标志，记录秒级时间、最近 checkpoint、备份位置、最后安全状态及恢复入口；设置有界 lock_timeout/statement_timeout，关闭后台workers、恢复与自动初始化。迁移函数使用事务和专用 PostgreSQL advisory lock，检查旧结构后再执行 DDL，最后写 v3 checksum 并严格验证。同事务失败回滚；失败保留脱敏错误证据，不自动重启或重试写库。

执行命令草案（**本票未执行**）：在已核实环境运行 `python -m scripts.migrate_billing_mvp --backup-confirmed`。该参数只表示执行者已独立完成备份，不能代替用户授权与恢复演练。

迁移后：验证schema=3和checksum、价格来源XOR、模型身份不可变、订单快照与终态不可变、历史只读查询、账本平衡；用有界专用数据验证新管理配置。价格写入、账务验证、重启仍分别遵守授权边界。

回退：迁移事务未提交时自动rollback。提交后优先使用**认识v3且关闭新消费入口**的安全应用构建，保留所有新订单/流水；旧v2构建不能直接启动v3 schema。仅在确认没有任何新账务且不会抹除已发生交易、并取得恢复授权后，才能用已验证完整备份恢复至单独确认目标。禁止删除新消费记录、降checksum、放松历史触发器或直接强制降schema。

实际离线验证：临时SQLite冻结基线v2结构与合成历史升级至v3，旧订单、价格引用、用量和流水逐值一致，旧订单查询可读；v1路径亦通过。SQLite重建订单表先defer外键，复制后执行完整foreign_key_check，再恢复立即外键检查。SQLite结果不证明PostgreSQL DDL、锁竞争、备份恢复或目标环境已通过；这些项目仍待对应环境授权。
