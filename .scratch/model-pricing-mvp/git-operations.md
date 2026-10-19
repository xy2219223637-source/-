# 本地集成与中转推送操作标志

- task_id: model-pricing-mvp-20261019-git
- 时间：2026-10-19T17:47:00+08:00
- 来源：用户明确允许本地提交并推送指定中转仓库，且限定仅用于本次 implement-spec。
- 目标：按依赖汇集已测试票据，交付单一可审核集成分支。
- 范围：本地 codex/model-pricing-*；远程仅 https://github.com/xy2219223637-source/-.git 的 codex/model-pricing-mvp，不更改main及原Gitee origin，不创建PR/发布。
- 风险：本地提交和合并写入Git状态；推送上传代码与任务文档；故仅暂存本主题文件，排除.env/运行凭据/原工作区既有三项无关改动。
- 预检查：原HEAD=3deddd85350a897620aca705584f58147366b15d；独立worktree；目标ls-remote确认main=2afb757b65979304ff87b665d69fdde2fbb9e4e7；17:43禁交互push --dry-run证实新分支可创建，原main不受影响。
- 执行路径：每票定向测试→本票提交→并入集成tip→merger在集成worktree合并→定向回归→双轴独立审核→普通非强制push到指定分支。
- 备份位置：D:/原力2222222222/model-pricing-work/backup（原既有变动）；所有已合并Git提交；原始规范外部context副本。远程目标分支原不存在，不覆盖旧分支。
- 备份记录：三项原工作区改动已复制；集成文档checkpoint=043c14c；后续checkpoint通过git log与execution.md记录。
- 回退路径：保留原checkout及运行服务；本地未完成合并可git merge --abort。已提交有问题使用新增revert提交，禁止reset--hard/强推/删账。已推送分支的回退同样采用新增revert并普通推送；不擅自删除仓库或远程分支。
- 恢复命令：git -C D:/原力2222222222/model-pricing-work/integration status --short --branch；git -C D:/原力2222222222/model-pricing-work/integration log -5 --oneline；然后按execution.md继续下一未完成票。
- 自动重启/自动回退：均关闭；本Git操作不启动或重启进程。失败保留worktree、错误日志和最后安全提交，拒绝非快进时停止推送并读取远程差异，不强推。
- 验证：检查暂存范围、git diff --check、相关测试、原checkout状态；推送后以ls-remote核对远程分支SHA等于集成HEAD。
- 最后安全状态：仅文档提交已形成，原运行代码/环境未变；下一步恢复入口=票01最后验证与本地提交。

## 02 本地提交检查点

- 时间：2026-10-19T18:09:00+08:00；task_id=model-pricing-mvp-20261019-02；来源=用户本次 implement-spec 本地提交授权及主线程分派。
- 目标/范围：仅 ticket-02 worktree 的本票应用、测试、票据和证据；不推送、不改原 checkout/.env/运行服务/真实业务 schema。
- 预检查：integration 8b345da 已快进合入；75 项后端检查、17 项补充核心回归、15 项组件、tsc、diff-check 结果见 evidence-02.md。暂存前使用明确文件清单并复核 diff --cached --stat。
- 备份/checkpoint：基准 8b345da；本分支提交是后续恢复点。已有风险、恢复及失败保护沿用本文件；失败保留工作树，不使用 reset --hard、强推或删除账务。回退通过新增 revert，主线程在 integration 合入前可直接检查差异。
