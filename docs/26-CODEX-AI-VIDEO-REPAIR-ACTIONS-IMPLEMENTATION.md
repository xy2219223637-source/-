# CODEX AI 审核失败修改：判断、方案与执行实现规格

> 状态：待实施
> 目标版本：Video Repair V3（受约束 AI 决策闭环）
> 面向读者：Codex、后端开发、前端开发、测试
> 前置文档：`docs/25-CODEX-STANDALONE-VIDEO-REPAIR-MODULE.md`
> 核心范围：AI 判断问题、系统生成可执行方案、人工确认、渲染 V2 结果

本文档是一份可直接交给 Codex 执行的实现规格。除非本文件明确要求，否则不得重写已经完成的独立视频修复模块，也不得重新引入千川 Mock 依赖。

---

## 1. 目标与最小闭环

本期只实现以下闭环：

```text
选择一个素材库中的失败成片 V1
        ↓
输入抽象、非固定格式的审核退回理由
        ↓
多模态 AI 阅读审核理由、视频画面、ASR、OCR/字幕和时间轴
        ↓
输出结构化问题判断与问题时间段
        ↓
系统根据已实现能力匹配可执行动作和候选素材
        ↓
人工确认或调整动作、时间段、候选素材
        ↓
冻结 approved_repair_plan
        ↓
确定性时间轴引擎执行并渲染
        ↓
生成新的 V2，V1 保持不变
```

本期不实现额外体验功能，不实现自动复审、自动投流、复杂时间轴编辑器、开放式 AI 剪辑，也不要求生成多个预览版本。

### 1.1 核心原则

1. AI 负责理解和决策建议，不直接操作 FFmpeg，也不直接修改数据库时间轴。
2. 系统能力注册表决定“哪些动作允许执行”，AI 不得发明新动作。
3. 人工确认后冻结执行快照；执行阶段不得再次调用 AI 改变方案。
4. 每个动作必须包含合法时间段、候选素材和明确的轨道影响。
5. 无法可靠判断、没有候选素材或系统能力不足时，返回不可自动修复，不得勉强执行。
6. 每次修复都生成新 V2，不覆盖 V1，不自动上传或重新投流。

---

## 2. 名称约定

产品讨论中的动作名称与当前代码名称存在差异。为避免无价值的破坏性迁移，本期继续使用当前代码中的内部枚举：

| 业务名称 | 当前代码内部枚举 | 含义 |
|---|---|---|
| `REPLACE_VISUAL` | `VISUAL_REPLACE` | 只替换目标区间画面，保留原音频与可编辑字幕 |
| `REPLACE_AV` | `AV_SEGMENT_REPLACE` | 同时替换目标区间画面和声音 |
| `INSERT_AV` | `PROCESS_INSERT` | 在指定切点插入一段完整音视频，后续时间轴顺延 |
| 转人工 | `NEED_MORE_MATERIAL` 或 `UNSUPPORTED` | 不执行自动修复，说明阻断原因 |

前端显示业务中文名称，API、数据库和时间轴引擎使用内部枚举。不得同时新增一套同义枚举。

---

## 3. 当前实现基线与本期改造边界

### 3.1 已存在并应复用

当前仓库已经具备：

- 独立“审核失败修改”入口、任务列表、创建页和工作区。
- `VideoRepairInput`、`VideoRepairTask`、V1/V2 与计划版本模型。
- `VISUAL_REPLACE`、`AV_SEGMENT_REPLACE`、`PROCESS_INSERT` 的时间轴补丁雏形。
- 候选素材检索、同租户/同商品限制和问题来源血缘排除。
- 人工选择候选、确认方案、冻结方案和幂等渲染。
- V2 新建、来源链路记录和后续素材分析。

相关代码：

```text
backend/app/services/video_repair/diagnosis.py
backend/app/services/video_repair/planner.py
backend/app/services/video_repair/candidates.py
backend/app/services/video_repair/service.py
backend/app/services/video_repair/rendering.py
backend/app/services/video_editing/plan_patch.py
frontend/src/pages/VideoRepairCreate.tsx
frontend/src/components/VideoRepairWorkbench.tsx
```

### 3.2 当前主要不足

1. `diagnosis.py` 依赖关键词规则，不能理解抽象、非固定的审核理由。
2. 问题描述中的“6-10秒”等时间表达尚未可靠提取。
3. 诊断、动作选择和素材匹配之间缺少统一的能力约束。
4. `planner.py` 默认只取第一个问题区间，无法表达多问题但本期可明确限制为单个主问题。
5. 候选素材只有基础角色、时长、比例评分，缺少“是否真的能解决当前问题”的语义验证。
6. `AV_SEGMENT_REPLACE` 当前要求素材有音频，但未严格校验等长替换和音频切点质量。
7. `PROCESS_INSERT` 会延长时间轴，但需要更严格地验证字幕、音轨和后续元素是否完整顺延。
8. 人工只能换候选素材，暂时不能修改 AI 时间段和动作。

### 3.3 本期明确不做

- 自动生成新口播或自由改写文案。
- 自动从混合音轨中分离背景音乐与人声。
- 自动修改烧录字幕。
- 任意多轨可视化剪辑器。
- 一次任务自动执行多个不同问题的复杂修复链。
- 自动接受低置信度诊断。
- 直接调用模型生成 FFmpeg 命令。

本期一个任务只执行一个主修复动作。若识别出多个问题，AI 应输出全部问题，但系统只为最高优先级且可执行的问题生成当前方案，并在诊断中记录其他问题。

---

## 4. 三种动作的业务形态与执行语义

## 4.1 `VISUAL_REPLACE`：只替换画面

### 适用条件

必须同时满足：

- 问题主要影响 `visual` 轨道。
- 原口播和原声音不需要删除。
- 原字幕内容正确，或字幕由独立可编辑字幕轨渲染。
- 候选素材和原视频属于同租户、同商品，并能覆盖目标时长。
- 候选素材不包含需要保留的自身声音；执行时忽略候选音频。

典型问题：

- 商品展示错误或与描述不一致。
- 局部风险画面、竞品、水印或误导性展示。
- 需要用真实商品特写替换问题镜头。

### 时间轴语义

```text
video_track:
  原 0..start + 候选画面 start..end + 原 end..duration

narrative/audio/caption:
  完整保留原时间轴

总时长：不变
```

### 默认约束

- 首版只允许等长替换。
- 候选有效时长必须大于或等于目标时长，允许误差 `50ms`。
- 禁止默认循环不足素材。
- 不做自动大幅变速；第一版直接裁切足够长的候选素材。
- 候选画面统一按项目比例 `cover` 或当前渲染器既有规则处理。
- 切点可以使用已有渲染器硬切；如渲染器支持转场，只允许固定短转场，不由 AI 自由指定。

### 阻断条件

- 原字幕烧录在被替换画面中且系统没有独立字幕轨可重建。
- 审核问题同时涉及口播、声音或字幕文本。
- 目标时间段未被现有 `video_track` 连续覆盖。
- 候选素材不足目标时长。

阻断时优先考虑 `AV_SEGMENT_REPLACE`，仍不可执行则转人工。

---

## 4.2 `AV_SEGMENT_REPLACE`：替换完整音视频段

### 适用条件

满足任一：

- 问题涉及口播或源音频。
- 画面、口播和字幕整体不匹配。
- 目标片段整体存在风险，局部保留没有价值。
- 缺少使用过程，但原视频中存在可牺牲的重复或低价值片段。

### 时间轴语义

```text
video_track:
  原 0..start + 候选画面 start..end + 原 end..duration

narrative/audio:
  原 0..start + 候选声音 start..end + 原 end..duration

caption:
  删除目标区间原字幕；仅在候选素材有结构化字幕时加入候选字幕

总时长：不变
```

### 默认约束

- 第一版只支持等长替换，不移动后续时间轴。
- 候选必须有可用音频轨。
- 候选有效时长必须大于或等于目标时长，允许误差 `50ms`。
- 统一音频采样率、声道和响度后再拼接。
- 在音频切点应用固定短淡入淡出，建议 `80-120ms`。
- 如果基础计划只有混合音频，不承诺保留原背景音乐。
- 如果基础计划有独立 BGM 轨，则保持 BGM 连续，仅替换叙事/源声音轨。

### 阻断条件

- 候选没有音频。
- 候选口播语义与修复目标不匹配。
- 候选含有新的审核风险。
- 目标区间未被画面轨和叙事轨同时完整覆盖。
- 候选时长不足且需要超出允许范围的变速或循环。

---

## 4.3 `PROCESS_INSERT`：插入完整音视频

### 适用条件

- 问题是缺少使用过程、证明过程或必要步骤。
- 原内容本身不错误，没有合适的低价值区间可替换。
- 人工接受总时长增加。
- 插入点是合法切点，或时间轴引擎能够安全切分该位置的片段。

### 时间轴语义

```text
原 0..insert_at
  + 候选完整音视频
  + 原 insert_at..duration 整体向后平移 candidate_duration

总时长：原时长 + 候选时长
```

必须同步平移：

- `video_track` 后续片段。
- `narrative_segments` 后续片段。
- `subtitle_words` 的开始与结束时间。
- 基础计划中其他具有 `timeline_range` 的受支持元素。
- 当前任务中尚未执行的后续问题时间段；本期单动作任务可不涉及该项。

### 默认约束

- 插入素材必须有音频。
- 不自动循环或拉长候选素材。
- 前端必须显示新增时长和修复后总时长。
- AI 应先判断是否存在可以等长替换的低价值区间；存在时优先使用 `AV_SEGMENT_REPLACE`。
- 如果基础计划存在当前补丁引擎无法平移的轨道或特效，禁止执行插入并转人工。

---

## 5. AI 判断架构

AI 判断不得直接返回一句自然语言结论。必须分为“上下文构建”“模型推理”“结构校验”“能力匹配”四步。

```text
VideoRepairInput
      +
视频结构化上下文（关键帧、ASR、OCR、字幕、时间轴、商品信息）
      ↓
MultimodalDiagnosisProvider
      ↓
video_repair_diagnosis.v2 JSON
      ↓
Pydantic/JSON Schema 校验 + 时间范围归一化
      ↓
Capability Registry 校验
      ↓
Repair Planner 生成 video_repair_plan.v2
```

### 5.1 新增文件

```text
backend/app/services/video_repair/ai_context.py
backend/app/services/video_repair/ai_provider.py
backend/app/services/video_repair/diagnosis_schema.py
backend/app/services/video_repair/capabilities.py
backend/app/services/video_repair/diagnosis_v2.py
```

职责：

- `ai_context.py`：从成片、计划、分析结果构建模型输入，不做决策。
- `ai_provider.py`：定义多模态模型接口和具体 provider 适配器。
- `diagnosis_schema.py`：定义严格诊断输出模型。
- `capabilities.py`：系统支持动作、轨道影响和前置条件的唯一事实源。
- `diagnosis_v2.py`：调用模型、校验结果、处理失败和输出规范化诊断。

不得把具体模型 SDK 调用散落到 `service.py`、`planner.py` 或 API 路由。

### 5.2 AI 输入上下文

模型输入至少包含：

```json
{
  "review": {
    "raw_reason": "6-10秒的商品描述不准确",
    "suggestions": [],
    "explicit_time_ranges": [],
    "category_hint": "UNKNOWN"
  },
  "video": {
    "duration_ms": 26300,
    "width": 1080,
    "height": 1920,
    "burned_subtitles": false
  },
  "timeline": {
    "narrative_segments": [],
    "video_segments": []
  },
  "observations": {
    "asr_segments": [],
    "ocr_segments": [],
    "keyframes": []
  },
  "product": {
    "product_id": 32,
    "known_facts": []
  },
  "capabilities": [
    "VISUAL_REPLACE",
    "AV_SEGMENT_REPLACE",
    "PROCESS_INSERT"
  ]
}
```

关键帧不应一次发送整条原视频的所有帧。优先使用现有视频分析结果；若缺少分析结果，则按镜头边界、字幕变化和固定间隔抽取受控数量的关键帧。

### 5.3 时间段提取优先级

时间定位按以下优先级合并：

1. 用户结构化填写的 `time_ranges`，标记 `EXACT / INPUT`。
2. 审核文本中的时间表达，例如：
   - `6-10s`
   - `6-10秒`
   - `6~10秒`
   - `6到10秒`
   - `00:06-00:10`
3. ASR 或字幕语义匹配结果。
4. OCR 与关键帧语义匹配结果。
5. 模型基于多模态证据给出的近似范围。
6. 都无法定位时返回空范围并要求人工确认；不得伪造全片精确问题。

必须新增一个确定性文本时间解析器，并在调用模型之前执行：

```text
backend/app/services/video_repair/time_extraction.py
```

模型可以补充时间定位，但不能覆盖用户明确输入的时间段。

### 5.4 AI 输出契约

建议新增严格模型：

```python
class Evidence(BaseModel):
    source: Literal["REVIEW_TEXT", "USER_INPUT", "ASR", "OCR", "FRAME", "TIMELINE", "PRODUCT_FACT"]
    start_ms: int | None = None
    end_ms: int | None = None
    description: str


class IssueRange(BaseModel):
    start_ms: int
    end_ms: int
    precision: Literal["EXACT", "APPROXIMATE"]
    source: Literal["INPUT", "TEXT_PARSE", "ASR", "OCR", "MULTIMODAL"]
    confidence: float


class DiagnosedIssue(BaseModel):
    issue_id: str
    category: Literal[
        "VISUAL_MISMATCH",
        "COPY_AUDIO_CAPTION_RISK",
        "AUDIO_VISUAL_RISK",
        "MISSING_PROCESS_EVIDENCE",
        "NON_VIDEO_COMPLIANCE",
        "UNKNOWN"
    ]
    description: str
    affected_modalities: list[Literal["visual", "audio", "caption", "full_video"]]
    ranges: list[IssueRange]
    evidence: list[Evidence]
    confidence: float
    suggested_action: Literal[
        "VISUAL_REPLACE",
        "AV_SEGMENT_REPLACE",
        "PROCESS_INSERT",
        "UNSUPPORTED"
    ]


class VideoRepairDiagnosisV2(BaseModel):
    schema_version: Literal["video_repair_diagnosis.v2"]
    summary: str
    issues: list[DiagnosedIssue]
    primary_issue_id: str | None
    requires_human_range_confirmation: bool
    overall_confidence: float
```

约束：

- 所有毫秒值必须满足 `0 <= start_ms < end_ms <= duration_ms`。
- `confidence` 必须在 `[0, 1]`。
- 每个结论至少有一条证据。
- `NON_VIDEO_COMPLIANCE` 只能映射为 `UNSUPPORTED`。
- 影响 `audio` 或 `caption` 的问题不得映射为 `VISUAL_REPLACE`。
- 没有问题时间段的局部动作必须设置 `requires_human_range_confirmation=true`。
- 模型输出解析失败时不得自动批准；任务进入 `NEEDS_CONFIRMATION` 并显示“AI诊断失败，需要人工指定”。

### 5.5 模型调用失败降级

保留当前确定性规则诊断作为降级能力，但改变其定位：

```text
多模态模型成功 → 使用 diagnosis.v2
多模态模型超时/不可用 → 使用规则诊断并标记 mode=RULE_FALLBACK
规则也无法可靠判断 → UNSUPPORTED，等待人工确认
```

禁止在模型不可用时将整段视频自动当成精确修复范围。

---

## 6. 系统能力注册表

新增 `capabilities.py`，作为 AI 能力提示、计划校验和审批校验的统一来源。

建议结构：

```python
REPAIR_CAPABILITIES = {
    "VISUAL_REPLACE": {
        "affected_tracks": ["video_track"],
        "duration_policy": "KEEP_TOTAL_DURATION",
        "requires_candidate": True,
        "requires_candidate_audio": False,
        "preserve_original_audio": True,
        "minimum_range_ms": 300,
    },
    "AV_SEGMENT_REPLACE": {
        "affected_tracks": ["video_track", "narrative_segments"],
        "duration_policy": "KEEP_TOTAL_DURATION",
        "requires_candidate": True,
        "requires_candidate_audio": True,
        "preserve_original_audio": False,
        "minimum_range_ms": 300,
    },
    "PROCESS_INSERT": {
        "affected_tracks": ["video_track", "narrative_segments", "subtitles"],
        "duration_policy": "EXTEND_TIMELINE",
        "requires_candidate": True,
        "requires_candidate_audio": True,
        "minimum_range_ms": 0,
    },
}
```

`planner.py` 不得用分散的 `if/elif` 独立定义另一套能力事实。所有计划必须先通过：

```python
validate_diagnosis_against_capabilities(diagnosis)
validate_plan_against_capabilities(plan, source_asset, base_plan)
```

---

## 7. 修复方案 V2

当前 `video_repair_plan.v1` 升级为 `video_repair_plan.v2`。历史 V1 计划仍可读取和执行，不迁移已批准任务。

### 7.1 计划结构

```json
{
  "schema_version": "video_repair_plan.v2",
  "primary_issue_id": "issue-1",
  "primary_action": "AV_SEGMENT_REPLACE",
  "actions": [
    {
      "action_id": "action-1",
      "type": "AV_SEGMENT_REPLACE",
      "target_range": {
        "start_ms": 6000,
        "end_ms": 10000
      },
      "affected_tracks": ["video_track", "narrative_segments", "subtitles"],
      "duration_policy": "KEEP_TOTAL_DURATION",
      "candidate_ids": ["asset-2:h1", "asset-5:h3"],
      "selected_candidate_id": "asset-2:h1",
      "candidate_source_range": {
        "start_ms": 1500,
        "end_ms": 5500
      },
      "predicted_effects": {
        "total_duration_delta_ms": 0,
        "visual_changed": true,
        "audio_changed": true,
        "caption_changed": true
      }
    }
  ],
  "warnings": [],
  "generated_by": {
    "diagnosis_mode": "MULTIMODAL",
    "model": "provider-model-name",
    "prompt_version": "video-repair-diagnosis-v2"
  }
}
```

内部时间轴目前使用秒。API V2 使用毫秒减少浮点歧义，进入现有时间轴补丁层时统一转换为三位小数秒。不得在不同模块中反复混用单位。

### 7.2 动作选择规则

```text
问题涉及 audio 或 caption？
├─ 是 → AV_SEGMENT_REPLACE
└─ 否
   ├─ 仅 visual → VISUAL_REPLACE
   └─ 缺少过程/证明
      ├─ 能找到可牺牲的低价值等长区间 → AV_SEGMENT_REPLACE
      └─ 无可替换区间且计划支持安全顺延 → PROCESS_INSERT
```

如果 AI 建议与规则冲突，以能力规则为准，并在 `warnings` 中记录调整原因。

### 7.3 人工可修改字段

在 `NEEDS_CONFIRMATION` 状态允许修改：

- `primary_action`，仅限能力注册表中的动作。
- `target_range` 或插入点。
- `selected_candidate_id`。

人工修改后必须重新运行服务端计划校验和候选检索。不能只在前端本地修改。

新增接口：

```http
PATCH /api/video-repair-tasks/{task_id}/draft-plan
```

请求：

```json
{
  "updated_at": "2026-09-29T10:00:00Z",
  "action_type": "AV_SEGMENT_REPLACE",
  "target_range": {"start_ms": 6000, "end_ms": 10000},
  "selected_candidate_id": "asset-2:h1"
}
```

响应返回重新校验并重新计算候选后的完整任务。使用 `updated_at` 做乐观锁。

---

## 8. 候选素材匹配

候选检索继续复用 `candidates.py`，但需要增加两层校验。

### 8.1 硬过滤

必须满足：

- 同租户。
- 同商品。
- 非当前成片本身。
- 可复用且分析完成。
- 不命中问题来源血缘区间。
- 比例可适配。
- `VISUAL_REPLACE`：候选有效时长覆盖目标时长。
- `AV_SEGMENT_REPLACE`：候选有效时长覆盖目标时长且有音频。
- `PROCESS_INSERT`：候选有音频，且角色为 `usage` 或 `product_demo`。

修正当前逻辑：等长替换不得只要求 `duration >= min(1.0, target_duration)`；必须要求候选时长覆盖完整目标时长。

### 8.2 语义评分

在基础角色、时长、比例和质量分之外，增加：

- 与问题描述的语义匹配度。
- 与目标时间段口播/字幕的语义匹配度。
- 与修复目的的匹配度，例如“展示真实使用过程”。
- 新风险检测结果。

语义评分只能影响排序，不能绕过硬过滤。

### 8.3 候选快照

批准时不得只保存候选 ID。`approved_plan_json` 中同时保存批准时的候选快照：

```json
{
  "candidate_id": "asset-2:h1",
  "source_asset_id": 2,
  "source_asset_public_id": "asset-2",
  "source_range": {"start_ms": 1500, "end_ms": 5500},
  "content_hash": "...",
  "has_audio": true
}
```

渲染前重新校验权限、素材存在性和 `content_hash`。素材内容已变化时拒绝执行，不能静默使用变化后的素材。

---

## 9. 人工确认与冻结

本期只有一个强制人工节点：执行前确认。

工作区必须显示：

1. AI 判断的问题类型、说明和置信度。
2. 问题时间段及定位来源。
3. 判断证据，包括对应 ASR、OCR 或关键帧摘要。
4. 系统选择的修复动作。
5. 会改变和会保留的轨道。
6. 候选素材和候选源时间段。
7. 总时长是否变化。
8. 阻断和风险提示。

人工操作仅需：

- 修改动作、时间段或候选素材。
- 确认并冻结方案。
- 无法接受时不执行，转人工处理。

### 9.1 审批校验

`approve_task()` 在冻结前必须重新校验：

- 任务仍为 `NEEDS_CONFIRMATION`。
- `updated_at` 未过期。
- 动作存在于能力注册表。
- 时间范围合法且被相关轨道覆盖。
- 候选仍可用、权限正确、同商品、时长足够。
- 动作与问题影响轨道不冲突。
- `PROCESS_INSERT` 的时间轴可安全顺延。

校验成功后深拷贝为 `approved_plan_json`。渲染只读取该快照，不读取可变的 `repair_plan_json`。

---

## 10. 确定性时间轴执行

### 10.1 执行入口

继续使用：

```text
backend/app/services/video_repair/rendering.py
backend/app/services/video_editing/plan_patch.py
backend/app/services/video_editing/renderer.py
```

执行阶段禁止调用诊断模型或规划模型。

### 10.2 `VISUAL_REPLACE`

调用现有 `replace_range(video_track, target, candidate)`：

- 切开与目标相交的原画面片段。
- 插入候选画面。
- 不修改 `narrative_segments`。
- 不读取候选音频。
- 验证画面轨连续覆盖原总时长。

需要补充：

- 验证烧录字幕条件。
- 在修复元数据中记录 `preserve_original_audio=true`。
- 单元测试验证输出音频来源仍为 V1 对应音轨。

### 10.3 `AV_SEGMENT_REPLACE`

调用现有 `replace_range()` 分别修改画面轨和叙事轨，但候选音频片段必须显式标记：

```json
{
  "audio_strategy": "source",
  "speech_text": "",
  "subtitle_words": []
}
```

需要补充：

- 严格等长检查。
- 删除目标范围内原字幕。
- 音频格式标准化和固定短淡入淡出。
- 独立 BGM 存在时保持 BGM 连续。
- 验证画面轨和叙事轨都连续覆盖原总时长。

### 10.4 `PROCESS_INSERT`

继续使用 `insert_segment()`，但在执行前枚举所有支持的时间轴轨道。

如果基础计划存在无法识别但带有时间语义的轨道，返回：

```text
UNSUPPORTED_TIMELINE_TRACK_FOR_INSERT
```

不得仅移动画面和叙事轨而遗漏其他时间元素。

### 10.5 渲染后校验

渲染完成后、创建 V2 记录前至少校验：

- 输出文件存在且可读取。
- 实际时长与计划时长误差不超过 `100ms` 或现有渲染器可稳定达到的阈值。
- 输出宽高和项目比例正确。
- `VISUAL_REPLACE` 总时长不变。
- `AV_SEGMENT_REPLACE` 总时长不变。
- `PROCESS_INSERT` 总时长增加量与候选时长一致。
- 输出有音频。
- 来源链路覆盖完整输出画面时间轴。

只有校验通过后才创建 `VideoAsset(final_output)` 并把任务标记为 `COMPLETED`。

---

## 11. 状态机

复用当前状态，不增加不必要状态：

```text
ANALYZING
  ├─> NEEDS_CONFIRMATION
  ├─> NEED_MORE_MATERIAL
  ├─> NON_VIDEO_FIXABLE
  └─> FAILED

NEEDS_CONFIRMATION
  ├─> APPROVED
  └─> CANCELED

APPROVED
  └─> RENDERING

RENDERING
  ├─> COMPLETED
  └─> FAILED
```

规则：

- AI 判断成功不代表可以执行；只有计划校验成功才进入 `NEEDS_CONFIRMATION`。
- 找不到候选素材进入 `NEED_MORE_MATERIAL`。
- 非视频问题进入 `NON_VIDEO_FIXABLE`。
- 诊断不确定但人工可以补充时间和动作时，可以进入 `NEEDS_CONFIRMATION`，但禁止批准不完整方案。
- `COMPLETED` 不允许回退。

---

## 12. 数据模型调整

优先复用 `VideoRepairTask` 现有 JSON 字段：

- `diagnosis_json` 保存 `video_repair_diagnosis.v2`。
- `repair_plan_json` 保存人工确认前可变草案。
- `selected_candidates_json` 保留兼容，但 V2 计划中也保存选择结果。
- `approved_plan_json` 保存最终冻结快照。

建议新增以下审计字段；需要显式迁移：

| 字段 | 类型 | 说明 |
|---|---|---|
| `diagnosis_mode` | string | `MULTIMODAL / RULE_FALLBACK / MANUAL` |
| `diagnosis_model` | string | 模型名称，可为空 |
| `diagnosis_prompt_version` | string | Prompt/Schema 版本 |
| `diagnosis_completed_at` | datetime nullable | 诊断完成时间 |

如果不希望本期迁移表结构，也可以先放入 `diagnosis_json.meta`，但 Codex 必须选择一种方式并保持接口一致，不得两处重复写入。

不得修改历史任务的诊断和批准快照。

---

## 13. API 调整

保留当前接口：

```text
POST /api/video-repair-tasks
GET  /api/video-repair-tasks
GET  /api/video-repair-tasks/{task_id}
POST /api/video-repair-tasks/{task_id}/candidates/select
POST /api/video-repair-tasks/{task_id}/approve
POST /api/video-repair-tasks/{task_id}/render
```

新增：

```text
PATCH /api/video-repair-tasks/{task_id}/draft-plan
```

可选但建议新增：

```text
POST /api/video-repair-tasks/{task_id}/reanalyze
```

`reanalyze` 仅允许未批准任务使用，目的是在视频分析完成或用户修正输入后重新判断。已批准和已完成任务禁止重新分析。

统一错误码至少包含：

```text
AI_DIAGNOSIS_FAILED
AI_DIAGNOSIS_INVALID
LOW_CONFIDENCE_REQUIRES_HUMAN
INVALID_TARGET_RANGE
TARGET_RANGE_UNCOVERED
ACTION_MODALITY_CONFLICT
CANDIDATE_NOT_ALLOWED
CANDIDATE_TOO_SHORT
CANDIDATE_AUDIO_REQUIRED
CANDIDATE_CHANGED_AFTER_APPROVAL
BURNED_CAPTION_VISUAL_REPLACE_UNSUPPORTED
UNSUPPORTED_TIMELINE_TRACK_FOR_INSERT
OUTPUT_VALIDATION_FAILED
```

---

## 14. 前端最小改造

不增加额外页面，只调整现有：

```text
frontend/src/components/VideoRepairWorkbench.tsx
frontend/src/api/client.ts
```

工作区按顺序显示：

```text
审核失败事实
AI 判断与证据
问题时间段
系统修复方案
候选素材
人工确认执行
V1 / V2 结果
```

必须支持：

- 编辑开始和结束时间。
- 在允许动作中切换。
- 切换动作后重新请求候选素材。
- 选择候选素材。
- 显示修改轨道、保留轨道和总时长变化。
- 人工确认后禁用所有计划编辑控件。

不实现：

- 拖拽时间轴。
- 多动作排序。
- 自动播放对比。
- 结果二次验收状态。

---

## 15. 配置与模型提供者

新增配置项名称应与项目现有配置风格一致，至少需要：

```text
VIDEO_REPAIR_AI_ENABLED
VIDEO_REPAIR_AI_PROVIDER
VIDEO_REPAIR_AI_MODEL
VIDEO_REPAIR_AI_TIMEOUT_SECONDS
VIDEO_REPAIR_AI_MAX_KEYFRAMES
VIDEO_REPAIR_AI_PROMPT_VERSION
```

要求：

- 未配置模型时系统仍可启动。
- `VIDEO_REPAIR_AI_ENABLED=false` 时使用规则降级诊断。
- API Key 不写入数据库、不写入日志、不返回前端。
- 记录模型名称、耗时、成功/失败和 token 用量等元数据，但不记录敏感凭证。
- 模型调用必须设置超时和有限重试，不能无限阻塞任务创建。

---

## 16. Codex 实施顺序

Codex 必须按以下顺序实施，每一步通过测试后再进入下一步。

### Phase 1：契约与能力注册表

1. 新增 `diagnosis_schema.py`。
2. 新增 `capabilities.py`。
3. 新增 `time_extraction.py`。
4. 为时间解析和能力冲突编写单元测试。
5. 不改变现有渲染行为。

### Phase 2：多模态诊断适配层

1. 新增 `ai_context.py` 和 `ai_provider.py`。
2. 新增 `diagnosis_v2.py`。
3. 将当前规则诊断封装为 fallback，不删除。
4. 使用可替换 fake provider 编写测试，测试中禁止依赖真实外部模型。
5. 确保模型失败不会导致错误计划被批准。

### Phase 3：计划 V2 与候选严格过滤

1. 改造 `planner.py` 使用能力注册表。
2. 修正候选时长硬过滤。
3. 生成 `video_repair_plan.v2`。
4. 增加计划修改接口。
5. 审批时保存候选快照并重新校验。

### Phase 4：执行引擎加固

1. 加固 `VISUAL_REPLACE` 的字幕和音频保留条件。
2. 加固 `AV_SEGMENT_REPLACE` 的等长和音频处理。
3. 加固 `PROCESS_INSERT` 的全轨道顺延检查。
4. 增加渲染后媒体校验。
5. 保持历史 `video_repair_plan.v1` 可执行。

### Phase 5：人工确认界面

1. 展示 AI 证据和置信度。
2. 支持编辑动作、时间段、候选素材。
3. 展示轨道影响和时长变化。
4. 确认后冻结控件。
5. 保持 V1/V2 对比和下载入口。

### Phase 6：回归与文档

1. 运行后端单元测试。
2. 运行真实 FFmpeg 三动作回归测试。
3. 运行前端测试和构建。
4. 更新 OpenAPI 和必要的部署配置说明。
5. 不修改或删除历史任务。

---

## 17. 测试要求

### 17.1 时间解析

至少覆盖：

```text
6-10s
6-10秒
6~10秒
6到10秒
00:06-00:10
第6秒到第10秒
超出视频时长
倒置区间
没有时间信息
```

### 17.2 AI 诊断契约

- 合法 JSON 可以解析。
- 未知动作被拒绝。
- 越界时间段被拒绝。
- 没有证据的结论被拒绝。
- audio 问题错误选择 `VISUAL_REPLACE` 时被能力层纠正或拒绝。
- 模型超时进入规则降级。
- 模型与规则都不确定时不得自动批准。

### 17.3 `VISUAL_REPLACE`

- 画面被替换，原叙事音频保持。
- 总时长不变。
- 候选太短被拒绝。
- 目标画面轨不连续时被拒绝。
- 问题来源素材不能被重新选回。
- 烧录字幕且无法重建时被拒绝。

### 17.4 `AV_SEGMENT_REPLACE`

- 目标范围画面和音频同时替换。
- 目标范围原字幕被删除。
- 总时长不变。
- 无音频候选被拒绝。
- 候选太短被拒绝。
- 音频切点处理后输出仍有有效音轨。

### 17.5 `PROCESS_INSERT`

- 插入位置正确。
- 后续画面、叙事和字幕时间整体顺延。
- 总时长准确增加。
- 不支持的时间轨道会阻断执行。
- 插入点越界时被拒绝。

### 17.6 审批与并发

- 过期 `updated_at` 返回冲突。
- 批准后修改草案不影响 `approved_plan_json`。
- 批准后候选内容变化时渲染被拒绝。
- 重复渲染已完成任务返回原 V2，不生成新文件。

### 17.7 前端

- 显示 AI 判断、证据、时间范围和置信度。
- 修改时间段会调用草案更新接口。
- 切换动作会刷新候选。
- 批准后编辑控件禁用。
- `COMPLETED` 显示 V1/V2。
- `NEED_MORE_MATERIAL` 不显示可执行按钮。

---

## 18. 验收场景

### 场景 A：局部画面错误

输入：

```text
审核理由：6-10秒商品展示不准确
```

期望：

- 文本解析得到 `6000-10000ms`。
- AI 判断 `VISUAL_MISMATCH`。
- 系统生成 `VISUAL_REPLACE`。
- 候选时长至少 4 秒。
- 人工确认后输出 V2。
- V2 总时长与 V1 一致，目标区间原音频保留。

### 场景 B：口播和字幕错误

输入：

```text
审核理由：7秒左右的功效描述缺少依据，口播和字幕均需修改
```

期望：

- AI 综合 ASR、字幕定位近似区间。
- 影响轨道包含 `audio` 和 `caption`。
- 禁止生成 `VISUAL_REPLACE`。
- 系统生成 `AV_SEGMENT_REPLACE` 或因无合适素材进入 `NEED_MORE_MATERIAL`。

### 场景 C：缺少使用过程

输入：

```text
审核理由：只展示了结果，没有实际使用过程
```

期望：

- AI 判断 `MISSING_PROCESS_EVIDENCE`。
- 若存在低价值等长区间，优先生成 `AV_SEGMENT_REPLACE`。
- 否则生成 `PROCESS_INSERT`，显示新增时长。
- 人工确认后所有后续时间元素正确顺延。

### 场景 D：非视频问题

输入：

```text
审核理由：商品资质不完整
```

期望：

- 进入 `NON_VIDEO_FIXABLE`。
- 不检索候选素材。
- 不显示确认执行按钮。

### 场景 E：理由抽象且无法定位

输入：

```text
审核理由：内容表达不准确，请修改
```

期望：

- AI 可以列出可能问题和证据，但不得伪造精确时间段。
- 若无法形成合法局部动作，要求人工指定时间和动作。
- 未补充完整前不能批准执行。

---

## 19. Definition of Done

以下条件全部满足才算完成：

- [ ] 抽象审核理由由多模态诊断适配层处理，不再只依赖关键词。
- [ ] `6-10s` 等文本时间表达可以确定性提取。
- [ ] AI 输出经过严格 Schema 校验。
- [ ] 能力注册表限制 AI 只能选择系统支持动作。
- [ ] `VISUAL_REPLACE`、`AV_SEGMENT_REPLACE`、`PROCESS_INSERT` 具备明确且经过测试的执行语义。
- [ ] 候选素材执行前经过同租户、同商品、时长、音频、血缘和内容快照校验。
- [ ] 人工可以修改动作、时间段和候选素材。
- [ ] 人工确认后冻结 `approved_plan_json`，渲染阶段不再调用 AI。
- [ ] V1 不变，成功时创建新的 V2 和计划版本。
- [ ] 失败时不留下伪完成任务或不完整 V2。
- [ ] 历史 V1 修复计划仍可读取和执行。
- [ ] 后端测试、前端测试、构建和真实 FFmpeg 回归全部通过。
- [ ] 修复完成不触发任何自动投流或外部平台写入。

---

## 20. Codex 执行约束

Codex 实施本方案时必须遵守：

1. 先阅读本文件列出的现有实现，不得假设接口不存在。
2. 保留工作区中与本任务无关的用户修改。
3. 不得删除现有迁移或重建历史修复数据。
4. 不得为了测试依赖真实外部多模态模型；使用 fake provider。
5. 不得把模型自然语言直接拼接成 FFmpeg 命令。
6. 不得把模型建议直接写入 `approved_plan_json`。
7. 不得在未确认的情况下自动渲染。
8. 不得用全片范围伪装精确定位成功。
9. 每完成一个 Phase，运行对应测试并记录结果。
10. 若现有渲染器无法满足某项音轨或时间轴约束，应明确返回不支持，不能静默降级生成错误视频。
