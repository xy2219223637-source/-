from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.services.storage import OBJECT_ROUTE, get_storage, get_storage_by_name

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get("/objects/{token}")
def read_object(token: str):
    try:
        return RedirectResponse(get_storage().signed_url(OBJECT_ROUTE + token), status_code=307)
    except Exception as exc:
        raise HTTPException(404, "文件不存在或地址无效") from exc


@router.get("/objects/{provider}/{token}")
def read_provider_object(provider: str, token: str):
    try:
        url = f"{OBJECT_ROUTE}{provider}/{token}"
        return RedirectResponse(get_storage_by_name(provider).signed_url(url), status_code=307)
    except Exception as exc:
        raise HTTPException(404, "文件不存在或地址无效") from exc
