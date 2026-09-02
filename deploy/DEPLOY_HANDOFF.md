# Enrui AI Commerce Agent 部署交接单

## 一、已知前提

- 服务器：`121.199.52.72`
- 生产域名：`waynepartners.xyz`、`www.waynepartners.xyz`
- 系统要求：Ubuntu 22.04+，root 权限
- 阿里云安全组放行 TCP `80`、`443`
- PostgreSQL 数据库 `enrui_ai_commerce_agent` 与 37 张业务表已经初始化
- 不需要建库、建表、执行 SQL、迁移或处理本地业务数据
- 生产配置只保存在服务器 `/opt/enrui-ai-commerce-agent/shared/.env`

## 二、已经完成的部分

项目侧已经具备以下部署能力，同事不需要重新设计部署架构：

- 前端 React 生产构建及 Nginx 托管。
- FastAPI 后端 Docker 镜像。
- Docker Compose 前后端编排及自动重启。
- 前端容器仅监听宿主机 `127.0.0.1:8080`，不直接暴露公网端口。
- 宿主机 Nginx 负责域名、TLS 终止和 HTTP 跳转 HTTPS；容器 Nginx 负责 `/api` 代理和前端 SPA 路由。
- PostgreSQL 线上业务库连接及 37 张业务表自动初始化。
- Redis 队列唤醒、分布式任务锁和并发协调。
- 阿里云 OSS 主存储、签名访问和临时处理缓存。
- 部署前强制检查 PostgreSQL、Redis、OSS，任意服务不可用即停止部署。
- 自动拉取 `main` 最新代码、构建镜像、更新容器和健康检查。
- 生产密钥独立保存在服务器，不进入 Git 仓库。

线上数据库 `enrui_ai_commerce_agent` 及现有 37 张业务表已经初始化完成。同事不需要建库、手工建表、执行 SQL 或迁移本地数据。部署脚本中的数据库步骤只进行幂等结构检查：已有表不会重复创建、清空或覆盖。

核心部署入口：

```text
/opt/enrui-ai-commerce-agent/repository/deploy.sh
```

生产配置位置：

```text
/opt/enrui-ai-commerce-agent/shared/.env
```

宿主机站点与证书位置：

```text
/www/server/panel/vhost/nginx/waynepartners.xyz.conf
/etc/letsencrypt/live/waynepartners.xyz/
/opt/enrui-ai-commerce-agent/shared/certbot-www/
```

## 三、同事需要完成的工作

### 1. 检查服务器

使用 root 登录服务器，确认宿主机 Nginx 监听公网 `80/443`，前端容器仅监听本机 `8080`：

```bash
ss -ltnp | grep -E ':(80|443|8080) ' || true
nginx -t
systemctl status nginx --no-pager
df -h /
free -h
```

预期 `80/443` 由宿主机 Nginx 监听，`127.0.0.1:8080` 由 Docker 监听。不要让前端容器重新直接绑定公网 `80/443`。

阿里云安全组至少需要开放：

```text
TCP 80：网页访问
TCP 443：HTTPS 网页访问
TCP 22：运维 SSH，建议只允许办公网络固定 IP
```

PostgreSQL、Redis 不需要继续向所有公网 IP 开放，只保留应用服务器和必要开发出口 IP。

### 2. 首次初始化

```bash
git clone https://github.com/Dreamsmama/enrui-ai-commerce-agent.git /opt/enrui-ai-commerce-agent/repository
bash /opt/enrui-ai-commerce-agent/repository/deploy.sh
```

脚本会自动安装 Git 和 Docker。首次运行会生成生产配置文件并主动停止，这是正常行为。

### 3. 填写服务器运行配置

编辑：

```bash
nano /opt/enrui-ai-commerce-agent/shared/.env
```

必须填写或确认：

- `DATABASE_URL`：确认指向已经建好的线上 `enrui_ai_commerce_agent`，不要改成 `canvas_platform` 或 SQLite。
- `REDIS_URL`：线上 Redis 连接串。
- `ALIYUN_OSS_ACCESS_KEY_ID`：轮换后的 OSS RAM AccessKey。
- `ALIYUN_OSS_ACCESS_KEY_SECRET`：轮换后的 OSS RAM Secret。
- `AUTH_SECRET`：执行 `openssl rand -hex 32` 生成，不能使用默认值。
- `LLM_API_KEY`、`LLM_MODEL`、`LLM_VISION_MODEL`：实际大模型配置。
- `IMAGE_GENERATION_API_KEY`、`IMAGE_GENERATION_MODEL`：火山方舟 Seedream 配置。

检查不能残留占位符：

```bash
grep -n 'FILL_' /opt/enrui-ai-commerce-agent/shared/.env
```

正确结果应当没有任何输出。不要把生产 `.env` 发到群里、提交到 Git 或复制到工单。

### 4. 正式部署

```bash
bash /opt/enrui-ai-commerce-agent/repository/deploy.sh
```

脚本会依次执行：拉取最新代码、构建镜像、幂等检查现有数据库表及线上服务连接、启动容器、执行健康检查。无需额外执行建库、建表或数据迁移命令。部署日志保存在 `/opt/enrui-ai-commerce-agent/shared/logs/`。

前端默认监听 `127.0.0.1:8080`，部署脚本也通过该地址执行健康检查。如需调整，可在执行脚本时通过 `FRONTEND_BIND`、`FRONTEND_PORT` 和 `HEALTHCHECK_URL` 覆盖；生产环境不要把前端端口直接暴露到公网。

### 5. 验收

```bash
cd /opt/enrui-ai-commerce-agent/repository
docker compose ps || docker-compose ps
curl -fsS http://127.0.0.1:8080/api/health
curl -fsSI http://waynepartners.xyz/
curl -fsSI https://waynepartners.xyz/
nginx -t
systemctl is-active nginx
systemctl is-active certbot.timer
```

健康接口应至少满足：

```text
status=ok
database=postgresql
redis=ok
storage=aliyun_oss
```

浏览器访问：

```text
https://waynepartners.xyz
```

完成基础部署验收：

1. HTTP 自动跳转 HTTPS，两个生产域名均可打开登录页面，静态资源加载正常。
2. 调用 `/api/health`，确认 PostgreSQL、Redis 和 OSS 状态正常。
3. 登录一个已有账号；如果线上还没有账号，再注册首个企业 Owner。
4. 页面刷新和前端路由跳转不出现 404。

## 四、以后更新代码

代码 push 到 `main` 后，只运行：

```bash
bash /opt/enrui-ai-commerce-agent/repository/deploy.sh
```

脚本会记录 Git 提交 ID。若新版本健康检查失败，会自动恢复上一次运行中的镜像；回退失败时会保留容器日志并返回非零退出码。

不要删除 `/opt/enrui-ai-commerce-agent/shared/.env`。它独立于仓库，不会被 `git pull` 覆盖。

宿主机 Nginx、Let's Encrypt 证书和 Certbot webroot 独立于应用仓库，应用部署或镜像回退不应修改这些文件。证书续期后由 `/etc/letsencrypt/renewal-hooks/deploy/reload-waynepartners-nginx` 校验并重载宿主机 Nginx。

## 五、失败时回传信息

请不要只回复“部署失败”。回传以下脱敏结果，禁止包含 `.env`、密码、AccessKey 和 API Key：

```bash
cd /opt/enrui-ai-commerce-agent/repository
docker compose ps || docker-compose ps
docker compose logs --tail=200 backend frontend 2>&1 || docker-compose logs --tail=200 backend frontend 2>&1
curl -i http://127.0.0.1:8080/api/health
nginx -t
systemctl status nginx certbot.timer --no-pager
```

同时说明：

- 执行到哪一步失败。
- 完整错误时间。
- `80/443` 是否由宿主机 Nginx 监听，`8080` 是否仅监听 `127.0.0.1`。
- 阿里云安全组是否已开放 `80/443`。
- 最近一次部署对应的 Git 提交 ID：`git rev-parse HEAD`。

## 六、完成标准

满足以下全部条件才算完成：

- 前后端容器状态正常且设置自动重启。
- 宿主机 Nginx 状态正常，HTTP 自动跳转 HTTPS，两个生产域名均可打开登录页面。
- 前端容器仅监听 `127.0.0.1:8080`，没有直接暴露公网端口。
- Certbot 定时器启用，模拟续期验证成功。
- 健康接口显示 PostgreSQL、Redis、OSS 均正常。
- 上传文件实际进入 OSS，而不是服务器持久目录。
- 线上 PostgreSQL 连接正常，部署过程没有修改或迁移既有业务数据。
- 生产 `.env` 权限为 600 且未进入 Git。
- 将访问地址、部署提交 ID 和验收结果回复项目负责人。

## 七、验收回执

部署完成后请回复：

1. 公网访问地址：`https://waynepartners.xyz`。
2. 部署的 Git 提交 ID。
3. `curl http://127.0.0.1:8080/api/health` 的完整结果（应包含 `database=postgresql`、`redis=ok`、`storage=aliyun_oss`）。
4. `docker compose ps` 中前后端容器均为 `Up`/健康。

生产密码、Aliyun AccessKey、LLM/API Key 只写入服务器 `.env`，不要发群聊或提交 Git。
