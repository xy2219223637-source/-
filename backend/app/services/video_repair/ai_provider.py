from __future__ import annotations

import base64
import copy
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel

from app.services.video_repair.diagnosis_schema import VideoRepairDiagnosisV2
from app.services.storage import get_storage_for_url


@dataclass(frozen=True)
class DiagnosisProviderResult:
    payload: dict[str, Any]
    model: str
    token_usage: dict[str, int] = field(default_factory=dict)


class MultimodalDiagnosisProvider(Protocol):
    def diagnose(self, context: dict[str, Any], *,
                 schema_model: type[BaseModel] = VideoRepairDiagnosisV2) -> DiagnosisProviderResult: ...


def _image_data_url(source: str) -> str | None:
    if source.startswith(("data:", "http://", "https://")):
        return source
    local = get_storage_for_url(source).local_path(source)
    if not local:
        return None
    path = Path(local)
    if not path.exists():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_multimodal_content(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn stored product references and the source video into real model inputs."""
    prompt_context = copy.deepcopy(context)
    video = prompt_context.get("video") or {}
    source_url = str(video.pop("source_url", "") or "")
    product = prompt_context.get("product") or {}
    references = list(product.pop("reference_images", []) or [])
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "请结合结构化审核上下文、商品基准图和原始成片完成诊断。"
            "基准图用于判断成片中展示的商品、包装、品牌文字是否一致；"
            "原始成片用于定位时间范围。上下文如下：\n"
            + json.dumps(prompt_context, ensure_ascii=False)
        ),
    }]
    for index, reference in enumerate(references[:2]):
        resolved = _image_data_url(str(reference.get("url") or ""))
        if not resolved:
            continue
        content.append({
            "type": "text",
            "text": f"商品基准图 {index + 1}（{reference.get('role') or 'reference'}）：{reference.get('description') or ''}",
        })
        content.append({"type": "image_url", "image_url": {"url": resolved}})
    if source_url:
        signed = get_storage_for_url(source_url).signed_url(source_url)
        if signed:
            content.append({"type": "text", "text": "以下是需要审核返修的原始成片。"})
            content.append({"type": "video_url", "video_url": {"url": signed}})
    return content


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

    def diagnose(self, context: dict[str, Any], *,
                 schema_model: type[BaseModel] = VideoRepairDiagnosisV2) -> DiagnosisProviderResult:
        schema = schema_model.model_json_schema()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是受约束的视频审核修复诊断器。只能从 capabilities 中选择动作；"
                        "如无动作能完整解决问题，选择 UNSUPPORTED，不得强行降级为局部修复；"
                        "不得伪造时间范围；用户明确时间范围优先；输出必须符合 JSON Schema。"
                    ),
                },
                {"role": "user", "content": build_multimodal_content(context)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_model.__name__, "strict": True,
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
