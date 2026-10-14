# 测试环境后台任务

- 保持 `RUN_BACKGROUND_WORKERS=true`, `RUN_STARTUP_RECOVERY=true` 和 `RUN_DATABASE_INIT=false`.
- 保持 `VIDEO_REPAIR_V4_ENABLED=true` 和 `VIDEO_REPAIR_ASYNC_WORKER_ENABLED=true`, 启用当前分支的视频修复队列.
- 保持独立测试数据库, Redis 和 OSS 配置; 部署前执行 runtime `check` 验证资源身份.
- 使用 `bash /opt/enrui-ai-commerce-agent/deploy.sh test` 选择分支更新测试环境.
- 保持服务器 runtime 的健康检查适配; 通过 `/api/health` 确认 `production_queue`, `video_analysis` 和 `video_repair` 均为 `true`.
- 将配置要求纳入测试部署脚本, 拒绝后台任务或启动恢复被关闭的常规部署.
- 保持千川当前禁用配置; 使用千川前单独配置测试授权和外部接口.
- 变更分支后台启动方式时审核 runtime 适配; 禁止绕过独立资源与启动检查.
- 更新 runtime 发布目录时同步 `source-manifest.json`, 保留旧目录以便恢复.
