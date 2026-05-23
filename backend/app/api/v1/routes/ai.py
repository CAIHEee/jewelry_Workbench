import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from app.api.deps import require_module
from app.core.config import get_settings
from app.models.user import User
from app.schemas.ai import (
    FusionJobAccepted,
    FusionMode,
    FusionRequestMetadata,
    GenerationFeatureCatalog,
    GenerationResult,
    ModelCatalogResponse,
    MultiViewSplitRequest,
    MultiViewSplitResponse,
    ReferenceImageRequestMetadata,
    TextToImageRequest,
)
from app.services.ai_service import AIService
from app.services.cache_service import get_cache_service


router = APIRouter()
service = AIService()
cache_service = get_cache_service()


def _parse_string_array_json(value: str | None, *, field_name: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}.") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be a string array.")
    return parsed


@router.get("/features", response_model=GenerationFeatureCatalog)
def list_features() -> GenerationFeatureCatalog:
    return service.get_feature_catalog()


@router.get("/models", response_model=ModelCatalogResponse)
def list_models() -> ModelCatalogResponse:
    # 不缓存模型目录，因为密钥激活状态可能随时变化
    # 每次请求都重新获取，确保返回最新的模型列表
    response = service.get_model_catalog()
    return response


@router.post("/text-to-image", response_model=GenerationResult)
async def text_to_image(
    payload: TextToImageRequest,
    current_user: User = Depends(require_module("text_to_image")),
) -> GenerationResult:
    return await service.generate_text_to_image(payload, current_user=current_user)


@router.post("/reference-image-transform", response_model=GenerationResult)
async def reference_image_transform(
    image: UploadFile | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    feature: str = Form(default="image_edit"),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="1K"),
    current_user: User = Depends(require_module("image_edit")),
) -> GenerationResult:
    if image is None and not source_image_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either image or source_image_url is required.")

    metadata = ReferenceImageRequestMetadata(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        feature=feature,
        strength=strength,
        image_size=image_size,
        filename=(image.filename if image else source_image_name) or "reference.png",
    )
    return await service.transform_reference_image(file=image, metadata=metadata, current_user=current_user, source_image_url=source_image_url)


async def _reference_feature_transform(
    *,
    image: UploadFile | None,
    prompt: str,
    model: str,
    feature: str,
    source_image_url: str | None,
    source_image_name: str | None,
    negative_prompt: str | None,
    strength: float,
    image_size: str,
    current_user: User,
) -> GenerationResult:
    if image is None and not source_image_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either image or source_image_url is required.")

    metadata = ReferenceImageRequestMetadata(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        feature=feature,
        strength=strength,
        image_size=image_size,
        filename=(image.filename if image else source_image_name) or "reference.png",
    )
    return await service.transform_reference_image(file=image, metadata=metadata, current_user=current_user, source_image_url=source_image_url)


async def _multi_reference_feature_transform(
    *,
    image: UploadFile | None,
    images: list[UploadFile] | None,
    prompt: str,
    model: str,
    feature: str,
    source_image_url: str | None,
    source_image_name: str | None,
    source_image_urls_json: str | None,
    source_image_names_json: str | None,
    negative_prompt: str | None,
    strength: float,
    image_size: str,
    current_user: User,
) -> GenerationResult:
    settings = get_settings()
    source_image_urls = _parse_string_array_json(source_image_urls_json, field_name="source_image_urls_json")
    source_image_names = _parse_string_array_json(source_image_names_json, field_name="source_image_names_json")

    if source_image_url:
        source_image_urls = [source_image_url, *source_image_urls]
        source_image_names = [source_image_name or "reference.png", *source_image_names]

    image_files = [*(images or [])]
    if image is not None:
        image_files = [image, *image_files]

    total_images = len(image_files) if image_files else len(source_image_urls)
    if total_images < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one reference image is required.")
    if total_images > settings.ai_max_fusion_images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {settings.ai_max_fusion_images} reference images are supported.",
        )
    if source_image_urls and source_image_names and len(source_image_urls) != len(source_image_names):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_image_urls_json and source_image_names_json must have the same length.",
        )

    filenames = (
        [file.filename or f"reference-{index + 1}.png" for index, file in enumerate(image_files)]
        if image_files
        else [source_image_names[index] if index < len(source_image_names) else f"reference-{index + 1}.png" for index in range(len(source_image_urls))]
    )
    metadata = ReferenceImageRequestMetadata(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        feature=feature,
        strength=strength,
        image_size=image_size,
        image_count=total_images,
        filename=filenames[0],
        filenames=filenames,
    )
    return await service.transform_reference_images(
        files=image_files,
        metadata=metadata,
        current_user=current_user,
        source_image_urls=source_image_urls,
    )


@router.post("/product-refine", response_model=GenerationResult)
async def product_refine(
    image: UploadFile | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    source_image_urls_json: str | None = Form(default=None),
    source_image_names_json: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="1K"),
    current_user: User = Depends(require_module("product_refine")),
) -> GenerationResult:
    return await _multi_reference_feature_transform(
        image=image,
        images=images,
        prompt=prompt,
        model=model,
        feature="product_refine",
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=source_image_urls_json,
        source_image_names_json=source_image_names_json,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
    )


@router.post("/gemstone-design", response_model=GenerationResult)
async def gemstone_design(
    image: UploadFile | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    source_image_urls_json: str | None = Form(default=None),
    source_image_names_json: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="1K"),
    current_user: User = Depends(require_module("gemstone_design")),
) -> GenerationResult:
    return await _multi_reference_feature_transform(
        image=image,
        images=images,
        prompt=prompt,
        model=model,
        feature="gemstone_design",
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=source_image_urls_json,
        source_image_names_json=source_image_names_json,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
    )


@router.post("/upscale", response_model=GenerationResult)
async def upscale(
    image: UploadFile | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="2K"),
    current_user: User = Depends(require_module("upscale")),
) -> GenerationResult:
    return await _reference_feature_transform(
        image=image,
        prompt=prompt,
        model=model,
        feature="upscale",
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
    )


@router.post("/multi-view", response_model=GenerationResult)
async def multi_view_generation(
    image: UploadFile | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    source_image_urls_json: str | None = Form(default=None),
    source_image_names_json: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="1K"),
    current_user: User = Depends(require_module("multi_view")),
) -> GenerationResult:
    source_image_urls = _parse_string_array_json(source_image_urls_json, field_name="source_image_urls_json")
    source_image_names = _parse_string_array_json(source_image_names_json, field_name="source_image_names_json")
    image_files = [*(images or [])]
    if image is not None:
        image_files = [image, *image_files]
    if source_image_url:
        source_image_urls = [source_image_url, *source_image_urls]
        source_image_names = [source_image_name or "multi-view-reference.png", *source_image_names]
    if not image_files and not source_image_urls:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either image or source_image_url is required.")
    if source_image_urls and source_image_names and len(source_image_urls) != len(source_image_names):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_image_urls_json and source_image_names_json must have the same length.")

    metadata = ReferenceImageRequestMetadata(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        feature="multi_view",
        strength=strength,
        image_size=image_size,
        image_count=len(image_files) if image_files else len(source_image_urls),
        filename=(image_files[0].filename if image_files else source_image_names[0] if source_image_names else source_image_name) or "multi-view-reference.png",
        filenames=[file.filename or f"reference-{index + 1}.png" for index, file in enumerate(image_files)]
        if image_files
        else source_image_names,
    )
    return await service.generate_multi_view(
        file=None,
        files=image_files,
        metadata=metadata,
        current_user=current_user,
        source_image_urls=source_image_urls,
    )


@router.post("/split-multi-view", response_model=MultiViewSplitResponse)
async def split_multi_view(
    payload: MultiViewSplitRequest,
    current_user: User = Depends(require_module("multi_view_split")),
) -> MultiViewSplitResponse:
    return await service.split_multi_view_image(payload, current_user=current_user)


@router.post("/remove-background", response_class=Response)
async def remove_background(
    image: UploadFile | None = File(default=None),
    source_image_url: str | None = Form(default=None),
    current_user: User = Depends(require_module("remove_background")),
) -> Response:
    image_bytes, media_type = await service.remove_background_image(
        file=image,
        source_image_url=source_image_url,
        current_user=current_user,
    )
    return Response(
        content=image_bytes,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/fuse-images",
    response_model=FusionJobAccepted,
    status_code=status.HTTP_200_OK,
)
async def fuse_images(
    images: list[UploadFile] | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    source_image_urls_json: str | None = Form(default=None),
    source_image_names_json: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    mode: FusionMode = Form(default=FusionMode.balanced),
    primary_image_index: int = Form(default=0),
    strength: float = Form(default=0.75),
    current_user: User = Depends(require_module("multi_image_fusion")),
) -> FusionJobAccepted:
    settings = get_settings()
    source_image_urls: list[str] = []
    source_image_names: list[str] = []

    if source_image_urls_json:
        try:
            parsed_urls = json.loads(source_image_urls_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid source_image_urls_json.") from exc
        if not isinstance(parsed_urls, list) or not all(isinstance(item, str) and item for item in parsed_urls):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_image_urls_json must be a string array.")
        source_image_urls = parsed_urls

    if source_image_names_json:
        try:
            parsed_names = json.loads(source_image_names_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid source_image_names_json.") from exc
        if not isinstance(parsed_names, list) or not all(isinstance(item, str) and item for item in parsed_names):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_image_names_json must be a string array.")
        source_image_names = parsed_names

    image_files = images or []
    total_images = len(image_files) if image_files else len(source_image_urls)

    if total_images < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least two images are required for fusion.",
        )

    if total_images > settings.ai_max_fusion_images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {settings.ai_max_fusion_images} images are supported.",
        )

    if primary_image_index < 0 or primary_image_index >= total_images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="primary_image_index is out of range.",
        )

    if source_image_urls and source_image_names and len(source_image_urls) != len(source_image_names):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_image_urls_json and source_image_names_json must have the same length.",
        )

    filenames = (
        [file.filename or f"image-{index + 1}.png" for index, file in enumerate(image_files)]
        if image_files
        else [source_image_names[index] if index < len(source_image_names) else f"image-{index + 1}.png" for index in range(len(source_image_urls))]
    )
    metadata = FusionRequestMetadata(
        model=model,
        image_count=total_images,
        prompt=prompt,
        negative_prompt=negative_prompt,
        mode=mode,
        primary_image_index=primary_image_index,
        strength=strength,
        filenames=filenames,
    )
    return await service.fuse_images(files=image_files, metadata=metadata, current_user=current_user, source_image_urls=source_image_urls)
