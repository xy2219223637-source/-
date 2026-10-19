# 现有测试 PostgreSQL 的隔离事务验证提案

- task_id: model-pricing-mvp-20261019-pg-isolated
- 时间：2026-10-19T17:55:00+08:00
- 状态：用户于2026-10-19T17:55 +08:00明确回复“允许隔离 schema 定向验证”；已执行首轮，记录如下。
- 来源：票01/02/06的事务并发、迁移与真实阶段门禁；用户规则15/16要求数据库写入单独授权。现有测试库只读校验为schema v2，见read-only-preflight.json脱敏环境引用d06030ee0880a2db13dbabbb32c24354388bfb885bdd1b6fdee9526557db9e2d。
- 推荐：在现有测试数据库内创建名称以test_billing_开头、附随机UUID的独占临时schema，设置连接search_path到该schema，仅创建合成租户/成员/费率/账务记录，执行定向API/事务/迁移测试；结束只删除本轮确认归属的schema。不新增数据库，不修改public或现有业务schema，不启动业务服务。
- 替代：仅继续临时SQLite验证。代价是不能证明PostgreSQL锁/DDL/事务行为，MOCK完整门禁不能据此声称已通过，REAL继续关闭。
- 范围：现有test_billing_postgres.py的随机schema机制及新增模型测试对应包装；运行器显式隔离配置、关闭初始化/恢复/workers，仅从现有安全配置把连接值传入子进程环境，不输出连接字符串。不使用用户GitHub密码。
- 预检查：确认PostgreSQL及目标脱敏引用一致；检查schema不存在、CREATE SCHEMA权限、search_path、锁/语句timeout。若权限不足，不更改数据库角色或权限。
- 风险：短暂DDL/连接/CPU负载及合成测试记录；故障可能遗留只属于本轮的schema。测试包括唯一约束、并发保存/消费、失败回滚与不可变保护；不接厂商或真实对象存储。
- 备份：原业务schema完全不写，不需要覆盖式备份；新schema创建前为空。测试创建的冻结v2合成历史在升级前保留fixture及行值摘要，事务失败自动回滚；不会把合成历史称为业务备份恢复演练。
- 执行路径：授权→更新此标志和run_id→预检查→创建独占schema→写合成fixture→定向测试→验证账务/约束→按schema归属清理→验证不存在→落盘脱敏报告。
- 恢复入口：测试前在外部run-state.json记录schema名及状态；中断后先读取该记录，再查询pg_namespace，仅允许操作记录中精确名称且匹配test_billing_+UUID的schema，不按前缀批量删除。
- 回退：失败保留脱敏日志；先处置本轮连接，再使用SQLAlchemy DropSchema(精确本轮schema,cascade=True)清理，清理前复核目标库及归属；删除后确认pg_namespace无该精确名称。不能确认归属就停止清理并报告。
- 自动重启/回退：不重启任何服务；连接退出回滚未提交事务；正常fixture cleanup清理本轮schema，异常清理失败保留恢复标志，不扩大范围。
- 验证方式：定向测试无失败、必测不跳过；原schema版本仍[2]且checksum/保护不变；临时schema已清理；真实厂商/存储调用为0。此授权不包含运行schema v3迁移、真实费率/账务写入、真实模型调用或进程操作。
- 首轮pg-01-20261019-1758：16项中5项历史事务检查通过、11项新模型检查在初始化失败；完整日志显示外部运行器读取原配置后缓存了旧app包，缺新模块，并非通过结果。16个独占schema已全部清理，业务schema版本前后[2]。已修正运行器包加载边界，准备新run_id重跑；原失败日志与run-state保留。
- 重跑pg-01-20261019-1800：基于集成b6a1235，20项全部通过、无跳过，42.824秒；20个独占schema全部清理，业务schema版本前后[2]。结果包含15项当前费率与registry/API测试、4项旧账务并发/保护、1项审计故障回滚。外部证据：D:/原力2222222222/model-pricing-work/pg-01-20261019-1800/run-state.json及同名.log。没有对运行业务schema应用v3迁移；新schema初始建v3通过不替代v2→v3 PostgreSQL迁移演练。18:02只读再次核实业务schema v2/checksum/保护仍通过。
