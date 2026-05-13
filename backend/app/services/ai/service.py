import asyncio
import base64
import binascii
import json
import re
from contextvars import ContextVar, Token
from datetime import datetime
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlparse
from uuid import uuid4

import httpx
from fastapi import HTTPException, UploadFile, status
from langchain_core.prompts import FewShotPromptTemplate, PromptTemplate
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
from app.services.ai import MODEL_CATALOG, ImageModelConfig
from app.services.asset_service import AssetService
from app.services.history_service import HistoryService
from app.services.storage_service import StorageService


_request_user: ContextVar[User | None] = ContextVar("ai_request_user", default=None)
_job_stage_callback: ContextVar[Callable[[str], None] | None] = ContextVar("ai_job_stage_callback", default=None)


class AIService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.asset_service = AssetService()
        self.history_service = HistoryService()
        self.storage_service = StorageService()
        self.storage_dir = Path(__file__).resolve().parents[3] / "data"
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

            if self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    return await self._generate_with_gemini_apiyi(request=request, model=model)
                return await self._generate_with_openai_image_apiyi(request=request, model=model)

            if model.provider == ProviderType.gemini:
                return await self._generate_with_gemini(request=request, model=model)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model {request.model} is not available on TTAPI upstream.",
            )
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

            if model.provider == ProviderType.apiyi:
                result = await self._fuse_with_apiyi_gpt_image2_all(files=submit_files, metadata=metadata, model=model)
            elif model.provider == ProviderType.closeai:
                result = await self._fuse_with_closeai_gpt_image2(files=submit_files, metadata=metadata, model=model)
            elif self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    result = await self._fuse_with_gemini_apiyi(files=submit_files, metadata=metadata, model=model)
                else:
                    result = await self._fuse_with_openai_image_apiyi(files=submit_files, metadata=metadata, model=model)
            elif model.provider == ProviderType.gemini:
                result = await self._fuse_with_gemini(files=submit_files, metadata=metadata, model=model)
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model {metadata.model} is not available on TTAPI upstream.",
                )

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

            if model.provider == ProviderType.apiyi:
                return await self._transform_with_apiyi_gpt_image2_all(files=submit_files, metadata=metadata, model=model)
            if model.provider == ProviderType.closeai:
                return await self._transform_with_closeai_gpt_image2(files=submit_files, metadata=metadata, model=model)

            if self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    return await self._transform_with_gemini_apiyi(files=submit_files, metadata=metadata, model=model)
                return await self._transform_with_openai_image_apiyi(files=submit_files, metadata=metadata, model=model)

            if model.provider == ProviderType.gemini:
                return await self._transform_with_gemini(files=submit_files, metadata=metadata, model=model)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model {metadata.model} is not available on TTAPI upstream.",
            )
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
        model = self._get_model_or_404(metadata.model)

        # 多视图统一先走 Qwen 反推，再按当前模型供应商进入各自的图生图链路。
        if model.supports_reference_images:
            return await self._generate_multi_view_with_qwen_prompt(
                file=file,
                files=files,
                metadata=metadata,
                current_user=current_user,
                source_image_url=source_image_url,
                source_image_urls=source_image_urls,
                stage_callback=stage_callback,
            )

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

    async def _generate_multi_view_with_qwen_prompt(
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
        """使用 Qwen3-VL 反推提示词，再用当前 image2 模型生成多视图。"""
        context_token = _request_user.set(current_user)
        stage_token = _job_stage_callback.set(stage_callback)
        model = self._get_model_or_404(metadata.model)

        # 确定用户输入的原图
        input_file = files[0] if files else file
        primary_source_url = source_image_urls[0] if source_image_urls else source_image_url
        if input_file is None and source_image_urls:
            input_file = await self._build_upload_file_from_url(
                source_image_urls[0],
                metadata.filename or "input-image.png",
            )
        if input_file is None and source_image_url:
            input_file = await self._build_upload_file_from_url(
                source_image_url,
                metadata.filename or "input-image.png",
            )
        if input_file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="需要上传一张原图作为多视图生成的主体",
            )
        metadata = self._with_primary_multi_view_source(metadata, primary_source_url)
        generated_prompt = await self._build_qwen_multi_view_prompt(input_file=input_file, metadata=metadata)
        updated_metadata = metadata.model_copy(update={"prompt": generated_prompt, "image_count": 1})

        try:
            if model.provider == ProviderType.closeai:
                return await self._transform_with_closeai_gpt_image2(
                    files=[input_file],
                    metadata=updated_metadata,
                    model=model,
                )
            if self._use_apiyi():
                if model.provider == ProviderType.gemini:
                    return await self._transform_with_gemini_apiyi(
                        files=[input_file],
                        metadata=updated_metadata,
                        model=model,
                    )
                return await self._transform_with_apiyi_gpt_image2_all(
                    files=[input_file],
                    metadata=updated_metadata,
                    model=model,
                )
            if model.provider == ProviderType.gemini:
                return await self._transform_with_gemini(
                    files=[input_file],
                    metadata=updated_metadata,
                    model=model,
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model {metadata.model} does not support multi-view generation.",
            )
        finally:
            _job_stage_callback.reset(stage_token)
            _request_user.reset(context_token)

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

    def _get_model_or_404(self, model_id: str) -> ImageModelConfig:
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

    def _require_closeai_api_key(self) -> str:
        if not self.settings.closeai_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="CLOSEAI_API_KEY is not configured.",
            )
        return self.settings.closeai_api_key

    def _use_apiyi(self) -> bool:
        return self.settings.ai_upstream_platform.strip().lower() == "apiyi"

    def _upstream_platform_label(self) -> str:
        return "APIYI" if self._use_apiyi() else "TTAPI"

    def _build_model_pricing_hint(self, model: ImageModelConfig, platform_label: str) -> str:
        if model.provider == ProviderType.apiyi:
            return model.pricing_hint
        if model.id == "gemini-3.1-flash-image-preview":
            return f"Nano Banana 2 image generation via {platform_label}"
        return f"{model.label} via {platform_label}"

    async def _fuse_with_apiyi_gpt_image2_all(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
    ) -> GenerationResult:
        prompt = self._build_fusion_prompt(metadata)
        data = await self._post_apiyi_gpt_image2_vip_edit(
            model=model,
            prompt=prompt,
            files=files,
            size="auto",
        )
        return await self._finalize_openai_edit_fusion_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=prompt,
            upstream_platform="apiyi",
            upstream_api="images_edits",
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )

    async def _fuse_with_closeai_gpt_image2(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
    ) -> GenerationResult:
        prompt = self._build_fusion_prompt(metadata)
        data = await self._post_closeai_gpt_image2_edit(
            model=model,
            prompt=prompt,
            files=files,
            size="auto",
        )
        return await self._finalize_openai_edit_fusion_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=prompt,
            upstream_platform="closeai",
            upstream_api="images_edits",
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )

    async def _transform_with_closeai_gpt_image2(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
    ) -> GenerationResult:
        prompt = self._build_apiyi_reference_prompt(metadata)
        data = await self._post_closeai_gpt_image2_edit(
            model=model,
            prompt=prompt,
            files=files,
            size=self._map_apiyi_vip_edit_size(metadata.image_size),
        )
        return await self._finalize_openai_edit_reference_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=prompt,
            upstream_platform="closeai",
            upstream_api="images_edits",
            preferred_name=metadata.filename,
        )

    async def _transform_with_apiyi_gpt_image2_all(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
    ) -> GenerationResult:
        prompt = self._build_apiyi_reference_prompt(metadata)
        data = await self._post_apiyi_gpt_image2_vip_edit(
            model=model,
            prompt=prompt,
            files=files,
            size=self._map_apiyi_vip_edit_size(metadata.image_size),
        )
        return await self._finalize_openai_edit_reference_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=prompt,
            upstream_platform="apiyi",
            upstream_api="images_edits",
            preferred_name=metadata.filename,
        )

    async def _generate_with_gemini_apiyi(
        self,
        *,
        request: TextToImageRequest,
        model: ImageModelConfig,
    ) -> GenerationResult:
        api_key = self._require_apiyi_api_key()
        data = await self._post_json_with_bearer(
            base_url=self.settings.apiyi_gemini_base_url,
            path=f"/models/{model.upstream_model_id}:generateContent",
            api_key=api_key,
            payload={
                "contents": [{"parts": [{"text": request.prompt}]}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": request.aspect_ratio,
                        "imageSize": request.image_size,
                    },
                    "thinkingConfig": {
                        "thinkingLevel": self._gemini_thinking_level(request.thinking_level),
                    },
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        return await self._finalize_gemini_text_result(
            data=data,
            model=model,
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
            image_size=request.image_size,
            upstream_platform="apiyi",
        )

    async def _generate_with_openai_image_apiyi(
        self,
        *,
        request: TextToImageRequest,
        model: ImageModelConfig,
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
        model: ImageModelConfig,
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
        return await self._finalize_gemini_text_result(
            data=data,
            model=model,
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
            image_size=request.image_size,
        )

    async def _fuse_with_gemini(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        refer_images = [await self._file_to_data_url(file) for file in files]
        payload = {
            "model": model.id,
            "prompt": self._build_fusion_prompt(metadata),
            "aspect_ratio": "1:1",
            "image_size": "1K",
            "thinking_level": "High",
            "refer_images": refer_images,
        }
        data = await self._post_json(
            base_url=self.settings.ttapi_openai_base_url,
            path="/gemini/image/generate",
            api_key=api_key,
            payload=payload,
        )
        return await self._finalize_gemini_fusion_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=metadata.prompt,
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )

    async def _fuse_with_gemini_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
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
            path=f"/models/{model.upstream_model_id}:generateContent",
            api_key=api_key,
            payload={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": "1:1",
                        "imageSize": "1K",
                    },
                    "thinkingConfig": {
                        "thinkingLevel": self._gemini_thinking_level(),
                    },
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        return await self._finalize_gemini_fusion_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=metadata.prompt,
            preferred_name=self._preferred_fusion_asset_name(metadata),
            upstream_platform="apiyi",
        )

    async def _transform_with_gemini(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
    ) -> GenerationResult:
        api_key = self._require_api_key()
        refer_images = [await self._file_to_data_url(file) for file in files]
        payload = {
            "model": model.id,
            "prompt": metadata.prompt,
            "aspect_ratio": "1:1",
            "image_size": metadata.image_size,
            "thinking_level": "High",
            "refer_images": refer_images,
        }
        data = await self._post_json(
            base_url=self.settings.ttapi_openai_base_url,
            path="/gemini/image/generate",
            api_key=api_key,
            payload=payload,
        )
        return await self._finalize_gemini_reference_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=metadata.prompt,
            preferred_name=metadata.filename,
        )

    async def _transform_with_gemini_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
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
            path=f"/models/{model.upstream_model_id}:generateContent",
            api_key=api_key,
            payload={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": "1:1",
                        "imageSize": metadata.image_size,
                    },
                    "thinkingConfig": {
                        "thinkingLevel": self._gemini_thinking_level(),
                    },
                },
            },
            timeout=self.settings.apiyi_timeout_seconds,
        )
        return await self._finalize_gemini_reference_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=metadata.prompt,
            preferred_name=metadata.filename,
            upstream_platform="apiyi",
        )

    async def _fuse_with_openai_image_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
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
        return await self._finalize_openai_edit_fusion_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=metadata.prompt,
            upstream_platform="apiyi",
            preferred_name=self._preferred_fusion_asset_name(metadata),
        )

    async def _transform_with_openai_image_apiyi(
        self,
        *,
        files: list[UploadFile],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
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
        return await self._finalize_openai_edit_reference_result(
            data=data,
            metadata=metadata,
            model=model,
            prompt=metadata.prompt,
            upstream_platform="apiyi",
            preferred_name=metadata.filename,
        )

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

    async def _post_apiyi_gpt_image2_vip_edit(
        self,
        *,
        model: ImageModelConfig,
        prompt: str,
        files: list[UploadFile],
        size: str = "auto",
    ) -> dict[str, Any]:
        multipart_files = [await self._build_named_multipart_file(file, field_name="image") for file in files]
        if not multipart_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one image is required for APIYI image edit.",
            )
        return await self._post_multipart_with_bearer_base_url(
            base_url=self.settings.apiyi_openai_base_url,
            path="/images/edits",
            api_key=self._require_apiyi_api_key(),
            data={
                "model": model.upstream_model_id,
                "prompt": prompt,
                "size": size,
                "response_format": "url",
            },
            files=multipart_files,
            timeout=self.settings.apiyi_timeout_seconds,
        )

    async def _post_closeai_gpt_image2_edit(
        self,
        *,
        model: ImageModelConfig,
        prompt: str,
        files: list[UploadFile],
        size: str = "auto",
    ) -> dict[str, Any]:
        multipart_files = [await self._build_named_multipart_file(file, field_name="image[]") for file in files]
        if not multipart_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one image is required for CloseAI image edit.",
            )
        return await self._post_multipart_with_bearer_base_url(
            base_url=self.settings.closeai_base_url,
            path="/images/edits",
            api_key=self._require_closeai_api_key(),
            data={
                "model": model.upstream_model_id,
                "prompt": prompt,
                "size": size,
            },
            files=multipart_files,
            timeout=self.settings.closeai_timeout_seconds,
        )

    async def _build_qwen_multi_view_prompt(
        self,
        *,
        input_file: UploadFile,
        metadata: ReferenceImageRequestMetadata,
    ) -> str:
        api_key = self._require_dashscope_api_key()
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DASHSCOPE_API_KEY or AGENT_LLM_API_KEY is required for multi-view prompt generation.",
        )

        self._notify_stage("qwen_prompt")
        data_url = await self._file_to_data_url(input_file)
        prompt = self._build_qwen_multi_view_prompt_request_text(metadata.prompt)
        data = await self._post_dashscope_qwen_chat(
            api_key=api_key,
            payload={
                "model": self.settings.multi_view_prompt_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
                "stream": True,
                "enable_thinking": True,
                "thinking_budget": self.settings.multi_view_prompt_thinking_budget,
            },
        )
        generated_prompt = self._extract_chat_completion_content(data)
        if not generated_prompt:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"message": "Qwen prompt generation returned no content.", "upstream_response": data},
            )
        self._notify_stage("image_generation")
        return self._normalize_single_line_prompt(generated_prompt)

    def _build_qwen_multi_view_prompt_request_text(self, user_prompt: str | None) -> str:
        normalized_user_prompt = self._normalize_multi_view_user_prompt(user_prompt)
        user_prompt_section = normalized_user_prompt or "无"
        one_shot_prompt = self._multi_view_prompt_template_context()
        example_prompt = PromptTemplate.from_template("{example_text}")
        request_template = FewShotPromptTemplate(
            examples=[{"example_text": one_shot_prompt}],
            example_prompt=example_prompt,
            prefix=(
                "任务：请反推当前珠宝图片的生成提示词，并集成给出一份新的生图提示词。"
                "输出要求：只输出中文纯文本，不要 Markdown，不要换行符，不要额外解释。"
                "最终提示词必须使用中文标点断句，至少使用逗号、句号或分号分隔主体描述、四视角要求和整体风格，不能输出没有标点的一整段长句。"
                "新提示词必须要求生成该珠宝的四个角度视角：正视图、左侧视90度、右侧视90度、背视图。"
                # "四视角必须明确写为：正视（需与原图一致）、左侧视（90度）、右侧视（90度）、背视。"
                "正视图必须与原图完全一致，无需过多赘述；左侧视和右侧视必须基于参考图清楚表达90度侧面结构，；背视必须基于参考图合理描述，一般来说简单的镶嵌包裹描述即可。"
                "请先观察当前图片，反推珠宝的设计提示词，再把这些信息写入最终提示词。"
                "只能把模板中的省略内容替换为从当前图片反推出的具体信息，不得删减模板要求，不得缩写成摘要，不得改写成短句列表。"
                "如果此珠宝为戒指，则左右侧视图需要45度视角，其他默认90度"
            ),
            suffix=(
                "用户补充提示词：{user_prompt}。"
                "现在请基于当前图片和用户补充提示词，直接输出最终生图提示词。"
                "如果用户提示词存在，则它的权重最高。"
            ),
            input_variables=["user_prompt"],
            example_separator="\n\n",
        )
        return request_template.format(user_prompt=user_prompt_section)

    def _multi_view_prompt_template_context(self) -> str:
        template = PromptTemplate.from_template(
            (
            "展示一件...。设计风格为...，采用....造型。"
            "生成基于参考图的4个标准视角（4视图），每个视图在几何上必须忠实于参考模型。"
            "背景为纯白色，突出珠宝设计。图片需包含四个视角的展示："
            "1. 正视图：正面垂直视角，绝对与参照图一致保持一成不变。"
            "2. 左侧视：从左侧90度角拍摄，展示金属镶嵌的简约风格，自然主义工艺、艺术雕塑感，无缝连续的有机曲线轮廓，简洁实心结构，无层间空隙或分层细节.....。"
            "3. 右侧视：从右侧90度角拍摄，展示珠宝的另一侧.....。"
            "4. 背视：背面视角，展示珠宝的背面结构.....。"
            "整体风格：.......，线条清晰，镶嵌金属简约风格，统一金属包裹。"
            "无需生成文字解释。"
            ),
            template_format="mustache",
        )
        return template.template

    def _require_dashscope_api_key(self) -> str | None:
        dashscope_api_key = getattr(self.settings, "dashscope_api_key", None)
        agent_llm_api_key = getattr(self.settings, "agent_llm_api_key", None)
        return (dashscope_api_key or agent_llm_api_key or "").strip() or None

    async def _post_dashscope_qwen_chat(self, *, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = self.settings.agent_llm_base_url.rstrip("/")
        path = "/chat/completions" if base_url.endswith("/v1") else "/v1/chat/completions"
        async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{base_url}{path}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    response_text = await response.aread()
                    try:
                        error_data = json.loads(response_text.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        error_data = {"message": response_text.decode("utf-8", errors="replace")}
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail={"upstream_status": response.status_code, "upstream_response": error_data},
                    )
                answer_parts: list[str] = []
                usage: dict[str, Any] | None = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    raw_data = line.removeprefix("data:").strip()
                    if raw_data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw_data)
                    except json.JSONDecodeError:
                        continue
                    if not chunk.get("choices"):
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
                        continue
                    text = self._extract_stream_delta_text(chunk)
                    if text:
                        answer_parts.append(text)
        message: dict[str, Any] = {"content": "".join(answer_parts)}
        data: dict[str, Any] = {"choices": [{"message": message}]}
        if usage:
            data["usage"] = usage
        return data

    def _extract_stream_delta_text(self, body: dict[str, Any]) -> str:
        choice = (body.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, str):
                    fragments.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        fragments.append(text)
            return "".join(fragments)
        fallback_text = choice.get("text")
        return fallback_text if isinstance(fallback_text, str) else ""

    def _normalize_single_line_prompt(self, prompt: str) -> str:
        return re.sub(r"\s+", " ", prompt.strip().strip("`")).strip()

    def _build_apiyi_reference_prompt(self, metadata: ReferenceImageRequestMetadata) -> str:
        return metadata.prompt.strip()

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
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
            )
        return self._handle_response(response)

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
                    if b64_json.startswith("data:"):
                        header, _, encoded = b64_json.partition(",")
                        mime_match = re.match(r"data:([^;]+);base64", header)
                        content_type = mime_match.group(1) if mime_match else "image/png"
                        return base64.b64decode(encoded), content_type, None
                    return base64.b64decode(b64_json), "image/png", None
                url = item.get("url")
                if isinstance(url, str) and url:
                    return None, None, url
        return None, None, None

    def _map_apiyi_model_id(self, model_id: str) -> str:
        return model_id

    def _map_apiyi_vip_edit_size(self, image_size: str | None) -> str:
        if image_size in {"2K", "2048x2048"}:
            return "2048x2048"
        if image_size in {"4K", "2880x2880"}:
            return "2880x2880"
        return "auto"

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

    async def _finalize_openai_edit_fusion_result(
        self,
        *,
        data: dict[str, Any],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
        prompt: str,
        upstream_platform: str,
        preferred_name: str | None,
        upstream_api: str | None = None,
    ) -> GenerationResult:
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind="fusion",
            model=model.id,
            preferred_name=preferred_name,
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
            history_metadata: dict[str, object] = {
                "upstream_platform": upstream_platform,
                "upstream_model": model.upstream_model_id,
                **self._build_fusion_history_metadata(metadata),
                "negative_prompt": metadata.negative_prompt,
                "strength": metadata.strength,
                **stored_asset["metadata"],
                **raw_asset_metadata,
            }
            if upstream_api:
                history_metadata["upstream_api"] = upstream_api
            self._persist_history(
                kind="fusion",
                title=self._history_title_for_kind("fusion"),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata=history_metadata,
            )
        return result

    async def _finalize_openai_edit_reference_result(
        self,
        *,
        data: dict[str, Any],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
        prompt: str,
        upstream_platform: str,
        preferred_name: str | None,
        upstream_api: str | None = None,
    ) -> GenerationResult:
        stored_asset, raw_asset_metadata = await self._store_generated_result_from_openai_payload(
            data=data,
            kind=metadata.feature,
            model=model.id,
            preferred_name=preferred_name,
        )
        result = GenerationResult(
            job_id=self._extract_job_id(data),
            status="completed" if stored_asset["access_url"] else "failed",
            provider=model.provider,
            model=model.id,
            image_url=stored_asset["access_url"],
            revised_prompt=prompt,
            message="Reference image transform completed." if stored_asset["access_url"] else "Reference image transform returned no asset.",
            raw_response=data,
        ).model_copy(update=self._build_reference_result_update(metadata))
        if result.image_url:
            history_metadata: dict[str, object] = {
                "upstream_platform": upstream_platform,
                "upstream_model": model.upstream_model_id,
                "original_prompt": metadata.prompt,
                **stored_asset["metadata"],
                **raw_asset_metadata,
                **self._build_reference_history_metadata(metadata),
            }
            if upstream_api:
                history_metadata["upstream_api"] = upstream_api
            self._persist_history(
                kind=self._map_feature_to_history_kind(metadata.feature),
                title=self._feature_title(metadata.feature, model.label),
                model_id=model.id,
                provider=model.provider.value,
                status=result.status,
                prompt=prompt,
                job_id=result.job_id,
                image_url=result.image_url,
                storage_url=stored_asset["storage_url"],
                metadata=history_metadata,
            )
        return result

    async def _finalize_gemini_text_result(
        self,
        *,
        data: dict[str, Any],
        model: ImageModelConfig,
        prompt: str,
        aspect_ratio: str,
        image_size: str,
        upstream_platform: str | None = None,
    ) -> GenerationResult:
        stored_asset = await self._store_gemini_result_asset(
            data=data,
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
        history_metadata: dict[str, object] = {
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
            **stored_asset["metadata"],
        }
        if upstream_platform:
            history_metadata["upstream_platform"] = upstream_platform
        self._persist_history(
            kind="text_to_image",
            title=self._history_title_for_kind("text_to_image"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=prompt,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata=history_metadata,
        )
        return result

    async def _finalize_gemini_fusion_result(
        self,
        *,
        data: dict[str, Any],
        metadata: FusionRequestMetadata,
        model: ImageModelConfig,
        prompt: str,
        preferred_name: str | None,
        upstream_platform: str | None = None,
    ) -> GenerationResult:
        stored_asset = await self._store_gemini_result_asset(
            data=data,
            kind="fusion",
            model=model.id,
            preferred_name=preferred_name,
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
        history_metadata: dict[str, object] = {
            **self._build_fusion_history_metadata(metadata),
            **stored_asset["metadata"],
        }
        if upstream_platform:
            history_metadata["upstream_platform"] = upstream_platform
            history_metadata["negative_prompt"] = metadata.negative_prompt
            history_metadata["strength"] = metadata.strength
        self._persist_history(
            kind="fusion",
            title=self._history_title_for_kind("fusion"),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=prompt,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata=history_metadata,
        )
        return result

    async def _finalize_gemini_reference_result(
        self,
        *,
        data: dict[str, Any],
        metadata: ReferenceImageRequestMetadata,
        model: ImageModelConfig,
        prompt: str,
        preferred_name: str | None,
        upstream_platform: str | None = None,
    ) -> GenerationResult:
        stored_asset = await self._store_gemini_result_asset(
            data=data,
            kind=metadata.feature,
            model=model.id,
            preferred_name=preferred_name,
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
        history_metadata: dict[str, object] = {
            **stored_asset["metadata"],
            **self._build_reference_history_metadata(metadata),
        }
        if upstream_platform:
            history_metadata["upstream_platform"] = upstream_platform
        self._persist_history(
            kind=self._map_feature_to_history_kind(metadata.feature),
            title=self._feature_title(metadata.feature, model.label),
            model_id=model.id,
            provider=model.provider.value,
            status=result.status,
            prompt=prompt,
            image_url=result.image_url,
            storage_url=stored_asset["storage_url"],
            metadata=history_metadata,
        )
        return result

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

    def _extension_from_content_type(self, content_type: str) -> str:
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        return mapping.get(content_type.lower(), ".png")

    def _normalize_multi_view_user_prompt(self, prompt: str | None) -> str:
        if not prompt:
            return ""
        normalized = prompt.strip()
        if normalized in {"默认多视图规则", "生成多视图", "默认规则"}:
            return ""
        legacy_default_fragments = (
            "生成基于参考图的4个标准视角",
            "所有四个视图必须来自一个连贯的三维模型",
            "纯白色哑光珠宝垫背景",
        )
        if all(fragment in normalized for fragment in legacy_default_fragments):
            return ""
        return normalized

    def _with_primary_multi_view_source(
        self,
        metadata: ReferenceImageRequestMetadata,
        source_image_url: str | None,
    ) -> ReferenceImageRequestMetadata:
        if not source_image_url:
            return metadata

        filename = metadata.filename or "input-image.png"
        return metadata.model_copy(
            update={
                "source_image_url": self._resolve_source_preview_url(source_image_url, filename),
                "source_image_storage_url": source_image_url if self._is_custom_storage_url(source_image_url) else None,
                "source_images": [
                    self._build_source_image_reference(url=source_image_url, filename=filename),
                ],
            }
        )

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

    def _gemini_thinking_level(self, value: str | None = None) -> str:
        normalized = (value or "High").strip().lower()
        if normalized in {"high", "deep", "深度推理"}:
            return "High"
        return "minimal"

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

    async def _store_gemini_result_asset(
        self,
        *,
        data: dict[str, Any],
        kind: str,
        model: str,
        preferred_name: str | None = None,
        name_suffix: str | None = None,
    ) -> dict[str, Any]:
        try:
            image_bytes, content_type = self._extract_inline_image(data)
        except HTTPException:
            image_url = self._extract_image_url(data)
            return await self._store_generated_asset(
                image_url=image_url,
                kind=kind,
                model=model,
                preferred_name=preferred_name,
                name_suffix=name_suffix,
            )
        return await self._store_generated_binary_asset(
            image_bytes=image_bytes,
            content_type=content_type,
            kind=kind,
            model=model,
            preferred_name=preferred_name,
            name_suffix=name_suffix,
        )

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

        async with httpx.AsyncClient(timeout=self.settings.ttapi_timeout_seconds) as client:
            response = await client.get(image_url)
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to download upstream image asset, status={response.status_code}.",
            )
        content_type = response.headers.get("Content-Type", "image/png").split(";")[0].strip() or "image/png"
        return response.content, content_type

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
