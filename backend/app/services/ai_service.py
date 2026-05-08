import asyncio
import base64
import binascii
import json
import re
import subprocess
import tempfile
from contextvars import ContextVar, Token
from datetime import datetime
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile, status
from PIL import Image
from starlette.datastructures import Headers

from app.core.config import get_settings
from app.models.user import User
from app.schemas.ai import (
    FeatureDefinition,
    FusionJobAccepted,
    FusionRequestMetadata,
    GenerationFeatureCatalog,
    GenerationResult,
    ModelCatalogResponse,
    MultiViewSplitItem,
    MultiViewSplitRequest,
    MultiViewSplitResponse,
    ProviderType,
    ReferenceImageRequestMetadata,
    TTAPIModelDefinition,
    TextToImageRequest,
)
from app.schemas.history import HistoryRecordCreate
from app.services.asset_service import AssetService
from app.services.history_service import HistoryService
from app.services.storage_service import StorageService


@dataclass(frozen=True)
class TTAPIModelConfig:
    id: str
    label: str
    provider: ProviderType
    category: str
    upstream_model_id: str
    supports_text_to_image: bool
    supports_multi_image_fusion: bool
    supports_reference_images: bool
    pricing_hint: str


MODEL_CATALOG: dict[str, TTAPIModelConfig] = {
    "gpt-image-2-all-apiyi": TTAPIModelConfig(
        id="gpt-image-2-all-apiyi",
        label="APIYI · GPT Image 2 All",
        provider=ProviderType.apiyi,
        category="image_generation",
        upstream_model_id="gpt-image-2-all",
        supports_text_to_image=True,
        supports_multi_image_fusion=True,
        supports_reference_images=True,
        pricing_hint="GPT Image 2 All via APIYI chat completions",
    ),
    "gemini-3.1-flash-image-preview": TTAPIModelConfig(
        id="gemini-3.1-flash-image-preview",
        label="APIYI · Nano Banana 2",
        provider=ProviderType.gemini,
        category="image_generation",
        upstream_model_id="gemini-3.1-flash-image-preview",
        supports_text_to_image=True,
        supports_multi_image_fusion=True,
        supports_reference_images=True,
        pricing_hint="Gemini image preview family on TTAPI",
    ),
}

_request_user: ContextVar[User | None] = ContextVar("ai_request_user", default=None)
_job_stage_callback: ContextVar[Callable[[str], None] | None] = ContextVar("ai_job_stage_callback", default=None)


class AIService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.asset_service = AssetService()
        self.history_service = HistoryService()
        self.storage_service = StorageService()
        self.storage_dir = Path(__file__).resolve().parents[2] / "data"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.local_asset_root = self.storage_dir / "local_assets"
        self.local_asset_root.mkdir(parents=True, exist_ok=True)

    def get_feature_catalog(self) -> GenerationFeatureCatalog:
        platform_label = self._upstream_platform_label()
        return GenerationFeatureCatalog(
            features=[
                FeatureDefinition(
                    key="text_to_image",
                    title="Text to image",
                    description=f"Generate jewelry design images from prompts with selectable {platform_label} models.",
                ),
                FeatureDefinition(
                    key="image_edit",
                    title="Image editing",
                    description=f"Edit or refine existing design images through {platform_label}-backed models.",
                ),
                FeatureDefinition(
                    key="multi_image_fusion",
                    title="Multi image fusion",
                    description="Upload two or more images and blend them into a new design concept.",
                ),
                FeatureDefinition(
                    key="reference_image_transform",
                    title="Reference image transform",
                    description="Transform a single reference image into edits, grayscale relief, or other derived variants.",
                ),
                FeatureDefinition(
                    key="multi_view",
                    title="Multi-view image",
                    description="Generate a single four-view composition image from one reference image.",
                ),
                FeatureDefinition(
                    key="split_multi_view",
                    title="Split multi-view image",
                    description="Split a four-view composition image into four separate view assets with adjustable cut lines.",
                ),
            ]
        )

    def get_model_catalog(self) -> ModelCatalogResponse:
        platform_label = self._upstream_platform_label()
        return ModelCatalogResponse(
            models=[
                TTAPIModelDefinition(
                    id=model.id,
                    label=model.label,
                    provider=model.provider,
                    category=model.category,
                    supports_text_to_image=model.supports_text_to_image,
                    supports_multi_image_fusion=model.supports_multi_image_fusion,
                    supports_reference_images=model.supports_reference_images,
                    pricing_hint=self._build_model_pricing_hint(model, platform_label),
                )
                for model in MODEL_CATALOG.values()
            ]
        )

    async def generate_text_to_image(
        self,
        request: TextToImageRequest,
        *,
        current_user: User,
        stage_callback: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        context_token = _request_user.set(current_user)
        stage_token = _job_stage_callback.set(stage_callback)
        model = self._get_model_or_404(request.model)
        try:
            if not model.supports_text_to_image:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model {request.model} does not support text-to-image generation.",
                )

            if model.provider == ProviderType.aiapis:
                return await self._generate_with_aiapis_gpt_image2(request=request, model=model)
            if model.provider == ProviderType.apiyi:
                return await self._generate_with_apiyi_gpt_image2_all(request=request, model=model)
            if model.provider == ProviderType.wuyin:
                return await self._generate_with_wuyin_gpt_image2(request=request, model=model)

            if self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    return await self._generate_with_gemini_apiyi(request=request, model=model)
                return await self._generate_with_flux_apiyi(request=request, model=model)

            if model.provider == ProviderType.gemini:
                return await self._generate_with_gemini(request=request, model=model)
            return await self._generate_with_flux(request=request, model=model)
        finally:
            _job_stage_callback.reset(stage_token)
            _request_user.reset(context_token)

    async def fuse_images(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        current_user: User,
        source_image_urls: list[str] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> FusionJobAccepted:
        context_token = _request_user.set(current_user)
        stage_token = _job_stage_callback.set(stage_callback)
        model = self._get_model_or_404(metadata.model)
        try:
            if not model.supports_multi_image_fusion:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model {metadata.model} does not support multi-image fusion.",
                )

            submit_files = files
            if files:
                input_assets = [
                    await self.asset_service.create_input_asset(
                        file=file,
                        module_kind="fusion",
                        current_user=current_user,
                        metadata={
                            "feature": "fusion",
                            "model": metadata.model,
                            "primary_image_index": metadata.primary_image_index,
                        },
                    )
                    for file in files
                ]
                metadata = metadata.model_copy(
                    update={
                        "source_images": [
                            {
                                "filename": asset.name,
                                "source_image_url": asset.preview_url,
                                "storage_url": asset.storage_url,
                                "preview_url": asset.preview_url,
                            }
                            for asset in input_assets
                        ]
                    }
                )
            else:
                normalized_urls = [item for item in (source_image_urls or []) if item]
                submit_files = [
                    await self._build_upload_file_from_url(url, metadata.filenames[index] if index < len(metadata.filenames) else None)
                    for index, url in enumerate(normalized_urls)
                ]
                metadata = metadata.model_copy(
                    update={
                        "source_images": [
                            self._build_source_image_reference(
                                url=url,
                                filename=metadata.filenames[index] if index < len(metadata.filenames) else f"image-{index + 1}.png",
                            )
                            for index, url in enumerate(normalized_urls)
                        ]
                    }
                )

            if model.provider == ProviderType.aiapis:
                result = await self._fuse_with_aiapis_gpt_image2(files=submit_files, metadata=metadata, model=model)
            elif model.provider == ProviderType.apiyi:
                result = await self._fuse_with_apiyi_gpt_image2_all(files=submit_files, metadata=metadata, model=model)
            elif model.provider == ProviderType.dmxapi:
                result = await self._fuse_with_dmxapi_gpt_image2(files=submit_files, metadata=metadata, model=model)
            elif model.provider == ProviderType.wuyin:
                result = await self._fuse_with_wuyin_gpt_image2(metadata=metadata, model=model)
            elif self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    result = await self._fuse_with_gemini_apiyi(files=submit_files, metadata=metadata, model=model)
                else:
                    result = await self._fuse_with_flux_apiyi(files=submit_files, metadata=metadata, model=model)
            elif model.provider == ProviderType.gemini:
                result = await self._fuse_with_gemini(files=submit_files, metadata=metadata, model=model)
            else:
                result = await self._fuse_with_flux(files=submit_files, metadata=metadata, model=model)

            return FusionJobAccepted(
                job_id=result.job_id,
                status=result.status,
                message=result.message,
                provider=result.provider,
                feature="multi_image_fusion",
                model=result.model,
                image_url=result.image_url,
                metadata=metadata,
                raw_response=result.raw_response,
            )
        finally:
            _job_stage_callback.reset(stage_token)
            _request_user.reset(context_token)

    async def transform_reference_image(
        self,
        *,
        file: UploadFile | None,
        metadata: ReferenceImageRequestMetadata,
        current_user: User,
        source_image_url: str | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        return await self.transform_reference_images(
            files=[file] if file is not None else [],
            metadata=metadata,
            current_user=current_user,
            source_image_urls=[source_image_url] if source_image_url else None,
            stage_callback=stage_callback,
        )

    async def transform_reference_images(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        current_user: User,
        source_image_urls: list[str] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        context_token = _request_user.set(current_user)
        stage_token = _job_stage_callback.set(stage_callback)
        model = self._get_model_or_404(metadata.model)
        try:
            if not model.supports_reference_images:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model {metadata.model} does not support reference-image transforms.",
                )

            submit_files = files
            if files:
                input_asset = await self.asset_service.create_input_asset(
                    file=files[0],
                    module_kind=self._map_feature_to_history_kind(metadata.feature),
                    current_user=current_user,
                    metadata={
                        "feature": metadata.feature,
                        "model": metadata.model,
                        "filename": metadata.filename,
                    },
                )
                input_assets = [input_asset]
                for file in files[1:]:
                    input_assets.append(
                        await self.asset_service.create_input_asset(
                            file=file,
                            module_kind=self._map_feature_to_history_kind(metadata.feature),
                            current_user=current_user,
                            metadata={
                                "feature": metadata.feature,
                                "model": metadata.model,
                                "filename": file.filename or "reference.png",
                            },
                        )
                    )
                metadata = metadata.model_copy(
                    update={
                        "image_count": len(input_assets),
                        "filename": input_assets[0].name,
                        "filenames": [asset.name for asset in input_assets],
                        "source_image_url": input_assets[0].preview_url,
                        "source_image_storage_url": input_assets[0].storage_url,
                        "source_images": [
                            {
                                "filename": asset.name,
                                "source_image_url": asset.preview_url,
                                "storage_url": asset.storage_url,
                                "preview_url": asset.preview_url,
                            }
                            for asset in input_assets
                        ],
                    }
                )
            elif source_image_urls:
                normalized_urls = [url for url in source_image_urls if url]
                if not normalized_urls:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Either file or source_image_url is required.",
                    )
                submit_files = [
                    await self._build_upload_file_from_url(
                        url,
                        metadata.filenames[index] if index < len(metadata.filenames) else metadata.filename,
                    )
                    for index, url in enumerate(normalized_urls)
                ]
                filenames = [
                    metadata.filenames[index] if index < len(metadata.filenames) else f"reference-{index + 1}.png"
                    for index in range(len(normalized_urls))
                ]
                metadata = metadata.model_copy(
                    update={
                        "image_count": len(normalized_urls),
                        "filename": filenames[0],
                        "filenames": filenames,
                        "source_image_url": self._resolve_source_preview_url(normalized_urls[0], filenames[0]),
                        "source_image_storage_url": normalized_urls[0] if self._is_custom_storage_url(normalized_urls[0]) else None,
                        "source_images": [
                            self._build_source_image_reference(url=url, filename=filenames[index])
                            for index, url in enumerate(normalized_urls)
                        ],
                    }
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Either file or source_image_url is required.",
                )

            if model.provider == ProviderType.aiapis:
                return await self._transform_with_aiapis_gpt_image2(files=submit_files, metadata=metadata, model=model)
            if model.provider == ProviderType.apiyi:
                return await self._transform_with_apiyi_gpt_image2_all(files=submit_files, metadata=metadata, model=model)
            if model.provider == ProviderType.dmxapi:
                return await self._transform_with_dmxapi_gpt_image2(files=submit_files, metadata=metadata, model=model)
            if model.provider == ProviderType.wuyin:
                return await self._transform_with_wuyin_gpt_image2(metadata=metadata, model=model)

            if self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    return await self._transform_with_gemini_apiyi(files=submit_files, metadata=metadata, model=model)
                return await self._transform_with_flux_apiyi(files=submit_files, metadata=metadata, model=model)

            if model.provider == ProviderType.gemini:
                return await self._transform_with_gemini(files=submit_files, metadata=metadata, model=model)
            return await self._transform_with_flux(files=submit_files, metadata=metadata, model=model)
        finally:
            _job_stage_callback.reset(stage_token)
            _request_user.reset(context_token)

    async def generate_multi_view(
        self,
        *,
        file: UploadFile | None,
        files: list[UploadFile] | None = None,
        metadata: ReferenceImageRequestMetadata,
        current_user: User,
        source_image_url: str | None = None,
        source_image_urls: list[str] | None = None,
        stage_callback: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        if metadata.feature != "multi_view":
            metadata = metadata.model_copy(update={"feature": "multi_view"})

        submit_files = files if files is not None else ([file] if file is not None else [])
        submit_source_urls = source_image_urls if source_image_urls is not None else ([source_image_url] if source_image_url else None)
        if len(submit_files) > 1 or submit_source_urls:
            return await self.transform_reference_images(
                files=submit_files,
                metadata=metadata,
                current_user=current_user,
                source_image_urls=submit_source_urls,
                stage_callback=stage_callback,
            )
        return await self.transform_reference_image(
            file=submit_files[0] if submit_files else None,
            metadata=metadata,
            current_user=current_user,
            source_image_url=None,
            stage_callback=stage_callback,
        )

    async def split_multi_view_image(
        self,
        payload: MultiViewSplitRequest,
        *,
        current_user: User,
        stage_callback: Callable[[str], None] | None = None,
    ) -> MultiViewSplitResponse:
        context_token = _request_user.set(current_user)
        stage_token = _job_stage_callback.set(stage_callback)
        image_bytes, _ = await self._download_image(payload.image_url)
        try:
            split_assets = await self._split_and_store_multi_view_assets(
                image_bytes=image_bytes,
                source_image_url=payload.image_url,
                source_image_name=payload.source_image_name,
                model=payload.model,
                split_x_ratio=payload.split_x_ratio,
                split_y_ratio=payload.split_y_ratio,
                gap_x_ratio=payload.gap_x_ratio,
                gap_y_ratio=payload.gap_y_ratio,
            )

            response = MultiViewSplitResponse(
                status="completed",
                message="Multi-view image split completed.",
                source_image_url=payload.image_url,
                split_x_ratio=payload.split_x_ratio,
                split_y_ratio=payload.split_y_ratio,
                gap_x_ratio=payload.gap_x_ratio,
                gap_y_ratio=payload.gap_y_ratio,
                items=[MultiViewSplitItem(**item) for item in split_assets],
            )
            first_item = split_assets[0] if split_assets else None
            self._persist_history(
                kind="multi_view_split",
                title=self._history_title_for_kind("multi_view_split"),
                model_id=payload.model,
                provider="system",
                status=response.status,
                prompt="Split four-grid multi-view image into separate view assets.",
                image_url=first_item["image_url"] if first_item else None,
                storage_url=first_item["storage_url"] if first_item else None,
                metadata={
                    "source_image_url": payload.image_url,
                    "source_image_name": payload.source_image_name,
                    "split_x_ratio": payload.split_x_ratio,
                    "split_y_ratio": payload.split_y_ratio,
                    "gap_x_ratio": payload.gap_x_ratio,
                    "gap_y_ratio": payload.gap_y_ratio,
                    "items": split_assets,
                },
            )
            return response
        finally:
            _job_stage_callback.reset(stage_token)
            _request_user.reset(context_token)

    async def remove_background_image(
        self,
        *,
        file: UploadFile | None,
        source_image_url: str | None,
        current_user: User,
    ) -> tuple[bytes, str]:
        context_token = _request_user.set(current_user)
        try:
            if file is not None:
                image_bytes = await file.read()
                await file.seek(0)
            elif source_image_url:
                image_bytes, _ = await self._download_image(source_image_url)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either image or source_image_url is required.")

            return await asyncio.to_thread(self._remove_background_to_white_png, image_bytes)
        finally:
            _request_user.reset(context_token)

    def _get_model_or_404(self, model_id: str) -> TTAPIModelConfig:
        model = MODEL_CATALOG.get(model_id)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown model: {model_id}",
            )
        return model

    def _require_api_key(self) -> str:
        if not self.settings.ttapi_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="TTAPI_API_KEY is not configured.",
            )
        return self.settings.ttapi_api_key

    def _require_apiyi_api_key(self) -> str:
        if not self.settings.apiyi_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="APIYI_API_KEY is not configured.",
            )
        return self.settings.apiyi_api_key

    def _require_aiapis_api_key(self) -> str:
        if not self.settings.aiapis_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AIAPIS_API_KEY is not configured.",
            )
        return self.settings.aiapis_api_key

    def _require_dmxapi_api_key(self) -> str:
        if not self.settings.dmxapi_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DMXAPI_API_KEY is not configured.",
            )
        return self.settings.dmxapi_api_key

    def _require_wuyin_api_key(self) -> str:
        if not self.settings.wuyin_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="WUYIN_API_KEY is not configured.",
            )
        return self.settings.wuyin_api_key

    def _use_apiyi(self) -> bool:
        return self.settings.ai_upstream_platform.strip().lower() == "apiyi"

    def _upstream_platform_label(self) -> str:
        return "APIYI" if self._use_apiyi() else "TTAPI"

    def _build_model_pricing_hint(self, model: TTAPIModelConfig, platform_label: str) -> str:
        if model.provider == ProviderType.apiyi:
            return "GPT Image 2 All via APIYI chat completions"
        if model.provider == ProviderType.aiapis:
            return "GPT Image 2 generation via AIAPIS relay"
        if model.provider == ProviderType.wuyin:
            return "GPT Image 2 generation via Wuyin relay"
        if model.provider == ProviderType.dmxapi:
            return "GPT Image 2 image editing via DMXAPI relay"
        if model.id == "gemini-3.1-flash-image-preview":
            return f"Nano Banana 2 image generation via {platform_label}"
        return f"{model.label} via {platform_label}"

    async def _generate_with_apiyi_gpt_image2_all(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        data = await self._post_apiyi_gpt_image2_chat(
            model=model,
            prompt=request.prompt,
            files=[],
        )
        upstream_image_url = self._extract_chat_completion_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=upstream_image_url,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Image generation completed." if stored_asset["access_url"] else "Image generation returned no asset.",
            raw_response=data,
        )
        if result.image_url:
            self._persist_history(
                kind="text_to_image",
                title=self._history_title_for_kind("text_to_image"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=request.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "upstream_platform": "apiyi",
                    "upstream_api": "chat_completions",
                    "upstream_model": model.upstream_model_id,
                    "aspect_ratio": request.aspect_ratio,
                    "size": request.size,
                    **stored_asset["metadata"],
                },
            )
        return result

    async def _generate_with_wuyin_gpt_image2(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        submit_data, final_data, job_id, image_urls = await self._submit_and_poll_wuyin_image_job(
            prompt=request.prompt,
            size=request.aspect_ratio,
            urls=None,
        )
        first_image_url = image_urls[0] if image_urls else None
        if not first_image_url:
            wuyin_status = self._extract_wuyin_status(final_data)
            message = "Wuyin GPT Image 2 task did not return any image URL."
            if wuyin_status == "failed":
                message = "Wuyin GPT Image 2 task failed."
            elif wuyin_status == "processing":
                message = "Wuyin GPT Image 2 task is still processing."
            return GenerationResult(
                job_id=job_id,
                status="failed" if wuyin_status == "failed" else "queued",
                provider=model.provider,
                model=model.id,
                image_url=None,
                message=message,
                raw_response=final_data,
            )

        stored_asset = await self._store_generated_asset(
            image_url=first_image_url,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=job_id,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Image generation completed.",
            raw_response=final_data,
        )
        self._persist_history(
            kind="text_to_image",
            title=self._history_title_for_kind("text_to_image"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=request.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "wuyin",
                "aspect_ratio": request.aspect_ratio,
                "submitted_size": request.aspect_ratio,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _generate_with_aiapis_gpt_image2(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_aiapis_api_key()
        data = await self._post_json_with_bearer_base_url(
            base_url=self.settings.aiapis_base_url,
            path="/images/generations",
            api_key=api_key,
            payload={
                "model": model.upstream_model_id,
                "prompt": request.prompt,
                "n": 1,
                "size": self._map_image_size_for_aiapis(request.size),
                "response_format": "b64_json",
                "quality": "hd",
                "style": "natural",
            },
            timeout=self.settings.aiapis_timeout_seconds,
        )
        data = self._normalize_openai_style_image_urls(data, base_url=self.settings.aiapis_base_url)
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=self._extract_revised_prompt(data),
            message="Image generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="text_to_image",
            title=self._history_title_for_kind("text_to_image"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=request.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "aiapis",
                "aspect_ratio": request.aspect_ratio,
                "submitted_size": self._map_image_size_for_aiapis(request.size),
                **raw_asset_metadata,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _fuse_with_wuyin_gpt_image2(
        self,
        *,
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        reference_urls = self._resolve_wuyin_reference_urls(metadata.source_images)
        submit_data, final_data, job_id, image_urls = await self._submit_and_poll_wuyin_image_job(
            prompt=self._build_fusion_prompt(metadata),
            size="1:1",
            urls=reference_urls,
        )
        first_image_url = image_urls[0] if image_urls else None
        if not first_image_url:
            wuyin_status = self._extract_wuyin_status(final_data)
            return GenerationResult(
                job_id=job_id,
                status="failed" if wuyin_status == "failed" else "queued",
                provider=model.provider,
                model=model.id,
                image_url=None,
                message="Fusion task failed." if wuyin_status == "failed" else "Fusion task is still processing.",
                raw_response=final_data if final_data else submit_data,
            )

        stored_asset = await self._store_generated_asset(
            image_url=first_image_url,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=job_id,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Fusion generation completed.",
            raw_response=final_data,
        )
        self._persist_history(
            kind="fusion",
            title=self._history_title_for_kind("fusion"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "wuyin",
                "reference_urls": reference_urls,
                **self._build_fusion_history_metadata(metadata),
                "negative_prompt": metadata.negative_prompt,
                "strength": metadata.strength,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _fuse_with_aiapis_gpt_image2(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_aiapis_api_key()
        fusion_image = await self._build_aiapis_fusion_upload(files)
        data = await self._post_multipart_with_bearer_base_url(
            base_url=self.settings.aiapis_base_url,
            path="/images/edits",
            api_key=api_key,
            data={
                "model": model.upstream_model_id,
                "prompt": self._build_fusion_prompt(metadata),
                "size": "1024x1024",
                "response_format": "b64_json",
                "quality": "high",
                "n": "1",
            },
            files=[fusion_image],
            timeout=self.settings.aiapis_timeout_seconds,
        )
        data = self._normalize_openai_style_image_urls(data, base_url=self.settings.aiapis_base_url)
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Fusion generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="fusion",
            title=self._history_title_for_kind("fusion"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "aiapis",
                **self._build_fusion_history_metadata(metadata),
                "negative_prompt": metadata.negative_prompt,
                "strength": metadata.strength,
                "fusion_input_layout": "grid_square_png",
                **raw_asset_metadata,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _fuse_with_apiyi_gpt_image2_all(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        prompt = self._build_fusion_prompt(metadata)
        data = await self._post_apiyi_gpt_image2_chat(model=model, prompt=prompt, files=files)
        upstream_image_url = self._extract_chat_completion_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=upstream_image_url,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Fusion generation completed." if stored_asset["access_url"] else "Fusion returned no asset.",
            raw_response=data,
        )
        if result.image_url:
            self._persist_history(
                kind="fusion",
                title=self._history_title_for_kind("fusion"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=metadata.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "upstream_platform": "apiyi",
                    "upstream_api": "chat_completions",
                    "upstream_model": model.upstream_model_id,
                    **self._build_fusion_history_metadata(metadata),
                    "negative_prompt": metadata.negative_prompt,
                    "strength": metadata.strength,
                    **stored_asset["metadata"],
                },
            )
        return result

    async def _fuse_with_dmxapi_gpt_image2(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_dmxapi_api_key()
        multipart_files = [await self._build_named_multipart_file(file, field_name="image") for file in files]
        data = await self._post_multipart_with_bearer_base_url(
            base_url=self.settings.dmxapi_base_url,
            path="/images/edits",
            api_key=api_key,
            data={
                "model": model.upstream_model_id,
                "prompt": self._build_fusion_prompt(metadata),
                "size": "1024x1024",
                "background": "auto",
                "output_format": "png",
                "quality": "high",
                "n": "1",
            },
            files=multipart_files,
            timeout=self.settings.dmxapi_timeout_seconds,
        )
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Fusion generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="fusion",
            title=self._history_title_for_kind("fusion"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "dmxapi",
                **self._build_fusion_history_metadata(metadata),
                "negative_prompt": metadata.negative_prompt,
                "strength": metadata.strength,
                **raw_asset_metadata,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _transform_with_wuyin_gpt_image2(
        self,
        *,
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        reference_urls = self._resolve_wuyin_reference_urls(metadata.source_images)
        submit_data, final_data, job_id, image_urls = await self._submit_and_poll_wuyin_image_job(
            prompt=metadata.prompt,
            size="1:1",
            urls=reference_urls,
        )
        first_image_url = image_urls[0] if image_urls else None
        if not first_image_url:
            wuyin_status = self._extract_wuyin_status(final_data)
            return GenerationResult(
                job_id=job_id,
                status="failed" if wuyin_status == "failed" else "queued",
                provider=model.provider,
                model=model.id,
                image_url=None,
                message="Reference image transform failed." if wuyin_status == "failed" else "Reference image transform is still processing.",
                raw_response=final_data if final_data else submit_data,
            ).model_copy(update=self._build_reference_result_update(metadata))

        stored_asset = await self._store_generated_asset(
            image_url=first_image_url,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=job_id,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Reference image transform completed.",
            raw_response=final_data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        self._persist_history(
            kind=self._map_feature_to_history_kind(metadata.feature),
            title=self._feature_title(metadata.feature, model.label),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "wuyin",
                "reference_urls": reference_urls,
                **stored_asset["metadata"],
                **self._build_reference_history_metadata(metadata),
            },
        )
        return result

    async def _transform_with_aiapis_gpt_image2(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_aiapis_api_key()
        target_size = self._map_image_size_for_aiapis_from_image_size(metadata.image_size)
        prepared_file = await self._build_aiapis_single_image_upload(files[0], target_size=target_size)
        data = await self._post_multipart_with_bearer_base_url(
            base_url=self.settings.aiapis_base_url,
            path="/images/edits",
            api_key=api_key,
            data={
                "model": model.upstream_model_id,
                "prompt": metadata.prompt,
                "size": target_size,
                "response_format": "b64_json",
                "n": "1",
            },
            files=[prepared_file],
            timeout=self.settings.aiapis_timeout_seconds,
        )
        data = self._normalize_openai_style_image_urls(data, base_url=self.settings.aiapis_base_url)
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Reference image transform completed.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        self._persist_history(
            kind=self._map_feature_to_history_kind(metadata.feature),
            title=self._feature_title(metadata.feature, model.label),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "aiapis",
                "submitted_size": target_size,
                **raw_asset_metadata,
                **stored_asset["metadata"],
                **self._build_reference_history_metadata(metadata),
            },
        )
        return result

    async def _transform_with_apiyi_gpt_image2_all(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        prompt = self._build_apiyi_reference_prompt(metadata)
        data = await self._post_apiyi_gpt_image2_chat(model=model, prompt=prompt, files=files)
        upstream_image_url = self._extract_chat_completion_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=upstream_image_url,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Reference image transform completed." if stored_asset["access_url"] else "Reference image transform returned no asset.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        if result.image_url:
            self._persist_history(
                kind=self._map_feature_to_history_kind(metadata.feature),
                title=self._feature_title(metadata.feature, model.label),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=metadata.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "upstream_platform": "apiyi",
                    "upstream_api": "chat_completions",
                    "upstream_model": model.upstream_model_id,
                    **stored_asset["metadata"],
                    **self._build_reference_history_metadata(metadata),
                },
            )
        return result

    async def _transform_with_dmxapi_gpt_image2(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_dmxapi_api_key()
        multipart_files = [await self._build_named_multipart_file(file, field_name="image") for file in files]
        data = await self._post_multipart_with_bearer_base_url(
            base_url=self.settings.dmxapi_base_url,
            path="/images/edits",
            api_key=api_key,
            data={
                "model": model.upstream_model_id,
                "prompt": metadata.prompt,
                "size": self._map_image_size_for_dmxapi(metadata.image_size),
                "background": "auto",
                "output_format": "png",
                "quality": "high",
                "n": "1",
            },
            files=multipart_files,
            timeout=self.settings.dmxapi_timeout_seconds,
        )
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Reference image transform completed.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        self._persist_history(
            kind=self._map_feature_to_history_kind(metadata.feature),
            title=self._feature_title(metadata.feature, model.label),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "dmxapi",
                **raw_asset_metadata,
                **stored_asset["metadata"],
                **self._build_reference_history_metadata(metadata),
            },
        )
        return result

    async def _generate_with_gemini_apiyi(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        data = await self._post_json_with_bearer(
            base_url=self.settings.apiyi_gemini_base_url,
            path=f"/models/{model.id}:generateContent",
            api_key=api_key,
            payload={
                "contents": [{"parts": [{"text": request.prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": request.aspect_ratio,
                        "imageSize": request.image_size,
                    },
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        image_bytes, content_type = self._extract_inline_image(data)
        stored_asset = await self._store_generated_binary_asset(
            image_bytes=image_bytes,
            content_type=content_type,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=None,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=self._extract_revised_prompt(data),
            message="Image generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="text_to_image",
            title=self._history_title_for_kind("text_to_image"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=request.prompt,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "apiyi",
                "aspect_ratio": request.aspect_ratio,
                "image_size": request.image_size,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _generate_with_flux_apiyi(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        data = await self._post_json_with_bearer(
            base_url=self.settings.apiyi_openai_base_url,
            path="/images/generations",
            api_key=api_key,
            payload={
                "model": self._map_apiyi_model_id(model.id),
                "prompt": request.prompt,
                "n": 1,
                "size": request.size,
                "extra_body": {
                    "aspect_ratio": request.aspect_ratio,
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        upstream_image_url = self._extract_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=upstream_image_url,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Image generation completed." if stored_asset["access_url"] else "Image generation returned no asset.",
            raw_response=data,
        )
        if result.image_url:
            self._persist_history(
                kind="text_to_image",
                title=self._history_title_for_kind("text_to_image"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=request.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "upstream_platform": "apiyi",
                    "aspect_ratio": request.aspect_ratio,
                    "size": request.size,
                    **stored_asset["metadata"],
                },
            )
        return result

    async def _generate_with_gemini(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        payload = {
            "model": model.id,
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "image_size": request.image_size,
            "thinking_level": request.thinking_level,
        }
        data = await self._post_json(
            base_url=self.settings.ttapi_openai_base_url,
            path="/gemini/image/generate",
            api_key=api_key,
            payload=payload,
        )
        image_url = self._extract_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=None,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=self._extract_revised_prompt(data),
            message="Image generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="text_to_image",
            title=self._history_title_for_kind("text_to_image"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=request.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "aspect_ratio": request.aspect_ratio,
                "image_size": request.image_size,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _generate_with_flux(
        self,
        *,
        request: TextToImageRequest,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        payload = {
            "prompt": request.prompt,
            "mode": model.id,
            "size": request.size,
            "aspect_ratio": request.aspect_ratio,
        }
        submit_data = await self._post_json(
            base_url=self.settings.ttapi_flux_base_url,
            path="/flux/generate",
            api_key=api_key,
            payload=payload,
        )
        job_id = self._extract_job_id(submit_data)
        if not job_id:
            return GenerationResult(
                job_id=None,
                status="failed",
                provider=model.provider,
                model=model.id,
                image_url=None,
                message="Flux generation did not return a task id.",
                raw_response=submit_data,
            )

        final_data = await self._poll_flux_result(job_id=job_id, api_key=api_key)
        image_url = self._extract_image_url(final_data)
        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind="text_to_image",
            model=model.id,
        )
        result = GenerationResult(
            job_id=job_id,
            status="completed" if image_url else "queued",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Image generation completed." if image_url else "Flux task submitted and still processing.",
            raw_response=final_data,
        )
        if result.image_url:
            self._persist_history(
                kind="text_to_image",
                title=self._history_title_for_kind("text_to_image"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=request.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "aspect_ratio": request.aspect_ratio,
                    "size": request.size,
                    **stored_asset["metadata"],
                },
            )
        return result

    async def _fuse_with_gemini(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        refer_images = [await self._file_to_data_url(file) for file in files]
        payload = {
            "model": model.id,
            "prompt": self._build_fusion_prompt(metadata),
            "aspect_ratio": "1:1",
            "image_size": "1K",
            "thinking_level": "Minimal",
            "refer_images": refer_images,
        }
        data = await self._post_json(
            base_url=self.settings.ttapi_openai_base_url,
            path="/gemini/image/generate",
            api_key=api_key,
            payload=payload,
        )
        image_url = self._extract_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=None,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=image_url,
            revised_prompt=self._extract_revised_prompt(data),
            message="Fusion generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="fusion",
            title=self._history_title_for_kind("fusion"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                **self._build_fusion_history_metadata(metadata),
                **stored_asset["metadata"],
            },
        )
        return result

    async def _fuse_with_gemini_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        parts: list[dict[str, Any]] = [{"text": self._build_fusion_prompt(metadata)}]
        for file in files:
            image_bytes = await file.read()
            await file.seek(0)
            parts.append(
                {
                    "inlineData": {
                        "mimeType": file.content_type or "image/png",
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    }
                }
            )
        data = await self._post_json_with_bearer(
            base_url=self.settings.apiyi_gemini_base_url,
            path=f"/models/{model.id}:generateContent",
            api_key=api_key,
            payload={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": "1:1",
                        "imageSize": "1K",
                    },
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        image_bytes, content_type = self._extract_inline_image(data)
        stored_asset = await self._store_generated_binary_asset(
            image_bytes=image_bytes,
            content_type=content_type,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=None,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=self._extract_revised_prompt(data),
            message="Fusion generation completed.",
            raw_response=data,
        )
        self._persist_history(
            kind="fusion",
            title=self._history_title_for_kind("fusion"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "apiyi",
                **self._build_fusion_history_metadata(metadata),
                "negative_prompt": metadata.negative_prompt,
                "strength": metadata.strength,
                **stored_asset["metadata"],
            },
        )
        return result

    async def _transform_with_gemini(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        refer_images = [await self._file_to_data_url(file) for file in files]
        payload = {
            "model": model.id,
            "prompt": metadata.prompt,
            "aspect_ratio": "1:1",
            "image_size": metadata.image_size,
            "thinking_level": "Minimal",
            "refer_images": refer_images,
        }
        data = await self._post_json(
            base_url=self.settings.ttapi_openai_base_url,
            path="/gemini/image/generate",
            api_key=api_key,
            payload=payload,
        )
        image_url = self._extract_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=None,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=self._extract_revised_prompt(data),
            message="Reference image transform completed.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        self._persist_history(
            kind=self._map_feature_to_history_kind(metadata.feature),
            title=self._feature_title(metadata.feature, model.label),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            job_id=result.job_id,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                **stored_asset["metadata"],
                **self._build_reference_history_metadata(metadata),
            },
        )
        return result

    async def _transform_with_gemini_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        parts: list[dict[str, Any]] = [{"text": metadata.prompt}]
        for file in files:
            image_bytes = await file.read()
            await file.seek(0)
            parts.append(
                {
                    "inlineData": {
                        "mimeType": file.content_type or "image/png",
                        "data": base64.b64encode(image_bytes).decode("utf-8"),
                    }
                }
            )
        data = await self._post_json_with_bearer(
            base_url=self.settings.apiyi_gemini_base_url,
            path=f"/models/{model.id}:generateContent",
            api_key=api_key,
            payload={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": "1:1",
                        "imageSize": metadata.image_size,
                    },
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        result_image_bytes, content_type = self._extract_inline_image(data)
        stored_asset = await self._store_generated_binary_asset(
            image_bytes=result_image_bytes,
            content_type=content_type,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=None,
            status="completed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=self._extract_revised_prompt(data),
            message="Reference image transform completed.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        self._persist_history(
            kind=self._map_feature_to_history_kind(metadata.feature),
            title=self._feature_title(metadata.feature, model.label),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=metadata.prompt,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata={
                "upstream_platform": "apiyi",
                **stored_asset["metadata"],
                **self._build_reference_history_metadata(metadata),
            },
        )
        return result

    async def _fuse_with_flux(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        multipart_files = [await self._build_multipart_file(file) for file in files]
        form_data = {
            "prompt": self._build_fusion_prompt(metadata),
            "mode": model.id,
            "aspect_ratio": "1:1",
        }
        submit_data = await self._post_multipart(
            base_url=self.settings.ttapi_flux_base_url,
            path="/flux/edits",
            api_key=api_key,
            data=form_data,
            files=multipart_files,
        )
        job_id = self._extract_job_id(submit_data)
        if not job_id:
            return GenerationResult(
                job_id=None,
                status="failed",
                provider=model.provider,
                model=model.id,
                message="Flux edit did not return a task id.",
                raw_response=submit_data,
            )
        final_data = await self._poll_flux_result(job_id=job_id, api_key=api_key)
        image_url = self._extract_image_url(final_data)
        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=job_id,
            status="completed" if image_url else "queued",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Fusion completed." if image_url else "Fusion task submitted and still processing.",
            raw_response=final_data,
        )
        if result.image_url:
            self._persist_history(
                kind="fusion",
                title=self._history_title_for_kind("fusion"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=metadata.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    **self._build_fusion_history_metadata(metadata),
                    **stored_asset["metadata"],
                },
            )
        return result

    async def _fuse_with_flux_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        multipart_files = [await self._build_multipart_file(file) for file in files]
        data = await self._post_multipart_with_bearer(
            base_url=self.settings.apiyi_openai_base_url,
            path="/images/edits",
            api_key=api_key,
            data={
                "model": self._map_apiyi_model_id(model.id),
                "prompt": self._build_fusion_prompt(metadata),
            },
            files=multipart_files,
        )
        upstream_image_url = self._extract_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=upstream_image_url,
            kind="fusion",
            model=model.id,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Fusion completed." if stored_asset["access_url"] else "Fusion returned no asset.",
            raw_response=data,
        )
        if result.image_url:
            self._persist_history(
                kind="fusion",
                title=self._history_title_for_kind("fusion"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=metadata.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "upstream_platform": "apiyi",
                    **self._build_fusion_history_metadata(metadata),
                    "negative_prompt": metadata.negative_prompt,
                    "strength": metadata.strength,
                    **stored_asset["metadata"],
                },
            )
        return result

    async def _transform_with_flux(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        multipart_files = [await self._build_multipart_file(file) for file in files]
        form_data = {
            "prompt": metadata.prompt,
            "mode": model.id,
            "aspect_ratio": "1:1",
        }
        submit_data = await self._post_multipart(
            base_url=self.settings.ttapi_flux_base_url,
            path="/flux/edits",
            api_key=api_key,
            data=form_data,
            files=multipart_files,
        )
        job_id = self._extract_job_id(submit_data)
        if not job_id:
            return GenerationResult(
                job_id=None,
                status="failed",
                provider=model.provider,
                model=model.id,
                message="Flux transform did not return a task id.",
                raw_response=submit_data,
            )

        final_data = await self._poll_flux_result(job_id=job_id, api_key=api_key)
        image_url = self._extract_image_url(final_data)
        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=job_id,
            status="completed" if image_url else "queued",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Reference image transform completed." if image_url else "Reference image transform still processing.",
            raw_response=final_data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        if result.image_url:
            self._persist_history(
                kind=self._map_feature_to_history_kind(metadata.feature),
                title=self._feature_title(metadata.feature, model.label),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=metadata.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    **stored_asset["metadata"],
                    **self._build_reference_history_metadata(metadata),
                },
        )
        return result

    async def _transform_with_flux_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: TTAPIModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        multipart_files = [await self._build_multipart_file(file) for file in files]
        data = await self._post_multipart_with_bearer(
            base_url=self.settings.apiyi_openai_base_url,
            path="/images/edits",
            api_key=api_key,
            data={
                "model": self._map_apiyi_model_id(model.id),
                "prompt": metadata.prompt,
                "size": "2048x2048" if metadata.image_size == "2K" else "1024x1024",
            },
            files=multipart_files,
        )
        upstream_image_url = self._extract_image_url(data)
        stored_asset = await self._store_generated_asset(
            image_url=upstream_image_url,
            kind=metadata.feature,
            model=model.id,
            preferred_name=metadata.filename,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            message="Reference image transform completed." if stored_asset["access_url"] else "Reference image transform returned no asset.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        if result.image_url:
            self._persist_history(
                kind=self._map_feature_to_history_kind(metadata.feature),
                title=self._feature_title(metadata.feature, model.label),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=metadata.prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata={
                    "upstream_platform": "apiyi",
                    **stored_asset["metadata"],
                    **self._build_reference_history_metadata(metadata),
                },
            )
        return result

    async def _post_json(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.ttapi_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={
                    "TT-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return self._handle_response(response)

    async def _post_json_with_bearer(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout or self.settings.ttapi_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return self._handle_response(response)

    async def _post_apiyi_gpt_image2_chat(
        self,
        *,
        model: TTAPIModelConfig,
        prompt: str,
        files: list[UploadFile],
    ) -> dict[str, Any]:
        content: str | list[dict[str, Any]]
        if files:
            content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for index, file in enumerate(files):
                filename = file.filename or f"reference-{index + 1}.png"
                if len(files) > 1:
                    role_text = "待生成多视图的原图" if index == len(files) - 1 else f"风格参考图 {index + 1}"
                    content_parts.append({"type": "text", "text": f"{role_text}：{filename}"})
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": await self._file_to_data_url(file)},
                    }
                )
            content = content_parts
        else:
            content = prompt

        return await self._post_json_with_bearer(
            base_url=self.settings.apiyi_openai_base_url,
            path="/chat/completions",
            api_key=self._require_apiyi_api_key(),
            payload={
                "model": model.upstream_model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )

    def _build_apiyi_reference_prompt(self, metadata: ReferenceImageRequestMetadata) -> str:
        prompt = metadata.prompt.strip()
        if metadata.feature != "multi_view" or metadata.image_count <= 1:
            return prompt
        return (
            "这是一个带上下文参考图的多视图生成任务。"
            "输入图片按顺序理解：前面的图片都是风格、材质、光线、背景和工艺细节参考；最后一张图片是必须生成四视图的原图主体。"
            "请只基于最后一张原图的主体结构生成正面、左侧、右侧、背面四个视角，保持主体身份和几何一致；"
            "同时吸收前面参考图的摄影风格、珠宝质感、金属/宝石细节、画面洁净度和光照方式。"
            f"\n\n具体生成要求：{prompt}"
        )

    async def _post_json_with_bearer_base_url(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        if self._should_use_curl_transport(base_url):
            return await self._curl_json_request(
                method="POST",
                url=f"{base_url}{path}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout=timeout,
            )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return self._handle_response(response)

    async def _post_json_with_authorization(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.wuyin_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={
                    "Authorization": api_key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return self._handle_response(response)

    async def _get_json_with_authorization(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.wuyin_timeout_seconds) as client:
            response = await client.get(
                f"{base_url}{path}",
                headers={
                    "Authorization": api_key,
                    "Accept": "application/json",
                },
                params=query,
            )
        return self._handle_response(response)

    async def _post_multipart(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.ttapi_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={"TT-API-KEY": api_key},
                data=data,
                files=files,
            )
        return self._handle_response(response)

    async def _post_multipart_with_bearer(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.ttapi_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
            )
        return self._handle_response(response)

    async def _post_multipart_with_bearer_base_url(
        self,
        *,
        base_url: str,
        path: str,
        api_key: str,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
        timeout: float,
    ) -> dict[str, Any]:
        if self._should_use_curl_transport(base_url):
            return await self._curl_multipart_request(
                url=f"{base_url}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
                timeout=timeout,
            )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
            )
        return self._handle_response(response)

    async def _poll_flux_result(self, *, job_id: str, api_key: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.settings.ttapi_timeout_seconds) as client:
            for _ in range(self.settings.ttapi_poll_attempts):
                response = await client.request(
                    "GET",
                    f"{self.settings.ttapi_flux_base_url}/flux/fetch",
                    headers={"TT-API-KEY": api_key},
                    params={"jobId": job_id},
                )
                data = self._handle_response(response)
                status_value = self._extract_status(data)
                if status_value in {"completed", "succeeded", "success"}:
                    return data
                if status_value in {"failed", "error"}:
                    return data
                await asyncio.sleep(self.settings.ttapi_poll_interval_seconds)
        return {"id": job_id, "status": "queued"}

    async def _poll_wuyin_result(self, *, job_id: str, api_key: str) -> dict[str, Any]:
        last_data: dict[str, Any] = {"id": job_id, "data": {"status": 0}}
        for _ in range(self.settings.wuyin_poll_attempts):
            data = await self._get_json_with_authorization(
                base_url=self.settings.wuyin_base_url,
                path="/api/async/detail",
                api_key=api_key,
                query={"id": job_id},
            )
            last_data = data
            if self._extract_wuyin_image_urls(data):
                return data
            status_value = self._extract_wuyin_status(data)
            if status_value in {"failed", "succeeded"}:
                return data
            await asyncio.sleep(self.settings.wuyin_poll_interval_seconds)
        return last_data

    async def _submit_and_poll_wuyin_image_job(
        self,
        *,
        prompt: str,
        size: str,
        urls: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], str | None, list[str]]:
        api_key = self._require_wuyin_api_key()
        payload: dict[str, Any] = {
            "prompt": prompt,
            "size": size,
            "count": "1",
        }
        if urls:
            payload["urls"] = urls
        submit_data = await self._post_json_with_authorization(
            base_url=self.settings.wuyin_base_url,
            path="/api/async/image_gpt",
            api_key=api_key,
            payload=payload,
        )
        job_id = self._extract_job_id(submit_data)
        if not job_id:
            return submit_data, submit_data, None, []
        final_data = await self._poll_wuyin_result(job_id=job_id, api_key=api_key)
        return submit_data, final_data, job_id, self._extract_wuyin_image_urls(final_data)

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid upstream response: {response.text}",
            ) from exc

        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"upstream_status": response.status_code, "upstream_response": data},
            )
        return data

    async def _file_to_data_url(self, file: UploadFile) -> str:
        content = await file.read()
        await file.seek(0)
        mime_type = file.content_type or "image/png"
        encoded = base64.b64encode(content).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    async def _build_multipart_file(self, file: UploadFile) -> tuple[str, tuple[str, bytes, str]]:
        content = await file.read()
        await file.seek(0)
        return (
            "image",
            (
                file.filename or f"{uuid4()}.png",
                content,
                file.content_type or "image/png",
            ),
        )

    async def _build_named_multipart_file(self, file: UploadFile, *, field_name: str) -> tuple[str, tuple[str, bytes, str]]:
        content = await file.read()
        await file.seek(0)
        return (
            field_name,
            (
                file.filename or f"{uuid4()}.png",
                content,
                file.content_type or "image/png",
            ),
        )

    async def _build_aiapis_single_image_upload(self, file: UploadFile, *, target_size: str) -> tuple[str, tuple[str, bytes, str]]:
        content = await file.read()
        await file.seek(0)
        normalized = await asyncio.to_thread(
            self._normalize_image_for_aiapis,
            content,
            target_size,
        )
        filename = f"{Path(file.filename or 'reference').stem or 'reference'}.png"
        return ("image", (filename, normalized, "image/png"))

    async def _build_aiapis_fusion_upload(self, files: list[UploadFile]) -> tuple[str, tuple[str, bytes, str]]:
        images: list[bytes] = []
        for file in files:
            content = await file.read()
            await file.seek(0)
            images.append(content)
        composed = await asyncio.to_thread(self._compose_aiapis_fusion_canvas, images)
        return ("image", ("fusion-reference.png", composed, "image/png"))

    def _build_fusion_prompt(self, metadata: FusionRequestMetadata) -> str:
        mode_hint = {
            "balanced": "Blend the uploaded references evenly.",
            "style_first": "Prioritize the shared style, material finish, and mood from the references.",
            "structure_first": "Preserve the main structure and silhouette of the primary image.",
            "detail_enhanced": "Focus on settings, texture, and small decorative detail.",
        }[metadata.mode.value]
        negative_hint = f" Negative prompt: {metadata.negative_prompt}." if metadata.negative_prompt else ""
        return (
            f"{metadata.prompt} {mode_hint} "
            f"Primary image index: {metadata.primary_image_index}. "
            f"Fusion strength: {metadata.strength:.2f}.{negative_hint}"
        )

    def _extract_job_id(self, data: dict[str, Any]) -> str | None:
        for key in ("id", "task_id", "request_id", "jobId", "job_id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("id", "task_id", "request_id", "jobId", "job_id"):
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    def _extract_status(self, data: dict[str, Any]) -> str:
        if isinstance(data.get("status"), str):
            return data["status"].lower()
        task = data.get("data")
        if isinstance(task, dict) and isinstance(task.get("status"), str):
            return task["status"].lower()
        if isinstance(task, dict) and isinstance(task.get("state"), str):
            return task["state"].lower()
        return ""

    def _extract_wuyin_status(self, data: dict[str, Any]) -> str:
        task = data.get("data")
        raw_status = task.get("status") if isinstance(task, dict) else data.get("status")
        mapping = {
            0: "processing",
            1: "failed",
            2: "succeeded",
            "0": "processing",
            "1": "failed",
            "2": "succeeded",
        }
        if raw_status in mapping:
            return mapping[raw_status]
        if isinstance(raw_status, str):
            return raw_status.lower()
        return ""

    def _extract_revised_prompt(self, data: dict[str, Any]) -> str | None:
        data_list = data.get("data")
        if isinstance(data_list, list) and data_list:
            first_item = data_list[0]
            if isinstance(first_item, dict):
                prompt = first_item.get("revised_prompt")
                if isinstance(prompt, str):
                    return prompt
        return None

    def _extract_image_url(self, data: dict[str, Any]) -> str | None:
        if isinstance(data.get("image_url"), str):
            return data["image_url"]

        if isinstance(data.get("url"), str):
            return data["url"]

        data_field = data.get("data")
        if isinstance(data_field, list):
            for item in data_field:
                if isinstance(item, dict):
                    b64_json = item.get("b64_json")
                    if isinstance(b64_json, str) and b64_json:
                        mime_type = item.get("mime_type") if isinstance(item.get("mime_type"), str) else "image/png"
                        return f"data:{mime_type};base64,{b64_json}"
                    for key in ("url", "image_url"):
                        value = item.get(key)
                        if isinstance(value, str):
                            return value
        if isinstance(data_field, dict):
            b64_json = data_field.get("b64_json")
            if isinstance(b64_json, str) and b64_json:
                mime_type = data_field.get("mime_type") if isinstance(data_field.get("mime_type"), str) else "image/png"
                return f"data:{mime_type};base64,{b64_json}"
            for key in ("url", "image_url", "result_url", "imageUrl"):
                value = data_field.get(key)
                if isinstance(value, str):
                    return value

        result = data.get("result")
        if isinstance(result, list):
            for item in result:
                if isinstance(item, str):
                    return item
                if isinstance(item, dict):
                    value = item.get("url")
                    if isinstance(value, str):
                        return value

        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str):
                    return item
                if isinstance(item, dict):
                    for key in ("url", "image_url"):
                        value = item.get(key)
                        if isinstance(value, str):
                            return value
        images = data.get("images")
        if isinstance(images, list):
            for item in images:
                if isinstance(item, dict):
                    value = item.get("url")
                    if isinstance(value, str):
                        return value
        return None

    def _extract_chat_completion_image_url(self, data: dict[str, Any]) -> str | None:
        content = self._extract_chat_completion_content(data)
        if not content:
            return self._extract_image_url(data)

        markdown_match = re.search(r"!\[[^\]]*]\((?P<url>[^)]+)\)", content)
        if markdown_match:
            return markdown_match.group("url").strip()

        url_match = re.search(r"(https?://[^\s)'\"<>]+|data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)", content)
        if url_match:
            return url_match.group(1).strip()

        return self._extract_image_url(data)

    def _extract_chat_completion_content(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if not isinstance(choices, list):
            return None
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                fragments: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        fragments.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            fragments.append(text)
                if fragments:
                    return "\n".join(fragments).strip()
        return None

    def _extract_wuyin_image_urls(self, data: dict[str, Any]) -> list[str]:
        task = data.get("data")
        result = task.get("result") if isinstance(task, dict) else data.get("result")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, str) and item]
        if isinstance(result, str) and result:
            return [result]
        return []

    def _extract_inline_image(self, data: dict[str, Any]) -> tuple[bytes, str]:
        candidates = data.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                parts = content.get("parts")
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    inline_data = part.get("inlineData")
                    if not isinstance(inline_data, dict):
                        continue
                    encoded = inline_data.get("data")
                    if not isinstance(encoded, str) or not encoded:
                        continue
                    mime_type = inline_data.get("mimeType")
                    if not isinstance(mime_type, str) or not mime_type:
                        mime_type = "image/png"
                    return base64.b64decode(encoded), mime_type
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "No inline image found in upstream response.", "upstream_response": data},
        )

    def _extract_openai_image_payload(self, data: dict[str, Any]) -> tuple[bytes | None, str | None, str | None]:
        payload = data.get("data")
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                b64_json = item.get("b64_json")
                if isinstance(b64_json, str) and b64_json:
                    return base64.b64decode(b64_json), "image/png", None
                url = item.get("url")
                if isinstance(url, str) and url:
                    return None, None, url
        return None, None, None

    def _normalize_openai_style_image_urls(self, data: dict[str, Any], *, base_url: str) -> dict[str, Any]:
        payload = data.get("data")
        if not isinstance(payload, list):
            return data

        normalized_items: list[Any] = []
        changed = False
        for item in payload:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            url = normalized_item.get("url")
            if isinstance(url, str) and url and not url.startswith(("http://", "https://", "data:")):
                normalized_item["url"] = urljoin(f"{base_url.rstrip('/')}/", url)
                changed = True
            normalized_items.append(normalized_item)

        if not changed:
            return data

        normalized_data = dict(data)
        normalized_data["data"] = normalized_items
        return normalized_data

    def _map_apiyi_model_id(self, model_id: str) -> str:
        mapping = {
            "flux1-pro": "black-forest-labs/flux-pro-v1.1",
            "flux-2-pro": "black-forest-labs/flux-pro-v1.1",
            "flux-2-max": "black-forest-labs/flux-kontext-max",
            "flux-kontext-pro": "flux-kontext-pro",
            "flux-kontext-max": "black-forest-labs/flux-kontext-max",
        }
        return mapping.get(model_id, model_id)

    def _persist_history(
        self,
        *,
        kind: str,
        title: str,
        model_id: str,
        provider: str,
        status: str,
        prompt: str,
        job_id: str | None = None,
        user_id: str | None = None,
        image_url: str | None,
        storage_url: str | None,
        metadata: dict[str, object] | None,
    ) -> None:
        request_user = self._require_request_user()
        self.history_service.create_record(
            HistoryRecordCreate(
                kind=kind,  # type: ignore[arg-type]
                title=title,
                model=model_id,
                provider=provider,
                status=status,
                prompt=prompt,
                user_id=user_id or request_user.id,
                job_id=job_id,
                image_url=image_url,
                storage_url=storage_url,
                metadata=metadata,
            ),
            current_user=request_user,
        )

    def _map_feature_to_history_kind(self, feature: str) -> str:
        feature_to_kind = {
            "image_edit": "image_edit",
            "sketch_to_realistic": "sketch_to_realistic",
            "product_refine": "product_refine",
            "gemstone_design": "gemstone_design",
            "upscale": "upscale",
            "grayscale_relief": "grayscale_relief",
            "multi_view": "multi_view",
            "multi_view_refine": "multi_view",
        }
        return feature_to_kind.get(feature, "image_edit")

    def _feature_title(self, feature: str, model_label: str) -> str:
        return self._module_display_name(self._map_feature_to_history_kind(feature))

    def _history_title_for_kind(self, kind: str) -> str:
        return self._module_display_name(kind)

    def _module_display_name(self, kind: str) -> str:
        label_map = {
            "text_to_image": "文生图",
            "fusion": "多图融合",
            "image_edit": "图像编辑",
            "sketch_to_realistic": "线稿转写实图",
            "product_refine": "产品精修",
            "gemstone_design": "裸石设计",
            "upscale": "高清放大",
            "grayscale_relief": "转灰度图",
            "multi_view": "生成多视图",
            "multi_view_split": "多视图切图",
        }
        return label_map.get(kind, "生成结果")

    def _generated_asset_name(
        self,
        *,
        kind: str,
        content_type: str,
        preferred_name: str | None = None,
        name_suffix: str | None = None,
    ) -> str:
        extension = self._extension_from_content_type(content_type)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if preferred_name:
            source_name = Path(preferred_name).name.strip()
            source_stem = Path(source_name).stem.strip()
            if source_stem:
                sanitized_stem = source_stem.replace("/", "_").replace("\\", "_")
                if name_suffix:
                    sanitized_suffix = name_suffix.replace("/", "_").replace("\\", "_")
                    return f"{sanitized_stem}_{sanitized_suffix}_{timestamp}{extension}"
                return f"{sanitized_stem}_{timestamp}{extension}"

        return f"{self._module_display_name(kind)}_{timestamp}{extension}"

    def _stored_filename_from_object_key(self, object_key: str, fallback_name: str) -> str:
        filename = Path(object_key).name.strip()
        return filename or fallback_name

    def _extension_from_content_type(self, content_type: str) -> str:
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        return mapping.get(content_type.lower(), ".png")

    def _build_reference_history_metadata(self, metadata: ReferenceImageRequestMetadata) -> dict[str, object]:
        payload: dict[str, object] = {
            "feature": metadata.feature,
            "image_count": metadata.image_count,
            "source_filename": metadata.filename,
            "filenames": metadata.filenames or [metadata.filename],
            "strength": metadata.strength,
            "negative_prompt": metadata.negative_prompt,
        }
        if metadata.source_image_url:
            payload["source_image_url"] = metadata.source_image_url
        if metadata.source_image_storage_url:
            payload["source_image_storage_url"] = metadata.source_image_storage_url
        if metadata.source_images:
            payload["source_images"] = self._serialize_input_image_sources(metadata.source_images)
        return payload

    def _build_reference_result_update(self, metadata: ReferenceImageRequestMetadata) -> dict[str, str]:
        update: dict[str, str] = {}
        if metadata.source_image_url:
            update["source_image_url"] = metadata.source_image_url
        if metadata.source_image_storage_url:
            update["source_image_storage_url"] = metadata.source_image_storage_url
        return update

    def _build_fusion_history_metadata(self, metadata: FusionRequestMetadata) -> dict[str, object]:
        return {
            "mode": metadata.mode.value,
            "image_count": metadata.image_count,
            "primary_image_index": metadata.primary_image_index,
            "filenames": metadata.filenames,
            "source_images": self._serialize_input_image_sources(metadata.source_images),
        }

    def _serialize_input_image_sources(self, items: list[object]) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for item in items:
            if hasattr(item, "model_dump"):
                serialized.append(item.model_dump())
            elif isinstance(item, dict):
                serialized.append(dict(item))
        return serialized

    def _preferred_fusion_asset_name(self, metadata: FusionRequestMetadata) -> str | None:
        if not metadata.filenames:
            return None
        if 0 <= metadata.primary_image_index < len(metadata.filenames):
            return metadata.filenames[metadata.primary_image_index]
        return metadata.filenames[0]

    def _build_asset_content_url(self, storage_url: str, filename: str | None = None) -> str:
        query: dict[str, str] = {"storage_url": storage_url}
        if filename:
            query["filename"] = filename
        return f"/api/v1/assets/content?{urlencode(query)}"

    def _is_custom_storage_url(self, value: str | None) -> bool:
        return bool(value and value.startswith(("oss://", "local://")))

    def _resolve_source_preview_url(self, url: str, filename: str | None = None) -> str:
        if self._is_custom_storage_url(url):
            return self._build_asset_content_url(url, filename)
        return url

    def _build_source_image_reference(self, *, url: str, filename: str) -> dict[str, object]:
        storage_url = url if self._is_custom_storage_url(url) else None
        preview_url = self._resolve_source_preview_url(url, filename)
        return {
            "filename": filename,
            "source_image_url": preview_url,
            "storage_url": storage_url,
            "preview_url": preview_url,
        }

    def _map_image_size_for_dmxapi(self, image_size: str) -> str:
        normalized = image_size.strip().upper()
        if normalized == "2K":
            return "1536x1536"
        return "1024x1024"

    def _map_image_size_for_aiapis(self, size: str) -> str:
        allowed = {"256x256", "512x512", "1024x1024"}
        normalized = size.strip().lower()
        if normalized in allowed:
            return normalized
        return "1024x1024"

    def _map_image_size_for_aiapis_from_image_size(self, image_size: str) -> str:
        normalized = image_size.strip().upper()
        if normalized == "2K":
            return "1024x1024"
        return "1024x1024"

    def _normalize_image_for_aiapis(self, image_bytes: bytes, target_size: str) -> bytes:
        side = int(target_size.split("x", 1)[0])
        with Image.open(BytesIO(image_bytes)) as source:
            image = source.convert("RGBA")
            fitted = image.copy()
            fitted.thumbnail((side, side), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
            offset = ((side - fitted.width) // 2, (side - fitted.height) // 2)
            canvas.alpha_composite(fitted, offset)
            return self._serialize_png_under_limit(canvas)

    def _compose_aiapis_fusion_canvas(self, image_payloads: list[bytes]) -> bytes:
        side = 1024
        count = max(1, len(image_payloads))
        columns = 1 if count == 1 else 2 if count <= 4 else 3
        rows = ceil(count / columns)
        cell_size = side // max(columns, rows)
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
        for index, payload in enumerate(image_payloads):
            with Image.open(BytesIO(payload)) as source:
                tile = source.convert("RGBA")
                tile.thumbnail((cell_size, cell_size), Image.Resampling.LANCZOS)
                col = index % columns
                row = index // columns
                cell_left = col * cell_size + (cell_size - tile.width) // 2
                cell_top = row * cell_size + (cell_size - tile.height) // 2
                canvas.alpha_composite(tile, (cell_left, cell_top))
        return self._serialize_png_under_limit(canvas)

    def _serialize_png_under_limit(self, image: Image.Image, *, max_bytes: int = 4 * 1024 * 1024) -> bytes:
        working = image
        for _ in range(6):
            output = BytesIO()
            working.save(output, format="PNG", optimize=True)
            content = output.getvalue()
            if len(content) <= max_bytes:
                return content
            next_side = max(256, int(working.width * 0.85))
            working = working.resize((next_side, next_side), Image.Resampling.LANCZOS)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prepared PNG for GPT Image 2 exceeds the 4MB upstream limit.",
        )

    def _resolve_wuyin_reference_urls(self, items: list[object]) -> list[str]:
        resolved: list[str] = []
        for item in items:
            payload = item.model_dump() if hasattr(item, "model_dump") else item if isinstance(item, dict) else None
            if not isinstance(payload, dict):
                continue
            storage_url = payload.get("storage_url")
            preview_url = payload.get("preview_url") or payload.get("source_image_url")
            candidate = storage_url if isinstance(storage_url, str) and storage_url else preview_url
            if not isinstance(candidate, str) or not candidate:
                continue
            resolved.append(self._build_wuyin_accessible_url(candidate))
        return resolved

    def _build_wuyin_accessible_url(self, candidate: str) -> str:
        if candidate.startswith(("http://", "https://")):
            return candidate

        if candidate.startswith("/api/v1/assets/content"):
            parsed = urlparse(candidate)
            nested_storage_url = parse_qs(parsed.query).get("storage_url", [None])[0]
            if nested_storage_url:
                return self._build_wuyin_accessible_url(nested_storage_url)

        generated = self.storage_service.generate_access_url(candidate) if self._is_custom_storage_url(candidate) else candidate
        if isinstance(generated, str) and generated.startswith(("http://", "https://")):
            return generated

        public_base_url = (self.settings.public_base_url or "").strip().rstrip("/")
        if not public_base_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="APP_PUBLIC_BASE_URL is required for GPT Image 2 reference-image generation.",
            )
        if not generated.startswith("/"):
            generated = f"/{generated}"
        return f"{public_base_url}{generated}"

    async def _store_generated_asset(
        self,
        *,
        image_url: str | None,
        kind: str,
        model: str,
        preferred_name: str | None = None,
        name_suffix: str | None = None,
    ) -> dict[str, Any]:
        if not image_url:
            return {"storage_url": None, "access_url": None, "metadata": {}}

        self._notify_stage("uploading")
        request_user = self._require_request_user()
        if not self.storage_service.is_configured():
            image_bytes, content_type = await self._download_image(image_url)
            object_key = self._build_object_key(kind=kind, model=model, content_type=content_type)
            fallback_name = self._generated_asset_name(
                kind=kind,
                content_type=content_type,
                preferred_name=preferred_name,
                name_suffix=name_suffix,
            )
            asset_name = fallback_name
            storage_url = self._store_local_generated_asset(
                object_key=object_key,
                image_bytes=image_bytes,
            )
            access_url = self._build_asset_content_url(storage_url, asset_name)
            self.asset_service.create_stored_asset_record(
                current_user=request_user,
                name=asset_name,
                module_kind=kind,
                storage_url=storage_url,
                mime_type=content_type,
                file_size=len(image_bytes),
                metadata={
                    "storage_provider": "local_disk",
                    "object_key": object_key,
                    "source_image_url": image_url,
                },
            )
            return {
                "storage_url": storage_url,
                "access_url": access_url,
                "metadata": {
                    "storage_provider": "local_disk",
                    "object_key": object_key,
                    "source_image_url": image_url,
                },
            }

        image_bytes, content_type = await self._download_image(image_url)
        object_key = self._build_object_key(kind=kind, model=model, content_type=content_type)
        fallback_name = self._generated_asset_name(
            kind=kind,
            content_type=content_type,
            preferred_name=preferred_name,
            name_suffix=name_suffix,
        )
        asset_name = fallback_name
        stored = self.storage_service.upload_bytes(
            object_key=object_key,
            content=image_bytes,
            content_type=content_type,
        )
        access_url = self._build_asset_content_url(stored.storage_url, asset_name)
        self.asset_service.create_stored_asset_record(
            current_user=request_user,
            name=asset_name,
            module_kind=kind,
            storage_url=stored.storage_url,
            mime_type=content_type,
            file_size=len(image_bytes),
            metadata={
                "storage_provider": self.settings.oss_provider,
                "object_key": stored.object_key,
                "source_image_url": image_url,
            },
        )
        return {
            "storage_url": stored.storage_url,
            "access_url": access_url,
            "metadata": {
                "storage_provider": self.settings.oss_provider,
                "object_key": stored.object_key,
                "source_image_url": image_url,
            },
        }

    async def _store_generated_result_from_openai_payload(
        self,
        *,
        data: dict[str, Any],
        kind: str,
        model: str,
        preferred_name: str | None = None,
        name_suffix: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        image_bytes, content_type, image_url = self._extract_openai_image_payload(data)
        if image_bytes is not None and content_type is not None:
            stored_asset = await self._store_generated_binary_asset(
                image_bytes=image_bytes,
                content_type=content_type,
                kind=kind,
                model=model,
                preferred_name=preferred_name,
                name_suffix=name_suffix,
            )
            return stored_asset, {"upstream_response_format": "b64_json"}

        stored_asset = await self._store_generated_asset(
            image_url=image_url,
            kind=kind,
            model=model,
            preferred_name=preferred_name,
            name_suffix=name_suffix,
        )
        return stored_asset, {"upstream_response_format": "url", "upstream_image_url": image_url}

    async def _store_generated_binary_asset(
        self,
        *,
        image_bytes: bytes,
        content_type: str,
        kind: str,
        model: str,
        preferred_name: str | None = None,
        name_suffix: str | None = None,
    ) -> dict[str, Any]:
        self._notify_stage("uploading")
        request_user = self._require_request_user()
        fallback_name = self._generated_asset_name(
            kind=kind,
            content_type=content_type,
            preferred_name=preferred_name,
            name_suffix=name_suffix,
        )
        if not self.storage_service.is_configured():
            object_key = self._build_object_key(kind=kind, model=model, content_type=content_type)
            asset_name = fallback_name
            storage_url = self._store_local_generated_asset(
                object_key=object_key,
                image_bytes=image_bytes,
            )
            access_url = self._build_asset_content_url(storage_url, asset_name)
            self.asset_service.create_stored_asset_record(
                current_user=request_user,
                name=asset_name,
                module_kind=kind,
                storage_url=storage_url,
                mime_type=content_type,
                file_size=len(image_bytes),
                metadata={
                    "storage_provider": "local_disk",
                    "object_key": object_key,
                },
            )
            return {
                "storage_url": storage_url,
                "access_url": access_url,
                "metadata": {
                    "storage_provider": "local_disk",
                    "object_key": object_key,
                },
            }

        object_key = self._build_object_key(kind=kind, model=model, content_type=content_type)
        asset_name = fallback_name
        stored = self.storage_service.upload_bytes(
            object_key=object_key,
            content=image_bytes,
            content_type=content_type,
        )
        access_url = self._build_asset_content_url(stored.storage_url, asset_name)
        self.asset_service.create_stored_asset_record(
            current_user=request_user,
            name=asset_name,
            module_kind=kind,
            storage_url=stored.storage_url,
            mime_type=content_type,
            file_size=len(image_bytes),
            metadata={
                "storage_provider": self.settings.oss_provider,
                "object_key": stored.object_key,
            },
        )
        return {
            "storage_url": stored.storage_url,
            "access_url": access_url,
            "metadata": {
                "storage_provider": self.settings.oss_provider,
                "object_key": stored.object_key,
            },
        }

    async def _split_and_store_multi_view_assets(
        self,
        *,
        image_bytes: bytes,
        source_image_url: str,
        source_image_name: str | None,
        model: str,
        split_x_ratio: float,
        split_y_ratio: float,
        gap_x_ratio: float,
        gap_y_ratio: float,
    ) -> list[dict[str, Any]]:
        split_items = self._split_multi_view_binary(
            image_bytes=image_bytes,
            split_x_ratio=split_x_ratio,
            split_y_ratio=split_y_ratio,
            gap_x_ratio=gap_x_ratio,
            gap_y_ratio=gap_y_ratio,
        )

        stored_items: list[dict[str, Any]] = []
        for item in split_items:
            stored_asset = await self._store_generated_binary_asset(
                image_bytes=item["image_bytes"],
                content_type="image/png",
                kind=f"multi_view/{item['view']}",
                model=model,
                preferred_name=source_image_name,
                name_suffix=item["view"],
            )
            stored_items.append(
                {
                    "view": item["view"],
                    "image_url": stored_asset["access_url"],
                    "storage_url": stored_asset["storage_url"],
                    "width": item["width"],
                    "height": item["height"],
                    "source_image_url": source_image_url,
                }
            )

        return stored_items

    async def _download_image(self, image_url: str) -> tuple[bytes, str]:
        if image_url.startswith("data:image/"):
            header, _, encoded = image_url.partition(",")
            content_type = header.removeprefix("data:").split(";", 1)[0] or "image/png"
            try:
                return base64.b64decode(encoded), content_type
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Invalid base64 image data returned by upstream.",
                ) from exc

        if image_url.startswith(("local://", "oss://", "/api/v1/assets/")):
            self.asset_service.ensure_storage_url_access(storage_url=image_url, current_user=self._require_request_user())
            content, content_type, _ = self.asset_service.fetch_asset_bytes(image_url)
            return content, content_type

        if self._should_use_curl_transport(image_url):
            content, content_type, status_code = await self._curl_download(image_url, timeout=self.settings.ttapi_timeout_seconds)
            if status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to download upstream image asset, status={status_code}.",
                )
            return content, content_type

        async with httpx.AsyncClient(timeout=self.settings.ttapi_timeout_seconds) as client:
            response = await client.get(image_url)
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to download upstream image asset, status={response.status_code}.",
            )
        content_type = response.headers.get("Content-Type", "image/png").split(";")[0].strip() or "image/png"
        return response.content, content_type

    def _should_use_curl_transport(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.hostname == "img.aiapis.help"

    async def _curl_json_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._run_curl_json_request,
            method,
            url,
            headers,
            payload,
            timeout,
        )

    def _run_curl_json_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as body_file:
            body_file.write(json.dumps(payload, ensure_ascii=False))
            body_file.flush()
            command = [
                "curl",
                "-k",
                "-sS",
                "-X",
                method,
                "--max-time",
                str(max(1, int(timeout))),
                "-o",
                "-",
                "-w",
                "\n%{http_code}",
            ]
            for key, value in headers.items():
                command.extend(["-H", f"{key}: {value}"])
            command.extend(["--data-binary", f"@{body_file.name}", url])
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
        return self._handle_curl_response(completed)

    async def _curl_multipart_request(
        self,
        *,
        url: str,
        headers: dict[str, str],
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
        timeout: float,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._run_curl_multipart_request,
            url,
            headers,
            data,
            files,
            timeout,
        )

    def _run_curl_multipart_request(
        self,
        url: str,
        headers: dict[str, str],
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
        timeout: float,
    ) -> dict[str, Any]:
        temp_paths: list[str] = []
        try:
            command = [
                "curl",
                "-k",
                "-sS",
                "--max-time",
                str(max(1, int(timeout))),
                "-o",
                "-",
                "-w",
                "\n%{http_code}",
            ]
            for key, value in headers.items():
                command.extend(["-H", f"{key}: {value}"])
            for key, value in data.items():
                command.extend(["-F", f"{key}={value}"])
            for field_name, (filename, content, content_type) in files:
                suffix = Path(filename).suffix or ".bin"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    temp_paths.append(temp_file.name)
                command.extend(["-F", f"{field_name}=@{temp_paths[-1]};filename={filename};type={content_type}"])
            command.append(url)
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            return self._handle_curl_response(completed)
        finally:
            for path in temp_paths:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _handle_curl_response(self, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        if completed.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Curl upstream request failed: {completed.stderr.strip() or completed.stdout.strip()}",
            )
        body, _, status_text = completed.stdout.rpartition("\n")
        try:
            status_code = int(status_text.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid curl upstream status trailer: {completed.stdout[-200:]}",
            ) from exc
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid upstream response: {body[:1000]}",
            ) from exc
        if status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"upstream_status": status_code, "upstream_response": data},
            )
        return data

    async def _curl_download(self, url: str, *, timeout: float) -> tuple[bytes, str, int]:
        return await asyncio.to_thread(self._run_curl_download, url, timeout)

    def _run_curl_download(self, url: str, timeout: float) -> tuple[bytes, str, int]:
        command = [
            "curl",
            "-k",
            "-sS",
            "-L",
            "--max-time",
            str(max(1, int(timeout))),
            "-D",
            "-",
            "-o",
            "-",
            "-w",
            "\n%{http_code}",
            url,
        ]
        completed = subprocess.run(command, check=False, capture_output=True)
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="ignore")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Curl upstream download failed: {stderr.strip()}",
            )
        stdout = completed.stdout
        header_end = stdout.find(b"\r\n\r\n")
        if header_end == -1:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid curl download response.")
        header_block = stdout[:header_end].decode("utf-8", errors="ignore")
        remaining = stdout[header_end + 4 :]
        body, _, status_bytes = remaining.rpartition(b"\n")
        try:
            status_code = int(status_bytes.decode("utf-8", errors="ignore").strip())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid curl download status trailer.") from exc
        content_type = "image/png"
        for line in header_block.splitlines():
            if line.lower().startswith("content-type:"):
                content_type = line.split(":", 1)[1].strip().split(";", 1)[0] or "image/png"
        return body, content_type, status_code

    def _require_request_user(self) -> User:
        user = _request_user.get()
        if user is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Missing request user context.")
        return user

    def _notify_stage(self, stage: str) -> None:
        callback = _job_stage_callback.get()
        if callback is not None:
            callback(stage)

    async def _build_upload_file_from_url(self, image_url: str, filename: str | None = None) -> UploadFile:
        image_bytes, content_type = await self._download_image(image_url)
        resolved_filename = filename or f"reference{self._guess_extension(content_type)}"
        return UploadFile(
            file=BytesIO(image_bytes),
            filename=resolved_filename,
            headers=Headers({"content-type": content_type}),
        )

    def _split_multi_view_binary(
        self,
        *,
        image_bytes: bytes,
        split_x_ratio: float,
        split_y_ratio: float,
        gap_x_ratio: float,
        gap_y_ratio: float,
    ) -> list[dict[str, Any]]:
        try:
            from PIL import Image
        except ModuleNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Pillow is not installed. Please install Pillow to enable multi-view splitting.",
            ) from exc

        image = Image.open(BytesIO(image_bytes)).convert("RGBA")
        width, height = image.size

        split_x = int(round(width * split_x_ratio))
        split_y = int(round(height * split_y_ratio))
        gap_x = int(round(width * gap_x_ratio))
        gap_y = int(round(height * gap_y_ratio))

        left_end = split_x - gap_x // 2
        right_start = split_x + ceil(gap_x / 2)
        top_end = split_y - gap_y // 2
        bottom_start = split_y + ceil(gap_y / 2)

        if left_end <= 0 or top_end <= 0 or right_start >= width or bottom_start >= height:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Split line or gap is out of image bounds.",
            )

        boxes = [
            ("front", (0, 0, left_end, top_end)),
            ("side", (right_start, 0, width, top_end)),
            ("top", (0, bottom_start, left_end, height)),
            ("back", (right_start, bottom_start, width, height)),
        ]

        results: list[dict[str, Any]] = []
        for view_name, box in boxes:
            left, upper, right, lower = box
            if right - left < 8 or lower - upper < 8:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Split area for {view_name} is too small. Please adjust cut lines.",
                )

            cropped = image.crop(box)
            output = BytesIO()
            cropped.save(output, format="PNG")
            results.append(
                {
                    "view": view_name,
                    "image_bytes": output.getvalue(),
                    "width": cropped.size[0],
                    "height": cropped.size[1],
                }
            )

        return results

    def _remove_background_to_white_png(self, image_bytes: bytes) -> tuple[bytes, str]:
        try:
            from rembg import remove
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="rembg is not installed on the server.",
            ) from exc

        try:
            transparent_bytes = remove(image_bytes)
            with Image.open(BytesIO(transparent_bytes)).convert("RGBA") as foreground:
                white_background = Image.new("RGBA", foreground.size, (255, 255, 255, 255))
                white_background.alpha_composite(foreground)
                output_buffer = BytesIO()
                white_background.convert("RGB").save(output_buffer, format="PNG")
                return output_buffer.getvalue(), "image/png"
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Background removal failed: {exc}",
            ) from exc

    def _build_object_key(self, *, kind: str, model: str, content_type: str) -> str:
        now = datetime.utcnow()
        extension = self._guess_extension(content_type)
        return f"generated/{kind}/{model}/{now:%Y/%m/%d}/{uuid4()}{extension}"

    def _store_local_generated_asset(self, *, object_key: str, image_bytes: bytes) -> str:
        local_path = self._resolve_local_object_path(object_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(image_bytes)
        return f"local://{object_key}"

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
