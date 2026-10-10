from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Optional

import httpx

from app.config import get_settings

TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def synthesize_speech(text: str, output: Path, speaker: Optional[str] = None, *, timing_output: Optional[dict] = None, parameters: Optional[dict] = None) -> Path:
    settings = get_settings()

    if not settings.doubao_tts_api_key:
        raise RuntimeError("豆包 TTS API Key 未配置")

    request_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": settings.doubao_tts_api_key,
        "X-Api-Resource-Id": (parameters or {}).get(
            "resource_id", settings.doubao_tts_resource_id
        ),
        "X-Api-Request-Id": request_id,
    }
    payload = {"user": {"uid": "enrui-auto-edit"}, "req_params": {
        "text": text, "speaker": speaker or settings.doubao_tts_speaker,
        "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": (parameters or {}).get("speech_rate", 0),
                         "loudness_rate": (parameters or {}).get("loudness_rate", 0), "enable_subtitle": True},
    }}
    chunks: list[bytes] = []
    timestamps: list[dict] = []
    with httpx.Client(timeout=120.0) as client:
        with client.stream("POST", TTS_ENDPOINT, headers=headers, json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip(): continue
                item = json.loads(line)
                if item.get("data"):
                    chunks.append(base64.b64decode(item["data"]))
                extra = item.get("sentence") or item.get("timestamps") or item.get("additions")
                if extra:
                    if isinstance(extra, str):
                        try: extra = json.loads(extra)
                        except ValueError: extra = {}
                    timestamps.append(extra)
                code = item.get("code")
                if code not in (None, 0, 20000000):
                    raise RuntimeError(f"豆包 TTS 失败：{code} {item.get('message', '')}")
    if not chunks:
        raise RuntimeError("豆包 TTS 未返回音频数据")
    output.write_bytes(b"".join(chunks))
    if timing_output is not None:
        timing_output["provider_timestamps"] = timestamps
    return output
