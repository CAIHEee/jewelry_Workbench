import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { FloatingToast } from "../components/FloatingToast";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import {
  confirmAgentAction,
  createAgentConversation,
  createAgentMemory,
  deleteAgentConversation,
  deleteAgentMemory,
  fetchGenerationJob,
  fetchAgentConversationDetail,
  fetchAgentConversations,
  fetchAgentMemories,
  registerAgentGenerationResult,
  sendAgentMessageStream,
  updateAgentMemory,
  uploadInputAsset,
  waitForAgentJobResult,
} from "../services/api";
import type {
  AgentAction,
  AgentAssetRef,
  AgentConversation,
  AgentDesignOption,
  AgentMemoryProposal,
  AgentMessage,
  AgentMode,
  AgentUserMemory,
} from "../types/agent";
import type { GenerationJobProgress, GenerationResult, MultiViewSplitResponse } from "../types/fusion";
import type { AssetItem } from "../types/mockData";
import { buildGenerationJobProgress } from "../utils/jobProgress";

interface AgentPageProps {
  assetItems: AssetItem[];
}

interface AgentFlowOption {
  title: string;
  helper: string;
  prompt: string;
  behavior?: "send" | "draft_refine_prompt" | "regenerate_from_sources" | "draft_design_revision" | "end";
}

interface ActiveGenerationPreview {
  actionTitle: string;
  moduleKey: string;
  sourceUrl: string | null;
  resultUrl: string | null;
  resultAsset: AgentAssetRef | null;
}

interface AgentLightboxState {
  title: string;
  sourceUrl: string | null;
  resultUrl: string;
}

interface ResultOptionContext {
  messageId?: string;
  resultAsset?: AgentAssetRef | null;
  sourceAssets?: AgentAssetRef[];
}

const moduleLabels: Record<string, string> = {
  text_to_image: "设计出图",
  gemstone_design: "裸石镶嵌设计",
  sketch_to_realistic: "线稿转写实图",
  product_refine: "产品精修",
  multi_view: "生成多视图",
  grayscale_relief: "转灰度图",
  multi_view_split: "多视图切图",
};

const workflowOptions: AgentFlowOption[] = [];

const defaultResultStepOptions: AgentFlowOption[] = [
  {
    title: "不满意，回炉重造",
    helper: "沿用本次输入图，重新生成一版当前结果",
    prompt: "重新生成写实图",
    behavior: "regenerate_from_sources",
  },
  {
    title: "产品精修",
    helper: "选择默认精修，或补充自己的精修要求",
    prompt: "",
    behavior: "draft_refine_prompt",
  },
  {
    title: "生成多视图",
    helper: "基于最新写实图生成正侧背四视图",
    prompt: "生成多视图",
  },
  {
    title: "结束对话",
    helper: "本轮结果已确认，暂时不继续生成",
    prompt: "结束对话",
    behavior: "end",
  },
];

const multiViewResultStepOptions: AgentFlowOption[] = [
  {
    title: "重新生成多视图",
    helper: "基于最新写实图重新生成一版多视图",
    prompt: "重新生成多视图",
  },
  {
    title: "生成灰度图",
    helper: "基于当前多视图结果生成灰度立体化参考",
    prompt: "生成灰度图",
  },
  {
    title: "结束对话",
    helper: "本轮结果已确认，暂时不继续生成",
    prompt: "结束对话",
    behavior: "end",
  },
];

const grayscaleResultStepOptions: AgentFlowOption[] = [
  {
    title: "重新生成灰度图",
    helper: "基于当前结果重新生成灰度立体化参考",
    prompt: "重新生成灰度图",
  },
  {
    title: "结束对话",
    helper: "本轮结果已确认，暂时不继续生成",
    prompt: "结束对话",
    behavior: "end",
  },
];

const designResultStepOptions: AgentFlowOption[] = [
  {
    title: "重新生成",
    helper: "沿用当前 brief 和裸石来源，重新生成一版设计图",
    prompt: "重新生成设计图",
    behavior: "regenerate_from_sources",
  },
  {
    title: "修改设计",
    helper: "回到 brief 继续补充理念、材质、风格或工艺",
    prompt: "我想调整设计：",
    behavior: "draft_design_revision",
  },
  {
    title: "结束对话",
    helper: "本轮设计结果已确认，暂时不继续生成",
    prompt: "结束对话",
    behavior: "end",
  },
];

const refineChoiceOptions: AgentFlowOption[] = [
  {
    title: "进行默认精修",
    helper: "使用系统默认精修提示词，直接提交产品精修",
    prompt: "直接精修",
  },
  {
    title: "补充精修提示词",
    helper: "在默认精修策略基础上，补充你想重点修改的地方",
    prompt: "产品精修：",
  },
  {
    title: "仅用自定义提示词",
    helper: "不叠加默认精修词，只按你的提示词进行精修",
    prompt: "仅自定义精修：",
  },
];

function getActionResultImage(result: unknown): string | null {
  if (!result || typeof result !== "object") return null;
  if ("image_url" in result && typeof (result as GenerationResult).image_url === "string") {
    return (result as GenerationResult).image_url;
  }
  if ("items" in result && Array.isArray((result as MultiViewSplitResponse).items)) {
    return (result as MultiViewSplitResponse).items.find((item) => item.image_url)?.image_url ?? null;
  }
  return null;
}

function isFollowUpStep(content: string) {
  return /多视图|四视图|精修|灰度|立体|优化|修一下/.test(content);
}

function getAssetPreviewUrl(asset: AgentAssetRef | null | undefined): string | null {
  return asset?.preview_url ?? asset?.storage_url ?? null;
}

function getGenerationEvent(message: AgentMessage): {
  type?: string;
  module_key?: string;
  title?: string;
  source_assets?: AgentAssetRef[];
  result_asset?: AgentAssetRef;
} | null {
  const event = message.event;
  if (!event || typeof event !== "object" || event.type !== "generation_result") {
    return null;
  }
  return event as {
    type?: string;
    module_key?: string;
    title?: string;
    source_assets?: AgentAssetRef[];
    result_asset?: AgentAssetRef;
  };
}

function getResultStepOptions(moduleKey: string | undefined): AgentFlowOption[] {
  if (moduleKey === "text_to_image" || moduleKey === "gemstone_design") return designResultStepOptions;
  if (moduleKey === "multi_view") return multiViewResultStepOptions;
  if (moduleKey === "grayscale_relief") return grayscaleResultStepOptions;
  return defaultResultStepOptions;
}

function getGenerationResultCopy(moduleKey: string | undefined, fallback: string) {
  if (moduleKey === "text_to_image" || moduleKey === "gemstone_design") {
    return "设计图生成完成。可以基于当前 brief 重新生成，或继续修改设计理念后再出一版。";
  }
  if (moduleKey === "multi_view") {
    return "多视图生成完成。对结果满意吗？可以重新生成，或进入下一步生成灰度图。";
  }
  if (moduleKey === "grayscale_relief") {
    return "灰度图生成完成。对结果满意吗？可以重新生成，或结束本轮对话。";
  }
  return fallback;
}

function getRefineAttachments(context: ResultOptionContext): AgentAssetRef[] {
  const refs = [...(context.sourceAssets ?? [])];
  if (context.resultAsset) refs.push(context.resultAsset);
  const seen = new Set<string>();
  return refs.filter((item) => {
    const key = item.asset_id ?? item.storage_url ?? item.preview_url ?? item.name;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function getPrimaryResultAttachment(context: ResultOptionContext): AgentAssetRef[] | undefined {
  return context.resultAsset ? [context.resultAsset] : undefined;
}

function formatConversationTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (item: number) => String(item).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

function normalizeDesignOptions(items: unknown): AgentDesignOption[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    .map((item) => ({
      label: String(item.label ?? item.value ?? "").trim(),
      value: String(item.value ?? item.label ?? "").trim(),
      description: item.description ? String(item.description) : undefined,
    }))
    .filter((item) => item.label && item.value)
    .slice(0, 4);
}

function getConversationDisplayTitle(conversation: AgentConversation) {
  if (/^(agent工作流|设计出图)_\d{8}_\d{6}$/.test(conversation.title)) {
    return conversation.title;
  }
  return `${conversation.mode === "design" ? "设计出图" : "agent工作流"}_${formatConversationTimestamp(conversation.created_at)}`;
}

function AgentMarkdown({ content }: { content: string }) {
  return (
    <div className="agent-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function AgentPage({ assetItems }: AgentPageProps) {
  const [mode, setMode] = useState<AgentMode>("workflow");
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [, setActions] = useState<AgentAction[]>([]);
  const [memories, setMemories] = useState<AgentUserMemory[]>([]);
  const [memoryProposal, setMemoryProposal] = useState<AgentMemoryProposal | null>(null);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssetItems, setSelectedAssetItems] = useState<AssetItem[]>([]);
  const [assetPickerResetToken, setAssetPickerResetToken] = useState(0);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressState, setProgressState] = useState<"idle" | "running" | "success" | "error">("idle");
  const [jobProgress, setJobProgress] = useState<GenerationJobProgress | null>(null);
  const [activeGeneration, setActiveGeneration] = useState<ActiveGenerationPreview | null>(null);
  const [latestGeneratedAsset, setLatestGeneratedAsset] = useState<AgentAssetRef | null>(null);
  const [lightbox, setLightbox] = useState<AgentLightboxState | null>(null);
  const [consumedResultMessageIds, setConsumedResultMessageIds] = useState<Set<string>>(() => new Set());
  const [pendingDeleteConversation, setPendingDeleteConversation] = useState<AgentConversation | null>(null);
  const [pendingDraftAttachments, setPendingDraftAttachments] = useState<AgentAssetRef[] | null>(null);
  const [pendingRefineContext, setPendingRefineContext] = useState<ResultOptionContext | null>(null);
  const [pendingDesignOptions, setPendingDesignOptions] = useState<AgentDesignOption[]>([]);
  const [designOtherText, setDesignOtherText] = useState("");
  const draftRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    if (!activeConversationId) return;
    void loadConversation(activeConversationId);
  }, [activeConversationId]);

  useEffect(() => {
    resizeDraftTextarea();
  }, [draft]);

  function resizeDraftTextarea() {
    const textarea = draftRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 42), 150)}px`;
  }

  async function bootstrap() {
    try {
      const [conversationItems, memoryItems] = await Promise.all([fetchAgentConversations(), fetchAgentMemories()]);
      setConversations(conversationItems);
      setMemories(memoryItems);
      if (conversationItems[0]) {
        setActiveConversationId(conversationItems[0].id);
        setMode(conversationItems[0].mode);
      }
    } catch (bootError) {
      setError(bootError instanceof Error ? bootError.message : "Agent 初始化失败");
    }
  }

  async function loadConversation(conversationId: string) {
    try {
      const detail = await fetchAgentConversationDetail(conversationId);
      setMessages(detail.messages);
      setActions(detail.actions);
      setMode(detail.conversation.mode);
      const latestGenerated = detail.conversation.state?.latest_generated_asset;
      setLatestGeneratedAsset(latestGenerated && typeof latestGenerated === "object" ? latestGenerated as AgentAssetRef : null);
      setPendingDesignOptions(restorePendingDesignOptions(detail.conversation.state));
      setDesignOtherText("");
      void restoreSubmittedGeneration(detail.conversation.id, detail.actions, detail.messages);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取对话失败");
    }
  }

  function restorePendingDesignOptions(rawState: Record<string, unknown> | null | undefined) {
    return normalizeDesignOptions(rawState?.pending_design_options);
  }

  async function restoreSubmittedGeneration(conversationId: string, actionItems: AgentAction[], messageItems: AgentMessage[]) {
    if (activeGeneration) return;
    const currentConversationId = activeConversationId;
    const resultActionIds = new Set(
      messageItems
        .map((message) => {
          const event = message.event;
          return event && typeof event === "object" && event.type === "generation_result" && typeof event.action_id === "string" ? event.action_id : null;
        })
        .filter((item): item is string => Boolean(item)),
    );
    const pendingAction = actionItems.find((action) => action.status === "submitted" && action.result_job_id && !resultActionIds.has(action.id));
    if (!pendingAction?.result_job_id) return;
    const sourceUrl = getAssetPreviewUrl(pendingAction.source_assets[0]) ?? pendingAction.source_image_urls[0] ?? null;
    setProgressState("running");
    setJobProgress({ percent: 18, label: `${moduleLabels[pendingAction.module_key] ?? pendingAction.title}任务恢复中...` });
    setActiveGeneration({
      actionTitle: pendingAction.title,
      moduleKey: pendingAction.module_key,
      sourceUrl,
      resultUrl: null,
      resultAsset: null,
    });
    try {
      const initialJob = await fetchGenerationJob(pendingAction.result_job_id);
      setJobProgress(buildGenerationJobProgress(initialJob));
      const result =
        initialJob.status === "succeeded" && initialJob.result
          ? (initialJob.result as unknown as GenerationResult | MultiViewSplitResponse)
          : await waitForAgentJobResult<GenerationResult | MultiViewSplitResponse>(pendingAction.result_job_id, "Agent 动作执行失败", {
              onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job)),
            });
      const imageUrl = getActionResultImage(result);
      if (imageUrl) {
        const registeredAsset = await registerAgentGenerationResult(conversationId, {
          action_id: pendingAction.id,
          module_key: pendingAction.module_key,
          image_url: imageUrl,
          name: moduleLabels[pendingAction.module_key] ?? pendingAction.title,
        });
        setLatestGeneratedAsset(registeredAsset);
      }
      setProgressState("success");
      setJobProgress({ percent: 100, label: "已完成" });
      setActiveGeneration(null);
      if (currentConversationId === conversationId) {
        await loadConversation(conversationId);
      }
    } catch (restoreError) {
      setProgressState("error");
      const message = restoreError instanceof Error ? restoreError.message : "恢复生成任务失败";
      setJobProgress({ percent: 100, label: message });
      setError(message);
    }
  }

  function resetConversationView(nextMode = mode) {
    setMode(nextMode);
    setMessages([]);
    setActions([]);
    setDraft("");
    setLatestGeneratedAsset(null);
    setConsumedResultMessageIds(new Set());
    setFiles([]);
    setSelectedAssetItems([]);
    setAssetPickerResetToken((value) => value + 1);
      setMemoryProposal(null);
      setActiveGeneration(null);
      setJobProgress(null);
      setProgressState("idle");
      setPendingDeleteConversation(null);
      setPendingDraftAttachments(null);
      setPendingRefineContext(null);
      setPendingDesignOptions([]);
      setDesignOtherText("");
  }

  function handleNewConversation(nextMode = mode) {
    setActiveConversationId(null);
    resetConversationView(nextMode);
  }

  async function handleStartConversation(nextMode: AgentMode) {
    try {
      const created = await createAgentConversation(nextMode);
      setConversations((current) => [created, ...current]);
      setActiveConversationId(created.id);
      resetConversationView(created.mode);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "新建对话失败");
    }
  }

  async function handleDeleteConversation(conversationId: string) {
    try {
      await deleteAgentConversation(conversationId);
      const nextConversations = conversations.filter((item) => item.id !== conversationId);
      setConversations(nextConversations);
      if (conversationId === activeConversationId) {
        const nextActive = nextConversations[0] ?? null;
        if (nextActive) {
          setActiveConversationId(nextActive.id);
          setMode(nextActive.mode);
        } else {
          setActiveConversationId(null);
          resetConversationView(mode);
        }
      }
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除对话失败");
    }
  }

  async function buildAttachments(content: string): Promise<AgentAssetRef[]> {
    const selectedRefs: AgentAssetRef[] = selectedAssetItems.map((asset) => ({
      asset_id: asset.persistedAssetId ?? null,
      name: asset.name,
      storage_url: asset.storageUrl ?? null,
      preview_url: asset.previewUrl ?? asset.fileUrl ?? null,
    }));
    const uploadedRefs: AgentAssetRef[] = [];
    for (const file of files) {
      const uploaded = await uploadInputAsset(file, "ai_agent", "agent_upload");
      uploadedRefs.push({
        asset_id: uploaded.id,
        name: uploaded.name,
        storage_url: uploaded.storage_url,
        preview_url: uploaded.preview_url,
      });
    }
    const explicitRefs = [...selectedRefs, ...uploadedRefs];
    if (explicitRefs.length > 0) {
      return explicitRefs;
    }
    if (latestGeneratedAsset && isFollowUpStep(content)) {
      return [latestGeneratedAsset];
    }
    return [];
  }

  async function handleSend(contentOverride?: string, attachmentOverride?: AgentAssetRef[]) {
    if (loading || !activeConversationId) return;
    if (pendingDesignOptions.length > 0 && contentOverride === undefined) return;
    const content = (contentOverride ?? draft).trim();
    if (!content && files.length === 0 && selectedAssetItems.length === 0) return;

    setLoading(true);
    setError(null);
    setMemoryProposal(null);
    setPendingDesignOptions([]);
    setDesignOtherText("");
    const tempUserMessage: AgentMessage = {
      id: `local-user-${Date.now()}`,
      conversation_id: activeConversationId,
      role: "user",
      content: content || "已选择参考图片。",
      attachments: [],
      created_at: new Date().toISOString(),
    };
    const assistantId = `local-assistant-${Date.now()}`;
    const tempAssistantMessage: AgentMessage = {
      id: assistantId,
      conversation_id: activeConversationId,
      role: "assistant",
      content: "",
      attachments: [],
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, tempUserMessage, tempAssistantMessage]);
    if (contentOverride === undefined) {
      setDraft("");
    }

    try {
      const shouldUsePendingDraftAttachments = pendingDraftAttachments && files.length === 0 && selectedAssetItems.length === 0;
      const attachments = attachmentOverride ?? (shouldUsePendingDraftAttachments ? pendingDraftAttachments : await buildAttachments(content));
      await sendAgentMessageStream(
        activeConversationId,
        { content, mode, attachments },
        {
          onDelta: (text) => {
            setMessages((current) =>
              current.map((item) => (item.id === assistantId ? { ...item, content: `${item.content}${text}` } : item)),
            );
          },
          onAction: (action) => {
            setPendingDesignOptions([]);
            setDesignOtherText("");
            setActions((current) => [action, ...current.filter((item) => item.id !== action.id)]);
            void handleConfirmAction(action);
          },
          onDesignOptions: (options) => setPendingDesignOptions(options),
          onMemoryProposal: setMemoryProposal,
          onError: (message) => setError(message),
        },
      );
      setFiles([]);
      setSelectedAssetItems([]);
      setPendingDraftAttachments(null);
      setPendingRefineContext(null);
      setAssetPickerResetToken((value) => value + 1);
      const [conversationItems, memoryItems] = await Promise.all([fetchAgentConversations(), fetchAgentMemories()]);
      setConversations(conversationItems);
      setMemories(memoryItems);
      await loadConversation(activeConversationId);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "Agent 回复失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleConfirmAction(action: AgentAction) {
    setPendingDesignOptions([]);
    setDesignOtherText("");
    const sourceUrl = getAssetPreviewUrl(action.source_assets[0]) ?? action.source_image_urls[0] ?? null;
    setProgressState("running");
    setJobProgress({ percent: 18, label: `${moduleLabels[action.module_key] ?? action.title}任务排队中...` });
    setActiveGeneration({
      actionTitle: action.title,
      moduleKey: action.module_key,
      sourceUrl,
      resultUrl: null,
      resultAsset: null,
    });
    setError(null);
    try {
      const accepted = await confirmAgentAction(action.id, {
        prompt: action.prompt ?? null,
        params: action.params,
        source_assets: action.source_assets,
        source_image_urls: action.source_image_urls,
      });
      setActions((current) => current.map((item) => (item.id === action.id ? accepted.action : item)));
      const result = await waitForAgentJobResult<GenerationResult | MultiViewSplitResponse>(accepted.job_id, "Agent 动作执行失败", {
        onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job)),
      });
      const imageUrl = getActionResultImage(result);
      if (imageUrl) {
        const registeredAsset = await registerAgentGenerationResult(action.conversation_id, {
          action_id: action.id,
          module_key: action.module_key,
          image_url: imageUrl,
          name: moduleLabels[action.module_key] ?? action.title,
        });
        setLatestGeneratedAsset(registeredAsset);
        setActiveGeneration({
          actionTitle: action.title,
          moduleKey: action.module_key,
          sourceUrl,
          resultUrl: imageUrl,
          resultAsset: registeredAsset,
        });
      }
      setProgressState("success");
      setJobProgress({ percent: 100, label: "已完成" });
      await loadConversation(action.conversation_id);
      setActiveGeneration(null);
    } catch (confirmError) {
      setProgressState("error");
      const message = confirmError instanceof Error ? confirmError.message : "Agent 动作执行失败";
      setJobProgress({ percent: 100, label: message });
      setError(message);
    }
  }

  async function handleSaveMemory() {
    if (!memoryProposal) return;
    try {
      const memory = await createAgentMemory({
        content: memoryProposal.content,
        memory_type: memoryProposal.memory_type,
        source_conversation_id: activeConversationId,
      });
      setMemories((current) => [memory, ...current]);
      setMemoryProposal(null);
    } catch (memoryError) {
      setError(memoryError instanceof Error ? memoryError.message : "保存记忆失败");
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (pendingDesignOptions.length > 0) {
      event.preventDefault();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  function handleDraftChange(event: ChangeEvent<HTMLTextAreaElement>) {
    setDraft(event.target.value);
    if (!event.target.value.trim()) {
      setPendingDraftAttachments(null);
    }
  }

  function handleResultOption(option: AgentFlowOption, context: ResultOptionContext = {}) {
    if (context.messageId) {
      setConsumedResultMessageIds((current) => {
        const next = new Set(current);
        next.add(context.messageId as string);
        return next;
      });
    }
    if (option.behavior === "draft_refine_prompt") {
      setPendingRefineContext(context);
      return;
    }
    if (option.behavior === "regenerate_from_sources") {
      void handleSend(option.prompt, context.sourceAssets?.length ? context.sourceAssets : context.resultAsset ? [context.resultAsset] : undefined);
      return;
    }
    if (option.behavior === "draft_design_revision") {
      setDraft(option.prompt);
      setPendingDraftAttachments(context.sourceAssets?.length ? context.sourceAssets : null);
      return;
    }
    if (option.behavior === "end") {
      void handleSend(option.prompt);
      return;
    }
    if (option.prompt.includes("精修")) {
      const refs = getRefineAttachments(context);
      void handleSend(option.prompt, refs.length ? refs : undefined);
      return;
    }
    if (option.prompt.includes("多视图") || option.prompt.includes("灰度")) {
      void handleSend(option.prompt, getPrimaryResultAttachment(context));
      return;
    }
    if (context.resultAsset) {
      void handleSend(option.prompt, [context.resultAsset]);
      return;
    }
    void handleSend(option.prompt);
  }

  function handleRefineChoice(option: AgentFlowOption) {
    const refs = getRefineAttachments(pendingRefineContext ?? {});
    setPendingRefineContext(null);
    if (option.prompt === "直接精修") {
      void handleSend(option.prompt, refs.length ? refs : undefined);
      return;
    }
    setDraft(option.prompt);
    setPendingDraftAttachments(refs.length ? refs : null);
  }

  function handleDesignOption(option: AgentDesignOption) {
    void handleSend(option.value);
  }

  function handleDesignOtherSubmit() {
    const value = designOtherText.trim();
    if (!value) return;
    void handleSend(value);
  }

  const activeOptions = mode === "design" ? [] : latestGeneratedAsset ? [] : workflowOptions;
  const shouldShowInlineOptions = activeOptions.length > 0 && !activeGeneration;
  const isDesignChoiceLocked = pendingDesignOptions.length > 0 && !loading;

  return (
    <div className="agent-chat-page">
      <FloatingToast message={error} />
      <section className={activeConversationId ? "agent-chat-shell agent-chat-shell-plain" : "agent-chat-shell agent-chat-shell-start"}>
        {!activeConversationId ? (
          <div className="agent-start-screen">
            <div className="agent-start-copy">
              <h3>选择 Agent 模式</h3>
              <p>新对话开始前先确定用途，后续对话中不再切换模式。</p>
            </div>
            <div className="agent-start-options">
              <button type="button" onClick={() => handleStartConversation("workflow")} disabled={loading}>
                <strong>流程助手</strong>
                <span>线稿转写实、精修、多视图、灰度图工作流</span>
              </button>
              <button type="button" onClick={() => handleStartConversation("design")} disabled={loading}>
                <strong>设计出图</strong>
                <span>引导设计理念、整理专业提示词并生成首版图</span>
              </button>
            </div>
          </div>
        ) : (
        <div className="agent-chat-body">
          <div className="agent-chat-thread" aria-live="polite">
            {messages.length === 0 ? (
              <article className="agent-message assistant">
                <div className="agent-message-content">
                <AgentMarkdown content={mode === "design" ? "请直接描述你的设计理念，或上传裸石/玉石图片。我会把信息整理成 brief：品类、主石、材质、风格、工艺和场景；信息不足时只追问关键项，足够后可直接生成首版设计图。" : "您可以直接发送一张线稿图，发送后我会自动进入「线稿转写实」流程，并直接提交写实图生成任务。生成完成后，可以在此基础上进行后续的流程。或者如果您有其它需求，可随时跟我沟通～"} />
                </div>
              </article>
            ) : null}
            {messages.map((message) => {
              const generationEvent = getGenerationEvent(message);
              const resultAsset = generationEvent?.result_asset ?? message.attachments?.[0] ?? null;
              const resultUrl = generationEvent ? getAssetPreviewUrl(resultAsset) : null;
              const sourceUrl = generationEvent ? getAssetPreviewUrl(generationEvent.source_assets?.[0]) : null;
              const isLatestResult = Boolean(resultUrl && latestGeneratedAsset && resultUrl === getAssetPreviewUrl(latestGeneratedAsset));
              const generationModuleKey = generationEvent?.module_key;
              const showResultOptions = isLatestResult && !activeGeneration && !loading && !consumedResultMessageIds.has(message.id);
              return (
                <div className="agent-message-group" key={message.id}>
                <article className={message.role === "assistant" ? "agent-message assistant" : "agent-message user"}>
                  {generationEvent && resultUrl ? (
                    <div className="agent-message-content agent-generation-card agent-generation-history-card">
                      <div className="agent-generation-head">
                        <div>
                          <h4>{generationEvent.title ?? moduleLabels[generationModuleKey ?? ""] ?? "生成结果"}</h4>
                          <p>{getGenerationResultCopy(generationModuleKey, message.content)}</p>
                        </div>
                        <span>已完成</span>
                      </div>
                      <div className="agent-generation-grid agent-generation-grid-single">
                        <div className="agent-generation-preview-frame">
                          <button
                            className="agent-generation-tile has-image agent-generation-clickable"
                            type="button"
                            onClick={() =>
                              setLightbox({
                                title: generationEvent.title ?? "生成结果预览",
                                sourceUrl,
                                resultUrl,
                              })
                            }
                          >
                            <img src={resultUrl} alt={generationEvent.title ?? "生成结果"} />
                          </button>
                          <a
                            className="agent-generation-download-button"
                            href={resultUrl}
                            download
                            aria-label="下载生成图"
                            title="下载生成图"
                          >
                            下载
                          </a>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="agent-message-content">
                      <AgentMarkdown content={message.content} />
                      {message.attachments?.length ? (
                        <div className="agent-attachment-list">
                          {message.attachments.map((item, index) => (
                            <span key={`${item.name ?? "asset"}-${index}`}>{item.name ?? "图片"}</span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )}
                </article>
                {generationEvent && resultUrl && showResultOptions ? (
                  <article className="agent-message assistant agent-options-message">
                    <div className="agent-result-step-grid">
                      {getResultStepOptions(generationModuleKey).map((option) => (
                        <button
                          className="agent-option-card"
                          type="button"
                          key={option.title}
                          data-helper={option.helper}
                          onClick={() =>
                            handleResultOption(option, {
                              messageId: message.id,
                              resultAsset,
                              sourceAssets: generationEvent.source_assets,
                            })
                          }
                          disabled={loading}
                        >
                          <strong>{option.title}</strong>
                          <span>{option.helper}</span>
                        </button>
                      ))}
                    </div>
                  </article>
                ) : null}
                </div>
              );
            })}

            {pendingRefineContext ? (
              <article className="agent-message assistant agent-options-message agent-refine-choice-message">
                <div className="agent-refine-choice-card">
                  <div className="agent-refine-choice-head">
                    <strong>产品精修</strong>
                    <span>选择一种精修方式</span>
                  </div>
                  <div className="agent-result-step-grid agent-refine-choice-grid">
                    {refineChoiceOptions.map((option) => (
                      <button
                        className="agent-option-card"
                        type="button"
                        key={option.title}
                        data-helper={option.helper}
                        onClick={() => handleRefineChoice(option)}
                        disabled={loading}
                      >
                        <strong>{option.title}</strong>
                        <span>{option.helper}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </article>
            ) : null}

            {shouldShowInlineOptions ? (
              <article className="agent-message assistant">
                <div className="agent-message-content agent-inline-options-card">
                  <div className="agent-inline-option-grid">
                    {activeOptions.map((option) => (
                      <button className="agent-option-card" type="button" key={option.title} onClick={() => handleSend(option.prompt)} disabled={loading}>
                        <i aria-hidden="true" />
                        <strong>{option.title}</strong>
                        <span>{option.helper}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </article>
            ) : null}

            {isDesignChoiceLocked ? (
              <article className="agent-message assistant agent-options-message agent-design-choice-message">
                <div className="agent-design-choice-card">
                  <div className="agent-design-choice-head">
                    <strong>请选择一个方向</strong>
                    <span>选择后我会继续整理 brief；也可以在“其他”中补充自己的想法。</span>
                  </div>
                  <div className="agent-design-choice-grid">
                    {pendingDesignOptions.map((option) => (
                      <button
                        className="agent-design-choice-option"
                        type="button"
                        key={`${option.label}-${option.value}`}
                        onClick={() => handleDesignOption(option)}
                      >
                        <strong>{option.label}</strong>
                        {option.description ? <span>{option.description}</span> : null}
                      </button>
                    ))}
                    <div className="agent-design-choice-other">
                      <strong>其他</strong>
                      <textarea
                        value={designOtherText}
                        onChange={(event) => setDesignOtherText(event.target.value)}
                        placeholder="输入其他需求或补充说明"
                        rows={2}
                      />
                      <button type="button" onClick={handleDesignOtherSubmit} disabled={!designOtherText.trim()}>
                        提交
                      </button>
                    </div>
                  </div>
                </div>
              </article>
            ) : null}

            {memoryProposal ? (
              <article className="agent-message assistant">
                <div className="agent-message-content">
                  <p>是否保存为长期偏好：{memoryProposal.content}</p>
                  <div className="inline-action-row">
                    <button className="primary-button" type="button" onClick={handleSaveMemory}>保存</button>
                    <button className="secondary-button" type="button" onClick={() => setMemoryProposal(null)}>忽略</button>
                  </div>
                </div>
              </article>
            ) : null}

            {activeGeneration ? (
              <article className="agent-message assistant agent-generation-message">
                <div className="agent-message-content agent-generation-card">
                  <div className="agent-generation-head">
                    <div>
                      <h4>{moduleLabels[activeGeneration.moduleKey] ?? activeGeneration.actionTitle}</h4>
                      <p>{activeGeneration.resultUrl ? "生成已完成，可以继续选择下一步。" : "我正在为你生成图片，请稍候。"}</p>
                    </div>
                    {progressState === "running" ? null : <span>{progressState === "success" ? "已完成" : "失败"}</span>}
                  </div>
                  <div className="agent-generation-grid agent-generation-grid-single">
                    <div className="agent-generation-preview-frame">
                      <button
                        className={activeGeneration.resultUrl ? "agent-generation-tile has-image agent-generation-clickable" : "agent-generation-tile"}
                        type="button"
                        disabled={!activeGeneration.resultUrl}
                        onClick={() =>
                          activeGeneration.resultUrl
                            ? setLightbox({
                                title: moduleLabels[activeGeneration.moduleKey] ?? activeGeneration.actionTitle,
                                sourceUrl: activeGeneration.sourceUrl,
                                resultUrl: activeGeneration.resultUrl,
                              })
                            : undefined
                        }
                      >
                        {activeGeneration.resultUrl ? (
                          <img src={activeGeneration.resultUrl} alt="生成结果" />
                        ) : (
                          <div className="agent-generation-placeholder">
                            <span />
                            <strong>{Math.round(jobProgress?.percent ?? 18)}%</strong>
                          </div>
                        )}
                      </button>
                      {activeGeneration.resultUrl ? (
                        <a
                          className="agent-generation-download-button"
                          href={activeGeneration.resultUrl}
                          download
                          aria-label="下载生成图"
                          title="下载生成图"
                        >
                          下载
                        </a>
                      ) : null}
                    </div>
                  </div>
                </div>
              </article>
            ) : null}
            {activeGeneration?.resultUrl ? (
              <article className="agent-message assistant agent-options-message">
                <div className="agent-result-step-grid">
                  {getResultStepOptions(activeGeneration.moduleKey).map((option) => (
                    <button
                      className="agent-option-card"
                      type="button"
                      key={option.title}
                      data-helper={option.helper}
                      onClick={() => handleResultOption(option, { resultAsset: activeGeneration.resultAsset })}
                      disabled={loading}
                    >
                      <strong>{option.title}</strong>
                      <span>{option.helper}</span>
                    </button>
                  ))}
                </div>
              </article>
            ) : null}
          </div>

          <div className="agent-composer-wrap">
            <div className={isDesignChoiceLocked ? "agent-composer locked" : "agent-composer"}>
              <textarea
                ref={draftRef}
                value={draft}
                onChange={handleDraftChange}
                onKeyDown={handleInputKeyDown}
                placeholder={isDesignChoiceLocked ? "请先选择上方选项，或在“其他”中补充" : mode === "design" ? "描述设计理念、材质、风格，或上传裸石图片后发送" : "输入其他需求，或先用下方 + 上传/选择图片"}
                rows={1}
                disabled={isDesignChoiceLocked}
              />
              <div className="agent-composer-toolbar">
                <div className="agent-composer-tools">
                  <AssetSourcePicker
                    key={`${activeConversationId ?? "new"}-${assetPickerResetToken}`}
                    title="选择线稿或参考图"
                    assetItems={assetItems}
                    helper="选择资产时可预览；完成选择后会在输入框下方显示已选图片。"
                    uploadLabel="上传线稿或参考图"
                    compactTrigger
                    onUploadFilesChange={setFiles}
                    onSelectedAssetsChange={setSelectedAssetItems}
                  />
                </div>
                <div className="agent-send-row">
                  <span>Enter 发送 / Shift+Enter 换行</span>
                  <button className="agent-send-button" type="button" onClick={() => handleSend()} disabled={loading || isDesignChoiceLocked || (!draft.trim() && files.length === 0 && selectedAssetItems.length === 0)}>
                    <span aria-hidden="true">›</span>
                    发送
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
        )}
      </section>

      {lightbox ? (
        <ResultPreviewModal
          title={lightbox.title}
          sourceUrl={lightbox.sourceUrl}
          sourceLabel="原始图"
          resultUrl={lightbox.resultUrl}
          resultLabel="生成图"
          onClose={() => setLightbox(null)}
        />
      ) : null}

      <aside className={historyCollapsed ? "agent-history-column collapsed" : "agent-history-column"}>
        {!historyCollapsed ? (
          <button className="page-history-toggle-button agent-new-chat-button" type="button" onClick={() => handleNewConversation(mode)}>
            新对话
          </button>
        ) : null}
        <div className={historyCollapsed ? "page-history-sidebar agent-history-sidebar collapsed" : "page-history-sidebar agent-history-sidebar"}>
          <div className="page-history-sidebar-header">
            {!historyCollapsed ? (
              <>
                <div className="stack-list compact-stack"><h4>对话与记忆</h4></div>
                <button className="page-history-toggle-button agent-history-icon-button" type="button" onClick={() => setHistoryCollapsed(true)} aria-label="收起对话与记忆">
                  <span aria-hidden="true">‹</span>
                </button>
              </>
            ) : (
              <button className="page-history-toggle-button collapsed agent-history-icon-button" type="button" onClick={() => setHistoryCollapsed(false)} aria-label="展开对话与记忆">
                <span aria-hidden="true">›</span>
              </button>
            )}
          </div>
          <div className="page-history-sidebar-body">
            <div className="page-history-sidebar-list">
              {conversations.map((conversation) => (
                <article className={conversation.id === activeConversationId ? "page-history-card agent-history-card active" : "page-history-card agent-history-card"} key={conversation.id}>
                  <button className="page-history-card-button" type="button" onClick={() => setActiveConversationId(conversation.id)}>
                    {!historyCollapsed ? (
                      <>
                        <div className="history-inline-head history-entry-head"><h4>{getConversationDisplayTitle(conversation)}</h4></div>
                        <div className="history-meta-row"><span className="history-time-pill">{new Date(conversation.updated_at).toLocaleString()}</span></div>
                      </>
                    ) : <span className="agent-history-mini-dot" aria-hidden="true" />}
                  </button>
                  {!historyCollapsed ? (
                    <button
                      className="agent-history-delete-button"
                      type="button"
                      aria-label={`删除 ${getConversationDisplayTitle(conversation)}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        setPendingDeleteConversation(conversation);
                      }}
                    >
                      ×
                    </button>
                  ) : null}
                </article>
              ))}
              {!historyCollapsed && memories.map((memory) => (
                <article className="page-history-card agent-history-card" key={memory.id}>
                  <div className="page-history-card-button">
                    <div className="history-inline-head history-entry-head"><h4>长期偏好</h4></div>
                    <p className="muted">{memory.content}</p>
                    <div className="inline-action-row">
                      <button className="secondary-button" type="button" onClick={() => updateAgentMemory(memory.id, { is_enabled: !memory.is_enabled }).then((next) => setMemories((current) => current.map((item) => item.id === next.id ? next : item)))}>
                        {memory.is_enabled ? "停用" : "启用"}
                      </button>
                      <button className="secondary-button" type="button" onClick={() => deleteAgentMemory(memory.id).then(() => setMemories((current) => current.filter((item) => item.id !== memory.id)))}>
                        删除
                      </button>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>
      </aside>

      {pendingDeleteConversation ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setPendingDeleteConversation(null)}>
          <div className="admin-modal-card agent-delete-modal" role="dialog" aria-modal="true" aria-label="删除对话确认" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <h3>删除对话</h3>
              <button className="template-close-button" type="button" onClick={() => setPendingDeleteConversation(null)} aria-label="关闭删除确认">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="muted">确定删除「{getConversationDisplayTitle(pendingDeleteConversation)}」吗？该对话内的消息和 Agent 动作记录会一起删除。</p>
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button" type="button" onClick={() => setPendingDeleteConversation(null)}>
                取消
              </button>
              <button
                className="primary-button danger-button"
                type="button"
                onClick={() => {
                  const conversationId = pendingDeleteConversation.id;
                  setPendingDeleteConversation(null);
                  void handleDeleteConversation(conversationId);
                }}
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
