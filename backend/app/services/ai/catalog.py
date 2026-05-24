from dataclasses import dataclass

from app.schemas.ai import ProviderType


@dataclass(frozen=True)
class ImageModelConfig:
    id: str
    label: str
    provider: ProviderType
    category: str
    upstream_model_id: str
    supports_text_to_image: bool
    supports_multi_image_fusion: bool
    supports_reference_images: bool
    pricing_hint: str


MODEL_CATALOG: dict[str, ImageModelConfig] = {
    "gpt-image-2-all-apiyi": ImageModelConfig(
        id="gpt-image-2-all-apiyi",
        label="APIYI · GPT Image 2 VIP",
        provider=ProviderType.apiyi,
        category="image_generation",
        upstream_model_id="gpt-image-2-vip",
        supports_text_to_image=True,
        supports_multi_image_fusion=True,
        supports_reference_images=True,
        pricing_hint="GPT Image 2 VIP via APIYI image edits",
    ),
    "gemini-3-pro-image-preview-apiyi": ImageModelConfig(
        id="gemini-3-pro-image-preview-apiyi",
        label="APIYI · Nano Banana Pro",
        provider=ProviderType.gemini,
        category="image_generation",
        upstream_model_id="gemini-3-pro-image-preview",
        supports_text_to_image=True,
        supports_multi_image_fusion=True,
        supports_reference_images=True,
        pricing_hint="Nano Banana Pro via APIYI Gemini image edit",
    ),
    "gpt-image-2-closeai": ImageModelConfig(
        id="gpt-image-2-closeai",
        label="CloseAI · GPT Image 2",
        provider=ProviderType.closeai,
        category="image_generation",
        upstream_model_id="gpt-image-2",
        supports_text_to_image=True,
        supports_multi_image_fusion=True,
        supports_reference_images=True,
        pricing_hint="GPT Image 2 via CloseAI OpenAI-compatible image edits",
    ),
    "gemini-3.1-flash-image-preview": ImageModelConfig(
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
