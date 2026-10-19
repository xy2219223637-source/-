# 05 独立只读审核

- task_id: review05-20261019; 来源：主线程委派的 05 音频 MOCK 契约审核；时间：2026-10-19T18:36:08+08:00。
- 目标：仅检查 `.scratch/model-pricing-mvp/issues/05-audio-mock-conversion.md` 的实质正确性、安全、恢复与契约问题，不审 REAL 或 06 尚未交付事项。
- 对象：`D:/原力2222222222/model-pricing-work/ticket-05`；起始 HEAD `26e823cb64c693e17313db97d0aea38488f1b8bf`；实际测试及最终核对 HEAD `b97fb75f7b52a25172e97bf9c2dca011c03e13a2`。期间仅快进新增 `.scratch/model-pricing-mvp/real-test-authorization.md`（21 行），不影响受审代码；未以该授权扩大本审核权限。
- dirty 固定清单：修改票05、`backend/app/services/tts.py`；新增 `evidence-05.md`、`backend/app/services/billing/audio_adapter.py`、`backend/tests/test_audio_metering.py`、`frontend/tests/model-audio-metering.test.tsx`。均已读取，未遗漏 untracked 应用/测试。审核未改 worktree、原 checkout、配置、服务或依赖。
- 规范来源：spec.md、api-spec.md、openapi.json 的音频/用量/订单契约、GLOSSARY.md、docs/agents 三份规范；docs/adr 不存在。使用 code-review 的规范/需求核对方式，遵守任务要求未再委派。

## 可复现问题：1 项

### [P2] PCM 尾部半帧被静默舍弃，非法 ASR 音频仍成功结算

定位：`backend/app/services/billing/audio_adapter.py:26-31`（问题中心为 29 行）。`wave.getnframes()` 将 data chunk 字节数整除每帧字节数；随后 `readframes(frames)` 只读取已取整的帧数，因此 `len(...) == frames*2` 不能证明整个 PCM data chunk 由完整 16-bit 帧组成。尾部 1 字节不会参与后续整数毫秒检查。

复现：正常 8000 帧/8000Hz/16bit/mono WAV，将 data chunk 从 16000 改成 16001 字节，附加尾部半采样帧并补 RIFF padding，更新 RIFF 长度。通过公共 `execute_metered` 发起 ASR，实际输出为：`accepted_frames=8000`、`accepted_duration_ms=1000`、`execution=SUCCEEDED`、`order=SETTLED`、`charged_credits=1.0000`、`submits=1`。它既接受了不完整 PCM 帧，也把带半帧的非法输入当作可核验整数毫秒，现有分数毫秒测试未覆盖此形态。

依据：票05第12行要求可核验毫秒来源，第14行要求无效音频可控验证；api-spec.md:98、101、103 要求原始实际音频毫秒、无中间取整、规格不符在调用前拒绝。evidence-05.md 声明本地协议为“完整帧且整数毫秒”。这是本地协议自检缺口，与厂商最低规格无关。

建议：在时长计算前验证完整 data chunk 字节数与实际可读字节数，拒绝不能按单帧字节数整除或截断的 PCM；不要只检查 `getnframes()` 已截断后的读取长度。新增 16001 字节 data chunk（含合法 RIFF pad）反例，预期 `PRICE_SPEC_MISMATCH`、零订单/预占/提交/扣额。

复现脚本：`D:/原力2222222222/model-pricing-work/review05_pcm_repro.py`。仅外部审核脚本，使用临时 SQLite 和隔离 Settings，实际运行 1/1，断言当前错误行为存在。

## 实际验证

- 原 checkout venv Python `-B` + worktree `backend/scripts/run_refinement_tests.py test_audio_metering.py test_tts.py`：21/21 通过，无 skipped；bootstrap/账务库均为临时 SQLite，env_file 禁用，初始化/恢复/workers 关闭。
- worktree 现有 Vitest：`tests/model-audio-metering.test.tsx` 8/8 通过，缓存写 TEMP。
- `git diff --check`：通过；起终 dirty 清单一致。
- 外部复现脚本：1/1，确认以上非法音频成功计费问题。未接触真实 DB/网络/存储/凭据；未执行提交。
- 检查用工具：PowerShell exec、git diff/status/log、Get-Content/rg、Python unittest、Vitest、apply_patch（仅外部脚本与本报告）。未对应用做修复；没有高危环境操作。

## 固定文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| backend/app/services/billing/audio_adapter.py | A9A9104966D2DD7DFEF3F6287A2977834373FFA736D7E2D0A97586B5402EF0D8 |
| backend/tests/test_audio_metering.py | FF3DCB9AB54B1AAB29E8A5DDF91CF312232647034B7234B84973C60065B8DF33 |
| frontend/tests/model-audio-metering.test.tsx | 2172236EB9CC6FA0FB17C177E9F8C3AE190CA6F54EAEA00BCB9D3A2F148B5AED |
| backend/app/services/tts.py | 97E3C40E24F76AC323EBB1BC59E438D1C099606837A0555244DAF66A80A8FC5C |
| .scratch/model-pricing-mvp/issues/05-audio-mock-conversion.md | A0F60DC725A8A27AE663168E767BC5BD9B103971174ACD23D771251712AC1623 |
| .scratch/model-pricing-mvp/evidence-05.md | 69AF9DCA8344706621DAF06CD61E3D267EDFA404BDD8F420AAF0C46317EC3CE6 |

## 结论与恢复入口

仅发现上述 1 个本地 PCM 契约问题；其余已审路径未发现可复现实质缺陷。TTS 原文本计量、ASR 完整帧的整数毫秒、折算/快照、失败与未知预占、局部结果、清理重试、旧 TTS guard、查询/UI 断言在现有离线范围内通过。离线 SQLite 不能代替 PostgreSQL 事务/锁验证，PG 待主线程集中执行。下一步：实施者修复半帧边界后重跑该反例与现有音频检查，再由主线程作 PG 验证。本审核没有应用改动需要回退；外部脚本/报告可保留作审计证据。
