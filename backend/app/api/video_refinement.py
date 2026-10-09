from __future__ import annotations

import math
import asyncio
import logging
from pathlib import Path
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import Product, VideoAsset, VideoLineageSegment, VideoRefinementPlan, VideoRefinementPreview, VideoRefinementSnapshot, VideoRefinementTask, VideoRefinementVersion, VideoRefinementRequest, VideoRefinementAnalysisJob, VideoRefinementVoiceover
from app.schemas.video_refinement import ConfirmInput, DraftInput, PreviewInput, RefinementInput, RevisionInput, RequirementsInput, VoiceoverInput
from app.services.refinement_analysis import analyze_source, build_snapshot
from app.services.refinement_media import pin_plan_media, render_recipe, verified_asset, verified_path, used_material_ids
from app.services.refinement_plan import compile_plan, content_hash

router = APIRouter(prefix="/video-refinements", tags=["video-refinement"])
EDITABLE = ("CREATED", "PLAN_READY", "CONFIGURING", "NEEDS_HUMAN_CONFIRMATION", "MATERIAL_REQUIRED", "PLAN_CONFIRMED")
logger = logging.getLogger(__name__)


def _get_task(db: Session, auth: AuthContext, task_id: int) -> VideoRefinementTask:
    task = db.query(VideoRefinementTask).filter_by(id=task_id, tenant_id=auth.tenant_id).populate_existing().first()
    if not task:
        raise HTTPException(404, "精修任务不存在")
    return task


def _request(db, task):
    return db.query(VideoRefinementRequest).filter(VideoRefinementRequest.task_id==task.id,VideoRefinementRequest.tenant_id==task.tenant_id,VideoRefinementRequest.revision<=task.current_revision).order_by(VideoRefinementRequest.revision.desc()).first()


def _snapshot(db: Session, task: VideoRefinementTask):
    from types import SimpleNamespace
    base = db.query(VideoRefinementSnapshot).filter_by(task_id=task.id).one()
    request = _request(db,task)
    if not request:
        return base
    return SimpleNamespace(id=base.id, snapshot_json=request.snapshot_json,content_hash=request.snapshot_hash,is_mock=False, request_id=request.id)


def _options(db,task):
    request = _request(db,task)
    return request.options_json if request else {}



def _current_plan(db: Session, task: VideoRefinementTask) -> VideoRefinementPlan | None:
    return db.query(VideoRefinementPlan).filter_by(task_id=task.id, revision=task.current_revision).first()


def _serialize(db: Session, task: VideoRefinementTask) -> dict:
    snapshot, plan = _snapshot(db, task), _current_plan(db, task)
    previews = db.query(VideoRefinementPreview).filter_by(task_id=task.id, tenant_id=task.tenant_id).order_by(VideoRefinementPreview.id.desc()).all()
    version = db.query(VideoRefinementVersion).filter_by(task_id=task.id, tenant_id=task.tenant_id).first()
    from app.services.refinement_voiceover import serialize_voiceover
    job = db.query(VideoRefinementAnalysisJob).filter_by(task_id=task.id,tenant_id=task.tenant_id).order_by(VideoRefinementAnalysisJob.id.desc()).first()
    analysis = {"id":job.id,"revision":job.revision,"status":job.status,"progress":job.progress_json,"error_message":job.error_message} if job else None
    return {"analysis_state":analysis, "options":_options(db,task),
            "voiceovers":[serialize_voiceover(v) for v in db.query(VideoRefinementVoiceover).filter_by(task_id=task.id,tenant_id=task.tenant_id).order_by(VideoRefinementVoiceover.id.desc()).all()],
            "id": task.id, "source_asset_id": task.source_asset_id, "product_id": task.product_id,
            "source_hash": task.source_hash, "source_snapshot": snapshot.snapshot_json, "snapshot_hash": snapshot.content_hash,
            "user_brief": task.user_brief, "expected_result": task.expected_result, "focus": task.focus,
            "status": task.status, "current_revision": task.current_revision, "confirmed_revision": task.confirmed_revision,
            "plan": plan.plan_json if plan else None, "plan_hash": plan.plan_hash if plan else None,
            "preview_supported": not snapshot.is_mock,
            "previews": [{"id": row.id, "revision": row.revision, "plan_hash": row.plan_hash, "status": row.status,
                "output_url": row.output_url, "validation": row.validation_json, "error_message": row.error_message} for row in previews],
            "output_asset_id": version.output_asset_id if version else None,
            "output_version": version.version if version else None,
            "created_at": task.created_at.replace(tzinfo=timezone.utc).isoformat(),
            "updated_at": task.updated_at.replace(tzinfo=timezone.utc).isoformat()}


def _matching_analysis(db: Session, task: VideoRefinementTask, revision: int) -> dict | None:
    db.refresh(task)
    if task.current_revision != revision + 1 or task.status not in {"PLAN_READY", "NEEDS_HUMAN_CONFIRMATION", "MATERIAL_REQUIRED"}:
        return None
    plan = _current_plan(db, task)
    return _serialize(db, task) if plan and plan.origin == "ai" else None


def _validate_source(db: Session, task: VideoRefinementTask) -> None:
    source = db.query(VideoAsset).filter_by(id=task.source_asset_id, tenant_id=task.tenant_id).populate_existing().first()
    frozen = task.source_snapshot
    if not source or source.content_hash != task.source_hash or source.product_id != task.product_id or source.object_url != frozen["object_url"] or source.asset_kind != "final_output" or (source.width, source.height, source.fps, round(source.duration_seconds * 1000)) != (frozen["width"], frozen["height"], frozen["fps"], frozen["duration_ms"]):
        raise HTTPException(409, "源成片内容或商品关联已变化, 请重新创建精修任务")


def _validate_materials(db: Session, task: VideoRefinementTask, plan: dict) -> None:
    frozen = {row["asset_id"]: row for row in _snapshot(db, task).snapshot_json["material_catalog"]}
    for asset_id in used_material_ids(plan):
        material = db.query(VideoAsset).filter_by(id=asset_id, tenant_id=task.tenant_id, product_id=task.product_id, asset_kind="material").populate_existing().first()
        expected = frozen.get(asset_id)
        pinned_hash = next((row["file_sha256"] for row in plan.get("media_verifications", []) if row["asset_id"] == asset_id and expected and row["content_hash"] == expected["content_hash"]), None)
        allowed_hashes = {expected["content_hash"], pinned_hash} if expected else set()
        if not material or not expected or material.content_hash not in allowed_hashes or material.object_url != expected["object_url"] or material.excluded or material.reusable_status == "blocked":
            raise HTTPException(409, "采用的素材已变化或不可用, 请重新建立素材快照")


def _cas(db: Session, task: VideoRefinementTask, revision: int, values: dict) -> None:
    updated = db.query(VideoRefinementTask).filter(
        VideoRefinementTask.id == task.id, VideoRefinementTask.tenant_id == task.tenant_id,
        VideoRefinementTask.current_revision == revision, VideoRefinementTask.status.in_(EDITABLE),
    ).update({**values, "updated_at": datetime.utcnow()}, synchronize_session=False)
    if not updated:
        db.rollback()
        raise HTTPException(409, "方案已更新或任务已取消, 请刷新后重试")


def _save_plan(db: Session, task: VideoRefinementTask, revision: int, plan: dict, status: str, origin: str) -> dict:
    snapshot = _snapshot(db, task)
    if plan.get("schema_version") == "video_refinement_plan.v2":
        plan["request_snapshot_id"] = getattr(snapshot,"request_id",None)
    previous = _current_plan(db, task)
    automatic = origin == "ai" and plan.get("automatic_preview") and not plan["execution_blockers"] and any(row["enabled"] for row in plan["items"])
    try:
        pin_plan_media(snapshot.snapshot_json, plan, previous.plan_json if previous else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        _cas(db, task, revision, {"current_revision": revision + 1, "confirmed_revision": revision + 1 if automatic else None,
                                "status":"PLAN_CONFIRMED" if automatic else status})
    except HTTPException as exc:
        if origin == "ai" and exc.status_code == 409:
            existing = _matching_analysis(db, task, revision)
            if existing:
                return existing
        raise
    db.query(VideoRefinementPreview).filter(VideoRefinementPreview.task_id == task.id, VideoRefinementPreview.status != "accepted").update({"status": "superseded"}, synchronize_session=False)
    saved = VideoRefinementPlan(task_id=task.id, revision=revision + 1, source_snapshot_id=snapshot.id,
                              plan_json=plan, plan_hash=content_hash(plan), origin=origin)
    db.add(saved)
    if automatic:
        db.flush()
        db.add(VideoRefinementPreview(tenant_id=task.tenant_id,task_id=task.id,plan_id=saved.id,revision=revision+1,plan_hash=saved.plan_hash))
    db.commit()
    db.refresh(task)
    return _serialize(db, task)


def _queue_analysis(db, task, revision, snapshot, options):
    from sqlalchemy.exc import IntegrityError
    from app.services.refinement_queue import enqueue_preview
    job = db.query(VideoRefinementAnalysisJob).filter_by(task_id=task.id,revision=revision).first()
    if not job:
        job = VideoRefinementAnalysisJob(tenant_id=task.tenant_id,task_id=task.id,revision=revision,snapshot_json=snapshot,
              options_json=options,fingerprint=content_hash({"snapshot":content_hash(snapshot),"brief":task.user_brief,"options":options,"method":"v2"}))
        previous = db.query(VideoRefinementAnalysisJob).filter_by(task_id=task.id,tenant_id=task.tenant_id).order_by(VideoRefinementAnalysisJob.id.desc()).first()
        if previous:
            job.batches_json = previous.batches_json
        db.add(job)
    elif job.status in {"queued","analyzing"}:
        if bool(job.options_json.get("auto_preview")) != bool(options.get("auto_preview")):
            raise HTTPException(409,"当前分析正在运行, 请等待完成后重试")
    elif job.status == "failed":
        job.status="queued";job.error_message="";job.completed_at=None;job.options_json=options;job.snapshot_json=snapshot
        job.fingerprint=content_hash({"snapshot":content_hash(snapshot),"brief":task.user_brief,"options":options,"method":"v2"})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    enqueue_preview()


@router.get("")
def list_refinements(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    tasks = db.query(VideoRefinementTask).filter_by(tenant_id=auth.tenant_id).order_by(VideoRefinementTask.updated_at.desc()).limit(100).all()
    return [_serialize(db, task) for task in tasks]


@router.post("", status_code=201)
def create_refinement(payload: RefinementInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    source = db.query(VideoAsset).filter_by(id=payload.source_asset_id, tenant_id=auth.tenant_id, asset_kind="final_output").first()
    if not source:
        raise HTTPException(404, "成片不存在")
    product = db.query(Product).filter_by(id=source.product_id, tenant_id=auth.tenant_id).first()
    if not product:
        raise HTTPException(409, "成片关联的商品不存在")
    if not source.content_hash or not source.object_url or source.width <= 0 or source.height <= 0 or not math.isfinite(source.duration_seconds) or source.duration_seconds < .01 or not math.isfinite(source.fps) or source.fps <= 0:
        raise HTTPException(422, "源成片缺少有效的媒体信息或内容哈希, 请先完成资产验证")
    # One ordered streaming SELECT uses a consistent database statement snapshot.
    materials = list(db.query(VideoAsset).filter_by(tenant_id=auth.tenant_id,product_id=source.product_id,asset_kind="material").order_by(VideoAsset.id).yield_per(200))
    snapshot = build_snapshot(db, source, product, materials)
    snapshot["tenant_id"] = auth.tenant_id
    task = VideoRefinementTask(tenant_id=auth.tenant_id, source_asset_id=source.id, product_id=source.product_id,
        source_hash=source.content_hash, source_snapshot=snapshot["source"], user_brief=payload.user_brief,
        expected_result=payload.expected_result.strip(), focus=list(dict.fromkeys(payload.focus)), created_by=auth.user_id)
    db.add(task)
    db.flush()
    db.add(VideoRefinementSnapshot(task_id=task.id, schema_version=snapshot["schema_version"], is_mock=False,
                                   snapshot_json=snapshot, content_hash=content_hash(snapshot)))
    if snapshot["schema_version"] == "video_refinement_snapshot.v2":
        db.add(VideoRefinementRequest(tenant_id=task.tenant_id,task_id=task.id,revision=0,snapshot_json=snapshot,snapshot_hash=content_hash(snapshot),options_json={"duration_preference":payload.duration_preference,"target_duration_ms":payload.target_duration_ms,"decisions":{}}))
    db.commit()
    db.refresh(task)
    return _serialize(db, task)


@router.get("/sources")
def list_sources(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    rows = db.query(VideoAsset, Product.name).join(Product, Product.id == VideoAsset.product_id).filter(
        VideoAsset.tenant_id == auth.tenant_id, Product.tenant_id == auth.tenant_id,
        VideoAsset.asset_kind == "final_output",
    ).order_by(VideoAsset.created_at.desc(), VideoAsset.id.desc()).all()
    return [{"id": row.id, "product_id": row.product_id, "product_name": name, "filename": row.filename,
             "object_url": row.object_url, "duration_seconds": row.duration_seconds, "width": row.width, "height": row.height,
             "fps": row.fps, "has_audio": row.has_audio, "burned_subtitles": row.burned_subtitles,
             "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat()} for row, name in rows]


@router.get("/{task_id}")
def get_refinement(task_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    return _serialize(db, _get_task(db, auth, task_id))


@router.post("/{task_id}/analyze")
async def analyze(task_id: int, payload: RevisionInput, response: Response, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _get_task(db, auth, task_id)
    _validate_source(db, task)
    snapshot = _snapshot(db, task).snapshot_json
    if snapshot["is_mock"]:
        raise HTTPException(409, "旧版示例快照不能用于真实分析, 请使用真实数据创建新任务")
    if snapshot.get("schema_version") == "video_refinement_snapshot.v2":
        if task.current_revision != payload.revision or task.status not in EDITABLE:
            raise HTTPException(409,"任务或方案版本已变化, 请刷新后重试")
        _queue_analysis(db,task,payload.revision,snapshot,_options(db,task))
        response.status_code=202
        return _serialize(db,task)
    existing = _matching_analysis(db, task, payload.revision)
    if existing:
        return existing
    if task.current_revision != payload.revision or task.status not in EDITABLE:
        raise HTTPException(409, "任务或方案版本已变化, 请刷新后重试")
    try:
        plan = await analyze_source(snapshot, task.focus, task.user_brief, task.expected_result)
    except ValueError as exc:
        existing = _matching_analysis(db, task, payload.revision)
        if existing:
            return existing
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("Real refinement analysis failed for task %s", task.id)
        raise HTTPException(502, "AI 分析失败, 请稍后重试, 原方案保持原样") from exc
    status = "MATERIAL_REQUIRED" if any(row["code"] == "MATERIAL_REQUIRED" for row in plan["execution_blockers"]) else "NEEDS_HUMAN_CONFIRMATION" if any(row["confidence_level"] == "LOW" for row in plan["items"]) else "PLAN_READY"
    return _save_plan(db, task, payload.revision, plan, status, "ai")


@router.post("/{task_id}/auto-preview", status_code=202)
def auto_preview(task_id: int, payload: RevisionInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from copy import deepcopy
    task = _get_task(db,auth,task_id)
    _validate_source(db,task)
    if task.current_revision != payload.revision or task.status not in EDITABLE:
        raise HTTPException(409,"任务或方案版本已变化, 请刷新后重试")
    plan = _current_plan(db,task)
    if task.status == "PLAN_CONFIRMED" and plan and plan.plan_json.get("automatic_preview"):
        return create_preview(task_id,PreviewInput(revision=payload.revision,plan_hash=plan.plan_hash),db,auth)
    snapshot = deepcopy(_snapshot(db,task).snapshot_json)
    if snapshot.get("is_mock") or snapshot.get("schema_version") != "video_refinement_snapshot.v2":
        raise HTTPException(409,"请先刷新真实素材并使用新规则生成预览")
    if plan:
        known = {row["asset_id"]:row for row in plan.plan_json.get("analysis",{}).get("context",{}).get("material_catalog",[])}
        for material in snapshot["material_catalog"]:
            previous = known.get(material["asset_id"])
            if previous and previous["content_hash"] == material["content_hash"] and previous.get("burned_subtitles"):
                material["burned_subtitles"] = True
    options = {**_options(db,task),"auto_preview":True,"supplement_narration":"tts"}
    _queue_analysis(db,task,payload.revision,snapshot,options)
    return _serialize(db,task)


@router.patch("/{task_id}/draft")
def update_draft(task_id: int, payload: DraftInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _get_task(db, auth, task_id)
    _validate_source(db, task)
    previous = _current_plan(db, task)
    snapshot = _snapshot(db,task).snapshot_json
    from app.services.refinement_voiceover import voiceover_catalog
    requirements = previous.plan_json.get("requirements_resolution",[]) if previous else []
    if previous and previous.plan_json.get("schema_version") == "video_refinement_plan.v2":
        snapshot = previous.plan_json.get("analysis",{}).get("context",snapshot)
    try:
        plan = compile_plan(snapshot, payload.items, task.user_brief, task.expected_result, manual=True, requirements=requirements,options=_options(db,task),voiceovers=voiceover_catalog(db,task))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = _current_plan(db, task)
    if previous:
        if "analysis" in previous.plan_json:
            plan["analysis"] = previous.plan_json["analysis"]
        plan["findings_not_applied"] = previous.plan_json["findings_not_applied"]
    previous_items = {row["item_id"]: row for row in previous.plan_json["items"]} if previous else {}
    for row in plan["items"]:
        old = previous_items.get(row["item_id"])
        if old and {key: value for key, value in old.items() if key != "status"} == {key: value for key, value in row.items() if key != "status"}:
            row["status"] = old["status"]
    return _save_plan(db, task, payload.revision, plan, "CONFIGURING", "human")


@router.post("/{task_id}/plans/confirm")
def confirm_plan(task_id: int, payload: ConfirmInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _get_task(db, auth, task_id)
    _validate_source(db, task)
    plan = _current_plan(db, task)
    if not plan or payload.revision != task.current_revision or payload.plan_hash != plan.plan_hash:
        raise HTTPException(409, "方案版本或哈希已变化, 请刷新后确认")
    if task.status == "PLAN_CONFIRMED" and task.confirmed_revision == payload.revision:
        return _serialize(db, task)
    items = [row for row in plan.plan_json["items"] if row["enabled"]]
    if plan.plan_json.get("schema_version") == "video_refinement_plan.v2" and plan.plan_json["execution_blockers"]:
        raise HTTPException(422,"请先解决逐条要求和具体执行阻塞; 风险勾选不能替代处理决定")
    if not items:
        raise HTTPException(422, "当前评估没有需要渲染的修改项")
    if (plan.plan_json["execution_blockers"] or any(row["confidence_level"] == "LOW" for row in items)) and not payload.acknowledge_risks:
        raise HTTPException(422, "请先确认低置信度建议及人工处理风险")
    _validate_materials(db, task, plan.plan_json)
    from app.services.refinement_voiceover import validate_voiceovers
    try:
        validate_voiceovers(db,task,plan.plan_json)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    _cas(db, task, payload.revision, {"status": "PLAN_CONFIRMED", "confirmed_revision": payload.revision})
    db.commit()
    db.refresh(task)
    return _serialize(db, task)


@router.post("/{task_id}/cancel")
def cancel_refinement(task_id: int, payload: RevisionInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _get_task(db, auth, task_id)
    if task.status == "CANCELLED" and payload.revision == task.current_revision:
        return _serialize(db, task)
    _cas(db, task, payload.revision, {"status": "CANCELLED", "confirmed_revision": None})
    db.query(VideoRefinementPreview).filter(VideoRefinementPreview.task_id == task.id, VideoRefinementPreview.status != "accepted").update({"status": "superseded"}, synchronize_session=False)
    db.commit()
    db.refresh(task)
    return _serialize(db, task)


@router.post("/{task_id}/previews", status_code=202)
def create_preview(task_id: int, payload: PreviewInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from app.services.refinement_queue import enqueue_preview

    task = db.query(VideoRefinementTask).filter_by(id=task_id, tenant_id=auth.tenant_id).with_for_update().populate_existing().first()
    if not task:
        raise HTTPException(404, "精修任务不存在")
    plan = _current_plan(db, task)
    if task.status != "PLAN_CONFIRMED" or task.confirmed_revision != payload.revision or task.current_revision != payload.revision or not plan or plan.plan_hash != payload.plan_hash:
        raise HTTPException(409, "请先整体确认当前方案, 再生成预览")
    _validate_source(db, task)
    _validate_materials(db, task, plan.plan_json)
    try:
        from app.services.refinement_voiceover import validate_voiceovers
        validate_voiceovers(db,task,plan.plan_json)
        render_recipe(_snapshot(db, task).snapshot_json, plan.plan_json)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    preview = db.query(VideoRefinementPreview).filter_by(task_id=task.id, revision=payload.revision).first()
    if not preview:
        preview = VideoRefinementPreview(tenant_id=task.tenant_id, task_id=task.id, plan_id=plan.id, revision=payload.revision, plan_hash=plan.plan_hash)
        db.add(preview)
    elif preview.status == "failed":
        preview.status = "queued"
        preview.error_message = ""
        preview.completed_at = None
    db.commit()
    enqueue_preview()
    return _serialize(db, task)


@router.post("/{task_id}/previews/{preview_id}/accept")
async def accept_preview(task_id: int, preview_id: int, payload: PreviewInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = db.query(VideoRefinementTask).filter_by(id=task_id, tenant_id=auth.tenant_id).with_for_update().populate_existing().first()
    if not task:
        raise HTTPException(404, "精修任务不存在")
    preview = db.query(VideoRefinementPreview).filter_by(id=preview_id, task_id=task.id, tenant_id=auth.tenant_id).with_for_update().first()
    if not preview:
        raise HTTPException(404, "精修预览不存在")
    existing = db.query(VideoRefinementVersion).filter_by(preview_id=preview.id).first()
    if existing:
        return _serialize(db, task)
    plan = _current_plan(db, task)
    if task.status != "PLAN_CONFIRMED" or task.current_revision != payload.revision or task.confirmed_revision != payload.revision or preview.status != "ready" or preview.revision != payload.revision or preview.plan_hash != payload.plan_hash or not plan or plan.plan_hash != payload.plan_hash:
        raise HTTPException(409, "预览已过期或尚未通过检查, 请重新确认和生成预览")
    _validate_source(db, task)
    _validate_materials(db, task, plan.plan_json)
    snapshot = _snapshot(db, task)
    try:
        from app.services.refinement_voiceover import validate_voiceovers
        validate_voiceovers(db,task,plan.plan_json)
        await asyncio.to_thread(verified_path, verified_asset(snapshot.snapshot_json["source"], plan.plan_json))
        used = used_material_ids(plan.plan_json)
        for material in snapshot.snapshot_json["material_catalog"]:
            if material["asset_id"] in used:
                await asyncio.to_thread(verified_path, verified_asset(material, plan.plan_json))
        await asyncio.to_thread(verified_path, {"object_url": preview.output_url, "content_hash": preview.output_hash, "storage_provider": preview.storage_provider})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not preview.validation_json.get("passed") or not preview.recipe_json:
        raise HTTPException(409, "预览未通过媒体检查")
    source = db.get(VideoAsset, task.source_asset_id)
    parent = db.query(VideoRefinementVersion).filter_by(output_asset_id=source.id, tenant_id=task.tenant_id).first()
    number = (parent.version if parent else 1) + 1
    metadata = preview.validation_json
    material_ids = [row["public_asset_id"] for row in snapshot.snapshot_json["material_catalog"] if row["asset_id"] in used]
    depth = source.generation_depth + 1
    asset = VideoAsset(tenant_id=task.tenant_id, product_id=task.product_id, project_id=source.project_id,
        asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=f"{Path(source.filename).stem}-精修V{number}.mp4",
        source="video_refinement_output", library_type="finished", asset_kind="final_output", generation_depth=depth,
        origin_asset_ids=[source.asset_id, *material_ids], reusable_status="blocked" if depth > 1 else "review_required",
        burned_subtitles=metadata["burned_subtitles"], has_background_music=metadata["has_background_music"],
        object_url=preview.output_url, storage_provider=preview.storage_provider, content_hash=preview.output_hash,
        duration_seconds=metadata["duration_seconds"], width=metadata["width"], height=metadata["height"], fps=metadata["fps"],
        has_audio=metadata["has_audio"], status="uploaded")
    db.add(asset)
    db.flush()
    cursor = 0.0
    v2 = isinstance(preview.recipe_json,dict) and preview.recipe_json.get("schema_version") == "video_refinement_plan.v2"
    segments = preview.recipe_json["video_track"] if v2 else preview.recipe_json
    for index, segment in enumerate(segments):
        if v2:
            duration = (segment["output_range"]["end_ms"]-segment["output_range"]["start_ms"])/1000
            start = segment["source_range"]["start_ms"]/1000
            cursor = segment["output_range"]["start_ms"]/1000
            source_id = segment["source_asset_id"]
        else:
            duration = segment["duration_ms"]/1000
            start = segment["video_start_ms"]/1000
            source_id = segment["video_asset_id"]
        target_end = cursor+duration if index < len(segments)-1 else asset.duration_seconds
        db.add(VideoLineageSegment(tenant_id=task.tenant_id, source_asset_id=source_id,
            target_asset_id=asset.id,plan_version_id=plan.id,variant_id=f"refinement-{task.id}-r{plan.revision}",
            segment_index=index,source_start=start,source_end=start+duration,target_start=cursor,target_end=target_end,created_at=asset.created_at))
        cursor = target_end
    from app.services.video_taxonomy import inherit_public_tags_to_final
    source_assets = db.query(VideoAsset).filter(VideoAsset.tenant_id==task.tenant_id, VideoAsset.id.in_([source.id,*used])).all()
    inherit_public_tags_to_final(db, asset, source_assets, auth.user_id)
    db.add(VideoRefinementVersion(tenant_id=task.tenant_id, task_id=task.id, preview_id=preview.id,
        parent_asset_id=source.id, output_asset_id=asset.id, source_snapshot_id=snapshot.id, plan_id=plan.id,
        version=number, accepted_by=auth.user_id))
    preview.status = "accepted"
    task.status = "COMPLETED"
    db.commit()
    db.refresh(task)
    return _serialize(db, task)


@router.get("/{task_id}/materials")
def list_materials(task_id: int, cursor: str | None = None, search: str = Query(default="",max_length=200), limit: int = Query(default=30,ge=1,le=100), db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _get_task(db,auth,task_id)
    snapshot = _snapshot(db,task)
    position = 0
    token_hash = content_hash({"snapshot":snapshot.content_hash,"search":search.casefold()})[:16]
    if cursor:
        try:
            digest, raw = cursor.split(":")
            position = int(raw)
            if digest != token_hash or position < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(409,"素材游标已失效, 请重新加载当前快照")
    rows = snapshot.snapshot_json["material_catalog"]
    if search:
        rows = [r for r in rows if search.casefold() in (r["filename"]+" "+str(r.get("analysis",{}).get("summary", ""))).casefold()]
    selected = rows[position:position+limit]
    return {"items":selected,"total":len(rows),"snapshot_hash":snapshot.content_hash,"next_cursor":f"{token_hash}:{position+limit}" if position+limit < len(rows) else None}


def _invalidate_jobs(db,task):
    db.query(VideoRefinementPreview).filter(VideoRefinementPreview.task_id==task.id,VideoRefinementPreview.status!="accepted").update({"status":"superseded"},synchronize_session=False)
    db.query(VideoRefinementAnalysisJob).filter(VideoRefinementAnalysisJob.task_id==task.id,VideoRefinementAnalysisJob.status.in_(["queued","analyzing"])).update({"status":"superseded"},synchronize_session=False)


@router.post("/{task_id}/materials/refresh")
def refresh_materials(task_id: int, payload: RevisionInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from copy import deepcopy
    task = _get_task(db,auth,task_id)
    _validate_source(db,task)
    original = _snapshot(db,task).snapshot_json
    source = db.get(VideoAsset,task.source_asset_id)
    product = db.query(Product).filter_by(id=task.product_id,tenant_id=task.tenant_id).one()
    materials = list(db.query(VideoAsset).filter_by(tenant_id=task.tenant_id,product_id=task.product_id,asset_kind="material").order_by(VideoAsset.id).yield_per(200))
    snapshot = build_snapshot(db,source,product,materials)
    snapshot["source"] = deepcopy(original["source"])
    snapshot["tenant_id"] = task.tenant_id
    old,new = {r["asset_id"]:r["content_hash"] for r in original["material_catalog"]},{r["asset_id"]:r["content_hash"] for r in snapshot["material_catalog"]}
    snapshot["refresh_changes"] = {"added":len(new.keys()-old.keys()),"removed":len(old.keys()-new.keys()),"changed":sum(old[i]!=new[i] for i in old.keys()&new.keys())}
    options = deepcopy(_options(db,task))
    options.pop("accept_partial_evaluation", None)
    _cas(db,task,payload.revision,{"current_revision":payload.revision+1,"confirmed_revision":None,"status":"CREATED"})
    _invalidate_jobs(db,task)
    db.add(VideoRefinementRequest(tenant_id=task.tenant_id,task_id=task.id,revision=payload.revision+1,snapshot_json=snapshot,snapshot_hash=content_hash(snapshot),options_json=options))
    db.commit();db.refresh(task)
    return _serialize(db,task)


@router.patch("/{task_id}/requirements")
def update_requirements(task_id: int, payload: RequirementsInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from copy import deepcopy
    task = _get_task(db,auth,task_id)
    _validate_source(db,task)
    snapshot = _snapshot(db,task).snapshot_json
    if snapshot.get("schema_version") != "video_refinement_snapshot.v2":
        raise HTTPException(409,"请先刷新素材, 显式升级为新规则")
    options = deepcopy(_options(db,task))
    previous = _current_plan(db,task)
    requirements = previous.plan_json.get("requirements_resolution",[]) if previous else []
    if not requirements:
        last = db.query(VideoRefinementAnalysisJob).filter_by(task_id=task.id,tenant_id=task.tenant_id).order_by(VideoRefinementAnalysisJob.id.desc()).first()
        requirements = (last.result_json or {}).get("requirements_resolution",[]) if last else []
    known = {r["requirement_id"] for r in requirements}
    for decision in payload.decisions:
        if decision.requirement_id not in known:
            raise HTTPException(422,"回答或放弃决定引用了不存在的要求")
        if decision.decision == "answered" and not decision.answer.strip():
            raise HTTPException(422,"请填写对该要求的具体回答")
        options.setdefault("decisions",{})[decision.requirement_id] = {"decision":decision.decision,"answer":decision.answer.strip()}
    if payload.user_brief is not None and payload.user_brief != task.user_brief:
        options["decisions"] = {}
        options.pop("existing_requirements", None)
        options.pop("accept_partial_evaluation", None)
    elif payload.decisions:
        options["existing_requirements"] = [{**r,"status":r.get("planning_status",r["status"])} for r in requirements]
    if (payload.duration_preference is not None and payload.duration_preference != options.get("duration_preference")
            or "target_duration_ms" in payload.model_fields_set and payload.target_duration_ms != options.get("target_duration_ms")):
        options.pop("accept_partial_evaluation", None)
    if payload.accept_partial_evaluation is not None:
        options["accept_partial_evaluation"] = payload.accept_partial_evaluation
    if payload.duration_preference is not None:
        options["duration_preference"] = payload.duration_preference
    if "target_duration_ms" in payload.model_fields_set:
        options["target_duration_ms"] = payload.target_duration_ms
    values = {"current_revision":payload.revision+1,"confirmed_revision":None,"status":"CREATED"}
    if payload.user_brief is not None:
        values["user_brief"] = payload.user_brief
    _cas(db,task,payload.revision,values)
    _invalidate_jobs(db,task)
    db.add(VideoRefinementRequest(tenant_id=task.tenant_id,task_id=task.id,revision=payload.revision+1,snapshot_json=deepcopy(snapshot),snapshot_hash=content_hash(snapshot),options_json=options))
    db.commit();db.refresh(task)
    return _serialize(db,task)


@router.post("/{task_id}/voiceovers",status_code=202)
def create_voiceover(task_id: int, payload: VoiceoverInput, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from sqlalchemy.exc import IntegrityError
    from app.services.refinement_voiceover import voiceover_fingerprint,voiceover_parameters
    from app.services.refinement_queue import enqueue_preview
    task = _get_task(db,auth,task_id)
    plan = _current_plan(db,task)
    if task.status not in EDITABLE or task.current_revision!=payload.revision or not plan or plan.plan_hash!=payload.plan_hash:
        raise HTTPException(409,"请先保存当前草稿, 再生成局部配音")
    if plan.plan_json.get("schema_version") != "video_refinement_plan.v2":
        raise HTTPException(409,"历史方案需先刷新并使用新规则重新分析")
    item = next((r for r in plan.plan_json["items"] if r["item_id"]==payload.item_id and r["enabled"]),None)
    if not item or item["narration_strategy"]!="tts" or not item["script"].strip():
        raise HTTPException(422,"该修改项没有待生成的完整台词")
    parameters = voiceover_parameters()
    fingerprint = voiceover_fingerprint(item["script"],parameters)
    job = db.query(VideoRefinementVoiceover).filter_by(tenant_id=task.tenant_id,task_id=task.id,fingerprint=fingerprint).first()
    if not job:
        db.add(VideoRefinementVoiceover(tenant_id=task.tenant_id,task_id=task.id,revision=payload.revision,item_id=item["item_id"],fingerprint=fingerprint,script=item["script"].strip(),parameters_json=parameters))
    elif job.status == "failed":
        job.status="queued";job.error_message="";job.completed_at=None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    enqueue_preview()
    return _serialize(db,task)


@router.get("/{task_id}/voiceovers")
def list_voiceovers(task_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from app.services.refinement_voiceover import voiceover_catalog
    return list(voiceover_catalog(db,_get_task(db,auth,task_id)).values())


@router.get("/{task_id}/voiceovers/{voiceover_id}")
def get_voiceover(task_id: int, voiceover_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    from app.services.refinement_voiceover import serialize_voiceover
    task = _get_task(db,auth,task_id)
    row = db.query(VideoRefinementVoiceover).filter_by(id=voiceover_id,task_id=task.id,tenant_id=task.tenant_id).first()
    if not row:
        raise HTTPException(404,"配音作业不存在")
    return serialize_voiceover(row)
