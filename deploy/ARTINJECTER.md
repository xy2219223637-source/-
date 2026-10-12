# artinjecter 部署

- 执行 `bash /opt/enrui-ai-commerce-agent/deploy.sh artinjecter`, 选择远端分支并部署.
- 非交互执行 `DEPLOY_BRANCH=main bash /opt/enrui-ai-commerce-agent/deploy.sh artinjecter`.
- 执行 `bash /opt/enrui-ai-commerce-agent/deploy.sh artinjecter --config-only`, 保留当前镜像并更新配置.
- 使用独立 Compose 项目 `enrui-artinjecter`, 前端 `127.0.0.1:8280`, 后端 `127.0.0.1:8200`.
- 保持业务数据库与生产一致, 独立维护 `shared/.env`; 保持 `DEPLOYMENT_SERVICE_ID=artinjecter`.
- 使用本服务分支的后台进程领取本服务任务; Redis 队列唤醒与任务锁按服务隔离.
- 保持 `shared/shared-database-runtime.py` 的只读挂载; 部署前执行 runtime `check`, 不执行全局任务恢复或数据库初始化.
- 保留历史任务的 `production` 归属; 已结束任务从本服务重新发起时转为本服务归属; 禁止转移另一服务活动任务.
- 查看 `/api/health` 的 `deployment_service` 与 `background_workers`, 确认生产队列和视频分析进程存活.
- 千川进程只在原千川配置有效时启动; 修复千川配置后重新部署本服务.
- 若分支改变队列实现, 模型结构或启动流程, 先审核隔离适配与共享数据库迁移, 再部署; 禁止绕过兼容性检查.
- 使用独立仓库, 镜像标签, 日志, 部署锁及容器; 以只读方式复用 Whisper 模型卷.
- 保持域名 `artinjecter.waynepartners.xyz` 指向 wuyang, 使用通用 Certbot 定时器续期证书.
