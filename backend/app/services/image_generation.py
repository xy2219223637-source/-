"""Replaceable image generation provider for the creative canvas."""

from __future__ import annotations

import base64
import mimetypes
import textwrap
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from app.config import get_settings
from app.services.storage import get_storage

FONT_PATH = next((path for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/STHeiti Medium.ttc") if Path(path).exists()), "/System/Library/Fonts/STHeiti Medium.ttc")


def _local_path(url: str) -> Optional[Path]:
    return get_storage().local_path(url)


def _save_image(image: Image.Image, filename: str, project_id: int, format: str = "PNG") -> str:
    output = BytesIO()
    image.save(output, format=format, quality=95)
    return get_storage().save_bytes(output.getvalue(), filename, f"creative/{project_id}")


def _image_input(url: str) -> str:
    path = _local_path(url)
    if not path:
        return url
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _seedream_size(width: int, height: int) -> str:
    ratio = max(0.34, min(3.0, width / max(height, 1)))
    max_side = 4096 if max(width, height) >= 3000 else 2048
    if ratio >= 1:
        output_width = max_side
        output_height = round(max_side / ratio / 64) * 64
    else:
        output_height = max_side
        output_width = round(max_side * ratio / 64) * 64
    return f"{max(1024, output_width)}x{max(1024, output_height)}"


class ArkSeedreamImageProvider:
    name = "ark_seedream"

    def __init__(self, model_override: str = "") -> None:
        settings = get_settings()
        self.base_url = (settings.image_generation_base_url or settings.llm_api_base).rstrip("/")
        self.api_key = settings.image_generation_api_key or settings.llm_api_key
        self.model = model_override or settings.image_generation_model
        self.timeout = settings.image_generation_timeout_seconds
        self.watermark = settings.image_generation_watermark

    def generate(
        self,
        *,
        source_url: str,
        source_urls: Optional[list[str]] = None,
        variant_labels: Optional[list[str]] = None,
        prompt: str,
        action: str,
        count: int,
        width: int,
        height: int,
        project_id: int,
    ) -> list[str]:
        # An explicit empty list means "generate a background plate without
        # reference images". Only fall back to source_url when the caller did
        # not provide source_urls at all.
        input_urls = [source_url] if source_urls is None else source_urls
        image_inputs = [_image_input(url) for url in input_urls[:10] if url]
        urls: list[str] = []
        with httpx.Client(timeout=self.timeout) as client:
            for index in range(count):
                label = variant_labels[index] if variant_labels and index < len(variant_labels) else action
                request_prompt = f"{prompt}\n本张图片任务：{label}。保持所有参考商品的包装、瓶型、Logo与可见文字准确一致。"
                payload: dict = {
                    "model": self.model,
                    "prompt": request_prompt,
                    "size": _seedream_size(width, height),
                    "stream": False,
                    "response_format": "url",
                    "watermark": self.watermark,
                }
                if image_inputs:
                    payload["image"] = image_inputs
                response = client.post(
                    f"{self.base_url}/images/generations",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if response.is_error:
                    raise RuntimeError(f"Seedream 请求失败 ({response.status_code}): {response.text[:500]}")
                data = response.json().get("data") or []
                if not data or not data[0].get("url"):
                    raise RuntimeError("Seedream 未返回图片 URL")
                image_response = client.get(data[0]["url"])
                image_response.raise_for_status()
                suffix = mimetypes.guess_extension(image_response.headers.get("content-type", "").split(";")[0]) or ".png"
                filename = f"seedream-{uuid.uuid4().hex}{suffix}"
                urls.append(get_storage().save_bytes(image_response.content, filename, f"creative/{project_id}"))
        return urls


class ApilioImageProvider:
    """OpenAI-compatible Apilio image-generation adapter.

    This is intentionally separate from the Ark adapter: both providers expose
    the same application-facing ``generate`` contract, but their request-only
    options remain contained here.
    """

    name = "apilio"

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.apilio_image_base_url.rstrip("/")
        self.api_key = settings.apilio_image_api_key
        self.model = settings.apilio_image_model
        self.quality = settings.apilio_image_quality
        self.timeout = settings.image_generation_timeout_seconds
        if not self.api_key:
            raise RuntimeError("APILIO_IMAGE_API_KEY 未配置")
        if not self.model:
            raise RuntimeError("APILIO_IMAGE_MODEL 未配置")

    def generate(
        self,
        *,
        source_url: str,
        source_urls: Optional[list[str]] = None,
        variant_labels: Optional[list[str]] = None,
        prompt: str,
        action: str,
        count: int,
        width: int,
        height: int,
        project_id: int,
    ) -> list[str]:
        input_urls = [source_url] if source_urls is None else source_urls
        image_inputs = [_image_input(url) for url in input_urls[:10] if url]
        urls: list[str] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            for index in range(count):
                label = variant_labels[index] if variant_labels and index < len(variant_labels) else action
                request_prompt = f"{prompt}\n本张图片任务：{label}。保持所有参考商品的包装、瓶型、Logo与可见文字准确一致。"
                payload: dict = {
                    "model": self.model,
                    "prompt": request_prompt,
                    "size": f"{width}x{height}",
                    "quality": self.quality,
                    "response_format": "url",
                }
                # Apilio exposes an OpenAI-compatible image endpoint. Its image
                # models accept reference images through this compatibility
                # field; keeping it here avoids leaking provider syntax upward.
                if image_inputs:
                    payload["image"] = image_inputs
                response = client.post(
                    f"{self.base_url}/images/generations",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if response.is_error:
                    raise RuntimeError(f"Apilio 图片请求失败 ({response.status_code}): {response.text[:500]}")
                data = response.json().get("data") or []
                result = data[0] if data else {}
                image_url = result.get("url")
                if not image_url:
                    raise RuntimeError("Apilio 未返回图片 URL")
                image_response = client.get(image_url)
                image_response.raise_for_status()
                suffix = mimetypes.guess_extension(image_response.headers.get("content-type", "").split(";")[0]) or ".png"
                filename = f"apilio-{uuid.uuid4().hex}{suffix}"
                urls.append(get_storage().save_bytes(image_response.content, filename, f"creative/{project_id}"))
        return urls


class LocalDemoImageProvider:
    name = "local_demo"

    def generate(
        self,
        *,
        source_url: str,
        source_urls: Optional[list[str]] = None,
        variant_labels: Optional[list[str]] = None,
        prompt: str,
        action: str,
        count: int,
        width: int,
        height: int,
        project_id: int,
    ) -> list[str]:
        input_urls = [source_url] if source_urls is None else source_urls
        source_paths = [path for url in input_urls if (path := _local_path(url))]
        sources = [Image.open(path).convert("RGB") for path in source_paths]
        if not sources:
            sources = [Image.new("RGB", (width, height), "#eef3ef")]
        palettes = [(235, 243, 237), (242, 236, 226), (226, 240, 239), (242, 229, 232)]
        urls = []
        for index in range(count):
            variant_label = variant_labels[index] if variant_labels and index < len(variant_labels) else action
            background = Image.new("RGB", (width, height), palettes[index % len(palettes)])
            visible_sources = sources[:3]
            slot_width = int(width * 0.78 / len(visible_sources))
            for source_index, source in enumerate(visible_sources):
                fitted = ImageOps.contain(ImageEnhance.Contrast(source).enhance(1.02 + index * 0.02), (slot_width, int(height * 0.66)))
                group_width = slot_width * len(visible_sources)
                x = (width - group_width) // 2 + source_index * slot_width + (slot_width - fitted.width) // 2 + (index - 1) * 8
                y = int(height * 0.18) + (int(height * 0.62) - fitted.height) // 2
                background.paste(fitted, (x, y))
            draw = ImageDraw.Draw(background)
            draw.rounded_rectangle((24, 22, width - 24, 128), radius=20, fill=(255, 255, 255), outline=(210, 220, 212), width=2)
            draw.text((45, 38), f"方案 {chr(65 + index)} · {variant_label}", font=ImageFont.truetype(FONT_PATH, 28), fill=(25, 75, 59))
            lines = textwrap.wrap(prompt or "基于所选商品与参考素材生成", width=26)[:2]
            draw.multiline_text((45, 78), "\n".join(lines), font=ImageFont.truetype(FONT_PATH, 17), fill=(80, 92, 84), spacing=4)
            filename = f"result-{uuid.uuid4().hex}.png"
            urls.append(_save_image(background, filename, project_id))
        return urls


def get_image_provider(task_type: str = "") -> ArkSeedreamImageProvider | ApilioImageProvider | LocalDemoImageProvider:
    settings = get_settings()
    provider = settings.image_generation_provider.strip().lower()
    if provider == "apilio":
        return ApilioImageProvider()
    if provider not in {"", "ark_seedream"}:
        raise RuntimeError(f"不支持的 IMAGE_GENERATION_PROVIDER: {settings.image_generation_provider}")
    if settings.image_generation_model and (settings.image_generation_api_key or settings.llm_api_key):
        portrait=any(key in task_type.lower() for key in ["portrait","model","human"]);edit=any(key in task_type.lower() for key in ["edit","regional"]);upscale=any(key in task_type.lower() for key in ["upscale","final","4k"])
        model=settings.image_portrait_model if portrait else settings.image_edit_model if edit else settings.image_upscale_model if upscale else settings.image_product_model
        return ArkSeedreamImageProvider(model or settings.image_generation_model)
    return LocalDemoImageProvider()
