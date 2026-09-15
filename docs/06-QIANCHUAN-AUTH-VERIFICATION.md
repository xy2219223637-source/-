# 千川授权闭环验证记录

本文保留授权阶段记录；后续素材上传已完成真实验收，见 [素材上传验证记录](07-QIANCHUAN-UPLOAD-VERIFICATION.md)。

> 日期：2026-09-14  
> 范围：授权、账户解析、账户选择与接口测试；不包含素材上传。  
> 结论：真实授权回调、Token 交换与加密保存已通过；最新店铺与直接账户授权已同步出 2 个可用账户，尚未完成真实页面账户选择和素材上传。

2026-09-14 19:40 后续联调：最新授权包含有效的 `PLATFORM_ROLE_SHOP_ACCOUNT` 和 `ADVERTISER`。A02、A04 成功，A08 返回 `40000`，实际原因是 `advertiser_ids: Field must be set to array`。客户端此前发送单值，已改为 JSON 整数数组；固定错误提示也改为同时提示核查参数、授权和权限，避免一概归因于权限。Mock 契约同步增加数组类型校验。34 项千川专项测试通过（56.265 秒）。使用本次有效 Token 经正常同步流程重试，连接恢复 `active`、错误清空，保存 2 个不同投放账户，类型为 `SHOP_LINKED` 和 `COMMON_STAR`。没有重放授权码或创建投放计划。

以下为此前工作台授权阶段的记录，最新状态以上述重试结果为准。

最新重试：用户将环境配置改为千川官方授权入口 `https://qianchuan.jinritemai.com/openapi/qc/audit/oauth.html` 后，已重新校验配置并重启后端。此前一轮连接被停用；用户再次授权并保留连接后，最新会话确认 `completed`、连接 `active`、加密 Token 存在。对该连接实时重查 A02，返回一个有效的工作台管理对象 `CUSTOMER_ADMIN`；A09 限定 `QIANCHUAN` 后仍返回 `accounts=[]`、总数 0。最新连接保持启用，当前需要核对目标工作台、千川账户绑定与授权选择，无需为了排查反复停用或重新生成 Token。

## 已实现

- 六个授权 API：创建 OAuth 会话、公开回调、查询会话、账户列表、请求同步、停用连接。
- 三张授权表：连接、一次性会话、账户绑定。保留租户/角色边界与历史绑定。
- state + 浏览器 nonce Cookie、有效期及并发消费校验；重新授权后旧回调不能覆盖新凭据。
- 独立密钥加密 Token，按返回的有效期刷新；刷新版本检查防止覆盖新授权。
- 单个后台线程执行账户同步；店铺、千川代理商、工作台和直接广告账户分支，完整分页后原子更新。
- 成片详情的授权窗口、账户选择、授权结果页；登录失效后可返回原授权结果页。
- 缺少配置时关闭千川功能；平台错误使用固定脱敏信息，回调及 Token 查询日志过滤。

本阶段账户选择只在当前页面回显，不保存默认账户，也不执行上传。A06/A07、上传任务表与上传恢复逻辑留待下一阶段。

## 测试结果

| 验证 | 结果 |
| --- | --- |
| 千川专项自动化测试 | 34 项通过；新增完整授权链接处理和工作台角色、分页、空结果、错误类型测试 |
| 前端 TypeScript 检查与 Vite 生产构建 | 通过；保留现有 canvas 分包超过 500 kB 的提示 |
| 浏览器 Mock 店铺、代理商授权 | 成片 → 模拟授权页 → Cookie/state 回调 → 授权结果页显示“授权已连接”；刷新结果页仍保持连接 |
| 浏览器账户选择 | 返回两个账户，选择结果正确回显，超过 JS 安全整数范围的 ID 未丢精度 |
| 授权弹窗交互 | 弹窗置于页面顶层，位置不受成片页动画影响；Tab/Shift+Tab 在窗口内循环，Escape 关闭后焦点返回入口 |
| 上一轮全量后端回归 | 当时 84 项，83 通过、1 项环境错误；本轮新增适配后运行千川专项，未重新执行全量 |
| 真实 PostgreSQL 与本地 HTTP 接口 | 已连接，已建立三张千川表；经前端代理的健康检查 200、未登录账户接口 401、无参数回调 400 |
| 真实授权回调与 Token 交换 | 会话 `completed`，Token 已加密保存，连接进入 `active` |
| 真实授权对象与工作台账户查询 | A02 返回一个有效的 `CUSTOMER_ADMIN`；A09 限定 QIANCHUAN 后返回 `accounts=[]`、`total_number=0`、`total_page=0` |
| 真实非空账户选择、Token 刷新/撤权、生产 Nginx/TLS | 尚未完成；等待确认选中的工作台及其账户绑定/授权范围 |

全量回归剩余错误为 `test_video_workflow.VideoWorkflowTests.test_ffmpeg_finished_clip_keeps_mixed_audio`：测试直接运行 `ffmpeg`，本机 PATH 未提供该命令（`WinError 2`）；其后还依赖 `ffprobe`。已补装仓库声明的 `imageio-ffmpeg==0.6.0` 到项目虚拟环境，修复另外两项媒体测试的依赖缺失；没有修改媒体测试或业务逻辑来绕过失败。

专项测试使用临时 SQLite 数据库、真实的本系统 Bearer 校验及 `httpx.MockTransport`。覆盖：无效/过期/重复/并发回调、浏览器 Cookie 缺失、owner 失权、跨租户访问、取消和交换超时、Token 密文篡改、分页不全、客户范围缺失与为空的区别、账户类型校验、停用后重新授权、刷新与授权竞争，以及重启后不重放授权码。

运行环境：项目现有 Python 3.12.13 虚拟环境；前端使用 nvm 管理的 Node 22.14.0。生产 Docker 为 Python 3.11，本次未执行容器内回归。

## 本地运行

从 `backend` 执行专项测试：

~~~powershell
uv run --no-project --python .venv/Scripts/python.exe python -W ignore::DeprecationWarning -m unittest discover -s tests -p test_qianchuan_auth.py -v
~~~

浏览器验证使用单独的演示服务。先在 `frontend` 执行 `npm run build`，然后在 `backend` 执行：

~~~powershell
uv run --no-project --python .venv/Scripts/python.exe python scripts/qianchuan_auth_demo.py
~~~

打开 [Mock 测试入口](http://127.0.0.1:5173/mock/start)，点击“进入成片并验证授权” → “推送到千川” → “添加官方授权”。模拟授权页可选择店铺、千川代理商或直接广告账户，也可以取消授权。店铺、代理商流程已在浏览器实测，直接广告账户分支由接口测试覆盖。

该服务在 5173 同时提供已构建前端和 API，不必另起 Vite/8000。它在导入业务模块前使用临时数据库和测试登录密钥，只注入 MockTransport；不启动生产应用的其他 Worker，不访问 OSS/Redis/真实千川。停止服务后临时数据删除。成片只有模拟元数据，没有可播放的视频文件；授权测试不依赖视频播放。

日常 Vite 开发仍沿用 `5173 /api → 8000`；Docker 本地入口为 8080。HTTP 本地 Cookie 例外仅在 DEBUG 与明确的 loopback origin 条件下启用；Mock 路由不会挂载到生产 `app.main`。

## 真实联调前必须补齐

本次只输出配置校验结果，没有输出任何真实秘密；App ID、Secret、回调和加密密钥已配置，校验状态为 `ready`。此状态不证明凭据已获平台认可。

在后端实际使用的环境文件中配置：

~~~env
QIANCHUAN_ENABLED=true
QIANCHUAN_APP_ID=<真实应用 ID>
QIANCHUAN_APP_SECRET=<真实应用 Secret>
QIANCHUAN_AUTHORIZATION_URL=<应用后台确认的官方授权地址或完整授权链接>
QIANCHUAN_REDIRECT_URI=<平台已登记的完整回调地址>
QIANCHUAN_SCOPE=<按获批权限填写，可按平台规则留空>
QIANCHUAN_TOKEN_ENCRYPTION_KEY=<独立 Fernet 密钥>
~~~

密钥可使用 `cryptography.fernet.Fernet.generate_key()` 在本地生成并保存到环境文件；不要提交仓库。首次真实测试建议用获准的 HTTPS 地址；本地 HTTP 地址是否可登记仍需平台确认。

上线前仍需核验：

1. 当前配置为平台完整授权链接，含 `material_auth=1` 与 `rid`。实现保留这两个用户配置的参数，不推断 `rid` 的含义；`state` 每次重新生成。只接受明确参数集合，拒绝重复参数和 App ID、回调或 scope 配置冲突。实际授权范围、`rid` 与已登记回调是否对应，仍需在平台授权页核对。
2. A05 的代理身份与客户范围，当前仅接受明确的千川代理类型，并以 `customer_company_id` 生成重复的 `company_ids` 查询参数；普通 AGENT 未启用。
3. A08 的 `ecp_type=AGENT` 本阶段保守拒绝选择，需真实样本确认用途。类型校验成功不证明获得素材上传权限。
4. 账户分页当前支持 SDK 的 page/page_size 与 page_info；若实际账户仅返回 cursor 结构，会明确失败，需据真实样本补适配。
5. 固定回调、同一 origin/Cookie、应用 API 权限及真实数据库保存情况。生产宿主机 Nginx 也必须处理回调查询串日志；仓库已处理容器 Nginx、Uvicorn/httpx 和 Vite 的相关日志，但没有修改线上宿主机配置。

拿到测试账户后，应逐一记录“店铺 / 代理商 / 直接账户”的真实授权、账户范围与撤权结果，再进入素材上传阶段。

## 当前真实联调启动方式

为避免接入已有数据库时启动其他视频任务，新增独立启动脚本，复用真实应用路由、登录校验、数据库和千川客户端，仅创建三张千川表并启动千川同步线程。不执行通用历史迁移，不启动视频/创作 Worker；已有业务表结构须与当前代码兼容。这是本地授权联调入口，不是完整生产服务启动方式。

在 `backend` 运行（需可连接已配置的数据库与官方 API）：

~~~powershell
uv run --no-project --python .venv/Scripts/python.exe python scripts/qianchuan_auth_live.py
~~~

在 `frontend` 运行：

~~~powershell
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
~~~

先停止同端口的 Mock 服务。用 owner 账号从 `http://127.0.0.1:5173/login` 登录，经成片详情发起官方授权。此前 Mock 登录凭证不能用于真实服务；平台授权确认由用户自行完成。本轮已完成真实回调并获得有效 Token，目前需用户核对工作台与千川账户绑定。

工作台分支依据：[官方 SDK 账户查询](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/api/CustomerCenterAccountListV30Api.java)、[千川类型过滤](https://github.com/oceanengine/ad_open_sdk_java/blob/6122a5fe2096ddc673f1e7a553bc5d3437645025/src/main/java/com/bytedance/ads/model/CustomerCenterAccountListV30FilterAccountType.java)。保持现有 Token，不通过改写平台 ID 或扩大查询业务类型来猜测账户关系。
