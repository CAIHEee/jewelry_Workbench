import { useEffect, useMemo, useState } from "react";

import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { AutoResizeTextarea } from "../components/AutoResizeTextarea";
import { FloatingToast } from "../components/FloatingToast";
import { GeneratingImagePlaceholder } from "../components/GeneratingImagePlaceholder";
import { PageGenerationHistory } from "../components/PageGenerationHistory";
import { PreviewTimer } from "../components/PreviewTimer";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import { useModelCatalog } from "../hooks/useModelCatalog";
import { submitMultiViewGeneration } from "../services/api";
import type { GenerationJobProgress, GenerationResult } from "../types/fusion";
import type { AssetItem } from "../types/mockData";
import type { WorkspaceRun } from "../types/workspace";
import { buildGenerationJobProgress } from "../utils/jobProgress";
import type { ModuleHistoryEntry } from "../utils/history";

interface MultiViewPageProps {
  assetItems: AssetItem[];
  onRecordRun: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  onRefreshHistory?: () => Promise<void> | void;
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

const defaultMultiViewPromptLabel = "默认多视图规则";
const progressPhases = [
  { at: 18, label: "多视图任务排队中..." },
  { at: 34, label: "反推模型分析原图中..." },
  { at: 46, label: "生成多视图提示词..." },
  { at: 74, label: "生成多角度视图中..." },
  { at: 95, label: "拼合四宫格结果..." },
];
const preferredMultiViewModelId = "gpt-image-2-all-apiyi";
const allowedMultiViewModelIds = new Set([
  "gpt-image-2-all-apiyi",
  "gemini-3-pro-image-preview-apiyi",
  "gpt-image-2-closeai",
]);
const generationCountOptions = [1, 2, 4] as const;
type GenerationCount = (typeof generationCountOptions)[number];
const jobProgressLabels = {
  queued: "多视图任务排队中...",
  qwenPrompt: "反推模型分析原图并生成提示词中...",
  imageGeneration: "提示词已生成，正在生成多视图...",
  running: "生成多角度视图中...",
  uploading: "正在拼合并保存多视图结果...",
  succeeded: "已完成",
  failed: "多视图生成失败",
};

export function MultiViewPage({ assetItems, onRecordRun: _onRecordRun, onRefreshHistory, pageRuns, onDeleteHistory }: MultiViewPageProps) {
  const { models, error: modelError, defaultModelId } = useModelCatalog((model) => allowedMultiViewModelIds.has(model.id));
  const multiViewDefaultModelId = useMemo(
    () => models.find((item) => item.id === preferredMultiViewModelId)?.id ?? defaultModelId,
    [defaultModelId, models],
  );
  const [model, setModel] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<AssetItem[]>([]);
  const [additionalPrompt, setAdditionalPrompt] = useState("");
  const [results, setResults] = useState<GenerationResult[]>([]);
  const [generationCount, setGenerationCount] = useState<GenerationCount>(1);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressState, setProgressState] = useState<"idle" | "running" | "success" | "error">("idle");
  const [jobProgress, setJobProgress] = useState<GenerationJobProgress | null>(null);
  const [currentGenerationStartedAt, setCurrentGenerationStartedAt] = useState<string | null>(null);

  useEffect(() => {
    if (!models.length) return;
    if (!model || !models.some((item) => item.id === model)) {
      setModel(multiViewDefaultModelId);
    }
  }, [model, models, multiViewDefaultModelId]);

  const selectedModel = useMemo(() => models.find((item) => item.id === model) ?? models[0] ?? null, [model, models]);
  const uploadedPreviewUrl = useMemo(() => (files[0] ? URL.createObjectURL(files[0]) : null), [files]);
  const selectedHistory = useMemo(() => pageRuns.find((item) => item.id === selectedHistoryId) ?? null, [pageRuns, selectedHistoryId]);
  const latestResult = results[0] ?? null;
  const previewResultUrl = loading ? null : selectedHistory?.imageUrl ?? latestResult?.image_url ?? null;
  const previewSourceUrl = selectedHistory?.sourceImageUrl ?? latestResult?.source_image_url ?? (uploadedPreviewUrl ?? selectedAssets[0]?.previewUrl ?? selectedAssets[0]?.storageUrl ?? null);
  const selectedAssetRefs = useMemo(
    () =>
      selectedAssets
        .map((asset) => ({
          url: asset.fileUrl ?? asset.previewUrl ?? asset.storageUrl ?? null,
          name: asset.name,
        }))
        .filter((item): item is { url: string; name: string } => Boolean(item.url)),
    [selectedAssets],
  );
  const hasInputSource = files.length > 0 || selectedAssetRefs.length > 0;

  useEffect(() => {
    return () => {
      if (uploadedPreviewUrl) {
        URL.revokeObjectURL(uploadedPreviewUrl);
      }
    };
  }, [uploadedPreviewUrl]);

  async function handleGenerate() {
    // 立即设置 loading，防止竞态条件导致重复提交
    if (loading) {
      return;
    }
    setError(null);

    if (!selectedModel) {
      setError("当前没有可用的多视图模型。");
      return;
    }

    if (!hasInputSource) {
      setError("请先选择一张原图。");
      return;
    }

    if (files.length > 1) {
      setError("多视图模型只支持上传 1 张原图。");
      return;
    }

    setLoading(true);
    setResults([]);
    setSelectedHistoryId(null);
    const startedAt = new Date().toISOString();
    setCurrentGenerationStartedAt(startedAt);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "多视图任务排队中..." });

    try {
      const selectedAssetUrls = selectedAssetRefs.map((item) => item.url);
      const selectedAssetNames = selectedAssetRefs.map((item) => item.name);
      if (files.length === 0 && selectedAssetUrls.length === 0) {
        throw new Error("未获取到可用原图");
      }

      const displayPrompt = additionalPrompt.trim() || defaultMultiViewPromptLabel;

      const response = await submitMultiViewGeneration({
        files: files.slice(0, 1),
        sourceImageUrls: files.length > 0 ? undefined : selectedAssetUrls.slice(0, 1),
        sourceImageNames: files.length > 0 ? undefined : selectedAssetNames.slice(0, 1),
        model: selectedModel.id,
        prompt: displayPrompt,
        feature: "multi_view",
        batchSize: generationCount,
      }, {
        onJobUpdate: (job) => {
          const nextProgress = buildGenerationJobProgress(job, jobProgressLabels);
          setJobProgress({
            ...nextProgress,
            label: generationCount > 1 ? `${nextProgress.label}（批量 ${generationCount} 张）` : nextProgress.label,
          });
        },
      });
      const responseItems = response.results?.length ? response.results : [response];
      const validResponses = responseItems.filter((item) => Boolean(item.image_url));
      if (validResponses.length === 0) {
        throw new Error("生成完成，但没有返回多视图结果图片，请稍后重试。");
      }

      setResults([...validResponses].reverse());
      setJobProgress({ percent: 100, label: generationCount > 1 ? `已完成 ${validResponses.length}/${generationCount} 张` : "已完成" });
      setProgressState("success");
      await onRefreshHistory?.();
      window.setTimeout(() => {
        void onRefreshHistory?.();
      }, 800);
      if (validResponses.length < generationCount) {
        setError(`已完成 ${validResponses.length}/${generationCount} 张，${generationCount - validResponses.length} 张生成失败或超时。`);
      }
    } catch (submitError) {
      setLoading(false);
      setProgressState("error");
      setJobProgress({
        percent: 100,
        label: submitError instanceof Error ? submitError.message : "多视图生成失败",
      });
      setError(submitError instanceof Error ? submitError.message : "多视图生成失败");
      return;
    }

    setLoading(false);
  }

  return (
    <div className="page-stack compact-page split-page multi-view-page">
      <FloatingToast message={error} />
      <section className="panel compact-panel">
        <div className="dashboard-grid result-heavy single-result-layout">
          <div className="form-card parameter-scroll-panel compact-parameter-panel">
            <label className="input-group compact-input-group">
              <span>模型</span>
              <select value={model} onChange={(event) => setModel(event.target.value)} disabled={models.length === 0}>
                {models.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
              {modelError ? <small>{modelError}</small> : null}
            </label>

            <AssetSourcePicker
              title="选择多视图原图"
              assetItems={assetItems}
              allowMultiple={false}
              uploadLabel="上传原图"
              onUploadFilesChange={setFiles}
              onSelectedAssetsChange={setSelectedAssets}
            />

            <label className="input-group compact-input-group prompt-input-group">
              <div className="prompt-input-header compact-prompt-header">
                <span>补充提示词</span>
              </div>
              <AutoResizeTextarea
                className="prompt-textarea"
                rows={3}
                value={additionalPrompt}
                onChange={(e) => setAdditionalPrompt(e.target.value)}
                placeholder="输入额外的生成要求（可选）..."
              />
            </label>

            <div className="input-group compact-input-group count-input-row">
              <span>生成数量</span>
              <div className="count-segmented" role="radiogroup" aria-label="生成数量">
                {generationCountOptions.map((count) => (
                  <button
                    className={generationCount === count ? "count-option active" : "count-option"}
                    type="button"
                    key={count}
                    role="radio"
                    aria-checked={generationCount === count}
                    onClick={() => setGenerationCount(count)}
                    disabled={loading}
                  >
                    {count}
                  </button>
                ))}
              </div>
            </div>

            <button className="primary-button align-start" type="button" onClick={handleGenerate} disabled={loading || !selectedModel || !hasInputSource}>
              {loading ? "生成中..." : `生成${generationCount > 1 ? ` ${generationCount} 张` : ""}多视图`}
            </button>
          </div>

          <div className="preview-history-layout multi-view-preview-layout">
            <div className="stack-list preview-history-main multi-view-preview-main">
              <details className="drawer-panel" open>
                <summary className="drawer-summary compact-drawer-summary">
                  <div className="preview-summary-row">
                    <h4>结果预览</h4>
                    <PreviewTimer startedAt={currentGenerationStartedAt} running={loading} />
                  </div>
                  <span className="drawer-hint">展开 / 收起</span>
                </summary>
                <div className="drawer-content">
                  <div className="result-preview-pane result-preview-pane-single">
                    <span>多视图结果</span>
                    <div
                      className={previewResultUrl ? "generated-result-card compare multi-view-result-card image-edit-result-card interactive-result-card" : "generated-result-card compare multi-view-result-card image-edit-result-card"}
                      role={previewResultUrl ? "button" : undefined}
                      tabIndex={previewResultUrl ? 0 : undefined}
                      onClick={previewResultUrl ? () => setPreviewOpen(true) : undefined}
                    >
                      {loading ? (
                        <GeneratingImagePlaceholder percent={jobProgress?.percent} />
                      ) : previewResultUrl ? (
                        <img className="generated-image image-fit-contain interactive-preview-image" src={previewResultUrl} alt="多视图结果" />
                      ) : (
                        <div className="multi-view-single-card">四宫格结果图</div>
                      )}
                    </div>
                  </div>
                </div>
              </details>
            </div>

            <PageGenerationHistory
              title="多视图历史"
              items={pageRuns}
              activeId={selectedHistoryId}
              onPreview={(item) => setSelectedHistoryId(item.id)}
              onDeleteHistory={onDeleteHistory}
            />
          </div>
        </div>
      </section>

      {previewOpen ? (
        <ResultPreviewModal
          title="多视图结果预览"
          sourceUrl={previewSourceUrl}
          sourceLabel="原始图"
          resultUrl={previewResultUrl}
          resultLabel="多视图结果"
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </div>
  );
}
