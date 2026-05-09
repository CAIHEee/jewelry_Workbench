import asyncio
from io import BytesIO

from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.api.v1.routes.ai import cache_service
from app.schemas.ai import ReferenceImageRequestMetadata
from app.services.ai_service import AIService


def test_model_catalog_exposes_expected_models(client: TestClient) -> None:
    cache_service.delete(cache_service.model_catalog_key())
    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    body = response.json()
    models = {item["id"]: item for item in body["models"]}
    model_ids = set(models)
    assert model_ids == {
        "gpt-image-2-all-apiyi",
        "multi-view-few-shot-apiyi",
        "gemini-3.1-flash-image-preview",
    }
    assert "gpt-image-2-all-apiyi" in model_ids
    assert "multi-view-few-shot-apiyi" in model_ids
    assert "gpt-image-2-aiapis" not in model_ids
    assert "gpt-image-2-wuyin" not in model_ids
    assert "gpt-image-2-dmxapi" not in model_ids
    assert "gemini-3.1-flash-image-preview" in model_ids
    assert "gemini-3-pro-image-preview" not in model_ids
    assert "flux1-dev" not in model_ids
    assert "flux-kontext-pro" not in model_ids
    assert models["gpt-image-2-all-apiyi"]["supports_text_to_image"] is True
    assert models["gpt-image-2-all-apiyi"]["supports_multi_image_fusion"] is True
    assert models["gpt-image-2-all-apiyi"]["supports_reference_images"] is True
    assert models["gpt-image-2-all-apiyi"]["provider"] == "apiyi"
    assert models["gpt-image-2-all-apiyi"]["label"].startswith("APIYI")
    assert models["multi-view-few-shot-apiyi"]["supports_text_to_image"] is False
    assert models["multi-view-few-shot-apiyi"]["supports_multi_image_fusion"] is False
    assert models["multi-view-few-shot-apiyi"]["supports_reference_images"] is True
    assert models["multi-view-few-shot-apiyi"]["provider"] == "apiyi"
    assert models["multi-view-few-shot-apiyi"]["label"].startswith("APIYI")
    assert models["gemini-3.1-flash-image-preview"]["supports_text_to_image"] is True
    assert models["gemini-3.1-flash-image-preview"]["supports_multi_image_fusion"] is True
    assert models["gemini-3.1-flash-image-preview"]["supports_reference_images"] is True
    assert models["gemini-3.1-flash-image-preview"]["label"].startswith("APIYI")


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


def test_extracts_apiyi_chat_completion_data_b64_json() -> None:
    service = AIService()
    data = {"data": [{"b64_json": "ZmFrZS1pbWFnZQ=="}]}

    assert service._extract_chat_completion_image_url(data) == "data:image/png;base64,ZmFrZS1pbWFnZQ=="


def test_builds_apiyi_multi_view_context_prompt() -> None:
    service = AIService()
    metadata = ReferenceImageRequestMetadata(
        model="gpt-image-2-all-apiyi",
        prompt="生成标准四视图",
        feature="multi_view",
        filename="style.png",
        image_count=3,
    )

    prompt = service._build_apiyi_reference_prompt(metadata)

    assert "前面的图片都是 few-shot 输出示例" in prompt
    assert "最后一张图片是必须生成四视图的原图主体" in prompt
    assert "不要复制前面示例图的款式" in prompt
    assert "生成标准四视图" in prompt


def test_builds_apiyi_few_shot_multi_view_prompt() -> None:
    service = AIService()
    metadata = ReferenceImageRequestMetadata(
        model="gpt-image-2-all-apiyi",
        prompt="默认多视图规则",
        feature="multi_view",
        filename="source.png",
        image_count=6,
    )

    prompt = service._build_apiyi_reference_prompt(metadata)
    system_prompt = service._build_apiyi_reference_system_prompt(metadata)

    assert "few-shot 输出示例图" in prompt
    assert "图6：用户上传的唯一主体原图" in prompt
    assert "默认多视图规则" not in prompt
    assert system_prompt is not None
    assert "最后一张才是用户上传的唯一主体原图" in system_prompt


def test_apiyi_chat_payload_marks_reference_and_source_images(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(service, "_require_apiyi_api_key", lambda: "test-key")

    captured: dict[str, object] = {}

    async def fake_post_json_with_bearer(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"data": [{"url": "https://example.com/result.png"}]}

    monkeypatch.setattr(service, "_post_json_with_bearer", fake_post_json_with_bearer)

    async def run_request() -> None:
        files = [
            UploadFile(filename="example-a.png", file=BytesIO(b"example-a")),
            UploadFile(filename="example-b.png", file=BytesIO(b"example-b")),
            UploadFile(filename="source.png", file=BytesIO(b"source")),
        ]
        await service._post_apiyi_gpt_image2_chat(
            model=service._get_model_or_404("gpt-image-2-all-apiyi"),
            prompt="生成多视图",
            files=files,
            system_prompt="只使用用户原图",
        )

        for item in files:
            item.file.close()

    asyncio.run(run_request())

    payload = captured["payload"]
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "只使用用户原图"
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]
    assert content[0]["text"] == "生成多视图"
    assert content[1]["text"] == "few-shot 输出示例图 1：example-a.png"
    assert content[3]["text"] == "few-shot 输出示例图 2：example-b.png"
    assert content[5]["text"] == "用户上传的唯一主体原图：source.png"
    assert content[6]["image_url"]["url"].startswith("data:image/")


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
