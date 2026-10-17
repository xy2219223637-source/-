# 企业共享积分池 MVP：实现与测试运行手册

日期：2026-10-17。用户确认计划后实施 B–D。范围为测试上线 MVP；未接真实模型、支付、充值、人民币兑换或报价同步。本次没有修改现有业务数据库，也没有发布到现有服务器。

## 1. 已实现的行为

- 平台管理员通过 `/api/platform-admin/tenants/{tenant_id}/credits/grants` 发放企业积分，同 UUID 幂等键重放原结果；金额或原因变化返回409。Redis Lua 原子计数做跨进程限流，Redis不可用时503。
- 企业共享余额与冻结；成员只有月度总限额、已用、预占与剩余额度。额度总和可超企业余额；提高额度不发积分，不写GRANT。
- 企业管理员通过专属 `billing.view` / `billing.quota.manage` 权限看余额、流水与成员额度。普通成员通过 `/api/me/billing/quota` / `orders` 只看自己。
- FIXED 与输入/输出Token USAGE，按功能发布版本费率；订单绑定完整快照。整数微积分与精确有理数计算，全请求末尾一次向上取整，未使用浮点计价。
- 可信 Python Service 提供 estimate/reserve/settle/cancel；没有公开内部结算HTTP接口。浏览器仅传业务输入，不能提交用量、扣费、租户或操作者。
- 数据库事务同时更新账户、额度、订单、流水和领域审计；企业行锁串行保护同企业写入，覆盖首次账户/额度创建。账本、领域审计、幂等历史禁止 UPDATE/DELETE，费率内容、订单归属与价格快照有数据库触发器保护。
- 北京时间月周期，跨月按原 quota_id/period_key 结算；新月继承最近有效限额，保留历史。新收费调用检查有效成员，已授权订单允许可信恢复服务在成员停用后结算。
- 业务执行只领取一次；结果持久化后结算。明确无消费可取消；未知执行状态保留预占、标记待核对。补偿与扫描复用 Service。
- 前端新增 `/my-billing`、`/tenant-admin/billing`，包含本人额度/消费、企业余额/成员额度编辑/流水。后续按用户反馈，在 `/platform-admin` 企业列表的操作列新增“发放积分”，支持金额、原因、结果及本月发放记录。

## 2. 与原计划的实施差异

1. 显式迁移版本1一次创建全部8张表，包括阶段C的价格/订单结构。这样阶段B账本即有完整外键，不在阶段C重建SQLite流水表。迁移不发积分、不推算历史成本。
2. 同一企业使用既有 Tenant 行作为首次创建与并发写的首把锁，再访问账户/成员/额度/订单。MVP优先正确性；高吞吐优化留待测得瓶颈后再做。
3. 事务工具、发放和额度管理集中于 `services/billing/service.py`；计价/订单与恢复在 `orders.py`；金额与迁移独立。保留路由与事务 Service 边界。
4. 计量目前只允许服务端登记的 `mock_fixed` 和 `mock_text`，不提供伪装成真实模型的通用网关。真实模型接入前还需扩充功能/模型登记、可信用量适配和单次上界验证。
5. 原 `deploy/test-runtime` 要求真实测试模型凭证，与本次纯Mock目标冲突。因此新增独立 `deploy/billing-test.compose.yml`，不改原视频测试运行器，不使用共享任务数据库/队列。
6. 移除了 `init_db()` 按固定历史账号自动恢复平台管理员/owner/active的逻辑。既有权限不回收，但以后启动不再恢复被撤销的权限；尚未完成手机号迁移的古老账号应先由受控账号管理流程处理。
7. 对既有内置 employee/administrator 角色在显式迁移中补充相应计费权限并审计，不扩张自定义角色权限。已有自定义角色需企业owner显式授权。
8. 用户体验反馈后明确要求平台管理员可在页面给其他企业增加积分，因此扩展原“只做发放API”的界面范围；表单仍调用同一个RBAC、限流、幂等及审计保护的API，没有新增直接改余额路径。结果不明的请求按操作者和企业保存至当前浏览器会话，关闭重开或刷新后继续使用原幂等键重试。

## 3. 默认开关与隔离边界

普通部署的三个计费开关默认均false。只有完成显式迁移后才启用 `BILLING_ENABLED`。本次Mock必须同时开启 `BILLING_MOCK_ENABLED` 与 `BILLING_TEST_ISOLATION`，组合非法则拒绝启动。

隔离模式只放行登录/身份/改密、健康检查、管理路由、本人计费查询、两条Mock及API文档。旧生成、素材、供应商同步和内部结算路径均拒绝；启动直接跳过旧恢复与后台任务。请求体限16KiB，包括分块请求；不要把隔离模式当作现有视频工作台的可用替代部署。

发放单笔默认上限100000 credits，每操作者+企业每分钟10次；额度可为0，但不能低于已用+预占。预占超时默认1800秒，超时只标记待核对。配置名见根及backend的 `.env.example`。

## 4. 在独立测试环境启动

使用新Compose配置，**不要**将它叠加到现有production/test共享运行器。数据库为专用 `billing_mvp_test`，Redis独立，无真实模型凭证；前后端端口仅绑定本机18080/18000。下面的命令在仓库根目录运行。

先在当前终端设置独立测试值：`BILLING_TEST_DB_PASSWORD`（推荐随机32位十六进制，避免URL转义问题）、`BILLING_TEST_AUTH_SECRET`（至少32位随机字符）。不复用生产口令，不提交到Git。Compose缺少这些值时会直接拒绝。

```text
docker compose -f deploy/billing-test.compose.yml config --quiet
docker compose -f deploy/billing-test.compose.yml build
docker compose -f deploy/billing-test.compose.yml up -d billing-db billing-redis
docker compose -f deploy/billing-test.compose.yml run --rm -e BILLING_ENABLED=false backend python -m scripts.init_deployment
```

独立备份上述身份库，例如在db容器用 `pg_dump -U billing_test -d billing_mvp_test --format=custom --file=/tmp/billing-before.dump`，再用Compose cp将它保存到仓库外的受控备份目录。确认备份存在且可恢复，再执行：

```text
docker compose -f deploy/billing-test.compose.yml run --rm backend python -m scripts.migrate_billing_mvp --backup-confirmed
docker compose -f deploy/billing-test.compose.yml run --rm backend python -m scripts.bootstrap_billing_test_admin --phone <测试手机号> --operator <操作者标识> --confirm-test-database
docker compose -f deploy/billing-test.compose.yml up -d backend frontend
```

首次管理员脚本只允许专用测试库、只在尚无平台管理员时创建新测试身份，不提升已有用户，不发积分；在终端交互输入至少12位独立密码，密码不出现在命令参数/输出。已有管理员的库不运行此脚本。

打开 [独立测试前端](http://127.0.0.1:18080) 或 [API文档](http://127.0.0.1:18000/docs)。用新平台管理员登录，通过既有平台管理功能创建企业及owner，再通过企业管理创建成员。计费没有匿名注册或默认口令后门。

新Compose文件仅完成配置语法校验，本次未执行完整镜像构建或启动该新栈；当前验证使用本机独立PostgreSQL16与独立Redis7，未复用任何业务库。

## 5. 测试费率与API验收

页面发放入口：平台管理员登录 → “平台管理后台” → 找到目标企业 → 操作列“发放积分” → 输入本次增加的积分及原因 → “确认发放”。表单显示发放后的企业余额，并可查本月发放记录。企业管理员只能设置月额度，不能使用该入口发行积分。停用企业的发放按钮不可用。进入“企业积分管理”时，侧栏“企业管理后台”不再同时选中。

平台管理员通过 `POST /api/platform-admin/billing/price-rules` 创建草稿，再通过 `POST /api/platform-admin/billing/price-rules/{id}/activate` 发布。费率内容不能覆盖修改，改价创建新version。

```json
{"feature_code":"mock_fixed","billing_mode":"FIXED","version":1,"fixed_credits":"20"}
```

```json
{"feature_code":"mock_text","billing_mode":"USAGE","version":1,"metrics":[{"metric":"input_tokens","unit_size":1000,"credits_per_unit":"0.20"},{"metric":"output_tokens","unit_size":1000,"credits_per_unit":"0.80"}]}
```

这些仅为测试积分费率，不代表任何供应商报价。

发放带 Bearer 与独立 UUID `Idempotency-Key`，请求体 `{"amount_credits":"10000","reason":"测试初始积分"}`。企业owner通过 `PUT /api/tenant-admin/billing/members/{user_id}/quota` 分别提交 `{"total_limit_credits":"4000","reason":"本月消费上限"}` 和7000。路径是用户字符串ID，区别于既有后台成员管理的整数member_id。

成员带Bearer与UUID分别调用 `POST /api/ai/mock/fixed`、`POST /api/ai/mock/text`，body只需 `{"text":"测试"}`。Token用量由服务器固定为输入2000/输出1000；预占上界为3000/2000，结算实际1.2并释放差额。FIXED实际20。

期望：企业9978.8000，A已用21.2000，B已用0，所有reserved归零；GRANT一条、CHARGE两条。成员额度修改只增加审计。重复请求键返回原结果；同键改body返回409。前端在响应不明时保留同一键重试。

## 6. 扫描、核账与恢复

```text
python -m scripts.reconcile_billing_orders
python -m scripts.reconcile_billing_orders --recover --batch-size 100
```

默认扫描超时订单并标记NEEDS_REVIEW，不扣费、不释放。`--recover`仅自动处理可证明未开始的订单，或已持久化可信执行结果的订单。未知STARTED继续占额，不重复调用Mock。已标记未知订单不会堵住后续扫描批次；重复扫描/恢复不重复记账。

脚本同时核账：企业余额=全部流水delta之和；企业冻结=未终结订单预占之和；各月used/冻结与绑定该周期订单一致。`ok=false`时停止扩大测试范围。应用回退前停用入口，保留账本；不能删流水、清used或直接UPDATE余额来“修复”。

这不是定时后台进程。部署方需按测试环境运维约定定期执行扫描并检索NEEDS_REVIEW；未知执行需要人工核对，当前没有管理员任意补扣/退额API。

## 7. 自动化验证与范围说明

测试用独立临时SQLite、随机PostgreSQL schema与独立Redis。PostgreSQL设置 `BILLING_TEST_DATABASE_URL`，Redis设置 `BILLING_TEST_REDIS_URL`，备份恢复测试另设 `BILLING_TEST_PG_BIN` 指向本地工具目录。不得把这些变量指向生产实例。

从backend运行：

```text
uv --no-cache run --no-project --python ../.venv/Scripts/python.exe python -m unittest discover -s tests -p "test_billing_*.py" -v
```

Windows工具链：已用uv复用项目Python3.12虚拟环境；Node使用nvm管理目录中的22.14.0。当前沙箱不能切换全局nvm符号链接，也不能写已有node_modules缓存，因此前端检查将tsBuildInfoFile改到忽略的测试目录，Vite/Vitest使用 `--configLoader runner`，没有更换全仓依赖或安装全局包。

T01–T31定位：管理测试覆盖T01–05、T17、T19、T21–31；订单测试覆盖T06–16、T18、T20及结算竞态/响应丢失/停用恢复；同一套管理与订单合同在PostgreSQL再次运行。安全测试覆盖三角色真实登录E2E、隔离/分块请求限额、实际跨进程Redis限流、领域故障回滚、SQLite恢复、启动检查与辅助访问审计失败；PostgreSQL另做pg_dump/pg_restore恢复。

实际验证结果：

| 项目 | 结果 |
|---|---|
| 计费专项 | 58项通过，无跳过；包含真实PostgreSQL并发与pg_dump/pg_restore，以及实际跨进程Redis限流 |
| 前端完整回归 | 11个文件，185项通过（其中计费页面4项）；TypeScript检查通过 |
| 前端构建 | Vite生产构建通过，产物位于忽略的独立测试目录；既有大chunk警告仍存在 |
| 后端完整回归 | 首轮681项，680通过，1项旧电商素材测试读取本地Redis配置而环境报错；切换独立测试Redis后，该失败测试复测通过。未宣称首轮完整回归全绿 |
| 显式迁移CLI | 独立空PostgreSQL测试库先初始化93张旧业务表；跨进程首次迁移成功、二次返回already_current，启动schema校验通过 |
| 三角色真实HTTP | 实际Uvicorn进程验证：登录、企业/团队/成员创建、费率发布、发放/重放/冲突、额度、两条Mock及越权/用量伪造/旧同步拒绝；最终9978.8000、A已用21.2000、B已用0、3条流水 |
| 新Compose | 配置语法校验通过；未实际构建并启动整套新Compose镜像 |
| 代码检查 | git diff --check通过；未引入模型/支付依赖或凭证 |

可重放真实HTTP验收：先在当前终端设置 `BILLING_SMOKE_ADMIN_PASSWORD`（不放命令参数），然后执行 `python -m scripts.billing_mock_smoke --base-url http://127.0.0.1:18000 --phone <平台测试手机号> --confirm-test-environment`。脚本只接受已启用隔离Mock的环境；创建新的随机测试企业，通过API完成发放与消费；遇已有不同费率会拒绝覆盖，不输出口令/令牌。

完整前端回归还发现旧标题清单未覆盖当前App路由；已补齐计费及现有缺失路由标题，并更新受影响测试的配置API mock，没有放宽原有覆盖断言。一次千川目录分页测试在并发回归中出现异步偶发失败，随后完整185项通过，未改动千川业务逻辑。

尚未进行远程发布、实际浏览器人工验收或真实模型联调。后端首轮回归的环境依赖及新Compose未整栈启动，均不能当作已经在目标服务器验收；部署前应按上述独立资源配置复跑完整回归。测试临时服务在验证完成后停止，测试数据不并入业务库。

导航调整：侧栏统一为‘企业管理后台’，进入后通过‘企业积分管理’页签查看共享积分池、设置成员月度额度和查询消费流水；原 /tenant-admin/billing 地址保留。平台发积分入口仍在平台管理的企业列表中。


### 平台扣回多发积分（schema v2）

平台管理员在「平台管理 → 企业列表 → 扣回积分」打开面板，核对企业、余额、预占和可扣回金额，输入正数积分及扣回原因后确认。只允许扣回 `balance - reserved` 内的积分；不调整成员月度额度，不修改或删除原发放流水。企业流水及平台本月积分调整记录分别显示 GRANT / REVOKE / CHARGE，扣回记录保留负数变化、余额、操作者和原因。发放记录旧 API 仍只返回 GRANT。

新增受平台管理员权限控制的 API：`POST /api/platform-admin/tenants/{tenant_id}/credits/revocations`（请求字段 amount_credits、reason，必传 UUID Idempotency-Key）、`GET .../credits`（余额/预占/可用）、`GET .../credits/adjustments`（本月发放与扣回记录，分页）。扣回复用发放单笔上限及限流，金额最多四位小数；同键同参重试返回首次结果，同键异参拒绝。扣回与发放采用独立幂等作用域。前端保存未确定的扣回请求，关闭重开继续原请求；不要换浏览器重新发起未确定的操作。

已有 v1 数据库先停写、备份，再运行原迁移 CLI `python -m scripts.migrate_billing_mvp --backup-confirmed`。v2 扩展账本方向约束，PostgreSQL 原位更新 CHECK，SQLite 事务内重建账本、原样复制历史并恢复不可变触发器；新库直接创建 v2。迁移幂等，启动仍只校验 schema。不能直接回退到仅支持 v1 的旧应用；已发生的扣回必须保留账目，不以删除流水撤销。

本次本地验证：计费专项70项通过（SQLite及独立PostgreSQL，包括v1升级保留历史、不可变触发器、并发扣回/消费、预占订单正常结算）；真实HTTP在新建独立验收企业执行发放10、扣回3、重放、超额拒绝，余额7、两条调整流水。原演示企业余额未改动。当前本地测试库已先使用pg_dump备份，再升级v2；未操作远程业务数据库。


### 平台模型费用管理

平台管理员通过侧栏「平台模型费用管理」或 `/platform-admin/model-pricing` 进入独立页面。当前只配置 Mock 测试功能的企业积分扣费规则，不配置供应商成本或人民币金额，不接入真实模型。

- 按次固定积分：填写“每次扣除积分”，点击“保存按次新版本”。
- Token换算：输入和输出分别填写“单位数量”和“每单位积分”，例如输入每1000 Token收0.20积分，输出每1000 Token收0.80积分。输入2000、输出1000合计1.2000积分；不足单位按实际数量比例计算，最终总额向上取整至0.0001积分。实际用量仍只能由受信任服务端提供。
- 保存仅创建待启用版本。核对“费率版本记录”中的功能、版本、扣费设置后点击“启用此版本”；同功能旧版本自动停用，新订单使用新版本，原订单继续按原快照结算。不能直接编辑历史版本，需保存并启用新版本。
- 金额必须大于0，最多四位小数；Token单位为1至10亿的整数。版本由服务器摘要建议，多个管理员同时保存可能出现版本冲突，刷新核对已有版本后再操作。响应丢失时先刷新查看是否已有待启用版本，不要直接换新版本重复创建。

复用原有创建/启用/分页查询API，增加 `GET /api/platform-admin/billing/price-rules/summary` 返回两类规则的当前生效版本及下一个建议版本，所有接口校验平台管理员。费率响应增加精确十进制字符串 fixed_credits，页面不使用浮点数换算积分。本次无数据库迁移。自动化测试覆盖手动调价、保存/启用分离、权限、版本冲突、新旧Token订单按各自快照结算。
