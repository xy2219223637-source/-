from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional, Tuple

from app.config import get_settings

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
            "text": "".join(item["text"] for item in segments), "segments": segments, "words": words}
