from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


AgentMode = Literal["workflow", "design"]
AgentRole = Literal["user", "assistant", "system"]
AgentActionKind = Literal["text_to_image", "image_to_image", "split_multi_view"]
AgentActionStatus = Literal["draft", "confirmed", "submitted", "failed", "cancelled"]


class AgentAssetRef(BaseModel):
    asset_id: str | None = None
    name: str | None = None
    storage_url: str | None = None
    preview_url: str | None = None


class AgentActionCard(BaseModel):
    id: str | None = None
    kind: AgentActionKind
    module_key: str
    title: str = Field(min_length=1)
    prompt: str | None = None
    params: dict[str, object] = Field(default_factory=dict)
    source_assets: list[AgentAssetRef] = Field(default_factory=list)
    source_image_urls: list[str] = Field(default_factory=list)
    editable_prompt: bool = False
    next_question: str | None = None


class AgentMemoryProposal(BaseModel):
    content: str = Field(min_length=1)
    memory_type: str = "preference"


class AgentConversationCreate(BaseModel):
    mode: AgentMode = "workflow"
    title: str | None = None


class AgentConversationResponse(BaseModel):
    id: str
    mode: AgentMode
    title: str
    current_stage: str | None = None
    status: str
    summary: str | None = None
    state: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime


class AgentMessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: AgentRole
    content: str
    attachments: list[AgentAssetRef] = Field(default_factory=list)
    event: dict[str, object] | None = None
    created_at: datetime


class AgentActionResponse(BaseModel):
    id: str
    conversation_id: str
    kind: AgentActionKind
    module_key: str
    status: AgentActionStatus
    title: str
    prompt: str | None = None
    params: dict[str, object] = Field(default_factory=dict)
    source_assets: list[AgentAssetRef] = Field(default_factory=list)
    source_image_urls: list[str] = Field(default_factory=list)
    result_job_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentConversationDetail(BaseModel):
    conversation: AgentConversationResponse
    messages: list[AgentMessageResponse]
    actions: list[AgentActionResponse]


class AgentMessageCreate(BaseModel):
    content: str = ""
    mode: AgentMode | None = None
    attachments: list[AgentAssetRef] = Field(default_factory=list)


class AgentDesignStateUpdate(BaseModel):
    design_brief: dict[str, object] | None = None
    selected_knowledge_cards: list[dict[str, object]] | None = None


class AgentActionConfirmRequest(BaseModel):
    prompt: str | None = None
    params: dict[str, object] | None = None
    source_assets: list[AgentAssetRef] | None = None
    source_image_urls: list[str] | None = None


class AgentActionConfirmResponse(BaseModel):
    action: AgentActionResponse
    job_id: str
    status: str
    message: str


class AgentGenerationResultRegister(BaseModel):
    action_id: str | None = None
    module_key: str
    image_url: str = Field(min_length=1)
    name: str | None = None


class AgentUserMemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    memory_type: str = "preference"
    source_conversation_id: str | None = None


class AgentUserMemoryResponse(BaseModel):
    id: str
    memory_type: str
    content: str
    is_enabled: bool
    source_conversation_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentUserMemoryUpdate(BaseModel):
    is_enabled: bool | None = None
    content: str | None = None
