"""Durable preview jobs, claimed atomically from the refinement preview table."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
import tempfile
import threading

from app.database import SessionLocal
from app.models import EcommerceVideoRefinementPlan, EcommerceVideoRefinementPreview, EcommerceVideoRefinementTask, EcommerceVideoRefinementAnalysisJob, EcommerceVideoRefinementVoiceover
from app.services.refinement_media import file_hash, render_preview, verified_path, used_material_ids, verified_asset
from app.services.storage import get_storage_by_name

logger = logging.getLogger(__name__)
_thread = None
_stop = threading.Event()
_wake = threading.Event()


def refinement_worker_state() -> dict:
    from app.config import get_settings

    if not get_settings().refinement_worker_enabled:
        return {"status": "disabled", "message": "后台精修服务已关闭, 请联系管理员启用. 已排队任务保留, 无需重复提交."}
    if not _thread or not _thread.is_alive() or _stop.is_set():
        return {"status": "stopped", "message": "后台精修服务未运行, 请联系管理员检查服务. 已排队任务保留, 无需重复提交."}
    return {"status": "running", "message": ""}


def require_refinement_worker() -> None:
    from fastapi import HTTPException

    state = refinement_worker_state()
    if state["status"] != "running":
        raise HTTPException(503, state["message"])


def enqueue_preview() -> None:
    _wake.set()


def run_preview(preview_id: int) -> None:
    from app.api.video_refinement import _snapshot, _validate_materials, _validate_source

    with SessionLocal() as db:
        pending = db.get(EcommerceVideoRefinementPreview, preview_id)
        if not pending or pending.status not in {"queued", "queued_v3"}:
            return
        queued_status = pending.status
        rendering_status = "rendering_v3" if queued_status == "queued_v3" else "rendering"
        claimed = db.query(EcommerceVideoRefinementPreview).filter_by(id=preview_id, status=queued_status).update({"status": rendering_status, "attempts": EcommerceVideoRefinementPreview.attempts + 1}, synchronize_session=False)
        db.commit()
        if not claimed:
            return
        preview = db.get(EcommerceVideoRefinementPreview, preview_id)
        attempt = preview.attempts
        task = db.get(EcommerceVideoRefinementTask, preview.task_id)
        plan = db.get(EcommerceVideoRefinementPlan, preview.plan_id)
        uploaded = ""
        try:
            if not task or task.status != "PLAN_CONFIRMED" or task.confirmed_revision != preview.revision or task.current_revision != preview.revision or plan.plan_hash != preview.plan_hash:
                preview.status = "superseded"
                db.commit()
                return
            _validate_source(db, task)
            _validate_materials(db, task, plan.plan_json)
            snapshot = _snapshot(db, task).snapshot_json
            from app.services.refinement_voiceover import validate_voiceovers
            validate_voiceovers(db,task,plan.plan_json)
            with tempfile.TemporaryDirectory(prefix=f"refinement-preview-{preview.id}-") as directory:
                output = Path(directory) / "preview.mp4"
                validation, recipe = render_preview(snapshot, plan.plan_json, output)
                # Recheck file bytes after rendering; a metadata-only comparison is insufficient.
                verified_path(verified_asset(snapshot["source"],plan.plan_json))
                used = used_material_ids(plan.plan_json)
                for material in snapshot["material_catalog"]:
                    if material["asset_id"] in used:
                        verified_path(verified_asset(material,plan.plan_json))
                storage = get_storage_by_name(snapshot["source"].get("storage_provider", "aliyun_oss"))
                output_hash = file_hash(output)
                uploaded = asyncio.run(storage.upload_path(output, f"preview-r{preview.revision}.mp4", f"ecommerce-refinements/{task.id}/previews"))
            db.refresh(task)
            db.refresh(preview)
            _validate_source(db, task)
            _validate_materials(db, task, plan.plan_json)
            validate_voiceovers(db,task,plan.plan_json)
            current_task = db.query(EcommerceVideoRefinementTask.id).filter(
                EcommerceVideoRefinementTask.id == task.id, EcommerceVideoRefinementTask.status == "PLAN_CONFIRMED",
                EcommerceVideoRefinementTask.current_revision == preview.revision, EcommerceVideoRefinementTask.confirmed_revision == preview.revision,
            ).exists()
            published = db.query(EcommerceVideoRefinementPreview).filter(
                EcommerceVideoRefinementPreview.id == preview.id, EcommerceVideoRefinementPreview.status == rendering_status,
                EcommerceVideoRefinementPreview.attempts == attempt, current_task,
            ).update({"status":"ready", "output_url":uploaded, "storage_provider":storage.name,
                "output_hash":output_hash, "validation_json":validation, "recipe_json":recipe,
                "completed_at":datetime.utcnow(), "error_message":""}, synchronize_session=False)
            if published:
                uploaded = ""
            else:
                db.query(EcommerceVideoRefinementPreview).filter_by(id=preview.id,status=rendering_status,attempts=attempt).update({"status":"superseded"},synchronize_session=False)
            db.commit()
        except Exception as exc:
            logger.exception("Refinement preview %s failed", preview_id)
            db.rollback()
            preview = db.get(EcommerceVideoRefinementPreview, preview_id)
            if preview.status == rendering_status and preview.attempts == attempt:
                preview.status = "failed"
                preview.error_message = str(exc)[:500] if isinstance(exc, ValueError) else "预览生成失败, 请检查源片和素材后重试"
                preview.completed_at = datetime.utcnow()
                db.commit()
        finally:
            if uploaded:
                try:
                    asyncio.run(storage.delete(uploaded))
                except Exception:
                    logger.exception("Could not remove unused refinement preview")


def _loop() -> None:
    from app.services.refinement_voiceover import run_voiceover
    while not _stop.is_set():
        try:
            selected = None
            with SessionLocal() as db:
                for model, runner, prefix in ((EcommerceVideoRefinementPreview,run_preview,"preview"),(EcommerceVideoRefinementVoiceover,run_voiceover,"voice"),(EcommerceVideoRefinementAnalysisJob,run_analysis,"analysis")):
                    row = db.query(model.id).filter(model.status.in_(["queued", "queued_v3"])).order_by(model.id).first()
                    if row:
                        selected = (row.id,runner,prefix)
                        break
            if selected:
                job_id,runner,prefix = selected
                # Each runner atomically claims queued rows and checks its attempt
                # before publishing. A Redis lease can outlive a restarted worker
                # for hours and prevent the recovered database job from running.
                runner(job_id)
            else:
                _wake.wait(1)
        except Exception:
            logger.exception("Refinement worker failed")
            _wake.wait(1)
        _wake.clear()


def start_refinement_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    with SessionLocal() as db:
        db.query(EcommerceVideoRefinementPreview).filter_by(status="rendering").update({"status": "queued", "error_message": "服务重启后自动重新排队"}, synchronize_session=False)
        db.query(EcommerceVideoRefinementPreview).filter_by(status="rendering_v3").update({"status":"queued_v3","error_message":"服务重启后自动重新排队"},synchronize_session=False)
        db.query(EcommerceVideoRefinementAnalysisJob).filter_by(status="analyzing").update({"status":"queued"},synchronize_session=False)
        db.query(EcommerceVideoRefinementAnalysisJob).filter_by(status="analyzing_v3").update({"status":"queued_v3"},synchronize_session=False)
        db.query(EcommerceVideoRefinementVoiceover).filter_by(status="generating").update({"status":"queued"},synchronize_session=False)
        db.query(EcommerceVideoRefinementVoiceover).filter_by(status="generating_v3").update({"status":"queued_v3"},synchronize_session=False)
        db.commit()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="refinement-preview-queue", daemon=True)
    _thread.start()


def stop_refinement_worker() -> None:
    _stop.set()
    _wake.set()


class AnalysisSuperseded(Exception):
    pass


def run_analysis(job_id):
    from app.api.video_refinement import _save_plan, _validate_source, EDITABLE
    from app.services.refinement_analysis import analyze_source
    with SessionLocal() as db:
        pending = db.get(EcommerceVideoRefinementAnalysisJob,job_id)
        if not pending or pending.status not in {"queued", "queued_v3"}:
            return
        queued_status = pending.status
        analyzing_status = "analyzing_v3" if queued_status == "queued_v3" else "analyzing"
        claimed = db.query(EcommerceVideoRefinementAnalysisJob).filter_by(id=job_id,status=queued_status).update({"status":analyzing_status,"attempts":EcommerceVideoRefinementAnalysisJob.attempts+1,"started_at":datetime.utcnow()},synchronize_session=False)
        db.commit()
        if not claimed:
            return
        job = db.get(EcommerceVideoRefinementAnalysisJob,job_id)
        attempt = job.attempts
        task = db.get(EcommerceVideoRefinementTask,job.task_id)
        def progress(batches,stats):
            db.refresh(task);db.refresh(job)
            if task.current_revision != job.revision or task.status not in EDITABLE or job.status != analyzing_status or job.attempts != attempt:
                raise AnalysisSuperseded()
            job.batches_json=batches;job.progress_json=dict(stats)
            db.commit()
        try:
            if not task or task.current_revision!=job.revision or task.status not in EDITABLE:
                raise AnalysisSuperseded()
            _validate_source(db,task)
            plan = asyncio.run(analyze_source(job.snapshot_json,task.focus,task.user_brief,task.expected_result,
                               options=job.options_json,progress=progress,batches=job.batches_json))
            if job.options_json.get("auto_preview"):
                from app.services.refinement_automation import prepare_narration
                def stage(name):
                    progress(job.batches_json,{**job.progress_json,"stage":name})
                def checkpoint(context):
                    from copy import deepcopy
                    from app.services.refinement_selection import checkpoint_key
                    key = checkpoint_key(context, task.user_brief, task.expected_result)
                    saved = {field:deepcopy(context.get(field, {})) for field in ("localization_cache", "local_candidates")}
                    progress({**job.batches_json,key:saved},{**job.progress_json,**context.get("material_evaluation", {}),"stage":"localizing"})
                plan = prepare_narration(db,task,plan,stage,checkpoint=checkpoint)
            db.refresh(task);db.refresh(job)
            if job.status!=analyzing_status or job.attempts!=attempt or task.current_revision!=job.revision:
                raise AnalysisSuperseded()
            _validate_source(db,task)
            status = "MATERIAL_REQUIRED" if any(b["code"] in {"MATERIAL_REQUIRED","MATERIAL_TOO_SHORT"} for b in plan["execution_blockers"]) else "NEEDS_HUMAN_CONFIRMATION" if plan["execution_blockers"] or any(r["confidence_level"]=="LOW" for r in plan["items"]) else "PLAN_READY"
            _save_plan(db,task,job.revision,plan,status,"ai")
            job.result_json=plan
            job.progress_json=plan.get("material_evaluation",{})
            job.status="needs_user_input" if any(r["status"] in {"CONFLICT","NEEDS_CLARIFICATION","UNSUPPORTED","AWAITING_MATERIAL"} and r.get("decision")!="waived" for r in plan.get("requirements_resolution",[])) else "ready"
            job.completed_at=datetime.utcnow();db.commit()
        except AnalysisSuperseded:
            db.rollback()
            db.query(EcommerceVideoRefinementAnalysisJob).filter_by(id=job_id,status=analyzing_status,attempts=attempt).update({"status":"superseded"},synchronize_session=False)
            db.commit()
        except Exception as exc:
            logger.exception("Refinement analysis %s failed",job_id)
            db.rollback()
            db.query(EcommerceVideoRefinementAnalysisJob).filter_by(id=job_id,status=analyzing_status,attempts=attempt).update({"status":"failed","error_message":str(exc)[:500] if isinstance(exc,ValueError) else "分析失败, 请检查 AI 配置后重试","completed_at":datetime.utcnow()},synchronize_session=False)
            db.commit()
