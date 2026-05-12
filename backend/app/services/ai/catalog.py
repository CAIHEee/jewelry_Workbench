from dataclasses import dataclass

from app.schemas.ai import ProviderType


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
        label="APIYI · GPT Image 2 VIP",
        provider=ProviderType.apiyi,
        category="image_generation",
        upstream_model_id="gpt-image-2-vip",
        supports_text_to_image=False,
        supports_multi_image_fusion=True,
        supports_reference_images=True,
        pricing_hint="GPT Image 2 VIP via APIYI image edits",
    ),
    "multi-view-few-shot-apiyi": TTAPIModelConfig(
        id="multi-view-few-shot-apiyi",
        label="APIYI · Multi-View Few-Shot",
        provider=ProviderType.apiyi,
        category="image_generation",
        upstream_model_id="gpt-image-2",
        supports_text_to_image=False,
        supports_multi_image_fusion=False,
        supports_reference_images=True,
        pricing_hint="Multi-view generation with fixed few-shot context via APIYI images/edits",
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
