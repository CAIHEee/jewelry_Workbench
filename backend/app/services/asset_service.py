from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import httpx
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import desc, func, or_, select

from app.db.session import SessionLocal, init_db
from app.models.asset_record import AssetRecord as AssetRecordModel
from app.models.user import User
from app.schemas.assets import AssetListResponse, AssetRecord
from app.services.storage_service import StorageService


class AssetService:
    def __init__(self) -> None:
        self.storage_dir = Path(__file__).resolve().parents[2] / "data"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.local_asset_root = self.storage_dir / "local_assets"
        self.local_asset_root.mkdir(parents=True, exist_ok=True)
        self.storage_service = StorageService()
        init_db()

    def list_records(
        self,
        *,
        current_user: User,
        scope: str = "library",
        owner_user_id: str | None = None,
        module_kind: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 24,
    ) -> AssetListResponse:
        with SessionLocal() as session:
            scoped_statement = select(AssetRecordModel)
            scoped_statement = self._apply_scope(scoped_statement, current_user=current_user, scope=scope, owner_user_id=owner_user_id)
            scoped_statement = self._apply_filters(scoped_statement, module_kind=module_kind, keyword=keyword)

            total = session.execute(select(func.count()).select_from(scoped_statement.subquery())).scalar_one()

            statement = scoped_statement.order_by(desc(AssetRecordModel.created_at)).offset((page - 1) * page_size).limit(page_size)
            records = session.execute(statement).scalars().all()
            owners = self._load_user_map(session)
        return AssetListResponse(
            items=[self._to_schema(record, current_user=current_user, owners=owners) for record in records],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create_input_asset(
        self,
        *,
        file: UploadFile,
        module_kind: str,
        current_user: User,
        source_kind: str = "input_upload",
        metadata: dict[str, object] | None = None,
        visibility: str = "private",
    ) -> AssetRecord:
        if current_user.role != "root" and visibility != "private":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only root can upload community assets directly.")

        file_bytes = await file.read()
        await file.seek(0)

        content_type = file.content_type or "image/png"
        filename = file.filename or "upload.png"
        asset_name = self._build_uploaded_asset_name(
            username=current_user.username,
            filename=filename,
            content_type=content_type,
        )
        object_key = self._build_object_key(module_kind=module_kind, filename=filename, content_type=content_type)

        if self.storage_service.is_configured():
            stored = self.storage_service.upload_bytes(
                object_key=object_key,
                content=file_bytes,
                content_type=content_type,
            )
            storage_url = stored.storage_url
        else:
            local_path = self._resolve_local_object_path(object_key)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(file_bytes)
            storage_url = f"local://{object_key}"

        with SessionLocal() as session:
            record = AssetRecordModel(
                user_id=current_user.id,
                owner_user_id=current_user.id,
                name=asset_name,
                source_kind=source_kind,
                module_kind=module_kind,
                visibility=visibility,
                storage_url=storage_url,
                mime_type=content_type,
                file_size=len(file_bytes),
                metadata_json=self._dump_metadata(metadata),
                published_at=datetime.now(timezone.utc) if visibility == "community" else None,
                published_by_user_id=current_user.id if visibility == "community" else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            owners = self._load_user_map(session)
        return self._to_schema(record, current_user=current_user, owners=owners)

    def create_stored_asset_record(
        self,
        *,
        current_user: User,
        name: str,
        module_kind: str,
        storage_url: str,
        mime_type: str | None,
        file_size: int | None,
        source_kind: str = "generated_output",
        metadata: dict[str, object] | None = None,
        visibility: str = "private",
    ) -> AssetRecord:
        with SessionLocal() as session:
            record = AssetRecordModel(
                user_id=current_user.id,
                owner_user_id=current_user.id,
                name=name,
                source_kind=source_kind,
                module_kind=module_kind,
                visibility=visibility,
                storage_url=storage_url,
                mime_type=mime_type,
                file_size=file_size,
                metadata_json=self._dump_metadata(metadata),
                published_at=datetime.now(timezone.utc) if visibility == "community" else None,
                published_by_user_id=current_user.id if visibility == "community" else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            owners = self._load_user_map(session)
        return self._to_schema(record, current_user=current_user, owners=owners)

    def publish_asset(self, asset_id: str, *, current_user: User) -> AssetRecord:
        with SessionLocal() as session:
            record = session.get(AssetRecordModel, asset_id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
            if record.owner_user_id != current_user.id and current_user.role != "root":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot publish assets owned by another user.")
            record.visibility = "community"
            record.published_at = datetime.now(timezone.utc)
            record.published_by_user_id = current_user.id
            session.commit()
            session.refresh(record)
            owners = self._load_user_map(session)
        return self._to_schema(record, current_user=current_user, owners=owners)

    def unpublish_asset(self, asset_id: str, *, current_user: User) -> AssetRecord:
        with SessionLocal() as session:
            record = session.get(AssetRecordModel, asset_id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
            if current_user.role != "root" and record.published_by_user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot unpublish another user's community asset.")
            if record.owner_user_id != current_user.id and current_user.role != "root":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot move another user's asset to private.")
            record.visibility = "private"
            record.published_at = None
            record.published_by_user_id = None
            session.commit()
            session.refresh(record)
            owners = self._load_user_map(session)
        return self._to_schema(record, current_user=current_user, owners=owners)

    def delete_record(self, asset_id: str, *, current_user: User) -> None:
        with SessionLocal() as session:
            record = session.get(AssetRecordModel, asset_id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
            if not self._can_delete(record, current_user):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this asset.")

            if self.storage_service.is_configured() and record.storage_url.startswith("oss://"):
                try:
                    _, object_key = self.storage_service.parse_storage_url(record.storage_url)
                    self.storage_service.delete_object(object_key)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Failed to delete OSS asset: {exc}",
                    ) from exc
            elif record.storage_url.startswith("local://"):
                local_path = self._resolve_local_object_path(record.storage_url.removeprefix("local://"))
                if local_path.exists():
                    local_path.unlink()

            session.delete(record)
            session.commit()

    def get_local_asset_response(self, object_key: str) -> FileResponse:
        local_path = self._resolve_local_object_path(object_key)
        if not local_path.exists() or not local_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local asset not found.")
        media_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        return FileResponse(local_path, media_type=media_type, filename=local_path.name)

    def get_asset_content_response(self, storage_url: str, *, current_user: User, filename: str | None = None) -> Response:
        self.ensure_storage_url_access(storage_url=storage_url, current_user=current_user)
        content, media_type, resolved_filename = self.fetch_asset_bytes(storage_url, filename=filename)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{quote(resolved_filename)}"'},
        )

    def ensure_storage_url_access(self, *, storage_url: str, current_user: User) -> None:
        if storage_url.startswith("/api/v1/assets/content"):
            parsed = urlparse(storage_url)
            nested_storage_url = parse_qs(parsed.query).get("storage_url", [None])[0]
            if nested_storage_url:
                self.ensure_storage_url_access(storage_url=nested_storage_url, current_user=current_user)
            return
        if storage_url.startswith(("http://", "https://", "/api/v1/assets/local/")):
            return
        with SessionLocal() as session:
            record = session.execute(select(AssetRecordModel).where(AssetRecordModel.storage_url == storage_url)).scalar_one_or_none()
            if record is None:
                return
            if current_user.role == "root":
                return
            if record.visibility == "community":
                return
            if record.owner_user_id == current_user.id:
                return
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this asset.")

    def build_asset_content_url(self, storage_url: str, filename: str | None = None) -> str:
        query: dict[str, str] = {"storage_url": storage_url}
        if filename:
            query["filename"] = filename
        return f"/api/v1/assets/content?{urlencode(query)}"

    def fetch_asset_bytes(self, storage_url: str, filename: str | None = None) -> tuple[bytes, str, str]:
        resolved_filename = filename or "asset"

        if storage_url.startswith("local://"):
            object_key = storage_url.removeprefix("local://")
            local_path = self._resolve_local_object_path(object_key)
            if not local_path.exists() or not local_path.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local asset not found.")
            media_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
            final_filename = self._ensure_filename_extension(filename, media_type, storage_url) if filename else local_path.name
            return local_path.read_bytes(), media_type, final_filename

        if storage_url.startswith("oss://"):
            bucket_name, object_key = self.storage_service.parse_storage_url(storage_url)
            if bucket_name != self.storage_service.settings.oss_bucket:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported OSS bucket.")
            try:
                bucket = self.storage_service._build_bucket()
                result = bucket.get_object(object_key)
                content = result.read()
                headers = getattr(result, "headers", {}) or {}
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to download OSS asset: {exc}") from exc
            media_type = headers.get("Content-Type") or mimetypes.guess_type(object_key)[0] or "application/octet-stream"
            return content, media_type, self._ensure_filename_extension(resolved_filename, media_type, storage_url)

        if storage_url.startswith(("http://", "https://")):
            try:
                response = httpx.get(storage_url, timeout=60.0)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to download remote asset: {exc}") from exc
            if response.status_code >= 400:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to download remote asset, status={response.status_code}.")
            media_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
            return response.content, media_type, self._ensure_filename_extension(resolved_filename, media_type, storage_url)

        if storage_url.startswith("/api/v1/assets/content"):
            parsed = urlparse(storage_url)
            nested_storage_url = parse_qs(parsed.query).get("storage_url", [None])[0]
            nested_filename = parse_qs(parsed.query).get("filename", [filename])[0]
            if not nested_storage_url:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing storage_url in asset content URL.")
            return self.fetch_asset_bytes(nested_storage_url, filename=nested_filename)

        if storage_url.startswith("/api/v1/assets/local/"):
            object_key = unquote(storage_url.removeprefix("/api/v1/assets/local/"))
            return self.fetch_asset_bytes(f"local://{object_key}", filename=filename)

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported storage URL.")

    def _apply_scope(self, statement, *, current_user: User, scope: str, owner_user_id: str | None):
        if current_user.role == "root":
            if scope == "community":
                return statement.where(AssetRecordModel.visibility == "community")
            if scope == "mine":
                return statement.where(AssetRecordModel.owner_user_id == current_user.id)
            if scope == "owner" and owner_user_id:
                return statement.where(AssetRecordModel.owner_user_id == owner_user_id)
            return statement

        if scope == "community":
            return statement.where(AssetRecordModel.visibility == "community")
        if scope == "mine":
            return statement.where(AssetRecordModel.owner_user_id == current_user.id)
        return statement.where(
            or_(
                AssetRecordModel.visibility == "community",
                AssetRecordModel.owner_user_id == current_user.id,
            )
        )

    def _apply_filters(self, statement, *, module_kind: str | None, keyword: str | None):
        normalized_module_kind = (module_kind or "").strip()
        if normalized_module_kind:
            statement = statement.where(AssetRecordModel.module_kind == normalized_module_kind)

        normalized_keyword = (keyword or "").strip()
        if normalized_keyword:
            keyword_pattern = f"%{normalized_keyword}%"
            statement = statement.where(
                or_(
                    AssetRecordModel.name.ilike(keyword_pattern),
                    AssetRecordModel.source_kind.ilike(keyword_pattern),
                    AssetRecordModel.module_kind.ilike(keyword_pattern),
                    AssetRecordModel.metadata_json.ilike(keyword_pattern),
                )
            )
        return statement

    def _to_schema(self, record: AssetRecordModel, *, current_user: User, owners: dict[str, User]) -> AssetRecord:
        preview_url = self.build_asset_content_url(record.storage_url, record.name)
        owner = owners.get(record.owner_user_id or "")
        return AssetRecord(
            id=record.id,
            name=record.name,
            source_kind=record.source_kind,
            module_kind=record.module_kind,
            visibility=record.visibility,
            owner_user_id=record.owner_user_id,
            owner_username=owner.username if owner else None,
            storage_url=record.storage_url,
            preview_url=preview_url,
            mime_type=record.mime_type,
            file_size=record.file_size,
            metadata=self._load_metadata(record.metadata_json),
            can_delete=self._can_delete(record, current_user),
            can_publish=self._can_publish(record, current_user),
            can_unpublish=self._can_unpublish(record, current_user),
            created_at=self._normalize_datetime(record.created_at),
        )

    def _can_delete(self, record: AssetRecordModel, current_user: User) -> bool:
        if current_user.role == "root":
            return True
        if record.visibility == "community":
            return False
        return record.owner_user_id == current_user.id

    def _can_publish(self, record: AssetRecordModel, current_user: User) -> bool:
        if record.visibility != "private":
            return False
        return current_user.role == "root" or record.owner_user_id == current_user.id

    def _can_unpublish(self, record: AssetRecordModel, current_user: User) -> bool:
        if record.visibility != "community":
            return False
        return current_user.role == "root" or (record.owner_user_id == current_user.id and record.published_by_user_id == current_user.id)

    def _load_user_map(self, session) -> dict[str, User]:
        return {user.id: user for user in session.execute(select(User)).scalars().all()}

    def _ensure_filename_extension(self, filename: str | None, media_type: str, storage_url: str) -> str:
        resolved_filename = filename or "asset"
        if Path(resolved_filename).suffix:
            return resolved_filename
        guessed_extension = mimetypes.guess_extension(media_type, strict=False)
        if guessed_extension:
            return f"{resolved_filename}{guessed_extension}"
        if storage_url.startswith("local://"):
            source_extension = Path(storage_url.removeprefix("local://")).suffix
            if source_extension:
                return f"{resolved_filename}{source_extension}"
        if storage_url.startswith("oss://"):
            _, object_key = self.storage_service.parse_storage_url(storage_url)
            source_extension = Path(object_key).suffix
            if source_extension:
                return f"{resolved_filename}{source_extension}"
        return f"{resolved_filename}.png"

    def _build_object_key(self, *, module_kind: str, filename: str, content_type: str) -> str:
        import re
        from uuid import uuid4

        safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
        extension = Path(safe_filename).suffix or self._guess_extension(content_type)
        stem = Path(safe_filename).stem or "asset"
        now = datetime.utcnow()
        return f"assets/input/{module_kind}/{now:%Y/%m/%d}/{stem}_{uuid4().hex[:8]}{extension}"

    def _build_uploaded_asset_name(self, *, username: str, filename: str, content_type: str) -> str:
        sanitized_username = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in username.strip()) or "user"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = Path(filename).suffix or self._guess_extension(content_type)
        return f"{sanitized_username}_upload_{timestamp}{extension.lower()}"

    def _resolve_local_object_path(self, object_key: str) -> Path:
        normalized_parts = [part for part in Path(object_key).parts if part not in {"", ".", ".."}]
        local_path = self.local_asset_root.joinpath(*normalized_parts)
        resolved_root = self.local_asset_root.resolve()
        resolved_path = local_path.resolve()
        if not str(resolved_path).startswith(str(resolved_root)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid local asset path.")
        return resolved_path

    def _guess_extension(self, content_type: str) -> str:
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        return mapping.get(content_type.lower(), ".png")

    def _dump_metadata(self, metadata: dict[str, object] | None) -> str | None:
        if metadata is None:
            return None
        return json.dumps(metadata, ensure_ascii=False)

    def _load_metadata(self, metadata_json: str | None) -> dict[str, object] | None:
        if not metadata_json:
            return None
        loaded = json.loads(metadata_json)
        return loaded if isinstance(loaded, dict) else None

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
