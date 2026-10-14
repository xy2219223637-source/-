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
    input_mode: str = "VIDEO"


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


def build_multimodal_content(context: dict[str, Any], *, include_video: bool = True) -> list[dict[str, Any]]:
    """Turn stored product references and the source video into real model inputs."""
    prompt_context = copy.deepcopy(context)
    video = prompt_context.get("video") or {}
    source_url = str(video.pop("source_url", "") or "")
    signed_video = get_storage_for_url(source_url).signed_url(source_url) if source_url and include_video else None
    product = prompt_context.get("product") or {}
    references = list(product.pop("reference_images", []) or [])
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            ("请结合结构化审核上下文、商品基准图和原始成片完成诊断。"
             if signed_video else
             "原视频未提供给本次请求。请仅依据审核上下文、已有源视频分析和商品基准图判断；"
             "不得声称亲眼看过原片。") +
            "基准图用于判断成片中展示的商品、包装、品牌文字是否一致；"
            "审核明确时间范围优先。上下文如下：\n"
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
    if signed_video:
        content.append({"type": "text", "text": "以下是需要审核返修的原始成片。"})
        content.append({"type": "video_url", "video_url": {"url": signed_video}})
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
        guidance = (
            "你是受约束的视频审核修复诊断器。对照审核意见、原片画面、转写和商品事实，"
            "判断完整修复需要改变哪些轨道；只能从 capabilities 选择动作。"
            "纯画面问题用 VISUAL_REPLACE，保留原声音及字幕；纯声音问题用 AUDIO_REPLACE，保留原画面及字幕；"
            "缺少产品功能展示、使用过程或证明片段，且新增完整音画片段即可解决时用 CONTENT_INSERT。"
            "若现有画面和声音必须同时改动、必须修改已有字幕、或三种动作无法完整解决，选 UNSUPPORTED。"
            "不能因为素材库暂时没有合适素材就判定动作不支持。"
            "不得伪造时间范围；审核明确时间范围优先。无可靠范围时保留空 ranges 并要求人工确认。"
            "证据需标注来源，文本推断不得冒充已观察到的画面。输出必须符合 JSON Schema。"
        )
        video_content = build_multimodal_content(context)
        has_video = any(item["type"] == "video_url" for item in video_content)
        json_schema_format = {"type": "json_schema", "json_schema": {
            "name": schema_model.__name__, "strict": True, "schema": schema}}
        attempts = [(video_content, json_schema_format, "VIDEO" if has_video else "STRUCTURED_CONTEXT")]
        if has_video:
            attempts.append((video_content, {"type": "json_object"}, "VIDEO"))
            attempts.append((build_multimodal_content(context, include_video=False),
                             {"type": "json_object"}, "STRUCTURED_CONTEXT"))
        else:
            attempts.append((video_content, {"type": "json_object"}, "STRUCTURED_CONTEXT"))
        errors = []
        for content, response_format, input_mode in attempts:
            if errors and input_mode == "VIDEO" and "video_url" in str(errors[-1]).lower():
                continue
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": guidance},
                              {"role": "user", "content": content}],
                    response_format=response_format,
                )
                payload = json.loads(response.choices[0].message.content or "{}")
                break
            except Exception as exc:
                errors.append(exc)
        else:
            last_error = errors[-1]
            raise RuntimeError("AI 视频请求与兼容请求均失败: " +
                               "; ".join(type(error).__name__ for error in errors) +
                               f"; {last_error}") from last_error
        usage = response.usage
        return DiagnosisProviderResult(
            payload=payload,
            model=self.model,
            input_mode=input_mode,
            token_usage={
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            },
        )
