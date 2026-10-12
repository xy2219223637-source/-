# 共享数据库后台任务隔离

- 保持生产服务身份为 `production`, artinjecter 服务身份为 `artinjecter`; 禁止交换服务身份.
- 使用各部署目录的独立代码镜像, 后台进程与前后端一起运行本服务分支代码.
- 保持任务表 `deployment_service` 归属字段; 历史任务默认归属 `production`.
- 只领取和恢复本服务任务; 已结束任务重新提交时在同一事务内记录发起服务; 禁止接管另一服务活动任务.
- 保持 Redis 业务缓存共享; 使用 `deployment:<service>:` 隔离队列唤醒与任务锁.
- 使用 `bash /opt/enrui-ai-commerce-agent/deploy.sh production` 或 `artinjecter` 独立更新与选择分支.
- 使用 `--config-only` 保留当前镜像并更新配置; 部署前验证 runtime, 数据库字段及 Redis 连接.
- 禁止部署脚本调用全局 `init_deployment.py` 或启动全局 `init_db`.
- 审核变更过的队列实现与启动流程, 再更新 runtime 中的已验证源码指纹; 禁止直接添加未审核指纹.
- 单独评估分支的数据库结构变化并执行显式迁移, 再部署; 保持另一分支的字段兼容性.
- 通过 `/api/health` 检查本服务身份及后台进程存活; 千川进程受既有授权配置开关约束.
- 查看部署日志与数据库任务归属, 验证恢复操作未改变另一服务任务.
- 保持 `test.waynepartners.xyz` 配置独立; 本修正不适用于其独立数据库.
