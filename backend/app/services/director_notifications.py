"""Persist script assignments before sending; retry never creates another task."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import DirectorProject, DirectorSection, ShootingTask, User
from app.services.feishu import FeishuError, get_feishu_client, public_script_url

logger = logging.getLogger(__name__)
SEND_LEASE_SECONDS = 300
RUNNING_PROJECT_STATES = {"capturing", "queued", "analyzing", "generating", "mixing"}


def notification_state(row: ShootingTask) -> str:
    if row.notification_status == "sending" and (not row.notification_started_at or
            row.notification_started_at < datetime.utcnow() - timedelta(seconds=SEND_LEASE_SECONDS)):
        return "unknown"
    return row.notification_status


def locked_project(db: Session, tenant_id: str, project_id: int) -> DirectorProject:
    row = db.query(DirectorProject).filter_by(id=project_id, tenant_id=tenant_id).populate_existing().with_for_update().first()
    if not row:
        raise HTTPException(404, "编导项目不存在")
    return row


def _same_request(row, project_id, payload):
    if (row.project_id != project_id or row.recipient_type != payload.recipient_type or
            row.recipient_id != payload.recipient_id or row.note != payload.note.strip()):
        raise HTTPException(409, "同一个请求编号不能用于不同脚本、接收对象或备注")
    return row


def create_notification(db, auth, project_id, payload):
    client = get_feishu_client(auth.tenant_id, db=db)
    request_id = str(payload.request_id)
    previous = db.query(ShootingTask).filter_by(tenant_id=auth.tenant_id, notification_uuid=request_id).first()
    if previous:
        return _same_request(previous, project_id, payload)
    # Do network work before acquiring the project lock shared by create/retry/delete.
    if not db.query(DirectorProject).filter_by(id=project_id, tenant_id=auth.tenant_id).first():
        raise HTTPException(404, "编导项目不存在")
    link = public_script_url(client.settings, project_id)
    recipient = client.recipient(payload.recipient_type, payload.recipient_id)
    project = locked_project(db, auth.tenant_id, project_id)
    previous = db.query(ShootingTask).filter_by(tenant_id=auth.tenant_id, notification_uuid=request_id).first()
    if previous:
        db.commit()
        return _same_request(previous, project_id, payload)
    if project.status in RUNNING_PROJECT_STATES:
        raise HTTPException(409, "脚本正在处理中，请等待保存完成后推送")
    count = db.query(DirectorSection).filter_by(project_id=project.id, tenant_id=auth.tenant_id).count()
    if not count:
        raise HTTPException(409, "脚本暂无可推送的内容")
    existing = db.query(ShootingTask).filter(
        ShootingTask.tenant_id == auth.tenant_id, ShootingTask.project_id == project_id,
        ShootingTask.recipient_type == payload.recipient_type, ShootingTask.recipient_id == payload.recipient_id,
        ShootingTask.status.in_(["pending", "in_progress"]),
    ).first()
    if existing:
        raise HTTPException(409, "该脚本已有发给此对象的未完成任务，请在拍摄任务中查看或重试")
    user = db.query(User).filter_by(id=auth.user_id).first()
    sender = user.name if user and user.name else auth.email
    row = ShootingTask(
        tenant_id=auth.tenant_id, project_id=project.id, created_by=auth.user_id,
        channel_id="feishu", channel_name="飞书",
        department_id=recipient.get("department_id", "") if payload.recipient_type == "user" else "",
        department_name=recipient.get("department_name", "") if payload.recipient_type == "user" else "",
        assignee_id=payload.recipient_id if payload.recipient_type == "user" else "",
        assignee_name=recipient["name"] if payload.recipient_type == "user" else "",
        recipient_type=payload.recipient_type, recipient_id=payload.recipient_id, recipient_name=recipient["name"],
        note=payload.note.strip(), status="pending", notification_status="sending",
        notification_uuid=request_id, notification_app_id=client.settings.feishu_app_id,
        notification_started_at=datetime.utcnow(),
    )
    try:
        db.add(row)
        db.flush()
        message = f"【拍摄任务】{project.name}\n脚本结构：{count} 段\n发起人：{sender}"
        if row.note:
            message += f"\n备注：{row.note}"
        row.notification_payload = {"text": f"{message}\n\n查看脚本：{link}\n任务编号：{row.id}"}
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = db.query(ShootingTask).filter_by(tenant_id=auth.tenant_id, notification_uuid=request_id).first()
        if previous:
            return _same_request(previous, project_id, payload)
        raise HTTPException(409, "该脚本的通知已被其他请求创建，请刷新拍摄任务")
    return _deliver(db, row, client)


def retry_notification(db, auth, task_id):
    client = get_feishu_client(auth.tenant_id, db=db)
    row = db.query(ShootingTask).filter_by(id=task_id, tenant_id=auth.tenant_id).first()
    if not row:
        raise HTTPException(404, "拍摄任务不存在")
    if row.notification_app_id != client.settings.feishu_app_id or not row.notification_uuid:
        raise HTTPException(409, "此任务属于模拟数据或旧飞书应用，不能重试")
    client.recipient(row.recipient_type, row.recipient_id)
    locked_project(db, auth.tenant_id, row.project_id)
    row = db.query(ShootingTask).filter_by(id=task_id, tenant_id=auth.tenant_id).populate_existing().with_for_update().first()
    if not row:
        raise HTTPException(404, "拍摄任务不存在")
    if row.notification_status != "failed" or row.status == "completed":
        raise HTTPException(409, "只有明确发送失败的未完成任务可以重试；结果待确认时请先核对飞书消息")
    # CAS also protects deployments without row-level SELECT FOR UPDATE support.
    claimed = db.query(ShootingTask).filter_by(id=row.id, tenant_id=auth.tenant_id, notification_status="failed").update({
        "notification_status": "sending", "notification_started_at": datetime.utcnow(), "notification_error": "",
    }, synchronize_session=False)
    if not claimed:
        db.rollback()
        raise HTTPException(409, "此通知正在由其他请求重试")
    db.commit()
    db.refresh(row)
    return _deliver(db, row, client)


def _deliver(db, row, client):
    task_id = row.id
    try:
        message_id = client.send(row.recipient_type, row.recipient_id, row.notification_payload["text"], row.notification_uuid)
        row.notification_status = "sent"
        row.feishu_message_id = message_id
        row.notified_at = datetime.utcnow()
        row.notification_error = ""
    except FeishuError as exc:
        row.notification_status = "unknown" if exc.uncertain else "failed"
        row.notification_error = str(exc)
    except Exception as exc:
        logger.error("Unexpected Feishu delivery error for task %s (%s)", task_id, type(exc).__name__)
        row.notification_status = "unknown"
        row.notification_error = "发送结果无法确认，请核对飞书是否已收到消息"
    try:
        db.commit()
        db.refresh(row)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Could not persist Feishu delivery result for task %s", task_id)
        raise HTTPException(503, "任务已创建，但发送结果保存失败；请查看拍摄任务并核对飞书消息，不要重复推送") from exc
    return row
