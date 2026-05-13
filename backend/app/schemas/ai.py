from enum import Enum

from pydantic import BaseModel, Field


class FusionMode(str, Enum):
    balanced = "balanced"
    style_first = "style_first"
    structure_first = "structure_first"
    detail_enhanced = "detail_enhanced"


class ProviderType(str, Enum):
    flux = "flux"
    gemini = "gemini"
    apiyi = "apiyi"
    closeai = "closeai"
    wuyin = "wuyin"


class FeatureDefinition(BaseModel):
    key: str
    title: str
    description: str


class TTAPIModelDefinition(BaseModel):
    id: str
    label: str
    provider: ProviderType
    category: str
    supports_text_to_image: bool = True
    supports_multi_image_fusion: bool = False
    supports_reference_images: bool = False
    pricing_hint: str


class GenerationFeatureCatalog(BaseModel):
    features: list[FeatureDefinition]


class ModelCatalogResponse(BaseModel):
    models: list[TTAPIModelDefinition]


class TextToImageRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str
    aspect_ratio: str = "1:1"
    size: str = "1024x1024"
    image_size: str = "1K"
    thinking_level: str = "High"


class InputImageSource(BaseModel):
    filename: str
    source_image_url: str | None = None
    storage_url: str | None = None
    preview_url: str | None = None


class ReferenceImageRequestMetadata(BaseModel):
    model: str
    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    feature: str = "image_edit"
    strength: float = Field(default=0.75, ge=0.0, le=1.0)
    image_size: str = "1K"
    image_count: int = Field(default=1, ge=1)
    filename: str
    filenames: list[str] = Field(default_factory=list)
    source_image_url: str | None = None
    source_image_storage_url: str | None = None
    source_images: list[InputImageSource] = Field(default_factory=list)


class FusionRequestMetadata(BaseModel):
    model: str
    image_count: int = Field(ge=2)
    prompt: str
    negative_prompt: str | None = None
    mode: FusionMode
    primary_image_index: int = Field(ge=0)
    strength: float = Field(ge=0.0, le=1.0)
    filenames: list[str]
    source_images: list[InputImageSource] = Field(default_factory=list)


class GenerationResult(BaseModel):
    job_id: str | None = None
    status: str
    provider: ProviderType
    model: str
    image_url: str | None = None
    source_image_url: str | None = None
    source_image_storage_url: str | None = None
    revised_prompt: str | None = None
    message: str
    raw_response: dict[str, object] | None = None


class MultiViewSplitRequest(BaseModel):
    image_url: str = Field(min_length=1)
    source_image_name: str | None = None
    model: str = Field(default="multi_view_split")
    split_x_ratio: float = Field(default=0.5, gt=0.05, lt=0.95)
    split_y_ratio: float = Field(default=0.5, gt=0.05, lt=0.95)
    gap_x_ratio: float = Field(default=0.0, ge=0.0, lt=0.3)
    gap_y_ratio: float = Field(default=0.0, ge=0.0, lt=0.3)


class MultiViewSplitItem(BaseModel):
    view: str
    image_url: str | None = None
    storage_url: str | None = None
    width: int
    height: int


class MultiViewSplitResponse(BaseModel):
    status: str
    message: str
    source_image_url: str
    split_x_ratio: float
    split_y_ratio: float
    gap_x_ratio: float
    gap_y_ratio: float
    items: list[MultiViewSplitItem]


class FusionJobAccepted(BaseModel):
    job_id: str | None = None
    status: str
    message: str
    provider: ProviderType
    feature: str
    model: str
    image_url: str | None = None
    metadata: FusionRequestMetadata
    raw_response: dict[str, object] | None = None
