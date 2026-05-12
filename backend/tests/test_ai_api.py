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
    assert "gpt-image-2-wuyin" not in model_ids
    assert "gemini-3.1-flash-image-preview" in model_ids
    assert "gemini-3-pro-image-preview" not in model_ids
    assert "flux1-dev" not in model_ids
    assert "flux-kontext-pro" not in model_ids
    assert models["gpt-image-2-all-apiyi"]["supports_text_to_image"] is False
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


def test_extracts_openai_payload_data_url_b64_json() -> None:
    service = AIService()
    data = {"data": [{"b64_json": "data:image/webp;base64,ZmFrZS1pbWFnZQ=="}]}

    image_bytes, content_type, image_url = service._extract_openai_image_payload(data)

    assert image_bytes == b"fake-image"
    assert content_type == "image/webp"
    assert image_url is None


def test_apiyi_reference_prompt_uses_qwen_output_without_old_chain() -> None:
    service = AIService()
    metadata = ReferenceImageRequestMetadata(
        model="gpt-image-2-all-apiyi",
        prompt="Qwen 反推后的多视图提示词",
        feature="multi_view",
        filename="style.png",
        image_count=3,
    )

    prompt = service._build_apiyi_reference_prompt(metadata)

    assert prompt == "Qwen 反推后的多视图提示词"


def test_builds_qwen_multi_view_prompt_request_text() -> None:
    service = AIService()

    prompt = service._build_qwen_multi_view_prompt_request_text("保留翡翠绿色")

    assert "请反推当前珠宝图片的生成提示词" in prompt
    assert "只输出中文纯文本" in prompt
    assert "不要 Markdown，不要换行符" in prompt
    assert "参考 gpt image 2 的提示词编写规范" in prompt
    assert "必须使用中文标点断句" in prompt
    assert "不能输出没有标点的一整段长句" in prompt
    assert "正视（需与原图一致）" in prompt
    assert "左侧视（45度）" in prompt
    assert "右侧视（45度）" in prompt
    assert "背视" in prompt
    assert "任务：请反推当前珠宝图片的生成提示词" in prompt
    assert "生成基于参考图的4个标准视角" in prompt
    assert "正面垂直视角，绝对与参照图一致保持一成不变" in prompt
    assert "不得删减模板要求" in prompt
    assert "不得缩写成摘要" in prompt
    assert "胸针" not in prompt
    assert "祖母绿绿色" not in prompt
    assert prompt.index("任务：请反推当前珠宝图片的生成提示词") < prompt.index("用户补充提示词：保留翡翠绿色")
    assert prompt.endswith("现在请基于当前图片和用户补充提示词，直接输出最终生图提示词。")
    assert "用户补充提示词：保留翡翠绿色" in prompt


def test_qwen_multi_view_prompt_payload_uses_image_and_thinking(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(service.settings, "agent_llm_api_key", "test-qwen-key")
    monkeypatch.setattr(service.settings, "multi_view_prompt_model", "qwen3-vl-plus")
    monkeypatch.setattr(service.settings, "multi_view_prompt_thinking_budget", 81920)

    captured: dict[str, object] = {}

    async def fake_post_dashscope_qwen_chat(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "  生成四视图\n保留主体  "}}]}

    monkeypatch.setattr(service, "_post_dashscope_qwen_chat", fake_post_dashscope_qwen_chat)

    async def run_request() -> str:
        upload = UploadFile(filename="source.png", file=BytesIO(b"source"), headers={"content-type": "image/png"})
        try:
            return await service._build_qwen_multi_view_prompt(
                input_file=upload,
                metadata=ReferenceImageRequestMetadata(
                    model="gpt-image-2-all-apiyi",
                    prompt="增加侧面厚度",
                    feature="multi_view",
                    filename="source.png",
                    image_count=1,
                ),
            )
        finally:
            upload.file.close()

    prompt = asyncio.run(run_request())

    assert prompt == "生成四视图 保留主体"
    assert captured["api_key"] == "test-qwen-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen3-vl-plus"
    assert payload["stream"] is True
    assert payload["enable_thinking"] is True
    assert payload["thinking_budget"] == 81920
    content = payload["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "用户补充提示词：增加侧面厚度" in content[1]["text"]


def test_apiyi_vip_edit_payload_uses_multipart_images(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(service, "_require_apiyi_api_key", lambda: "test-key")

    captured: dict[str, object] = {}

    async def fake_post_multipart_with_bearer_base_url(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"data": [{"url": "https://example.com/result.png"}]}

    monkeypatch.setattr(service, "_post_multipart_with_bearer_base_url", fake_post_multipart_with_bearer_base_url)

    async def run_request() -> None:
        files = [
            UploadFile(filename="example-a.png", file=BytesIO(b"example-a")),
            UploadFile(filename="example-b.png", file=BytesIO(b"example-b")),
            UploadFile(filename="source.png", file=BytesIO(b"source")),
        ]
        await service._post_apiyi_gpt_image2_vip_edit(
            model=service._get_model_or_404("gpt-image-2-all-apiyi"),
            prompt="Qwen 生成的新多视图提示词",
            files=files,
        )

        for item in files:
            item.file.close()

    asyncio.run(run_request())

    assert captured["base_url"] == service.settings.apiyi_openai_base_url
    assert captured["path"] == "/images/edits"
    data = captured["data"]
    assert isinstance(data, dict)
    assert data == {
        "model": "gpt-image-2-vip",
        "prompt": "Qwen 生成的新多视图提示词",
        "size": "auto",
        "response_format": "url",
    }
    files = captured["files"]
    assert isinstance(files, list)
    assert [item[0] for item in files] == ["image", "image", "image"]
    assert files[0][1][0] == "example-a.png"
    assert files[1][1][0] == "example-b.png"
    assert files[2][1][0] == "source.png"


def test_apiyi_vip_edit_size_mapping() -> None:
    service = AIService()

    assert service._map_apiyi_vip_edit_size("1K") == "auto"
    assert service._map_apiyi_vip_edit_size("2K") == "2048x2048"
    assert service._map_apiyi_vip_edit_size("4K") == "2880x2880"


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
