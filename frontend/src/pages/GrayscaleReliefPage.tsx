import { useEffect, useMemo, useState } from "react";

import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { FloatingToast } from "../components/FloatingToast";
import { GenerationProgress } from "../components/GenerationProgress";
import { PageGenerationHistory } from "../components/PageGenerationHistory";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import { getPromptTemplatesByModule } from "../data/promptTemplates";
import { useModelCatalog } from "../hooks/useModelCatalog";
import { submitReferenceImageTransform } from "../services/api";
import type { GenerationJobProgress, GenerationResult } from "../types/fusion";
import type { AssetItem } from "../types/mockData";
import type { WorkspaceRun } from "../types/workspace";
import { buildGenerationJobProgress } from "../utils/jobProgress";
import type { ModuleHistoryEntry } from "../utils/history";
import { isNotMultiViewOnlyModel } from "../utils/modelFilters";

const templates = getPromptTemplatesByModule("grayscale-relief");
const defaultPrompt =
  templates[0]?.content ??
  "严格遵循参考图像：保持精确的3D结构、比例、透视以及所有雕塑细节。形式上不得有任何偏差，不得添加任何元素。渲染为纯黏土模型：单色调哑光灰色材质，无金属反射，无宝石折射，无抛光。仅使用微妙的灰度着色来传达形态。照明：仅使用柔和的环境光遮挡（AO），最小程度的平行光，无刺眼的阴影，无镜面高光。细节必须保持清晰——每个纹理、雕刻和结构边缘都要完全保留。背景为深哑光黑色，拓扑风格简洁呈现。无后期处理，无珠宝风格渲染，无逼真效果。";
const progressPhases = [
  { at: 18, label: "提取结构轮廓..." },
  { at: 42, label: "提交灰度转换请求..." },
  { at: 76, label: "生成灰阶泥模质感..." },
  { at: 95, label: "整理灰度结果..." },
];
const preferredGrayscaleModelId = "gpt-image-2-all-apiyi";
const jobProgressLabels = {
  queued: "灰度转换任务排队中...",
  running: "生成灰阶泥模质感...",
  uploading: "正在整理并保存灰度结果...",
  succeeded: "已完成",
  failed: "灰度转换失败",
};

interface GrayscaleReliefPageProps {
  assetItems: AssetItem[];
  onRecordRun: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

export function GrayscaleReliefPage({ assetItems, onRecordRun, pageRuns, onDeleteHistory }: GrayscaleReliefPageProps) {
  const { models, error: modelError, defaultModelId } = useModelCatalog((model) => model.supports_reference_images && isNotMultiViewOnlyModel(model));
  const grayscaleDefaultModelId = useMemo(
    () => models.find((item) => item.id === preferredGrayscaleModelId)?.id ?? defaultModelId,
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
    if (!models.length) {
      return;
    }
    if (!model || !models.some((item) => item.id === model)) {
      setModel(grayscaleDefaultModelId);
    }
  }, [grayscaleDefaultModelId, model, models]);

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
      setError("当前没有可用的转灰度图模型。");
      return;
    }

    if (files.length === 0 && selectedAssets.length === 0) {
      setError("请先选择一张参考图。");
      return;
    }

    setLoading(true);
    setError(null);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "灰度转换任务排队中..." });

    try {
      const selectedAsset = selectedAssets[0] ?? null;
      const selectedAssetUrl = selectedAsset?.fileUrl ?? selectedAsset?.previewUrl ?? selectedAsset?.storageUrl ?? null;
      const inputFile = files[0] ?? null;
      if (!inputFile && !selectedAssetUrl) {
        throw new Error("未获取到可用参考图");
      }

      const response = await submitReferenceImageTransform({
        file: inputFile,
        sourceImageUrl: inputFile ? undefined : selectedAssetUrl ?? undefined,
        sourceImageName: inputFile ? undefined : selectedAsset?.name,
        model: selectedModel.id,
        prompt: defaultPrompt,
        feature: "grayscale_relief",
      }, {
        onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job, jobProgressLabels)),
      });
      if (!response.image_url) {
        throw new Error("生成完成，但没有返回灰度结果图片，请稍后重试。");
      }

      setResult(response);
      setSelectedHistoryId(null);
      onRecordRun({
        kind: "grayscale_relief",
        title: "转灰度图",
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
        label: submitError instanceof Error ? submitError.message : "转灰度图失败",
      });
      setError(submitError instanceof Error ? submitError.message : "转灰度图失败");
      return;
    }

    setLoading(false);
  }

  return (
    <div className="page-stack compact-page split-page">
      <FloatingToast message={error} />
      <section className="panel compact-panel">
        <div className="dashboard-grid result-heavy image-edit-layout">
          <div className="form-card parameter-scroll-panel image-edit-form compact-parameter-panel">
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
              title="选择灰度图来源"
              assetItems={assetItems}
              uploadLabel="上传参考图"
              onUploadFilesChange={setFiles}
              onSelectedAssetsChange={setSelectedAssets}
            />

            <GenerationProgress
              state={progressState}
              phases={progressPhases}
              successLabel="灰度图已完成"
              errorLabel="灰度转换失败"
              progressValue={jobProgress?.percent ?? null}
              progressLabel={jobProgress?.label ?? null}
            />

            <button className="primary-button align-start" type="button" onClick={handleGenerate} disabled={loading || !selectedModel}>
              {loading ? "生成中..." : "生成灰度图"}
            </button>
          </div>

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
                  <div className="result-preview-pane result-preview-pane-single">
                    <span>灰度结果</span>
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
                      {previewResultUrl ? <img className="generated-image image-fit-contain interactive-preview-image" src={previewResultUrl} alt="灰度结果" /> : <div className="grayscale-preview-card" />}
                    </div>
                  </div>
                </div>
              </details>
            </div>

            <PageGenerationHistory
              title="转灰度图历史"
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
          title="转灰度图预览"
          sourceUrl={previewSourceUrl}
          sourceLabel="原始图"
          resultUrl={previewResultUrl}
          resultLabel="灰度结果"
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </div>
  );
}
