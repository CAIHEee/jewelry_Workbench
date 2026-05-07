import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_service.main import app as agent_app
from app.schemas.agent import AgentAssetRef
from app.services.agent_service import (
    AgentService,
    DEFAULT_GEMSTONE_DESIGN_PROMPT,
    GRAYSCALE_PROMPT,
    MULTI_VIEW_PROMPT,
    PRODUCT_REFINE_DEFAULT_PROMPT,
    PRODUCT_REFINE_REMOVE_SELECTED_PROMPT,
    SKETCH_TO_REALISTIC_PROMPT,
)


def _agent_client(auth_client: TestClient) -> TestClient:
    client = TestClient(agent_app)
    for cookie in auth_client.cookies.jar:
        client.cookies.set(cookie.name, cookie.value)
    return client


def test_agent_sketch_prompt_matches_original_module_template() -> None:
    template_path = Path(__file__).resolve().parents[2] / "shared/prompt_templates.json"
    templates = json.loads(template_path.read_text(encoding="utf-8"))
    prompt = next(item["content"] for item in templates if item["id"] == "sketch-to-realistic-default")

    assert SKETCH_TO_REALISTIC_PROMPT == prompt


def test_agent_image_prompts_match_frontend_templates() -> None:
    template_path = Path(__file__).resolve().parents[2] / "shared/prompt_templates.json"
    templates = json.loads(template_path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item["content"] for item in templates}

    assert MULTI_VIEW_PROMPT == by_id["multi-view-jewelry-grid"]
    assert GRAYSCALE_PROMPT == by_id["grayscale-relief-clay"]
    assert PRODUCT_REFINE_DEFAULT_PROMPT == by_id["product-refine-jewelry-shot"]
    assert PRODUCT_REFINE_REMOVE_SELECTED_PROMPT == by_id["product-refine-remove-yellow-markup"]
    assert DEFAULT_GEMSTONE_DESIGN_PROMPT == by_id["gemstone-design-cabochon"]


def test_agent_conversation_stream_creates_draft_action(auth_client: TestClient, monkeypatch) -> None:
    async def fake_llm(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        return self._heuristic_agent_result(conversation.mode, content, attachments)

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fake_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "设计一枚祖母绿吊坠，18K金，复古风格，生成首版设计图", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: message_delta" in response.text
    assert "event: action_card" in response.text

    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["messages"]) >= 2
    assert body["actions"][0]["status"] == "draft"
    assert body["actions"][0]["module_key"] == "text_to_image"
    assert body["actions"][0]["params"]["model"] == "gemini-3.1-flash-image-preview"


def test_design_stream_uses_visible_llm_delta(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_result(self, *, conversation, current_user, content, attachments, analyze_attachments=True):  # noqa: ANN001, ARG001
        return {
            "reply": "最终规划回复不应该覆盖流式正文。",
            "design_state": {"design_brief": {}, "selected_knowledge_cards": [], "knowledge_cards": []},
            "knowledge_cards": [],
            "design_options": [],
        }

    async def fake_visible_stream(self, *, conversation, content, attachments):  # noqa: ANN001, ARG001
        yield "这是"
        yield "流式正文。"

    monkeypatch.setattr(AgentService, "_design_agent_result", fake_design_result)
    monkeypatch.setattr(AgentService, "_stream_design_visible_reply", fake_visible_stream)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "你好", "attachments": []},
    )

    assert response.status_code == 200
    assert "这是" in response.text
    assert "流式正文。" in response.text
    assert "最终规划回复不应该覆盖流式正文。" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["messages"][-1]["content"] == "最终规划回复不应该覆盖流式正文。"


def test_agent_memory_crud(auth_client: TestClient) -> None:
    agent_client = _agent_client(auth_client)
    created = agent_client.post(
        "/agent-api/v1/memories",
        json={"content": "偏好低饱和、复古 Art Deco 风格", "memory_type": "preference"},
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    updated = agent_client.patch(f"/agent-api/v1/memories/{memory_id}", json={"is_enabled": False})
    assert updated.status_code == 200
    assert updated.json()["is_enabled"] is False

    deleted = agent_client.delete(f"/agent-api/v1/memories/{memory_id}")
    assert deleted.status_code == 204


def test_agent_end_conversation_does_not_call_llm(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Ending a conversation should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(f"/agent-api/v1/conversations/{conversation_id}/end")

    assert response.status_code == 200
    body = response.json()
    assert body["messages"][-2]["role"] == "user"
    assert body["messages"][-2]["content"] == "结束对话"
    assert body["messages"][-1]["role"] == "assistant"
    assert "已结束" in body["messages"][-1]["content"]
    assert body["conversation"]["current_stage"] == "ended"
    assert body["conversation"]["status"] == "ended"


def test_design_mode_with_gemstone_image_creates_gemstone_action_and_state(auth_client: TestClient) -> None:
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "围绕这块玉生成首版设计图",
            "attachments": [
                {"name": "jade.png", "storage_url": "https://example.com/jade.png"},
            ],
        },
    )

    assert response.status_code == 200
    assert "event: design_state" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["actions"][0]["module_key"] == "gemstone_design"
    assert detail["actions"][0]["params"]["model"] == "gemini-3.1-flash-image-preview"
    assert detail["actions"][0]["source_image_urls"] == ["https://example.com/jade.png"]
    assert detail["conversation"]["state"]["stone_analysis"]["source"] == "fallback"
    assert detail["conversation"]["state"]["latest_design_mode"] == "gemstone_design"


def test_qwen_agent_llm_is_reused_for_vision_when_vision_config_empty(monkeypatch) -> None:  # noqa: ANN001
    service = AgentService()
    monkeypatch.setattr(service.settings, "agent_llm_base_url", "https://dashscope.aliyuncs.com/compatible-mode")
    monkeypatch.setattr(service.settings, "agent_llm_api_key", "test-qwen-key")
    monkeypatch.setattr(service.settings, "agent_llm_model", "qwen3.6-plus")
    monkeypatch.setattr(service.settings, "agent_vision_llm_base_url", None)
    monkeypatch.setattr(service.settings, "agent_vision_llm_api_key", None)
    monkeypatch.setattr(service.settings, "agent_vision_llm_model", None)

    assert service._effective_vision_llm_config() == (
        "https://dashscope.aliyuncs.com/compatible-mode",
        "test-qwen-key",
        "qwen3.6-plus",
    )


def test_text_only_agent_llm_is_not_reused_for_vision(monkeypatch) -> None:  # noqa: ANN001
    service = AgentService()
    monkeypatch.setattr(service.settings, "agent_llm_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(service.settings, "agent_llm_api_key", "test-deepseek-key")
    monkeypatch.setattr(service.settings, "agent_llm_model", "deepseek-v4-flash")
    monkeypatch.setattr(service.settings, "agent_vision_llm_base_url", None)
    monkeypatch.setattr(service.settings, "agent_vision_llm_api_key", None)
    monkeypatch.setattr(service.settings, "agent_vision_llm_model", None)

    assert service._effective_vision_llm_config() is None


def test_chat_completions_url_accepts_base_with_or_without_v1() -> None:
    service = AgentService()

    assert (
        service._chat_completions_url("https://dashscope.aliyuncs.com/compatible-mode")
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert (
        service._chat_completions_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )


def test_vision_image_url_uses_server_side_data_url(monkeypatch) -> None:  # noqa: ANN001
    service = AgentService()
    monkeypatch.setattr(service.asset_service, "ensure_storage_url_access", lambda **kwargs: None)
    monkeypatch.setattr(service.asset_service, "fetch_asset_bytes", lambda *args, **kwargs: (b"image-bytes", "image/jpeg", "stone.jpg"))

    image_url = service._build_vision_image_url(
        attachment=AgentAssetRef(name="stone.jpg", storage_url="local://input/stone.jpg"),
        current_user=object(),  # type: ignore[arg-type]
    )

    assert image_url == "data:image/jpeg;base64,aW1hZ2UtYnl0ZXM="


def test_design_mode_autofill_updates_brief_without_card_selection(auth_client: TestClient, monkeypatch) -> None:
    async def fake_llm(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        return self._heuristic_agent_result(conversation.mode, content, attachments)

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fake_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "让 Agent 自行补全设计 brief", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: action_card" not in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    brief = detail["conversation"]["state"]["design_brief"]
    assert brief["category"]
    assert brief["metal"]
    assert brief["style"]
    assert brief["craft"]
    assert detail["conversation"]["state"]["latest_design_mode"] == "text_to_image"
    assert detail["conversation"]["state"]["selected_knowledge_cards"] == []


def test_design_mode_short_answer_fills_pending_slot(auth_client: TestClient) -> None:
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    first = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "我想设计一款玉石吊坠", "attachments": []},
    )
    assert first.status_code == 200
    assert "金属材质" in first.text

    second = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "18k", "attachments": []},
    )

    assert second.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["conversation"]["state"]["design_brief"]["metal"] == "18K金"
    assert detail["conversation"]["state"]["pending_design_slot"] == "style"


def test_design_mode_uses_llm_plan_for_brief_slots(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        assert kwargs["content"] == "这是我要设计镶嵌的裸石"
        return {
            "design_brief": {
                "gemstone": "裸石图片",
                "craft": "镶嵌",
            },
            "missing_slots": ["category"],
            "pending_design_slot": "category",
            "should_generate": False,
            "latest_design_mode": "gemstone_design",
            "reply": "已识别为裸石来源。想做成吊坠、戒指还是胸针？",
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "这是我要设计镶嵌的裸石",
            "attachments": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        },
    )

    assert response.status_code == 200
    assert "想做成吊坠" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    brief = detail["conversation"]["state"]["design_brief"]
    assert brief["gemstone"] == "裸石图片"
    assert brief["craft"] == "镶嵌"
    assert brief.get("concept") is None
    assert detail["conversation"]["state"]["pending_design_slot"] == "category"


def test_design_mode_reuses_cached_stone_analysis(auth_client: TestClient, monkeypatch) -> None:
    analyze_calls = 0

    async def fake_analyze(self, attachments, content, *, current_user):  # noqa: ANN001, ARG001
        nonlocal analyze_calls
        analyze_calls += 1
        return {
            "count": 1,
            "shape": "水滴形",
            "color": "绿色",
            "texture": "天然纹理",
            "setting_direction": "随形包镶",
            "risk_notes": "保留裸石比例",
            "source": "vision",
        }

    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {"gemstone": "裸石图片"},
            "missing_slots": ["category"],
            "pending_design_slot": "category",
            "should_generate": False,
            "latest_design_mode": "gemstone_design",
            "reply": "请选择品类。",
            "options": [{"label": "吊坠", "value": "吊坠", "description": "适合随形裸石"}],
        }

    monkeypatch.setattr(AgentService, "_analyze_stones_or_fallback", fake_analyze)
    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    first = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "这是我要设计镶嵌的裸石",
            "attachments": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        },
    )
    second = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "吊坠", "attachments": []},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert analyze_calls == 1


def test_design_stone_analysis_and_generation_prompt_are_chinese(auth_client: TestClient, monkeypatch) -> None:
    async def fake_analyze(self, attachments, content, *, current_user):  # noqa: ANN001, ARG001
        return {
            "count": 4,
            "shape": "Mixed geometric shapes (rectangular block, elongated oval/cabochon, triangle, irregular trapezoid)",
            "color": "Translucent white, amber/honey yellow, reddish-brown/orange, deep emerald green",
            "transparency": "Semi-transparent to translucent with a glossy, waxy or vitreous luster",
            "setting_direction": "Due to irregular shapes and varying sizes, bezel settings or custom low-profile prong settings are recommended to secure edges. Suitable for cluster rings, earrings, or pendant accents.",
            "recommended_style": "Light luxury cluster style",
            "source": "vision",
        }

    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "戒指",
                "metal": "18K白金",
                "style": "Light luxury cluster style",
                "craft": "bezel settings and low-profile prong settings",
                "scene": "晚宴聚会",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": True,
            "latest_design_mode": "gemstone_design",
            "reply": "信息已经足够生成首版设计图。",
        }

    async def no_prompt_llm(self, **kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(AgentService, "_analyze_stones_or_fallback", fake_analyze)
    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    monkeypatch.setattr(AgentService, "_call_design_generation_prompt_llm", no_prompt_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成首版设计图",
            "attachments": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state_text = json.dumps(detail["conversation"]["state"], ensure_ascii=False)
    prompt = detail["actions"][0]["prompt"]
    combined = f"{state_text}\n{prompt}"
    assert "Mixed geometric" not in combined
    assert "Light luxury" not in combined
    assert "bezel settings" not in combined
    assert "low-profile prong" not in combined
    assert "多种几何随形" in combined
    assert "轻奢围镶风" in combined


def test_gemstone_image_design_requires_craft_and_scene_before_ready(auth_client: TestClient, monkeypatch) -> None:
    async def fake_analyze(self, attachments, content, *, current_user):  # noqa: ANN001, ARG001
        return {
            "count": 1,
            "shape": "水滴形",
            "color": "阳绿色",
            "transparency": "冰种",
            "setting_direction": "适合吊坠纵向镶嵌",
            "source": "vision",
        }

    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        brief = dict(kwargs.get("brief") or {})
        brief.setdefault("category", "吊坠")
        brief.setdefault("metal", "18K白金")
        brief.setdefault("style", "现代东方")
        return {
            "design_brief": brief,
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": False,
            "latest_design_mode": "gemstone_design",
            "reply": "信息已经足够生成首版设计图。",
            "options": [],
        }

    monkeypatch.setattr(AgentService, "_analyze_stones_or_fallback", fake_analyze)
    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    first = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "用这颗裸石做设计",
            "attachments": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        },
    )

    assert first.status_code == 200
    assert "event: action_card" not in first.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["pending_design_slot"] == "craft"
    assert state["pending_design_option_source"] == "fallback"

    second = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "包镶", "attachments": []},
    )

    assert second.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["design_brief"]["craft"] == "包镶"
    assert state["pending_design_slot"] == "scene"
    assert "佩戴/使用场景" in state["pending_design_question"]

    third = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "日常通勤", "attachments": []},
    )

    assert third.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["design_brief"]["scene"] == "日常通勤"
    assert state["pending_design_slot"] is None
    assert state["pending_design_option_source"] == "ready"
    assert state["pending_design_options"][0]["value"] == "生成首版设计图"


def test_text_only_design_requires_gemstone_slot_before_generate(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "胸针",
                "concept": "自然花卉风",
                "metal": "18K金",
                "style": "东方雅致",
                "craft": "手工錾刻",
            },
            "missing_slots": ["style"],
            "pending_design_slot": "style",
            "should_generate": True,
            "latest_design_mode": "text_to_image",
            "reply": "信息已经足够，可以直接生成。",
            "options": [],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "我想做一个18K金东方雅致风的胸针，用手工錾刻", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: design_options" in response.text
    assert "event: action_card" not in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["pending_design_slot"] == "gemstone"
    assert state["pending_design_options"]


def test_jade_gemstone_options_prefer_section_six_knowledge_for_necklace() -> None:
    service = AgentService()

    options = service._fallback_design_options("gemstone", brief={"category": "项链"})

    labels = [item["label"] for item in options]
    values = [item["value"] for item in options]
    assert labels
    assert any("冰种" in label or "白冰" in label or "飘花" in label for label in labels)
    assert any("翡翠" in value for value in values)
    assert any("项链吊坠" in value or "项链" in value for value in values)
    assert all("钻石" not in label for label in labels)


def test_design_llm_generated_gemstone_options_are_preserved(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "项链",
                "metal": "18K金",
                "style": "东方雅致",
            },
            "missing_slots": ["gemstone"],
            "pending_design_slot": "gemstone",
            "should_generate": False,
            "latest_design_mode": "text_to_image",
            "reply": "请先确定主石。",
            "options": [
                {"label": "晴水叶子主石", "value": "主石走晴水翡翠叶子方向，做项链", "description": "更轻盈留白，适合东方雅致路线"},
                {"label": "白冰蛋面双石", "value": "主石用白冰翡翠蛋面双石组合，做项链", "description": "更现代，也把数量关系一起定下来"},
            ],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "设计一款项链", "attachments": []},
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    options = detail["conversation"]["state"]["pending_design_options"]
    labels = [item["label"] for item in options]
    assert labels == ["晴水叶子主石", "白冰蛋面双石"]


def test_design_alternative_options_request_returns_new_batch(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        assert kwargs["alternative_options_requested"] is True
        assert [item["label"] for item in kwargs["previous_options"]] == ["白冰水滴耳坠", "飘花叶子耳饰"]
        return {
            "design_brief": {
                "category": "耳环",
                "metal": "18K金",
                "style": "东方雅致",
            },
            "missing_slots": ["gemstone"],
            "pending_design_slot": "gemstone",
            "should_generate": False,
            "latest_design_mode": "text_to_image",
            "reply": "好的，我换一组更不同的翡翠方向给你参考。",
            "options": [
                {"label": "紫罗兰蛋面耳钉", "value": "主石用一对紫罗兰翡翠蛋面，做耳钉", "description": "柔和精致，适合轻礼服路线"},
                {"label": "满绿无事牌耳坠", "value": "主石用一对满绿色翡翠无事牌，做耳坠", "description": "色彩存在感强，适合大气礼服路线"},
            ],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    service = AgentService()
    service._save_conversation_state(
        conversation_id,
        {
            "design_brief": {"category": "耳环", "metal": "18K金", "style": "东方雅致"},
            "pending_design_slot": "gemstone",
            "pending_design_options": [
                {"label": "白冰水滴耳坠", "value": "主石用一对白冰翡翠水滴，做耳坠", "description": "清冷轻盈"},
                {"label": "飘花叶子耳饰", "value": "主石用一对飘花翡翠叶子，做耳饰", "description": "自然灵动"},
            ],
            "pending_design_question": "请先确定主石方案。",
            "pending_design_option_source": "llm",
        },
    )

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "推荐一下其他选择", "attachments": []},
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    options = detail["conversation"]["state"]["pending_design_options"]
    assert [item["label"] for item in options] == ["紫罗兰蛋面耳钉", "满绿无事牌耳坠"]
    assert detail["conversation"]["state"]["pending_design_slot"] == "gemstone"
    assert detail["conversation"]["state"]["design_brief"].get("gemstone") in (None, "")


def test_design_alternative_options_request_does_not_advance_slot(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "耳环",
                "gemstone": "紫罗兰蛋面耳钉",
                "metal": "18K黄金",
            },
            "missing_slots": ["metal"],
            "pending_design_slot": "metal",
            "should_generate": False,
            "latest_design_mode": "text_to_image",
            "reply": "我再给你换一组更不同的主石方向。",
            "options": [
                {"label": "紫罗兰蛋面耳钉", "value": "主石用一对紫罗兰翡翠蛋面，做耳钉", "description": "柔和精致"},
                {"label": "满绿无事牌耳坠", "value": "主石用一对满绿色翡翠无事牌，做耳坠", "description": "大气礼服感"},
            ],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    service = AgentService()
    service._save_conversation_state(
        conversation_id,
        {
            "design_brief": {"category": "耳环", "style": "东方雅致"},
            "pending_design_slot": "gemstone",
            "pending_design_options": [
                {"label": "白冰水滴耳坠", "value": "主石用一对白冰翡翠水滴，做耳坠", "description": "清冷轻盈"},
                {"label": "飘花叶子耳饰", "value": "主石用一对飘花翡翠叶子，做耳饰", "description": "自然灵动"},
            ],
            "pending_design_question": "请先确定主石方案。",
            "pending_design_option_source": "llm",
        },
    )

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "换一批", "attachments": []},
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["pending_design_slot"] == "gemstone"
    assert state["design_brief"].get("gemstone") in (None, "")
    labels = [item["label"] for item in state["pending_design_options"]]
    assert labels
    assert "白冰水滴耳坠" not in labels
    assert "飘花叶子耳饰" not in labels


def test_design_completed_slot_is_not_reasked_by_llm(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "style": "现代极简",
            },
            "missing_slots": ["metal"],
            "pending_design_slot": "metal",
            "should_generate": False,
            "latest_design_mode": "text_to_image",
            "reply": "风格已确认为现代极简。请问您希望使用哪种金属材质进行镶嵌？",
            "options": [
                {"label": "18K白金", "value": "18K白金", "description": "清爽现代"},
                {"label": "18K黄金", "value": "18K黄金", "description": "经典稳妥"},
            ],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    service = AgentService()
    service._save_conversation_state(
        conversation_id,
        {
            "design_brief": {
                "category": "耳环",
                "gemstone": "白冰水滴耳坠",
                "metal": "24K足金",
            },
            "pending_design_slot": "style",
            "pending_design_options": [
                {"label": "现代极简", "value": "现代极简", "description": "线条利落"},
                {"label": "东方雅致", "value": "东方雅致", "description": "温润含蓄"},
            ],
            "pending_design_question": "请确认设计风格。",
            "pending_design_option_source": "llm",
        },
    )

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "现代极简", "attachments": []},
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["design_brief"]["metal"] == "24K足金"
    assert state["design_brief"]["style"] == "现代极简"
    assert state["pending_design_slot"] is None
    assert state["pending_design_options"][0]["value"] == "生成首版设计图"


def test_merge_design_content_updates_style_slot_from_revision_text() -> None:
    service = AgentService()
    brief = {"style": "复古", "concept": "复古宫廷"}

    service._merge_design_content_into_brief(brief, "修改一下风格，改成轻奢")

    assert brief["style"] == "轻奢"
    assert brief["concept"] == "复古宫廷"


def test_merge_design_content_updates_metal_slot_from_revision_text() -> None:
    service = AgentService()
    brief = {"metal": "18K金", "style": "现代极简"}

    service._merge_design_content_into_brief(brief, "材质改为铂金")

    assert brief["metal"] == "铂金"
    assert brief["style"] == "现代极简"


def test_merge_design_content_handles_compound_revision_naturally() -> None:
    service = AgentService()
    brief = {
        "style": "复古",
        "concept": "宫廷繁花",
        "supplement": "强调钻石火彩",
    }

    service._merge_design_content_into_brief(
        brief,
        "修改一下风格，改成轻奢，设计理念你帮我想一份进行补充，补充说明删除",
    )

    assert brief["style"] == "轻奢"
    assert "concept" not in brief
    assert "supplement" not in brief


def test_jewelry_term_cards_parse_markdown_tables(monkeypatch) -> None:  # noqa: ANN001
    service = AgentService()
    markdown = """
## 设计风格

| 术语 | 定义 | 视觉要点 | 适用场景 | 中文提示词 |
|---|---|---|---|---|
| 东方雅致风格 | 融合中式符号与克制审美 | 留白、祥云、莲花、竹节 | 翡翠、和田玉、黄金饰品 | 东方雅致，留白构图，祥云如意，温润含蓄 |

## 翡翠镶嵌专项

### 7.2 翡翠常用镶嵌结构

| 结构 | 定义 | 适合翡翠 | 视觉效果 | 中文提示词 | 注意事项 |
|---|---|---|---|---|---|
| 翡翠蛋面围钻 | 蛋面外圈环绕小钻 | 阳绿蛋面、白冰蛋面 | 显大、闪耀、高级 | 翡翠蛋面，小钻围镶，外圈闪耀 | 小钻应均匀，不要盖住翡翠边缘 |
"""
    monkeypatch.setattr(service, "_read_jewelry_terms_markdown", lambda: markdown)

    cards = service._load_jewelry_term_cards()

    assert len(cards) == 2
    assert cards[0]["title"] == "东方雅致风格"
    assert cards[0]["category"] == "设计风格"
    assert "留白" in cards[0]["content"]
    assert cards[1]["title"] == "翡翠蛋面围钻"
    assert cards[1]["category"] == "7.2 翡翠常用镶嵌结构"
    assert "显大、闪耀、高级" in cards[1]["content"]
    assert "小钻应均匀" in cards[1]["content"]


def test_jewelry_knowledge_search_can_hit_later_doc_sections(monkeypatch) -> None:  # noqa: ANN001
    service = AgentService()
    cards = [
        {"id": "term-1", "category": "设计风格", "title": "装饰艺术风格", "content": "几何对称，复古高级感"},
        {"id": "term-2", "category": "7.2 翡翠常用镶嵌结构", "title": "翡翠蛋面围钻", "content": "适合阳绿蛋面，显大、闪耀、高级"},
        {"id": "term-3", "category": "7.4 翡翠与金属搭配", "title": "阳绿翡翠", "content": "推荐18K白金、18K黄金，颜色鲜活通透"},
    ]
    monkeypatch.setattr(service, "_load_jewelry_term_cards", lambda: cards)

    results = service._search_jewelry_knowledge(
        content="我想做一枚阳绿翡翠蛋面戒指，18K白金围钻",
        brief={"category": "戒指", "gemstone": "翡翠蛋面", "metal": "18K白金"},
        stone_analysis=None,
    )

    titles = [item["title"] for item in results]
    assert "翡翠蛋面围钻" in titles
    assert "阳绿翡翠" in titles


def test_design_generation_prompt_is_summarized_by_llm(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "吊坠",
                "gemstone": "裸石图片",
                "metal": "18K金",
                "style": "现代东方",
                "craft": "包镶与局部围钻",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": True,
            "latest_design_mode": "gemstone_design",
            "reply": "我会生成首版裸石镶嵌设计图。",
        }

    async def fake_prompt_llm(self, **kwargs):  # noqa: ANN001
        assert "最近对话" not in kwargs
        assert kwargs["has_design_source"] is True
        return "以参考裸石为核心设计现代东方风格18K金吊坠，严格保留裸石原始形状、颜色、比例和天然纹理，采用贴合轮廓的包镶结构与局部围钻，金属线条干净，层次清晰，呈现高级珠宝成品设计渲染图。"

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    monkeypatch.setattr(AgentService, "_call_design_generation_prompt_llm", fake_prompt_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成首版设计图",
            "attachments": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    prompt = detail["actions"][0]["prompt"]
    assert "以参考裸石为核心" in prompt
    assert "完整的珠宝设计正视图" in prompt
    assert "不裁切" in prompt
    assert "专业参考：" not in prompt
    assert "当前 brief" not in prompt


def test_design_generation_prompt_rejects_meta_text_from_llm(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "胸针",
                "concept": "花开富贵",
                "gemstone": "裸石图片",
                "metal": "18K玫瑰金",
                "style": "自然花卉风",
                "craft": "立体浮雕与錾刻",
                "knowledge_summary": "本文档用于优化文生图模型的提示词质量，Gold-Silver Inlay, metal inlay work",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": True,
            "latest_design_mode": "gemstone_design",
            "reply": "开始生成。",
        }

    async def bad_prompt_llm(self, **kwargs):  # noqa: ANN001
        return (
            "专业参考摘要：本文档用于优化文生图模型的提示词质量；Gold-Silver Inlay, metal inlay work；"
            "> 本文档用于优化文生图模型提示词质量。生成完整的珠宝设计正视图。"
        )

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    monkeypatch.setattr(AgentService, "_call_design_generation_prompt_llm", bad_prompt_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成首版设计图",
            "attachments": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    prompt = detail["actions"][0]["prompt"]
    assert "本文档" not in prompt
    assert "文生图模型" not in prompt
    assert "提示词" not in prompt
    assert "Gold-Silver" not in prompt
    assert "metal inlay work" not in prompt
    assert "完整的珠宝设计正视图" in prompt
    assert "花开富贵" in prompt


def test_design_generation_prompt_rejects_english_prompt() -> None:
    service = AgentService()

    assert not service._is_safe_design_generation_prompt(
        "Create a luxury jewelry pendant with bezel settings, translucent gemstone, glossy waxy luster, modern oriental style."
    )


def test_design_regenerate_skips_followup_options_when_brief_is_ready(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_design_llm_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Ready regenerate should not call the design brief LLM.")

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fail_if_design_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    conversation = detail["conversation"]
    conversation_state = conversation["state"] or {}
    conversation_state.update(
        {
            "design_brief": {
                "category": "胸针",
                "concept": "花开富贵",
                "metal": "18K玫瑰金",
                "style": "自然花卉风",
                "craft": "立体浮雕与錾刻",
                "scene": "收藏展示",
            },
            "stone_analysis": {
                "count": 2,
                "shape": "水滴形与随形",
                "color": "橙色与绿色",
                "texture": "保留天然纹理",
                "setting_direction": "围绕裸石轮廓进行镶嵌",
                "risk_notes": "保持裸石比例与颜色",
                "source": "fallback",
            },
            "design_source_assets": [{"name": "stone.png", "storage_url": "https://example.com/stone.png"}],
        }
    )
    update_response = agent_client.patch(
        f"/agent-api/v1/conversations/{conversation_id}/design-state",
        json={
            "design_brief": conversation_state["design_brief"],
            "selected_knowledge_cards": [],
        },
    )
    assert update_response.status_code == 200

    service = AgentService()
    service._save_conversation_state(conversation_id, conversation_state)

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "重新生成设计图", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: action_card" in response.text
    assert "event: design_options" not in response.text
    assert "正在生成选项卡" not in response.text


def test_design_ready_state_returns_generate_option(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "耳饰",
                "concept": "灵动花叶",
                "gemstone": "裸石图片",
                "metal": "18K白金",
                "style": "自然灵动",
                "craft": "随形镶嵌",
                "scene": "晚宴聚会",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": False,
            "latest_design_mode": "gemstone_design",
            "reply": "信息已经足够生成首版设计图。",
            "options": [],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "晚宴聚会", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: design_options" in response.text
    assert "生成首版设计图" in response.text
    assert "继续补充设计要求" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["pending_design_options"][0]["value"] == "生成首版设计图"
    assert state["pending_design_option_source"] == "ready"


def test_design_regenerate_uses_alternate_text_to_image_model(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "项链",
                "gemstone": "冰种阳绿翡翠蛋面",
                "metal": "18K黄金",
                "style": "现代简约",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": True,
            "latest_design_mode": "text_to_image",
            "reply": "开始重新生成设计图。",
            "options": [],
        }

    async def fake_prompt_llm(self, **kwargs):  # noqa: ANN001
        return "冰种阳绿翡翠蛋面项链，18K黄金，现代简约风格，完整的珠宝设计正视图。"

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    monkeypatch.setattr(AgentService, "_call_design_generation_prompt_llm", fake_prompt_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "重新生成设计图", "attachments": []},
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["actions"][0]["module_key"] == "text_to_image"
    assert detail["actions"][0]["params"]["model"] == "gpt-image-2-all-apiyi"


def test_merge_llm_design_brief_preserves_user_selected_detailed_gemstone() -> None:
    service = AgentService()

    merged = service._merge_llm_design_brief(
        {
            "category": "戒指",
            "gemstone": "两颗白冰小翡翠双石组合",
            "metal": "18K黄金",
            "style": "简约现代",
        },
        {
            "gemstone": "翡翠圆形蛋面",
            "style": "新中式",
        },
    )

    assert merged["gemstone"] == "两颗白冰小翡翠双石组合"
    assert merged["style"] == "简约现代"


def test_merge_llm_design_brief_allows_generic_gemstone_to_become_specific() -> None:
    service = AgentService()

    merged = service._merge_llm_design_brief(
        {
            "category": "戒指",
            "gemstone": "翡翠",
            "metal": "18K黄金",
        },
        {
            "gemstone": "白冰翡翠蛋面",
        },
    )

    assert merged["gemstone"] == "白冰翡翠蛋面"


def test_design_does_not_auto_generate_after_last_slot_answer(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "项链",
                "gemstone": "冰种翡翠蛋面",
                "metal": "18K黄金",
                "style": "现代简约",
                "scene": "晚宴聚会",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": True,
            "latest_design_mode": "text_to_image",
            "reply": "所有设计要素已确认，正在为您生成。",
            "options": [],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "晚宴聚会", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: action_card" not in response.text
    assert "event: design_options" in response.text
    assert "继续补充设计要求" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["actions"] == []
    assert detail["conversation"]["state"]["pending_design_option_source"] == "ready"


def test_design_continue_supplement_intent_unlocks_freeform_input(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "项链",
                "gemstone": "冰种翡翠蛋面",
                "metal": "18K黄金",
                "style": "现代简约",
            },
            "missing_slots": [],
            "pending_design_slot": "",
            "should_generate": False,
            "latest_design_mode": "text_to_image",
            "reply": "信息已经足够生成首版设计图。",
            "options": [],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "继续补充设计要求", "attachments": []},
    )

    assert response.status_code == 200
    assert "event: design_options" not in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["conversation"]["state"]["pending_design_options"] == []


def test_design_review_summary_uses_fixed_jade_field_order() -> None:
    service = AgentService()

    summary = service._format_design_brief_for_review(
        {
            "category": "项链",
            "gemstone": "冰种阳绿翡翠蛋面",
            "metal": "18K黄金",
            "craft": "微镶小钻",
            "style": "现代简约",
            "scene": "晚宴聚会",
            "concept": "轻盈、通透、偏高级珠宝",
        },
        None,
    )

    assert "- 品类：项链" in summary
    assert "- 主石种水：冰种" in summary
    assert "- 翡翠颜色：阳绿" in summary
    assert "- 翡翠形制：蛋面" in summary
    assert "- 玉石数量：单颗主石" in summary
    assert "- 材质：18K黄金" in summary
    assert "- 镶嵌/工艺：微镶小钻" in summary


def test_design_llm_cannot_repeat_gemstone_slot_after_gemstone_is_filled(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_llm(self, **kwargs):  # noqa: ANN001
        return {
            "design_brief": {
                "category": "项链",
                "gemstone": "冰种阳绿翡翠蛋面",
                "metal": "18K黄金",
                "style": "现代简约",
                "scene": "收藏送礼",
            },
            "missing_slots": [],
            "pending_design_slot": "gemstone",
            "should_generate": False,
            "latest_design_mode": "text_to_image",
            "reply": "请继续补充主石特征。",
            "options": [
                {"label": "冰种阳绿蛋面", "value": "冰种阳绿蛋面", "description": "清透鲜亮"},
            ],
        }

    monkeypatch.setattr(AgentService, "_call_design_brief_llm", fake_design_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "冰种阳绿蛋面", "attachments": []},
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    state = detail["conversation"]["state"]
    assert state["pending_design_slot"] is None
    assert state["pending_design_option_source"] == "ready"


def test_agent_delete_conversation_cleans_related_records(auth_client: TestClient, monkeypatch) -> None:
    async def fake_llm(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        return self._heuristic_agent_result(conversation.mode, content, attachments)

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fake_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "design"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "设计一枚蓝宝石戒指", "attachments": []},
    )
    assert response.status_code == 200

    memory = agent_client.post(
        "/agent-api/v1/memories",
        json={"content": "偏好冷色宝石", "memory_type": "preference", "source_conversation_id": conversation_id},
    )
    assert memory.status_code == 201
    memory_id = memory.json()["id"]

    deleted = agent_client.delete(f"/agent-api/v1/conversations/{conversation_id}")
    assert deleted.status_code == 204

    missing = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}")
    assert missing.status_code == 404
    memories = agent_client.get("/agent-api/v1/memories")
    assert memories.status_code == 200
    saved_memory = next(item for item in memories.json() if item["id"] == memory_id)
    assert saved_memory["source_conversation_id"] is None


def test_workflow_uses_recent_image_and_routes_sketch_to_realistic(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        raise AssertionError("Clear workflow routing should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "",
            "attachments": [
                {
                    "name": "sketch.png",
                    "storage_url": "https://example.com/sketch.png",
                    "preview_url": "https://example.com/sketch.png",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert "event: action_card" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    action = detail["actions"][0]
    assert action["module_key"] == "sketch_to_realistic"
    assert action["params"]["model"] == "gemini-3.1-flash-image-preview"


def test_workflow_prompt_with_image_uses_llm_planning(auth_client: TestClient, monkeypatch) -> None:
    async def fake_llm(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        assert content == "对这个图片拓展成多视图"
        return self._build_workflow_action_result("multi_view", content, attachments)

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fake_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "对这个图片拓展成多视图",
            "attachments": [
                {
                    "name": "product.png",
                    "storage_url": "https://example.com/product.png",
                    "preview_url": "https://example.com/product.png",
                }
            ],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["actions"][0]["module_key"] == "multi_view"
    assert detail["actions"][0]["params"]["model"] == "gpt-image-2-all-apiyi"


def test_agent_registers_latest_generation_result_for_followup(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        raise AssertionError("Card action routing should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    initial = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "",
            "attachments": [
                {"name": "sketch.png", "storage_url": "https://example.com/sketch.png"},
            ],
        },
    )
    assert initial.status_code == 200

    registered = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/generation-result",
        json={
            "module_key": "sketch_to_realistic",
            "image_url": "https://example.com/realistic-result.png",
            "name": "写实结果",
        },
    )
    assert registered.status_code == 200

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "生成多视图", "attachments": []},
    )
    assert response.status_code == 200
    assert "event: action_card" in response.text
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    assert detail["actions"][0]["module_key"] == "multi_view"
    assert detail["actions"][0]["source_image_urls"] == ["https://example.com/realistic-result.png"]
    assert detail["actions"][0]["params"]["model"] == "gpt-image-2-all-apiyi"


def test_custom_refine_prompt_does_not_append_default_prompt(auth_client: TestClient, monkeypatch) -> None:
    async def fake_llm(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        return self._build_workflow_action_result("product_refine", content, attachments)

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fake_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "仅自定义精修：只把金属改成哑光质感",
            "attachments": [
                {"name": "sketch.png", "storage_url": "https://example.com/sketch.png"},
                {"name": "current.png", "storage_url": "https://example.com/current.png"},
            ],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    action = detail["actions"][0]
    assert action["module_key"] == "product_refine"
    assert action["prompt"] == "只把金属改成哑光质感"
    assert action["source_image_urls"] == ["https://example.com/sketch.png", "https://example.com/current.png"]


def test_agent_refine_remove_selected_uses_local_delete_template(auth_client: TestClient, monkeypatch) -> None:
    async def fake_llm(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        return self._heuristic_agent_result(conversation.mode, content, attachments)

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fake_llm)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "Agent精修：删除选中内容",
            "attachments": [
                {"name": "marked.png", "storage_url": "https://example.com/marked.png"},
            ],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    action = detail["actions"][0]
    assert action["module_key"] == "product_refine"
    assert "严格移除参考图中黄色线圈定/标注的区域" in action["prompt"]
    assert "画面其他部分保持100%不变" in action["prompt"]


def test_card_action_grayscale_uses_explicit_result_without_llm(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        raise AssertionError("Card action routing should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成灰度图",
            "attachments": [
                {"name": "multi-view.png", "storage_url": "https://example.com/multi-view.png"},
            ],
        },
    )

    assert response.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    action = detail["actions"][0]
    assert action["module_key"] == "grayscale_relief"
    assert action["source_image_urls"] == ["https://example.com/multi-view.png"]
    assert action["params"]["model"] == "gpt-image-2-all-apiyi"


def test_agent_result_event_preserves_action_sources(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        raise AssertionError("Clear workflow routing should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    response = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成灰度图",
            "attachments": [
                {"name": "multi-view.png", "storage_url": "https://example.com/multi-view.png"},
            ],
        },
    )
    assert response.status_code == 200
    action = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()["actions"][0]

    registered = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/generation-result",
        json={
            "action_id": action["id"],
            "module_key": "grayscale_relief",
            "image_url": "https://example.com/grayscale-result.png",
            "name": "灰度结果",
        },
    )
    assert registered.status_code == 200
    detail = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()
    generation_message = next(item for item in detail["messages"] if (item.get("event") or {}).get("type") == "generation_result")
    assert generation_message["event"]["source_assets"][0]["storage_url"] == "https://example.com/multi-view.png"
    assert detail["conversation"]["state"]["generation_source_assets_by_module"]["grayscale_relief"][0]["storage_url"] == "https://example.com/multi-view.png"


def test_regenerate_grayscale_reuses_original_module_source(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        raise AssertionError("Card action routing should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    first = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成灰度图",
            "attachments": [
                {"name": "multi-view.png", "storage_url": "https://example.com/multi-view.png"},
            ],
        },
    )
    assert first.status_code == 200
    first_action = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()["actions"][0]
    registered = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/generation-result",
        json={
            "action_id": first_action["id"],
            "module_key": "grayscale_relief",
            "image_url": "https://example.com/grayscale-result.png",
            "name": "灰度结果",
        },
    )
    assert registered.status_code == 200

    regenerated = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "重新生成灰度图",
            "attachments": [
                {"name": "wrong-latest-result.png", "storage_url": "https://example.com/grayscale-result.png"},
            ],
        },
    )
    assert regenerated.status_code == 200
    next_action = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()["actions"][0]
    assert next_action["module_key"] == "grayscale_relief"
    assert next_action["source_image_urls"] == ["https://example.com/multi-view.png"]


def test_regenerate_multi_view_reuses_original_module_source(auth_client: TestClient, monkeypatch) -> None:
    async def fail_if_llm_called(self, *, conversation, current_user, content, attachments, memories):  # noqa: ANN001, ARG001
        raise AssertionError("Card action routing should not call the LLM.")

    monkeypatch.setattr(AgentService, "_call_llm_or_fallback", fail_if_llm_called)
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]

    first = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "生成多视图",
            "attachments": [
                {"name": "realistic.png", "storage_url": "https://example.com/realistic.png"},
            ],
        },
    )
    assert first.status_code == 200
    first_action = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()["actions"][0]
    registered = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/generation-result",
        json={
            "action_id": first_action["id"],
            "module_key": "multi_view",
            "image_url": "https://example.com/multi-view-result.png",
            "name": "多视图结果",
        },
    )
    assert registered.status_code == 200

    regenerated = agent_client.post(
        f"/agent-api/v1/conversations/{conversation_id}/messages/stream",
        json={
            "content": "重新生成多视图",
            "attachments": [
                {"name": "wrong-latest-result.png", "storage_url": "https://example.com/multi-view-result.png"},
            ],
        },
    )
    assert regenerated.status_code == 200
    next_action = agent_client.get(f"/agent-api/v1/conversations/{conversation_id}").json()["actions"][0]
    assert next_action["module_key"] == "multi_view"
    assert next_action["source_image_urls"] == ["https://example.com/realistic.png"]


def test_agent_rejects_unknown_action_module(auth_client: TestClient) -> None:
    agent_client = _agent_client(auth_client)
    created = agent_client.post("/agent-api/v1/conversations", json={"mode": "workflow"})
    conversation_id = created.json()["id"]
    service = AgentService()
    current_user = auth_client.get("/api/v1/auth/me").json()

    from app.models.user import User
    from app.schemas.agent import AgentActionCard

    user = User(id=current_user["id"], username=current_user["username"], role=current_user["role"])
    try:
        service.create_action_from_card(
            conversation_id=conversation_id,
            current_user=user,
            card=AgentActionCard(kind="image_to_image", module_key="unknown_module", title="错误动作"),
        )
    except Exception as exc:  # noqa: BLE001
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Unknown Agent module should be rejected.")
