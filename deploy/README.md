# 独立部署与后台任务

- 使用仓库根目录 `deploy.sh` 作为统一入口, 选择 `production`, `artinjecter` 或 `test`.
- 使用 `deploy/deploy-production.sh` 维护生产与 artinjecter 的构建, 兼容性检查和独立更新.
- 使用 `deploy/shared-runtime/task_isolation_runtime.py` 保存当前已验证的共享数据库任务隔离实现及 `migrate/check/serve` 命令.
- 使用 `deploy/test-runtime/` 保存测试资源隔离检查, 后台启动控制与线程健康检查.
- 使用 `deploy/deploy-test.sh` 部署测试分支; 保持后台任务及启动恢复开启.
- 保持已有服务器资源清单, 应用环境文件, 迁移账号, 固定基础镜像和独立测试基础服务配置; 禁止将其秘密写入仓库.

## 安装到已有服务器

- 先使用 `bash deploy/install-runtime.sh production --dry-run` 确认安装目标.
- 按需执行 `bash deploy/install-runtime.sh production`, `artinjecter` 或 `test`, 将版本化部署文件安装到对应服务目录.
- 保留安装器创建的配置备份; 安装器不修改 `.env`, 不执行数据库迁移, 不重启容器.
- 安装生产部署脚本后再安装 artinjecter 包装脚本, 保持其共用部署入口可用.
- 从当前服务的 Compose 配置运行 runtime `check`, 通过后再重建对应服务容器.
- 首次使用共享任务归属字段时, 先备份业务数据库并显式执行共享 runtime `migrate`, 再执行 `check`; 迁移给历史任务设置 `production` 归属.
- 保持测试 `.env` 的 `RUN_DATABASE_INIT=false`, `RUN_BACKGROUND_WORKERS=true`, `RUN_STARTUP_RECOVERY=true`; 使用视频修复时同时设置两个视频修复开关为 `true`.
- 使用 `base-images.example` 的已固定公共镜像配置创建各服务 `shared/base-images.env`, 或保留服务器现有固定配置.
- 查看 `/api/health` 的服务身份及后台线程状态; 千川线程只在授权配置有效时启动.

## 验证

- 使用已有后台依赖镜像运行 `BACKEND_TEST_IMAGE=<image> bash deploy/tests/run-runtime-tests.sh`.
- 使用仓库中的当前分支业务代码, 在独立 PostgreSQL 容器验证迁移, 任务归属, 跨服务恢复和任务领取; 在临时 SQLite 验证测试后台启动与健康检查.
- 保持测试容器与生产网络隔离, 禁止读取生产 `.env`; 测试替换生成和分析的外部调用, 不产生第三方生成或上传请求.
- 在支持视频修复的测试分支另行运行 `deploy/tests/test_test_workers.py`, 验证该分支的视频修复后台; 主分支无此模型时只验证其已有后台.

## 维护范围

- 本次版本化保留已上线的部署层实现, 不改变分支业务源码, 不自动部署提交.
- 保留共享 runtime 的已审核源码指纹; 队列实现或启动流程变更时先审核适配及测试, 禁止直接加入未验证指纹.
- 若将隔离逻辑迁入业务源码, 同时替换部署层动态适配, 避免重复定义归属字段.
- 参照 `TASK-ISOLATION.md`, `ARTINJECTER.md` 和 `TEST-WORKERS.md` 维护各环境.
