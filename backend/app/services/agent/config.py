import json
from pathlib import Path
from typing import Any


DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_IMAGE_REGENERATE_MODEL = "gpt-image-2-all-apiyi"
DEFAULT_MULTI_VIEW_MODEL = "gpt-image-2-all-apiyi"
DEFAULT_GRAYSCALE_RELIEF_MODEL = "gpt-image-2-all-apiyi"
DEFAULT_GEMSTONE_DESIGN_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_SKETCH_TO_REALISTIC_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX = "高级珠宝产品渲染效果，背景干净，金属光泽真实，工艺细节清晰。"
DESIGN_FRONT_VIEW_CONSTRAINT = (
    "必须生成完整的珠宝设计正视图，主体珠宝完整入画、居中展示，不裁切、不只展示局部特写，"
    "正面视角或近似正交正视图，完整呈现整体轮廓、结构比例、主石位置、镶嵌结构和全部关键设计细节。"
)


def _load_shared_prompt_templates() -> dict[str, dict[str, str]]:
    template_path = Path(__file__).resolve().parents[4] / "shared" / "prompt_templates.json"
    raw_items = json.loads(template_path.read_text(encoding="utf-8"))
    return {str(item["id"]): item for item in raw_items}


_PROMPT_TEMPLATES = _load_shared_prompt_templates()


def _shared_prompt(template_id: str) -> str:
    template = _PROMPT_TEMPLATES.get(template_id)
    if not template:
        raise KeyError(f"Missing shared prompt template: {template_id}")
    return str(template["content"])


DEFAULT_GEMSTONE_DESIGN_PROMPT = _shared_prompt("gemstone-design-cabochon")
SKETCH_TO_REALISTIC_PROMPT = _shared_prompt("sketch-to-realistic-default")
MULTI_VIEW_PROMPT = _shared_prompt("multi-view-jewelry-grid")
GRAYSCALE_PROMPT = _shared_prompt("grayscale-relief-clay")
PRODUCT_REFINE_DEFAULT_PROMPT = _shared_prompt("product-refine-jewelry-shot")
PRODUCT_REFINE_REMOVE_SELECTED_PROMPT = _shared_prompt("product-refine-remove-yellow-markup")


MODULE_RULES: dict[str, dict[str, Any]] = {
    "text_to_image": {"kind": "text_to_image", "title": "设计出图", "editable_prompt": True, "min_images": 0},
    "gemstone_design": {
        "kind": "image_to_image",
        "title": "裸石镶嵌设计",
        "editable_prompt": True,
        "min_images": 1,
        "default_prompt": DEFAULT_GEMSTONE_DESIGN_PROMPT,
    },
    "sketch_to_realistic": {
        "kind": "image_to_image",
        "title": "线稿转写实图",
        "editable_prompt": False,
        "min_images": 1,
        "default_prompt": SKETCH_TO_REALISTIC_PROMPT,
    },
    "product_refine": {
        "kind": "image_to_image",
        "title": "产品精修",
        "editable_prompt": True,
        "min_images": 1,
        "default_prompt": PRODUCT_REFINE_DEFAULT_PROMPT,
    },
    "multi_view": {
        "kind": "image_to_image",
        "title": "生成多视图",
        "editable_prompt": False,
        "min_images": 1,
        "default_prompt": MULTI_VIEW_PROMPT,
    },
    "grayscale_relief": {
        "kind": "image_to_image",
        "title": "转灰度图",
        "editable_prompt": False,
        "min_images": 1,
        "default_prompt": GRAYSCALE_PROMPT,
    },
    "multi_view_split": {"kind": "split_multi_view", "title": "多视图切图", "editable_prompt": False, "min_images": 1},
}
