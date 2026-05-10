import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_current_user, require_module
from app.core.config import get_settings
from app.models.user import User
from app.schemas.ai import FusionMode, FusionRequestMetadata, MultiViewSplitRequest, ReferenceImageRequestMetadata, TextToImageRequest
from app.schemas.jobs import GenerationJobAccepted, GenerationJobStatusResponse
from app.services.asset_service import AssetService
from app.services.job_queue_service import JobQueueService


router = APIRouter(prefix="/ai/jobs")
job_service = JobQueueService()
asset_service = AssetService()


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


async def _store_uploaded_sources(
    *,
    files: list[UploadFile],
    module_kind: str,
    current_user: User,
) -> tuple[list[str], list[str]]:
    source_urls: list[str] = []
    source_names: list[str] = []
    for index, file in enumerate(files):
        original_name = file.filename or f"{module_kind}-{index + 1}.png"
        stored = await asset_service.create_input_asset(
            file=file,
            module_kind=module_kind,
            current_user=current_user,
        )
        source_urls.append(stored.storage_url)
        source_names.append(original_name)
    return source_urls, source_names


@router.get("/{job_id}", response_model=GenerationJobStatusResponse)
def get_generation_job(job_id: str, current_user: User = Depends(get_current_user)) -> GenerationJobStatusResponse:
    return job_service.get_job(job_id=job_id, current_user=current_user)


@router.post("/text-to-image", response_model=GenerationJobAccepted)
def enqueue_text_to_image(
    payload: TextToImageRequest,
    current_user: User = Depends(require_module("text_to_image")),
) -> GenerationJobAccepted:
    return job_service.enqueue_job(
        current_user=current_user,
        feature_key="text_to_image",
        model=payload.model,
        prompt=payload.prompt,
        request_payload={"request": payload.model_dump(mode="json")},
    )


async def _enqueue_reference_job(
    *,
    feature_key: str,
    image: UploadFile | None,
    images: list[UploadFile] | None,
    prompt: str,
    model: str,
    source_image_url: str | None,
    source_image_name: str | None,
    source_image_urls_json: str | None,
    source_image_names_json: str | None,
    negative_prompt: str | None,
    strength: float,
    image_size: str,
    current_user: User,
    batch_index: int = 0,
    batch_size: int = 1,
) -> GenerationJobAccepted:
    settings = get_settings()
    job_service.ensure_can_enqueue(current_user=current_user, active_job_limit_override=batch_size)

    source_urls = _parse_string_array_json(source_image_urls_json, field_name="source_image_urls_json")
    source_names = _parse_string_array_json(source_image_names_json, field_name="source_image_names_json")
    upload_files = [*(images or [])]
    if image is not None:
        upload_files = [image, *upload_files]

    if upload_files:
        uploaded_urls, uploaded_names = await _store_uploaded_sources(files=upload_files, module_kind=feature_key, current_user=current_user)
        source_urls = [*uploaded_urls, *source_urls]
        source_names = [*uploaded_names, *source_names]

    if source_image_url:
        source_urls = [source_image_url, *source_urls]
        source_names = [source_image_name or "reference.png", *source_names]

    if not source_urls:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少需要一张参考图。")
    if len(source_urls) > settings.ai_max_fusion_images:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"At most {settings.ai_max_fusion_images} reference images are supported.")
    if source_names and len(source_names) != len(source_urls):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_image_urls_json and source_image_names_json must have the same length.")

    metadata = ReferenceImageRequestMetadata(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        feature=feature_key,
        strength=strength,
        image_size=image_size,
        image_count=len(source_urls),
        filename=source_names[0] if source_names else "reference.png",
        filenames=source_names,
    )
    return job_service.enqueue_job(
        current_user=current_user,
        feature_key=feature_key,
        model=model,
        prompt=prompt,
        request_payload={
            "metadata": metadata.model_dump(mode="json"),
            "source_image_urls": source_urls,
            **({"batch_size": batch_size} if batch_size > 1 else {}),
        },
        active_job_limit_override=batch_size,
    )


@router.post("/reference-image-transform", response_model=GenerationJobAccepted)
async def enqueue_reference_image_transform(
    image: UploadFile | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    feature: str = Form(default="image_edit"),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="1K"),
    batch_size: int = Form(default=1),
    batch_index: int = Form(default=0),
    current_user: User = Depends(require_module("image_edit")),
) -> GenerationJobAccepted:
    if batch_size not in {1, 2, 4}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="生成数量只能选择 1、2 或 4。")
    if batch_index < 0 or batch_index >= batch_size:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="批量任务序号无效。")
    return await _enqueue_reference_job(
        feature_key=feature,
        image=image,
        images=None,
        prompt=prompt,
        model=model,
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=None,
        source_image_names_json=None,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
        batch_index=batch_index,
        batch_size=batch_size,
    )


@router.post("/product-refine", response_model=GenerationJobAccepted)
async def enqueue_product_refine(
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
) -> GenerationJobAccepted:
    return await _enqueue_reference_job(
        feature_key="product_refine",
        image=image,
        images=images,
        prompt=prompt,
        model=model,
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=source_image_urls_json,
        source_image_names_json=source_image_names_json,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
    )


@router.post("/gemstone-design", response_model=GenerationJobAccepted)
async def enqueue_gemstone_design(
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
) -> GenerationJobAccepted:
    return await _enqueue_reference_job(
        feature_key="gemstone_design",
        image=image,
        images=images,
        prompt=prompt,
        model=model,
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=source_image_urls_json,
        source_image_names_json=source_image_names_json,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
    )


@router.post("/upscale", response_model=GenerationJobAccepted)
async def enqueue_upscale(
    image: UploadFile | None = File(default=None),
    prompt: str = Form(...),
    model: str = Form(...),
    source_image_url: str | None = Form(default=None),
    source_image_name: str | None = Form(default=None),
    negative_prompt: str | None = Form(default=None),
    strength: float = Form(default=0.75),
    image_size: str = Form(default="2K"),
    current_user: User = Depends(require_module("upscale")),
) -> GenerationJobAccepted:
    return await _enqueue_reference_job(
        feature_key="upscale",
        image=image,
        images=None,
        prompt=prompt,
        model=model,
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=None,
        source_image_names_json=None,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
    )


@router.post("/multi-view", response_model=GenerationJobAccepted)
async def enqueue_multi_view(
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
    batch_size: int = Form(default=1),
    current_user: User = Depends(require_module("multi_view")),
) -> GenerationJobAccepted:
    if batch_size not in {1, 2, 4}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="生成数量只能选择 1、2 或 4。")
    return await _enqueue_reference_job(
        feature_key="multi_view",
        image=image,
        images=images,
        prompt=prompt,
        model=model,
        source_image_url=source_image_url,
        source_image_name=source_image_name,
        source_image_urls_json=source_image_urls_json,
        source_image_names_json=source_image_names_json,
        negative_prompt=negative_prompt,
        strength=strength,
        image_size=image_size,
        current_user=current_user,
        batch_size=batch_size,
    )


@router.post("/split-multi-view", response_model=GenerationJobAccepted)
def enqueue_split_multi_view(
    payload: MultiViewSplitRequest,
    current_user: User = Depends(require_module("multi_view_split")),
) -> GenerationJobAccepted:
    return job_service.enqueue_job(
        current_user=current_user,
        feature_key="multi_view_split",
        model=payload.model,
        prompt="Split four-grid multi-view image into separate view assets.",
        request_payload={"payload": payload.model_dump(mode="json")},
    )


@router.post("/fuse-images", response_model=GenerationJobAccepted)
async def enqueue_fuse_images(
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
) -> GenerationJobAccepted:
    settings = get_settings()
    job_service.ensure_can_enqueue(current_user=current_user)

    source_urls = _parse_string_array_json(source_image_urls_json, field_name="source_image_urls_json")
    source_names = _parse_string_array_json(source_image_names_json, field_name="source_image_names_json")

    upload_files = images or []
    if upload_files:
        uploaded_urls, uploaded_names = await _store_uploaded_sources(files=upload_files, module_kind="fusion", current_user=current_user)
        source_urls = [*uploaded_urls, *source_urls]
        source_names = [*uploaded_names, *source_names]

    total_images = len(source_urls)
    if total_images < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least two images are required for fusion.")
    if total_images > settings.ai_max_fusion_images:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"At most {settings.ai_max_fusion_images} images are supported.")
    if primary_image_index < 0 or primary_image_index >= total_images:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="primary_image_index is out of range.")
    if source_names and len(source_names) != len(source_urls):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_image_urls_json and source_image_names_json must have the same length.")

    metadata = FusionRequestMetadata(
        model=model,
        image_count=total_images,
        prompt=prompt,
        negative_prompt=negative_prompt,
        mode=mode,
        primary_image_index=primary_image_index,
        strength=strength,
        filenames=source_names or [f"image-{index + 1}.png" for index in range(total_images)],
    )
    return job_service.enqueue_job(
        current_user=current_user,
        feature_key="multi_image_fusion",
        model=model,
        prompt=prompt,
        request_payload={
            "metadata": metadata.model_dump(mode="json"),
            "source_image_urls": source_urls,
        },
    )
