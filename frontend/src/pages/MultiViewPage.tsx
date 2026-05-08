import { useEffect, useMemo, useState } from "react";

import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { AutoResizeTextarea } from "../components/AutoResizeTextarea";
import { FloatingToast } from "../components/FloatingToast";
import { GenerationProgress } from "../components/GenerationProgress";
import { PageGenerationHistory } from "../components/PageGenerationHistory";
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
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

const defaultMultiViewPromptLabel = "默认多视图规则";
const progressPhases = [
  { at: 18, label: "分析主视图结构..." },
  { at: 40, label: "提交多视图请求..." },
  { at: 74, label: "生成多角度视图中..." },
  { at: 95, label: "拼合四宫格结果..." },
];
const preferredMultiViewModelId = "gpt-image-2-all-apiyi";
const allowedMultiViewModelIds = new Set(["gpt-image-2-all-apiyi", "multi-view-few-shot-apiyi"]);
const jobProgressLabels = {
  queued: "多视图任务排队中...",
  running: "生成多角度视图中...",
  uploading: "正在拼合并保存多视图结果...",
  succeeded: "已完成",
  failed: "多视图生成失败",
};

export function MultiViewPage({ assetItems, onRecordRun, pageRuns, onDeleteHistory }: MultiViewPageProps) {
  const { models, error: modelError, defaultModelId } = useModelCatalog((model) => allowedMultiViewModelIds.has(model.id));
  const multiViewDefaultModelId = useMemo(
    () => models.find((item) => item.id === preferredMultiViewModelId)?.id ?? defaultModelId,
    [defaultModelId, models],
  );
  const [model, setModel] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<AssetItem[]>([]);
  const [additionalPrompt, setAdditionalPrompt] = useState("");
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressState, setProgressState] = useState<"idle" | "running" | "success" | "error">("idle");
  const [jobProgress, setJobProgress] = useState<GenerationJobProgress | null>(null);

  useEffect(() => {
    if (!models.length) return;
    if (!model || !models.some((item) => item.id === model)) {
      setModel(multiViewDefaultModelId);
    }
  }, [model, models, multiViewDefaultModelId]);

  useEffect(() => {
    if (result || selectedHistoryId || pageRuns.length === 0) {
      return;
    }
    setSelectedHistoryId(pageRuns[0].id);
  }, [pageRuns, result, selectedHistoryId]);

  const selectedModel = useMemo(() => models.find((item) => item.id === model) ?? models[0] ?? null, [model, models]);
  const uploadedPreviewUrl = useMemo(() => (files[0] ? URL.createObjectURL(files[0]) : null), [files]);
  const selectedHistory = useMemo(() => pageRuns.find((item) => item.id === selectedHistoryId) ?? null, [pageRuns, selectedHistoryId]);
  const activeHistory = selectedHistory ?? (!result ? pageRuns[0] ?? null : null);
  const previewResultUrl = activeHistory?.imageUrl ?? result?.image_url ?? null;
  const previewSourceUrl = activeHistory?.sourceImageUrl ?? (uploadedPreviewUrl ?? selectedAssets[0]?.previewUrl ?? selectedAssets[0]?.storageUrl ?? null);

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
    setLoading(true);
    setError(null);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "多视图任务排队中..." });

    if (!selectedModel) {
      setError("当前没有可用的多视图模型。");
      setLoading(false);
      return;
    }

    if (files.length === 0 && selectedAssets.length === 0) {
      setError("请先选择一张原图。");
      setLoading(false);
      return;
    }

    if (files.length > 1) {
      setError("多视图模型只支持上传 1 张原图。");
      setLoading(false);
      return;
    }

    try {
      const selectedAssetRefs = selectedAssets
        .map((asset) => ({
          url: asset.fileUrl ?? asset.previewUrl ?? asset.storageUrl ?? null,
          name: asset.name,
        }))
        .filter((item): item is { url: string; name: string } => Boolean(item.url));
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
      }, {
        onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job, jobProgressLabels)),
      });
      if (!response.image_url) {
        throw new Error("生成完成，但没有返回多视图结果图片，请稍后重试。");
      }

      const recordedPrompt = response.revised_prompt || displayPrompt;
      setResult(response);
      setSelectedHistoryId(null);
      onRecordRun({
        kind: "multi_view",
        title: "生成多视图",
        model: selectedModel.id,
        provider: response.provider,
        status: response.status,
        imageUrl: response.image_url,
        sourceImageUrl: response.source_image_url ?? uploadedPreviewUrl ?? selectedAssets[0]?.previewUrl ?? selectedAssets[0]?.storageUrl ?? null,
        prompt: recordedPrompt,
      });
      setJobProgress({ percent: 100, label: "已完成" });
      setProgressState("success");
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

            <GenerationProgress
              state={progressState}
              phases={progressPhases}
              successLabel="多视图已完成"
              errorLabel="多视图生成失败"
              progressValue={jobProgress?.percent ?? null}
              progressLabel={jobProgress?.label ?? null}
            />

            <button className="primary-button align-start" type="button" onClick={handleGenerate} disabled={loading || !selectedModel}>
              {loading ? "生成中..." : "生成多视图"}
            </button>
          </div>

          <div className="preview-history-layout multi-view-preview-layout">
            <div className="stack-list preview-history-main multi-view-preview-main">
              <details className="drawer-panel" open>
                <summary className="drawer-summary compact-drawer-summary">
                  <div>
                    <h4>结果预览</h4>
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
                      {previewResultUrl ? <img className="generated-image image-fit-contain interactive-preview-image" src={previewResultUrl} alt="多视图结果" /> : <div className="multi-view-single-card">四宫格结果图</div>}
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
