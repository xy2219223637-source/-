from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI

from app.services.video_repair.diagnosis_schema import VideoRepairDiagnosisV2


@dataclass(frozen=True)
class DiagnosisProviderResult:
    payload: dict[str, Any]
    model: str
    token_usage: dict[str, int] = field(default_factory=dict)


class MultimodalDiagnosisProvider(Protocol):
    def diagnose(self, context: dict[str, Any]) -> DiagnosisProviderResult: ...


class OpenAICompatibleDiagnosisProvider:
    """The only model-SDK boundary for video-repair diagnosis."""

    def __init__(self, *, api_key: str, base_url: str, model: str,
                 timeout_seconds: float, prompt_version: str):
        if not api_key or not model:
            raise ValueError("视频修复 AI 未配置 API Key 或模型")
        self.model = model
        self.prompt_version = prompt_version
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds,
                             max_retries=1)

    def diagnose(self, context: dict[str, Any]) -> DiagnosisProviderResult:
        schema = VideoRepairDiagnosisV2.model_json_schema()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是受约束的视频审核修复诊断器。只能从 capabilities 中选择动作；"
                        "不得伪造时间范围；用户明确时间范围优先；输出必须符合 JSON Schema。"
                    ),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "video_repair_diagnosis_v2", "strict": True,
                                "schema": schema},
            },
        )
        content = response.choices[0].message.content or "{}"
        usage = response.usage
        return DiagnosisProviderResult(
            payload=json.loads(content),
            model=self.model,
            token_usage={
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        )
