from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException, status
from sqlalchemy import case, delete, desc, select, update

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.models.agent import AgentAction, AgentConversation, AgentMessage, AgentUserMemory
from app.models.asset_record import AssetRecord as AssetRecordModel
from app.models.user import User
from app.schemas.agent import (
    AgentActionCard,
    AgentActionConfirmRequest,
    AgentActionConfirmResponse,
    AgentActionResponse,
    AgentAssetRef,
    AgentConversationDetail,
    AgentConversationResponse,
    AgentMemoryProposal,
    AgentMessageResponse,
    AgentUserMemoryResponse,
)
from app.schemas.ai import FusionMode, FusionRequestMetadata, MultiViewSplitRequest, ReferenceImageRequestMetadata, TextToImageRequest
from app.services.asset_service import AssetService
from app.services.job_queue_service import JobQueueService


DEFAULT_IMAGE_MODEL = "gpt-image-2-all-apiyi"
DEFAULT_SKETCH_TO_REALISTIC_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX = "高级珠宝产品渲染效果，背景干净，金属光泽真实，工艺细节清晰。"
DESIGN_FRONT_VIEW_CONSTRAINT = (
    "必须生成完整的珠宝设计正视图，主体珠宝完整入画、居中展示，不裁切、不只展示局部特写，"
    "正面视角或近似正交正视图，完整呈现整体轮廓、结构比例、主石位置、镶嵌结构和全部关键设计细节。"
)
DEFAULT_GEMSTONE_DESIGN_PROMPT = (
    "以参考图中的裸石/玉石为核心进行镶嵌珠宝设计，不改变裸石原始形状、颜色、大小比例与天然纹理。"
    "根据裸石形态设计合理镶口与结构，可采用18K金、爪镶、包镶、围钻、花丝、镂空或对称布局，"
    "突出裸石天然美感，结构可制作，细节完整，光影精致，高级珠宝产品设计渲染图。"
)
SKETCH_TO_REALISTIC_PROMPT = (
    "将参考线稿图转换风格为现实写实成品图，玉石还原天然玉石翡翠的真实质感，天然玉石的温润光泽，"
    "透光程度与自然纹理，无塑料感、玻璃感，颜色过渡自然，无过度饱和，需与参考图玉石颜色一致。"
    "金属还原亮面抛光质感，边缘高光与阴影过渡自然，无过曝发黑、无CG感，精准复现参考图中的每一颗钻石，"
    "呈现天然白钻的清晰刻面、真实火彩与颗粒分明的质感，棚拍光线、真实自然光影，成品图需与参考图的珠宝设计细节一致。"
)
MULTI_VIEW_PROMPT = (
    "生成基于参考图的4个标准视角（正面、左侧、右侧、背面）并以2x2网格布局呈现，"
    "所有视图与参考图的风格、材质及工艺细节保持一致，来自同一个连贯的三维模型。"
)
GRAYSCALE_PROMPT = (
    "严格遵循参考图像：保持精确的3D结构、比例、透视以及所有雕塑细节。渲染为纯黏土模型："
    "单色调哑光灰色材质，无金属反射，无宝石折射，无抛光，细节必须保持清晰。"
)
PRODUCT_REFINE_DEFAULT_PROMPT = (
    "在保持原始设计结构和主体造型一致的前提下，对当前珠宝效果图进行产品级精修：优化金属抛光质感、"
    "宝石通透度、钻石火彩、边缘高光、阴影层次和整体清晰度，修正轻微变形、脏污、过曝或塑料感，"
    "背景保持干净，呈现真实高级珠宝棚拍效果。"
)
PRODUCT_REFINE_REMOVE_SELECTED_PROMPT = (
    "以参考图为唯一依据进行局部修改：严格移除参考图中黄色线圈定/标注的区域，"
    "移除后不在该位置补充、绘制任何新元素，也不修改周围的原有细节，"
    "不新增、不改动、不添加任何其他内容，除了黄线区域的移除操作，画面其他部分保持100%不变。"
)


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


class AgentService:
    def __init__(self) -> None:
        init_db()
        self.settings = get_settings()
        self.asset_service = AssetService()
        self.job_service = JobQueueService()

    def list_conversations(self, *, current_user: User) -> list[AgentConversationResponse]:
        with SessionLocal() as session:
            records = session.execute(
                select(AgentConversation)
                .where(AgentConversation.user_id == current_user.id)
                .order_by(desc(AgentConversation.updated_at))
            ).scalars().all()
        return [self._conversation_to_schema(record) for record in records]

    def create_conversation(self, *, current_user: User, mode: str, title: str | None = None) -> AgentConversationResponse:
        now = datetime.now(timezone.utc)
        default_title = self._default_conversation_title(mode, now)
        with SessionLocal() as session:
            record = AgentConversation(
                id=str(uuid4()),
                user_id=current_user.id,
                mode=mode,
                title=title or default_title,
                current_stage="intake",
                status="active",
                state_json=self._dump_json({"recent_assets": [], "last_action_id": None}),
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
        return self._conversation_to_schema(record)

    def delete_conversation(self, *, conversation_id: str, current_user: User) -> None:
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation_id)
            if record is None or record.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent conversation not found.")
            now = datetime.now(timezone.utc)
            session.execute(
                update(AgentUserMemory)
                .where(
                    AgentUserMemory.user_id == current_user.id,
                    AgentUserMemory.source_conversation_id == conversation_id,
                )
                .values(source_conversation_id=None, updated_at=now)
            )
            session.execute(delete(AgentAction).where(AgentAction.conversation_id == conversation_id))
            session.execute(delete(AgentMessage).where(AgentMessage.conversation_id == conversation_id))
            session.execute(
                delete(AgentConversation).where(
                    AgentConversation.id == conversation_id,
                    AgentConversation.user_id == current_user.id,
                )
            )
            session.commit()

    def get_conversation_detail(self, *, conversation_id: str, current_user: User) -> AgentConversationDetail:
        conversation = self._get_conversation(conversation_id, current_user=current_user)
        with SessionLocal() as session:
            messages = session.execute(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation_id)
                .order_by(
                    AgentMessage.created_at,
                    case(
                        (AgentMessage.role == "user", 0),
                        (AgentMessage.role == "assistant", 1),
                        else_=2,
                    ),
                    AgentMessage.id,
                )
            ).scalars().all()
            actions = session.execute(
                select(AgentAction)
                .where(AgentAction.conversation_id == conversation_id)
                .order_by(desc(AgentAction.created_at))
            ).scalars().all()
        return AgentConversationDetail(
            conversation=self._conversation_to_schema(conversation),
            messages=[self._message_to_schema(item) for item in messages],
            actions=[self._action_to_schema(item) for item in actions],
        )

    def update_design_state(
        self,
        *,
        conversation_id: str,
        current_user: User,
        design_brief: dict[str, object] | None = None,
        selected_knowledge_cards: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation_id)
            if record is None or record.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent conversation not found.")
            state = self._load_json(record.state_json) or {}
            if design_brief is not None:
                state["design_brief"] = design_brief
            if selected_knowledge_cards is not None:
                state["selected_knowledge_cards"] = selected_knowledge_cards
            record.state_json = self._dump_json(state)
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
        return self._build_design_state_payload(state)

    async def handle_user_message(
        self,
        *,
        conversation_id: str,
        current_user: User,
        content: str,
        attachments: list[AgentAssetRef],
    ) -> tuple[str, AgentActionResponse | None, AgentMemoryProposal | None]:
        conversation = self._get_conversation(conversation_id, current_user=current_user)
        normalized_attachments = self._normalize_asset_refs(attachments, current_user=current_user)
        active_attachments = normalized_attachments or self._load_recent_assets(conversation)
        self._create_message(
            conversation_id=conversation_id,
            current_user=current_user,
            role="user",
            content=content or "已选择参考图片。",
            attachments=normalized_attachments,
        )

        memories = self.list_memories(current_user=current_user, enabled_only=True)
        if conversation.mode == "design":
            design_attachments = normalized_attachments or self._load_design_source_assets(conversation)
            design_result = await self._design_agent_result(
                conversation=conversation,
                current_user=current_user,
                content=content,
                attachments=design_attachments,
            )
            self._create_message(
                conversation_id=conversation_id,
                current_user=current_user,
                role="assistant",
                content=str(design_result["reply"]),
                event={
                    "design_state": design_result["design_state"],
                    "knowledge_cards": design_result["knowledge_cards"],
                    "design_options": design_result.get("design_options") or [],
                },
            )
            action_response = None
            action_card = design_result.get("action_card")
            if action_card:
                action_response = self.create_action_from_card(
                    conversation_id=conversation_id,
                    current_user=current_user,
                    card=AgentActionCard.model_validate(action_card),
                )
            self._update_conversation_after_message(
                conversation_id=conversation_id,
                content=content,
                action=action_response,
                attachments=normalized_attachments,
            )
            return (
                str(design_result["reply"]),
                action_response,
                None,
            )
        deterministic_result = self._deterministic_workflow_result(conversation.mode, content, active_attachments)
        llm_result = deterministic_result or await self._call_llm_or_fallback(
            conversation=conversation,
            current_user=current_user,
            content=content,
            attachments=active_attachments,
            memories=memories,
        )
        reply = llm_result.get("reply") or self._fallback_reply(conversation.mode, content, active_attachments)
        action_card = llm_result.get("action_card")
        memory_proposal = llm_result.get("memory_proposal")

        action_response = None
        if action_card:
            try:
                action_response = self.create_action_from_card(
                    conversation_id=conversation_id,
                    current_user=current_user,
                    card=AgentActionCard.model_validate(action_card),
                )
            except HTTPException:
                fallback = self._heuristic_agent_result(conversation.mode, content, active_attachments)
                fallback_card = fallback.get("action_card")
                if fallback_card:
                    action_response = self.create_action_from_card(
                        conversation_id=conversation_id,
                        current_user=current_user,
                        card=AgentActionCard.model_validate(fallback_card),
                    )
                    reply = fallback.get("reply") or reply

        self._create_message(
            conversation_id=conversation_id,
            current_user=current_user,
            role="assistant",
            content=reply,
            event={
                "action_id": action_response.id if action_response else None,
                "memory_proposal": memory_proposal,
            },
        )
        self._update_conversation_after_message(
            conversation_id=conversation_id,
            content=content,
            action=action_response,
            attachments=normalized_attachments,
        )

        return (
            reply,
            action_response,
            AgentMemoryProposal.model_validate(memory_proposal) if memory_proposal else None,
        )

    async def stream_user_message(
        self,
        *,
        conversation_id: str,
        current_user: User,
        content: str,
        attachments: list[AgentAssetRef],
    ) -> AsyncIterator[tuple[str, object]]:
        conversation = self._get_conversation(conversation_id, current_user=current_user)
        normalized_attachments = self._normalize_asset_refs(attachments, current_user=current_user)
        active_attachments = normalized_attachments or self._load_recent_assets(conversation)
        self._create_message(
            conversation_id=conversation_id,
            current_user=current_user,
            role="user",
            content=content or "已选择参考图片。",
            attachments=normalized_attachments,
        )

        memories = self.list_memories(current_user=current_user, enabled_only=True)
        if conversation.mode == "design":
            design_attachments = normalized_attachments or self._load_design_source_assets(conversation)
            design_task = asyncio.create_task(self._design_agent_result(
                conversation=conversation,
                current_user=current_user,
                content=content,
                attachments=design_attachments,
            ))
            streamed_reply = ""
            stream_buffer = ""
            try:
                async for delta in self._stream_design_visible_reply(
                    conversation=conversation,
                    content=content,
                    attachments=design_attachments,
                ):
                    streamed_reply += delta
                    stream_buffer += delta
                    chunks, stream_buffer = self._drain_stream_text_buffer(stream_buffer)
                    for chunk in chunks:
                        yield ("message_delta", {"text": chunk})
            except Exception:  # noqa: BLE001
                streamed_reply = ""
                stream_buffer = ""
            if stream_buffer:
                chunks, stream_buffer = self._drain_stream_text_buffer(stream_buffer, final=True)
                for chunk in chunks:
                    yield ("message_delta", {"text": chunk})
            if not design_task.done():
                yield ("option_card_loading", {"message": "正在生成选项卡"})
            design_result = await design_task
            if streamed_reply.strip():
                for chunk in self._chunk_text(f"\n{design_result['reply']}"):
                    yield ("message_delta", {"text": chunk})
            else:
                for chunk in self._chunk_text(str(design_result["reply"])):
                    yield ("message_delta", {"text": chunk})
            yield ("design_state", design_result["design_state"])
            yield ("knowledge_cards", {"items": design_result["knowledge_cards"]})
            if design_result.get("design_options"):
                yield (
                    "design_options",
                    {
                        "items": design_result["design_options"],
                        "question": design_result.get("design_question") or design_result.get("reply") or "请选择一个方向",
                        "source": design_result.get("design_option_source") or "llm",
                    },
                )
            action_response = None
            action_card = design_result.get("action_card")
            if action_card:
                action_response = self.create_action_from_card(
                    conversation_id=conversation_id,
                    current_user=current_user,
                    card=AgentActionCard.model_validate(action_card),
                )
                yield ("action_card", action_response)
            self._create_message(
                conversation_id=conversation_id,
                current_user=current_user,
                role="assistant",
                content=str(design_result["reply"]),
                event={
                    "design_state": design_result["design_state"],
                    "knowledge_cards": design_result["knowledge_cards"],
                    "design_options": design_result.get("design_options") or [],
                },
            )
            self._update_conversation_after_message(
                conversation_id=conversation_id,
                content=content,
                action=action_response,
                attachments=normalized_attachments,
            )
            return
        deterministic_result = self._deterministic_workflow_result(conversation.mode, content, active_attachments)
        if deterministic_result is not None:
            async for event in self._emit_completed_agent_result(
                conversation_id=conversation_id,
                current_user=current_user,
                content=content,
                attachments=normalized_attachments,
                llm_result=deterministic_result,
                mode=conversation.mode,
                active_attachments=active_attachments,
            ):
                yield event
            return

        if not self.settings.agent_llm_api_key:
            llm_result = await self._call_llm_or_fallback(
                conversation=conversation,
                current_user=current_user,
                content=content,
                attachments=active_attachments,
                memories=memories,
            )
            async for event in self._emit_completed_agent_result(
                conversation_id=conversation_id,
                current_user=current_user,
                content=content,
                attachments=normalized_attachments,
                llm_result=llm_result,
                mode=conversation.mode,
                active_attachments=active_attachments,
            ):
                yield event
            return

        llm_result: dict[str, Any] | None = None
        streamed_text = ""
        stream_buffer = ""
        try:
            async for delta in self._stream_llm_response(
                conversation=conversation,
                content=content,
                attachments=active_attachments,
                memories=memories,
            ):
                if isinstance(delta, str):
                    streamed_text += delta
                    stream_buffer += delta
                    chunks, stream_buffer = self._drain_stream_text_buffer(stream_buffer)
                    for chunk in chunks:
                        yield ("message_delta", {"text": chunk})
                else:
                    llm_result = delta
        except Exception:  # noqa: BLE001
            if streamed_text:
                llm_result = {"reply": streamed_text}
            else:
                llm_result = self._heuristic_agent_result(conversation.mode, content, active_attachments)

        if stream_buffer:
            chunks, stream_buffer = self._drain_stream_text_buffer(stream_buffer, final=True)
            for chunk in chunks:
                yield ("message_delta", {"text": chunk})

        if llm_result is None:
            llm_result = {"reply": streamed_text}
        if not (llm_result.get("reply") or "").strip():
            llm_result["reply"] = self._fallback_reply(conversation.mode, content, active_attachments)

        reply = str(llm_result.get("reply") or "")
        remaining_text = reply[len(streamed_text) :] if streamed_text and reply.startswith(streamed_text) else reply if not streamed_text else ""
        for chunk in self._chunk_text(remaining_text):
            yield ("message_delta", {"text": chunk})

        action_response, memory_proposal = self._finalize_agent_result(
            conversation_id=conversation_id,
            current_user=current_user,
            content=content,
            attachments=normalized_attachments,
            llm_result=llm_result,
            mode=conversation.mode,
            active_attachments=active_attachments,
        )
        if action_response is not None:
            yield ("action_card", action_response)
        if memory_proposal is not None:
            yield ("memory_proposal", memory_proposal)

    def create_action_from_card(self, *, conversation_id: str, current_user: User, card: AgentActionCard) -> AgentActionResponse:
        self._validate_action_card(card, current_user=current_user)
        now = datetime.now(timezone.utc)
        action_id = str(uuid4())
        with SessionLocal() as session:
            record = AgentAction(
                id=action_id,
                conversation_id=conversation_id,
                user_id=current_user.id,
                kind=card.kind,
                module_key=card.module_key,
                status="draft",
                title=card.title,
                prompt=card.prompt,
                params_json=self._dump_json(card.params),
                source_asset_ids_json=self._dump_json([item.asset_id for item in card.source_assets if item.asset_id]),
                source_image_urls_json=self._dump_json(self._collect_source_urls(card.source_assets, card.source_image_urls)),
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
        return self._action_to_schema(record)

    def confirm_action(
        self,
        *,
        action_id: str,
        current_user: User,
        payload: AgentActionConfirmRequest,
    ) -> AgentActionConfirmResponse:
        with SessionLocal() as session:
            action = session.get(AgentAction, action_id)
            if action is None or action.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent action not found.")
            if action.status not in {"draft", "failed"}:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该动作已提交或已取消。")

            card = self._action_record_to_card(action)
            if payload.prompt is not None:
                card = card.model_copy(update={"prompt": payload.prompt})
            if payload.params is not None:
                card = card.model_copy(update={"params": payload.params})
            if payload.source_assets is not None:
                card = card.model_copy(update={"source_assets": self._normalize_asset_refs(payload.source_assets, current_user=current_user)})
            if payload.source_image_urls is not None:
                card = card.model_copy(update={"source_image_urls": payload.source_image_urls})

            try:
                self._validate_action_card(card, current_user=current_user)
                accepted = self._submit_action_card(card, current_user=current_user)
            except Exception as exc:  # noqa: BLE001
                action.status = "failed"
                action.error_message = str(exc)[:4000]
                action.updated_at = datetime.now(timezone.utc)
                session.commit()
                raise

            action.status = "submitted"
            action.prompt = card.prompt
            action.params_json = self._dump_json(card.params)
            action.source_asset_ids_json = self._dump_json([item.asset_id for item in card.source_assets if item.asset_id])
            action.source_image_urls_json = self._dump_json(self._collect_source_urls(card.source_assets, card.source_image_urls))
            action.result_job_id = accepted.job_id
            action.error_message = None
            action.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(action)

        return AgentActionConfirmResponse(
            action=self._action_to_schema(action),
            job_id=accepted.job_id,
            status=accepted.status,
            message=accepted.message,
        )

    def list_memories(self, *, current_user: User, enabled_only: bool = False) -> list[AgentUserMemoryResponse]:
        with SessionLocal() as session:
            statement = select(AgentUserMemory).where(AgentUserMemory.user_id == current_user.id)
            if enabled_only:
                statement = statement.where(AgentUserMemory.is_enabled == 1)
            records = session.execute(statement.order_by(desc(AgentUserMemory.updated_at))).scalars().all()
        return [self._memory_to_schema(item) for item in records]

    def create_memory(
        self,
        *,
        current_user: User,
        content: str,
        memory_type: str = "preference",
        source_conversation_id: str | None = None,
    ) -> AgentUserMemoryResponse:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            record = AgentUserMemory(
                id=str(uuid4()),
                user_id=current_user.id,
                memory_type=memory_type,
                content=content.strip(),
                is_enabled=1,
                source_conversation_id=source_conversation_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
        return self._memory_to_schema(record)

    def update_memory(self, *, memory_id: str, current_user: User, is_enabled: bool | None, content: str | None) -> AgentUserMemoryResponse:
        with SessionLocal() as session:
            record = session.get(AgentUserMemory, memory_id)
            if record is None or record.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found.")
            if is_enabled is not None:
                record.is_enabled = 1 if is_enabled else 0
            if content is not None:
                record.content = content.strip()
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
        return self._memory_to_schema(record)

    def delete_memory(self, *, memory_id: str, current_user: User) -> None:
        with SessionLocal() as session:
            record = session.get(AgentUserMemory, memory_id)
            if record is None or record.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found.")
            session.delete(record)
            session.commit()

    def register_generation_result(
        self,
        *,
        conversation_id: str,
        current_user: User,
        module_key: str,
        image_url: str,
        name: str | None = None,
        action_id: str | None = None,
    ) -> AgentAssetRef:
        conversation = self._get_conversation(conversation_id, current_user=current_user)
        result_ref = AgentAssetRef(
            name=name or MODULE_RULES.get(module_key, {}).get("title") or "生成结果",
            storage_url=image_url,
            preview_url=image_url,
        )
        source_refs: list[AgentAssetRef] = []
        action_title = MODULE_RULES.get(module_key, {}).get("title") or name or "生成结果"
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation.id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent conversation not found.")
            action_record = session.get(AgentAction, action_id) if action_id else None
            if action_record is not None and action_record.user_id == current_user.id:
                action_schema = self._action_to_schema(action_record)
                source_refs = self._merge_asset_refs(
                    action_schema.source_assets,
                    [AgentAssetRef(name="参考图", storage_url=url, preview_url=url) for url in action_schema.source_image_urls],
                )
                action_title = action_schema.title
            state = self._load_json(record.state_json) or {}
            state["latest_generated_asset"] = result_ref.model_dump(mode="json")
            state["latest_generated_module"] = module_key
            if action_id:
                state["latest_generated_action_id"] = action_id
            recent_assets = state.get("recent_assets") if isinstance(state.get("recent_assets"), list) else []
            state["recent_assets"] = self._dedupe_asset_ref_payloads([result_ref.model_dump(mode="json"), *recent_assets])[:6]
            record.state_json = self._dump_json(state)
            record.current_stage = module_key
            record.updated_at = datetime.now(timezone.utc)
            existing_result_message = self._find_generation_result_message(
                session=session,
                conversation_id=conversation_id,
                action_id=action_id,
                image_url=image_url,
            )
            session.commit()
        if existing_result_message is None:
            self._create_message(
                conversation_id=conversation_id,
                current_user=current_user,
                role="assistant",
                content=f"已完成{action_title}。可以继续选择下一步。",
                attachments=[result_ref],
                event={
                    "type": "generation_result",
                    "action_id": action_id,
                    "module_key": module_key,
                    "title": action_title,
                    "source_assets": [item.model_dump(mode="json") for item in source_refs],
                    "result_asset": result_ref.model_dump(mode="json"),
                },
            )
        return result_ref

    def end_conversation_turn(self, *, conversation_id: str, current_user: User) -> AgentConversationDetail:
        conversation = self._get_conversation(conversation_id, current_user=current_user)
        reply = "好的，本次对话已结束。需要继续时，可以直接发送新的需求。"
        self._create_message(
            conversation_id=conversation.id,
            current_user=current_user,
            role="user",
            content="结束对话",
            attachments=[],
        )
        self._create_message(
            conversation_id=conversation.id,
            current_user=current_user,
            role="assistant",
            content=reply,
            attachments=[],
            event={"type": "conversation_ended"},
        )
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation.id)
            if record is not None:
                state = self._load_json(record.state_json) or {}
                for key in (
                    "pending_design_options",
                    "pending_design_question",
                    "pending_design_option_source",
                    "pending_design_slot",
                ):
                    state.pop(key, None)
                record.state_json = self._dump_json(state)
                record.summary = self._build_summary(record.summary, "结束对话")
                record.current_stage = "ended"
                record.updated_at = datetime.now(timezone.utc)
                session.commit()
        return self.get_conversation_detail(conversation_id=conversation.id, current_user=current_user)

    async def _call_llm_or_fallback(
        self,
        *,
        conversation: AgentConversation,
        current_user: User,
        content: str,
        attachments: list[AgentAssetRef],
        memories: list[AgentUserMemoryResponse],
    ) -> dict[str, Any]:
        if not self.settings.agent_llm_api_key:
            return self._heuristic_agent_result(conversation.mode, content, attachments)

        system_prompt = self._build_system_prompt(mode=conversation.mode, memories=memories)
        tools = [self._draft_action_tool_schema(), self._ask_followup_tool_schema(), self._propose_memory_tool_schema()]
        payload = {
            "model": self.settings.agent_llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_user_prompt(content=content, attachments=attachments)},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.agent_llm_base_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except Exception:  # noqa: BLE001
            return self._heuristic_agent_result(conversation.mode, content, attachments)

        return self._parse_llm_response(body, conversation.mode, content, attachments)

    async def _stream_llm_response(
        self,
        *,
        conversation: AgentConversation,
        content: str,
        attachments: list[AgentAssetRef],
        memories: list[AgentUserMemoryResponse],
    ) -> AsyncIterator[str | dict[str, Any]]:
        system_prompt = self._build_system_prompt(mode=conversation.mode, memories=memories)
        tools = [self._draft_action_tool_schema(), self._ask_followup_tool_schema(), self._propose_memory_tool_schema()]
        payload = {
            "model": self.settings.agent_llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_user_prompt(content=content, attachments=attachments)},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "stream": True,
        }
        reply_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.settings.agent_llm_base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    raw_data = line.removeprefix("data:").strip()
                    if raw_data == "[DONE]":
                        break
                    try:
                        body = json.loads(raw_data)
                    except json.JSONDecodeError:
                        continue
                    delta = ((body.get("choices") or [{}])[0].get("delta") or {})
                    text = self._extract_stream_delta_text(body)
                    if text:
                        reply_parts.append(text)
                        yield text
                    function_call = delta.get("function_call")
                    if function_call:
                        record = tool_calls.setdefault(0, {"function": {"name": "", "arguments": ""}})
                        function_record = record.setdefault("function", {"name": "", "arguments": ""})
                        if function_call.get("name"):
                            function_record["name"] = function_call["name"]
                        if function_call.get("arguments"):
                            function_record["arguments"] = f"{function_record.get('arguments', '')}{function_call['arguments']}"
                    for call_delta in delta.get("tool_calls") or []:
                        index = int(call_delta.get("index") or 0)
                        record = tool_calls.setdefault(index, {"function": {"name": "", "arguments": ""}})
                        if call_delta.get("id"):
                            record["id"] = call_delta["id"]
                        if call_delta.get("type"):
                            record["type"] = call_delta["type"]
                        function_delta = call_delta.get("function") or {}
                        function_record = record.setdefault("function", {"name": "", "arguments": ""})
                        if function_delta.get("name"):
                            function_record["name"] = function_delta["name"]
                        if function_delta.get("arguments"):
                            function_record["arguments"] = f"{function_record.get('arguments', '')}{function_delta['arguments']}"

        yield self._parse_streamed_llm_result(
            reply="".join(reply_parts),
            tool_calls=[tool_calls[index] for index in sorted(tool_calls)],
            mode=conversation.mode,
            content=content,
            attachments=attachments,
        )

    def _parse_streamed_llm_result(
        self,
        *,
        reply: str,
        tool_calls: list[dict[str, Any]],
        mode: str,
        content: str,
        attachments: list[AgentAssetRef],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"reply": reply}
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue
            if name == "draft_action_card":
                result["action_card"] = args
            elif name == "propose_save_memory":
                result["memory_proposal"] = args
            elif name == "ask_followup":
                result["reply"] = args.get("question") or result["reply"]
        if not result.get("reply"):
            result["reply"] = self._fallback_reply(mode, content, attachments)
        return result

    async def _emit_completed_agent_result(
        self,
        *,
        conversation_id: str,
        current_user: User,
        content: str,
        attachments: list[AgentAssetRef],
        llm_result: dict[str, Any],
        mode: str,
        active_attachments: list[AgentAssetRef],
    ) -> AsyncIterator[tuple[str, object]]:
        reply = llm_result.get("reply") or self._fallback_reply(mode, content, active_attachments)
        for chunk in self._chunk_text(str(reply)):
            yield ("message_delta", {"text": chunk})
        action_response, memory_proposal = self._finalize_agent_result(
            conversation_id=conversation_id,
            current_user=current_user,
            content=content,
            attachments=attachments,
            llm_result={**llm_result, "reply": reply},
            mode=mode,
            active_attachments=active_attachments,
        )
        if action_response is not None:
            yield ("action_card", action_response)
        if memory_proposal is not None:
            yield ("memory_proposal", memory_proposal)

    def _finalize_agent_result(
        self,
        *,
        conversation_id: str,
        current_user: User,
        content: str,
        attachments: list[AgentAssetRef],
        llm_result: dict[str, Any],
        mode: str,
        active_attachments: list[AgentAssetRef],
    ) -> tuple[AgentActionResponse | None, AgentMemoryProposal | None]:
        reply = llm_result.get("reply") or self._fallback_reply(mode, content, active_attachments)
        action_card = llm_result.get("action_card")
        memory_proposal = llm_result.get("memory_proposal")
        action_response = None
        if action_card:
            try:
                action_response = self.create_action_from_card(
                    conversation_id=conversation_id,
                    current_user=current_user,
                    card=AgentActionCard.model_validate(action_card),
                )
            except HTTPException:
                fallback = self._heuristic_agent_result(mode, content, active_attachments)
                fallback_card = fallback.get("action_card")
                if fallback_card:
                    action_response = self.create_action_from_card(
                        conversation_id=conversation_id,
                        current_user=current_user,
                        card=AgentActionCard.model_validate(fallback_card),
                    )
                    reply = fallback.get("reply") or reply

        self._create_message(
            conversation_id=conversation_id,
            current_user=current_user,
            role="assistant",
            content=str(reply),
            event={
                "action_id": action_response.id if action_response else None,
                "memory_proposal": memory_proposal,
            },
        )
        self._update_conversation_after_message(
            conversation_id=conversation_id,
            content=content,
            action=action_response,
            attachments=attachments,
        )
        return action_response, AgentMemoryProposal.model_validate(memory_proposal) if memory_proposal else None

    def _parse_llm_response(self, body: dict[str, Any], mode: str, content: str, attachments: list[AgentAssetRef]) -> dict[str, Any]:
        message = (body.get("choices") or [{}])[0].get("message") or {}
        reply = message.get("content") or self._fallback_reply(mode, content, attachments)
        result: dict[str, Any] = {"reply": reply}
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue
            if name == "draft_action_card":
                result["action_card"] = args
            elif name == "propose_save_memory":
                result["memory_proposal"] = args
            elif name == "ask_followup":
                result["reply"] = args.get("question") or result["reply"]
        return result

    async def _design_agent_result(
        self,
        *,
        conversation: AgentConversation,
        current_user: User,
        content: str,
        attachments: list[AgentAssetRef],
    ) -> dict[str, Any]:
        state = self._load_json(conversation.state_json) or {}
        brief = dict(state.get("design_brief") or {})
        selected_cards = list(state.get("selected_knowledge_cards") or [])
        stone_analysis = state.get("stone_analysis") if isinstance(state.get("stone_analysis"), dict) else None
        pending_slot = str(state.get("pending_design_slot") or "")
        if attachments:
            stone_analysis = await self._analyze_stones_or_fallback(attachments, content)
            brief["stones"] = stone_analysis
            state["design_source_assets"] = [item.model_dump(mode="json") for item in attachments[:1]]
        knowledge_cards = self._search_jewelry_knowledge(content=content, brief=brief, stone_analysis=stone_analysis)
        design_plan = await self._call_design_brief_llm(
            content=content,
            brief=brief,
            stone_analysis=stone_analysis,
            knowledge_cards=knowledge_cards,
            has_design_source=bool(state.get("design_source_assets")),
            pending_slot=pending_slot,
        )
        if design_plan:
            brief = self._merge_llm_design_brief(brief, design_plan.get("design_brief"))
            if stone_analysis:
                brief["stones"] = stone_analysis
            should_generate = bool(design_plan.get("should_generate"))
            missing_slots = self._coerce_slot_list(design_plan.get("missing_slots")) or self._missing_design_slots(brief, stone_analysis)
            next_slot = str(design_plan.get("pending_design_slot") or (missing_slots[0] if missing_slots and not should_generate else "")).strip()
            reply = str(design_plan.get("reply") or "").strip() or self._build_design_reply(brief, stone_analysis, should_generate, missing_slots)
            design_options = self._normalize_design_options(design_plan.get("options"))
        else:
            self._merge_design_content_into_brief(brief, content, pending_slot=pending_slot)
            if self._is_agent_autofill_intent(content):
                self._autofill_design_brief(brief, knowledge_cards[:4], stone_analysis)
            should_generate = self._is_design_generate_intent(content)
            missing_slots = self._missing_design_slots(brief, stone_analysis)
            next_slot = missing_slots[0] if missing_slots and not should_generate else ""
            reply = self._build_design_reply(brief, stone_analysis, should_generate, missing_slots)
            design_options = []
        if should_generate:
            design_options = []
            option_source = "none"
        elif not design_options:
            option_source = "fallback"
            design_options = self._fallback_design_options(next_slot)
        else:
            option_source = "llm"
        state["design_brief"] = brief
        state["stone_analysis"] = stone_analysis
        state["knowledge_cards"] = knowledge_cards
        state["selected_knowledge_cards"] = selected_cards
        state["latest_design_mode"] = "gemstone_design" if state.get("design_source_assets") else "text_to_image"
        state["pending_design_slot"] = next_slot if next_slot in {"category", "concept", "gemstone", "metal", "style", "craft", "scene"} and not should_generate else None
        state["pending_design_options"] = design_options
        state["pending_design_question"] = reply if design_options else None
        state["pending_design_option_source"] = option_source
        self._save_conversation_state(conversation.id, state)

        result: dict[str, Any] = {
            "reply": reply,
            "design_state": self._build_design_state_payload(state),
            "knowledge_cards": knowledge_cards,
            "design_options": design_options,
            "design_question": reply,
            "design_option_source": option_source,
        }
        if should_generate:
            selected_for_prompt = selected_cards or knowledge_cards[:4]
            prompt = await self._build_design_generation_prompt(
                conversation_id=conversation.id,
                brief=brief,
                selected_cards=selected_for_prompt,
                stone_analysis=stone_analysis,
                content=content,
                has_design_source=bool(state.get("design_source_assets")),
            )
            source_assets = [AgentAssetRef.model_validate(item) for item in state.get("design_source_assets") or []]
            if source_assets:
                result["action_card"] = {
                    "kind": "image_to_image",
                    "module_key": "gemstone_design",
                    "title": "裸石镶嵌设计",
                    "prompt": prompt,
                    "params": {"model": DEFAULT_IMAGE_MODEL, "image_size": "1K", "strength": 0.75},
                    "source_assets": [item.model_dump(mode="json") for item in source_assets[:1]],
                    "source_image_urls": [],
                    "editable_prompt": True,
                    "next_question": "生成后可以重新生成、继续修改设计 brief，或结束。",
                }
            else:
                result["action_card"] = {
                    "kind": "text_to_image",
                    "module_key": "text_to_image",
                    "title": "设计出图",
                    "prompt": prompt,
                    "params": {"model": DEFAULT_IMAGE_MODEL, "aspect_ratio": "1:1", "image_size": "1K"},
                    "source_assets": [],
                    "source_image_urls": [],
                    "editable_prompt": True,
                    "next_question": "生成后可以重新生成、继续修改设计 brief，或结束。",
                }
        return result

    def _save_conversation_state(self, conversation_id: str, state: dict[str, Any]) -> None:
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation_id)
            if record is None:
                return
            record.state_json = self._dump_json(state)
            record.updated_at = datetime.now(timezone.utc)
            session.commit()

    def _build_design_state_payload(self, state: dict[str, Any]) -> dict[str, object]:
        return {
            "design_brief": state.get("design_brief") or {},
            "selected_knowledge_cards": state.get("selected_knowledge_cards") or [],
            "stone_analysis": state.get("stone_analysis"),
            "knowledge_cards": state.get("knowledge_cards") or [],
            "latest_design_mode": state.get("latest_design_mode") or "text_to_image",
            "pending_design_options": state.get("pending_design_options") or [],
            "pending_design_question": state.get("pending_design_question"),
            "pending_design_option_source": state.get("pending_design_option_source") or "llm",
        }

    async def _call_design_brief_llm(
        self,
        *,
        content: str,
        brief: dict[str, Any],
        stone_analysis: dict[str, object] | None,
        knowledge_cards: list[dict[str, object]],
        has_design_source: bool,
        pending_slot: str,
    ) -> dict[str, Any] | None:
        if not self.settings.agent_llm_api_key:
            return None
        payload = {
            "model": self.settings.agent_llm_model,
            "messages": [
                {"role": "system", "content": self._build_design_brief_system_prompt()},
                {
                    "role": "user",
                    "content": self._build_design_brief_user_prompt(
                        content=content,
                        brief=brief,
                        stone_analysis=stone_analysis,
                        knowledge_cards=knowledge_cards,
                        has_design_source=has_design_source,
                        pending_slot=pending_slot,
                    ),
                },
            ],
            "temperature": 0.1,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.agent_llm_base_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                message = (response.json().get("choices") or [{}])[0].get("message") or {}
                parsed = self._parse_json_object(str(message.get("content") or ""))
                return parsed if isinstance(parsed, dict) else None
        except Exception:  # noqa: BLE001
            return None

    async def _stream_design_visible_reply(
        self,
        *,
        conversation: AgentConversation,
        content: str,
        attachments: list[AgentAssetRef],
    ) -> AsyncIterator[str]:
        if not self.settings.agent_llm_api_key:
            return
        state = self._load_json(conversation.state_json) or {}
        payload = {
            "model": self.settings.agent_llm_model,
            "messages": [
                {"role": "system", "content": self._build_design_visible_reply_system_prompt()},
                {
                    "role": "user",
                    "content": self._build_design_visible_reply_user_prompt(
                        content=content,
                        state=state,
                        attachments=attachments,
                    ),
                },
            ],
            "temperature": 0.2,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.settings.agent_llm_base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    raw_data = line.removeprefix("data:").strip()
                    if raw_data == "[DONE]":
                        break
                    try:
                        body = json.loads(raw_data)
                    except json.JSONDecodeError:
                        continue
                    text = self._extract_stream_delta_text(body)
                    if text:
                        yield text

    def _build_design_visible_reply_system_prompt(self) -> str:
        return (
            "你是金马珠宝内部的设计出图 Agent。你正在对话窗口直接回复设计师，必须输出自然中文正文。"
            "不要输出 JSON、Markdown 表格、代码块或工具调用。"
            "你的职责只是先给出简短承接，说明正在整理当前 brief 和下一步选择。"
            "不要追问具体设计问题，不要提出具体选项，不要要求用户回答某个槽位；这些会由后续选项卡统一承载。"
            "回复控制在 20-60 个中文字符。"
        )

    def _build_design_visible_reply_user_prompt(
        self,
        *,
        content: str,
        state: dict[str, Any],
        attachments: list[AgentAssetRef],
    ) -> str:
        visible_state = {
            "design_brief": state.get("design_brief") or {},
            "pending_design_slot": state.get("pending_design_slot"),
            "stone_analysis": state.get("stone_analysis"),
            "has_design_source": bool(attachments or state.get("design_source_assets")),
        }
        return (
            f"用户本轮输入：{content or '用户只上传/选择了图片，没有文字'}\n"
            f"本轮是否带图：{bool(attachments)}\n"
            f"当前状态：{json.dumps(visible_state, ensure_ascii=False)}\n"
            "请直接输出会展示给设计师的流式回复正文。"
        )

    def _build_design_brief_system_prompt(self) -> str:
        return (
            "你是金马珠宝内部的设计出图 Agent，负责和设计师问诊并维护结构化设计 brief。\n"
            "你必须只输出 JSON 对象，不要输出 Markdown、解释或代码块。\n"
            "任务：根据用户本轮输入、已有 brief、裸石分析和专业词库建议，更新 brief 槽位，并判断是否应该提交生成。\n"
            "允许槽位：category, concept, gemstone, metal, style, craft, scene, supplement, knowledge_summary。\n"
            "规则：\n"
            "1. 不要把用户的泛指句误当成设计理念。例如“这是我要设计镶嵌的裸石”只表示上传图片是裸石来源，不是 concept。\n"
            "2. 用户短答通常是在回答上一轮 pending_design_slot，例如只说“18k”应填 metal=18K金。\n"
            "3. 只有用户明确要求生成/出图/重新生成/直接生成/开始生成时，should_generate 才为 true；普通修改、补充、讨论不要生成。\n"
            "4. 如果用户要求 Agent 补全，可以基于专业珠宝常识和词库补足缺失槽位，但要保持可制作、专业、克制。\n"
            "5. 每次最多追问一个最关键问题；信息足够时提示可生成首版设计图。\n"
            "6. 如果还需要用户补充，必须给出 2-4 个适合当前问题的选项 options，选项要短、专业、可直接作为用户回答；不要包含“其他”，前端会固定添加。\n"
            "7. 有裸石来源时，生成路线是 gemstone_design；无裸石来源时是 text_to_image，但你只需要返回 latest_design_mode 字段。\n"
            "JSON 格式："
            '{"design_brief": {"category": null, "concept": null, "gemstone": null, "metal": null, "style": null, "craft": null, "scene": null, "supplement": null, "knowledge_summary": null}, '
            '"missing_slots": ["category"], "pending_design_slot": "category", "should_generate": false, '
            '"latest_design_mode": "text_to_image", "reply": "给用户看的中文回复", '
            '"options": [{"label": "吊坠", "value": "吊坠", "description": "适合裸石镶嵌和日常佩戴"}]}'
        )

    def _build_design_brief_user_prompt(
        self,
        *,
        content: str,
        brief: dict[str, Any],
        stone_analysis: dict[str, object] | None,
        knowledge_cards: list[dict[str, object]],
        has_design_source: bool,
        pending_slot: str,
    ) -> str:
        knowledge_text = "\n".join(
            f"- {card.get('category')}: {card.get('content') or card.get('title')}"
            for card in knowledge_cards[:6]
        )
        return (
            f"用户本轮输入：{content or '用户只上传/选择了图片，没有文字'}\n"
            f"是否已有裸石/参考图来源：{has_design_source}\n"
            f"上一轮正在追问的槽位：{pending_slot or '无'}\n"
            f"当前 brief：{json.dumps(brief, ensure_ascii=False)}\n"
            f"裸石视觉分析/降级分析：{json.dumps(stone_analysis or {}, ensure_ascii=False)}\n"
            f"专业词库候选：\n{knowledge_text or '无'}\n"
            "请返回合并后的完整 brief。未知槽位填 null，不要编造用户没有表达且不需要 Agent 补全的内容。"
        )

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(stripped[start : end + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
        return None

    def _merge_llm_design_brief(self, current: dict[str, Any], raw_brief: object) -> dict[str, Any]:
        if not isinstance(raw_brief, dict):
            return current
        allowed = {"category", "concept", "gemstone", "metal", "style", "craft", "scene", "supplement", "knowledge_summary", "stones"}
        merged = dict(current)
        for key, value in raw_brief.items():
            if key not in allowed:
                continue
            if value in (None, "", [], {}):
                continue
            merged[key] = value
        return merged

    def _coerce_slot_list(self, value: object) -> list[str]:
        allowed = {"category", "concept", "gemstone", "metal", "style", "craft", "scene"}
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item) in allowed]

    def _normalize_design_options(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("title") or item.get("value") or "").strip()
            option_value = str(item.get("value") or label).strip()
            description = str(item.get("description") or item.get("helper") or "").strip()
            if not label or not option_value or label in {"其他", "其它"}:
                continue
            items.append({"label": label[:24], "value": option_value[:120], "description": description[:120]})
        return items[:4]

    def _fallback_design_options(self, pending_slot: str) -> list[dict[str, str]]:
        presets: dict[str, list[tuple[str, str]]] = {
            "category": [("吊坠", "适合突出裸石主体"), ("戒指", "更强调佩戴和展示"), ("手链", "适合轻量日常款")],
            "style": [("自然", "强调裸石天然感"), ("复古", "更有装饰性和故事感"), ("几何", "线条清晰，更现代")],
            "metal": [("18K金", "经典稳妥"), ("玫瑰金", "柔和暖调"), ("白金", "清爽现代")],
            "craft": [("爪镶", "露出更多裸石"), ("包镶", "保护性更强"), ("围钻", "提升华丽度")],
            "scene": [("日常佩戴", "克制、耐看"), ("晚宴聚会", "更强调存在感"), ("收藏展示", "突出设计性")],
        }
        return [
            {"label": label, "value": label, "description": description}
            for label, description in presets.get(pending_slot, [])
        ]

    async def _build_design_generation_prompt(
        self,
        *,
        conversation_id: str,
        brief: dict[str, Any],
        selected_cards: list[dict[str, object]],
        stone_analysis: dict[str, object] | None,
        content: str,
        has_design_source: bool,
    ) -> str:
        conversation_context = self._load_recent_message_context(conversation_id)
        llm_prompt = await self._call_design_generation_prompt_llm(
            conversation_context=conversation_context,
            brief=brief,
            selected_cards=selected_cards,
            stone_analysis=stone_analysis,
            content=content,
            has_design_source=has_design_source,
        )
        if llm_prompt:
            return self._ensure_design_front_view_constraint(llm_prompt)
        return self._ensure_design_front_view_constraint(
            self._build_design_prompt(brief=brief, selected_cards=selected_cards, stone_analysis=stone_analysis, content=content)
        )

    async def _call_design_generation_prompt_llm(
        self,
        *,
        conversation_context: str,
        brief: dict[str, Any],
        selected_cards: list[dict[str, object]],
        stone_analysis: dict[str, object] | None,
        content: str,
        has_design_source: bool,
    ) -> str | None:
        if not self.settings.agent_llm_api_key:
            return None
        knowledge_text = "\n".join(
            f"- {card.get('category')}: {self._clean_prompt_fragment(str(card.get('content') or card.get('title') or ''))}"
            for card in selected_cards[:6]
        )
        mode_hint = "裸石镶嵌图生图 gemstone_design" if has_design_source else "纯文本设计出图 text_to_image"
        payload = {
            "model": self.settings.agent_llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是珠宝 AI 生图提示词工程师。你的任务是把设计师对话上下文、结构化 brief、裸石分析和专业词库，"
                        "总结成一段可直接发给生图模型的最终 prompt。\n"
                        "要求：只输出最终 prompt 文本，不要 JSON、Markdown、标题、解释。\n"
                        "不要把专业参考原文、表格、英文术语堆砌进去，要吸收后改写成自然的珠宝设计描述。\n"
                        "prompt 必须结构清晰：主体品类、主石/裸石约束、金属材质、镶嵌工艺、风格语言、比例结构、画面质感。\n"
                        "如果是裸石镶嵌，必须强调：不改变裸石原始形状、颜色、大小比例、天然纹理，以裸石为核心设计镶口和结构。\n"
                        f"必须包含这个硬性构图要求：{DESIGN_FRONT_VIEW_CONSTRAINT}\n"
                        "输出长度控制在 180-420 个中文字符，专业、明确、可生图。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"生成模式：{mode_hint}\n"
                        f"最近对话上下文：\n{conversation_context or '无'}\n"
                        f"当前用户生成指令：{content or '生成首版设计图'}\n"
                        f"结构化 brief：{self._format_design_brief_for_prompt(brief)}\n"
                        f"裸石分析：{self._format_design_brief_for_prompt(stone_analysis or {})}\n"
                        f"可参考的专业知识候选：\n{knowledge_text or '无'}"
                    ),
                },
            ],
            "temperature": 0.25,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.agent_llm_base_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                message = (response.json().get("choices") or [{}])[0].get("message") or {}
                prompt = self._strip_prompt_text(str(message.get("content") or ""))
                return prompt if prompt else None
        except Exception:  # noqa: BLE001
            return None

    def _load_recent_message_context(self, conversation_id: str, limit: int = 12) -> str:
        with SessionLocal() as session:
            messages = session.execute(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation_id)
                .order_by(desc(AgentMessage.created_at))
                .limit(limit)
            ).scalars().all()
        ordered = list(reversed(messages))
        lines: list[str] = []
        for message in ordered:
            content = message.content.strip()
            if not content:
                continue
            role = "设计师" if message.role == "user" else "Agent"
            lines.append(f"{role}: {content[:300]}")
        return "\n".join(lines)

    def _strip_prompt_text(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").strip()
            if stripped.lower().startswith(("text", "prompt", "markdown")):
                stripped = stripped.split("\n", 1)[-1].strip()
        return " ".join(stripped.split())

    def _clean_prompt_fragment(self, text: str) -> str:
        cleaned = text.replace("|", " ").replace("`", " ")
        cleaned = " ".join(cleaned.split())
        return cleaned[:160]

    def _ensure_design_front_view_constraint(self, prompt: str) -> str:
        stripped = prompt.strip()
        if "完整的珠宝设计正视图" in stripped and "不裁切" in stripped:
            return stripped
        return f"{stripped} {DESIGN_FRONT_VIEW_CONSTRAINT}"

    def _merge_design_content_into_brief(self, brief: dict[str, Any], content: str, *, pending_slot: str = "") -> None:
        stripped = content.strip()
        if not stripped or self._is_design_generate_intent(stripped) or self._is_agent_autofill_intent(stripped):
            return
        lowered = stripped.lower()
        if pending_slot in {"category", "concept", "gemstone", "metal", "style", "craft", "scene"} and len(stripped) <= 36:
            brief[pending_slot] = self._normalize_design_slot_value(pending_slot, stripped)
            return
        category = self._first_matching_phrase(stripped, ["戒指", "吊坠", "项链", "耳环", "耳坠", "胸针", "手镯", "手链"])
        if category:
            brief["category"] = category
        style = self._first_matching_phrase(stripped, ["Art Deco", "art deco", "复古", "现代", "中式", "东方", "宫廷", "极简", "自然", "新中式"])
        if style:
            brief["style"] = "Art Deco" if style.lower() == "art deco" else style
        metal = self._first_matching_phrase(stripped, ["18K金", "18k金", "18K", "18k", "玫瑰金", "黄金", "白金", "铂金", "银", "K金"])
        if metal:
            brief["metal"] = "18K金" if metal.lower() in {"18k金", "18k"} else metal
        gemstone = self._first_matching_phrase(stripped, ["祖母绿", "翡翠", "和田玉", "玉", "钻石", "钻", "红宝石", "红宝", "蓝宝石", "蓝宝", "珍珠", "裸石"])
        if gemstone:
            brief["gemstone"] = gemstone
        craft = self._first_matching_phrase(stripped, ["爪镶", "包镶", "围钻", "花丝", "镂空", "雕刻", "微镶", "密镶", "镶嵌"])
        if craft:
            brief["craft"] = craft
        scene = self._first_matching_phrase(stripped, ["日常", "通勤", "婚礼", "礼服", "商务", "收藏", "展会", "晚宴"])
        if scene:
            brief["scene"] = scene
        if lowered not in {"整理设计理念", "生成首版设计图", "优化提示词"}:
            brief["concept"] = stripped
            if not any([category, style, metal, gemstone, craft, scene]):
                previous = str(brief.get("supplement") or "")
                brief["supplement"] = f"{previous}\n{stripped}".strip()

    def _normalize_design_slot_value(self, slot: str, value: str) -> str:
        stripped = value.strip()
        if slot == "metal" and stripped.lower() in {"18k", "18k金"}:
            return "18K金"
        return stripped

    def _first_matching_phrase(self, text: str, phrases: list[str]) -> str | None:
        lowered = text.lower()
        for phrase in phrases:
            if phrase.lower() in lowered:
                return phrase
        return None

    def _is_design_generate_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return any(key in normalized for key in ["生成首版设计图", "生成设计图", "生成方案", "开始生成", "直接生成"])

    def _is_agent_autofill_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return "agent自行补全" in normalized or "agent补全" in normalized or "自动补全" in normalized

    async def _analyze_stones_or_fallback(self, attachments: list[AgentAssetRef], content: str) -> dict[str, object]:
        image_url = attachments[0].storage_url or attachments[0].preview_url if attachments else None
        fallback = {
            "count": len(attachments),
            "shape": "请结合裸石图片确认外形轮廓",
            "color": "请结合裸石图片确认颜色与种水",
            "texture": "保留天然纹理、色带与瑕疵特征",
            "setting_direction": "建议围绕裸石原始轮廓进行爪镶或包镶设计",
            "risk_notes": "不要改变裸石形状、颜色、比例和天然纹理",
            "source": "fallback",
        }
        if not (self.settings.agent_vision_llm_api_key and self.settings.agent_vision_llm_base_url and self.settings.agent_vision_llm_model and image_url):
            return fallback
        payload = {
            "model": self.settings.agent_vision_llm_model,
            "messages": [
                {"role": "system", "content": "你是珠宝裸石设计助理。请只输出 JSON。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "分析这张裸石/玉石图片，输出 count, shape, color, transparency, texture, setting_direction, risk_notes, recommended_style。"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "temperature": 0.1,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.agent_vision_llm_base_url.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.agent_vision_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                text = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
                parsed = json.loads(text.strip().strip("`").removeprefix("json").strip())
                if isinstance(parsed, dict):
                    parsed["source"] = "vision"
                    return parsed
        except Exception:  # noqa: BLE001
            return fallback
        return fallback

    def _search_jewelry_knowledge(self, *, content: str, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> list[dict[str, object]]:
        text = "\n".join(str(item) for item in [content, *brief.values(), *(stone_analysis or {}).values()] if item)
        terms = self._load_jewelry_term_cards()
        scored: list[tuple[int, dict[str, object]]] = []
        for card in terms:
            haystack = f"{card['title']} {card['content']} {card['category']}"
            score = 0
            for token in ["裸石", "玉", "翡翠", "镶", "爪镶", "包镶", "围钻", "花丝", "复古", "吊坠", "戒指", "耳环", "18K", "金"]:
                if token in text and token in haystack:
                    score += 3
            if str(card["category"]) in {"玉石描述", "镶嵌工艺", "工艺细节", "商业摄影描述"}:
                score += 1
            if score > 0:
                scored.append((score, card))
        scored.sort(key=lambda item: item[0], reverse=True)
        cards = [item[1] for item in scored[:10]]
        if cards:
            return cards
        return terms[:8]

    def _load_jewelry_term_cards(self) -> list[dict[str, object]]:
        raw = self._load_jewelry_terms().replace("\n珠宝专业词库节选：\n", "")
        if not raw:
            return []
        category_keywords = [
            ("镶嵌工艺", ["镶", "爪", "包镶", "围钻"]),
            ("玉石描述", ["玉", "翡翠", "种水", "纹理"]),
            ("金属材质", ["金", "银", "铂", "K金"]),
            ("风格", ["复古", "现代", "宫廷", "Art"]),
            ("工艺细节", ["花丝", "雕", "镂空", "抛光"]),
            ("商业摄影描述", ["背景", "渲染", "光影", "摄影"]),
        ]
        cards: list[dict[str, object]] = []
        paragraphs = [item.strip(" -#\t") for item in raw.splitlines() if len(item.strip(" -#\t")) >= 8]
        for index, paragraph in enumerate(paragraphs[:80]):
            category = "专业描述"
            for candidate, words in category_keywords:
                if any(word in paragraph for word in words):
                    category = candidate
                    break
            cards.append(
                {
                    "id": f"term-{index + 1}",
                    "category": category,
                    "title": paragraph[:24],
                    "content": paragraph[:180],
                }
            )
        return cards

    def _apply_knowledge_cards_to_brief(self, brief: dict[str, Any], cards: list[dict[str, object]]) -> None:
        if not cards:
            return
        brief["knowledge_summary"] = "；".join(str(card.get("content") or card.get("title")) for card in cards[:5])

    def _autofill_design_brief(
        self,
        brief: dict[str, Any],
        cards: list[dict[str, object]],
        stone_analysis: dict[str, object] | None,
    ) -> None:
        if stone_analysis:
            brief.setdefault("category", "吊坠")
            brief.setdefault("metal", "18K金")
            recommended_style = stone_analysis.get("recommended_style") if isinstance(stone_analysis, dict) else None
            brief.setdefault("style", str(recommended_style or "现代东方高级珠宝"))
            brief.setdefault("craft", "围绕裸石轮廓进行包镶或爪镶，局部围钻增强层次")
            brief.setdefault("concept", "以裸石天然颜色、纹理和外形为核心，设计可落地的镶嵌成品")
        else:
            brief.setdefault("category", "吊坠")
            brief.setdefault("metal", "18K金")
            brief.setdefault("gemstone", "主石可按设计理念选择")
            brief.setdefault("style", "现代高级珠宝")
            brief.setdefault("craft", "爪镶、包镶或局部围钻")
            brief.setdefault("concept", "结构清晰、比例优雅、适合成品展示的珠宝设计")
        brief.setdefault("scene", "日常佩戴与正式场合")
        self._apply_knowledge_cards_to_brief(brief, cards[:3])

    def _missing_design_slots(self, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> list[str]:
        required = ["category", "concept", "metal", "style", "craft"]
        if not stone_analysis:
            required.insert(2, "gemstone")
        return [item for item in required if not str(brief.get(item) or "").strip()]

    def _next_design_question(self, missing: list[str], stone_analysis: dict[str, object] | None) -> str:
        labels = {
            "category": "想做成什么品类，例如吊坠、戒指、耳环或胸针？",
            "concept": "这件作品想表达什么设计理念或情绪？",
            "gemstone": "主石或宝石希望使用什么？",
            "metal": "金属材质倾向于 18K金、玫瑰金、白金还是银？",
            "style": "风格更偏现代、复古、东方、新中式、极简，还是更商业款？",
            "craft": "工艺上希望偏爪镶、包镶、围钻、花丝、镂空，还是交给 Agent 补全？",
        }
        if not missing:
            return "信息已经足够生成首版设计图。你可以直接点击「生成首版设计图」，也可以继续补充想强调的比例、佩戴场景或商业风格。"
        if stone_analysis and missing[0] == "concept":
            return "我已保留裸石作为核心。请补充这件镶嵌作品的设计理念，或者点击「Agent 补全 brief」让我先给出一版。"
        return labels.get(missing[0], "请继续补充你的设计想法。")

    def _build_design_prompt(
        self,
        *,
        brief: dict[str, Any],
        selected_cards: list[dict[str, object]],
        stone_analysis: dict[str, object] | None,
        content: str,
    ) -> str:
        knowledge_text = "；".join(self._clean_prompt_fragment(str(card.get("content") or card.get("title") or "")) for card in selected_cards[:4])
        stone_text = self._format_design_brief_for_prompt(stone_analysis or {})
        brief_text = self._format_design_brief_for_prompt(brief)
        if stone_analysis:
            return (
                f"{DEFAULT_GEMSTONE_DESIGN_PROMPT}"
                f" 结合当前设计 brief：{brief_text}。"
                f" 裸石特征：{stone_text}。"
                f" 融合专业设计语言：{knowledge_text or '现代高级珠宝镶嵌设计'}。"
                f" 用户补充：{content or '生成首版设计图'}。"
            )
        prompt = (
            f"根据当前设计 brief 生成高级珠宝设计图：{brief_text}。"
            f" 融合专业设计语言：{knowledge_text or '现代高级珠宝设计，结构清晰，比例优雅'}。"
            f" 用户补充：{content or '生成首版设计图'}。"
            f"{DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX}"
        )
        return prompt

    def _format_design_brief_for_prompt(self, data: dict[str, Any]) -> str:
        labels = {
            "category": "品类",
            "concept": "设计理念",
            "gemstone": "主石/宝石",
            "stones": "裸石信息",
            "metal": "材质",
            "style": "风格",
            "craft": "工艺",
            "scene": "佩戴/使用场景",
            "supplement": "补充要求",
            "knowledge_summary": "专业参考摘要",
            "count": "裸石数量",
            "shape": "形状",
            "color": "颜色",
            "transparency": "透明度/种水",
            "texture": "纹理",
            "setting_direction": "镶嵌方向",
            "risk_notes": "设计风险",
            "recommended_style": "推荐风格",
        }
        lines: list[str] = []
        for key, value in data.items():
            if key == "source" or value in (None, "", [], {}):
                continue
            if isinstance(value, dict):
                rendered_value = "；".join(
                    f"{labels.get(str(child_key), str(child_key))}: {child_value}"
                    for child_key, child_value in value.items()
                    if child_key != "source" and child_value not in (None, "", [], {})
                )
            else:
                rendered_value = str(value)
            if rendered_value:
                lines.append(f"- {labels.get(str(key), str(key))}: {rendered_value}")
        return "\n".join(lines) or "- 设计信息不足，由 Agent 按高级珠宝设计常识补全"

    def _build_design_reply(
        self,
        brief: dict[str, Any],
        stone_analysis: dict[str, object] | None,
        should_generate: bool,
        missing: list[str],
    ) -> str:
        if should_generate:
            mode_text = "裸石镶嵌设计" if stone_analysis else "设计出图"
            return f"收到，我会基于当前 brief 提交{mode_text}任务。生成完成后，只围绕这张设计图继续重做或修改 brief。"
        summary = self._format_design_brief_for_chat(brief, stone_analysis)
        next_question = self._next_design_question(missing, stone_analysis)
        if stone_analysis:
            return f"我已把裸石作为设计核心，并更新当前 brief：\n\n{summary}\n\n{next_question}"
        return f"我已更新当前设计 brief：\n\n{summary}\n\n{next_question}"

    def _format_design_brief_for_chat(self, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> str:
        labels = [
            ("category", "品类"),
            ("concept", "设计理念"),
            ("gemstone", "主石/裸石"),
            ("metal", "材质"),
            ("style", "风格"),
            ("craft", "工艺"),
            ("scene", "场景"),
        ]
        lines: list[str] = []
        for key, label in labels:
            value = brief.get(key)
            if key == "gemstone" and stone_analysis:
                value = "已绑定裸石图片"
            if value:
                lines.append(f"- {label}：{value}")
        if not lines:
            return "- 暂未形成明确 brief"
        return "\n".join(lines)

    def _load_design_source_assets(self, conversation: AgentConversation) -> list[AgentAssetRef]:
        state = self._load_json(conversation.state_json) or {}
        items = state.get("design_source_assets")
        if not isinstance(items, list):
            return []
        refs: list[AgentAssetRef] = []
        for item in items:
            try:
                refs.append(AgentAssetRef.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
        return refs

    def _deterministic_workflow_result(self, mode: str, content: str, attachments: list[AgentAssetRef]) -> dict[str, Any] | None:
        if mode != "workflow":
            return None
        if self._is_end_flow_intent(content):
            return {"reply": "好的，本轮流程已结束。需要继续时，可以重新上传线稿或从当前结果继续选择下一步。"}
        if not attachments:
            return None
        if not (self._is_image_only_intent(content) or self._is_card_action_intent(content)):
            return None
        if self._is_agent_planned_refine_intent(content):
            return None
        module_key = self._resolve_clear_workflow_module(content)
        if module_key is None:
            return None
        return self._build_workflow_action_result(module_key, content, attachments)

    def _is_end_flow_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return normalized in {"结束", "结束对话", "完成", "先这样", "不用了", "暂不继续"}

    def _is_image_only_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return not normalized or normalized in {"已选择参考图片。", "已选择参考图片", "已上传参考图片。", "已上传参考图片"}

    def _is_card_action_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return normalized in {
            "直接精修",
            "生成多视图",
            "重新生成多视图",
            "生成灰度图",
            "重新生成灰度图",
            "重新生成写实图",
        }

    def _is_agent_planned_refine_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return normalized.startswith("agent精修") or normalized.startswith("ai精修")

    def _resolve_clear_workflow_module(self, content: str) -> str | None:
        normalized = content.strip().lower().replace(" ", "")
        if not normalized or normalized in {"已选择参考图片。", "已选择参考图片", "已上传参考图片。", "已上传参考图片"}:
            return "sketch_to_realistic"
        if normalized in {"1", "一", "第1", "第一", "选1"}:
            return "sketch_to_realistic"
        if "多视图" in content or "四视图" in content:
            return "multi_view"
        if "灰度" in content or "立体化" in content or "立体" in content:
            return "grayscale_relief"
        if "精修" in content or "修一下" in content or "优化" in content:
            return "product_refine"
        if "线稿转写实" in content or "线稿" in content or "写实图" in content or "生成写实" in content or "转写实" in content:
            return "sketch_to_realistic"
        return None

    def _heuristic_agent_result(self, mode: str, content: str, attachments: list[AgentAssetRef]) -> dict[str, Any]:
        text = content.lower()
        if mode == "design":
            prompt = content.strip()
            if prompt and DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX not in prompt:
                prompt = f"{prompt}，{DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX}"
            if prompt:
                return {
                    "reply": "我已整理好设计出图参数，会直接提交首版设计图生成；如果还想补充品类、宝石、金属或风格，也可以继续告诉我。",
                    "action_card": {
                        "kind": "text_to_image",
                        "module_key": "text_to_image",
                        "title": "设计出图",
                        "prompt": prompt,
                        "params": {"model": DEFAULT_IMAGE_MODEL, "aspect_ratio": "1:1", "image_size": "1K"},
                        "source_assets": [],
                        "source_image_urls": [],
                        "editable_prompt": True,
                        "next_question": "生成后我可以继续帮你做精修或多视图。",
                    },
                }
            return {"reply": "请先告诉我设计品类、主石、金属材质、风格和希望呈现的设计理念，我会整理成可执行的文生图方案。"}

        module_key = self._resolve_clear_workflow_module(content)
        if module_key is None and attachments:
            module_key = "sketch_to_realistic"
        if module_key is not None:
            return self._build_workflow_action_result(module_key, content, attachments)
        else:
            return {"reply": "请上传或选择线稿/当前结果图。我可以帮你走线稿转写实、精修、多视图或灰度图。你点选项后，我会直接提交对应生成任务。"}

    def _build_workflow_action_result(self, module_key: str, content: str, attachments: list[AgentAssetRef]) -> dict[str, Any]:
        rule = MODULE_RULES[module_key]
        reply_map = {
            "sketch_to_realistic": "收到，我会按默认线稿转写实提示词直接提交写实图生成。生成完成后再给你下一步选择。",
            "product_refine": "收到，我会基于原始线稿和当前结果图直接提交产品精修任务。",
            "multi_view": "收到，我会基于最新生成图直接提交多视图生成任务。",
            "grayscale_relief": "收到，我会基于最新生成图直接提交灰度立体化任务。",
        }
        prompt = rule.get("default_prompt") or content
        if module_key == "product_refine":
            prompt = self._build_product_refine_prompt(content)
        source_assets = attachments if module_key == "product_refine" else attachments[:1]
        return {
            "reply": reply_map.get(module_key) or self._fallback_reply("workflow", content, attachments),
            "action_card": {
                "kind": rule["kind"],
                "module_key": module_key,
                "title": rule["title"],
                "prompt": prompt,
                "params": {"model": self._default_model_for_module(module_key), "image_size": "2K" if module_key == "grayscale_relief" else "1K"},
                "source_assets": [item.model_dump(mode="json") for item in source_assets],
                "source_image_urls": [],
                "editable_prompt": bool(rule["editable_prompt"]),
                "next_question": "生成后我会继续给出精修、多视图或结束等选项。",
            },
        }

    def _build_product_refine_prompt(self, content: str) -> str:
        stripped = content.strip()
        if not stripped or stripped in {"产品精修", "直接精修", "Agent精修", "Agent精修：", "Agent精修:"}:
            return PRODUCT_REFINE_DEFAULT_PROMPT
        for prefix in ("Agent精修：", "Agent精修:", "AI精修：", "AI精修:"):
            if stripped.startswith(prefix):
                custom_prompt = stripped[len(prefix) :].strip()
                if custom_prompt:
                    if self._is_remove_selected_refine_intent(custom_prompt):
                        return PRODUCT_REFINE_REMOVE_SELECTED_PROMPT
                    return f"{PRODUCT_REFINE_DEFAULT_PROMPT}\n用户补充要求：{custom_prompt}"
                return PRODUCT_REFINE_DEFAULT_PROMPT
        for prefix in ("仅自定义精修：", "仅自定义精修:", "自定义精修：", "自定义精修:"):
            if stripped.startswith(prefix):
                custom_prompt = stripped[len(prefix) :].strip()
                return custom_prompt or PRODUCT_REFINE_DEFAULT_PROMPT
        if stripped.startswith("产品精修：") or stripped.startswith("产品精修:"):
            custom_prompt = stripped.split("：", 1)[-1] if "：" in stripped else stripped.split(":", 1)[-1]
            custom_prompt = custom_prompt.strip()
            if custom_prompt:
                if self._is_remove_selected_refine_intent(custom_prompt):
                    return PRODUCT_REFINE_REMOVE_SELECTED_PROMPT
                return f"{PRODUCT_REFINE_DEFAULT_PROMPT}\n用户补充要求：{custom_prompt}"
        if self._is_remove_selected_refine_intent(stripped):
            return PRODUCT_REFINE_REMOVE_SELECTED_PROMPT
        return f"{PRODUCT_REFINE_DEFAULT_PROMPT}\n用户补充要求：{stripped}"

    def _is_remove_selected_refine_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return any(
            keyword in normalized
            for keyword in (
                "删除选中内容",
                "移除选中内容",
                "删除标注",
                "移除标注",
                "删除圈选",
                "移除圈选",
                "去掉标注",
                "去掉圈选",
            )
        )

    def _fallback_reply(self, mode: str, content: str, attachments: list[AgentAssetRef]) -> str:
        if mode == "design":
            return "我会先把你的设计理念整理成专业珠宝提示词，再直接提交设计出图任务。"
        if attachments:
            return "我已收到参考图。你点选项后，我会直接提交对应生成任务。"
        return "请先描述你想调整的方向，或上传/选择参考图。我会引导你选择重做、精修、多视图或灰度图。"

    def _submit_action_card(self, card: AgentActionCard, *, current_user: User):
        params = dict(card.params)
        model = str(params.get("model") or self._default_model_for_module(card.module_key))
        prompt = card.prompt or ""
        source_urls = self._collect_source_urls(card.source_assets, card.source_image_urls)
        source_names = [item.name or f"reference-{index + 1}.png" for index, item in enumerate(card.source_assets)]

        if card.module_key == "text_to_image":
            request = TextToImageRequest(
                prompt=prompt,
                model=model,
                aspect_ratio=str(params.get("aspect_ratio") or "1:1"),
                size=str(params.get("size") or "1024x1024"),
                image_size=str(params.get("image_size") or "1K"),
            )
            return self.job_service.enqueue_job(
                current_user=current_user,
                feature_key="text_to_image",
                model=model,
                prompt=prompt,
                request_payload={"request": request.model_dump(mode="json")},
            )

        if card.module_key == "multi_view_split":
            payload = MultiViewSplitRequest(
                image_url=source_urls[0],
                source_image_name=source_names[0] if source_names else None,
                model="multi_view_split",
                split_x_ratio=float(params.get("split_x_ratio") or 0.5),
                split_y_ratio=float(params.get("split_y_ratio") or 0.5),
                gap_x_ratio=float(params.get("gap_x_ratio") or 0.0),
                gap_y_ratio=float(params.get("gap_y_ratio") or 0.0),
            )
            return self.job_service.enqueue_job(
                current_user=current_user,
                feature_key="multi_view_split",
                model=payload.model,
                prompt="Split four-grid multi-view image into separate view assets.",
                request_payload={"payload": payload.model_dump(mode="json")},
            )

        metadata = ReferenceImageRequestMetadata(
            model=model,
            prompt=prompt,
            negative_prompt=params.get("negative_prompt") if isinstance(params.get("negative_prompt"), str) else None,
            feature=card.module_key,
            strength=float(params.get("strength") or 0.75),
            image_size=str(params.get("image_size") or "1K"),
            image_count=len(source_urls),
            filename=source_names[0] if source_names else "reference.png",
            filenames=source_names,
        )
        return self.job_service.enqueue_job(
            current_user=current_user,
            feature_key=card.module_key,
            model=model,
            prompt=prompt,
            request_payload={"metadata": metadata.model_dump(mode="json"), "source_image_urls": source_urls},
        )

    def _validate_action_card(self, card: AgentActionCard, *, current_user: User) -> None:
        rule = MODULE_RULES.get(card.module_key)
        if rule is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent action module is not allowed.")
        if card.kind != rule["kind"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent action kind does not match module.")
        if not rule["editable_prompt"] and card.prompt and card.prompt != rule.get("default_prompt"):
            card.prompt = rule.get("default_prompt")
        if not card.prompt and rule.get("default_prompt") is not None:
            card.prompt = rule.get("default_prompt")
        params = dict(card.params)
        if card.module_key == "sketch_to_realistic":
            params["model"] = DEFAULT_SKETCH_TO_REALISTIC_MODEL
        elif not params.get("model"):
            params["model"] = self._default_model_for_module(card.module_key)
        card.params = params
        source_urls = self._collect_source_urls(card.source_assets, card.source_image_urls)
        if len(source_urls) < int(rule["min_images"]):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{rule['title']} 至少需要 {rule['min_images']} 张参考图。")
        for url in source_urls:
            self.asset_service.ensure_storage_url_access(storage_url=url, current_user=current_user)

    def _default_model_for_module(self, module_key: str) -> str:
        if module_key == "sketch_to_realistic":
            return DEFAULT_SKETCH_TO_REALISTIC_MODEL
        return DEFAULT_IMAGE_MODEL

    def _normalize_asset_refs(self, attachments: list[AgentAssetRef], *, current_user: User) -> list[AgentAssetRef]:
        normalized: list[AgentAssetRef] = []
        with SessionLocal() as session:
            for item in attachments:
                if item.asset_id:
                    record = session.get(AssetRecordModel, item.asset_id)
                    if record is None:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
                    self.asset_service.ensure_storage_url_access(storage_url=record.storage_url, current_user=current_user)
                    normalized.append(
                        AgentAssetRef(
                            asset_id=record.id,
                            name=record.name,
                            storage_url=record.storage_url,
                            preview_url=self.asset_service.build_asset_content_url(record.storage_url, record.name),
                        )
                    )
                elif item.storage_url or item.preview_url:
                    url = item.storage_url or item.preview_url
                    if url:
                        self.asset_service.ensure_storage_url_access(storage_url=url, current_user=current_user)
                    normalized.append(item)
        return normalized

    def _load_recent_assets(self, conversation: AgentConversation) -> list[AgentAssetRef]:
        state = self._load_json(conversation.state_json) or {}
        refs: list[AgentAssetRef] = []
        latest_generated = state.get("latest_generated_asset")
        if isinstance(latest_generated, dict):
            refs.append(AgentAssetRef.model_validate(latest_generated))
        recent_assets = state.get("recent_assets")
        if not isinstance(recent_assets, list):
            return refs
        for item in recent_assets:
            if isinstance(item, dict):
                refs.append(AgentAssetRef.model_validate(item))
        return self._merge_asset_refs(refs, [])

    def _dedupe_asset_ref_payloads(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payloads:
            key = str(item.get("asset_id") or item.get("storage_url") or item.get("preview_url") or item.get("name") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _merge_asset_refs(self, primary: list[AgentAssetRef], secondary: list[AgentAssetRef]) -> list[AgentAssetRef]:
        merged: list[AgentAssetRef] = []
        seen: set[str] = set()
        for item in [*primary, *secondary]:
            key = item.asset_id or item.storage_url or item.preview_url or item.name
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:6]

    def _collect_source_urls(self, assets: list[AgentAssetRef], extra_urls: list[str]) -> list[str]:
        urls: list[str] = []
        for item in assets:
            url = item.storage_url or item.preview_url
            if url and url not in urls:
                urls.append(url)
        for url in extra_urls:
            if url and url not in urls:
                urls.append(url)
        return urls

    def _get_conversation(self, conversation_id: str, *, current_user: User) -> AgentConversation:
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation_id)
            if record is None or record.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent conversation not found.")
            session.expunge(record)
        return record

    def _create_message(
        self,
        *,
        conversation_id: str,
        current_user: User,
        role: str,
        content: str,
        attachments: list[AgentAssetRef] | None = None,
        event: dict[str, object] | None = None,
    ) -> AgentMessage:
        with SessionLocal() as session:
            created_at = self._next_message_timestamp(session, conversation_id)
            record = AgentMessage(
                id=str(uuid4()),
                conversation_id=conversation_id,
                user_id=current_user.id,
                role=role,
                content=content,
                attachments_json=self._dump_json([item.model_dump(mode="json") for item in (attachments or [])]),
                event_json=self._dump_json(event),
                created_at=created_at,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def _find_generation_result_message(
        self,
        *,
        session: Any,
        conversation_id: str,
        action_id: str | None,
        image_url: str,
    ) -> AgentMessage | None:
        records = session.execute(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation_id)
            .where(AgentMessage.role == "assistant")
        ).scalars().all()
        for record in records:
            event = self._load_json(record.event_json)
            if not isinstance(event, dict) or event.get("type") != "generation_result":
                continue
            result_asset = event.get("result_asset")
            result_url = result_asset.get("preview_url") or result_asset.get("storage_url") if isinstance(result_asset, dict) else None
            if action_id and event.get("action_id") == action_id:
                return record
            if isinstance(result_url, str) and result_url == image_url:
                return record
        return None

    def _next_message_timestamp(self, session: Any, conversation_id: str) -> datetime:
        now = datetime.now(timezone.utc)
        latest = session.execute(
            select(AgentMessage.created_at)
            .where(AgentMessage.conversation_id == conversation_id)
            .order_by(desc(AgentMessage.created_at))
            .limit(1)
        ).scalar_one_or_none()
        latest = self._normalize_datetime(latest) if latest else None
        if latest and now <= latest:
            return latest + timedelta(seconds=1)
        return now

    def _update_conversation_after_message(
        self,
        *,
        conversation_id: str,
        content: str,
        action: AgentActionResponse | None,
        attachments: list[AgentAssetRef],
    ) -> None:
        with SessionLocal() as session:
            record = session.get(AgentConversation, conversation_id)
            if record is None:
                return
            state = self._load_json(record.state_json) or {}
            if attachments:
                state["recent_assets"] = [item.model_dump(mode="json") for item in attachments]
            if action:
                state["last_action_id"] = action.id
                record.current_stage = action.module_key
            record.state_json = self._dump_json(state)
            record.summary = self._build_summary(record.summary, content)
            record.updated_at = datetime.now(timezone.utc)
            session.commit()

    def _default_conversation_title(self, mode: str, value: datetime) -> str:
        label = "设计出图" if mode == "design" else "agent工作流"
        local_value = value.astimezone()
        return f"{label}_{local_value.strftime('%Y%m%d_%H%M%S')}"

    def _build_summary(self, previous: str | None, content: str) -> str | None:
        if not content.strip():
            return previous
        base = previous or ""
        next_summary = f"{base}\n用户：{content.strip()}".strip()
        return next_summary[-2000:]

    def _conversation_to_schema(self, record: AgentConversation) -> AgentConversationResponse:
        return AgentConversationResponse(
            id=record.id,
            mode=record.mode,  # type: ignore[arg-type]
            title=record.title,
            current_stage=record.current_stage,
            status=record.status,
            summary=record.summary,
            state=self._load_json(record.state_json),
            created_at=self._normalize_datetime(record.created_at),
            updated_at=self._normalize_datetime(record.updated_at),
        )

    def _message_to_schema(self, record: AgentMessage) -> AgentMessageResponse:
        return AgentMessageResponse(
            id=record.id,
            conversation_id=record.conversation_id,
            role=record.role,  # type: ignore[arg-type]
            content=record.content,
            attachments=[AgentAssetRef.model_validate(item) for item in (self._load_json(record.attachments_json) or [])],
            event=self._load_json(record.event_json),
            created_at=self._normalize_datetime(record.created_at),
        )

    def _action_to_schema(self, record: AgentAction) -> AgentActionResponse:
        asset_ids = self._load_json(record.source_asset_ids_json) or []
        source_urls = self._load_json(record.source_image_urls_json) or []
        source_assets = self._asset_refs_from_ids(asset_ids)
        return AgentActionResponse(
            id=record.id,
            conversation_id=record.conversation_id,
            kind=record.kind,  # type: ignore[arg-type]
            module_key=record.module_key,
            status=record.status,  # type: ignore[arg-type]
            title=record.title,
            prompt=record.prompt,
            params=self._load_json(record.params_json) or {},
            source_assets=source_assets,
            source_image_urls=[url for url in source_urls if url not in {item.storage_url for item in source_assets}],
            result_job_id=record.result_job_id,
            error_message=record.error_message,
            created_at=self._normalize_datetime(record.created_at),
            updated_at=self._normalize_datetime(record.updated_at),
        )

    def _action_record_to_card(self, record: AgentAction) -> AgentActionCard:
        response = self._action_to_schema(record)
        rule = MODULE_RULES[record.module_key]
        return AgentActionCard(
            id=record.id,
            kind=response.kind,
            module_key=response.module_key,
            title=response.title,
            prompt=response.prompt,
            params=response.params,
            source_assets=response.source_assets,
            source_image_urls=response.source_image_urls,
            editable_prompt=bool(rule["editable_prompt"]),
        )

    def _asset_refs_from_ids(self, asset_ids: list[str]) -> list[AgentAssetRef]:
        if not asset_ids:
            return []
        refs: list[AgentAssetRef] = []
        with SessionLocal() as session:
            records = session.execute(select(AssetRecordModel).where(AssetRecordModel.id.in_(asset_ids))).scalars().all()
        for record in records:
            refs.append(
                AgentAssetRef(
                    asset_id=record.id,
                    name=record.name,
                    storage_url=record.storage_url,
                    preview_url=self.asset_service.build_asset_content_url(record.storage_url, record.name),
                )
            )
        return refs

    def _memory_to_schema(self, record: AgentUserMemory) -> AgentUserMemoryResponse:
        return AgentUserMemoryResponse(
            id=record.id,
            memory_type=record.memory_type,
            content=record.content,
            is_enabled=bool(record.is_enabled),
            source_conversation_id=record.source_conversation_id,
            created_at=self._normalize_datetime(record.created_at),
            updated_at=self._normalize_datetime(record.updated_at),
        )

    def _build_system_prompt(self, *, mode: str, memories: list[AgentUserMemoryResponse]) -> str:
        mode_label = "设计出图" if mode == "design" else "流程助手"
        memory_text = "\n".join(f"- {item.content}" for item in memories[:20]) or "无"
        terms = self._load_jewelry_terms() if mode == "design" else ""
        return (
            f"你是金马珠宝内部 AI Agent，当前模式：{mode_label}。\n"
            "你只能生成内部动作卡，不能绕过后端白名单直接执行生图。用户点击选项卡即视为确认，前端会自动提交动作。\n"
            "流程助手用于线稿转写实、产品精修、多视图、灰度图；设计出图用于整理设计理念并文生图。\n"
            "流程助手中，如果用户上传图片且没有输入额外需求，系统会默认线稿转写实；如果用户同时输入了需求，"
            "你必须根据需求判断模块：多视图/四视图调用 multi_view，灰度/立体化调用 grayscale_relief，精修/优化调用 product_refine，"
            "线稿写实/写实图调用 sketch_to_realistic，并把用户额外要求合理写入可编辑模块的提示词。\n"
            "当用户输入以“Agent精修”开头时，必须生成 product_refine 动作卡。你需要根据用户补充要求整理最终精修提示词，"
            "并从图片引用中自行选择 source_assets：通常当前生成图必选；如果用户要求保持原设计结构、修正偏离线稿、补回设计细节，"
            "则同时带上原始线稿/输入图；如果只是材质、光影、清晰度、背景等局部优化，可以只带当前生成图。\n"
            "当用户输入“Agent精修：删除选中内容”或表达删除/移除圈选标注区域时，必须直接使用局部删除模板语义："
            f"{PRODUCT_REFINE_REMOVE_SELECTED_PROMPT}\n"
            "当用户输入其它局部修改要求时，需要先理解标注图和文字意图，把要求增强成清晰可执行的产品精修 prompt，"
            "强调只修改标注区域，未标注区域保持不变。\n"
            f"用户长期偏好：\n{memory_text}\n"
            f"{terms}"
        )

    def _load_jewelry_terms(self) -> str:
        terms_path = __import__("pathlib").Path(__file__).resolve().parents[3] / "docx" / "珠宝行业专业名词与描述语大全.md"
        if not terms_path.exists():
            return ""
        return "\n珠宝专业词库节选：\n" + terms_path.read_text(encoding="utf-8")[:6000]

    def _build_user_prompt(self, *, content: str, attachments: list[AgentAssetRef]) -> str:
        refs = "\n".join(
            f"{index + 1}. {json.dumps(item.model_dump(mode='json'), ensure_ascii=False)}" for index, item in enumerate(attachments)
        ) or "无"
        return f"用户输入：{content}\n图片引用（可直接复制到动作卡 source_assets）：\n{refs}"

    def _draft_action_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "draft_action_card",
                "description": "生成一个内部生图动作卡，由后端校验并由前端自动提交。",
                "parameters": AgentActionCard.model_json_schema(),
            },
        }

    def _ask_followup_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "ask_followup",
                "description": "当信息不足时向用户追问。",
                "parameters": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
            },
        }

    def _propose_memory_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "propose_save_memory",
                "description": "提出一条可由用户确认保存的长期偏好。",
                "parameters": AgentMemoryProposal.model_json_schema(),
            },
        }

    def _chunk_text(self, value: str, size: int = 18) -> list[str]:
        if not value:
            return []
        chunks: list[str] = []
        buffer = value
        while buffer:
            next_chunks, buffer = self._drain_stream_text_buffer(buffer, final=True, max_size=size * 2)
            if not next_chunks:
                break
            chunks.extend(next_chunks)
        return chunks

    def _extract_stream_delta_text(self, body: dict[str, Any]) -> str:
        choice = (body.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        text = delta.get("content")
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            parts: list[str] = []
            for item in text:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        fallback_text = choice.get("text")
        return fallback_text if isinstance(fallback_text, str) else ""

    def _drain_stream_text_buffer(
        self,
        buffer: str,
        *,
        final: bool = False,
        min_size: int = 4,
        max_size: int = 18,
    ) -> tuple[list[str], str]:
        chunks: list[str] = []
        while buffer:
            if not final and len(buffer) < min_size:
                break
            split_at = self._find_stream_split_index(buffer, final=final, min_size=min_size, max_size=max_size)
            if split_at is None:
                break
            chunk = buffer[:split_at]
            buffer = buffer[split_at:]
            if chunk:
                chunks.append(chunk)
        return chunks, buffer

    def _find_stream_split_index(
        self,
        buffer: str,
        *,
        final: bool,
        min_size: int,
        max_size: int,
    ) -> int | None:
        if final and len(buffer) <= max_size:
            return len(buffer)

        soft_limit = min(len(buffer), max_size)
        punctuation = "。！？!?；;：:\n"
        for index in range(soft_limit - 1, min_size - 2, -1):
            if buffer[index] in punctuation:
                return index + 1

        whitespace_index = -1
        for index in range(soft_limit - 1, min_size - 2, -1):
            if buffer[index].isspace():
                whitespace_index = index + 1
                break
        if whitespace_index > 0:
            return whitespace_index

        if len(buffer) >= max_size:
            return max_size
        if final:
            return len(buffer)
        return None

    def _dump_json(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def _load_json(self, value: str | None) -> Any:
        if not value:
            return None
        return json.loads(value)

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
