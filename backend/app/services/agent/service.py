from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
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
from app.services.agent import (
    DEFAULT_GEMSTONE_DESIGN_PROMPT,
    DEFAULT_GEMSTONE_DESIGN_MODEL,
    DEFAULT_GRAYSCALE_RELIEF_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_REGENERATE_MODEL,
    DEFAULT_MULTI_VIEW_MODEL,
    DEFAULT_SKETCH_TO_REALISTIC_MODEL,
    DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX,
    DESIGN_FRONT_VIEW_CONSTRAINT,
    GRAYSCALE_PROMPT,
    MODULE_RULES,
    MULTI_VIEW_PROMPT,
    PRODUCT_REFINE_DEFAULT_PROMPT,
    PRODUCT_REFINE_REMOVE_SELECTED_PROMPT,
    SKETCH_TO_REALISTIC_PROMPT,
)
from app.services.asset_service import AssetService
from app.services.job_queue_service import JobQueueService


logger = logging.getLogger(__name__)


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
        self._ensure_conversation_active(conversation)
        normalized_attachments = self._normalize_asset_refs(attachments, current_user=current_user)
        active_attachments = self._resolve_workflow_active_attachments(
            conversation=conversation,
            content=content,
            normalized_attachments=normalized_attachments,
        )
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
        self._ensure_conversation_active(conversation)
        normalized_attachments = self._normalize_asset_refs(attachments, current_user=current_user)
        active_attachments = self._resolve_workflow_active_attachments(
            conversation=conversation,
            content=content,
            normalized_attachments=normalized_attachments,
        )
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
            design_state = self._load_json(conversation.state_json) or {}
            direct_design_generate = self._is_ready_generate_design_option(content) and self._design_brief_has_generation_context(
                dict(design_state.get("design_brief") or {}),
                design_state.get("stone_analysis") if isinstance(design_state.get("stone_analysis"), dict) else None,
            )
            turn_started = perf_counter()
            design_task = asyncio.create_task(self._design_agent_result(
                conversation=conversation,
                current_user=current_user,
                content=content,
                attachments=design_attachments,
                analyze_attachments=bool(normalized_attachments),
            ))
            streamed_reply = ""
            stream_buffer = ""
            if direct_design_generate:
                streamed_reply = self._build_design_immediate_reply(
                    content=content,
                    attachments=design_attachments,
                    has_new_attachments=bool(normalized_attachments),
                )
                for chunk in self._chunk_text(streamed_reply):
                    yield ("message_delta", {"text": chunk})
            else:
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
                if not streamed_reply.strip():
                    streamed_reply = self._build_design_immediate_reply(
                        content=content,
                        attachments=design_attachments,
                        has_new_attachments=bool(normalized_attachments),
                    )
                    for chunk in self._chunk_text(streamed_reply):
                        yield ("message_delta", {"text": chunk})
                elif stream_buffer:
                    chunks, stream_buffer = self._drain_stream_text_buffer(stream_buffer, final=True)
                    for chunk in chunks:
                        yield ("message_delta", {"text": chunk})
            if not design_task.done() and not direct_design_generate:
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
            logger.info(
                "agent_design_turn conversation_id=%s elapsed_ms=%d has_new_attachments=%s has_action=%s has_options=%s",
                conversation_id,
                int((perf_counter() - turn_started) * 1000),
                bool(normalized_attachments),
                bool(action_response),
                bool(design_result.get("design_options")),
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
            conversation = session.get(AgentConversation, action.conversation_id)
            if conversation is None or conversation.user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent conversation not found.")
            self._ensure_conversation_active(conversation)
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
            logger.info(
                "agent_action_submitted conversation_id=%s action_id=%s module=%s model=%s job_id=%s",
                action.conversation_id,
                action.id,
                action.module_key,
                (card.params or {}).get("model"),
                accepted.job_id,
            )

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
            if source_conversation_id:
                conversation = session.get(AgentConversation, source_conversation_id)
                if conversation is None or conversation.user_id != current_user.id:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent conversation not found.")
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
            state = self._load_json(record.state_json) or {}
            resolved_action_id = action_id if action_id else state.get("last_action_id") if isinstance(state.get("last_action_id"), str) else None
            action_record = session.get(AgentAction, resolved_action_id) if resolved_action_id else None
            if conversation.mode == "design":
                if not action_id:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Design generation results must reference an Agent action.")
                if module_key not in {"text_to_image", "gemstone_design"}:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Design conversations only accept design generation results.")
            if resolved_action_id and (
                action_record is None
                or action_record.user_id != current_user.id
                or action_record.conversation_id != conversation_id
                or action_record.module_key != module_key
            ):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent action not found.")
            if action_record is not None:
                action_schema = self._action_to_schema(action_record)
                source_refs = self._merge_asset_refs(
                    action_schema.source_assets,
                    [AgentAssetRef(name="参考图", storage_url=url, preview_url=url) for url in action_schema.source_image_urls],
                )
                action_title = action_schema.title
            source_ref_payloads = [item.model_dump(mode="json") for item in source_refs]
            state["latest_generated_asset"] = result_ref.model_dump(mode="json")
            state["latest_generated_module"] = module_key
            state["latest_generation_source_assets"] = source_ref_payloads
            generated_assets_by_module = state.get("generated_assets_by_module")
            if not isinstance(generated_assets_by_module, dict):
                generated_assets_by_module = {}
            generated_assets_by_module[module_key] = result_ref.model_dump(mode="json")
            state["generated_assets_by_module"] = generated_assets_by_module
            source_assets_by_module = state.get("generation_source_assets_by_module")
            if not isinstance(source_assets_by_module, dict):
                source_assets_by_module = {}
            source_assets_by_module[module_key] = source_ref_payloads
            state["generation_source_assets_by_module"] = source_assets_by_module
            if action_record is not None:
                action_params = self._load_json(action_record.params_json) or {}
                if isinstance(action_params, dict) and action_params.get("model"):
                    state["latest_generated_model"] = action_params.get("model")
            if resolved_action_id:
                state["latest_generated_action_id"] = resolved_action_id
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
                    "action_id": resolved_action_id,
                    "module_key": module_key,
                    "title": action_title,
                    "source_assets": source_ref_payloads,
                    "result_asset": result_ref.model_dump(mode="json"),
                },
            )
        return result_ref

    def end_conversation_turn(self, *, conversation_id: str, current_user: User) -> AgentConversationDetail:
        conversation = self._get_conversation(conversation_id, current_user=current_user)
        reply = "好的，本次对话已结束。如果有其他需求，请新开对话窗口～"
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
                record.status = "ended"
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
            "temperature": 0.45,
            "max_tokens": 900,
        }
        try:
            started = perf_counter()
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    self._chat_completions_url(self.settings.agent_llm_base_url),
                    headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            logger.info("agent_llm_call stage=workflow_plan elapsed_ms=%d", int((perf_counter() - started) * 1000))
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
            "temperature": 0.35,
            "max_tokens": 900,
            "stream": True,
        }
        reply_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
            async with client.stream(
                "POST",
                self._chat_completions_url(self.settings.agent_llm_base_url),
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
        analyze_attachments: bool = True,
    ) -> dict[str, Any]:
        started = perf_counter()
        state = self._load_json(conversation.state_json) or {}
        brief = self._sanitize_design_brief(dict(state.get("design_brief") or {}))
        original_brief = dict(brief)
        selected_cards = list(state.get("selected_knowledge_cards") or [])
        stone_analysis = state.get("stone_analysis") if isinstance(state.get("stone_analysis"), dict) else None
        pending_slot = str(state.get("pending_design_slot") or "")
        previous_design_options = self._normalize_design_options(state.get("pending_design_options"))
        if attachments:
            if analyze_attachments or not stone_analysis:
                stone_started = perf_counter()
                stone_analysis = await self._analyze_stones_or_fallback(attachments, content, current_user=current_user)
                stone_analysis = self._localize_stone_analysis(stone_analysis)
                logger.info(
                    "agent_design_stage stage=stone_analysis conversation_id=%s elapsed_ms=%d source=%s",
                    conversation.id,
                    int((perf_counter() - stone_started) * 1000),
                    stone_analysis.get("source") if isinstance(stone_analysis, dict) else None,
                )
            else:
                stone_analysis = self._localize_stone_analysis(stone_analysis)
            brief["stones"] = stone_analysis
            state["design_source_assets"] = [item.model_dump(mode="json") for item in attachments[:1]]
        self._merge_design_content_into_brief(brief, content, pending_slot=pending_slot)
        brief = self._sanitize_design_brief(brief)
        knowledge_cards = self._search_jewelry_knowledge(content=content, brief=brief, stone_analysis=stone_analysis)
        explicit_generate_intent = self._is_design_generate_intent(content)
        continue_supplement_intent = self._is_design_continue_supplement_intent(content)
        alternative_options_intent = self._is_design_alternative_options_intent(content)
        design_plan = None
        if not (self._is_ready_generate_design_option(content) and self._design_brief_has_generation_context(brief, stone_analysis)):
            design_plan = await self._call_design_brief_llm(
                content=content,
                brief=brief,
                stone_analysis=stone_analysis,
                knowledge_cards=knowledge_cards,
                has_design_source=bool(state.get("design_source_assets")),
                pending_slot=pending_slot,
                previous_options=previous_design_options,
                alternative_options_requested=alternative_options_intent,
            )
        computed_missing_slots = self._missing_design_slots(brief, stone_analysis)
        if design_plan:
            brief = self._merge_llm_design_brief(brief, design_plan.get("design_brief"))
            brief = self._sanitize_design_brief(brief)
            brief = self._localize_design_brief(brief)
            if stone_analysis:
                brief["stones"] = stone_analysis
            if stone_analysis and self._is_design_generate_intent(content):
                self._autofill_design_brief(brief, knowledge_cards[:4], stone_analysis)
                brief = self._localize_design_brief(brief)
            computed_missing_slots = self._missing_design_slots(brief, stone_analysis)
            missing_slots = list(computed_missing_slots)
            should_generate = explicit_generate_intent and not computed_missing_slots
            llm_next_slot = str(design_plan.get("pending_design_slot") or "").strip()
            next_slot = computed_missing_slots[0] if computed_missing_slots else ""
            if should_generate:
                next_slot = ""
            reply = str(design_plan.get("reply") or "").strip() or self._build_design_reply(brief, stone_analysis, should_generate, missing_slots)
            design_options = self._normalize_design_options(design_plan.get("options"))
            if not next_slot:
                design_options = []
            elif llm_next_slot != next_slot:
                design_options = []
                reply = self._build_design_reply(brief, stone_analysis, should_generate, missing_slots)
            if alternative_options_intent and previous_design_options:
                design_options = self._exclude_previous_design_options(design_options, previous_design_options)
            if alternative_options_intent and pending_slot in {"category", "concept", "gemstone", "metal", "style", "craft", "scene"}:
                original_value = original_brief.get(pending_slot)
                if original_value in (None, "", [], {}):
                    brief.pop(pending_slot, None)
                else:
                    brief[pending_slot] = original_value
                computed_missing_slots = self._missing_design_slots(brief, stone_analysis)
                missing_slots = [pending_slot, *[item for item in computed_missing_slots if item != pending_slot]]
                should_generate = False
                next_slot = pending_slot
        else:
            if self._is_agent_autofill_intent(content):
                self._autofill_design_brief(brief, knowledge_cards[:4], stone_analysis)
                brief = self._localize_design_brief(brief)
            should_generate = explicit_generate_intent
            if should_generate and stone_analysis:
                self._autofill_design_brief(brief, knowledge_cards[:4], stone_analysis)
                brief = self._localize_design_brief(brief)
            missing_slots = self._missing_design_slots(brief, stone_analysis)
            if missing_slots:
                should_generate = False
            next_slot = missing_slots[0] if missing_slots and not should_generate else ""
            reply = self._build_design_reply(brief, stone_analysis, should_generate, missing_slots)
            design_options = []
        if continue_supplement_intent:
            should_generate = False
            next_slot = ""
            missing_slots = []
            design_options = []
            reply = self._build_design_continue_supplement_reply(brief, stone_analysis)
        if not should_generate and not missing_slots and not next_slot and not continue_supplement_intent:
            reply = self._build_design_ready_review_reply(brief, stone_analysis)
        if should_generate:
            design_options = []
            option_source = "none"
        elif not missing_slots and not next_slot and not continue_supplement_intent:
            option_source = "ready"
            design_options = self._ready_to_generate_design_options()
        elif continue_supplement_intent:
            option_source = "none"
            design_options = []
        elif not design_options:
            option_source = "fallback"
            design_options = self._fallback_design_options(
                next_slot,
                brief=brief,
                exclude_labels={item["label"] for item in previous_design_options} if alternative_options_intent else None,
            )
        else:
            option_source = "llm"
        brief = self._localize_design_brief(brief)
        if stone_analysis:
            stone_analysis = self._localize_stone_analysis(stone_analysis)
            brief["stones"] = stone_analysis
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
                    "params": {"model": DEFAULT_GEMSTONE_DESIGN_MODEL, "image_size": "1K", "strength": 0.75},
                    "source_assets": [item.model_dump(mode="json") for item in source_assets[:1]],
                    "source_image_urls": [],
                    "editable_prompt": True,
                    "next_question": "生成后可以重新生成、继续调整设计摘要，或结束。",
                }
            else:
                result["action_card"] = {
                    "kind": "text_to_image",
                    "module_key": "text_to_image",
                    "title": "设计出图",
                    "prompt": prompt,
                    "params": {
                        "model": self._default_design_text_to_image_model(content),
                        "aspect_ratio": "1:1",
                        "image_size": "1K",
                    },
                    "source_assets": [],
                    "source_image_urls": [],
                    "editable_prompt": True,
                    "next_question": "生成后可以重新生成、继续调整设计摘要，或结束。",
                }
        logger.info(
            "agent_design_stage stage=design_result conversation_id=%s elapsed_ms=%d should_generate=%s option_source=%s",
            conversation.id,
            int((perf_counter() - started) * 1000),
            bool(should_generate),
            result.get("design_option_source"),
        )
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
            "latest_generated_model": state.get("latest_generated_model"),
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
        previous_options: list[dict[str, str]] | None = None,
        alternative_options_requested: bool = False,
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
                        previous_options=previous_options or [],
                        alternative_options_requested=alternative_options_requested,
                    ),
                },
            ],
            "temperature": 0.2,
            "max_tokens": 700,
        }
        if self._model_supports_thinking_toggle(self.settings.agent_llm_model):
            payload["enable_thinking"] = False
        try:
            started = perf_counter()
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    self._chat_completions_url(self.settings.agent_llm_base_url),
                    headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                message = (response.json().get("choices") or [{}])[0].get("message") or {}
                parsed = self._parse_json_object(str(message.get("content") or ""))
                logger.info("agent_llm_call stage=design_brief elapsed_ms=%d", int((perf_counter() - started) * 1000))
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
            "max_tokens": 120,
            "stream": True,
        }
        if self._model_supports_thinking_toggle(self.settings.agent_llm_model):
            payload["enable_thinking"] = False
        payload["stream_options"] = {"include_usage": True}
        async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
            async with client.stream(
                "POST",
                self._chat_completions_url(self.settings.agent_llm_base_url),
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

    def _build_design_immediate_reply(self, *, content: str, attachments: list[AgentAssetRef], has_new_attachments: bool) -> str:
        if self._is_ready_generate_design_option(content):
            return "收到，我开始整理最终设计提示词。"
        if has_new_attachments:
            return "已收到裸石/参考图，我先识别图片特征并整理设计摘要。"
        if attachments:
            return "我会沿用已绑定的裸石/参考图，继续更新当前设计摘要。"
        return "收到，我先整理你的设计意图和下一步选项。"

    def _build_design_visible_reply_system_prompt(self) -> str:
        return (
            "你是金马珠宝内部的设计出图 Agent。你正在对话窗口直接回复设计师，必须输出自然中文正文。"
            "不要输出 JSON、Markdown 表格、代码块或工具调用。"
            "你的职责只是先给出简短承接，说明正在整理当前设计摘要和下一步选择。"
            "不要追问具体设计问题，不要提出具体选项，不要要求用户回答某个槽位；这些会由后续选项卡统一承载。"
            "不要总是重复固定句式，要根据本轮输入自然承接。"
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
            "你是金马珠宝内部的设计出图 Agent，负责和设计师问诊并维护结构化设计摘要。\n"
            "你必须只输出 JSON 对象，不要输出 Markdown、解释或代码块。\n"
            "任务：根据用户本轮输入、已有设计摘要、裸石分析和专业词库建议，更新槽位，并判断是否应该提交生成。\n"
            "允许槽位：category, concept, gemstone, metal, style, craft, scene, supplement, knowledge_summary。\n"
            "所有 JSON 字段值必须使用中文；不要输出英文宝石形状、颜色、透明度、镶嵌方式或设计风格。\n"
            "规则：\n"
            "1. 不要把用户的泛指句误当成设计理念。例如“这是我要设计镶嵌的裸石”只表示上传图片是裸石来源，不是 concept。\n"
            "2. 用户短答通常是在回答上一轮 pending_design_slot，例如只说“18k”应填 metal=18K金。\n"
            "3. 只有用户明确要求生成/出图/重新生成/直接生成/开始生成时，should_generate 才为 true；普通修改、补充、讨论不要生成。\n"
            "4. 无参考图的纯文生图流程中，gemstone 槽位必须明确追问或明确补全；主石/宝石未明确时，不得跳过，不得直接生成。\n"
            "5. 如果用户要求 Agent 补全，可以基于专业珠宝常识和词库补足缺失槽位，但要保持可制作、专业、克制。\n"
            "6. 每次最多追问一个最关键问题；信息足够时提示可生成首版设计图。\n"
            "7. 如果还需要用户补充，必须给出 2-4 个适合当前问题的选项 options，选项要短、专业、可直接作为用户回答；不要包含“其他”，前端会固定添加。\n"
            "8. 选项必须结合当前品类、风格、场景、设计理念和词库候选动态生成，不要每轮都重复固定模板；尤其 gemstone 槽位不要总是返回同一组翡翠选项，要像设计师灵感助手一样给出更贴合当前方向的分叉。\n"
            "9. 本业务默认以翡翠/玉石设计为主。若用户没有明确指定钻石、红宝石、蓝宝石、祖母绿等非玉石主石，则 gemstone 槽位默认按翡翠路线追问，优先给出翡翠种水、颜色、形制、数量相关选项，但要根据当前上下文灵活变化，不要返回泛彩宝选项。\n"
            "10. 如果用户说“推荐别的”“换一批”“还有其他选择”等，表示他要新的候选方向，不是在回答当前槽位。此时要保持 pending_design_slot 不变，并返回一组不同于上一轮的新 options，避免重复上一轮选项。\n"
            "11. 有裸石来源时，生成路线是 gemstone_design；无裸石来源时是 text_to_image，但你只需要返回 latest_design_mode 字段。\n"
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
        previous_options: list[dict[str, str]],
        alternative_options_requested: bool,
    ) -> str:
        knowledge_text = "\n".join(
            f"- {card.get('category')}: {card.get('content') or card.get('title')}"
            for card in knowledge_cards[:6]
        )
        previous_options_text = "\n".join(
            f"- {item.get('label')}: {item.get('value')} / {item.get('description') or ''}"
            for item in previous_options[:6]
        )
        return (
            f"用户本轮输入：{content or '用户只上传/选择了图片，没有文字'}\n"
            f"是否已有裸石/参考图来源：{has_design_source}\n"
            f"上一轮正在追问的槽位：{pending_slot or '无'}\n"
            f"用户本轮是否明确要求换一批候选：{alternative_options_requested}\n"
            f"上一轮已展示过的选项：\n{previous_options_text or '无'}\n"
            f"当前设计摘要：{json.dumps(brief, ensure_ascii=False)}\n"
            f"裸石视觉分析/降级分析：{json.dumps(stone_analysis or {}, ensure_ascii=False)}\n"
            f"专业词库候选：\n{knowledge_text or '无'}\n"
            "请返回合并后的完整设计摘要。未知槽位填 null，不要编造用户没有表达且不需要 Agent 补全的内容。"
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
            if self._should_preserve_current_design_brief_value(key, merged.get(key), value):
                continue
            merged[key] = value
        return merged

    def _should_preserve_current_design_brief_value(self, key: str, current_value: object, incoming_value: object) -> bool:
        if current_value in (None, "", [], {}) or key in {"knowledge_summary", "stones"}:
            return False
        current_text = str(current_value).strip()
        incoming_text = str(incoming_value).strip()
        if not current_text or not incoming_text:
            return False
        if current_text == incoming_text:
            return True
        if key == "gemstone":
            current_generic = self._is_generic_gemstone_brief_value(current_text)
            incoming_generic = self._is_generic_gemstone_brief_value(incoming_text)
            if current_generic and incoming_generic:
                return len(incoming_text) <= len(current_text)
            if current_generic and not incoming_generic:
                return False
            return True
        return True

    def _localize_design_brief(self, brief: dict[str, Any]) -> dict[str, Any]:
        localized: dict[str, Any] = {}
        for key, value in brief.items():
            if isinstance(value, dict):
                localized[key] = self._localize_stone_analysis(value) if key == "stones" else self._localize_design_brief(value)
            else:
                localized[key] = self._localize_text_value(value, field=key)
        return localized

    def _sanitize_design_brief(self, brief: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(brief)
        for slot in ("category", "concept", "gemstone", "metal", "style", "craft", "scene", "supplement"):
            if self._is_non_design_chitchat_value(sanitized.get(slot)):
                sanitized.pop(slot, None)
        return sanitized

    def _localize_stone_analysis(self, stone_analysis: dict[str, object] | None) -> dict[str, object] | None:
        if not isinstance(stone_analysis, dict):
            return stone_analysis
        localized: dict[str, object] = {}
        for key, value in stone_analysis.items():
            if key == "source":
                localized[key] = value
                continue
            localized[key] = self._localize_text_value(value, field=key)
        return localized

    def _localize_text_value(self, value: object, *, field: str = "") -> object:
        if value in (None, "", [], {}):
            return value
        if isinstance(value, list):
            return [self._localize_text_value(item, field=field) for item in value]
        if isinstance(value, dict):
            return {key: self._localize_text_value(item, field=str(key)) for key, item in value.items()}
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return text

        replacements = {
            "mixed geometric shapes": "多种几何随形",
            "rectangular block": "长方块状",
            "elongated oval": "长椭圆形",
            "cabochon": "弧面",
            "triangle": "三角形",
            "irregular trapezoid": "不规则梯形",
            "translucent white": "半透明白色",
            "amber": "琥珀色",
            "honey yellow": "蜜黄色",
            "reddish-brown": "红棕色",
            "reddish brown": "红棕色",
            "orange": "橙色",
            "deep emerald green": "深祖母绿色",
            "emerald green": "祖母绿色",
            "semi-transparent": "半透明",
            "translucent": "透光",
            "glossy": "光泽感",
            "waxy": "蜡质感",
            "vitreous luster": "玻璃光泽",
            "bezel settings": "包镶",
            "bezel setting": "包镶",
            "low-profile prong settings": "低位爪镶",
            "prong settings": "爪镶",
            "cluster rings": "群镶戒指",
            "earrings": "耳环",
            "pendant accents": "吊坠点缀",
            "irregular shapes": "不规则外形",
            "varying sizes": "尺寸不一",
            "secure edges": "保护边缘",
            "custom": "定制",
        }
        localized = text
        for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            localized = re.sub(re.escape(source), target, localized, flags=re.IGNORECASE)
        localized = localized.replace("/", "、")
        localized = re.sub(r"\s*,\s*", "、", localized)
        localized = re.sub(r"\s+or\s+", "或", localized, flags=re.IGNORECASE)
        localized = re.sub(r"\s+and\s+", "和", localized, flags=re.IGNORECASE)
        localized = re.sub(r"\s+", " ", localized).strip()

        if self._contains_latin_letters(localized):
            fallback_by_field = {
                "shape": "多种随形裸石，轮廓以图片识别为准",
                "color": "多色天然玉石色调，具体以图片颜色为准",
                "transparency": "半透明至透光质感，带自然光泽",
                "texture": "天然纹理与光泽，以图片细节为准",
                "setting_direction": "根据裸石不规则轮廓定制包镶或低位爪镶，保护边缘并保证佩戴稳定",
                "risk_notes": "保留裸石原始形状、颜色、比例和天然纹理",
                "recommended_style": "轻奢围镶风",
                "style": "轻奢围镶风",
            }
            return fallback_by_field.get(field, "结合图片特征进行中文设计描述")
        return localized

    def _contains_latin_letters(self, text: str) -> bool:
        normalized = re.sub(r"\d+\s*K", " ", text, flags=re.IGNORECASE)
        return any("a" <= char.lower() <= "z" for char in normalized)

    def _is_generic_gemstone_brief_value(self, value: str) -> bool:
        normalized = value.strip().replace("，", " ").replace("。", " ").replace("；", " ")
        if not normalized:
            return True
        generic_values = {
            "翡翠",
            "玉",
            "玉石",
            "裸石",
            "宝石",
            "钻石",
            "裸石图片",
            "这块玉",
            "这块裸石",
            "主石可按设计理念选择",
        }
        if normalized in generic_values:
            return True
        profile = self._extract_jade_profile_from_text(normalized)
        if any(profile.values()):
            return False
        return len(normalized) <= 4

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

    def _exclude_previous_design_options(
        self,
        options: list[dict[str, str]],
        previous_options: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        previous_keys = {
            (item.get("label") or "").strip().lower()
            for item in previous_options
            if (item.get("label") or "").strip()
        }
        previous_keys.update(
            {
                (item.get("value") or "").strip().lower()
                for item in previous_options
                if (item.get("value") or "").strip()
            }
        )
        filtered = [
            item
            for item in options
            if (item.get("label") or "").strip().lower() not in previous_keys
            and (item.get("value") or "").strip().lower() not in previous_keys
        ]
        return filtered[:4]

    def _fallback_design_options(
        self,
        pending_slot: str,
        *,
        brief: dict[str, Any] | None = None,
        exclude_labels: set[str] | None = None,
    ) -> list[dict[str, str]]:
        if pending_slot == "gemstone":
            jade_options = self._build_jade_gemstone_options(brief or {}, exclude_labels=exclude_labels)
            if jade_options:
                return jade_options
        presets: dict[str, list[tuple[str, str]]] = {
            "category": [("吊坠", "适合突出裸石主体"), ("戒指", "更强调佩戴和展示"), ("手链", "适合轻量日常款")],
            "gemstone": [("冰种翡翠", "默认按翡翠路线推进，强调种水和通透感"), ("白冰翡翠", "清冷干净，适合白金或铂金"), ("飘花翡翠", "更自然、更东方，适合题材款"), ("紫罗兰翡翠", "柔和温润，适合玫瑰金路线")],
            "style": [("自然", "强调裸石天然感"), ("复古", "更有装饰性和故事感"), ("几何", "线条清晰，更现代")],
            "metal": [("18K金", "经典稳妥"), ("玫瑰金", "柔和暖调"), ("白金", "清爽现代")],
            "craft": [("爪镶", "露出更多裸石"), ("包镶", "保护性更强"), ("围钻", "提升华丽度")],
            "scene": [("日常佩戴", "克制、耐看"), ("晚宴聚会", "更强调存在感"), ("收藏展示", "突出设计性")],
        }
        return [
            {"label": label, "value": label, "description": description}
            for label, description in presets.get(pending_slot, [])
            if not exclude_labels or label not in exclude_labels
        ]

    def _build_jade_gemstone_options(self, brief: dict[str, Any], *, exclude_labels: set[str] | None = None) -> list[dict[str, str]]:
        jade_cards = self._knowledge_cards_by_section_prefix("6.")
        if not jade_cards:
            return []
        category = str(brief.get("category") or "").strip()
        title_map = {
            str(card.get("title") or "").strip(): card
            for card in jade_cards
            if str(card.get("title") or "").strip()
        }

        def choose_title(*candidates: str, default: str) -> str:
            for candidate in candidates:
                if candidate in title_map:
                    return candidate
            return default

        if category in {"吊坠", "项链"}:
            specs = [
                (
                    "冰种蛋面单坠",
                    f"主石用单颗{choose_title('冰种', default='冰种')}翡翠{choose_title('蛋面', default='蛋面')}，做项链吊坠",
                    "清透水润，适合简洁高级的日常佩戴款",
                ),
                (
                    "白冰水滴单坠",
                    f"主石用单颗{choose_title('白冰', default='白冰')}翡翠{choose_title('水滴', default='水滴')}，做项链吊坠",
                    "清冷干净，垂坠感更明确",
                ),
                (
                    "飘花叶子单坠",
                    f"主石用单颗{choose_title('飘花', default='飘花')}翡翠{choose_title('叶子', default='叶子')}，做项链吊坠",
                    "自然东方气质更强，题材感明确",
                ),
                (
                    "福豆三石款",
                    f"主石方向用{choose_title('福豆', default='福豆')}题材，三颗豆粒造型，做项链吊坠",
                    "直接带出玉石数量和寓意，适合礼赠路线",
                ),
                (
                    "紫罗兰蛋面单坠",
                    f"主石用单颗{choose_title('紫罗兰', default='紫罗兰')}翡翠{choose_title('蛋面', default='蛋面')}，做项链吊坠",
                    "色调柔和偏浪漫，适合女性化路线",
                ),
                (
                    "晴水平安扣单坠",
                    f"主石用单颗{choose_title('晴水', default='晴水')}翡翠{choose_title('平安扣', default='平安扣')}，做项链吊坠",
                    "更克制耐看，适合日常高级感路线",
                ),
            ]
        elif category == "戒指":
            specs = [
                (
                    "冰种蛋面戒",
                    f"主石用单颗{choose_title('冰种', default='冰种')}翡翠{choose_title('蛋面', default='蛋面')}，做戒指",
                    "最稳妥的高频翡翠戒指路线",
                ),
                (
                    "阳绿蛋面戒",
                    f"主石用单颗阳绿色翡翠{choose_title('蛋面', default='蛋面')}，做戒指",
                    "突出颜色表现，适合高级感路线",
                ),
                (
                    "白冰双石戒",
                    f"主石用两颗{choose_title('白冰', default='白冰')}小翡翠双石组合，做戒指",
                    "更轻盈现代，也把数量信息补齐",
                ),
                (
                    "马鞍男戒",
                    f"主石用单颗{choose_title('马鞍戒面', default='马鞍戒面')}翡翠，做戒指",
                    "更厚重，适合中性或男款方向",
                ),
                (
                    "紫罗兰花头戒",
                    f"主石用单颗{choose_title('紫罗兰', default='紫罗兰')}翡翠{choose_title('蛋面', default='蛋面')}，做花头戒指",
                    "更华丽柔美，适合精致礼赠路线",
                ),
                (
                    "墨翠中性戒",
                    f"主石用单颗{choose_title('墨翠', default='墨翠')}翡翠，做中性戒指",
                    "对比更强，适合简洁力量感路线",
                ),
            ]
        elif category in {"耳环", "耳坠", "耳饰"}:
            specs = [
                (
                    "白冰水滴耳坠",
                    f"主石用一对{choose_title('白冰', default='白冰')}翡翠{choose_title('水滴', default='水滴')}，做耳坠",
                    "清冷轻盈，适合现代通勤路线",
                ),
                (
                    "飘花叶子耳饰",
                    f"主石用一对{choose_title('飘花', default='飘花')}翡翠{choose_title('叶子', default='叶子')}，做耳饰",
                    "更自然灵动，适合东方题材路线",
                ),
                (
                    "紫罗兰蛋面耳钉",
                    f"主石用一对{choose_title('紫罗兰', default='紫罗兰')}翡翠{choose_title('蛋面', default='蛋面')}，做耳钉",
                    "柔和精致，适合轻礼服路线",
                ),
                (
                    "满绿无事牌耳坠",
                    f"主石用一对满绿色翡翠无事牌，做耳坠",
                    "色彩存在感强，适合大气礼服路线",
                ),
                (
                    "冰种飘花蛋面耳坠",
                    f"主石用一对冰种飘花翡翠蛋面，做耳坠",
                    "活泼灵动，适合时髦佩戴路线",
                ),
                (
                    "晴水小平安扣耳饰",
                    f"主石用一对{choose_title('晴水', default='晴水')}翡翠{choose_title('平安扣', default='平安扣')}，做耳饰",
                    "更克制含蓄，适合日常高级路线",
                ),
            ]
        else:
            specs = [
                (
                    "冰种单颗蛋面",
                    f"主石用单颗{choose_title('冰种', default='冰种')}翡翠{choose_title('蛋面', default='蛋面')}",
                    "适合大多数品类，先把种水和形制定下来",
                ),
                (
                    "白冰单颗水滴",
                    f"主石用单颗{choose_title('白冰', default='白冰')}翡翠{choose_title('水滴', default='水滴')}",
                    "更清冷，也方便后续做吊坠或耳坠",
                ),
                (
                    "飘花随形单石",
                    f"主石用单颗{choose_title('飘花', default='飘花')}翡翠{choose_title('随形', default='随形')}",
                    "自然感更强，适合东方题材或艺术款",
                ),
                (
                    "福豆三颗题材",
                    f"主石方向用{choose_title('福豆', default='福豆')}题材，三颗豆粒造型",
                    "把数量和题材一起确定下来",
                ),
                (
                    "紫罗兰单颗蛋面",
                    f"主石用单颗{choose_title('紫罗兰', default='紫罗兰')}翡翠{choose_title('蛋面', default='蛋面')}",
                    "更柔和浪漫，也适合偏礼赠路线",
                ),
                (
                    "晴水单颗平安扣",
                    f"主石用单颗{choose_title('晴水', default='晴水')}翡翠{choose_title('平安扣', default='平安扣')}",
                    "更温润克制，适合现代东方路线",
                ),
            ]

        return [
            {"label": label[:24], "value": value[:120], "description": description[:120]}
            for label, value, description in specs
            if not exclude_labels or label not in exclude_labels
        ]

    def _should_force_jade_gemstone_options(
        self,
        *,
        content: str,
        brief: dict[str, Any],
        stone_analysis: dict[str, object] | None,
    ) -> bool:
        if stone_analysis:
            return False
        text = " ".join(
            str(item)
            for item in [
                content,
                brief.get("gemstone"),
                brief.get("concept"),
                brief.get("supplement"),
            ]
            if item
        ).lower()
        jade_markers = ("翡翠", "玉", "玉石", "和田玉", "墨翠", "白冰", "飘花", "紫罗兰", "晴水", "蓝水", "福豆", "叶子", "平安扣")
        non_jade_markers = ("钻石", "红宝石", "蓝宝石", "祖母绿", "珍珠", "彩宝", "碧玺", "欧泊", "海蓝宝", "坦桑石", "尖晶石")
        if any(marker in text for marker in non_jade_markers) and not any(marker in text for marker in jade_markers):
            return False
        return True

    def _knowledge_cards_by_section_prefix(self, prefix: str) -> list[dict[str, object]]:
        normalized_prefix = prefix.strip()
        return [
            card
            for card in self._load_jewelry_term_cards()
            if str(card.get("category") or "").strip().startswith(normalized_prefix)
        ]

    def _ready_to_generate_design_options(self) -> list[dict[str, str]]:
        return [
            {
                "label": "生成首版设计图",
                "value": "生成首版设计图",
                "description": "使用当前设计摘要和裸石来源直接出第一版",
            },
            {
                "label": "继续补充设计要求",
                "value": "继续补充设计要求",
                "description": "先不生成，继续补充设计理念、工艺或场景细节",
            },
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
        fallback_prompt = self._build_design_prompt(
            brief=brief,
            selected_cards=selected_cards,
            stone_analysis=stone_analysis,
            content=content,
        )
        llm_prompt = await self._call_design_generation_prompt_llm(
            conversation_context=conversation_context,
            brief=brief,
            selected_cards=selected_cards,
            stone_analysis=stone_analysis,
            content=content,
            has_design_source=has_design_source,
        )
        if llm_prompt and self._is_safe_design_generation_prompt(llm_prompt):
            return self._ensure_design_front_view_constraint(llm_prompt)
        return self._ensure_design_front_view_constraint(fallback_prompt)

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
                        "你是珠宝 AI 生图提示词工程师。你的任务是把设计师对话上下文、结构化设计摘要、裸石分析和专业词库，"
                        "总结成一段可直接发给生图模型的最终 prompt。\n"
                        "要求：只输出最终 prompt 文本，不要 JSON、Markdown、标题、解释。\n"
                        "不要把专业参考原文、表格、英文术语堆砌进去，要吸收后改写成自然的珠宝设计描述。\n"
                        "禁止出现这些元信息或文档说明：提示词、文生图、模型、本文档、表格、专业参考、候选、JSON、Markdown、英文名称、Prompt、用于优化。\n"
                        "禁止输出英文逗号词串、Markdown 引用符号 >、竖线表格、项目符号列表。\n"
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
                        f"结构化设计摘要：{self._format_design_brief_for_prompt(brief)}\n"
                        f"裸石分析：{self._format_design_brief_for_prompt(stone_analysis or {})}\n"
                        f"可参考的专业知识候选：\n{knowledge_text or '无'}"
                    ),
                },
            ],
            "temperature": 0.4,
            "max_tokens": 520,
        }
        if self._model_supports_thinking_toggle(self.settings.agent_llm_model):
            payload["enable_thinking"] = False
        try:
            started = perf_counter()
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    self._chat_completions_url(self.settings.agent_llm_base_url),
                    headers={"Authorization": f"Bearer {self.settings.agent_llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                message = (response.json().get("choices") or [{}])[0].get("message") or {}
                prompt = self._strip_prompt_text(str(message.get("content") or ""))
                logger.info("agent_llm_call stage=design_prompt elapsed_ms=%d", int((perf_counter() - started) * 1000))
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
        stripped = stripped.replace(">", " ").replace("|", " ").replace("`", " ")
        stripped = stripped.replace("Prompt:", " ").replace("prompt:", " ")
        return " ".join(stripped.split())

    def _clean_prompt_fragment(self, text: str) -> str:
        cleaned = text.replace("|", " ").replace("`", " ")
        cleaned = cleaned.replace(">", " ")
        blocked_fragments = (
            "本文档用于",
            "优化文生图模型",
            "提示词质量",
            "提示词示例",
            "中文名称",
            "英文名称",
            "适用场景",
            "用途：",
            "Prompt Templates",
            "常见文生图模型",
        )
        for fragment in blocked_fragments:
            cleaned = cleaned.replace(fragment, " ")
        cleaned = re.sub(r"\b(?!18K\b|24K\b|14K\b)[A-Za-z][A-Za-z0-9+/&.,' -]{2,}\b", " ", cleaned)
        cleaned = " ".join(cleaned.split())
        return cleaned[:80]

    def _is_safe_design_generation_prompt(self, prompt: str) -> bool:
        if not prompt.strip():
            return False
        blocked = (
            "本文档",
            "文生图模型",
            "提示词质量",
            "提示词示例",
            "专业参考",
            "候选",
            "Markdown",
            "JSON",
            "Prompt",
            "prompt",
            "表格",
            "英文名称",
            "用于优化",
            "gold-silver",
            "Gold-Silver",
            "metal inlay work",
            "Metal Inlay",
            "本文档用于优化",
        )
        if any(item in prompt for item in blocked):
            return False
        if any(mark in prompt for mark in ["|", "```", "\n-", "\n*"]):
            return False
        ascii_letters = sum(1 for char in prompt if ("a" <= char.lower() <= "z"))
        if ascii_letters > max(40, len(prompt) * 0.18):
            return False
        return True

    def _ensure_design_front_view_constraint(self, prompt: str) -> str:
        stripped = prompt.strip()
        if "完整的珠宝设计正视图" in stripped and "不裁切" in stripped:
            return stripped
        return f"{stripped} {DESIGN_FRONT_VIEW_CONSTRAINT}"

    def _merge_design_content_into_brief(self, brief: dict[str, Any], content: str, *, pending_slot: str = "") -> None:
        stripped = self._strip_design_generate_phrases(content.strip())
        if (
            not stripped
            or self._is_non_design_chitchat_value(stripped)
            or self._is_agent_autofill_intent(stripped)
            or self._is_design_alternative_options_intent(stripped)
        ):
            return
        gemstone_update = self._extract_gemstone_attribute_update(stripped)
        if gemstone_update:
            current_gemstone = str(brief.get("gemstone") or "").strip()
            brief["gemstone"] = self._merge_gemstone_attribute_update(current_gemstone, gemstone_update)
            return
        revision_intents = self._extract_design_revision_intents(stripped)
        if revision_intents["updates"] or revision_intents["clears"] or revision_intents["autofill_slots"]:
            for slot, value in revision_intents["updates"].items():
                brief[slot] = self._normalize_design_slot_value(slot, value)
            for slot in revision_intents["clears"]:
                brief.pop(slot, None)
            for slot in revision_intents["autofill_slots"]:
                brief.pop(slot, None)
            return
        lowered = stripped.lower()
        answered_pending_slot = False
        if pending_slot in {"category", "concept", "gemstone", "metal", "style", "craft", "scene"} and len(stripped) <= 36:
            brief[pending_slot] = self._normalize_design_slot_value(pending_slot, stripped)
            answered_pending_slot = True
        category = self._first_matching_phrase(stripped, ["戒指", "吊坠", "项链", "耳环", "耳坠", "胸针", "手镯", "手链"])
        if category:
            brief["category"] = category
        style = self._first_matching_phrase(stripped, ["Art Deco", "art deco", "复古", "现代", "中式", "东方", "宫廷", "极简", "自然", "新中式", "轻奢", "法式", "华丽", "简约"])
        if style and pending_slot != "style":
            brief["style"] = "Art Deco" if style.lower() == "art deco" else style
        metal = self._first_matching_phrase(stripped, ["18K金", "18k金", "18K", "18k", "玫瑰金", "黄金", "白金", "铂金", "银", "K金"])
        if metal and pending_slot != "metal":
            brief["metal"] = "18K金" if metal.lower() in {"18k金", "18k"} else metal
        gemstone = self._extract_gemstone_phrase(stripped)
        if gemstone:
            current_gemstone = str(brief.get("gemstone") or "").strip()
            if not current_gemstone or pending_slot == "gemstone" or self._is_generic_gemstone_brief_value(current_gemstone):
                brief["gemstone"] = gemstone
        craft = self._first_matching_phrase(stripped, ["爪镶", "包镶", "围钻", "花丝", "镂空", "雕刻", "微镶", "密镶", "镶嵌"])
        if craft:
            brief["craft"] = craft
        scene = self._first_matching_phrase(stripped, ["日常", "通勤", "婚礼", "礼服", "商务", "收藏", "展会", "晚宴"])
        if scene and pending_slot != "scene":
            brief["scene"] = scene
        upload_reference_markers = (
            "这是我要设计镶嵌的裸石",
            "这是我要设计的裸石",
            "这是我要镶嵌的裸石",
            "这是裸石",
            "围绕这块玉",
            "围绕这块裸石",
            "这块玉",
            "这块裸石",
        )
        if (
            not answered_pending_slot
            and not self._is_short_ambiguous_design_answer(stripped)
            and lowered not in {"整理设计理念", "生成首版设计图", "优化提示词"}
            and not any(marker in stripped for marker in upload_reference_markers)
        ):
            brief["concept"] = stripped
            if not any([category, style, metal, gemstone, craft, scene]):
                previous = str(brief.get("supplement") or "")
                brief["supplement"] = f"{previous}\n{stripped}".strip()

    def _extract_gemstone_phrase(self, text: str) -> str | None:
        normalized = text.strip()
        jade_profile = self._extract_jade_profile_from_text(normalized)
        jade_parts = [jade_profile.get("water"), jade_profile.get("color"), self._jade_shape_phrase(normalized, jade_profile.get("shape") or "")]
        if any(jade_parts) or any(token in normalized for token in ("翡翠", "玉", "翠")):
            parts = [item for item in jade_parts if item]
            if "翡翠" not in "".join(parts):
                parts.insert(0, "翡翠")
            count = jade_profile.get("count") or ""
            if count:
                parts.append(count)
            return "".join(dict.fromkeys(parts))
        return self._first_matching_phrase(normalized, ["祖母绿", "和田玉", "玉", "钻石", "钻", "红宝石", "红宝", "蓝宝石", "蓝宝", "珍珠", "裸石"])

    def _extract_gemstone_attribute_update(self, text: str) -> dict[str, str]:
        normalized = text.strip()
        if not any(marker in normalized for marker in ("主石", "玉石", "翡翠", "裸石", "宝石")):
            return {}
        profile = self._extract_jade_profile_from_text(normalized)
        updates = {key: value for key, value in profile.items() if value}
        if "形制" in normalized or "形状" in normalized or "外形" in normalized:
            shape = self._extract_jade_shape_value(normalized)
            if shape:
                updates["shape"] = shape
        explicit_count = any(marker in normalized for marker in ("数量", "几颗", "几粒", "单颗", "双石", "两颗", "二颗", "三颗", "多颗"))
        if not any(updates.get(key) for key in ("water", "color", "shape")) and not explicit_count:
            return {}
        return updates

    def _merge_gemstone_attribute_update(self, current: str, updates: dict[str, str]) -> str:
        profile = self._extract_jade_profile_from_text(current)
        for key, value in updates.items():
            if value:
                profile[key] = value
        parts: list[str] = []
        for value in [profile.get("water"), profile.get("color"), profile.get("shape")]:
            if value and value not in parts:
                parts.append(value)
        if not any(parts):
            return current
        rendered = "".join(item for item in parts if item)
        if "翡翠" not in rendered and ("翡翠" in current or any(updates.values())):
            rendered = f"翡翠{rendered}"
        count = profile.get("count") or self._infer_jade_count(rendered)
        if count and count not in rendered:
            rendered = f"{rendered}{count}"
        return rendered

    def _extract_jade_shape_value(self, text: str) -> str:
        explicit = re.search(r"(?:翡翠|玉石|主石|裸石|宝石)?(?:的)?(?:形制|形状|外形)\s*(?:是|为|改成|改为|调整为|定为|设为|:|：)?\s*([^，。；,;\s]+)", text)
        if explicit:
            return explicit.group(1).strip(" ：:，,。；;")
        return self._extract_jade_profile_from_text(text).get("shape") or ""

    def _jade_shape_phrase(self, text: str, fallback: str) -> str:
        if "随形" in text and "蛋面" in text:
            return "随形蛋面"
        return fallback

    def _normalize_design_slot_value(self, slot: str, value: str) -> str:
        stripped = value.strip()
        if slot == "metal" and stripped.lower() in {"18k", "18k金"}:
            return "18K金"
        if slot == "style":
            style_aliases = {
                "轻奢风": "轻奢",
                "现代风": "现代",
                "复古风": "复古",
                "极简风": "极简",
                "新中式风": "新中式",
            }
            return style_aliases.get(stripped, stripped)
        return stripped

    def _is_non_design_chitchat_value(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.strip().lower().replace(" ", "")
        return normalized in {
            "你好",
            "您好",
            "hello",
            "hi",
            "哈喽",
            "嗨",
            "在吗",
            "在不在",
            "谢谢",
            "好的",
            "好",
            "ok",
            "嗯",
            "嗯嗯",
        }

    def _is_short_ambiguous_design_answer(self, text: str) -> bool:
        stripped = text.strip()
        if len(stripped) > 8:
            return False
        if any(
            marker in stripped
            for marker in (
                "戒指",
                "吊坠",
                "项链",
                "耳环",
                "耳坠",
                "胸针",
                "手镯",
                "手链",
                "18K",
                "18k",
                "黄金",
                "白金",
                "铂金",
                "玫瑰金",
                "翡翠",
                "玉",
                "钻",
                "宝石",
                "蛋面",
                "水滴",
                "三角",
                "自然",
                "复古",
                "现代",
                "中式",
                "东方",
                "极简",
                "轻奢",
                "爪镶",
                "包镶",
                "围钻",
                "古法",
                "磨砂",
                "日常",
                "通勤",
                "晚宴",
                "聚会",
                "收藏",
                "婚礼",
            )
        ):
            return False
        return True

    def _extract_explicit_design_slot_updates(self, content: str) -> dict[str, str]:
        return self._extract_design_revision_intents(content)["updates"]

    def _extract_design_revision_intents(self, content: str) -> dict[str, Any]:
        slot_keywords = {
            "category": ("品类", "类别", "款式"),
            "gemstone": ("主石", "玉石", "翡翠", "裸石", "宝石"),
            "metal": ("材质", "金属"),
            "style": ("风格",),
            "craft": ("工艺", "镶嵌", "镶嵌工艺"),
            "scene": ("场景", "佩戴场景", "使用场景"),
            "concept": ("理念", "设计理念", "主题"),
            "supplement": ("补充说明", "补充要求", "补充", "备注", "说明"),
        }
        update_markers = ("改成", "改为", "换成", "换为", "调整为", "设为", "定为")
        clear_markers = ("删除", "删掉", "去掉", "去除", "移除", "清空", "不要")
        autofill_markers = (
            "帮我想",
            "你帮我想",
            "帮我补",
            "你帮我补",
            "帮我补全",
            "你帮我补全",
            "帮我完善",
            "你帮我完善",
            "帮我写",
            "你帮我写",
            "你来想",
            "你来补",
            "你来定",
            "补一下",
            "补充一下",
            "完善一下",
            "丰富一下",
            "扩写一下",
            "整理一下",
        )
        updates: dict[str, str] = {}
        clears: set[str] = set()
        autofill_slots: set[str] = set()
        active_slot = ""
        for clause in self._split_design_revision_clauses(content):
            slot = self._match_design_slot_keyword(clause, slot_keywords)
            if slot and any(marker in clause for marker in clear_markers):
                clears.add(slot)
                autofill_slots.discard(slot)
                updates.pop(slot, None)
                active_slot = slot
                continue
            if slot and any(marker in clause for marker in autofill_markers):
                autofill_slots.add(slot)
                clears.discard(slot)
                updates.pop(slot, None)
                active_slot = slot
                continue
            value = self._extract_design_revision_value(
                clause,
                slot=slot or active_slot,
                slot_keywords=slot_keywords,
                update_markers=update_markers,
            )
            if value:
                target_slot = slot or active_slot
                if target_slot:
                    updates[target_slot] = value[:60]
                    clears.discard(target_slot)
                    autofill_slots.discard(target_slot)
                    active_slot = target_slot
                    continue
            if slot:
                active_slot = slot
        return {"updates": updates, "clears": clears, "autofill_slots": autofill_slots}

    def _split_design_revision_clauses(self, content: str) -> list[str]:
        normalized = re.sub(r"[。；;\n]+", "，", content)
        normalized = normalized.replace(",", "，")
        clauses = [item.strip(" ，") for item in normalized.split("，") if item.strip(" ，")]
        return clauses or [content.strip()]

    def _match_design_slot_keyword(self, clause: str, slot_keywords: dict[str, tuple[str, ...]]) -> str:
        best_slot = ""
        best_length = 0
        for slot, keywords in slot_keywords.items():
            for keyword in keywords:
                if keyword in clause and len(keyword) > best_length:
                    best_slot = slot
                    best_length = len(keyword)
        return best_slot

    def _extract_design_revision_value(
        self,
        clause: str,
        *,
        slot: str,
        slot_keywords: dict[str, tuple[str, ...]],
        update_markers: tuple[str, ...],
    ) -> str:
        stripped = clause.strip()
        if not slot:
            return ""
        if any(stripped.startswith(marker) for marker in update_markers):
            for marker in update_markers:
                if stripped.startswith(marker):
                    return stripped[len(marker):].strip(" ：:，, ")
        for marker in update_markers:
            if marker in stripped:
                return stripped.split(marker, 1)[-1].strip(" ：:，, ")
        for keyword in slot_keywords.get(slot, ()):
            match = re.search(rf"{re.escape(keyword)}\s*[:：是为用走]\s*(.+)$", stripped)
            if match:
                return match.group(1).strip(" ，,")
        return ""

    def _first_matching_phrase(self, text: str, phrases: list[str]) -> str | None:
        lowered = text.lower()
        for phrase in phrases:
            if phrase.lower() in lowered:
                return phrase
        return None

    def _is_design_generate_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return any(
            key in normalized
            for key in ["生成首版设计图", "生成设计图", "重新生成设计图", "重新生成", "生成方案", "开始生成", "直接生成"]
        )

    def _is_ready_generate_design_option(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return normalized in {"生成首版设计图", "重新生成设计图", "重新生成", "开始生成", "直接生成"}

    def _is_design_continue_supplement_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return normalized in {"继续补充设计要求", "继续补充", "补充设计要求", "继续完善设计摘要", "继续完善"}

    def _is_design_alternative_options_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return any(
            marker in normalized
            for marker in (
                "推荐一下其他选择",
                "推荐其他选择",
                "其他选择",
                "还有其他选择",
                "还有别的选择",
                "换一批",
                "换一组",
                "换几个",
                "再推荐几个",
                "更多选择",
                "其他方案",
                "别的方向",
            )
        )

    def _design_brief_has_generation_context(self, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> bool:
        return all(self._design_slot_is_satisfied(item, brief, stone_analysis) for item in self._required_design_slots(stone_analysis))

    def _default_design_text_to_image_model(self, content: str) -> str:
        if "重新生成设计图" in content or content.strip().replace(" ", "") == "重新生成":
            return DEFAULT_IMAGE_REGENERATE_MODEL
        return DEFAULT_IMAGE_MODEL

    def _is_agent_autofill_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return "agent自行补全" in normalized or "agent补全" in normalized or "自动补全" in normalized

    async def _analyze_stones_or_fallback(self, attachments: list[AgentAssetRef], content: str, *, current_user: User) -> dict[str, object]:
        fallback = {
            "count": len(attachments),
            "shape": "请结合裸石图片确认外形轮廓",
            "color": "请结合裸石图片确认颜色与种水",
            "texture": "保留天然纹理、色带与瑕疵特征",
            "setting_direction": "建议围绕裸石原始轮廓进行爪镶或包镶设计",
            "risk_notes": "不要改变裸石形状、颜色、比例和天然纹理",
            "source": "fallback",
        }
        vision_config = self._effective_vision_llm_config()
        if not (vision_config and attachments):
            return fallback
        image_url = self._build_vision_image_url(attachments[0], current_user=current_user)
        if not image_url:
            return fallback
        base_url, api_key, model = vision_config
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是珠宝裸石设计助理。请只输出 JSON，所有字段值必须使用中文。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "分析这张裸石/玉石图片，输出 count, shape, color, transparency, texture, setting_direction, risk_notes, recommended_style。除 count 可为数字外，其余字段必须是中文短语或中文句子，不要夹英文。"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": 420,
        }
        try:
            started = perf_counter()
            async with httpx.AsyncClient(timeout=self.settings.agent_llm_timeout_seconds) as client:
                response = await client.post(
                    self._chat_completions_url(base_url),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                text = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
                parsed = json.loads(text.strip().strip("`").removeprefix("json").strip())
                if isinstance(parsed, dict):
                    parsed["source"] = "vision"
                    logger.info("agent_llm_call stage=stone_vision elapsed_ms=%d", int((perf_counter() - started) * 1000))
                    return parsed
        except Exception:  # noqa: BLE001
            return fallback
        return fallback

    def _build_vision_image_url(self, attachment: AgentAssetRef, *, current_user: User) -> str | None:
        source_url = attachment.storage_url or attachment.preview_url
        if not source_url:
            return None
        try:
            self.asset_service.ensure_storage_url_access(storage_url=source_url, current_user=current_user)
            content, media_type, _ = self.asset_service.fetch_asset_bytes(source_url, filename=attachment.name)
        except Exception:  # noqa: BLE001
            return source_url
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def _effective_vision_llm_config(self) -> tuple[str, str, str] | None:
        if self.settings.agent_vision_llm_base_url and self.settings.agent_vision_llm_api_key and self.settings.agent_vision_llm_model:
            return (
                self.settings.agent_vision_llm_base_url,
                self.settings.agent_vision_llm_api_key,
                self.settings.agent_vision_llm_model,
            )
        if self._agent_llm_model_can_accept_images() and self.settings.agent_llm_base_url and self.settings.agent_llm_api_key and self.settings.agent_llm_model:
            return (
                self.settings.agent_llm_base_url,
                self.settings.agent_llm_api_key,
                self.settings.agent_llm_model,
            )
        return None

    def _agent_llm_model_can_accept_images(self) -> bool:
        model = self.settings.agent_llm_model.strip().lower()
        image_capable_markers = ("qwen3.6", "qwen-vl", "qwen-omni", "gpt-4o", "gemini")
        return any(marker in model for marker in image_capable_markers)

    def _model_supports_thinking_toggle(self, model_name: str | None) -> bool:
        normalized = str(model_name or "").strip().lower()
        return normalized.startswith(("qwen3", "qwen-"))

    def _chat_completions_url(self, base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions"
        return f"{normalized}/v1/chat/completions"

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
        markdown = self._read_jewelry_terms_markdown()
        if not markdown:
            return []
        cards: list[dict[str, object]] = []
        current_h2 = ""
        current_h3 = ""
        lines = markdown.splitlines()
        line_count = len(lines)
        index = 0
        card_index = 0

        while index < line_count:
            raw_line = lines[index]
            stripped = raw_line.strip()
            if not stripped:
                index += 1
                continue
            if stripped.startswith("## "):
                current_h2 = stripped.removeprefix("## ").strip()
                current_h3 = ""
                index += 1
                continue
            if stripped.startswith("### "):
                current_h3 = stripped.removeprefix("### ").strip()
                index += 1
                continue
            if self._is_markdown_table_row(stripped):
                table_lines: list[str] = []
                while index < line_count and self._is_markdown_table_row(lines[index].strip()):
                    table_lines.append(lines[index].strip())
                    index += 1
                parsed_rows = self._parse_jewelry_markdown_table(table_lines, section=current_h2, subsection=current_h3)
                for row in parsed_rows:
                    card_index += 1
                    row["id"] = f"term-{card_index}"
                    cards.append(row)
                continue
            index += 1
        return cards

    def _is_markdown_table_row(self, line: str) -> bool:
        return line.startswith("|") and line.endswith("|") and line.count("|") >= 2

    def _parse_jewelry_markdown_table(
        self,
        table_lines: list[str],
        *,
        section: str,
        subsection: str,
    ) -> list[dict[str, object]]:
        if len(table_lines) < 3:
            return []
        headers = self._split_markdown_table_row(table_lines[0])
        if not headers or not self._is_markdown_separator_row(table_lines[1]):
            return []
        rows: list[dict[str, object]] = []
        title_keys = ("术语", "品类", "场景", "结构", "原则", "翡翠类型", "需求", "字段", "类型", "顺序", "优先级")
        ignored_headers = {"中文提示词", "注意事项"}
        for row_line in table_lines[2:]:
            values = self._split_markdown_table_row(row_line)
            if len(values) != len(headers):
                continue
            data = {headers[i]: values[i] for i in range(len(headers))}
            title = next((str(data.get(key) or "").strip() for key in title_keys if str(data.get(key) or "").strip()), "")
            if not title:
                continue
            parts: list[str] = []
            for header in headers:
                value = str(data.get(header) or "").strip()
                if not value or header in title_keys or header in ignored_headers:
                    continue
                cleaned = self._clean_prompt_fragment(value)
                if not cleaned:
                    continue
                parts.append(f"{header}：{cleaned}")
            prompt_fragment = str(data.get("中文提示词") or "").strip()
            if prompt_fragment:
                cleaned_prompt = self._clean_prompt_fragment(prompt_fragment)
                if cleaned_prompt:
                    parts.append(f"提示：{cleaned_prompt}")
            caution = str(data.get("注意事项") or "").strip()
            if caution:
                cleaned_caution = self._clean_prompt_fragment(caution)
                if cleaned_caution:
                    parts.append(f"注意：{cleaned_caution}")
            content = "；".join(parts)[:220]
            if not content:
                continue
            category = subsection or section or "专业描述"
            rows.append(
                {
                    "category": category[:32],
                    "title": title[:24],
                    "content": content,
                }
            )
        return rows

    def _split_markdown_table_row(self, row: str) -> list[str]:
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    def _is_markdown_separator_row(self, row: str) -> bool:
        cells = self._split_markdown_table_row(row)
        return bool(cells) and all(cell and set(cell) <= {"-", ":"} for cell in cells)

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
        return [item for item in self._required_design_slots(stone_analysis) if not self._design_slot_is_satisfied(item, brief, stone_analysis)]

    def _required_design_slots(self, stone_analysis: dict[str, object] | None) -> list[str]:
        required = ["category", "metal", "style", "craft", "scene"]
        if stone_analysis:
            return required
        else:
            required.insert(1, "gemstone")
        return required

    def _design_slot_is_satisfied(self, slot: str, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> bool:
        raw_value = brief.get(slot)
        if self._is_placeholder_design_value(raw_value):
            return False
        if slot == "gemstone":
            if stone_analysis:
                return True
            gemstone_value = str(brief.get("gemstone") or "").strip()
            if not gemstone_value or self._is_placeholder_design_value(gemstone_value):
                return False
            jade_profile = self._extract_jade_brief_profile(brief)
            return bool(
                gemstone_value
                and (
                    jade_profile.get("water")
                    or jade_profile.get("color")
                    or jade_profile.get("shape")
                    or jade_profile.get("count")
                    or len(gemstone_value) >= 2
                )
            )
        return bool(str(raw_value or "").strip())

    def _is_placeholder_design_value(self, value: object) -> bool:
        if value in (None, "", [], {}):
            return True
        if not isinstance(value, str):
            return False
        normalized = value.strip().lower().replace(" ", "")
        return normalized in {
            "待补充",
            "待确认",
            "待确定",
            "待细化",
            "未补充",
            "未确定",
            "暂无",
            "无",
            "none",
            "null",
            "n/a",
        }

    def _next_design_question(self, missing: list[str], stone_analysis: dict[str, object] | None) -> str:
        labels = {
            "category": "想做成什么品类，例如吊坠、戒指、耳环或胸针？",
            "concept": "这件作品想表达什么设计理念或情绪？",
            "gemstone": "这版先把翡翠主石定下来吧。可以直接选翡翠种水、颜色和形制，例如冰种蛋面、白冰水滴、飘花叶子，或直接补充玉石数量。",
            "metal": "金属材质倾向于 18K金、玫瑰金、白金还是银？",
            "style": "风格更偏现代、复古、东方、新中式、极简，还是更商业款？",
            "craft": "工艺上希望偏爪镶、包镶、围钻、花丝、镂空，还是交给 Agent 补全？",
            "scene": "佩戴/使用场景更偏日常通勤、晚宴聚会、收藏展示，还是婚礼礼服场合？",
        }
        if not missing:
            return "信息已经足够生成首版设计图。你可以直接点击「生成首版设计图」，也可以继续补充想强调的比例、佩戴场景或商业风格。"
        if stone_analysis and missing[0] == "concept":
            return "我已保留裸石作为核心。请补充这件镶嵌作品的设计理念，或者点击「Agent 补全设计摘要」让我先给出一版。"
        return labels.get(missing[0], "请继续补充你的设计想法。")

    def _strip_design_generate_phrases(self, content: str) -> str:
        stripped = content.strip()
        if not stripped:
            return ""
        cleaned = stripped
        phrases = [
            "生成首版设计图",
            "重新生成设计图",
            "重新生成",
            "生成设计图",
            "生成方案",
            "开始生成",
            "直接生成",
        ]
        for phrase in phrases:
            cleaned = cleaned.replace(phrase, " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _build_design_prompt(
        self,
        *,
        brief: dict[str, Any],
        selected_cards: list[dict[str, object]],
        stone_analysis: dict[str, object] | None,
        content: str,
    ) -> str:
        knowledge_text = self._design_language_from_knowledge_cards(selected_cards)
        stone_text = self._format_design_brief_for_prompt(stone_analysis or {})
        brief_text = self._format_design_brief_for_prompt(self._filtered_design_brief_for_prompt(brief))
        if stone_analysis:
            return (
                f"{DEFAULT_GEMSTONE_DESIGN_PROMPT} "
                f"设计要求：{brief_text}。"
                f"裸石特征：{stone_text}。"
                f"设计语言：{knowledge_text or '自然流畅的高级珠宝镶嵌语言，结构稳定，层次清晰'}。"
                f"{self._clean_user_generation_note(content)}"
            )
        prompt = (
            f"生成高级珠宝设计图：{brief_text}。"
            f"设计语言：{knowledge_text or '现代高级珠宝设计，结构清晰，比例优雅'}。"
            f"{self._clean_user_generation_note(content)}"
            f"{DEFAULT_TEXT_TO_IMAGE_PROMPT_SUFFIX}"
        )
        return prompt

    def _filtered_design_brief_for_prompt(self, brief: dict[str, Any]) -> dict[str, Any]:
        allowed = {"category", "concept", "gemstone", "metal", "style", "craft", "scene", "supplement", "stones"}
        return {key: value for key, value in brief.items() if key in allowed and value not in (None, "", [], {})}

    def _design_language_from_knowledge_cards(self, cards: list[dict[str, object]]) -> str:
        fragments: list[str] = []
        for card in cards[:4]:
            text = self._clean_prompt_fragment(str(card.get("content") or card.get("title") or ""))
            if not text or self._contains_prompt_meta_text(text):
                continue
            fragments.append(text)
        return "；".join(fragments[:3])

    def _clean_user_generation_note(self, content: str) -> str:
        stripped = content.strip()
        if not stripped or self._is_ready_generate_design_option(stripped) or self._is_design_generate_intent(stripped):
            return ""
        cleaned = self._clean_prompt_fragment(stripped)
        if not cleaned or self._contains_prompt_meta_text(cleaned):
            return ""
        return f"用户补充：{cleaned}。"

    def _contains_prompt_meta_text(self, text: str) -> bool:
        blocked = (
            "本文档",
            "文生图",
            "提示词",
            "模型",
            "表格",
            "Prompt",
            "prompt",
            "用于优化",
            "中文名称",
            "英文名称",
            "专业参考",
            "候选",
            "Gold-Silver",
            "gold-silver",
            "metal inlay work",
        )
        return any(item in text for item in blocked)

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
            return f"收到，我会基于当前设计摘要提交{mode_text}任务。生成完成后，只围绕这张设计图继续重做或修改设计摘要。"
        summary = self._format_design_brief_for_chat(brief, stone_analysis)
        next_question = self._next_design_question(missing, stone_analysis)
        if stone_analysis:
            return f"我已把裸石作为设计核心，并更新当前设计摘要：\n\n{summary}\n\n{next_question}"
        return f"我已更新当前设计摘要：\n\n{summary}\n\n{next_question}"

    def _build_design_ready_review_reply(self, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> str:
        summary = self._format_design_brief_for_review(brief, stone_analysis)
        if stone_analysis:
            return (
                "我先把这版设计摘要收拢给你确认：\n\n"
                f"{summary}\n\n"
                "如果这些信息没问题，可以直接点击「生成首版设计图」；如果还想细调，请点「继续补充设计要求」。"
            )
        return (
            "我先把目前收集到的设计摘要列给你确认：\n\n"
            f"{summary}\n\n"
            "如果这些信息没问题，可以直接点击「生成首版设计图」；如果还想细调，请点「继续补充设计要求」。"
        )

    def _build_design_continue_supplement_reply(self, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> str:
        summary = self._format_design_brief_for_review(brief, stone_analysis)
        return (
            "好的，当前设计摘要我先保留着：\n\n"
            f"{summary}\n\n"
            "你现在可以直接补充还想强调的设计理念、主石观感、镶嵌细节、佩戴场景或商业风格。"
        )

    def _format_design_brief_for_review(self, brief: dict[str, Any], stone_analysis: dict[str, object] | None) -> str:
        if stone_analysis:
            fields = [
                ("品类", self._review_value(brief.get("category"))),
                ("裸石数量", self._review_value(stone_analysis.get("count"))),
                ("裸石形状", self._review_value(stone_analysis.get("shape"))),
                ("裸石颜色", self._review_value(stone_analysis.get("color"))),
                ("透明度/种水", self._review_value(stone_analysis.get("transparency"))),
                ("镶嵌方向", self._review_value(stone_analysis.get("setting_direction"))),
                ("材质", self._review_value(brief.get("metal"))),
                ("镶嵌/工艺", self._review_value(brief.get("craft"))),
                ("风格", self._review_value(brief.get("style"))),
                ("场景", self._review_value(brief.get("scene"))),
                ("补充说明", self._review_value(brief.get("supplement"), default="无额外补充")),
            ]
        else:
            jade_profile = self._extract_jade_brief_profile(brief)
            fields = [
                ("品类", self._review_value(brief.get("category"))),
                ("主石种水", self._review_value(jade_profile.get("water"))),
                ("翡翠颜色", self._review_value(jade_profile.get("color"))),
                ("翡翠形制", self._review_value(jade_profile.get("shape"))),
                ("玉石数量", self._review_value(jade_profile.get("count"))),
                ("材质", self._review_value(brief.get("metal"))),
                ("镶嵌/工艺", self._review_value(brief.get("craft"))),
                ("风格", self._review_value(brief.get("style"))),
                ("场景", self._review_value(brief.get("scene"))),
                ("设计理念", self._review_value(brief.get("concept"))),
                ("补充说明", self._review_value(brief.get("supplement"))),
            ]
        return "\n".join(f"- {label}：{value}" for label, value in fields)

    def _extract_jade_brief_profile(self, brief: dict[str, Any]) -> dict[str, str]:
        gemstone_text = " ".join(
            str(item)
            for item in [
                brief.get("gemstone"),
                brief.get("concept"),
                brief.get("supplement"),
            ]
            if item
        )
        normalized = gemstone_text.replace("，", " ").replace("。", " ").replace("；", " ")
        profile = self._extract_jade_profile_from_text(normalized)
        if not profile["water"] and "翡翠" in normalized:
            profile["water"] = "翡翠主石，种水待细化"
        if not profile["shape"] and str(brief.get("category") or "").strip() in {"项链", "吊坠"}:
            profile["shape"] = "吊坠主石形制待细化"
        return profile

    def _extract_jade_profile_from_text(self, text: str) -> dict[str, str]:
        normalized = text.replace("，", " ").replace("。", " ").replace("；", " ")
        waters = ["玻璃种", "高冰种", "冰种", "冰糯种", "糯种", "豆种", "油青种", "蓝水", "晴水", "紫罗兰", "黄翡", "红翡", "墨翠", "飘花", "白冰", "春带彩"]
        colors = ["帝王绿", "满绿", "阳绿", "苹果绿", "白冰", "飘花", "紫罗兰", "黄翡", "红翡", "墨翠", "蓝水", "晴水", "春带彩", "绿色", "翠绿"]
        shapes = ["三角形", "三角", "圆形", "椭圆形", "方形", "长方形", "蛋面", "水滴", "平安扣", "无事牌", "叶子", "福豆", "福瓜", "葫芦", "马鞍戒面", "随形", "观音", "佛公"]
        return {
            "water": self._first_matching_phrase(normalized, waters) or "",
            "color": self._first_matching_phrase(normalized, colors) or "",
            "shape": self._first_matching_phrase(normalized, shapes) or "",
            "count": self._infer_jade_count(normalized),
        }

    def _infer_jade_count(self, text: str) -> str:
        normalized = text.replace("两", "二").replace("俩", "二")
        if any(token in normalized for token in ["三石", "三颗", "3颗", "3石"]):
            return "三颗主石/三石组合"
        if any(token in normalized for token in ["双石", "二石", "二颗", "2颗", "2石"]):
            return "双石组合"
        if any(token in normalized for token in ["群镶", "排镶", "多颗", "多石"]):
            return "多颗组合"
        if text.strip():
            return "单颗主石"
        return ""

    def _review_value(self, value: object, *, default: str = "待补充") -> str:
        text = str(value).strip() if value not in (None, "", [], {}) else ""
        return text or default

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
            return "- 暂未形成明确设计摘要"
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
        source_assets = attachments[-1:] if module_key == "product_refine" else attachments[:1]
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
                return custom_prompt
        if self._is_remove_selected_refine_intent(stripped):
            return PRODUCT_REFINE_REMOVE_SELECTED_PROMPT
        return stripped

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
        if module_key == "gemstone_design":
            return DEFAULT_GEMSTONE_DESIGN_MODEL
        if module_key == "multi_view":
            return DEFAULT_MULTI_VIEW_MODEL
        if module_key == "grayscale_relief":
            return DEFAULT_GRAYSCALE_RELIEF_MODEL
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

    def _resolve_workflow_active_attachments(
        self,
        *,
        conversation: AgentConversation,
        content: str,
        normalized_attachments: list[AgentAssetRef],
    ) -> list[AgentAssetRef]:
        if conversation.mode != "workflow":
            return normalized_attachments or self._load_recent_assets(conversation)
        if self._is_regenerate_intent(content):
            module_key = self._resolve_clear_workflow_module(content)
            previous_sources = self._load_generation_source_assets_for_module(conversation, module_key)
            if previous_sources:
                return previous_sources
        return normalized_attachments or self._load_recent_assets(conversation)

    def _is_regenerate_intent(self, content: str) -> bool:
        normalized = content.strip().lower().replace(" ", "")
        return "重新生成" in normalized or "重做" in normalized or "再来一版" in normalized or "回炉重造" in normalized

    def _load_generation_source_assets_for_module(self, conversation: AgentConversation, module_key: str | None) -> list[AgentAssetRef]:
        if not module_key:
            return []
        state = self._load_json(conversation.state_json) or {}
        source_assets_by_module = state.get("generation_source_assets_by_module")
        if not isinstance(source_assets_by_module, dict):
            return []
        raw_items = source_assets_by_module.get(module_key)
        if not isinstance(raw_items, list):
            return []
        refs: list[AgentAssetRef] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                refs.append(AgentAssetRef.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
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

    def _ensure_conversation_active(self, conversation: AgentConversation) -> None:
        if conversation.status != "active":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该 Agent 会话已结束。")

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
        raw = self._read_jewelry_terms_markdown()
        if not raw:
            return ""
        return "\n珠宝专业词库节选：\n" + raw[:6000]

    def _read_jewelry_terms_markdown(self) -> str:
        terms_path = Path(__file__).resolve().parents[4] / "docx" / "珠宝行业专业名词与描述语大全.md"
        if not terms_path.exists():
            return ""
        return terms_path.read_text(encoding="utf-8")

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
