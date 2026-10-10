# Codex 上下文感知视频修复设计方案

> 文档状态：设计稿，可进入分阶段实施
> 适用范围：审核失败后的局部视频修复
> 基于版本：`repair_plan.v2` / 当前 Video Repair V3 实现
> 目标版本：`repair_plan.v3` / Video Repair V4

## 1. 背景与问题

当前系统已经具备以下基础能力：

- 根据审核失败原因选择修复动作；
- 对素材进行硬约束过滤和候选排序；
- 在审批时冻结候选素材与修复方案；
- 使用确定性的 FFmpeg 参数执行局部替换；
- 保留原视频并生成新的版本。

但当前方案仍有三个关键缺口：

1. 候选素材主要按角色、时长、画幅和关键词排序，无法充分证明其与目标片段前后语义连续；
2. “源视频存在烧录字幕”曾被当作整条视频级阻断条件，过于保守；完全忽略该问题又会造成字幕断裂、重复或语义冲突；
3. 审批人只能看原片和候选素材，不能先看到真实拼接后的局部结果，风险判断缺少直接证据。

因此，本方案不承诺通过算法“保证”替换后一定连贯，而是建立一套可解释、可预览、可回退的控制链：

```text
目标问题定位
  -> 前后文建模
  -> 候选硬过滤
  -> 连续性评分与证据
  -> 字幕策略选择
  -> 边界调整
  -> 局部预览
  -> 人工确认与风险接受
  -> 冻结方案
  -> 完整渲染与验证
```

## 2. 设计目标

### 2.1 必须达到

- 对目标时间段分别判断字幕风险，而不是用整条视频是否存在烧录字幕来一刀切；
- 使用目标片段前后文评估候选的语义承接能力；
- 将“为什么推荐这个候选”以评分明细和证据展示给审核人；
- 低置信候选不自动选中；
- 支持目标区间字幕重建，并且只覆盖修复区间；
- 最终审批前可以生成包含前后文的局部拼接预览；
- 草稿发生任何实质变化后，旧预览立即失效；
- 审批冻结候选、字幕策略、有效区间、预览和已接受风险；
- 继续遵守确定性执行原则，模型不能直接生成或执行 FFmpeg 命令。

### 2.2 暂不包含

- 自动重写整条视频的文案或结构；
- 自动生成新的真人口播；
- 自动修复全片所有烧录字幕；
- 用生成式视频补帧或生成不存在的商品动作；
- 在没有人工确认的情况下自动提交审核或覆盖原视频；
- 第一阶段直接实现复杂的字幕区域修复、视频内容生成或逐像素 inpaint。

## 3. 核心设计决策

### 3.1 保留内部动作类型，扩展动作参数

继续使用现有动作语义：

- `REPLACE_VISUAL_KEEP_AUDIO`
- `REPLACE_AV`
- `REPLACE_PROCESS_SEGMENT`

不为每种字幕处理方式增加新的修复动作。字幕是动作的一个正交策略，保存在 `subtitle_strategy` 中。这样可以避免动作数量组合爆炸，也能保持现有渲染器结构稳定。

### 3.2 区分请求区间和实际执行区间

- `requested_range`：审核问题标出的原始区间，不允许被修改或丢失；
- `effective_range`：根据镜头切点、字幕周期和音频边界计算出的实际替换区间；
- `boundary_adjustment`：记录为何扩展或收缩，以及相对原区间的偏移。

默认只能向外扩展，且必须完整覆盖 `requested_range`。未经明确确认，不允许为了贴合镜头而缩短到漏掉审核问题。

### 3.3 预览拥有独立生命周期

任务仍保持现有主状态，不新增 `PREVIEW_RENDERING` 等任务状态。局部预览使用独立实体和状态：

- `QUEUED`
- `RENDERING`
- `READY`
- `FAILED`
- `SUPERSEDED`
- `EXPIRED`

这样不会因为预览失败或过期破坏任务本身的状态机，也允许一个任务保留最近几次预览的审计记录。

### 3.4 字幕判断以目标时间段为单位

“视频中出现过烧录字幕”不再构成阻断。系统只评估：

- 目标区间是否存在烧录字幕；
- 字幕是否跨越替换边界；
- 替换动作是否保留原音频；
- 候选素材是否自身带有冲突字幕；
- 是否有足够可信的文本可以重建目标区间字幕。

### 3.5 低置信候选不得静默自动通过

系统可以自动推荐，但不能把低分候选伪装成已确定方案。建议默认阈值：

| 总分 | 默认行为 |
| --- | --- |
| `>= 0.75` | 自动标为推荐候选，但仍需预览和人工批准 |
| `0.60–0.75` | 可选，显示黄色连续性风险，不自动选中 |
| `< 0.60` | 默认不可审批；有权限的用户可填写原因后强制选择 |

阈值必须配置化，并通过真实修复结果逐步校准。

## 4. 端到端流程

### 4.1 创建任务

1. 保存审核失败事实和原始目标区间；
2. 提取目标前后各 3–5 秒上下文；
3. 识别目标区间的镜头边界、音频边界和字幕风险；
4. 根据问题类型确定允许的修复动作；
5. 搜索并排序候选素材；
6. 仅在最佳候选达到推荐阈值时，将其标记为 `recommended_candidate_id`；
7. 不再把“第一个候选”自动写成 `selected_candidate_id`。

### 4.2 编辑草稿

审核人可以修改：

- 修复动作；
- 候选素材；
- 实际替换边界；
- 字幕策略；
- 目标字幕文本；
- 必要的风险接受项。

任何会改变输出画面的字段修改，都必须：

1. 增加任务版本号或更新时间；
2. 重新计算 `plan_hash`；
3. 将原 `READY` 预览标记为 `SUPERSEDED`；
4. 禁止使用旧预览审批新草稿。

### 4.3 生成局部预览

预览包含：

- 修复区间前 3 秒；
- 完整修复区间；
- 修复区间后 3 秒；
- 实际音频切换、转场和字幕处理结果。

若原片边界不足，则截取到视频起点或终点。预览使用与最终完整渲染相同的 patch 构建逻辑和核心编码参数，避免“预览能看、正式渲染不同”的双实现问题。

### 4.4 审批与冻结

默认审批前置条件：

- 当前草稿存在有效候选；
- 当前候选仍满足租户、产品、可复用和血缘约束；
- 当前 `plan_hash` 对应的预览状态为 `READY`；
- 字幕策略已明确，不允许为空；
- 所有警告级风险均被逐项接受；
- 低于阈值的强制选择包含人工原因；
- 乐观锁 `updated_at` 或版本号匹配。

审批后冻结完整快照，不再从实时素材记录重新推导已批准内容。

## 5. 上下文模型

新增 `repair_context.v1`，作为诊断、候选排序、字幕策略和审批解释的统一输入。

```json
{
  "schema_version": "repair_context.v1",
  "requested_range": {"start_ms": 10000, "end_ms": 13500},
  "context_window": {
    "pre": {"start_ms": 6000, "end_ms": 10000},
    "target": {"start_ms": 10000, "end_ms": 13500},
    "post": {"start_ms": 13500, "end_ms": 17500}
  },
  "narrative": {
    "pre_summary": "展示皮肤干燥和卡粉问题",
    "target_intent": "演示产品上脸后的遮瑕效果",
    "post_summary": "展示妆后近景并给出结论",
    "entities": ["模特", "面霜"],
    "actions": ["上脸", "涂抹", "近景展示"],
    "required_bridge": "候选需要承接问题展示并自然进入效果展示"
  },
  "visual": {
    "shot_ids": ["shot-03", "shot-04"],
    "entry_frame_hash": "...",
    "exit_frame_hash": "...",
    "dominant_subject": "face",
    "camera_motion": "static_closeup",
    "aspect_ratio": "9:16"
  },
  "audio": {
    "has_speech": true,
    "transcript_before": "...",
    "transcript_target": "...",
    "transcript_after": "...",
    "sentence_boundary_ms": [9800, 13620]
  },
  "subtitle": {
    "target_has_burned_subtitles": true,
    "candidate_has_burned_subtitles": false,
    "crosses_entry_boundary": false,
    "crosses_exit_boundary": true,
    "region": {"x": 0.08, "y": 0.76, "w": 0.84, "h": 0.16},
    "ocr_text": "...",
    "ocr_confidence": 0.91,
    "asr_consistency": 0.87
  },
  "evidence": []
}
```

### 5.1 证据来源优先级

按以下顺序使用已有数据，缺失时逐级降级：

1. 已确认的脚本、分镜和结构化字幕；
2. 素材分析结果、镜头标签和人工描述；
3. ASR 文本；
4. OCR 文本；
5. 视觉模型生成的摘要；
6. 文件名和标签关键词。

模型生成的摘要只能作为评分证据，不能单独作为硬事实。所有自动推断字段必须带 `source` 和 `confidence`。

### 5.2 上下文窗口

默认：

- 前文：4 秒；
- 后文：4 秒；
- 最小：2 秒；
- 最大：8 秒；
- 若附近存在句子或镜头边界，优先扩展到完整边界，但不得超过最大值。

对于时长小于 15 秒的短视频，可按视频总长的 25% 自适应缩短，避免上下文覆盖整条视频。

## 6. 候选筛选与连续性评分

### 6.1 硬约束

以下条件不满足时，候选直接排除，不进入评分：

- 同租户、同产品或明确允许跨产品复用；
- 素材状态允许复用且分析已完成；
- 不属于目标版本的后代，避免循环血缘；
- 时长可以覆盖 `effective_range`；
- 动作需要音频时，候选必须具备有效音轨；
- 画幅和最小分辨率符合输出要求；
- 文件哈希和对象地址仍有效；
- 候选自身不存在不可接受的审核风险；
- `REPLACE_PROCESS_SEGMENT` 的素材角色符合过程演示要求。

### 6.2 评分模型

候选总分以可解释分项组成：

```text
continuity_score =
    0.22 * target_intent_match
  + 0.16 * pre_context_match
  + 0.16 * post_context_match
  + 0.12 * entity_action_match
  + 0.10 * visual_entry_match
  + 0.08 * visual_exit_match
  + 0.06 * duration_fit
  + 0.05 * source_reliability
  + 0.05 * quality_score
  - risk_penalty
```

`risk_penalty` 建议范围为 `0–0.30`，包含：

- 候选自身烧录字幕冲突；
- 人物、商品、场景明显变化；
- 动作方向或镜头运动方向冲突；
- 音频句子被截断；
- 候选来源置信度过低；
- 候选曾导致相同审核问题。

### 6.3 评分证据返回

前端不得只展示一个总分。每个候选至少返回：

```json
{
  "candidate_id": "asset-123:h02",
  "score": 0.82,
  "recommendation_level": "RECOMMENDED",
  "score_breakdown": {
    "target_intent_match": 0.93,
    "pre_context_match": 0.81,
    "post_context_match": 0.77,
    "entity_action_match": 0.88,
    "visual_entry_match": 0.69,
    "visual_exit_match": 0.71,
    "duration_fit": 1.0,
    "source_reliability": 0.9,
    "quality_score": 0.84,
    "risk_penalty": 0.06
  },
  "reasons": [
    "候选动作与目标意图一致",
    "能够从卡粉问题自然过渡到上脸演示"
  ],
  "warnings": ["人物近景角度与后续镜头存在轻微差异"]
}
```

### 6.4 模型职责边界

AI 可以：

- 摘要前文、目标和后文；
- 识别实体、动作和叙事意图；
- 给出语义分项分数及理由；
- 判断候选是否形成合理的叙事桥梁。

AI 不可以：

- 绕过硬约束；
- 直接选择低分候选并审批；
- 直接生成 FFmpeg 命令；
- 伪造不存在的字幕文本、素材元数据或审核事实；
- 在没有确定证据时宣称“保证连贯”。

AI 不可用或调用失败时，系统降级为规则评分，并明确显示“未执行语义模型评估”。降级结果最高只能达到 `SELECTABLE_WITH_WARNING`，不能自动推荐。

## 7. 边界调整

### 7.1 调整优先级

优先顺序如下：

1. 完整覆盖原始审核问题区间；
2. 避免切断一个字幕句；
3. 避免切断一个语音句；
4. 对齐最近镜头切点；
5. 控制替换区间不要无必要地扩大。

默认允许在目标两侧各扩展最多 500ms；若要超过 500ms，必须展示为人工可见警告；超过 1500ms 时必须手动确认有效区间。

### 7.2 边界结果

```json
{
  "requested_range": {"start_ms": 10000, "end_ms": 13500},
  "effective_range": {"start_ms": 9800, "end_ms": 13620},
  "boundary_adjustment": {
    "start_delta_ms": -200,
    "end_delta_ms": 120,
    "reasons": ["对齐语音句边界", "避免截断字幕"]
  }
}
```

## 8. 字幕策略

定义四种策略：

### 8.1 `NONE`

适用条件：

- 目标区间没有烧录字幕；
- 候选素材也没有会冲突的烧录字幕；
- 替换后不会形成明显字幕断句。

如果检测到目标区间存在高置信烧录字幕，不能默认使用 `NONE`。

### 8.2 `REBUILD_TARGET`

推荐策略。适用于目标区间有烧录字幕，且能够获得可信字幕文本的情况。

执行方式：

1. 从结构化字幕、脚本、ASR 和 OCR 中确定目标区间文本；
2. 删除目标画面原有字幕的影响，即替换画面本身不再保留原字幕；
3. 仅在 `effective_range` 创建新的字幕覆盖轨；
4. 字体、大小、描边、位置尽量匹配相邻原字幕；
5. 新字幕时间轴按语音词级时间或句级时间重建；
6. 在局部预览中同时展示进入和退出边界。

字幕文本来源优先级：人工输入 > 已确认脚本 > 结构化字幕 > 高置信 ASR > OCR。ASR 与 OCR 明显冲突时不得自动生成，必须人工确认文本。

### 8.3 `PRESERVE_BAND`

保留原画面底部字幕带，只替换字幕带以上的视觉区域。

仅在以下条件同时满足时开放：

- 修复动作是 `REPLACE_VISUAL_KEEP_AUDIO`；
- 字幕区域位置稳定；
- 字幕带高度不超过画面高度的 25%；
- 目标内容不依赖字幕带内的重要商品或人物画面；
- 上下区域拼接不会形成明显几何错位；
- 局部预览通过人工确认。

该策略技术风险较高，建议放在第四阶段作为实验能力，不作为首期默认方案。

### 8.4 `IGNORE_WITH_WARNING`

允许不处理已识别的烧录字幕风险，但必须：

- 显示具体风险，例如字幕消失、句子中断或样式跳变；
- 由有权限的用户显式勾选接受；
- 填写原因；
- 将风险接受写入审批快照和审计日志。

该策略永远不能作为自动默认值。

### 8.5 候选自身带字幕

候选素材自身带有不可去除且与目标文案不同的烧录字幕时，应默认排除。只有字幕文本、时间和目标语义一致，且样式可接受时，才可降级为警告候选。不能通过再覆盖一层新字幕来掩盖冲突字幕。

## 9. 修复计划契约

新增 `repair_plan.v3`：

```json
{
  "schema_version": "repair_plan.v3",
  "action_type": "REPLACE_VISUAL_KEEP_AUDIO",
  "requested_range": {"start_ms": 10000, "end_ms": 13500},
  "effective_range": {"start_ms": 9800, "end_ms": 13620},
  "boundary_adjustment": {
    "start_delta_ms": -200,
    "end_delta_ms": 120,
    "reasons": ["对齐语音句边界"]
  },
  "recommended_candidate_id": "asset-123:h02",
  "selected_candidate_id": "asset-123:h02",
  "selected_candidate_snapshot": null,
  "candidate_score": {
    "score": 0.82,
    "recommendation_level": "RECOMMENDED",
    "score_breakdown": {}
  },
  "subtitle_strategy": {
    "type": "REBUILD_TARGET",
    "source": "CONFIRMED_SCRIPT",
    "segments": [
      {"start_ms": 10000, "end_ms": 11800, "text": "..."},
      {"start_ms": 11800, "end_ms": 13500, "text": "..."}
    ],
    "style_reference": "pre_target_subtitle_style"
  },
  "continuity_assessment": {
    "summary": "候选承接前文问题并进入效果展示",
    "warnings": []
  },
  "accepted_risks": [],
  "preview_requirement": {
    "required": true,
    "current_preview_id": null,
    "current_preview_plan_hash": null
  },
  "predicted_effects": [],
  "plan_hash": "sha256:..."
}
```

### 9.1 `plan_hash`

`plan_hash` 必须由规范化后的以下字段计算：

- 基础视频版本和文件哈希；
- 动作类型；
- `effective_range`；
- 候选素材 ID、文件哈希和候选截取范围；
- 音频策略；
- 字幕策略、字幕文本和样式快照；
- 转场和编码关键参数；
- 修复计划 schema 版本。

候选描述、界面折叠状态等不影响输出的字段不得进入哈希。

## 10. 局部预览

### 10.1 数据模型

建议新增 `video_repair_previews` 表，而不是把预览生命周期继续塞入任务 JSON：

| 字段 | 含义 |
| --- | --- |
| `id` | 预览 ID |
| `task_id` | 所属修复任务 |
| `plan_hash` | 生成时的计划哈希 |
| `status` | 预览状态 |
| `object_key` / `object_url` | 预览文件位置 |
| `window_start_ms` / `window_end_ms` | 预览对应原视频窗口 |
| `duration_ms` | 输出时长 |
| `error_code` / `error_message` | 失败信息 |
| `created_at` / `updated_at` | 审计时间 |
| `expires_at` | 过期时间，默认 24 小时 |

预览文件不是 `VideoAsset`，不进入素材候选池，也不形成视频版本血缘。

### 10.2 渲染规则

- 默认窗口：`effective_range` 前后各 3 秒；
- 最大预览时长：15 秒；
- 与正式渲染复用同一个 patch builder；
- 可以使用较低码率加快预览，但分辨率、画幅、字幕布局和音频时序必须一致；
- 预览上可加入非侵入式“局部预览”水印；
- 成功后运行媒体探测，验证时长、音视频流和解码完整性；
- 同一 `task_id + plan_hash` 已有 READY 结果时直接复用；
- 同一任务新计划生成后，旧结果标为 `SUPERSEDED`，文件按 TTL 清理。

### 10.3 审批一致性

审批请求必须提交：

- `updated_at` 或 `version`；
- `preview_id`；
- `plan_hash`；
- `accepted_risks`；
- 低置信强制选择时的原因。

后端重新计算当前计划哈希，并验证与预览一致。前端传入的哈希只能用于冲突提示，不能作为可信值。

## 11. API 设计

### 11.1 更新草稿

`PATCH /video-repair-tasks/{task_id}/draft-plan`

扩展请求字段：

```json
{
  "updated_at": "...",
  "action_type": "REPLACE_VISUAL_KEEP_AUDIO",
  "selected_candidate_id": "asset-123:h02",
  "effective_range": {"start_ms": 9800, "end_ms": 13620},
  "subtitle_strategy": {
    "type": "REBUILD_TARGET",
    "segments": []
  }
}
```

返回当前计划、候选评分明细、警告和预览失效状态。

### 11.2 生成预览

`POST /video-repair-tasks/{task_id}/previews`

```json
{
  "updated_at": "..."
}
```

返回 `202 Accepted` 和预览记录。渲染较快也仍走统一异步状态，避免接口超时和重复提交。

### 11.3 查询预览

- `GET /video-repair-tasks/{task_id}/previews/{preview_id}`
- `GET /video-repair-tasks/{task_id}/previews?current=true`

### 11.4 审批

`POST /video-repair-tasks/{task_id}/approve`

```json
{
  "updated_at": "...",
  "preview_id": "preview-456",
  "plan_hash": "sha256:...",
  "accepted_risks": [
    {
      "code": "VISUAL_ANGLE_MINOR_MISMATCH",
      "reason": "局部预览可接受",
      "accepted_by": "user-id"
    }
  ],
  "override_reason": null
}
```

### 11.5 错误码

新增稳定错误码：

- `REPAIR_CANDIDATE_SCORE_TOO_LOW`
- `REPAIR_SUBTITLE_STRATEGY_REQUIRED`
- `REPAIR_SUBTITLE_TEXT_UNCERTAIN`
- `REPAIR_PREVIEW_REQUIRED`
- `REPAIR_PREVIEW_NOT_READY`
- `REPAIR_PREVIEW_STALE`
- `REPAIR_PLAN_VERSION_CONFLICT`
- `REPAIR_RISK_NOT_ACCEPTED`
- `REPAIR_BOUNDARY_OUT_OF_LIMIT`

## 12. 状态与不变量

任务主状态继续沿用现有状态机。新增规则：

- 候选不足：进入 `NEED_MORE_MATERIAL`；
- 有可选候选但未选中：保持可编辑状态，不允许审批；
- 预览渲染中：任务仍保持当前可编辑状态，但界面提示预览进度；
- 预览失败：不改变任务状态，禁止审批并允许重试；
- 草稿变化：READY 预览变为 SUPERSEDED；
- 审批成功：冻结 `repair_plan.v3`、候选快照、预览快照和风险接受记录；
- 审批后执行只读取冻结快照；
- 正式渲染失败不能回写或污染原始视频版本。

必须始终成立的不变量：

1. `effective_range` 完整覆盖 `requested_range`；
2. 当前预览的 `plan_hash` 与待审批计划一致；
3. 候选快照哈希与实际输入文件一致；
4. 未被接受的警告不能静默消失；
5. 字幕覆盖轨只在配置区间内生效；
6. 输出永远创建新版本，不覆盖 V1；
7. 模型输出不能越过确定性规则层直接进入执行器。

## 13. 后端改造点

建议按职责拆分：

### 13.1 新增模块

- `backend/app/services/video_repair/context_window.py`
  - 构造前文、目标、后文窗口；
  - 汇总 ASR、OCR、镜头和脚本证据。
- `backend/app/services/video_repair/continuity.py`
  - 规则评分；
  - AI 语义评分适配；
  - 阈值和推荐等级。
- `backend/app/services/video_repair/subtitle_strategy.py`
  - 目标区间字幕风险判断；
  - 策略校验；
  - 字幕覆盖轨构建。
- `backend/app/services/video_repair/preview.py`
  - 计划哈希；
  - 预览记录生命周期；
  - 局部窗口渲染和探测。

### 13.2 修改模块

- `candidates.py`
  - 保留硬约束；
  - 替换当前简单语义加权为可解释连续性评分；
  - 不再让首个候选等同于已选候选。
- `planner.py`
  - 生成 `repair_plan.v3`；
  - 计算有效边界；
  - 写入字幕策略和风险。
- `service.py`
  - 草稿变化使预览失效；
  - 审批校验预览和计划哈希；
  - 冻结新增快照。
- `schemas.py`
  - 扩展草稿更新、候选评分、预览和审批请求模型。
- API 路由
  - 增加预览创建和查询接口；
  - 返回稳定错误码。
- 渲染器和 `plan_patch`
  - 支持局部窗口预览；
  - 支持目标区间字幕覆盖轨；
  - 继续通过白名单参数构造 FFmpeg 图。

### 13.3 数据库

新增预览表和索引：

- `index(task_id, created_at)`；
- `unique(task_id, plan_hash)`，若需要允许失败后重试，可改为逻辑唯一或增加 attempt；
- `index(status, expires_at)`，用于后台清理。

任务表中的 JSON 字段继续保存上下文与计划，因此 `repair_context.v1` 和 `repair_plan.v3` 本身不要求拆成大量关系表。

## 14. 前端工作台改造

页面按以下顺序组织，避免让用户先选素材、最后才看到关键风险：

1. 审核失败事实与原始时间段；
2. 前文—目标—后文摘要；
3. 建议有效边界及调整原因；
4. 候选列表、总分、分项证据和警告；
5. 字幕风险与字幕策略；
6. 局部拼接预览；
7. 风险接受和审批。

候选卡片需要区分：

- “系统推荐”而不是“系统已选择”；
- “可选但有风险”；
- “低于阈值，需强制选择”；
- “已排除”，并展示排除原因。

预览区域必须显示：

- 当前预览是否与草稿一致；
- 预览生成时间；
- 预览窗口范围；
- 字幕策略；
- 草稿变化后的“预览已失效”状态。

## 15. 配置建议

```env
VIDEO_REPAIR_CONTEXT_PRE_MS=4000
VIDEO_REPAIR_CONTEXT_POST_MS=4000
VIDEO_REPAIR_CONTEXT_MAX_MS=8000
VIDEO_REPAIR_RECOMMEND_SCORE=0.75
VIDEO_REPAIR_SELECTABLE_SCORE=0.60
VIDEO_REPAIR_PREVIEW_REQUIRED=true
VIDEO_REPAIR_PREVIEW_PADDING_MS=3000
VIDEO_REPAIR_PREVIEW_MAX_DURATION_MS=15000
VIDEO_REPAIR_PREVIEW_TTL_HOURS=24
VIDEO_REPAIR_BOUNDARY_AUTO_ADJUST_MS=500
VIDEO_REPAIR_BOUNDARY_CONFIRM_MS=1500
VIDEO_REPAIR_PRESERVE_BAND_ENABLED=false
```

生产环境中不建议提供全局“跳过字幕校验”开关。若确有应急需求，应使用带审计的单任务 override，而不是静默关闭保护。

## 16. 兼容与迁移

- 已批准的 `repair_plan.v2` 继续按冻结快照执行，不强制升级；
- 尚未批准的 v2 任务在下一次编辑或刷新候选时升级为 v3；
- 升级后 `selected_candidate_id` 不自动继承为批准选择，仍可显示为“历史选择”；
- v2 任务没有预览快照，升级后首次审批必须生成新预览；
- 前端读取时按 `schema_version` 兼容展示；
- 当前已移除的烧录字幕硬阻断视为临时过渡，不作为最终策略。

## 17. 安全、审计与可观测性

### 17.1 安全

- 所有任务、候选、预览和对象地址必须校验租户边界；
- 预览 URL 使用短期签名；
- FFmpeg 参数来自白名单模板；
- 用户输入字幕文本进行长度、字符和样式参数校验；
- 不允许模型输出路径、命令或对象地址进入执行器。

### 17.2 审计

记录：

- 自动推荐候选及分数；
- 用户最终选择和与推荐不同的原因；
- 字幕策略及文本来源；
- 边界调整；
- 预览 ID 和计划哈希；
- 所有风险接受；
- 强制选择和权限主体；
- 正式渲染使用的冻结快照。

### 17.3 指标

至少监控：

- 候选推荐率、无候选率和低分候选率；
- 用户更换推荐候选的比例；
- 各字幕策略使用比例；
- 预览失败率、平均生成耗时和重复生成次数；
- 因草稿变化导致的预览失效率；
- 低分强制选择率；
- 正式渲染失败率；
- 修复后再次审核失败率，并按动作和字幕策略拆分。

## 18. 测试方案

### 18.1 单元测试

- 上下文窗口在视频首尾的截断；
- `effective_range` 始终覆盖 `requested_range`；
- 镜头、句子、字幕边界的优先级；
- 候选硬约束和各分项权重；
- 0.60、0.75 阈值边界；
- AI 失败后的规则降级；
- 四种字幕策略的允许和拒绝条件；
- ASR/OCR 冲突时禁止自动重建；
- `plan_hash` 稳定性和字段敏感性；
- 草稿修改后预览失效；
- 旧预览不能审批新计划。

### 18.2 API 测试

- 草稿更新的乐观锁冲突；
- 并发生成相同计划预览时去重；
- 跨租户候选和预览访问被拒绝；
- 未生成预览、预览失败和预览过期时审批失败；
- 警告未接受时审批失败；
- 低分候选无 override 原因时审批失败；
- 审批快照字段完整且不可被后续素材变更污染。

### 18.3 真实媒体集成测试

至少覆盖：

1. 无字幕，保留原音频，替换视觉；
2. 目标有字幕，使用 `REBUILD_TARGET`；
3. 字幕跨越替换边界，边界自动扩展；
4. 候选自身带冲突字幕，被排除；
5. 替换音视频，验证音频切点和响度；
6. 过程片段替换，验证动作前后逻辑；
7. 预览与完整渲染目标窗口逐帧/时间轴一致；
8. 视频首尾修复，不出现负时间或越界；
9. 可变帧率和不同音频采样率素材；
10. 渲染失败后原版本保持不变。

### 18.4 前端测试

- 推荐候选不会被误显示为已选择；
- 分项评分、排除原因和警告可见；
- 字幕策略切换会使预览失效；
- 预览未完成时审批按钮不可用；
- 风险接受操作可审计且不会被默认勾选；
- 并发更新返回冲突后不会覆盖用户新选择。

## 19. 分阶段实施

### 阶段一：连续性评分与选择安全

- 新增 `repair_context.v1`；
- 改造候选评分和评分明细；
- 取消首候选自动选中；
- 增加推荐阈值和低分 override；
- 前端展示上下文、评分和警告。

这一阶段不修改最终渲染能力，风险最小，可以先验证候选质量。

### 阶段二：区间级字幕策略

- 实现目标区间字幕检测和边界判断；
- 实现 `NONE`、`REBUILD_TARGET`、`IGNORE_WITH_WARNING`；
- 增加目标区间字幕覆盖轨；
- 增加字幕文本人工编辑和来源标记；
- 移除临时的无条件字幕放行逻辑。

### 阶段三：局部预览与审批绑定

- 新增预览表和异步任务；
- 复用 patch builder 生成局部预览；
- 实现 plan hash 和预览失效；
- 审批强制绑定 READY 预览；
- 增加 TTL 清理和指标。

### 阶段四：实验能力

- 灰度开放 `PRESERVE_BAND`；
- 根据实际数据校准权重和阈值；
- 增加更精细的镜头进入/退出视觉连续性模型；
- 评估自动字幕样式匹配，但仍保留人工预览。

## 20. 验收场景

### 场景 A：原视频其他位置有字幕，目标区间无字幕

- 不阻断；
- 字幕策略自动建议 `NONE`；
- 候选通过连续性评估后可生成预览并审批。

### 场景 B：目标区间有字幕，保留原音频

- 系统建议 `REBUILD_TARGET`；
- 使用原音频的 ASR/脚本重建目标区间字幕；
- 预览展示前后字幕衔接；
- 审批后只在修复区间生成新字幕轨。

### 场景 C：字幕跨越结束边界

- 系统将结束边界扩展至字幕或句子结束；
- 显示调整量和原因；
- 超过阈值时要求人工确认。

### 场景 D：候选画面相关但叙事不连续

- 商品和画幅硬约束通过；
- 后文承接分低，总分低于推荐阈值；
- 系统不自动选中；
- 用户强制选择时必须说明原因并预览。

### 场景 E：候选自身有冲突烧录字幕

- 候选默认被排除；
- 不允许通过叠加新字幕掩盖；
- 系统继续寻找干净素材或进入 `NEED_MORE_MATERIAL`。

## 21. 完成定义

满足以下条件才认为本方案完成：

- 候选推荐具有前文、目标、后文的可解释评分；
- 低分候选不会被默认选中或静默审批；
- 字幕风险按目标区间判断；
- `REBUILD_TARGET` 能在真实视频上生成只覆盖修复区间的字幕；
- 局部预览与正式渲染复用同一计划和 patch 构建逻辑；
- 草稿变化后旧预览不可继续审批；
- 审批快照包含候选、区间、字幕、预览和风险接受；
- 所有核心规则、接口和真实媒体路径通过测试；
- 原始视频版本永不被覆盖；
- 生产环境不依赖全局关闭字幕校验才能工作。

## 22. 推荐实施顺序结论

不建议立即实现“保留字幕带”这种视觉合成难度较高的方案。优先顺序应为：

1. 先修正候选自动选择逻辑，并建立上下文连续性评分；
2. 再实现区间级字幕判断和 `REBUILD_TARGET`；
3. 然后用局部预览把计划、字幕和真实拼接结果绑定到审批；
4. 最后基于真实数据决定是否值得实现 `PRESERVE_BAND`。

这条路径能先解决“素材选错”和“字幕被一刀切阻断”两个主要问题，同时把最复杂、最容易产生画面瑕疵的能力留到有数据证明其必要性之后。
