from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.api.deps import require_module
from app.core.config import get_settings
from app.db.session import init_db
from app.models.user import User
from app.schemas.agent import (
    AgentActionConfirmRequest,
    AgentActionConfirmResponse,
    AgentConversationCreate,
    AgentConversationDetail,
    AgentConversationResponse,
    AgentDesignStateUpdate,
    AgentGenerationResultRegister,
    AgentMessageCreate,
    AgentAssetRef,
    AgentUserMemoryCreate,
    AgentUserMemoryResponse,
    AgentUserMemoryUpdate,
)
from app.services.agent_service import AgentService


settings = get_settings()
service = AgentService()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(
    title="Jinma Jewelry Agent Service",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.agent_service_allowed_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse(event: str, data: object) -> str:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")  # type: ignore[union-attr]
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@app.get("/agent-health", tags=["health"])
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.get("/agent-api/v1/conversations", response_model=list[AgentConversationResponse])
def list_conversations(current_user: User = Depends(require_module("ai_agent"))) -> list[AgentConversationResponse]:
    return service.list_conversations(current_user=current_user)


@app.post("/agent-api/v1/conversations", response_model=AgentConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: AgentConversationCreate,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentConversationResponse:
    return service.create_conversation(current_user=current_user, mode=payload.mode, title=payload.title)


@app.get("/agent-api/v1/conversations/{conversation_id}", response_model=AgentConversationDetail)
def get_conversation_detail(
    conversation_id: str,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentConversationDetail:
    return service.get_conversation_detail(conversation_id=conversation_id, current_user=current_user)


@app.patch("/agent-api/v1/conversations/{conversation_id}/design-state")
def update_design_state(
    conversation_id: str,
    payload: AgentDesignStateUpdate,
    current_user: User = Depends(require_module("ai_agent")),
) -> dict[str, object]:
    return service.update_design_state(
        conversation_id=conversation_id,
        current_user=current_user,
        design_brief=payload.design_brief,
        selected_knowledge_cards=payload.selected_knowledge_cards,
    )


@app.delete("/agent-api/v1/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(require_module("ai_agent")),
) -> Response:
    service.delete_conversation(conversation_id=conversation_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/agent-api/v1/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    payload: AgentMessageCreate,
    current_user: User = Depends(require_module("ai_agent")),
) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        try:
            async for event, data in service.stream_user_message(
                conversation_id=conversation_id,
                current_user=current_user,
                content=payload.content,
                attachments=payload.attachments,
            ):
                yield _sse(event, data)
            yield _sse("done", {"ok": True})
        except HTTPException as exc:
            yield _sse("error", {"message": exc.detail, "status_code": exc.status_code})
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"message": str(exc), "status_code": 500})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/agent-api/v1/actions/{action_id}/confirm", response_model=AgentActionConfirmResponse)
def confirm_action(
    action_id: str,
    payload: AgentActionConfirmRequest,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentActionConfirmResponse:
    return service.confirm_action(action_id=action_id, current_user=current_user, payload=payload)


@app.post("/agent-api/v1/conversations/{conversation_id}/generation-result", response_model=AgentAssetRef)
def register_generation_result(
    conversation_id: str,
    payload: AgentGenerationResultRegister,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentAssetRef:
    return service.register_generation_result(
        conversation_id=conversation_id,
        current_user=current_user,
        module_key=payload.module_key,
        image_url=payload.image_url,
        name=payload.name,
        action_id=payload.action_id,
    )


@app.post("/agent-api/v1/conversations/{conversation_id}/end", response_model=AgentConversationDetail)
def end_conversation_turn(
    conversation_id: str,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentConversationDetail:
    return service.end_conversation_turn(conversation_id=conversation_id, current_user=current_user)


@app.get("/agent-api/v1/memories", response_model=list[AgentUserMemoryResponse])
def list_memories(current_user: User = Depends(require_module("ai_agent"))) -> list[AgentUserMemoryResponse]:
    return service.list_memories(current_user=current_user)


@app.post("/agent-api/v1/memories", response_model=AgentUserMemoryResponse, status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: AgentUserMemoryCreate,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentUserMemoryResponse:
    return service.create_memory(
        current_user=current_user,
        content=payload.content,
        memory_type=payload.memory_type,
        source_conversation_id=payload.source_conversation_id,
    )


@app.patch("/agent-api/v1/memories/{memory_id}", response_model=AgentUserMemoryResponse)
def update_memory(
    memory_id: str,
    payload: AgentUserMemoryUpdate,
    current_user: User = Depends(require_module("ai_agent")),
) -> AgentUserMemoryResponse:
    return service.update_memory(
        memory_id=memory_id,
        current_user=current_user,
        is_enabled=payload.is_enabled,
        content=payload.content,
    )


@app.delete("/agent-api/v1/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, current_user: User = Depends(require_module("ai_agent"))) -> Response:
    service.delete_memory(memory_id=memory_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
