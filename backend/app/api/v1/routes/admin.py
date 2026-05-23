from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from app.api.deps import require_root
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.admin import (
    AdminSystemStatus,
    AdminUser,
    AdminUserCreate,
    AdminUserListResponse,
    AdminUserUpdate,
    ConfigGroup,
    ConfigGroupCreate,
    ConfigGroupUpdate,
    ConfigListResponse,
    ConfigToggleResponse,
    UserPermissionUpdateRequest,
)
from app.schemas.admin import ConfigGroupRaw
from app.schemas.auth import ModulePermissionItem, PasswordResetRequest
from app.services.config_service import ConfigService
from app.services.user_service import UserService


router = APIRouter()
service = UserService()
config_service = ConfigService()
settings = get_settings()
LOCAL_STORAGE_PATH = (Path(__file__).resolve().parents[4] / "data" / "local_assets").as_posix()


@router.get("/admin/users", response_model=AdminUserListResponse)
def list_users(_: User = Depends(require_root)) -> AdminUserListResponse:
    return service.list_users()


@router.get("/admin/system-status", response_model=AdminSystemStatus)
def get_system_status(_: User = Depends(require_root)) -> AdminSystemStatus:
    database_status = "ok"
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        database_status = "error"

    return AdminSystemStatus(
        backend_status="ok",
        database_status=database_status,
        storage_mode="local_disk",
        storage_path=LOCAL_STORAGE_PATH,
        oss_compat_enabled=settings.oss_enabled,
        environment=settings.app_env,
    )


@router.post("/admin/users", response_model=AdminUser, status_code=status.HTTP_201_CREATED)
def create_user(payload: AdminUserCreate, _: User = Depends(require_root)) -> AdminUser:
    return service.create_user(payload)


@router.patch("/admin/users/{user_id}", response_model=AdminUser)
def update_user(user_id: str, payload: AdminUserUpdate, _: User = Depends(require_root)) -> AdminUser:
    return service.update_user(user_id, payload)


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, _: User = Depends(require_root)) -> None:
    service.soft_delete_user(user_id)


@router.post("/admin/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: str, payload: PasswordResetRequest, _: User = Depends(require_root)) -> None:
    service.reset_password(user_id, payload.password)


@router.put("/admin/users/{user_id}/permissions", response_model=list[ModulePermissionItem])
def update_permissions(
    user_id: str,
    payload: UserPermissionUpdateRequest,
    _: User = Depends(require_root),
) -> list[ModulePermissionItem]:
    return service.update_permissions(user_id, payload)


# ==================== 密钥管理 API ====================


@router.get("/admin/config/keys", response_model=ConfigListResponse)
def list_config_keys(_: User = Depends(require_root)) -> ConfigListResponse:
    """获取所有密钥配置 (脱敏)"""
    return config_service.get_config_list(show_raw=False)


@router.get("/admin/config/keys/{group_key}", response_model=ConfigGroup)
def get_config_key(group_key: str, _: User = Depends(require_root)) -> ConfigGroup:
    """获取单个密钥配置 (明文，用于编辑)"""
    try:
        return config_service.get_group(group_key)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/admin/config/keys/{group_key}/raw", response_model=ConfigGroupRaw)
def get_config_key_raw(group_key: str, _: User = Depends(require_root)) -> ConfigGroupRaw:
    """获取单个密钥配置明文值，仅用于编辑回填。"""
    try:
        return ConfigGroupRaw(
            group_key=group_key,
            items=config_service.get_group_secret_values(group_key),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch("/admin/config/keys/{group_key}", response_model=ConfigGroup)
def update_config_key(
    group_key: str,
    payload: ConfigGroupUpdate,
    _: User = Depends(require_root),
) -> ConfigGroup:
    """更新密钥配置"""
    try:
        return config_service.update_group(
            group_key=group_key,
            items=payload.items,
            is_active=payload.is_active,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/admin/config/keys/{group_key}/toggle", response_model=ConfigToggleResponse)
def toggle_config_key(
    group_key: str,
    _: User = Depends(require_root),
) -> ConfigToggleResponse:
    """切换密钥启用/停用状态"""
    try:
        current = config_service.get_group(group_key)
        new_state = not current.is_active
        
        config_service.update_group(
            group_key=group_key,
            items={},
            is_active=new_state,
        )
        
        return ConfigToggleResponse(
            group_key=group_key,
            is_active=new_state,
            message=f"密钥已{'启用' if new_state else '停用'}",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/admin/config/catalog", response_model=dict)
def get_model_catalog(_: User = Depends(require_root)) -> dict:
    """获取当前激活的模型目录（供前端下拉框同步使用）"""
    return config_service.get_model_catalog_for_frontend()


@router.post("/admin/config/keys", response_model=ConfigGroup, status_code=status.HTTP_201_CREATED)
def create_config_key(
    payload: ConfigGroupCreate,
    _: User = Depends(require_root),
) -> ConfigGroup:
    """添加新供应商配置（OpenAI 兼容接口）"""
    try:
        return config_service.create_group(
            group_key=payload.group_key,
            label=payload.label,
            category=payload.category,
            base_url=payload.base_url,
            api_key=payload.api_key,
            models=payload.models,
            timeout=payload.timeout,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/admin/config/keys/{group_key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config_key(
    group_key: str,
    _: User = Depends(require_root),
) -> None:
    """删除供应商配置（仅支持自定义供应商）"""
    try:
        config_service.delete_group(group_key)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
