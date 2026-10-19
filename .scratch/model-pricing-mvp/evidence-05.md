# 05 音频 MOCK 折算验证

- task_id：model-pricing-mvp-20261019-05；来源：用户 implement-spec、并发 agent 授权、票 05、api-spec §4/§6 与主线程委派。
- 日期：2026-10-19（Asia/Singapore）；worktree：`D:/原力2222222222/model-pricing-work/ticket-05`；分支：`codex/model-pricing-05`。
- 初始 checkpoint：`14eaecaa`，实施前已确认；18:30 前快进到 integration `26e823c`，18:34 合入仅文档更新的 `b97fb75`，修复后合入 `8461e299`（04 独立适配器与证据，不改共享 metering）。当前 step：独立审核唯一 P2 已修复，23项离线检查及8项音频组件通过；原版 PG24、修中 PG2、最终源码 PG3 分批通过。后续入口：主线程合并本票，再按06完整MOCK门禁推进；本票不放行REAL。
- 范围仅 `audio_adapter.py`、旧 `tts.py` 前置 guard、音频定向后端/组件测试和本票证据。未改共享 metering、registry、查询 API 或通用 UI；未改原 checkout、配置、服务或依赖。

## 口径及可核验结果

本地 MOCK 协议为 `mock-audio:pcm8000:mono:v1`，仅 PCM WAV、8000 Hz、单声道、16 bit、完整帧且整数毫秒；ASR 仅 `route=local`，TTS 仅 `resource=local-tone`。它是受控测试协议，不是厂商最低有效规格、计费口径或真实语音识别效果声明。合成音频是本地音调；转写文本为合成测试结果。

| 能力 | 原始依据 | 折算测试比例 | 最终企业积分 | 可核对产物 |
|---|---|---|---|---|
| TTS | 原发送文本 code point 数 6；含首空格、emoji、组合字符和换行，不 trim/normalize | 1000 字符→1 Token；精确 0.006 Token | 0.0060 | 可解码 8000 帧 / 8000 Hz WAV；文本及 0–1000ms 时间戳 |
| ASR | 实际输入 WAV 8000 帧 / 8000 Hz =1000ms | 1000ms→1 Token | 1.0000 | 输入帧/采样率/SHA-256、合成文本及 0–1000ms 时间戳 |

两能力均保存 MOCK、FOLDED_TOKENS、原始数量、折算数量、供应商成本 NOT_INCURRED、独立请求引用与发起时快照；企业余额减少＝成员已用增加＝唯一 CHARGE 流水。API 同时验证成员与企业查询及跨租户/其他成员隐藏。

原始毫秒不能先取整。主线程复核指出早期 ceil 实现会使 1/8000 秒被计为 1ms；已按 api-spec §4.7 修正：`frames * 1000 % rate != 0` 的素材预占前拒绝。高费率 100.0000 的 TDD 反例先证实旧实现对 1 帧不拒绝，随后 1/7/9/7999 帧全部 PRICE_SPEC_MISMATCH、零订单/零传输/零预占/零扣额；8 帧精确 1ms 成功。只在最终积分向上取整至 0.0001。

## 实际验证与失败记录

工具为 PowerShell/exec、apply_patch、原环境 Python 与现有 node_modules；未安装依赖。runner 禁用 `.env`、清除配置环境值、关闭初始化/恢复/worker，并使用临时 SQLite bootstrap/账务夹具；这部分仅证明离线业务行为，不宣称 PostgreSQL 锁语义。

1. 首条 TTS 测试 red：缺 `audio_adapter` 模块；最小实现后 green。ASR red：缺实际时长/本地生命周期；添加解码与本地提交/轮询/临时资源后 green。后续受控异常及 guard 先 red（场景参数缺失、旧 TTS 先报密钥缺失），随后实现并验证通过。部分 TTS 音频帧数反例 red 8000≠4000，修正为实际 500ms 部分 WAV 后通过。毫秒计量纠正的反例见上段。
2. 18:30 基线定向回归：`python backend/scripts/run_refinement_tests.py test_audio_metering.py test_tts.py test_model_image_metering.py`，33/33 通过（当时音频18＋旧TTS2＋图像13）。
3. 毫秒边界修正后：`python backend/scripts/run_refinement_tests.py test_audio_metering.py test_tts.py`，21/21 通过（音频19＋旧TTS2）；无 skipped。实际 Python 路径为原 checkout `backend/.venv/Scripts/python.exe`，运行脚本和被测代码来自本 worktree。
4. `node node_modules/vitest/vitest.mjs run --config vitest.config.ts tests/model-audio-metering.test.tsx tests/model-image-metering.test.tsx`，12/12 通过（音频8、图像4）。覆盖两能力费率保存/回显与来源限制、成员/企业列表及详情、MOCK/折算/原始数量/积分/快照、缺用量保留预占提示。
5. 单文件 TypeScript：`node node_modules/typescript/bin/tsc --noEmit --jsx react-jsx --module esnext --moduleResolution bundler --target es2022 --esModuleInterop --skipLibCheck tests/model-audio-metering.test.tsx`，退出 0。`git diff --check` 通过。

### 独立审核后的半帧修正（18:38）

- 中性独立审核报告：`D:/原力2222222222/model-pricing-work/review05-audit.md`；原复现脚本：同目录 `review05_pcm_repro.py`。唯一 P2 为 `wave.getnframes()` 整除丢弃 data chunk 尾半帧；原检查只读取已经取整的帧数，故16001字节 PCM 被误当8000完整帧。
- 实际先运行原脚本：1/1，输出 ASR `SUCCEEDED/SETTLED/1.0000`，确认问题；新增 ASR/TTS 两个边界测试先 red（未拒绝与错误成功）。
- 修复：通过公开 `wave.readframes(frames + 1)` 读取整个 data chunk，要求可读字节严格等于 `frames * 2`，在整数毫秒计算前拒绝截断/半帧。TTS 生成响应的解码放到本地提交/轮询之后，畸形响应返回 FAILED＋INVALID_AUDIO、usage UNKNOWN、预占保留，finally 清理。
- 新方法：`test_odd_pcm_data_chunk_rejects_asr_before_reservation_and_submit`、`test_odd_pcm_tts_response_is_failed_with_unknown_usage_and_retained_reservation`；前者断言 PRICE_SPEC_MISMATCH、零订单/预占/提交/扣额，后者断言失败/未知、预占0.0060、零扣额及清理。
- 实际后端命令与第3项相同，最终 **23/23** 通过（音频21＋旧TTS2）；音频组件重新运行 **8/8** 通过；diff-check 通过。原复现脚本保持原样，逆转后在 `inspect_wav` 处 ValueError，退出1：该脚本故意断言旧错误，不能将其退出1伪装成未修复或当作新通过测试。
- 主线程原冻结版 PG24（音频19＋历史5）已执行通过：`pg-audio-before-review.json`（原外部报告 `pg-05-20261019-1835/run-state.json`），源摘要前后一致，24个独占schema均cleaned，业务schema版本仍2，厂商与真实存储请求均0。此报告**不覆盖上述新增两场景**。
- 半帧修复后的新增两场景主线程定向 PG **2/2** 通过：`pg-audio-half-frame-result.json`（原外部报告 `pg-05-fix-20261019-1840/run-state.json`），两个schema均清理、源摘要前后一致、业务schema版本仍2、厂商与真实存储请求均0。原24与本2是不同源码阶段的两批验证，**不是同一最终版本单轮26项通过**。
- TTS 解码重排后，自查补充保留原先 ASR `invalid_audio` 受控失败分支，避免无效响应场景静默成功。现有无效音频测试扩展到 TTS/ASR，先 red 确认 ASR 错误成功，恢复该分支后23/23低层重绿；不改变默认成功和半帧拒绝路径。
- 最终源码主线程定向 PG **3/3** 通过：`pg-audio-final-result.json`（原外部报告 `pg-05-final-20261019-1842/run-state.json`），12.534秒，覆盖两个 half-frame 方法及扩展的 `test_invalid_generated_audio_retains_reservation_instead_of_claiming_zero`；3个独占schema全部清理，当前源码前后摘要一致，业务schema仍2，厂商与真实存储请求均0。最终以该批结果绑定最后修正源码；旧24与修中2报告保留各自批次与源码范围，不冒称最终版本一次完整29项PG通过。

最终音频21项通过公共 `execute_metered` / 费率与订单查询 / 余额成员流水 / legacy TTS 边界验证：成功、重放不重复提交、调价快照、两能力部分结果、缺用量、TTS空产物、ASR空转写、空输入与无音轨、无效生成音频、不合法输入/规格/路由/上界、受限轮询超时、证明未消耗、清理失败独立恢复、写流水故障同事务回滚与恢复、权限及额度不足、整数毫秒与拒绝分数毫秒、PCM尾半帧输入/响应。

## 失败处理、限制与恢复

- 空转写不能抹去已确认的 ASR 输入时长；TTS 空产物/无效音频不能冒充零消耗。缺用量与轮询超时保留 RESERVED＋NEEDS_REVIEW；仅本地提交前证明无消耗才 CANCELED。
- 本地轮询每次任务最多 1–10 次（默认3），默认两次；只提交一次，重放不提交、不轮询。模拟轮询不调用真实计次器，真实三次预算未使用。
- 临时对象仅在每个 adapter 的内存字典；无真实存储对象。正常/超时均 finally 清理；失败写 logger 警告、返回 pending 引用并保留 cleanup_events，可调用 `retry_cleanup()` 单独恢复，不重复模型或扣账。进程结束内存自动释放，无外部资源遗留；这不是持久对象存储恢复的真实证明。
- 账务故障后企业余额/成员已用/流水同事务回滚；已保存结果继续保留预占，可用共享 `settle_model(order_id)` 恢复，无模型重提。
- 旧 TTS 在 billing_enabled 且无计费上下文路径中，于密钥检查、HTTP 客户端和文件写入之前拒绝；billing 关闭时原路径回归通过。旧 ASR 防绕过复用票02，不重复改写。
- 未访问真实厂商、真实对象存储或业务 schema；未修改运行服务、凭据或 `.env`。本票不证明 REAL 接入、官方最低规格或真实供应商用量；必须等待06门禁及09独立接入验证。

## 本地提交危险操作标志

- task_id：model-pricing-mvp-20261019-05-git；来源：用户限定本次 implement-spec 的本地提交授权与主线程分派；继承 `git-operations.md` 的全主题风险与恢复说明。
- 目标/范围：仅本分支、本票明确文件清单，本 agent 不推送；风险是 Git 索引/提交状态写入，未涉及数据库或进程操作。
- 预检查：最终定向测试、diff-check、暂存清单和 cached diff；备份/checkpoint 为 integration `8461e299` 与本地提交，原 checkout 不变。
- 执行路径：主线程 PG 验证→合入最新 integration→复核→明确清单暂存→本地提交；失败保留 worktree，不 reset/强推。回退用新增 revert，冲突可 `git merge --abort`；无自动重启/重载/自动回退。
- 恢复入口：本文件、票05、`git status --short --branch` 与 `git log -3 --oneline`。最近安全状态为最终23项离线低层/API与8项音频组件通过，旧版PG24、修中PG2、最终PG3分批通过且schema全部清理；下一步仅本票明确清单暂存与本地提交。所有PG写入由主线程在用户授权的独占合成schema内集中执行，本 agent 不直接访问DB。
