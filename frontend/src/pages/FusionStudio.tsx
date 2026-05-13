import { type FormEvent, useEffect, useMemo, useState } from "react";

import { AutoResizeTextarea } from "../components/AutoResizeTextarea";
import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { FloatingToast } from "../components/FloatingToast";
import { GenerationProgress } from "../components/GenerationProgress";
import { PageGenerationHistory } from "../components/PageGenerationHistory";
import { PromptTemplateImporter } from "../components/PromptTemplateImporter";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import { getPromptTemplatesByModule } from "../data/promptTemplates";
import { useModelCatalog } from "../hooks/useModelCatalog";
import { submitFusionJob } from "../services/api";
import type { FusionMode, FusionResult, GenerationJobProgress } from "../types/fusion";
import type { AssetItem } from "../types/mockData";
import type { WorkspaceRun } from "../types/workspace";
import { buildGenerationJobProgress } from "../utils/jobProgress";
import type { ModuleHistoryEntry } from "../utils/history";

const modes: Array<{ value: FusionMode; label: string; description: string }> = [
  { value: "balanced", label: "均衡融合", description: "尽量平衡保留各图核心元素" },
  { value: "style_first", label: "风格优先", description: "优先继承材质、表面风格与视觉氛围" },
  { value: "structure_first", label: "结构优先", description: "优先保留主体结构和轮廓关系" },
  { value: "detail_enhanced", label: "细节增强", description: "强化纹理、镶口和装饰细节" },
];

const templates = getPromptTemplatesByModule("fusion");
const progressPhases = [
  { at: 16, label: "校验多张参考图..." },
  { at: 38, label: "提交融合任务..." },
  { at: 72, label: "融合材质与结构中..." },
  { at: 95, label: "整理融合结果..." },
];
const jobProgressLabels = {
  queued: "多图融合任务排队中...",
  running: "融合材质与结构中...",
  uploading: "正在整理并保存融合结果...",
  succeeded: "已完成",
  failed: "多图融合失败",
};

interface FusionStudioProps {
  onRecordRun: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  assetItems: AssetItem[];
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

export function FusionStudio({ onRecordRun, assetItems, pageRuns, onDeleteHistory }: FusionStudioProps) {
  const { models, error: modelError, defaultModelId } = useModelCatalog((item) => item.supports_multi_image_fusion);
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<AssetItem[]>([]);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState(defaultModelId);
  const [mode, setMode] = useState<FusionMode>("balanced");
  const [primaryImageIndex, setPrimaryImageIndex] = useState(0);
  const [strength, setStrength] = useState(0.75);
  const [result, setResult] = useState<FusionResult | null>(null);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressState, setProgressState] = useState<"idle" | "running" | "success" | "error">("idle");
  const [jobProgress, setJobProgress] = useState<GenerationJobProgress | null>(null);

  useEffect(() => {
    if (!models.length) {
      return;
    }
    if (!model || !models.some((item) => item.id === model)) {
      setModel(defaultModelId);
    }
  }, [defaultModelId, model, models]);

  useEffect(() => {
    if (result || selectedHistoryId || pageRuns.length === 0) {
      return;
    }
    setSelectedHistoryId(pageRuns[0].id);
  }, [pageRuns, result, selectedHistoryId]);

  const selectedModel = useMemo(() => models.find((item) => item.id === model) ?? models[0] ?? null, [model, models]);
  const uploadPreviewUrls = useMemo(() => files.map((file) => URL.createObjectURL(file)), [files]);
  const selectedInputItems = useMemo(
    () =>
      files.length > 0
        ? files.map((file, index) => ({
            key: `upload-${index}-${file.name}`,
            name: file.name,
            previewUrl: uploadPreviewUrls[index] ?? null,
          }))
        : selectedAssets.map((asset) => ({
            key: `asset-${asset.id}`,
            name: asset.name,
            previewUrl: asset.previewUrl ?? asset.storageUrl ?? null,
          })),
    [files, selectedAssets, uploadPreviewUrls],
  );
  const selectedHistory = useMemo(() => pageRuns.find((item) => item.id === selectedHistoryId) ?? null, [pageRuns, selectedHistoryId]);
  const activeHistory = selectedHistory ?? (!result ? pageRuns[0] ?? null : null);
  const previewResultUrl = activeHistory?.imageUrl ?? result?.image_url ?? null;
  const previewSourceUrl = activeHistory?.sourceImages?.[activeHistory.primaryImageIndex ?? 0] ?? activeHistory?.sourceImageUrl ?? selectedInputItems[primaryImageIndex]?.previewUrl ?? null;

  useEffect(() => {
    return () => {
      uploadPreviewUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [uploadPreviewUrls]);

  useEffect(() => {
    if (primaryImageIndex >= selectedInputItems.length) {
      setPrimaryImageIndex(0);
    }
  }, [primaryImageIndex, selectedInputItems.length]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setError(null);

    if (!selectedModel) {
      setError("当前没有可用的多图融合模型。");
      return;
    }

    if (files.length + selectedAssets.length < 2) {
      setError("请至少选择两张参考图。");
      return;
    }
    if (!prompt.trim()) {
      setError("请输入融合提示词。");
      return;
    }

    setIsSubmitting(true);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "多图融合任务排队中..." });

    try {
      const sourceImageUrls = selectedAssets
        .map((asset) => asset.fileUrl ?? asset.previewUrl ?? asset.storageUrl ?? null)
        .filter((item): item is string => Boolean(item));
      const submitFiles = files.length > 0 ? files : undefined;
      const submitSourceImageUrls = files.length > 0 ? undefined : sourceImageUrls;
      const submitSourceImageNames = files.length > 0 ? undefined : selectedAssets.map((asset) => asset.name);

      const response = await submitFusionJob({
        files: submitFiles,
        sourceImageUrls: submitSourceImageUrls,
        sourceImageNames: submitSourceImageNames,
        model: selectedModel.id,
        prompt: prompt.trim(),
        mode,
        primaryImageIndex,
        strength,
      }, {
        onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job, jobProgressLabels)),
      });
      if (!response.image_url) {
        throw new Error("融合完成，但没有返回结果图片，请稍后重试。");
      }

      setResult(response);
      setSelectedHistoryId(null);
      onRecordRun({
        kind: "fusion",
        title: "多图融合",
        model: selectedModel.id,
        provider: response.provider,
        status: response.status,
        imageUrl: response.image_url,
        sourceImageUrl: selectedInputItems[primaryImageIndex]?.previewUrl ?? null,
        sourceImages: selectedInputItems.map((item) => item.previewUrl).filter((item): item is string => Boolean(item)),
        primaryImageIndex,
        prompt: prompt.trim(),
      });
      setJobProgress({ percent: 100, label: "已完成" });
      setProgressState("success");
    } catch (submitError) {
      setIsSubmitting(false);
      setProgressState("error");
      setJobProgress({
        percent: 100,
        label: submitError instanceof Error ? submitError.message : "多图融合失败",
      });
      setError(submitError instanceof Error ? submitError.message : "多图融合失败");
      return;
    }

    setIsSubmitting(false);
  }

  return (
    <div className="page-stack compact-page split-page">
      <FloatingToast message={error} />
      <section className="panel compact-panel">
        <div className="dashboard-grid result-heavy single-result-layout">
          <form className="form-card parameter-scroll-panel compact-parameter-panel" onSubmit={handleSubmit}>
            <label className="input-group compact-input-group">
              <span>融合模型</span>
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
              title="选择参考图"
              assetItems={assetItems}
              allowMultiple
              uploadLabel="上传融合参考图"
              onUploadFilesChange={(nextFiles) => {
                setFiles(nextFiles);
                if (primaryImageIndex >= nextFiles.length) {
                  setPrimaryImageIndex(0);
                }
              }}
              onSelectedAssetsChange={(nextAssets) => {
                setSelectedAssets(nextAssets);
                if (primaryImageIndex >= nextAssets.length) {
                  setPrimaryImageIndex(0);
                }
              }}
            />

            {selectedInputItems.length > 0 ? (
              <div className="file-list compact-file-list">
                {selectedInputItems.map((item, index) => (
                  <div className="file-chip fusion-primary-chip" key={item.key}>
                    <span className="fusion-primary-chip-name">{item.name}</span>
                    <button
                      type="button"
                      className={primaryImageIndex === index ? "fusion-primary-chip-button chip-active" : "fusion-primary-chip-button"}
                      onClick={() => setPrimaryImageIndex(index)}
                    >
                      {primaryImageIndex === index ? "主图" : "设为主图"}
                    </button>
                  </div>
                ))}
              </div>
            ) : null}

            <label className="input-group prompt-input-group compact-prompt-group">
              <div className="prompt-input-header compact-prompt-header">
                <span>融合提示词</span>
                <PromptTemplateImporter templates={templates} onImport={setPrompt} />
              </div>
              <AutoResizeTextarea className="prompt-textarea" value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={3} />
            </label>

            <details className="drawer-panel inner-drawer">
              <summary className="drawer-summary compact-drawer-summary">
                <div>
                  <h4>高级设置</h4>
                </div>
                <span className="drawer-hint">展开 / 收起</span>
              </summary>
              <div className="drawer-content">
                <div className="dense-grid">
                  <label className="input-group compact-input-group">
                    <span>融合模式</span>
                    <select value={mode} onChange={(event) => setMode(event.target.value as FusionMode)}>
                      {modes.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <small>{modes.find((item) => item.value === mode)?.description}</small>
                  </label>

                  <label className="input-group compact-input-group">
                    <span>融合强度：{strength.toFixed(2)}</span>
                    <input type="range" min="0.2" max="1" step="0.05" value={strength} onChange={(event) => setStrength(Number(event.target.value))} />
                  </label>
                </div>
              </div>
            </details>

            <GenerationProgress
              state={progressState}
              phases={progressPhases}
              successLabel="融合已完成"
              errorLabel="融合失败"
              progressValue={jobProgress?.percent ?? null}
              progressLabel={jobProgress?.label ?? null}
            />

            <button className="primary-button align-start" type="submit" disabled={isSubmitting || !selectedModel}>
              {isSubmitting ? "提交中..." : "提交融合任务"}
            </button>
          </form>

          <div className="preview-history-layout">
            <div className="stack-list preview-history-main">
              <details className="drawer-panel" open>
                <summary className="drawer-summary compact-drawer-summary">
                  <div>
                    <h4>结果预览</h4>
                  </div>
                  <span className="drawer-hint">展开 / 收起</span>
                </summary>
                <div className="drawer-content">
                  {previewResultUrl || result ? (
                    <div className="stack-list">
                      <div className="result-preview-pane result-preview-pane-single">
                        <span>融合结果</span>
                        <div
                          className={previewResultUrl ? "generated-result-card compare image-edit-result-card interactive-result-card" : "generated-result-card compare image-edit-result-card"}
                          role={previewResultUrl ? "button" : undefined}
                          tabIndex={previewResultUrl ? 0 : undefined}
                          onClick={previewResultUrl ? () => setPreviewOpen(true) : undefined}
                          onKeyDown={
                            previewResultUrl
                              ? (event) => {
                                  if (event.key === "Enter" || event.key === " ") {
                                    event.preventDefault();
                                    setPreviewOpen(true);
                                  }
                                }
                              : undefined
                          }
                        >
                          {previewResultUrl ? <img className="generated-image image-fit-contain interactive-preview-image" src={previewResultUrl} alt="融合结果" /> : <div className="compare-card after" />}
                        </div>
                      </div>

                    </div>
                  ) : (
                    <div className="result-preview-pane result-preview-pane-single">
                      <span>融合结果</span>
                      <div className="generated-result-card compare image-edit-result-card">
                        <div className="compare-card after" />
                      </div>
                    </div>
                  )}
                </div>
              </details>
            </div>

            <PageGenerationHistory title="多图融合历史" items={pageRuns} activeId={selectedHistoryId} onPreview={(item) => setSelectedHistoryId(item.id)} onDeleteHistory={onDeleteHistory} />
          </div>
        </div>
      </section>

      {previewOpen ? (
        <ResultPreviewModal
          title="多图融合预览"
          sourceUrl={previewSourceUrl}
          sourceLabel="主图"
          resultUrl={previewResultUrl}
          resultLabel="融合结果"
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </div>
  );
}
