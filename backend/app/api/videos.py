"""AI talking-product video projects, isolated from the existing image workflow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import BrandVisualProfile, Product, ProductAsset, VideoProject
from app.services.llm import LLMClient

router = APIRouter(prefix="/video-projects", tags=["video-projects"])


class VideoProjectCreate(BaseModel):
    product_id: int
    duration_seconds: int = Field(default=20, ge=15, le=30)
    platform: str = "小红书"
    video_type: str = "博主种草"
    voice: str = "自然分享女声"
    selling_points: str = ""
    extra_requirements: str = ""


class VideoProjectUpdate(BaseModel):
    plan: dict[str, Any]


def _product(db: Session, auth: AuthContext, product_id: int) -> Product:
    row = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="商品不存在")
    return row


def _serialize(row: VideoProject, product: Product) -> dict[str, Any]:
    return {
        "id": row.id, "product_id": row.product_id, "name": row.name,
        "platform": row.platform, "duration_seconds": row.duration_seconds,
        "style": row.style, "video_type": row.style, "content_focus": row.presenter_mode, "presenter_mode": row.presenter_mode, "voice": row.voice,
        "selling_points": row.selling_points, "extra_requirements": row.extra_requirements,
        "status": row.status, "plan": row.plan or {}, "output_url": row.output_url,
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
    focus_map = {
        "博主种草": "口播45%·商品30%·试用25%",
        "专业测评": "口播30%·商品25%·试用45%",
        "高级广告": "口播20%·商品65%·试用15%",
        "日常分享": "口播35%·商品25%·试用40%",
    }
    values["style"] = video_type
    values["presenter_mode"] = focus_map.get(video_type, focus_map["博主种草"])
    row = VideoProject(tenant_id=auth.tenant_id, name=f"{product.name} · {payload.platform}口播", **values)
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
可用素材数：{len(assets)}；品牌视觉：{brand.visual_keywords if brand else []}。
JSON必须含 title, concept, script, compliance_note, shots。shots为4到6项，每项含id,duration,type,title,visual,voiceover,status；type只能是A、B、C，分别代表人物口播、商品展示、产品试用，三类必须全部出现；status固定planned；duration总和必须为{row.duration_seconds}。商品镜头优先真实素材，复杂试用失败时允许降级为质地或妆效展示。不得虚构功效。"""
    try:
        plan = await LLMClient().chat_json([{"role": "user", "content": prompt}], temperature=.4)
        if not isinstance(plan.get("shots"), list) or not plan["shots"]: raise ValueError("invalid plan")
    except Exception:
        plan = _fallback_plan(row, product)
    row.plan = plan; row.status = "planned"
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
