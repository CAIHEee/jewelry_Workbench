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

const templates = getPromptTemplatesByModule("image-edit");
const defaultPrompt =
  templates[0]?.content ??
  "将这张珠宝线稿转换为写实高级珠宝产品图。保留原始轮廓、宝石位置、镶口结构和设计比例，加入抛光贵金属、真实宝石材质、柔和棚拍光线、干净背景和高级商业摄影质感。";
const progressPhases = [
  { at: 18, label: "分析线稿结构..." },
  { at: 40, label: "提交写实转绘请求..." },
  { at: 74, label: "渲染珠宝材质中..." },
  { at: 95, label: "整理写实结果..." },
];
const jobProgressLabels = {
  queued: "写实转绘任务排队中...",
  running: "渲染珠宝材质中...",
  uploading: "正在整理并保存写实结果...",
  succeeded: "已完成",
  failed: "线稿转写实图失败",
};
const preferredImageEditModelId = "gemini-3.1-flash-image-preview";
const generationCountOptions = [1, 2, 4] as const;
type GenerationCount = (typeof generationCountOptions)[number];

interface ImageEditPageProps {
  assetItems: AssetItem[];
  onRecordRun: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  onRefreshHistory?: () => Promise<void> | void;
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

export function ImageEditPage({ assetItems, onRecordRun: _onRecordRun, onRefreshHistory, pageRuns, onDeleteHistory }: ImageEditPageProps) {
  const { models, error: modelError, defaultModelId } = useModelCatalog((model) => model.supports_reference_images && isNotMultiViewOnlyModel(model));
  const imageEditDefaultModelId = useMemo(
    () => models.find((item) => item.id === preferredImageEditModelId)?.id ?? defaultModelId,
    [defaultModelId, models],
  );
  const [model, setModel] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<AssetItem[]>([]);
  const [results, setResults] = useState<GenerationResult[]>([]);
  const [generationCount, setGenerationCount] = useState<GenerationCount>(1);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progressState, setProgressState] = useState<"idle" | "running" | "success" | "error">("idle");
  const [jobProgress, setJobProgress] = useState<GenerationJobProgress | null>(null);

  useEffect(() => {
    if (!models.length) return;
    if (!model || !models.some((item) => item.id === model)) setModel(imageEditDefaultModelId);
  }, [imageEditDefaultModelId, model, models]);

  useEffect(() => {
    if (results.length > 0 || selectedHistoryId || pageRuns.length === 0) {
      return;
    }
    setSelectedHistoryId(pageRuns[0].id);
  }, [pageRuns, results.length, selectedHistoryId]);

  const selectedModel = useMemo(() => models.find((item) => item.id === model) ?? models[0] ?? null, [model, models]);
  const uploadedPreviewUrl = useMemo(() => (files[0] ? URL.createObjectURL(files[0]) : null), [files]);
  const selectedHistory = useMemo(() => pageRuns.find((item) => item.id === selectedHistoryId) ?? null, [pageRuns, selectedHistoryId]);
  const activeHistory = selectedHistory ?? (results.length === 0 ? pageRuns[0] ?? null : null);
  const latestResult = results[0] ?? null;
  const previewResultUrl = activeHistory?.imageUrl ?? latestResult?.image_url ?? null;
  const previewSourceUrl = useMemo(() => {
    const historySourceUrl = activeHistory?.sourceImageUrl ?? null;
    if (historySourceUrl && historySourceUrl !== previewResultUrl) {
      return historySourceUrl;
    }
    const historySourceImage = activeHistory?.sourceImages[0] ?? null;
    if (historySourceImage && historySourceImage !== previewResultUrl) {
      return historySourceImage;
    }
    return uploadedPreviewUrl ?? selectedAssets[0]?.previewUrl ?? selectedAssets[0]?.storageUrl ?? null;
  }, [activeHistory, previewResultUrl, selectedAssets, uploadedPreviewUrl]);

  useEffect(() => {
    return () => {
      if (uploadedPreviewUrl) URL.revokeObjectURL(uploadedPreviewUrl);
    };
  }, [uploadedPreviewUrl]);

  async function handleSubmit() {
    if (loading) {
      return;
    }
    if (!selectedModel) {
      setError("当前没有可用的图生图模型。");
      return;
    }
    if (files.length === 0 && selectedAssets.length === 0) {
      setError("请先选择一张线稿或草图。");
      return;
    }
    setLoading(true);
    setError(null);
    setResults([]);
    setSelectedHistoryId(null);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "写实转绘任务排队中..." });
    try {
      const selectedAsset = selectedAssets[0] ?? null;
      const selectedAssetUrl = selectedAsset?.fileUrl ?? selectedAsset?.previewUrl ?? selectedAsset?.storageUrl ?? null;
      const inputFile = files[0] ?? null;
      if (!inputFile && !selectedAssetUrl) throw new Error("未获取到可用参考图");

      const recordCompletedResponse = (response: GenerationResult) => {
        if (!response.image_url) {
          throw new Error("生成完成，但没有返回结果图片，请稍后重试。");
        }
        setResults((current) => [response, ...current]);
      };

      const response = await submitReferenceImageTransform({
        file: inputFile,
        sourceImageUrl: inputFile ? undefined : selectedAssetUrl ?? undefined,
        sourceImageName: inputFile ? undefined : selectedAsset?.name,
        model: selectedModel.id,
        prompt: defaultPrompt,
        feature: "sketch_to_realistic",
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
        throw new Error("生成完成，但没有返回结果图片，请稍后重试。");
      }
      validResponses.forEach((item) => recordCompletedResponse(item));
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
        label: submitError instanceof Error ? submitError.message : "线稿转写实图失败",
      });
      setError(submitError instanceof Error ? submitError.message : "线稿转写实图失败");
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
              title="选择线稿来源"
              assetItems={assetItems}
              uploadLabel="上传线稿或草图"
              enableRecommendedAsset={false}
              onUploadFilesChange={setFiles}
              onSelectedAssetsChange={setSelectedAssets}
            />

            <div className="input-group compact-input-group">
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

            <GenerationProgress
              state={progressState}
              phases={progressPhases}
              successLabel="写实图已完成"
              errorLabel="写实转绘失败"
              progressValue={jobProgress?.percent ?? null}
              progressLabel={jobProgress?.label ?? null}
            />

            <button className="primary-button align-start" type="button" onClick={handleSubmit} disabled={loading || !selectedModel}>
              {loading ? "生成中..." : `生成${generationCount > 1 ? ` ${generationCount} 张` : ""}写实图`}
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
                    <span>写实结果</span>
                    <div
                      className={previewResultUrl ? "generated-result-card compare image-edit-result-card interactive-result-card" : "generated-result-card compare image-edit-result-card"}
                      role={previewResultUrl ? "button" : undefined}
                      tabIndex={previewResultUrl ? 0 : undefined}
                      onClick={previewResultUrl ? () => setPreviewOpen(true) : undefined}
                    >
                      {previewResultUrl ? <img className="generated-image image-fit-contain interactive-preview-image" src={previewResultUrl} alt="写实结果" /> : <div className="compare-card after" />}
                    </div>
                  </div>
                </div>
              </details>
            </div>

            <PageGenerationHistory
              title="线稿转写实图历史"
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
          title="线稿转写实图预览"
          sourceUrl={previewSourceUrl}
          sourceLabel="原始图"
          resultUrl={previewResultUrl}
          resultLabel="写实结果"
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </div>
  );
}
