from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException, status
from sqlalchemy import desc, func, or_, select

from app.db.session import SessionLocal, init_db
from app.models.generation_record import GenerationRecord
from app.models.user import User
from app.schemas.history import HistoryListResponse, HistoryRecord, HistoryRecordCreate
from app.services.storage_service import StorageService


class HistoryService:
    def __init__(self) -> None:
        self.storage_dir = Path(__file__).resolve().parents[2] / "data"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_storage_path = self.storage_dir / "history_records.json"
        self.storage_service = StorageService()
        self._bootstrapped_legacy = False
        init_db()

    def list_records(
        self,
        *,
        current_user: User,
        include_all: bool = False,
        kind: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 24,
    ) -> HistoryListResponse:
        self._bootstrap_from_legacy_file()
        with SessionLocal() as session:
            statement = select(GenerationRecord).order_by(desc(GenerationRecord.created_at))
            if current_user.role != "root" or not include_all:
                statement = statement.where(GenerationRecord.user_id == current_user.id)
            statement = self._apply_filters(statement, kind=kind, keyword=keyword)
            total = session.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
            records = session.execute(statement.offset((page - 1) * page_size).limit(page_size)).scalars().all()
            users = {user.id: user for user in session.execute(select(User)).scalars().all()}
        items = [self._with_preview_url(self._to_schema(record, current_user=current_user, users=users)) for record in records]
        return HistoryListResponse(items=self._dedupe_items(items), total=total, page=page, page_size=page_size)

    def create_record(self, payload: HistoryRecordCreate, *, current_user: User | None = None) -> HistoryRecord:
        self._bootstrap_from_legacy_file()
        user_id = current_user.id if current_user is not None else payload.user_id
        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="History record requires a user.")

        with SessionLocal() as session:
            record = GenerationRecord(
                user_id=user_id,
                job_id=payload.job_id,
                kind=payload.kind,
                title=payload.title,
                model=payload.model,
                provider=payload.provider,
                status=payload.status,
                prompt=payload.prompt,
                image_url=payload.image_url,
                storage_url=payload.storage_url,
                metadata_json=self._dump_metadata(payload.metadata),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            users = {user.id: user for user in session.execute(select(User)).scalars().all()}
        effective_user = current_user or users[user_id]
        return self._with_preview_url(self._to_schema(record, current_user=effective_user, users=users))

    def delete_record(self, history_id: str, *, current_user: User) -> None:
        self._bootstrap_from_legacy_file()
        with SessionLocal() as session:
            record = session.get(GenerationRecord, history_id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="History record not found.")
            if current_user.role != "root" and record.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this history record.")

            session.delete(record)
            session.commit()

    def _bootstrap_from_legacy_file(self) -> None:
        if self._bootstrapped_legacy or not self.legacy_storage_path.exists():
            self._bootstrapped_legacy = True
            return

        with SessionLocal() as session:
            has_records = session.execute(select(GenerationRecord.id).limit(1)).first() is not None
            root_user = session.execute(select(User).where(User.role == "root")).scalar_one_or_none()
            if has_records or root_user is None:
                self._bootstrapped_legacy = True
                return

            payload = json.loads(self.legacy_storage_path.read_text(encoding="utf-8"))
            for item in payload:
                record = HistoryRecord.model_validate(item)
                session.add(
                    GenerationRecord(
                        id=record.id,
                        user_id=root_user.id,
                        kind=record.kind,
                        title=record.title,
                        model=record.model,
                        provider=record.provider,
                        status=record.status,
                        prompt=record.prompt,
                        image_url=record.image_url,
                        storage_url=record.storage_url,
                        metadata_json=self._dump_metadata(record.metadata),
                        created_at=record.created_at,
                        updated_at=record.created_at,
                    )
                )
            session.commit()
        self._bootstrapped_legacy = True

    def _apply_filters(self, statement, *, kind: str | None, keyword: str | None):
        normalized_kind = (kind or "").strip()
        if normalized_kind and normalized_kind != "全部":
            statement = statement.where(GenerationRecord.kind == normalized_kind)

        normalized_keyword = (keyword or "").strip()
        if normalized_keyword:
            pattern = f"%{normalized_keyword}%"
            statement = statement.where(
                or_(
                    GenerationRecord.title.ilike(pattern),
                    GenerationRecord.model.ilike(pattern),
                    GenerationRecord.provider.ilike(pattern),
                    GenerationRecord.prompt.ilike(pattern),
                )
            )
        return statement

    def _to_schema(self, record: GenerationRecord, *, current_user: User, users: dict[str, User]) -> HistoryRecord:
        owner = users.get(record.user_id or "")
        return HistoryRecord(
            id=record.id,
            kind=record.kind,  # type: ignore[arg-type]
            title=record.title,
            model=record.model,
            provider=record.provider,
            status=record.status,
            prompt=record.prompt,
            user_id=record.user_id,
            job_id=record.job_id,
            image_url=record.image_url,
            storage_url=record.storage_url,
            preview_url=None,
            metadata=self._load_metadata(record.metadata_json),
            owner_username=owner.username if owner else None,
            can_delete=current_user.role == "root" or record.user_id == current_user.id,
            created_at=self._normalize_datetime(record.created_at),
        )

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

    def _build_asset_content_url(self, storage_url: str, filename: str | None = None) -> str:
        query: dict[str, str] = {"storage_url": storage_url}
        if filename:
            query["filename"] = filename
        return f"/api/v1/assets/content?{urlencode(query)}"

    def _with_preview_url(self, item: HistoryRecord) -> HistoryRecord:
        preview_url = self._build_asset_content_url(item.storage_url, item.title) if item.storage_url else item.image_url
        return item.model_copy(update={"preview_url": preview_url, "metadata": self._with_source_preview_metadata(item.metadata)})

    def _dedupe_items(self, items: list[HistoryRecord]) -> list[HistoryRecord]:
        seen: set[str] = set()
        deduped: list[HistoryRecord] = []
        for item in items:
            dedupe_key = self._build_dedupe_key(item)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(item)
        return deduped

    def _build_dedupe_key(self, item: HistoryRecord) -> str:
        result_url = item.storage_url or item.image_url
        if result_url:
            return f"{item.user_id or ''}|{item.kind}|{item.model}|{item.provider}|{result_url}"
        return f"{item.user_id or ''}|{item.kind}|{item.model}|{item.provider}|{item.prompt}|{item.created_at.isoformat()}"

    def _with_source_preview_metadata(self, metadata: dict[str, object] | None) -> dict[str, object] | None:
        if metadata is None:
            return None

        enriched = dict(metadata)
        source_storage_url = enriched.get("source_image_storage_url")
        if isinstance(source_storage_url, str) and source_storage_url:
            enriched["source_image_url"] = self._build_asset_content_url(source_storage_url)

        normalized_images: list[dict[str, object]] = []
        source_images = enriched.get("source_images")
        if isinstance(source_images, list):
            for item in source_images:
                if not isinstance(item, dict):
                    continue
                next_item = dict(item)
                storage_url = next_item.get("storage_url")
                if isinstance(storage_url, str) and storage_url:
                    filename = next_item.get("filename") if isinstance(next_item.get("filename"), str) else None
                    next_item["preview_url"] = self._build_asset_content_url(storage_url, filename)
                elif isinstance(next_item.get("source_image_url"), str):
                    next_item["preview_url"] = next_item["source_image_url"]
                normalized_images.append(next_item)
            enriched["source_images"] = normalized_images

        items = enriched.get("items")
        if isinstance(items, list):
            normalized_items: list[dict[str, object]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                next_item = dict(item)
                storage_url = next_item.get("storage_url")
                if isinstance(storage_url, str) and storage_url:
                    filename = next_item.get("view") if isinstance(next_item.get("view"), str) else None
                    next_item["preview_url"] = self._build_asset_content_url(storage_url, filename)
                    next_item["image_url"] = next_item["preview_url"]
                normalized_items.append(next_item)
            enriched["items"] = normalized_items

        return enriched

    def _collect_generated_storage_urls(self, storage_url: str | None, metadata: dict[str, object] | None) -> list[str]:
        urls: list[str] = []
        if storage_url:
            urls.append(storage_url)
        if metadata:
            items = metadata.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        nested = item.get("storage_url")
                        if isinstance(nested, str) and nested:
                            urls.append(nested)
        return urls

    def _delete_storage_url(self, storage_url: str) -> None:
        if not storage_url.startswith("oss://"):
            return
        try:
            _, object_key = self.storage_service.parse_storage_url(storage_url)
            self.storage_service.delete_object(object_key)
        except Exception:  # noqa: BLE001
            return
