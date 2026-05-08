import { useEffect, useMemo, useState } from "react";

import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { FloatingToast } from "../components/FloatingToast";
import { GenerationProgress } from "../components/GenerationProgress";
import { PageGenerationHistory } from "../components/PageGenerationHistory";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import { getPromptTemplatesByModule } from "../data/promptTemplates";
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

const multiViewTemplates = getPromptTemplatesByModule("multi-view");
const defaultPrompt =
  multiViewTemplates[0]?.content ??
  "生成基于参考图的4个标准视角（正面（与参照图一致保持不变）、左侧、右侧、背面（采用正交透视法，仅展示作品本身的后部结构））并以2x2网格布局呈现，左侧、右侧、背面与参考图的风格、材质及工艺细节保持一致，所有四个视图必须来自一个连贯的三维模型。左侧、右侧、背面不得添加任何装饰、扭曲透视或虚构结构，无底座、无支架、无脚架、无支撑结构、无基座、无平衡装置。禁止在其背后或下方添加支撑，不用考虑重力，每个视图在几何上必须忠实于参考模型，柔和漫射影棚光线，优先保证细节清晰，避免过曝高光，纯白色哑光珠宝垫背景，8K高分辨率，专业珠宝摄影质感。";
const progressPhases = [
  { at: 18, label: "分析主视图结构..." },
  { at: 40, label: "提交多视图请求..." },
  { at: 74, label: "生成多角度视图中..." },
  { at: 95, label: "拼合四宫格结果..." },
];
const preferredMultiViewModelId = "gpt-image-2-all-apiyi";
const jobProgressLabels = {
  queued: "多视图任务排队中...",
  running: "生成多角度视图中...",
  uploading: "正在拼合并保存多视图结果...",
  succeeded: "已完成",
  failed: "多视图生成失败",
};

export function MultiViewPage({ assetItems, onRecordRun, pageRuns, onDeleteHistory }: MultiViewPageProps) {
  const { models, error: modelError, defaultModelId } = useModelCatalog((model) => model.supports_reference_images);
  const multiViewDefaultModelId = useMemo(
    () => models.find((item) => item.id === preferredMultiViewModelId)?.id ?? defaultModelId,
    [defaultModelId, models],
  );
  const [model, setModel] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<AssetItem[]>([]);
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
    if (loading) {
      return;
    }
    if (!selectedModel) {
      setError("当前没有可用的多视图模型。");
      return;
    }

    if (files.length === 0 && selectedAssets.length === 0) {
      setError("请先选择一张参考图。");
      return;
    }

    setLoading(true);
    setError(null);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "多视图任务排队中..." });

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
        throw new Error("未获取到可用参考图");
      }

      const response = await submitMultiViewGeneration({
        files,
        sourceImageUrls: files.length > 0 ? undefined : selectedAssetUrls,
        sourceImageNames: files.length > 0 ? undefined : selectedAssetNames,
        model: selectedModel.id,
        prompt: defaultPrompt,
        feature: "multi_view",
      }, {
        onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job, jobProgressLabels)),
      });
      if (!response.image_url) {
        throw new Error("生成完成，但没有返回多视图结果图片，请稍后重试。");
      }

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
        prompt: defaultPrompt,
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
              title="选择多视图来源"
              assetItems={assetItems}
              allowMultiple
              uploadLabel="上传多视图参考图"
              onUploadFilesChange={setFiles}
              onSelectedAssetsChange={setSelectedAssets}
            />

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
