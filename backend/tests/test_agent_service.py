from fastapi.testclient import TestClient

from agent_service.main import app as agent_app
from app.services.agent_service import AgentService


def _agent_client(auth_client: TestClient) -> TestClient:
    client = TestClient(agent_app)
    for cookie in auth_client.cookies.jar:
        client.cookies.set(cookie.name, cookie.value)
    return client


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


def test_design_stream_uses_visible_llm_delta(auth_client: TestClient, monkeypatch) -> None:
    async def fake_design_result(self, *, conversation, current_user, content, attachments):  # noqa: ANN001, ARG001
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
    assert detail["actions"][0]["source_image_urls"] == ["https://example.com/jade.png"]
    assert detail["conversation"]["state"]["stone_analysis"]["source"] == "fallback"
    assert detail["conversation"]["state"]["latest_design_mode"] == "gemstone_design"


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
    assert detail["actions"][0]["module_key"] == "sketch_to_realistic"


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
