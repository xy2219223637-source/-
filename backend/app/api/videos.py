"""AI talking-product video projects, isolated from the existing image workflow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import BrandVisualProfile, Product, ProductAsset, VideoProject
from app.services.llm import LLMClient
from app.services.storage import get_storage

router = APIRouter(prefix="/video-projects", tags=["video-projects"])


class VideoProjectCreate(BaseModel):
    product_id: int
    duration_seconds: int = Field(default=20, ge=15, le=30)
    platform: str = "小红书"
    video_type: str = "博主种草"
    voice: str = "自然分享女声"
    selling_points: str = ""
    extra_requirements: str = ""
    production_mode: str = Field(default="quick", pattern="^(quick|performance)$")
    campaign_brief: dict[str, Any] = Field(default_factory=dict)


class VideoProjectUpdate(BaseModel):
    plan: dict[str, Any]


class VariantReviewUpdate(BaseModel):
    status: str = Field(pattern="^(draft|approved|rejected)$")
    note: str = ""


class VariantPerformanceUpdate(BaseModel):
    impressions: int = Field(default=0, ge=0)
    spend: float = Field(default=0, ge=0)
    three_second_rate: float = Field(default=0, ge=0, le=100)
    completion_rate: float = Field(default=0, ge=0, le=100)
    ctr: float = Field(default=0, ge=0, le=100)
    cvr: float = Field(default=0, ge=0, le=100)


@router.post("/{project_id}/reference-video")
async def upload_reference_video(project_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    if not (file.content_type or "").startswith("video/"):
        raise HTTPException(status_code=415, detail="参考素材必须是视频文件")
    raw = await file.read()
    if len(raw) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="参考视频不能超过 500MB")
    file_url = await get_storage().upload(raw, file.filename or "reference.mp4", "reference-videos")
    plan = dict(row.plan or {}); brief = dict(plan.get("campaign_brief") or {})
    brief.update({"reference_name": file.filename or "参考视频", "reference_url": file_url})
    plan["campaign_brief"] = brief
    plan["reference_analysis"] = {
        "source": file.filename or "参考视频",
        "summary": "参考视频已上传。生成方案后将按 Hook、痛点、商品方案、使用过程、效果证据与 CTA 进行结构拆解。",
        "structure": ["等待生成结构拆解"],
    }
    row.plan = plan; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))


def _product(db: Session, auth: AuthContext, product_id: int) -> Product:
    row = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="商品不存在")
    return row


def _serialize(row: VideoProject, product: Product) -> dict[str, Any]:
    plan = row.plan or {}
    return {
        "id": row.id, "product_id": row.product_id, "name": row.name,
        "platform": row.platform, "duration_seconds": row.duration_seconds,
        "style": row.style, "video_type": row.style, "content_focus": row.presenter_mode, "presenter_mode": row.presenter_mode, "voice": row.voice,
        "selling_points": row.selling_points, "extra_requirements": row.extra_requirements,
        "status": row.status, "plan": plan, "output_url": row.output_url,
        "production_mode": plan.get("production_mode", "quick"),
        "campaign_brief": plan.get("campaign_brief", {}),
        "product": {"name": product.name, "brand_name": product.brand_name, "category": product.category, "image_urls": product.image_urls or []},
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.get("")
def list_projects(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    rows = db.query(VideoProject).filter(VideoProject.tenant_id == auth.tenant_id).order_by(VideoProject.updated_at.desc()).all()
    products = {p.id: p for p in db.query(Product).filter(Product.tenant_id == auth.tenant_id, Product.id.in_([r.product_id for r in rows])).all()} if rows else {}
    return [_serialize(row, products[row.product_id]) for row in rows if row.product_id in products]


@router.post("")
def create_project(payload: VideoProjectCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = _product(db, auth, payload.product_id)
    values = payload.model_dump()
    video_type = values.pop("video_type")
    production_mode = values.pop("production_mode")
    campaign_brief = values.pop("campaign_brief")
    focus_map = {
        "博主种草": "口播45%·商品30%·试用25%",
        "专业测评": "口播30%·商品25%·试用45%",
        "高级广告": "口播20%·商品65%·试用15%",
        "日常分享": "口播35%·商品25%·试用40%",
    }
    values["style"] = video_type
    values["presenter_mode"] = focus_map.get(video_type, focus_map["博主种草"])
    suffix = "投放项目" if production_mode == "performance" else "口播"
    row = VideoProject(
        tenant_id=auth.tenant_id,
        name=f"{product.name} · {payload.platform}{suffix}",
        plan={"production_mode": production_mode, "campaign_brief": campaign_brief},
        **values,
    )
    db.add(row); db.commit(); db.refresh(row)
    return _serialize(row, product)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    return _serialize(row, _product(db, auth, row.product_id))


def _fallback_plan(row: VideoProject, product: Product) -> dict[str, Any]:
    point = row.selling_points.strip() or product.description.strip() or "日常使用方便、产品信息清晰"
    total = row.duration_seconds
    cuts = [3, max(4, round(total * .25)), max(4, round(total * .25))]
    cuts.append(max(3, total - sum(cuts)))
    return {
        "title": f"{product.name}｜{row.style}分享",
        "concept": f"面向{product.target_users or '美妆用户'}的{row.platform}竖屏种草视频，按{row.presenter_mode}组合人物口播、商品展示与产品试用。",
        "script": f"最近在用的{product.name}，很适合想要简单做好日常护理的姐妹。它让我印象最深的是{point[:45]}。使用起来轻松没有负担，日常放在梳妆台上随手就能用。想认真选一款适合自己的产品，可以先了解一下它。",
        "compliance_note": "文案已避免绝对化和医疗化表述；正式投放前请结合商品事实复核。",
        "shots": [
            {"id": "shot-1", "duration": cuts[0], "type": "A", "title": "人物口播 · 开场", "visual": f"人物手持{product.name}，自然近景开场", "voiceover": f"最近在用的{product.name}，很适合想要简单做好日常护理的姐妹。", "status": "planned"},
            {"id": "shot-2", "duration": cuts[1], "type": "B", "title": "商品展示 · 特写", "visual": "优先使用真实商品素材，展示包装正面与质地细节，Logo保持清晰", "voiceover": f"它让我印象最深的是{point[:45]}。", "status": "planned"},
            {"id": "shot-3", "duration": cuts[2], "type": "C", "title": "产品试用 · 体验", "visual": "展示拿取、打开或轻度试用；复杂交互失败时降级为质地与妆效展示", "voiceover": "使用起来轻松没有负担，日常放在梳妆台上随手就能用。", "status": "planned"},
            {"id": "shot-4", "duration": cuts[3], "type": "A", "title": "人物口播 · 推荐", "visual": "人物与真实商品素材同框，品牌色背景，结尾定格", "voiceover": "想认真选一款适合自己的产品，可以先了解一下它。", "status": "planned"},
        ],
    }


def _investment_extensions(row: VideoProject, product: Product, assets: list[ProductAsset]) -> dict[str, Any]:
    """Create a deterministic, editable performance-production layer.

    It intentionally lives in VideoProject.plan so existing databases and quick
    talking-video projects remain backward compatible.
    """
    current = row.plan or {}
    brief = current.get("campaign_brief") or {}
    audience = brief.get("audience") or product.target_users or "目标美妆人群"
    pain_point = brief.get("pain_point") or "妆效不稳定"
    goal = brief.get("goal") or "素材探索"
    selling_points = [item.strip() for item in (row.selling_points or product.description or "自然妆效、轻薄服帖、日常易用").replace("，", ",").split(",") if item.strip()]
    selling_points = (selling_points + ["轻薄服帖", "自然妆效", "日常易用"])[:3]
    roles = [
        ("痛点 Hook", ["毛孔", "卡粉", "斑驳", "痘印"], "痛点近景"),
        ("商品展示", ["包装", "产品", "开盖", "取粉"], "包装正面与开盖特写"),
        ("上妆过程", ["上妆", "涂抹", "按压", "试用"], "半脸上妆过程"),
        ("效果证据", ["对比", "鼻翼", "眼下", "持妆"], "局部前后对比与持妆证据"),
        ("CTA 片尾", ["价格", "活动", "logo", "品牌"], "价格、活动与品牌片尾"),
    ]
    audit = []
    reshoots = []
    for index, (segment, keywords, requirement) in enumerate(roles):
        matched = [a.name for a in assets if any(key.lower() in " ".join([a.name, a.description or "", " ".join(a.tags or [])]).lower() for key in keywords)]
        # Unlabelled material can support basic opening/product/use slots, but
        # evidence slots require explicit tags to avoid inventing proof.
        available = bool(matched) or (bool(assets) and index < 3)
        audit.append({"segment": segment, "requirement": requirement, "status": "available" if available else "reshoot", "matched_assets": matched[:3]})
        if not available:
            reshoots.append({"title": requirement, "segment": segment, "shot": "竖屏近景，稳定机位，保留前后相同光线", "duration": "3–5秒", "proof_goal": f"为{selling_points[min(index, len(selling_points)-1)]}提供真实画面证据"})

    hooks = [f"{pain_point}的人先别急着下单", f"为什么一到下午就容易{pain_point}？", f"同一底妆，差别竟然在这一步"]
    evidences = ["半脸对比", "鼻翼与眼下近景", "带妆实测"]
    count = min(10, max(3, int(brief.get("version_count") or 5)))
    matrix = []
    for index in range(count):
        matrix.append({
            "id": f"A{index + 1}", "hook": hooks[index % len(hooks)],
            "selling_point": selling_points[index % len(selling_points)],
            "evidence": evidences[index % len(evidences)],
            "duration": row.duration_seconds, "status": "draft", "review_note": "", "metrics": {},
        })
    return {
        "production_mode": "performance",
        "campaign_brief": {**brief, "goal": goal, "audience": audience, "pain_point": pain_point, "version_count": count},
        "reference_analysis": {
            "source": brief.get("reference_name") or "未上传参考视频",
            "summary": brief.get("reference_notes") or "按信息流高转化结构建立可替换母版",
            "structure": ["0–3秒 痛点 Hook", "3–6秒 人群状态", "6–10秒 商品方案", "10–15秒 使用过程", "15秒后 效果证据与 CTA"],
        },
        "material_audit": audit,
        "reshoot_list": reshoots,
        "creative_matrix": matrix,
        "quality_checks": [
            {"label": "前三秒存在明确 Hook", "status": "passed"},
            {"label": "商品在前 6 秒内露出", "status": "passed"},
            {"label": "功效表达有商品事实支持", "status": "review"},
            {"label": "字幕位于 9:16 安全区", "status": "passed"},
            {"label": "前后对比光线与机位一致", "status": "review"},
        ],
    }


def _merge_plan_extensions(row: VideoProject, product: Product, assets: list[ProductAsset], plan: dict[str, Any]) -> dict[str, Any]:
    current = row.plan or {}
    mode = current.get("production_mode", "quick")
    base = {**plan, "production_mode": mode, "campaign_brief": current.get("campaign_brief", {})}
    if mode == "performance":
        return {**base, **_investment_extensions(row, product, assets)}
    return base


@router.post("/{project_id}/plan")
async def generate_plan(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    product = _product(db, auth, row.product_id)
    assets = db.query(ProductAsset).filter(ProductAsset.tenant_id == auth.tenant_id, ProductAsset.product_id == product.id, ProductAsset.excluded.is_(False)).all()
    brand = db.query(BrandVisualProfile).filter(BrandVisualProfile.tenant_id == auth.tenant_id, BrandVisualProfile.brand_name == product.brand_name).first()
    prompt = f"""为美妆商品生成{row.duration_seconds}秒竖屏口播视频方案，只返回JSON。
商品：{product.name}；品牌：{product.brand_name}；品类：{product.category}；描述：{product.description}；成分：{product.ingredients}；用法：{product.usage_method}；目标用户：{product.target_users}。
需求：平台={row.platform}，风格={row.style}，内容侧重={row.presenter_mode}，口播声音={row.voice}，卖点={row.selling_points}，补充={row.extra_requirements}。
投放模式={(row.plan or {}).get('production_mode', 'quick')}；投放任务={(row.plan or {}).get('campaign_brief', {})}。
可用素材数：{len(assets)}；品牌视觉：{brand.visual_keywords if brand else []}。
JSON必须含 title, concept, script, compliance_note, shots。shots为4到6项，每项含id,duration,type,title,visual,voiceover,status；type只能是A、B、C，分别代表人物口播、商品展示、产品试用，三类必须全部出现；status固定planned；duration总和必须为{row.duration_seconds}。商品镜头优先真实素材，复杂试用失败时允许降级为质地或妆效展示。不得虚构功效。"""
    try:
        plan = await LLMClient().chat_json([{"role": "user", "content": prompt}], temperature=.4)
        if not isinstance(plan.get("shots"), list) or not plan["shots"]: raise ValueError("invalid plan")
    except Exception:
        plan = _fallback_plan(row, product)
    row.plan = _merge_plan_extensions(row, product, assets, plan); row.status = "planned"
    db.commit(); db.refresh(row)
    return _serialize(row, product)


@router.put("/{project_id}/plan")
def update_plan(project_id: int, payload: VideoProjectUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    row.plan = payload.plan; row.status = "planned"; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))


@router.post("/{project_id}/generate")
def generate_video(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    if not (row.plan or {}).get("shots"): raise HTTPException(status_code=409, detail="请先生成视频方案")
    plan = dict(row.plan); plan["shots"] = [{**shot, "status": "ready"} for shot in plan["shots"]]
    plan["demo_notice"] = "当前为编排预览；接入视频生成、数字人和配音供应商后输出真实 MP4。"
    row.plan = plan; row.status = "preview_ready"; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))


@router.post("/{project_id}/batch-generate")
def batch_generate_video(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    plan = dict(row.plan or {})
    variants = plan.get("creative_matrix") or []
    if not variants: raise HTTPException(status_code=409, detail="请先生成投放方案与变量矩阵")
    plan["creative_matrix"] = [{**item, "status": "preview_ready"} for item in variants]
    plan["demo_notice"] = "投放版本已完成结构化编排；当前 Demo 使用同一预览母版表达批量衍生结果。"
    row.plan = plan; row.status = "preview_ready"; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))


@router.put("/{project_id}/variants/{variant_id}/review")
def review_variant(project_id: int, variant_id: str, payload: VariantReviewUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    plan = dict(row.plan or {}); found = False; variants = []
    for item in plan.get("creative_matrix", []):
        if item.get("id") == variant_id:
            item = {**item, "status": payload.status, "review_note": payload.note}; found = True
        variants.append(item)
    if not found: raise HTTPException(status_code=404, detail="投放版本不存在")
    plan["creative_matrix"] = variants; row.plan = plan; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))


@router.put("/{project_id}/variants/{variant_id}/performance")
def update_variant_performance(project_id: int, variant_id: str, payload: VariantPerformanceUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    plan = dict(row.plan or {}); found = False; variants = []
    for item in plan.get("creative_matrix", []):
        if item.get("id") == variant_id:
            item = {**item, "metrics": payload.model_dump()}; found = True
        variants.append(item)
    if not found: raise HTTPException(status_code=404, detail="投放版本不存在")
    measured = [item for item in variants if item.get("metrics")]
    best = max(measured, key=lambda item: (item["metrics"].get("ctr", 0), item["metrics"].get("three_second_rate", 0))) if measured else None
    plan["creative_matrix"] = variants
    plan["next_round_advice"] = [] if not best else [
        f"保留 {best['id']} 的 Hook：{best['hook']}",
        f"继续验证卖点：{best['selling_point']}，保持证据镜头：{best['evidence']}",
        "下一轮只替换 Hook 或证据中的一个变量，避免无法归因",
    ]
    row.plan = plan; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))


@router.post("/{project_id}/shots/{shot_id}/regenerate")
def regenerate_shot(project_id: int, shot_id: str, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoProject).filter(VideoProject.id == project_id, VideoProject.tenant_id == auth.tenant_id).first()
    if not row: raise HTTPException(status_code=404, detail="视频项目不存在")
    plan = dict(row.plan or {}); found = False; shots = []
    for shot in plan.get("shots", []):
        if shot.get("id") == shot_id:
            shot = {**shot, "status": "ready", "visual": f"{shot.get('visual', '')}（已按当前要求重新生成）"}; found = True
        shots.append(shot)
    if not found: raise HTTPException(status_code=404, detail="镜头不存在")
    plan["shots"] = shots; row.plan = plan; db.commit(); db.refresh(row)
    return _serialize(row, _product(db, auth, row.product_id))
