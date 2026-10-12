from __future__ import annotations

import asyncio
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional, Tuple

from app.config import get_settings


def transcribe_configured_media(source: Path, initial_prompt: str = "") -> dict[str, Any]:
    """Use the project's configured ASR, with the same seconds-based media contract."""
    from app.services.llm import LLMClient, resolve_director_asr_provider
    from app.services.refinement_media import probe
    settings = get_settings()
    provider = resolve_director_asr_provider(settings)
    if provider in {"none", "disabled"}:
        return {"enabled": False, "segments": [], "words": [], "text": ""}
    duration_ms = round(probe(source)["duration_seconds"] * 1000)
    with tempfile.TemporaryDirectory(prefix="refinement-asr-") as directory:
        from app.services.director_workflow import _ffmpeg_executable
        audio = Path(directory) / "speech.mp3"
        process = subprocess.run([_ffmpeg_executable(),"-y","-i",str(source),"-vn","-ar","16000","-ac","1","-c:a","libmp3lame",str(audio)],capture_output=True,timeout=120)
        if process.returncode or not audio.is_file():
            raise ValueError("无法提取用于语音识别的音轨")
        rows = asyncio.run(LLMClient().transcribe(audio))
    segments, words = [], []
    for row in rows:
        start, end = row.get("start_ms"), row.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= duration_ms + 100:
            raise ValueError("语音识别没有返回可信的分句时间, 请检查 ASR 服务配置")
        end = min(end, duration_ms)
        if end <= start:
            continue
        segments.append({"start":start/1000,"end":end/1000,"text":row["text"]})
        for word in row.get("words", []):
            a, b = word["start_ms"], word["end_ms"]
            if start <= a < b <= end:
                words.append({"start":a/1000,"end":b/1000,"text":word["text"]})
    return {"enabled":True,"model":settings.director_asr_model,"provider":provider,
            "segments":segments,"words":words,"text":"".join(c["text"] for c in segments),
            "speech_duration_seconds":sum(c["end"]-c["start"] for c in segments)}

_model: Any = None
_model_key: Optional[Tuple[str, str, str]] = None
_lock = threading.Lock()


def _get_model() -> Any:
    global _model, _model_key
    settings = get_settings()
    key = (settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    with _lock:
        if _model is None or _model_key != key:
            from faster_whisper import WhisperModel
            _model = WhisperModel(key[0], device=key[1], compute_type=key[2])
            _model_key = key
    return _model


def transcribe_media(source: Path, initial_prompt: str = "") -> dict[str, Any]:
    settings = get_settings()
    if not settings.whisper_enabled:
        return {"enabled": False, "segments": [], "words": [], "text": ""}
    segments_iter, info = _get_model().transcribe(
        str(source), language="zh", beam_size=5, vad_filter=True, word_timestamps=True,
        condition_on_previous_text=True, initial_prompt=initial_prompt or None,
    )
    segments, words = [], []
    for segment in segments_iter:
        text = segment.text.strip()
        segments.append({"start": round(float(segment.start), 3), "end": round(float(segment.end), 3), "text": text})
        for word in segment.words or []:
            token = word.word.strip()
            if token:
                words.append({"start": round(float(word.start), 3), "end": round(float(word.end), 3), "text": token,
                              "probability": round(float(word.probability), 4)})
    return {"enabled": True, "model": settings.whisper_model, "language": info.language,
            "language_probability": round(float(info.language_probability), 4),
            "speech_duration_seconds": round(float(info.duration_after_vad),3),
            "text": "".join(item["text"] for item in segments), "segments": segments, "words": words}
