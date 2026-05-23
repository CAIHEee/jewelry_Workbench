import asyncio
from io import BytesIO

from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.api.v1.routes.ai import cache_service
from app.services import config_service as config_module
from app.schemas.ai import GenerationResult, ReferenceImageRequestMetadata, TextToImageRequest
from app.services.ai_service import AIService
from app.models.user import User


def test_model_catalog_exposes_expected_models(client: TestClient) -> None:
    cache_service.delete(cache_service.model_catalog_key())
    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    body = response.json()
    models = {item["id"]: item for item in body["models"]}
    model_ids = set(models)
    assert "gpt-image-2-all-apiyi" in model_ids
    assert "gemini-3-pro-image-preview-apiyi" in model_ids
    assert "gemini-3.1-flash-image-preview" in model_ids
    assert models["gpt-image-2-all-apiyi"]["supports_text_to_image"] is False
    assert models["gpt-image-2-all-apiyi"]["supports_multi_image_fusion"] is True
    assert models["gpt-image-2-all-apiyi"]["supports_reference_images"] is True
    assert models["gpt-image-2-all-apiyi"]["provider"] == "apiyi"
    assert models["gpt-image-2-all-apiyi"]["label"].startswith("APIYI")
    assert models["gemini-3-pro-image-preview-apiyi"]["supports_text_to_image"] is True
    assert models["gemini-3-pro-image-preview-apiyi"]["supports_multi_image_fusion"] is True
    assert models["gemini-3-pro-image-preview-apiyi"]["supports_reference_images"] is True
    assert models["gemini-3-pro-image-preview-apiyi"]["provider"] == "gemini"
    assert models["gemini-3-pro-image-preview-apiyi"]["label"].startswith("APIYI")
    if "gpt-image-2-closeai" in models:
        assert models["gpt-image-2-closeai"]["supports_text_to_image"] is False
        assert models["gpt-image-2-closeai"]["supports_multi_image_fusion"] is True
        assert models["gpt-image-2-closeai"]["supports_reference_images"] is True
        assert models["gpt-image-2-closeai"]["provider"] == "closeai"
        assert models["gpt-image-2-closeai"]["label"].startswith("CloseAI")
    assert models["gemini-3.1-flash-image-preview"]["supports_text_to_image"] is True
    assert models["gemini-3.1-flash-image-preview"]["supports_multi_image_fusion"] is True
    assert models["gemini-3.1-flash-image-preview"]["supports_reference_images"] is True
    assert models["gemini-3.1-flash-image-preview"]["label"].startswith("APIYI")


def test_model_catalog_includes_active_custom_image_models(client: TestClient, monkeypatch) -> None:
    cache_service.delete(cache_service.model_catalog_key())
    monkeypatch.setattr(
        config_module,
        "_get_custom_groups",
        lambda: [
            {
                "group_key": "custom_image",
                "label": "自定义图像",
                "category": "image",
                "is_builtin": False,
                "is_active": True,
                "interface_type": "openai_compat",
                "items": [],
            },
            {
                "group_key": "custom_agent",
                "label": "自定义 Agent",
                "category": "agent",
                "is_builtin": False,
                "is_active": True,
                "interface_type": "openai_compat",
                "items": [],
            },
        ],
    )
    monkeypatch.setattr(
        config_module,
        "_parse_env_file",
        lambda: {
            "CUSTOM_GROUP_CUSTOM_IMAGE_MODELS": "alpha:Alpha Model,beta:Beta Model",
            "CUSTOM_GROUP_CUSTOM_AGENT_MODELS": "agent-x:Agent X",
        },
    )

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    body = response.json()
    models = {item["id"]: item for item in body["models"]}
    assert {"alpha", "beta"}.issubset(models)
    assert "agent-x" not in models
    assert models["alpha"]["label"] == "自定义图像 · Alpha Model"
    assert models["alpha"]["provider"] == "apiyi"
    assert models["alpha"]["category"] == "image_generation"


def test_custom_model_lookup_only_allows_active_image_groups(monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "_get_custom_groups",
        lambda: [
            {
                "group_key": "custom_image",
                "label": "自定义图像",
                "category": "image",
                "is_builtin": False,
                "is_active": True,
                "interface_type": "openai_compat",
                "items": [],
            },
            {
                "group_key": "disabled_image",
                "label": "停用图像",
                "category": "image",
                "is_builtin": False,
                "is_active": False,
                "interface_type": "openai_compat",
                "items": [],
            },
            {
                "group_key": "custom_agent",
                "label": "自定义 Agent",
                "category": "agent",
                "is_builtin": False,
                "is_active": True,
                "interface_type": "openai_compat",
                "items": [],
            },
        ],
    )
    monkeypatch.setattr(
        config_module,
        "_parse_env_file",
        lambda: {
            "CUSTOM_GROUP_CUSTOM_IMAGE_MODELS": "alpha:Alpha Model",
            "CUSTOM_GROUP_CUSTOM_IMAGE_BASE_URL": "https://image.example/v1",
            "CUSTOM_GROUP_CUSTOM_IMAGE_API_KEY": "image-key",
            "CUSTOM_GROUP_DISABLED_IMAGE_MODELS": "disabled:Disabled Model",
            "CUSTOM_GROUP_DISABLED_IMAGE_BASE_URL": "https://disabled.example/v1",
            "CUSTOM_GROUP_DISABLED_IMAGE_API_KEY": "disabled-key",
            "CUSTOM_GROUP_CUSTOM_AGENT_MODELS": "agent-x:Agent X",
            "CUSTOM_GROUP_CUSTOM_AGENT_BASE_URL": "https://agent.example/v1",
            "CUSTOM_GROUP_CUSTOM_AGENT_API_KEY": "agent-key",
        },
    )

    service = AIService()

    model = service._get_model_or_404("alpha")
    assert model.category == "image_generation"
    assert model.upstream_model_id == "custom_image|https://image.example/v1|image-key"

    for blocked_model in ("disabled", "agent-x"):
        try:
            service._get_model_or_404(blocked_model)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError(f"Expected {blocked_model} to be unavailable.")


def test_admin_config_raw_endpoint_returns_secret_values(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/admin/config/keys/apiyi/raw")

    assert response.status_code == 200
    body = response.json()
    assert body["group_key"] == "apiyi"
    assert "APIYI_API_KEY" in body["items"]


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
    assert "必须使用中文标点断句" in prompt
    assert "不能输出没有标点的一整段长句" in prompt
    assert "正视图必须与原图完全一致" in prompt
    assert "左侧视和右侧视必须基于参考图清楚表达" in prompt
    assert "侧面结构" in prompt
    assert "背视" in prompt
    assert "任务：请反推当前珠宝图片的生成提示词" in prompt
    assert "生成基于参考图的4个标准视角" in prompt
    assert "正面垂直视角，绝对与参照图一致保持一成不变" in prompt
    assert "不得删减模板要求" in prompt
    assert "不得缩写成摘要" in prompt
    assert "胸针" not in prompt
    assert "祖母绿绿色" not in prompt
    assert prompt.index("任务：请反推当前珠宝图片的生成提示词") < prompt.index("用户补充提示词：保留翡翠绿色")
    assert prompt.endswith("如果用户提示词存在，则它的权重最高。")
    assert "用户补充提示词：保留翡翠绿色" in prompt


def test_qwen_multi_view_prompt_payload_uses_image_and_thinking(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(service.settings, "dashscope_api_key", "")
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


def test_custom_image_model_edit_payload_uses_custom_provider_config(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(
        config_module,
        "_get_custom_groups",
        lambda: [
            {
                "group_key": "custom_image",
                "label": "自定义图像",
                "category": "image",
                "is_builtin": False,
                "is_active": True,
                "interface_type": "openai_compat",
                "items": [],
            },
        ],
    )
    monkeypatch.setattr(
        config_module,
        "_parse_env_file",
        lambda: {
            "CUSTOM_GROUP_CUSTOM_IMAGE_MODELS": "alpha:Alpha Model",
            "CUSTOM_GROUP_CUSTOM_IMAGE_BASE_URL": "https://custom.example/v1",
            "CUSTOM_GROUP_CUSTOM_IMAGE_API_KEY": "custom-key",
        },
    )

    captured: dict[str, object] = {}

    async def fake_post_multipart_with_bearer_base_url(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"data": [{"url": "https://example.com/result.png"}]}

    monkeypatch.setattr(service, "_post_multipart_with_bearer_base_url", fake_post_multipart_with_bearer_base_url)

    async def run_request() -> None:
        files = [UploadFile(filename="source.png", file=BytesIO(b"source"))]
        try:
            await service._post_apiyi_gpt_image2_vip_edit(
                model=service._get_model_or_404("alpha"),
                prompt="Qwen 反推后的自定义多视图提示词",
                files=files,
                size="auto",
            )
        finally:
            for item in files:
                item.file.close()

    asyncio.run(run_request())

    assert captured["base_url"] == "https://custom.example/v1"
    assert captured["path"] == "/images/edits"
    assert captured["api_key"] == "custom-key"
    data = captured["data"]
    assert isinstance(data, dict)
    assert data["model"] == "alpha"
    assert data["prompt"] == "Qwen 反推后的自定义多视图提示词"


def test_closeai_gpt_image2_edit_payload_uses_closeai_config(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(service, "_require_closeai_api_key", lambda: "test-closeai-key")
    monkeypatch.setattr(service.settings, "closeai_base_url", "https://api.openai-proxy.org/v1")

    captured: dict[str, object] = {}

    async def fake_post_multipart_with_bearer_base_url(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"data": [{"url": "https://example.com/result.png"}]}

    monkeypatch.setattr(service, "_post_multipart_with_bearer_base_url", fake_post_multipart_with_bearer_base_url)

    async def run_request() -> None:
        files = [UploadFile(filename="source.png", file=BytesIO(b"source"))]
        try:
            await service._post_closeai_gpt_image2_edit(
                model=service._get_model_or_404("gpt-image-2-closeai"),
                prompt="转写实",
                files=files,
                size="2048x2048",
            )
        finally:
            for item in files:
                item.file.close()

    asyncio.run(run_request())

    assert captured["base_url"] == "https://api.openai-proxy.org/v1"
    assert captured["path"] == "/images/edits"
    data = captured["data"]
    assert isinstance(data, dict)
    assert data == {
        "model": "gpt-image-2",
        "prompt": "转写实",
        "size": "2048x2048",
    }
    assert "response_format" not in data
    files = captured["files"]
    assert isinstance(files, list)
    assert [item[0] for item in files] == ["image[]"]
    assert captured["api_key"] == "test-closeai-key"


def test_closeai_multi_view_uses_qwen_prompt_stages(monkeypatch) -> None:
    service = AIService()
    stages: list[str] = []
    captured: dict[str, object] = {}

    async def fake_generate_multi_view_with_qwen_prompt(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        callback = kwargs["stage_callback"]
        callback("qwen_prompt")
        callback("image_generation")
        return GenerationResult(
            status="completed",
            provider=service._get_model_or_404("gpt-image-2-closeai").provider,
            model="gpt-image-2-closeai",
            image_url="/api/v1/assets/content?storage_url=local://closeai-result.png",
            revised_prompt="Qwen 反推后的 CloseAI 多视图提示词",
            message="Reference image transform completed.",
        )

    monkeypatch.setattr(service, "_generate_multi_view_with_qwen_prompt", fake_generate_multi_view_with_qwen_prompt)

    async def run_request() -> None:
        upload = UploadFile(filename="source.png", file=BytesIO(b"source"), headers={"content-type": "image/png"})
        try:
            await service.generate_multi_view(
                file=upload,
                metadata=ReferenceImageRequestMetadata(
                    model="gpt-image-2-closeai",
                    prompt="默认多视图规则",
                    feature="multi_view",
                    filename="source.png",
                    image_count=1,
                ),
                current_user=User(id="00000000-0000-0000-0000-000000000001", username="root", display_name="root"),
                stage_callback=stages.append,
            )
        finally:
            upload.file.close()

    asyncio.run(run_request())

    assert stages == ["qwen_prompt", "image_generation"]
    metadata = captured["metadata"]
    assert isinstance(metadata, ReferenceImageRequestMetadata)
    assert metadata.model == "gpt-image-2-closeai"


def test_apiyi_gemini_multi_view_uses_qwen_prompt_stages(monkeypatch) -> None:
    service = AIService()
    stages: list[str] = []
    captured: dict[str, object] = {}

    async def fake_generate_multi_view_with_qwen_prompt(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        callback = kwargs["stage_callback"]
        callback("qwen_prompt")
        callback("image_generation")
        return GenerationResult(
            status="completed",
            provider=service._get_model_or_404("gemini-3-pro-image-preview-apiyi").provider,
            model="gemini-3-pro-image-preview-apiyi",
            image_url="/api/v1/assets/content?storage_url=local://nano-banana-pro-result.png",
            revised_prompt="Qwen 反推后的 Nano Banana Pro 多视图提示词",
            message="Reference image transform completed.",
        )

    monkeypatch.setattr(service, "_generate_multi_view_with_qwen_prompt", fake_generate_multi_view_with_qwen_prompt)

    async def run_request() -> None:
        upload = UploadFile(filename="source.png", file=BytesIO(b"source"), headers={"content-type": "image/png"})
        try:
            await service.generate_multi_view(
                file=upload,
                metadata=ReferenceImageRequestMetadata(
                    model="gemini-3-pro-image-preview-apiyi",
                    prompt="默认多视图规则",
                    feature="multi_view",
                    filename="source.png",
                    image_count=1,
                ),
                current_user=User(id="00000000-0000-0000-0000-000000000001", username="root", display_name="root"),
                stage_callback=stages.append,
            )
        finally:
            upload.file.close()

    asyncio.run(run_request())

    assert stages == ["qwen_prompt", "image_generation"]
    metadata = captured["metadata"]
    assert isinstance(metadata, ReferenceImageRequestMetadata)
    assert metadata.model == "gemini-3-pro-image-preview-apiyi"


def test_multi_view_qwen_prompt_dispatches_to_apiyi_gemini(monkeypatch) -> None:
    service = AIService()
    current_user = User(id="00000000-0000-0000-0000-000000000001", username="root", display_name="root")
    captured: dict[str, object] = {}

    async def fake_build_qwen_multi_view_prompt(**kwargs):  # noqa: ANN003
        captured["prompt_metadata"] = kwargs["metadata"]
        return "Qwen 反推后的 Nano Banana Pro 多视图提示词"

    async def fake_transform_with_gemini_apiyi(**kwargs):  # noqa: ANN003
        captured["transform_kwargs"] = kwargs
        return GenerationResult(
            status="completed",
            provider=service._get_model_or_404("gemini-3-pro-image-preview-apiyi").provider,
            model="gemini-3-pro-image-preview-apiyi",
            image_url="/api/v1/assets/content?storage_url=local://nano-banana-pro-result.png",
            revised_prompt=kwargs["metadata"].prompt,
            message="Reference image transform completed.",
        )

    monkeypatch.setattr(service, "_build_qwen_multi_view_prompt", fake_build_qwen_multi_view_prompt)
    monkeypatch.setattr(service, "_transform_with_gemini_apiyi", fake_transform_with_gemini_apiyi)

    async def run_request() -> GenerationResult:
        upload = UploadFile(filename="source.png", file=BytesIO(b"source"), headers={"content-type": "image/png"})
        try:
            return await service._generate_multi_view_with_qwen_prompt(
                file=upload,
                metadata=ReferenceImageRequestMetadata(
                    model="gemini-3-pro-image-preview-apiyi",
                    prompt="默认多视图规则",
                    feature="multi_view",
                    filename="source.png",
                    image_count=1,
                ),
                current_user=current_user,
            )
        finally:
            upload.file.close()

    result = asyncio.run(run_request())

    assert result.model == "gemini-3-pro-image-preview-apiyi"
    transform_kwargs = captured["transform_kwargs"]
    assert transform_kwargs["model"].id == "gemini-3-pro-image-preview-apiyi"
    assert transform_kwargs["metadata"].prompt == "Qwen 反推后的 Nano Banana Pro 多视图提示词"
    assert transform_kwargs["metadata"].image_count == 1


def test_apiyi_gemini_uses_upstream_model_id(monkeypatch) -> None:
    service = AIService()
    monkeypatch.setattr(service, "_require_apiyi_api_key", lambda: "test-apiyi-key")
    monkeypatch.setattr(service, "_extract_inline_image", lambda data: (b"image-bytes", "image/png"))

    captured: dict[str, object] = {}

    async def fake_post_json_with_bearer(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"candidates": [{"content": {"parts": []}}]}

    async def fake_store_generated_binary_asset(**kwargs):  # noqa: ANN003
        return {
            "access_url": "/api/v1/assets/content?storage_url=local://generated.png",
            "storage_url": "local://generated.png",
            "metadata": {},
        }

    monkeypatch.setattr(service, "_post_json_with_bearer", fake_post_json_with_bearer)
    monkeypatch.setattr(service, "_store_generated_binary_asset", fake_store_generated_binary_asset)
    monkeypatch.setattr(service, "_persist_history", lambda **kwargs: None)

    async def run_request() -> None:
        await service._generate_with_gemini_apiyi(
            request=TextToImageRequest(
                prompt="珠宝图",
                model="gemini-3-pro-image-preview-apiyi",
                aspect_ratio="1:1",
                size="1024x1024",
                image_size="1K",
                thinking_level="High",
            ),
            model=service._get_model_or_404("gemini-3-pro-image-preview-apiyi"),
        )

    asyncio.run(run_request())

    assert captured["base_url"] == service.settings.apiyi_gemini_base_url
    assert captured["path"] == "/models/gemini-3-pro-image-preview:generateContent"


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
