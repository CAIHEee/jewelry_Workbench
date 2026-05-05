import type { GenerationJobStatus } from "./fusion";

export type AgentMode = "workflow" | "design";
export type AgentRole = "user" | "assistant" | "system";
export type AgentActionKind = "text_to_image" | "image_to_image" | "split_multi_view";
export type AgentActionStatus = "draft" | "confirmed" | "submitted" | "failed" | "cancelled";

export interface AgentAssetRef {
  asset_id?: string | null;
  name?: string | null;
  storage_url?: string | null;
  preview_url?: string | null;
}

export interface AgentConversation {
  id: string;
  mode: AgentMode;
  title: string;
  current_stage?: string | null;
  status: string;
  summary?: string | null;
  state?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface AgentMessage {
  id: string;
  conversation_id: string;
  role: AgentRole;
  content: string;
  attachments: AgentAssetRef[];
  event?: Record<string, unknown> | null;
  created_at: string;
}

export interface AgentAction {
  id: string;
  conversation_id: string;
  kind: AgentActionKind;
  module_key: string;
  status: AgentActionStatus;
  title: string;
  prompt?: string | null;
  params: Record<string, unknown>;
  source_assets: AgentAssetRef[];
  source_image_urls: string[];
  result_job_id?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentConversationDetail {
  conversation: AgentConversation;
  messages: AgentMessage[];
  actions: AgentAction[];
}

export interface AgentMemoryProposal {
  content: string;
  memory_type: string;
}

export interface AgentUserMemory {
  id: string;
  memory_type: string;
  content: string;
  is_enabled: boolean;
  source_conversation_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentActionConfirmResult {
  action: AgentAction;
  job_id: string;
  status: GenerationJobStatus;
  message: string;
}

export interface AgentGenerationResultRegister {
  action_id?: string | null;
  module_key: string;
  image_url: string;
  name?: string | null;
}

export interface AgentKnowledgeCard {
  id: string;
  category: string;
  title: string;
  content: string;
}

export interface AgentDesignOption {
  label: string;
  value: string;
  description?: string;
}

export interface AgentDesignState {
  design_brief: Record<string, unknown>;
  selected_knowledge_cards: AgentKnowledgeCard[];
  stone_analysis?: Record<string, unknown> | null;
  knowledge_cards: AgentKnowledgeCard[];
  latest_design_mode?: "text_to_image" | "gemstone_design";
  pending_design_options?: AgentDesignOption[];
  pending_design_question?: string | null;
  pending_design_option_source?: "llm" | "fallback" | "none" | string;
}
