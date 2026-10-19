# 实施与恢复记录

- task_id: model-pricing-mvp-20261019
- 来源：用户 2026-10-19 调用 implement-spec，入口 map.md；中途禁止任何提交，随后于17:38 +08:00明确改为“允许本地提交并推送指定中转仓库”。本地技能集成恢复；目标GitHub仓库尚待提供，原Gitee origin不推送，不创建远程PR或发布。
- 目标：按九票依赖实现同模型当前费率与计费链路；完成定向测试和独立审核；需环境授权的验收不冒充通过。
- 基线：Computing_Power_Management / 3deddd85350a897620aca705584f58147366b15d。
- 当前 step：01，在 codex/model-pricing-01 独立 worktree 实施；集成 worktree 为 D:/原力2222222222/model-pricing-work/integration，分支 codex/model-pricing-mvp。用户最新授权允许本地集成提交。
- 已完成：读取 spec、API、九票、tracker、术语和现有测试/迁移代码。
- 待完成：01→02→03/04→05→06→07/08/09；独立双轴审核、修复与验证。
- 测试边界：已由各票确认的计价规则、管理/订单 API、账务事务、供应商适配器和组件；按 TDD 逐条验证，默认无全量构建/E2E。
- checkpoint：原始 HEAD 与本记录；原工作区三项既有改动不纳入提交。
- 风险：本地提交/合并产生 Git 状态；迁移修改影响 schema checksum；运行服务仍使用旧 schema，不能提前热加载新代码。
- 危险操作标志：worktree、临时离线测试、本地Git提交与集成；运行环境迁移、配置/费率/账务写入、依赖安装、重启、发布均未执行。一次提交因缺身份失败，禁令期间取消暂存无提交；最新授权后仅在每次git命令临时设置用户提供的账号名和邮箱，不配置或记录密码。
- 预检查：当前分支及 git status 已读取；8000/5173 有运行进程，禁止重启或切换其代码文件。
- 执行路径：在独立集成 worktree 内落地；原 checkout 不切分支、不合并，以保护运行代码。
- 备份位置：D:/原力2222222222/model-pricing-work/backup；保存三项原改动与规范副本，Git 基线可恢复。
- 回退：原 checkout 不变；停止采用新 worktree 即恢复基线；禁止 reset/clean 原 checkout。无真实账务回退需求，因为不写入。
- 自动重启/回退：均关闭；失败保持最后安全状态并记录恢复入口，不自动删除工作树或账本。
- 验证结果：基线 unittest test_billing_management test_billing_orders test_billing_safety 共40项，39通过、1 skipped（不可视作完整门禁），日志 D:/原力2222222222/model-pricing-work/baseline-tests.log；真实调用0。2026-10-19T17:26 +08:00。
- 下一步：01 实施/TDD，逐票本地集成并回归，最后独立双轴审核；最终仅推送用户指定中转仓库。gh当前未登录，账号主页不构成仓库链接。环境副作用授权缺失时只记录待验收，不标 resolved。
