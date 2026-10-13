"""Content-addressed local narration assets, never implicitly bound to a draft."""
from __future__ import annotations

import asyncio
from datetime import datetime
import math
from pathlib import Path
import subprocess
import tempfile
import wave

from app.config import get_settings
from app.database import SessionLocal
from app.models import VideoRefinementTask, VideoRefinementVoiceover
from app.services.director_workflow import _ffmpeg_executable
from app.services.refinement_media import file_hash, verified_path
from app.services.refinement_plan import content_hash
from app.services.storage import get_storage_by_name
from app.services.transcription import transcribe_configured_media
from app.services.tts import synthesize_speech


def voiceover_parameters():
    settings = get_settings()
    return {"speaker":settings.doubao_tts_speaker,"speech_rate":0,"loudness_rate":0,
            "resource_id":settings.doubao_tts_resource_id,"format":"pcm_s16le","sample_rate":48000,"implementation":"refinement-tts-v3"}


def voiceover_fingerprint(script, parameters):
    return content_hash({"script":script.strip(),"parameters":parameters})


def serialize_voiceover(row):
    audio = (row.timing_json or {}).get("audio")
    audio = audio if isinstance(audio, dict) else {}
    return {"id":row.id,"revision":row.revision,"item_id":row.item_id,"fingerprint":row.fingerprint,
            "script":row.script,"parameters":row.parameters_json,"status":row.status.removesuffix("_v3"),"attempts":row.attempts,
            "object_url":row.object_url,"storage_provider":row.storage_provider,"content_hash":row.content_hash,
            "duration_ms":row.duration_ms,"sample_count":audio.get("sample_count"),
            "sample_rate":audio.get("sample_rate"),"timing":row.timing_json or {},"error_message":row.error_message}


def ensure_sample_metadata(db, voice):
    audio = (voice.timing_json or {}).get("audio", {})
    if voice.status != "ready" or (isinstance(audio, dict) and type(audio.get("sample_count")) is int and audio["sample_count"] > 0
                                  and audio.get("sample_rate") == 48000 and audio.get("channels") == 2 and audio.get("format") == "pcm_s16le"):
        return
    task = db.query(VideoRefinementTask).filter_by(id=voice.task_id,tenant_id=voice.tenant_id).populate_existing().first()
    if not task or task.status in {"CANCELLED", "COMPLETED"}:
        raise ValueError("已取消或已接受任务不能回写配音样本元数据")
    with wave.open(str(verified_path(serialize_voiceover(voice))), "rb") as stream:
        if stream.getframerate() != 48000 or stream.getsampwidth() != 2 or stream.getnchannels() != 2:
            raise ValueError("旧配音不是标准 PCM, 请显式重新生成配音")
        audio = {"sample_count":stream.getnframes(),"sample_rate":stream.getframerate(),"channels":2,"format":"pcm_s16le"}
    voice.timing_json = {**(voice.timing_json or {}),"audio":audio}
    db.commit()


def voiceover_catalog(db, task):
    return {row.id:serialize_voiceover(row) for row in db.query(VideoRefinementVoiceover).filter_by(task_id=task.id,tenant_id=task.tenant_id).all()}


def normalize_provider_timing(values, duration_ms):
    """Accept complete, finite provider cues in seconds without inventing offsets."""
    segments, words = [], []
    sentences = {}

    def cue(value, start_key="start", end_key="end", text_key="text"):
        if not isinstance(value, dict):
            return None
        start, end, text = value.get(start_key), value.get(end_key), value.get(text_key)
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration_ms / 1000
                or not isinstance(text, str) or not text.strip()):
            return None
        return {"start":start,"end":end,"text":text.strip()}

    for value in values:
        if isinstance(value, dict) and "words" in value:
            entries = value["words"]
            if entries == []:  # Initial streaming metadata has no alignment yet.
                continue
            if not isinstance(entries, list):
                return {"segments":[],"words":[]}
            sentence_words = [cue(entry,"startTime","endTime","word") for entry in entries]
            if (not sentence_words or any(word is None for word in sentence_words)
                    or any(left["end"] > right["start"] for left, right in zip(sentence_words,sentence_words[1:]))):
                return {"segments":[],"words":[]}
            text = value.get("text")
            if not isinstance(text, str) or not text.strip():
                return {"segments":[],"words":[]}
            key = (sentence_words[0]["start"],text.strip())
            previous = sentences.get(key,[])
            # The stream may repeat metadata or extend the same sentence.
            shorter, longer = sorted((previous,sentence_words),key=len)
            if longer[:len(shorter)] != shorter:
                return {"segments":[],"words":[]}
            sentences[key] = longer
        else:
            entries = value if isinstance(value,list) else value.get("segments",[]) if isinstance(value,dict) else []
            for entry in entries:
                segment = cue(entry)
                if segment is None:
                    return {"segments":[],"words":[]}
                if segment not in segments:
                    segments.append(segment)

    for (_, text), sentence_words in sentences.items():
        spoken_text = "".join(word["text"] for word in sentence_words)
        if ("".join(char for char in spoken_text if char.isalnum()).casefold()
                != "".join(char for char in text if char.isalnum()).casefold()):
            return {"segments":[],"words":[]}
        segments.append({"start":sentence_words[0]["start"],"end":sentence_words[-1]["end"],"text":text})
        words.extend(sentence_words)
    segments.sort(key=lambda entry:entry["start"])
    words.sort(key=lambda entry:entry["start"])
    if any(left["end"] > right["start"] for left, right in zip(segments,segments[1:])):
        return {"segments":[],"words":[]}
    return {"segments":segments,"words":words}


def align_voiceover_timing(timing, output, script, duration_ms):
    timing.update(normalize_provider_timing(timing.get("provider_timestamps",[]),duration_ms))
    if timing["segments"]:
        timing["alignment"] = "provider"
    else:
        aligned = transcribe_configured_media(output,script)
        timing.update(segments=aligned.get("segments",[]),words=aligned.get("words",[]),alignment="asr" if aligned.get("enabled") else "unavailable")


def run_voiceover(job_id):
    with SessionLocal() as db:
        pending = db.get(VideoRefinementVoiceover,job_id)
        if not pending or pending.status not in {"queued", "queued_v3"}:
            return
        queued_status = pending.status
        generating_status = "generating_v3" if queued_status == "queued_v3" else "generating"
        claimed = db.query(VideoRefinementVoiceover).filter_by(id=job_id,status=queued_status).update({"status":generating_status,"attempts":VideoRefinementVoiceover.attempts+1,"started_at":datetime.utcnow()},synchronize_session=False)
        db.commit()
        if not claimed:
            return
        job = db.get(VideoRefinementVoiceover,job_id)
        attempt = job.attempts
        uploaded = ""
        storage = None
        try:
            task = db.get(VideoRefinementTask,job.task_id)
            if not task or task.status in {"CANCELLED","COMPLETED"} or task.current_revision != job.revision:
                job.status = "superseded"; db.commit(); return
            with tempfile.TemporaryDirectory(prefix=f"refinement-voice-{job.id}-") as directory:
                raw, output = Path(directory)/"raw.mp3", Path(directory)/"voice.wav"
                timing = {}
                synthesize_speech(job.script,raw,speaker=job.parameters_json["speaker"],parameters=job.parameters_json,timing_output=timing)
                process = subprocess.run([_ffmpeg_executable(),"-y","-i",str(raw),"-vn","-ar","48000","-ac","2","-c:a","pcm_s16le",str(output)],capture_output=True,timeout=180)
                if process.returncode or not output.is_file():
                    raise ValueError("生成配音无法完整解码")
                with wave.open(str(output),"rb") as stream:
                    timing["audio"] = {"sample_count":stream.getnframes(),"sample_rate":stream.getframerate(),"channels":stream.getnchannels(),"format":"pcm_s16le"}
                    duration_ms = round(stream.getnframes()*1000/stream.getframerate())
                if duration_ms <= 0:
                    raise ValueError("配音服务返回空音频")
                align_voiceover_timing(timing,output,job.script,duration_ms)
                storage = get_storage_by_name(task.source_snapshot.get("storage_provider","aliyun_oss"))
                digest = file_hash(output)
                uploaded = asyncio.run(storage.upload_path(output,f"voice-{job.id}.wav",f"refinements/{task.id}/voiceovers"))
            db.refresh(task)
            active = db.query(VideoRefinementTask.id).filter(VideoRefinementTask.id==task.id,VideoRefinementTask.current_revision==job.revision,VideoRefinementTask.status.notin_(["CANCELLED","COMPLETED"])).exists()
            published = db.query(VideoRefinementVoiceover).filter(VideoRefinementVoiceover.id==job.id,VideoRefinementVoiceover.status==generating_status,VideoRefinementVoiceover.attempts==attempt,active).update({"status":"ready","object_url":uploaded,"storage_provider":storage.name,"content_hash":digest,"duration_ms":duration_ms,"timing_json":timing,"error_message":"","completed_at":datetime.utcnow()},synchronize_session=False)
            if published:
                uploaded = ""
            else:
                db.query(VideoRefinementVoiceover).filter_by(id=job.id,status=generating_status,attempts=attempt).update({"status":"superseded"},synchronize_session=False)
            db.commit()
        except Exception as exc:
            db.rollback()
            db.query(VideoRefinementVoiceover).filter_by(id=job_id,status=generating_status,attempts=attempt).update({"status":"failed","error_message":str(exc)[:500] if isinstance(exc,ValueError) else "局部配音生成失败, 请检查豆包配置后重试","completed_at":datetime.utcnow()},synchronize_session=False)
            db.commit()
        finally:
            if uploaded and storage:
                asyncio.run(storage.delete(uploaded))


def validate_voiceovers(db, task, plan):
    catalog = voiceover_catalog(db,task)
    for expected in plan.get("voiceover_assets",[]):
        actual = catalog.get(expected["id"])
        if not actual or actual["status"] != "ready" or actual["fingerprint"] != expected["fingerprint"] or actual["content_hash"] != expected["content_hash"] or actual["duration_ms"] != expected["duration_ms"]:
            raise ValueError("配音资产已失效, 请重新生成并绑定")
        if plan.get("schema_version") == "video_refinement_plan.v3" and any(actual.get(key) != expected.get(key) for key in ("sample_count","sample_rate")):
            raise ValueError("配音样本元数据已变化, 请重新编译方案")
        verified_path(actual)
