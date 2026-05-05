from fastapi.testclient import TestClient

from app.api.v1.routes.ai import cache_service
from app.services.ai_service import AIService


def test_model_catalog_exposes_expected_models(client: TestClient) -> None:
    cache_service.delete(cache_service.model_catalog_key())
    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    body = response.json()
    models = {item["id"]: item for item in body["models"]}
    model_ids = set(models)
    assert "gpt-image-2-all-apiyi" in model_ids
    assert "gpt-image-2-aiapis" in model_ids
    assert "gpt-image-2-wuyin" not in model_ids
    assert "gpt-image-2-dmxapi" in model_ids
    assert "gemini-3.1-flash-image-preview" in model_ids
    assert "gemini-3-pro-image-preview" not in model_ids
    assert "flux1-dev" not in model_ids
    assert "flux-kontext-pro" not in model_ids
    assert models["gpt-image-2-all-apiyi"]["supports_text_to_image"] is True
    assert models["gpt-image-2-all-apiyi"]["supports_multi_image_fusion"] is True
    assert models["gpt-image-2-all-apiyi"]["supports_reference_images"] is True
    assert models["gpt-image-2-all-apiyi"]["provider"] == "apiyi"
    assert models["gpt-image-2-all-apiyi"]["label"].startswith("APIYI")
    assert models["gpt-image-2-aiapis"]["supports_text_to_image"] is True
    assert models["gpt-image-2-aiapis"]["supports_multi_image_fusion"] is True
    assert models["gpt-image-2-aiapis"]["supports_reference_images"] is True
    assert models["gpt-image-2-aiapis"]["provider"] == "aiapis"
    assert models["gpt-image-2-aiapis"]["label"].startswith("AIAPIS")
    assert models["gpt-image-2-dmxapi"]["supports_text_to_image"] is False
    assert models["gpt-image-2-dmxapi"]["supports_multi_image_fusion"] is True
    assert models["gpt-image-2-dmxapi"]["supports_reference_images"] is True
    assert models["gpt-image-2-dmxapi"]["provider"] == "dmxapi"
    assert models["gpt-image-2-dmxapi"]["label"].startswith("DMXAPI")
    assert models["gemini-3.1-flash-image-preview"]["supports_text_to_image"] is True
    assert models["gemini-3.1-flash-image-preview"]["supports_multi_image_fusion"] is True
    assert models["gemini-3.1-flash-image-preview"]["supports_reference_images"] is True


def test_extracts_apiyi_chat_completion_image_url() -> None:
    service = AIService()
    data = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "message": {
                    "content": "生成完成：![result](https://cdn.example.com/generated.png)",
                }
            }
        ],
    }

    assert service._extract_chat_completion_image_url(data) == "https://cdn.example.com/generated.png"


def test_text_to_image_rejects_unknown_model(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/ai/text-to-image",
        json={
            "prompt": "test",
            "model": "does-not-exist",
            "aspect_ratio": "1:1",
            "size": "1024x1024",
            "image_size": "1K",
            "thinking_level": "Minimal",
        },
    )

    assert response.status_code == 404


def test_fusion_requires_two_images(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/ai/fuse-images",
        data={
            "prompt": "blend these images",
            "model": "gemini-3.1-flash-image-preview",
            "mode": "balanced",
            "primary_image_index": "0",
            "strength": "0.75",
        },
        files=[("images", ("only-one.png", b"fake-image", "image/png"))],
    )

    assert response.status_code == 400


def test_reference_transform_rejects_unknown_model(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/ai/reference-image-transform",
        data={
            "prompt": "turn this into grayscale relief",
            "model": "does-not-exist",
            "feature": "grayscale_relief",
            "strength": "0.8",
        },
        files={"image": ("reference.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 404
