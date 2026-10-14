from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, load_only
from app.services.generation_summary import generation_summary_rows

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import CreativeGeneration, CreativeProject, Product, ProductAsset, StoryboardModule

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _project_path(project_id: int) -> str:
    return f"/creative-projects/{project_id}/storyboard"


def _utc_iso(value: datetime) -> str:
    return f"{value.isoformat()}Z"


def _effective_status(generation: CreativeGeneration) -> str:
    if generation.status == "running" and generation.updated_at < datetime.utcnow() - timedelta(minutes=30):
        return "interrupted"
    return generation.status


@router.get("/stats")
def get_stats(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)) -> dict:
    """Tenant-wide dashboard for the current creative-project workflow."""
    projects = (
        db.query(CreativeProject)
        .options(load_only(CreativeProject.id, CreativeProject.product_id, CreativeProject.name,
                           CreativeProject.platform, CreativeProject.status, CreativeProject.review_status,
                           CreativeProject.updated_at))
        .filter(CreativeProject.tenant_id == auth.tenant_id)
        .order_by(CreativeProject.updated_at.desc())
        .all()
    )
    project_ids = [project.id for project in projects]
    products = db.query(Product).options(load_only(Product.id, Product.name, Product.image_urls, Product.detail_image_urls)).filter(Product.tenant_id == auth.tenant_id).all()
    products_by_id = {product.id: product for product in products}
    product_names = {product.id: product.name for product in products}
    product_ids_with_assets = {
        product_id for (product_id,) in (
            db.query(ProductAsset.product_id)
            .filter(
                ProductAsset.tenant_id == auth.tenant_id,
                ProductAsset.product_id.isnot(None),
                ProductAsset.excluded.is_(False),
            )
            .distinct()
            .all()
        )
    }

    modules = (
        db.query(StoryboardModule)
        .options(load_only(StoryboardModule.project_id, StoryboardModule.preview_node_id, StoryboardModule.final_node_id))
        .filter(StoryboardModule.tenant_id == auth.tenant_id)
        .all()
    )
    modules_by_project: dict[int, list[StoryboardModule]] = defaultdict(list)
    for module in modules:
        modules_by_project[module.project_id].append(module)

    generations = generation_summary_rows(
        db.query(CreativeGeneration)
        .filter(CreativeGeneration.tenant_id == auth.tenant_id)
        .order_by(CreativeGeneration.updated_at.desc())
    )
    generations_by_project: dict[int, list[CreativeGeneration]] = defaultdict(list)
    for generation in generations:
        generations_by_project[generation.project_id].append(generation)

    completed_pages = sum(
        1 for module in modules if module.preview_node_id or module.final_node_id
    )
    generated_images = sum(
        len(generation.result_node_ids or [])
        for generation in generations
        if generation.status == "completed"
    )
    failed_tasks = sum(_effective_status(generation) in {"failed", "interrupted"} for generation in generations)
    in_progress_projects = sum(
        project.status != "completed" and project.review_status != "finalized"
        for project in projects
    )

    recent_projects = []
    for project in projects[:8]:
        project_modules = modules_by_project.get(project.id, [])
        completed = sum(
            bool(module.preview_node_id or module.final_node_id)
            for module in project_modules
        )
        recent_projects.append({
            "id": project.id,
            "name": project.name,
            "product_name": product_names.get(project.product_id, f"商品 #{project.product_id}"),
            "platform": project.platform,
            "status": project.status,
            "review_status": project.review_status,
            "completed_pages": completed,
            "total_pages": len(project_modules),
            "updated_at": _utc_iso(project.updated_at),
            "path": _project_path(project.id),
        })

    recent_project_tasks = []
    projects_with_tasks = sorted(
        (project for project in projects if generations_by_project.get(project.id)),
        key=lambda project: generations_by_project[project.id][0].updated_at,
        reverse=True,
    )
    for project in projects_with_tasks[:8]:
        jobs = generations_by_project[project.id]
        statuses = [_effective_status(job) for job in jobs]
        running = statuses.count("running")
        pending = statuses.count("pending")
        interrupted = statuses.count("interrupted")
        failed = statuses.count("failed") + interrupted
        completed = statuses.count("completed")
        status = "running" if running else "pending" if pending else "interrupted" if interrupted else "failed" if failed else "completed"
        trigger = (jobs[0].context_snapshot or {}).get("triggered_by") or {}
        recent_project_tasks.append({
            "project_id": project.id,
            "project_name": project.name,
            "status": status,
            "running": running,
            "pending": pending,
            "completed": completed,
            "failed": failed,
            "generated_images": sum(len(job.result_node_ids or []) for job in jobs if job.status == "completed"),
            "created_at": _utc_iso(jobs[0].created_at),
            "updated_at": _utc_iso(jobs[0].updated_at),
            "triggered_by": trigger.get("email") or "历史任务未记录",
            "trigger_source": (jobs[0].context_snapshot or {}).get("trigger_source") or "unknown",
            "path": _project_path(project.id),
        })

    todos = []
    for project in projects:
        project_jobs = generations_by_project.get(project.id, [])
        failed = sum(job.status == "failed" for job in project_jobs)
        interrupted = sum(_effective_status(job) == "interrupted" for job in project_jobs)
        product = products_by_id.get(project.product_id)
        has_product_images = bool(
            product and (
                (product.image_urls or [])
                or (product.detail_image_urls or [])
                or product.id in product_ids_with_assets
            )
        )
        if failed:
            title = f"{failed} 个生成任务失败"
            action_label = "查看并重试"
            kind = "failed"
        elif interrupted:
            title = f"{interrupted} 个生成任务长时间未更新，可能已中断"
            action_label = "检查任务"
            kind = "blocked"
        elif not has_product_images:
            title = "缺少商品图片素材，可能影响生成效果"
            action_label = "补充素材"
            kind = "missing_material"
        else:
            continue
        todos.append({
            "project_id": project.id,
            "project_name": project.name,
            "title": title,
            "action_label": action_label,
            "kind": kind,
            "path": (
                f"/task-center?project_id={project.id}&status=failed"
                if kind == "failed"
                else f"/task-center?project_id={project.id}&status=attention"
                if kind == "blocked"
                else _project_path(project.id)
            ),
            "updated_at": _utc_iso(project.updated_at),
        })
        if len(todos) == 8:
            break

    last_project = recent_projects[0] if recent_projects else None
    return {
        "summary": {
            "project_count": len(project_ids),
            "in_progress_projects": in_progress_projects,
            "completed_pages": completed_pages,
            "failed_tasks": failed_tasks,
            "generated_images": generated_images,
        },
        "recent_projects": recent_projects,
        "recent_project_tasks": recent_project_tasks,
        "todos": todos,
        "last_project": last_project,
    }
