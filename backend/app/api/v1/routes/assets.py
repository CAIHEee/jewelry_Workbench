from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.assets import AssetListResponse, AssetRecord
from app.services.asset_service import AssetService


router = APIRouter()
service = AssetService()


@router.get("/assets", response_model=AssetListResponse)
def list_assets(
    scope: str = Query(default="library"),
    owner_user_id: str | None = Query(default=None),
    module_kind: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> AssetListResponse:
    return service.list_records(
        current_user=current_user,
        scope=scope,
        owner_user_id=owner_user_id,
        module_kind=module_kind,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.post("/assets/upload", response_model=AssetRecord, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: UploadFile = File(...),
    module_kind: str = Form(...),
    source_kind: str = Form(default="manual_upload"),
    visibility: str = Form(default="private"),
    current_user: User = Depends(get_current_user),
) -> AssetRecord:
    return await service.create_input_asset(
        file=file,
        module_kind=module_kind,
        source_kind=source_kind,
        current_user=current_user,
        visibility=visibility,
    )


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str, current_user: User = Depends(get_current_user)) -> Response:
    service.delete_record(asset_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets/{asset_id}/publish", response_model=AssetRecord)
def publish_asset(asset_id: str, current_user: User = Depends(get_current_user)) -> AssetRecord:
    return service.publish_asset(asset_id, current_user=current_user)


@router.post("/assets/{asset_id}/unpublish", response_model=AssetRecord)
def unpublish_asset(asset_id: str, current_user: User = Depends(get_current_user)) -> AssetRecord:
    return service.unpublish_asset(asset_id, current_user=current_user)


@router.get("/assets/local/{object_key:path}", response_class=FileResponse)
def get_local_asset(object_key: str) -> FileResponse:
    return service.get_local_asset_response(object_key)


@router.get("/assets/content")
def get_asset_content(
    storage_url: str,
    filename: str | None = None,
    current_user: User = Depends(get_current_user),
) -> Response:
    return service.get_asset_content_response(storage_url=storage_url, current_user=current_user, filename=filename)
