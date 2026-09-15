from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.services.qianchuan.service import QianchuanService, get_qianchuan_service
from app.services.qianchuan.uploads import UploadService
from app.services.qianchuan.reports import ReportService

router = APIRouter(prefix="/qianchuan", tags=["千川授权"])
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_asset_id: int | None = Field(default=None, gt=0)
    connection_id: UUID | None = None


@router.post("/oauth/sessions", status_code=201)
def create_session(payload: SessionCreate, request: Request, response: Response,
                   auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db),
                   service: QianchuanService = Depends(get_qianchuan_service)):
    if request.headers.get("origin") and request.headers["origin"] != service.origin:
        raise HTTPException(400, "请从配置的应用地址登录后发起授权")
    result = service.create_session(db, auth, payload.video_asset_id,
                                    str(payload.connection_id) if payload.connection_id else None)
    response.set_cookie(service.cookie_name(result["session_id"]), result.pop("nonce"), max_age=600,
                        secure=not service.local_http, httponly=True, samesite="lax", path="/")
    response.headers.update(NO_STORE)
    return result


@router.get("/oauth/callback")
def callback(request: Request, db: Session = Depends(get_db),
             service: QianchuanService = Depends(get_qianchuan_service)):
    params = request.query_params
    state, code, error = params.get("state", ""), params.get("auth_code"), params.get("error")
    if (not 32 <= len(state) <= 256 or (bool(code) == bool(error))
            or any(len(params.getlist(key)) > 1 for key in ("state", "auth_code", "error"))
            or (code is not None and len(code) > 4096) or (error is not None and len(error) > 256)):
        raise HTTPException(400, "授权回调参数无效", headers=NO_STORE)
    try:
        session_id = service.callback(db, state, request.cookies, code, error)
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **NO_STORE}
        raise
    response = RedirectResponse(f"{service.origin}/qianchuan/oauth/result?session_id={session_id}",
                                status_code=303, headers=NO_STORE)
    response.delete_cookie(service.cookie_name(session_id), path="/",
                           secure=not service.local_http, httponly=True, samesite="lax")
    return response


@router.get("/oauth/sessions/{session_id}")
def session_result(session_id: UUID, response: Response, auth: AuthContext = Depends(current_auth),
                   db: Session = Depends(get_db), service: QianchuanService = Depends(get_qianchuan_service)):
    response.headers.update(NO_STORE)
    return service.session_result(db, auth, str(session_id))


@router.get("/accounts")
def accounts(response: Response, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db),
             service: QianchuanService = Depends(get_qianchuan_service)):
    response.headers.update(NO_STORE)
    return service.accounts(db, auth)


@router.post("/connections/{connection_id}/sync", status_code=202)
def sync(connection_id: UUID, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db),
         service: QianchuanService = Depends(get_qianchuan_service)):
    service.request_sync(db, auth, str(connection_id))
    return {"status": "accepted"}


@router.delete("/connections/{connection_id}", status_code=204)
def disable(connection_id: UUID, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db),
            service: QianchuanService = Depends(get_qianchuan_service)):
    service.disable(db, auth, str(connection_id))
    return Response(status_code=204)


class UploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_asset_id: int = Field(gt=0)
    binding_id: UUID
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    is_aigc: StrictBool


@router.post("/connections/{connection_id}/remove", status_code=204)
def remove_connection(connection_id: UUID, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db),
                      service: QianchuanService = Depends(get_qianchuan_service)):
    service.disable(db, auth, str(connection_id), remove=True)
    return Response(status_code=204)


@router.post("/uploads", status_code=202)
def create_upload(payload: UploadCreate, response: Response, auth: AuthContext = Depends(current_auth),
                  db: Session = Depends(get_db), service: QianchuanService = Depends(get_qianchuan_service)):
    response.headers.update(NO_STORE)
    return UploadService(service).create(db, auth, payload.video_asset_id, str(payload.binding_id),
                                         payload.filename, payload.is_aigc)


@router.get("/uploads")
def uploads(response: Response, video_asset_id: int = Query(gt=0), auth: AuthContext = Depends(current_auth),
            db: Session = Depends(get_db), service: QianchuanService = Depends(get_qianchuan_service)):
    response.headers.update(NO_STORE)
    return UploadService(service).list(db, auth, video_asset_id)


@router.post("/uploads/{upload_id}/retry", status_code=202)
def retry_upload(upload_id: UUID, response: Response, auth: AuthContext = Depends(current_auth),
                 db: Session = Depends(get_db), service: QianchuanService = Depends(get_qianchuan_service)):
    response.headers.update(NO_STORE)
    return UploadService(service).retry(db, auth, str(upload_id))


@router.get("/uploads/{upload_id}/report")
def upload_report(upload_id: UUID, response: Response, days: int = Query(default=7, ge=1, le=180),
                  auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db),
                  service: QianchuanService = Depends(get_qianchuan_service)):
    response.headers.update(NO_STORE)
    return ReportService(service).get(db, auth, str(upload_id), days)
