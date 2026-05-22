from datetime import datetime

from pydantic import BaseModel


class AssetRecord(BaseModel):
    id: str
    name: str
    source_kind: str
    module_kind: str | None = None
    visibility: str
    owner_user_id: str | None = None
    owner_username: str | None = None
    storage_url: str
    preview_url: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    metadata: dict[str, object] | None = None
    can_delete: bool = False
    can_publish: bool = False
    can_unpublish: bool = False
    created_at: datetime


class AssetListResponse(BaseModel):
    items: list[AssetRecord]
    total: int
    page: int
    page_size: int
