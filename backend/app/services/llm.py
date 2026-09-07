"""OpenAI-compatible LLM client with text, vision, and embedding support."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

VOLCENGINE_ASR_BASE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
VOLCENGINE_ASR_PROVIDERS = {"volcengine", "volcengine_asr", "doubao_speech"}


def _is_volcengine_ark_url(value: str) -> bool:
    normalized = (value or "").lower()
    return "ark." in normalized and ".volces.com" in normalized


def resolve_director_asr_provider(settings: Any | None = None) -> str:
    settings = settings or get_settings()
    provider = settings.director_asr_provider.strip().lower() or "auto"
    if provider != "auto":
        return provider
    asr_base = settings.director_asr_api_base.lower()
    if "openspeech.bytedance.com" in asr_base or _is_volcengine_ark_url(settings.llm_api_base):
        return "volcengine_asr"
    return "openai_compatible"


def _normalize_asr_words(values: object, *, seconds: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    output: list[dict[str, Any]] = []
    scale = 1000 if seconds else 1
    for item in values:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        text = str(item.get("word") or item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("start_time") if item.get("start_time") is not None else item.get("start")
        end = item.get("end_time") if item.get("end_time") is not None else item.get("end")
        start_ms = max(0, int(float(start or 0) * scale))
        end_ms = max(start_ms, int(float(end if end is not None else start or 0) * scale))
        output.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
    return output


def _normalize_volcengine_asr_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: Any = payload.get("result") or {}
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        return []
    normalized: list[dict[str, Any]] = []
    utterances = result.get("utterances") if isinstance(result.get("utterances"), list) else []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start_ms = max(0, int(float(item.get("start_time") or 0)))
        end_ms = max(start_ms, int(float(item.get("end_time") or start_ms)))
        words = _normalize_asr_words(item.get("words") or item.get("word_details"))
        normalized.append({"start_ms": start_ms, "end_ms": end_ms, "text": text, **({"words": words} if words else {})})
    if normalized:
        return normalized
    text = str(result.get("text") or "").strip()
    duration = payload.get("audio_info", {}).get("duration") if isinstance(payload.get("audio_info"), dict) else 0
    return [{"start_ms": 0, "end_ms": max(0, int(float(duration or 0))) or 2**31 - 1, "text": text}] if text else []


def _volcengine_language(language: str) -> str:
    aliases = {
        "zh": "zh-CN",
        "en": "en-US",
        "ja": "ja-JP",
        "ko": "ko-KR",
        "yue": "yue-CN",
    }
    return aliases.get(language.strip(), language.strip())


@lru_cache(maxsize=2)
def _local_whisper(model_name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("本地 ASR 需要安装 faster-whisper, 请参考 backend/VIDEO_ANALYSIS_SETUP.md") from exc
    return WhisperModel(model_name, device=device, compute_type=compute_type)


class LLMClient:
    def __init__(self, *, api_base: str = "", api_key: str = "") -> None:
        settings = get_settings()
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key or "sk-placeholder",
            base_url=api_base or settings.llm_api_base,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if self.settings.llm_disable_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        response = await self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        return content.strip()

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        if self.settings.llm_mock_mode:
            return self._mock_json(messages)
        # Prefer native JSON mode; fall back to markdown fence extraction
        try:
            raw = await self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            system_hint = {
                "role": "system",
                "content": "You must respond with valid JSON only. No markdown fences.",
            }
            raw = await self.chat(
                [system_hint, *messages],
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return self._parse_json(raw)

    async def chat_vision(
        self,
        text_prompt: str,
        image_sources: list[str],
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 4096,
        as_json: bool = True,
        image_limit: int = 6,
        model: Optional[str] = None,
    ) -> Any:
        content: list[dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        for src in image_sources[:max(1, image_limit)]:
            image_url = await self._resolve_image_url(src)
            if image_url:
                content.append({"type": "image_url", "image_url": {"url": image_url}})

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        if as_json:
            return await self.chat_json(
                messages,
                model=model or self.settings.llm_vision_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return await self.chat(
            messages,
            model=model or self.settings.llm_vision_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def transcribe(self, audio_path: str | Path) -> list[dict[str, Any]]:
        """Return timestamped ASR segments; callers may degrade to an empty list."""
        path = Path(audio_path)
        provider = resolve_director_asr_provider(self.settings)
        if provider in {"", "none", "disabled"} or self.settings.llm_mock_mode or not path.exists() or path.stat().st_size == 0:
            return []
        if provider == "faster_whisper":
            return await asyncio.to_thread(self._transcribe_local, path)
        if provider in VOLCENGINE_ASR_PROVIDERS:
            return await self._transcribe_volcengine(path)
        if provider not in {"openai", "openai_compatible"}:
            raise ValueError(f"不支持的 DIRECTOR_ASR_PROVIDER: {provider}")
        asr_client = self.client
        if self.settings.director_asr_api_base or self.settings.director_asr_api_key:
            asr_client = AsyncOpenAI(
                api_key=self.settings.director_asr_api_key or self.settings.llm_api_key or "sk-placeholder",
                base_url=self.settings.director_asr_api_base or self.settings.llm_api_base,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=1,
            )
        with path.open("rb") as audio:
            granularities = ["word", "segment"] if provider == "openai" else ["segment"]
            response = await asr_client.audio.transcriptions.create(
                model=self.settings.director_asr_model,
                file=(path.name, audio, "audio/mpeg"),
                language=self.settings.director_asr_language or None,
                response_format="verbose_json",
                timestamp_granularities=granularities,
            )
        segments = getattr(response, "segments", None) or []
        normalized: list[dict[str, Any]] = []
        for item in segments:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            start_ms = max(0, int(float(item.get("start") or 0) * 1000))
            end_ms = max(start_ms, int(float(item.get("end") or 0) * 1000))
            normalized.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
        words = _normalize_asr_words(getattr(response, "words", None), seconds=True)
        if words:
            for segment in normalized:
                segment["words"] = [
                    word for word in words
                    if word["end_ms"] > segment["start_ms"] and word["start_ms"] < segment["end_ms"]
                ]
        if normalized:
            return normalized
        text = str(getattr(response, "text", "") or "").strip()
        return [{"start_ms": 0, "end_ms": 2**31 - 1, "text": text}] if text else []

    async def _transcribe_volcengine(self, path: Path) -> list[dict[str, Any]]:
        api_key = self.settings.director_asr_api_key
        if not api_key:
            raise RuntimeError("火山 ASR 缺少 API Key, 请配置 DIRECTOR_ASR_API_KEY")

        from app.services.storage import get_storage

        storage = get_storage()
        object_url = ""
        try:
            audio_data = await asyncio.to_thread(path.read_bytes)
            object_url = await asyncio.to_thread(
                storage.save_bytes,
                audio_data,
                path.name,
                "director/asr-temp",
            )
            audio_url = storage.signed_url(object_url)
            request_id = str(uuid.uuid4())
            headers = {
                "X-Api-Key": api_key,
                "X-Api-Resource-Id": self.settings.director_asr_resource_id,
                "X-Api-Request-Id": request_id,
                "X-Api-Sequence": "-1",
            }
            language = _volcengine_language(self.settings.director_asr_language)
            audio: dict[str, Any] = {"format": path.suffix.removeprefix(".").lower() or "mp3", "url": audio_url}
            if language:
                audio["language"] = language
            body = {
                "audio": audio,
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "enable_ddc": True,
                    "show_utterances": True,
                },
            }
            base_url = (self.settings.director_asr_api_base or VOLCENGINE_ASR_BASE_URL).rstrip("/")
            for suffix in ("/submit", "/query"):
                if base_url.endswith(suffix):
                    base_url = base_url.removesuffix(suffix)
            timeout = max(self.settings.llm_timeout_seconds, 30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{base_url}/submit", headers=headers, json=body)
                response.raise_for_status()
                status_code = response.headers.get("X-Api-Status-Code", "")
                if status_code != "20000000":
                    message = response.headers.get("X-Api-Message", "提交失败")
                    raise RuntimeError(f"火山 ASR 提交失败: {status_code or response.status_code} {message}")

                query_headers = {key: value for key, value in headers.items() if key != "X-Api-Sequence"}
                loop = asyncio.get_running_loop()
                deadline = loop.time() + max(10.0, self.settings.director_asr_poll_timeout_seconds)
                while True:
                    response = await client.post(f"{base_url}/query", headers=query_headers, json={})
                    response.raise_for_status()
                    status_code = response.headers.get("X-Api-Status-Code", "")
                    if status_code == "20000000":
                        return _normalize_volcengine_asr_response(response.json())
                    if status_code == "20000003":
                        return []
                    if status_code not in {"20000001", "20000002"}:
                        message = response.headers.get("X-Api-Message", "查询失败")
                        raise RuntimeError(f"火山 ASR 查询失败: {status_code or response.status_code} {message}")
                    if loop.time() >= deadline:
                        raise TimeoutError("火山 ASR 查询超时")
                    await asyncio.sleep(max(0.2, self.settings.director_asr_poll_interval_seconds))
        finally:
            if object_url:
                try:
                    await storage.delete(object_url)
                except Exception as exc:
                    logger.warning("Failed to delete temporary ASR audio object: %s", exc)

    def _transcribe_local(self, path: Path) -> list[dict[str, Any]]:
        model = _local_whisper(
            self.settings.director_asr_model,
            self.settings.director_asr_local_device,
            self.settings.director_asr_local_compute_type,
        )
        segments, _ = model.transcribe(
            str(path),
            language=self.settings.director_asr_language or None,
            vad_filter=True,
            word_timestamps=True,
        )
        output: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment.text or "").strip()
            if not text:
                continue
            words = _normalize_asr_words(
                [
                    {"word": word.word, "start": word.start, "end": word.end}
                    for word in (segment.words or [])
                ],
                seconds=True,
            )
            output.append(
                {
                    "start_ms": max(0, int(float(segment.start) * 1000)),
                    "end_ms": max(0, int(float(segment.end) * 1000)),
                    "text": text,
                    **({"words": words} if words else {}),
                }
            )
        return output

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.llm_mock_mode:
            return [self._mock_embedding(text) for text in texts]
        if self.settings.embedding_mode == "multimodal":
            return await self._embed_multimodal_texts(texts)
        # Batch in chunks of 64
        all_embeddings: list[list[float]] = []
        batch_size = 64
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await self.client.embeddings.create(
                model=self.settings.embedding_model,
                input=batch,
            )
            sorted_data = sorted(response.data, key=lambda x: x.index)
            all_embeddings.extend([d.embedding for d in sorted_data])
        return all_embeddings

    async def _embed_multimodal_texts(self, texts: list[str]) -> list[list[float]]:
        base_url = (self.settings.embedding_base_url or self.settings.llm_api_base).rstrip("/")
        api_key = self.settings.embedding_api_key or self.settings.llm_api_key
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for text in texts:
                response = await client.post(
                    f"{base_url}/embeddings/multimodal",
                    headers=headers,
                    json={
                        "model": self.settings.embedding_model,
                        "encoding_format": "float",
                        "input": [{"type": "text", "text": text}],
                    },
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or []
                item = data[0] if isinstance(data, list) and data else data
                embedding = item.get("embedding") if isinstance(item, dict) else None
                if embedding and isinstance(embedding[0], list):
                    embedding = embedding[0]
                if not isinstance(embedding, list):
                    raise ValueError("多模态向量接口未返回有效 embedding")
                if len(embedding) != self.settings.embedding_dimension:
                    logger.warning(
                        "Embedding dimension mismatch: expected %s, got %s",
                        self.settings.embedding_dimension,
                        len(embedding),
                    )
                embeddings.append([float(value) for value in embedding])
        return embeddings

    async def _resolve_image_url(self, src: str) -> Optional[str]:
        if src.startswith("data:"):
            return src
        if src.startswith("http://") or src.startswith("https://"):
            return src

        # Stored object or local file → base64 data URI
        from app.services.storage import get_storage
        stored = get_storage().local_path(src)
        path = stored or Path(src)
        if not path.exists():
            logger.warning("Image not found: %s", src)
            return None

        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/jpeg"
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Extract from markdown fence
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # Find outermost braces
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.error("Failed to parse JSON from LLM: %s", raw[:500])
        return {"raw": raw}

    @staticmethod
    def _mock_embedding(text: str, dimensions: int = 64) -> list[float]:
        vector = [0.0] * dimensions
        for index, char in enumerate(text):
            vector[(ord(char) + index) % dimensions] += 1.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    @staticmethod
    def _mock_json(messages: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = json.dumps(messages, ensure_ascii=False)
        if "marketing_copy" in prompt:
            return {
                "title": "专业成分护理，让每一次使用更安心",
                "selling_points": "- 核心成分清晰\n- 商品资料可追溯\n- 使用步骤简单\n- 适配日常护理场景\n- 专业克制的品牌表达",
                "advantages": "从商品资料与品牌知识出发，准确表达产品价值。",
                "scenarios": "## 日常护理\n适合早晚护肤与换季护理。",
                "pain_solutions": "将复杂商品信息转化为清晰易懂的购买理由。",
                "purchase_reasons": "信息完整、成分明确、使用方便。",
                "faq": "**Q：如何使用？**\n\nA：按商品说明在清洁后使用。\n\n**Q：适合谁？**\n\nA：请结合商品资料与个人肤质判断。",
                "after_sales": "请以品牌正式售后政策与商品包装信息为准。",
                "marketing_copy": "把复杂成分讲清楚，把真实价值呈现出来。",
                "main_image_copy": "- 专业成分护理\n- 商品知识驱动",
            }
        if "product_type" in prompt:
            return {
                "product_type": "美妆护肤商品",
                "features": ["配方信息清晰", "适合日常护理", "商品资料可追溯"],
                "core_advantages": ["基于商品知识生成", "突出核心成分与使用体验"],
                "purchase_reasons": ["信息完整", "使用方法明确"],
                "visual_insights": "建议使用产品正面图、成分特写与简洁留白布局。",
            }
        if "target_consumers" in prompt:
            return {
                "target_consumers": [{"persona": "品质护肤用户", "demographics": "关注成分与体验", "needs": "稳定、清晰的护理方案"}],
                "usage_scenarios": ["早晚日常护理", "换季肌肤护理"],
                "pain_points": ["商品信息复杂", "难以判断是否适合自己"],
                "decision_factors": ["成分", "功效依据", "使用方法"],
            }
        if "positioning" in prompt:
            return {
                "positioning": "以真实商品知识支撑的专业护理方案",
                "selling_points_ranked": [{"rank": 1, "point": "核心成分清晰", "reason": "降低购买决策成本"}],
                "competitive_advantages": ["资料完整", "表达专业"],
                "tone_style": "专业、克制、可信",
                "main_image_copy_suggestions": ["科学护理，简单有效", "核心成分，一目了然"],
            }
        return {"raw": "Mock LLM response"}


_llm: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
