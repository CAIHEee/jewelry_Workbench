import type { AdminSystemStatus, AdminUser, AdminUserListResponse } from "../types/admin";
import type { CurrentUser, LoginResponse, ModulePermissionItem } from "../types/auth";
import type {
  FusionRequest,
  FusionResult,
  GenerationJobAccepted,
  GenerationJobProgress,
  GenerationJobStatusResponse,
  GenerationResult,
  ModelCatalogResponse,
  MultiViewSplitRequest,
  MultiViewSplitResponse,
  ReferenceImageTransformRequest,
  TextToImageRequest,
} from "../types/fusion";
import type { PersistedAssetItem, PersistedAssetResponse } from "../types/assets";
import type { PersistedHistoryResponse } from "../types/history";
import type {
  AgentAction,
  AgentActionConfirmResult,
  AgentAssetRef,
  AgentConversation,
  AgentConversationDetail,
  AgentDesignOption,
  AgentDesignState,
  AgentGenerationResultRegister,
  AgentKnowledgeCard,
  AgentMemoryProposal,
  AgentMode,
  AgentUserMemory,
} from "../types/agent";
import type { AssetItem } from "../types/mockData";
import { buildGenerationJobProgress } from "../utils/jobProgress";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || "/api/v1").replace(/\/$/, "");
const AGENT_API_BASE_URL = (import.meta.env.VITE_AGENT_API_BASE_URL?.trim() || "/agent-api/v1").replace(/\/$/, "");

function buildApiUrl(path: string) {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

function buildAgentApiUrl(path: string) {
  return `${AGENT_API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  return fetch(input, {
    credentials: "include",
    ...init,
    headers: {
      ...(init?.headers ?? {}),
    },
  });
}

function normalizeApiErrorMessage(rawText: string, fallback = "请求失败"): string {
  const trimmed = rawText.trim();
  if (!trimmed) {
    return fallback;
  }

  try {
    const payload = JSON.parse(trimmed) as unknown;
    const normalizedKnown = normalizeKnownApiError(payload);
    if (normalizedKnown) {
      return normalizedKnown;
    }
    if (typeof payload === "string" && payload.trim()) {
      return payload;
    }
    if (payload && typeof payload === "object") {
      if ("detail" in payload) {
        const detail = (payload as { detail?: unknown }).detail;
        if (typeof detail === "string" && detail.trim()) {
          return detail;
        }
        if (Array.isArray(detail) && detail.length > 0) {
          const firstDetail = detail[0];
          if (typeof firstDetail === "string" && firstDetail.trim()) {
            return firstDetail;
          }
          if (firstDetail && typeof firstDetail === "object" && "msg" in firstDetail) {
            const msg = (firstDetail as { msg?: unknown }).msg;
            if (typeof msg === "string" && msg.trim()) {
              return msg;
            }
          }
        }
      }
      if ("message" in payload) {
        const message = (payload as { message?: unknown }).message;
        if (typeof message === "string" && message.trim()) {
          return message;
        }
      }
      if ("error" in payload) {
        const error = (payload as { error?: unknown }).error;
        if (typeof error === "string" && error.trim()) {
          return error;
        }
      }
    }
  } catch {
    // Keep the original text fallback below.
  }

  return trimmed;
}

function normalizeKnownApiError(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const detail = "detail" in payload ? (payload as { detail?: unknown }).detail : undefined;
  const upstreamStatus =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? ("upstream_status" in detail ? (detail as { upstream_status?: unknown }).upstream_status : undefined)
      : undefined;
  const upstreamPayload =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? ("upstream_response" in detail ? (detail as { upstream_response?: unknown }).upstream_response : undefined)
      : undefined;
  const upstreamError =
    upstreamPayload && typeof upstreamPayload === "object" && !Array.isArray(upstreamPayload)
      ? ("error" in upstreamPayload ? (upstreamPayload as { error?: unknown }).error : undefined)
      : undefined;

  if (upstreamError && typeof upstreamError === "object" && !Array.isArray(upstreamError)) {
    const code = "code" in upstreamError ? (upstreamError as { code?: unknown }).code : undefined;
    const message = "message" in upstreamError ? (upstreamError as { message?: unknown }).message : undefined;

    if (code === "insufficient_balance") {
      return "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。";
    }
    if (code === "model_not_found") {
      return "当前所选模型不可用，请切换模型或联系管理员检查模型配置。";
    }
    if (code === "Timeout") {
      return "当前所选 AI 服务处理超时，请稍后重试，或切换其他中转/模型。";
    }
    if (typeof message === "string" && message.includes("积分不足")) {
      return "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。";
    }
    if (typeof message === "string" && (message.includes("模型") && message.includes("不存在"))) {
      return "当前所选模型不可用，请切换模型或联系管理员检查模型配置。";
    }
    if (typeof message === "string" && (message.includes("timeout") || message.includes("超时"))) {
      return "当前所选 AI 服务处理超时，请稍后重试，或切换其他中转/模型。";
    }
    if (typeof message === "string" && (message.includes("Invalid token") || message.includes("Invalid Token"))) {
      return "当前所选 AI 服务认证失败，请联系管理员检查对应中转的 API Key。";
    }
    if (typeof message === "string" && (message.includes("self-signed certificate") || message.includes("certificate"))) {
      return "当前所选 AI 服务证书校验异常，请切换其他中转，或联系管理员检查该中转的 HTTPS 配置。";
    }
    if (typeof message === "string" && (message.includes("PNG") || message.includes("正方形") || message.includes("4MB"))) {
      return "上传图片不符合当前模型要求。请使用 PNG、正方形、且不超过 4MB 的图片后重试。";
    }
  }

  if (typeof detail === "string" && detail.includes("insufficient_balance")) {
    return "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。";
  }
  if (typeof detail === "string" && detail.includes("model_not_found")) {
    return "当前所选模型不可用，请切换模型或联系管理员检查模型配置。";
  }
  if (typeof detail === "string" && (detail.includes("Authentication required") || detail.includes("Unauthorized"))) {
    return "当前登录状态已失效，请重新登录后再试。";
  }
  if (typeof detail === "string" && detail.includes("Invalid username or password")) {
    return "用户名或密码错误，请检查后重试。";
  }
  if (typeof detail === "string" && detail.includes("User is disabled")) {
    return "该账号已被禁用，请联系管理员。";
  }
  if (typeof detail === "string" && detail.includes("User is deleted")) {
    return "该账号已被删除，请联系管理员。";
  }
  if (typeof detail === "string" && detail.includes("Username already exists")) {
    return "该用户名已存在，请换一个用户名。";
  }
  if (typeof detail === "string" && (detail.includes("SSLV3_ALERT_HANDSHAKE_FAILURE") || detail.includes("handshake failure"))) {
    return "当前所选 AI 服务连接异常，请稍后重试，或切换其他中转。";
  }
  if (typeof detail === "string" && detail.includes("self-signed certificate")) {
    return "当前所选 AI 服务证书校验异常，请切换其他中转，或联系管理员检查该中转的 HTTPS 配置。";
  }
  if (typeof detail === "string" && detail.includes("Failed to download upstream image asset")) {
    return "上游结果图下载失败，请稍后重试。若多次失败，请切换其他中转。";
  }

  if (upstreamStatus === 401) {
    return "当前所选 AI 服务认证失败，请联系管理员检查对应中转的 API Key。";
  }
  if (upstreamStatus === 402) {
    return "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。";
  }
  if (upstreamStatus === 404) {
    return "当前所选模型或上游资源不存在，请切换模型后重试。";
  }
  if (upstreamStatus === 408) {
    return "当前所选 AI 服务处理超时，请稍后重试，或切换其他中转/模型。";
  }
  if (upstreamStatus === 429) {
    return "当前所选 AI 服务请求过于频繁，请稍后再试。";
  }

  return null;
}

async function throwApiError(response: Response, fallback: string): Promise<never> {
  const errorText = await response.text();
  throw new Error(normalizeApiErrorMessage(errorText, fallback));
}

function isBlobUrl(value: string) {
  return value.startsWith("blob:");
}

function isCustomStorageUrl(value: string) {
  return value.startsWith("local://") || value.startsWith("oss://");
}

export function buildAssetContentUrl(storageUrl: string, filename?: string | null): string {
  const search = new URLSearchParams({ storage_url: storageUrl });
  if (filename) {
    search.set("filename", filename);
  }
  return `${buildApiUrl("/assets/content")}?${search.toString()}`;
}

async function handleJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    await throwApiError(response, "请求失败");
  }
  return (await response.json()) as T;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export async function fetchGenerationJob(jobId: string): Promise<GenerationJobStatusResponse> {
  const response = await apiFetch(buildApiUrl(`/ai/jobs/${jobId}`), { cache: "no-store" });
  return handleJsonResponse<GenerationJobStatusResponse>(response);
}

interface JobWaitOptions {
  onJobUpdate?: (job: GenerationJobStatusResponse, progress: GenerationJobProgress) => void;
  pollMs?: number;
  timeoutMs?: number;
}

async function waitForGenerationJobResult<T>(
  jobId: string,
  fallbackError: string,
  options?: JobWaitOptions,
): Promise<T> {
  async function resolveTerminalJob(job: GenerationJobStatusResponse): Promise<T> {
    if (job.status === "failed") {
      throw new Error(normalizeApiErrorMessage(job.error_message || job.message || "", fallbackError));
    }

    if (job.status === "succeeded") {
      if (isPlainObject(job.result)) {
        return job.result as T;
      }
      throw new Error("任务已完成，但没有返回有效结果。");
    }

    throw new Error("任务尚未完成。");
  }

  const pollMs = options?.pollMs ?? 1000;
  const timeoutMs = options?.timeoutMs ?? 10 * 60 * 1000;
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const job = await fetchGenerationJob(jobId);
    options?.onJobUpdate?.(job, buildGenerationJobProgress(job));

    if (job.status === "failed" || job.status === "succeeded") {
      return resolveTerminalJob(job);
    }

    await new Promise((resolve) => window.setTimeout(resolve, pollMs));
  }

  const finalJob = await fetchGenerationJob(jobId);
  options?.onJobUpdate?.(finalJob, buildGenerationJobProgress(finalJob));
  if (finalJob.status === "failed" || finalJob.status === "succeeded") {
    return resolveTerminalJob(finalJob);
  }

  throw new Error("任务处理超时，前端已停止轮询。请稍后到历史记录查看结果，或重新提交。");
}

export async function fetchAgentConversations(): Promise<AgentConversation[]> {
  const response = await apiFetch(buildAgentApiUrl("/conversations"), { cache: "no-store" });
  return handleJsonResponse<AgentConversation[]>(response);
}

export async function createAgentConversation(mode: AgentMode, title?: string): Promise<AgentConversation> {
  const response = await apiFetch(buildAgentApiUrl("/conversations"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, title }),
  });
  return handleJsonResponse<AgentConversation>(response);
}

export async function fetchAgentConversationDetail(conversationId: string): Promise<AgentConversationDetail> {
  const response = await apiFetch(buildAgentApiUrl(`/conversations/${conversationId}`), { cache: "no-store" });
  return handleJsonResponse<AgentConversationDetail>(response);
}

export async function deleteAgentConversation(conversationId: string): Promise<void> {
  const response = await apiFetch(buildAgentApiUrl(`/conversations/${conversationId}`), { method: "DELETE" });
  if (!response.ok) {
    await throwApiError(response, "删除 Agent 对话失败");
  }
}

export async function endAgentConversationTurn(conversationId: string): Promise<AgentConversationDetail> {
  const response = await apiFetch(buildAgentApiUrl(`/conversations/${conversationId}/end`), { method: "POST" });
  return handleJsonResponse<AgentConversationDetail>(response);
}

interface AgentStreamHandlers {
  onDelta?: (text: string) => void;
  onAction?: (action: AgentAction) => void;
  onDesignState?: (state: AgentDesignState) => void;
  onDesignOptions?: (options: AgentDesignOption[], meta?: { question?: string; source?: string }) => void;
  onOptionCardLoading?: (message: string) => void;
  onKnowledgeCards?: (cards: AgentKnowledgeCard[]) => void;
  onMemoryProposal?: (proposal: AgentMemoryProposal) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

export async function sendAgentMessageStream(
  conversationId: string,
  payload: { content: string; mode?: AgentMode; attachments?: AgentAssetRef[] },
  handlers: AgentStreamHandlers,
): Promise<void> {
  const response = await apiFetch(buildAgentApiUrl(`/conversations/${conversationId}/messages/stream`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, attachments: payload.attachments ?? [] }),
  });
  if (!response.ok || !response.body) {
    await throwApiError(response, "Agent 回复失败");
  }

  const body = response.body;
  if (!body) {
    throw new Error("Agent 回复失败");
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let currentDataLines: string[] = [];

  function dispatchAgentEvent(event: string | undefined, rawData: string) {
    let data: unknown = {};
    try {
      data = JSON.parse(rawData || "{}");
    } catch {
      data = {};
    }
    if (event === "message_delta" && data && typeof data === "object" && "text" in data) {
      handlers.onDelta?.(String((data as { text?: unknown }).text ?? ""));
    } else if (event === "action_card") {
      handlers.onAction?.(data as AgentAction);
    } else if (event === "design_state") {
      handlers.onDesignState?.(data as AgentDesignState);
    } else if (event === "design_options") {
      const items = data && typeof data === "object" && Array.isArray((data as { items?: unknown }).items) ? (data as { items: AgentDesignOption[] }).items : [];
      const question = data && typeof data === "object" && "question" in data ? String((data as { question?: unknown }).question ?? "") : "";
      const source = data && typeof data === "object" && "source" in data ? String((data as { source?: unknown }).source ?? "") : "";
      handlers.onDesignOptions?.(items, { question, source });
    } else if (event === "option_card_loading") {
      const message = data && typeof data === "object" && "message" in data ? String((data as { message?: unknown }).message ?? "正在生成选项卡") : "正在生成选项卡";
      handlers.onOptionCardLoading?.(message);
    } else if (event === "knowledge_cards") {
      const items = data && typeof data === "object" && Array.isArray((data as { items?: unknown }).items) ? (data as { items: AgentKnowledgeCard[] }).items : [];
      handlers.onKnowledgeCards?.(items);
    } else if (event === "memory_proposal") {
      handlers.onMemoryProposal?.(data as AgentMemoryProposal);
    } else if (event === "error") {
      const message = data && typeof data === "object" && "message" in data ? String((data as { message?: unknown }).message ?? "Agent 回复失败") : "Agent 回复失败";
      handlers.onError?.(message);
    } else if (event === "done") {
      handlers.onDone?.();
    }
  }

  function flushCurrentEvent() {
    if (!currentEvent && currentDataLines.length === 0) {
      return;
    }
    dispatchAgentEvent(currentEvent || "message", currentDataLines.join("\n"));
    currentEvent = "";
    currentDataLines = [];
  }

  function handleSseLine(rawLine: string) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      flushCurrentEvent();
      return;
    }
    if (line.startsWith(":")) {
      return;
    }
    const separatorIndex = line.indexOf(":");
    const field = separatorIndex >= 0 ? line.slice(0, separatorIndex) : line;
    let value = separatorIndex >= 0 ? line.slice(separatorIndex + 1) : "";
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      currentEvent = value;
    } else if (field === "data") {
      currentDataLines.push(value);
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach(handleSseLine);
    }
    buffer += decoder.decode();
    if (buffer) {
      buffer.split("\n").forEach(handleSseLine);
      buffer = "";
    }
    flushCurrentEvent();
  } finally {
    reader.releaseLock();
  }
}

export async function updateAgentDesignState(
  conversationId: string,
  payload: { design_brief?: Record<string, unknown>; selected_knowledge_cards?: AgentKnowledgeCard[] },
): Promise<AgentDesignState> {
  const response = await apiFetch(buildAgentApiUrl(`/conversations/${conversationId}/design-state`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<AgentDesignState>(response);
}

export async function confirmAgentAction(actionId: string, payload?: {
  prompt?: string | null;
  params?: Record<string, unknown>;
  source_assets?: AgentAssetRef[];
  source_image_urls?: string[];
}): Promise<AgentActionConfirmResult> {
  const response = await apiFetch(buildAgentApiUrl(`/actions/${actionId}/confirm`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  return handleJsonResponse<AgentActionConfirmResult>(response);
}

export async function registerAgentGenerationResult(
  conversationId: string,
  payload: AgentGenerationResultRegister,
): Promise<AgentAssetRef> {
  const response = await apiFetch(buildAgentApiUrl(`/conversations/${conversationId}/generation-result`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<AgentAssetRef>(response);
}

export async function waitForAgentJobResult<T>(
  jobId: string,
  fallbackError: string,
  options?: JobWaitOptions,
): Promise<T> {
  return waitForGenerationJobResult<T>(jobId, fallbackError, options);
}

export async function fetchAgentMemories(): Promise<AgentUserMemory[]> {
  const response = await apiFetch(buildAgentApiUrl("/memories"), { cache: "no-store" });
  return handleJsonResponse<AgentUserMemory[]>(response);
}

export async function createAgentMemory(payload: {
  content: string;
  memory_type?: string;
  source_conversation_id?: string | null;
}): Promise<AgentUserMemory> {
  const response = await apiFetch(buildAgentApiUrl("/memories"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<AgentUserMemory>(response);
}

export async function updateAgentMemory(memoryId: string, payload: { is_enabled?: boolean; content?: string }): Promise<AgentUserMemory> {
  const response = await apiFetch(buildAgentApiUrl(`/memories/${memoryId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<AgentUserMemory>(response);
}

export async function deleteAgentMemory(memoryId: string): Promise<void> {
  const response = await apiFetch(buildAgentApiUrl(`/memories/${memoryId}`), { method: "DELETE" });
  if (!response.ok) {
    await throwApiError(response, "删除记忆失败");
  }
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await apiFetch(buildApiUrl("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return handleJsonResponse<LoginResponse>(response);
}

export async function registerUser(payload: {
  username: string;
  password: string;
  display_name?: string;
  email?: string;
}): Promise<LoginResponse> {
  const response = await apiFetch(buildApiUrl("/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<LoginResponse>(response);
}

export async function logout(): Promise<void> {
  const response = await apiFetch(buildApiUrl("/auth/logout"), { method: "POST" });
  if (!response.ok) {
    await throwApiError(response, "退出登录失败");
  }
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  const response = await apiFetch(buildApiUrl("/auth/me"), { cache: "no-store" });
  return handleJsonResponse<CurrentUser>(response);
}

export async function fetchModelCatalog(): Promise<ModelCatalogResponse> {
  const response = await apiFetch(buildApiUrl("/ai/models"), { cache: "no-store" });
  return handleJsonResponse<ModelCatalogResponse>(response);
}

export async function submitTextToImage(payload: TextToImageRequest, options?: JobWaitOptions): Promise<GenerationResult> {
  const response = await apiFetch(buildApiUrl("/ai/jobs/text-to-image"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const accepted = await handleJsonResponse<GenerationJobAccepted>(response);
  return waitForGenerationJobResult<GenerationResult>(accepted.job_id, "图片生成失败", options);
}

export async function submitFusionJob(payload: FusionRequest, options?: JobWaitOptions): Promise<FusionResult> {
  const formData = new FormData();
  payload.files?.forEach((file) => formData.append("images", file));
  if (payload.sourceImageUrls?.length) {
    formData.append("source_image_urls_json", JSON.stringify(payload.sourceImageUrls));
  }
  if (payload.sourceImageNames?.length) {
    formData.append("source_image_names_json", JSON.stringify(payload.sourceImageNames));
  }
  formData.append("model", payload.model);
  formData.append("prompt", payload.prompt);
  formData.append("mode", payload.mode);
  formData.append("primary_image_index", String(payload.primaryImageIndex));
  formData.append("strength", String(payload.strength));

  const response = await apiFetch(buildApiUrl("/ai/jobs/fuse-images"), {
    method: "POST",
    body: formData,
  });
  const accepted = await handleJsonResponse<GenerationJobAccepted>(response);
  return waitForGenerationJobResult<FusionResult>(accepted.job_id, "融合任务提交失败", options);
}

export async function submitReferenceImageTransform(payload: ReferenceImageTransformRequest, options?: JobWaitOptions): Promise<GenerationResult> {
  return submitReferenceModuleTransform("/ai/reference-image-transform", payload, options);
}

export async function submitReferenceModuleTransform(path: string, payload: ReferenceImageTransformRequest, options?: JobWaitOptions): Promise<GenerationResult> {
  const formData = new FormData();
  payload.files?.forEach((file) => formData.append("images", file));
  if (payload.file) formData.append("image", payload.file);
  if (payload.sourceImageUrls?.length) {
    formData.append("source_image_urls_json", JSON.stringify(payload.sourceImageUrls));
  }
  if (payload.sourceImageNames?.length) {
    formData.append("source_image_names_json", JSON.stringify(payload.sourceImageNames));
  }
  if (payload.sourceImageUrl) formData.append("source_image_url", payload.sourceImageUrl);
  if (payload.sourceImageName) formData.append("source_image_name", payload.sourceImageName);
  formData.append("model", payload.model);
  formData.append("prompt", payload.prompt);
  formData.append("feature", payload.feature);
  if (payload.negativePrompt) formData.append("negative_prompt", payload.negativePrompt);
  if (typeof payload.strength === "number") formData.append("strength", String(payload.strength));
  if (payload.imageSize) formData.append("image_size", payload.imageSize);

  const jobPath = path.startsWith("/ai/") ? path.replace("/ai/", "/ai/jobs/") : path;
  const response = await apiFetch(buildApiUrl(jobPath), {
    method: "POST",
    body: formData,
  });
  const accepted = await handleJsonResponse<GenerationJobAccepted>(response);
  return waitForGenerationJobResult<GenerationResult>(accepted.job_id, "图片生成失败", options);
}

export async function submitMultiViewGeneration(payload: ReferenceImageTransformRequest, options?: JobWaitOptions): Promise<GenerationResult> {
  const formData = new FormData();
  payload.files?.forEach((file) => formData.append("images", file));
  if (payload.file) formData.append("image", payload.file);
  if (payload.sourceImageUrls?.length) {
    formData.append("source_image_urls_json", JSON.stringify(payload.sourceImageUrls));
  }
  if (payload.sourceImageNames?.length) {
    formData.append("source_image_names_json", JSON.stringify(payload.sourceImageNames));
  }
  if (payload.sourceImageUrl) formData.append("source_image_url", payload.sourceImageUrl);
  if (payload.sourceImageName) formData.append("source_image_name", payload.sourceImageName);
  formData.append("model", payload.model);
  formData.append("prompt", payload.prompt);
  if (payload.negativePrompt) formData.append("negative_prompt", payload.negativePrompt);
  if (typeof payload.strength === "number") formData.append("strength", String(payload.strength));

  const response = await apiFetch(buildApiUrl("/ai/jobs/multi-view"), {
    method: "POST",
    body: formData,
  });
  const accepted = await handleJsonResponse<GenerationJobAccepted>(response);
  return waitForGenerationJobResult<GenerationResult>(accepted.job_id, "多视图生成失败", options);
}

export async function splitMultiViewImage(payload: MultiViewSplitRequest, options?: JobWaitOptions): Promise<MultiViewSplitResponse> {
  const response = await apiFetch(buildApiUrl("/ai/jobs/split-multi-view"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const accepted = await handleJsonResponse<GenerationJobAccepted>(response);
  return waitForGenerationJobResult<MultiViewSplitResponse>(accepted.job_id, "多视图切图失败", options);
}

export async function submitRemoveBackground(payload: { file?: File | null; sourceImageUrl?: string | null }): Promise<Blob> {
  const formData = new FormData();
  if (payload.file) formData.append("image", payload.file);
  if (payload.sourceImageUrl) formData.append("source_image_url", payload.sourceImageUrl);

  const response = await apiFetch(buildApiUrl("/ai/remove-background"), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    await throwApiError(response, "去除背景失败");
  }
  return response.blob();
}

export async function fetchPersistedHistory(includeAll = false): Promise<PersistedHistoryResponse> {
  const search = new URLSearchParams({ _: String(Date.now()) });
  if (includeAll) search.set("include_all", "true");
  const response = await apiFetch(`${buildApiUrl("/history")}?${search.toString()}`, { cache: "no-store" });
  return handleJsonResponse<PersistedHistoryResponse>(response);
}

export async function fetchPersistedAssets(scope = "library"): Promise<PersistedAssetResponse> {
  const search = new URLSearchParams({ _: String(Date.now()), scope });
  const response = await apiFetch(`${buildApiUrl("/assets")}?${search.toString()}`, { cache: "no-store" });
  return handleJsonResponse<PersistedAssetResponse>(response);
}

export async function uploadInputAsset(
  file: File,
  moduleKind: string,
  sourceKind = "manual_upload",
  visibility: "private" | "community" = "private",
) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("module_kind", moduleKind);
  formData.append("source_kind", sourceKind);
  formData.append("visibility", visibility);

  const response = await apiFetch(buildApiUrl("/assets/upload"), {
    method: "POST",
    body: formData,
  });
  return handleJsonResponse<PersistedAssetItem>(response);
}

export async function publishPersistedAsset(assetId: string): Promise<PersistedAssetItem> {
  const response = await apiFetch(buildApiUrl(`/assets/${assetId}/publish`), { method: "POST" });
  return handleJsonResponse<PersistedAssetItem>(response);
}

export async function unpublishPersistedAsset(assetId: string): Promise<PersistedAssetItem> {
  const response = await apiFetch(buildApiUrl(`/assets/${assetId}/unpublish`), { method: "POST" });
  return handleJsonResponse<PersistedAssetItem>(response);
}

export async function deletePersistedAsset(assetId: string): Promise<void> {
  const response = await apiFetch(buildApiUrl(`/assets/${assetId}`), { method: "DELETE" });
  if (!response.ok) {
    await throwApiError(response, "删除资产失败");
  }
}

export async function deletePersistedHistory(historyId: string): Promise<void> {
  const response = await apiFetch(buildApiUrl(`/history/${historyId}`), { method: "DELETE" });
  if (!response.ok) {
    await throwApiError(response, "删除历史记录失败");
  }
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  const response = await apiFetch(buildApiUrl("/admin/users"), { cache: "no-store" });
  const body = await handleJsonResponse<AdminUserListResponse>(response);
  return body.items;
}

export async function fetchAdminSystemStatus(): Promise<AdminSystemStatus> {
  const response = await apiFetch(buildApiUrl("/admin/system-status"), { cache: "no-store" });
  return handleJsonResponse<AdminSystemStatus>(response);
}

export async function createAdminUser(payload: {
  username: string;
  display_name?: string;
  email?: string;
  password: string;
  is_disabled?: boolean;
}): Promise<AdminUser> {
  const response = await apiFetch(buildApiUrl("/admin/users"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<AdminUser>(response);
}

export async function updateAdminUser(
  userId: string,
  payload: { username?: string; display_name?: string | null; email?: string | null; is_disabled?: boolean },
): Promise<AdminUser> {
  const response = await apiFetch(buildApiUrl(`/admin/users/${userId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<AdminUser>(response);
}

export async function resetAdminUserPassword(userId: string, password: string): Promise<void> {
  const response = await apiFetch(buildApiUrl(`/admin/users/${userId}/reset-password`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    await throwApiError(response, "重置密码失败");
  }
}

export async function updateAdminUserPermissions(userId: string, items: ModulePermissionItem[]): Promise<ModulePermissionItem[]> {
  const response = await apiFetch(buildApiUrl(`/admin/users/${userId}/permissions`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      items: items.map((item) => ({ module_key: item.module_key, is_enabled: item.is_enabled })),
    }),
  });
  return handleJsonResponse<ModulePermissionItem[]>(response);
}

export async function deleteAdminUser(userId: string): Promise<void> {
  const response = await apiFetch(buildApiUrl(`/admin/users/${userId}`), { method: "DELETE" });
  if (!response.ok) {
    await throwApiError(response, "删除用户失败");
  }
}

export async function assetItemToFile(asset: AssetItem): Promise<File> {
  let assetUrl = asset.fileUrl ?? null;
  if (!assetUrl && asset.storageUrl && isCustomStorageUrl(asset.storageUrl)) {
    assetUrl = buildAssetContentUrl(asset.storageUrl, asset.name);
  }
  if (!assetUrl) assetUrl = asset.previewUrl ?? asset.storageUrl ?? null;
  if (!assetUrl) throw new Error(`资产 ${asset.name} 缺少可用图片地址`);
  if (isBlobUrl(assetUrl)) throw new Error(`资产 ${asset.name} 的当前会话预览已失效，请刷新后重试或重新上传。`);

  const response = await apiFetch(assetUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`读取资产图片失败：${asset.name}`);

  const blob = await response.blob();
  const mimeType = blob.type || "image/png";
  const extension = mimeType.includes("/") ? mimeType.split("/")[1] : "png";
  const fallbackName = `${asset.id}.${extension}`;
  const filename = asset.name.includes(".") ? asset.name : fallbackName;

  return new File([blob], filename, {
    type: mimeType,
    lastModified: Date.now(),
  });
}
