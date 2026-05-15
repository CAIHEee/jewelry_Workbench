import { useEffect, useMemo, useState, type CSSProperties } from "react";

import { AssetSourcePicker } from "../components/AssetSourcePicker";
import { FloatingToast } from "../components/FloatingToast";
import { PageGenerationHistory } from "../components/PageGenerationHistory";
import { PreviewTimer } from "../components/PreviewTimer";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import { splitMultiViewImage, uploadInputAsset } from "../services/api";
import type { GenerationJobProgress, MultiViewSplitItem, MultiViewSplitResponse } from "../types/fusion";
import type { AssetItem } from "../types/mockData";
import type { WorkspaceRun } from "../types/workspace";
import { buildDownloadFilename, buildDownloadUrl } from "../utils/download";
import { buildGenerationJobProgress } from "../utils/jobProgress";
import type { ModuleHistoryEntry } from "../utils/history";

interface MultiViewSplitPageProps {
  assetItems: AssetItem[];
  onRecordRun?: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

type SplitPreviewTab = "source" | "result";

const SPLIT_TOOL_ID = "multi_view_split";
const progressPhases = [
  { at: 18, label: "校验四宫格原图..." },
  { at: 42, label: "提交切图任务..." },
  { at: 78, label: "裁切并整理各视图..." },
  { at: 95, label: "保存切图结果..." },
];
const jobProgressLabels = {
  queued: "多视图切图任务排队中...",
  running: "裁切并整理各视图...",
  uploading: "正在保存切图结果...",
  succeeded: "已完成",
  failed: "多视图切图失败",
};

const splitViewLabels: Record<string, string> = {
  front: "正视图",
  side: "右视图",
  top: "左视图",
  back: "背视图",
};

function buildSplitDownloadName(item: MultiViewSplitItem | null) {
  if (!item) {
    return "multi-view-split-result.png";
  }

  return buildDownloadFilename(`multi-view-${item.view}`, item.image_url);
}

export function MultiViewSplitPage({ assetItems, onRecordRun, pageRuns, onDeleteHistory }: MultiViewSplitPageProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [selectedAssets, setSelectedAssets] = useState<AssetItem[]>([]);
  const [localSourcePreviewUrl, setLocalSourcePreviewUrl] = useState<string | null>(null);
  const [splitPreviewUrl, setSplitPreviewUrl] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [splitResult, setSplitResult] = useState<MultiViewSplitResponse | null>(null);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [pendingHistoryId, setPendingHistoryId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [splitXRatio, setSplitXRatio] = useState(0.5);
  const [splitYRatio, setSplitYRatio] = useState(0.5);
  const [gapXRatio, setGapXRatio] = useState(0);
  const [gapYRatio, setGapYRatio] = useState(0);
  const [activeTab, setActiveTab] = useState<SplitPreviewTab>("source");
  const [progressState, setProgressState] = useState<"idle" | "running" | "success" | "error">("idle");
  const [jobProgress, setJobProgress] = useState<GenerationJobProgress | null>(null);
  const [currentGenerationStartedAt, setCurrentGenerationStartedAt] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (localSourcePreviewUrl) {
        URL.revokeObjectURL(localSourcePreviewUrl);
      }
    };
  }, [localSourcePreviewUrl]);

  const splitOverlayStyle = {
    ["--split-x" as const]: `${(splitXRatio * 100).toFixed(2)}%`,
    ["--split-y" as const]: `${(splitYRatio * 100).toFixed(2)}%`,
    ["--gap-x" as const]: `${(gapXRatio * 100).toFixed(2)}%`,
    ["--gap-y" as const]: `${(gapYRatio * 100).toFixed(2)}%`,
  } as CSSProperties;

  const currentSourcePreviewUrl = localSourcePreviewUrl ?? selectedAssets[0]?.previewUrl ?? selectedAssets[0]?.storageUrl ?? null;
  const historyItems = useMemo<ModuleHistoryEntry[]>(() => {
    if (!pendingHistoryId || !loading) return pageRuns;
    return [
      {
        id: pendingHistoryId,
        kind: "multi_view_split",
        title: "生成中...",
        model: "处理中",
        provider: "任务队列",
        status: "生成中...",
        prompt: "",
        imageUrl: null,
        sourceImageUrl: currentSourcePreviewUrl,
        sourceImages: [],
        primaryImageIndex: null,
        splitItems: [],
        createdAt: currentGenerationStartedAt ?? new Date().toISOString(),
        source: "session",
      },
      ...pageRuns,
    ];
  }, [currentGenerationStartedAt, currentSourcePreviewUrl, loading, pageRuns, pendingHistoryId]);
  const selectedHistory = useMemo(() => historyItems.find((item) => item.id === selectedHistoryId) ?? null, [historyItems, selectedHistoryId]);
  const isViewingPendingGeneration = Boolean(pendingHistoryId && selectedHistoryId === pendingHistoryId && loading);
  const hasCurrentSourceSelection = Boolean(localSourcePreviewUrl || selectedAssets[0]);
  const sourcePreviewUrl = currentSourcePreviewUrl ?? selectedHistory?.sourceImageUrl ?? null;
  const activeSplitItems = useMemo<MultiViewSplitItem[]>(
    () => {
      if (selectedHistory?.splitItems.length) {
        return selectedHistory.splitItems.map((item) => ({
            view: item.view,
            image_url: item.imageUrl,
            storage_url: item.storageUrl ?? null,
            width: item.width,
            height: item.height,
        }));
      }
      if (!hasCurrentSourceSelection && splitResult?.items.length) {
        return splitResult.items.map((item) => ({
          view: item.view,
          image_url: item.image_url,
          storage_url: item.storage_url ?? null,
          width: item.width,
          height: item.height,
        }));
      }
      return [];
    },
    [hasCurrentSourceSelection, selectedHistory, splitResult],
  );

  useEffect(() => {
    if (!hasCurrentSourceSelection) {
      return;
    }

    setSelectedHistoryId(null);
    setActiveTab("source");
  }, [hasCurrentSourceSelection, localSourcePreviewUrl, selectedAssets]);

  function handleUploadFilesChange(nextFiles: File[]) {
    setFiles(nextFiles);
    setSelectedAssets([]);
    setSplitResult(null);
    setSplitPreviewUrl(null);
    setLocalSourcePreviewUrl((current) => {
      if (current) {
        URL.revokeObjectURL(current);
      }
      return nextFiles[0] ? URL.createObjectURL(nextFiles[0]) : null;
    });
  }

  function handleSelectedAssetsChange(nextAssets: AssetItem[]) {
    setSelectedAssets(nextAssets);
    if (nextAssets.length > 0) {
      setFiles([]);
      setSplitResult(null);
      setSplitPreviewUrl(null);
      setLocalSourcePreviewUrl((current) => {
        if (current) {
          URL.revokeObjectURL(current);
        }
        return null;
      });
    }
  }

  async function resolveSourceImageUrl() {
    if (selectedAssets[0]) {
      return selectedAssets[0].previewUrl ?? selectedAssets[0].storageUrl ?? null;
    }

    if (files[0]) {
      const uploaded = await uploadInputAsset(files[0], "multi_view", "split_source_upload");
      return uploaded.preview_url ?? uploaded.storage_url ?? null;
    }

    return null;
  }

  async function handleSplit() {
    if (loading) {
      return;
    }
    setLoading(true);
    setError(null);
    const startedAt = new Date().toISOString();
    const nextPendingHistoryId = `pending-multi-view-split-${Date.now()}`;
    setPendingHistoryId(nextPendingHistoryId);
    setSelectedHistoryId(nextPendingHistoryId);
    setActiveTab("result");
    setCurrentGenerationStartedAt(startedAt);
    setProgressState("running");
    setJobProgress({ percent: 18, label: "多视图切图任务排队中..." });

    try {
      const sourceImageUrl = await resolveSourceImageUrl();
      if (!sourceImageUrl) {
        throw new Error("请先选择一张待切图的四宫格多视图图片。");
      }
      const sourceImageName = selectedAssets[0]?.name ?? files[0]?.name;

      const response = await splitMultiViewImage({
        image_url: sourceImageUrl,
        source_image_name: sourceImageName,
        model: SPLIT_TOOL_ID,
        split_x_ratio: splitXRatio,
        split_y_ratio: splitYRatio,
        gap_x_ratio: gapXRatio,
        gap_y_ratio: gapYRatio,
      }, {
        onJobUpdate: (job) => setJobProgress(buildGenerationJobProgress(job, jobProgressLabels)),
      });
      if (!response.items.length || !response.items.some((item) => item.image_url)) {
        throw new Error("切图完成，但没有返回有效结果，请调整参数后重试。");
      }

      setSplitResult(response);
      setSelectedHistoryId(null);
      setActiveTab("result");
      setJobProgress({ percent: 100, label: "已完成" });
      setProgressState("success");
      onRecordRun?.({
        kind: "multi_view_split",
        title: "多视图切图",
        model: SPLIT_TOOL_ID,
        provider: "system",
        status: response.status,
        imageUrl: response.items[0]?.image_url ?? null,
        sourceImageUrl,
        sourceImages: response.items.map((item) => item.image_url).filter((item): item is string => Boolean(item)),
        primaryImageIndex: 0,
        splitItems: response.items.map((item) => ({
          view: item.view,
          imageUrl: item.image_url,
          storageUrl: item.storage_url,
          width: item.width,
          height: item.height,
        })),
        prompt: "Split four-grid multi-view image into separate view assets.",
      });
    } catch (splitError) {
      setLoading(false);
      setPendingHistoryId(null);
      setProgressState("error");
      setJobProgress({
        percent: 100,
        label: splitError instanceof Error ? splitError.message : "多视图切图失败。",
      });
      setError(splitError instanceof Error ? splitError.message : "多视图切图失败。");
      return;
    }

    setPendingHistoryId(null);
    setLoading(false);
  }

  function handlePreviewSource() {
    if (sourcePreviewUrl) {
      setPreviewOpen(true);
    }
  }

  return (
    <div className="page-stack compact-page split-page multi-view-page">
      <FloatingToast message={error} />
      <section className="panel compact-panel">
        <div className="dashboard-grid result-heavy single-result-layout">
          <div className="form-card parameter-scroll-panel compact-parameter-panel">
            <AssetSourcePicker
              title="选择切图来源"
              assetItems={assetItems}
              uploadLabel="上传图片"
              onUploadFilesChange={handleUploadFilesChange}
              onSelectedAssetsChange={handleSelectedAssetsChange}
            />

            <details className="drawer-panel inner-drawer">
              <summary className="drawer-summary compact-drawer-summary">
                <div>
                  <h4>切图设置</h4>
                </div>
                <span className="drawer-hint">展开 / 收起</span>
              </summary>
              <div className="drawer-content">
                <div className="dense-grid">
                  <label className="input-group compact-input-group">
                    <span>竖向裁切线：{splitXRatio.toFixed(2)}</span>
                    <input type="range" min="0.2" max="0.8" step="0.01" value={splitXRatio} onChange={(event) => setSplitXRatio(Number(event.target.value))} />
                  </label>
                  <label className="input-group compact-input-group">
                    <span>横向裁切线：{splitYRatio.toFixed(2)}</span>
                    <input type="range" min="0.2" max="0.8" step="0.01" value={splitYRatio} onChange={(event) => setSplitYRatio(Number(event.target.value))} />
                  </label>
                  <label className="input-group compact-input-group">
                    <span>竖向间隔：{gapXRatio.toFixed(2)}</span>
                    <input type="range" min="0" max="0.15" step="0.005" value={gapXRatio} onChange={(event) => setGapXRatio(Number(event.target.value))} />
                  </label>
                  <label className="input-group compact-input-group">
                    <span>横向间隔：{gapYRatio.toFixed(2)}</span>
                    <input type="range" min="0" max="0.15" step="0.005" value={gapYRatio} onChange={(event) => setGapYRatio(Number(event.target.value))} />
                  </label>
                </div>
              </div>
            </details>

            <button className="primary-button align-start" type="button" onClick={handleSplit} disabled={loading}>
              {loading ? "切图中..." : "开始切图"}
            </button>
          </div>

          <div className="preview-history-layout">
            <div className="stack-list preview-history-main multi-view-preview-main">
              <section className="drawer-panel split-preview-shell">
              <div className="split-preview-nav">
                <button
                  type="button"
                  className={activeTab === "source" ? "split-preview-tab active" : "split-preview-tab"}
                  onClick={() => setActiveTab("source")}
                >
                  切图预览
                </button>
                <button
                  type="button"
                  className={activeTab === "result" ? "split-preview-tab active" : "split-preview-tab"}
                  onClick={() => setActiveTab("result")}
                  disabled={activeSplitItems.length === 0 && !isViewingPendingGeneration}
                >
                  切图结果
                </button>
              </div>

              <div className="split-preview-panel">
                {activeTab === "source" ? (
                  <div className="split-preview-pane">
                    <div className="split-preview-panel-head compact">
                      <div>
                        <h4>待切图原图</h4>
                        <p className="muted">调整左侧参数后，这里会实时展示裁切线和间隔位置。</p>
                      </div>
                    </div>

                    <div
                      className={sourcePreviewUrl ? "generated-result-card compare image-edit-result-card interactive-result-card split-stage-card" : "generated-result-card compare image-edit-result-card split-stage-card"}
                      role={sourcePreviewUrl ? "button" : undefined}
                      tabIndex={sourcePreviewUrl ? 0 : undefined}
                      onClick={sourcePreviewUrl ? handlePreviewSource : undefined}
                    >
                      {sourcePreviewUrl ? (
                        <div className="split-preview-stage" style={splitOverlayStyle}>
                          <img className="generated-image image-fit-contain interactive-preview-image" src={sourcePreviewUrl} alt="待切图原图" />
                          <div className="split-line split-line-vertical" />
                          <div className="split-line split-line-horizontal" />
                          <div className="split-gap split-gap-vertical" />
                          <div className="split-gap split-gap-horizontal" />
                        </div>
                      ) : (
                        <div className="multi-view-single-card">请选择一张四宫格多视图图片。</div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="split-preview-pane result-preview-pane-single">
                    <div className="split-preview-panel-head compact">
                      <div>
                        <h4>切图结果</h4>
                        <p className="muted">以下展示四个切图结果，点击任意图片可进入灯箱放大预览。</p>
                      </div>
                      <PreviewTimer startedAt={currentGenerationStartedAt} running={loading} />
                    </div>

                    {isViewingPendingGeneration ? (
                      <div className="panel-subcard compact split-empty-state">
                        <p className="muted">{jobProgress?.label ?? "多视图切图任务处理中..."}</p>
                        <span className="status-pill online">{Math.round(jobProgress?.percent ?? 18)}%</span>
                      </div>
                    ) : activeSplitItems.length ? (
                      <>
                        <div className="split-result-thumbnail-row">
                          {activeSplitItems.map((item) => (
                            <div key={item.view} className="split-result-thumbnail active">
                              <button
                                type="button"
                                className="split-result-thumbnail-selector"
                                onClick={() => item.image_url && setSplitPreviewUrl(item.image_url)}
                                title={splitViewLabels[item.view] ?? item.view}
                              >
                                <span className="split-result-thumbnail-label">
                                  {splitViewLabels[item.view] ?? item.view}
                                  <span className="split-result-dimension-pill">
                                    {item.width} x {item.height}
                                  </span>
                                </span>
                                <div className="split-result-thumbnail-frame">
                                  {item.image_url ? (
                                    <img className="generated-image image-fit-contain" src={item.image_url} alt={splitViewLabels[item.view] ?? item.view} />
                                  ) : (
                                    <div className="compare-card after" />
                                  )}
                                </div>
                              </button>

                              {item.image_url ? (
                                <a className="split-result-thumbnail-download" href={buildDownloadUrl(item.image_url, buildSplitDownloadName(item)) ?? item.image_url} download={buildSplitDownloadName(item)}>
                                  下载图片
                                </a>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      </>
                    ) : (
                      <div className="panel-subcard compact split-empty-state">
                        <p className="muted">完成切图后，这里会展示各视图结果。</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
              </section>
            </div>

            <PageGenerationHistory
              title="多视图切图历史"
              items={historyItems}
              activeId={selectedHistoryId}
              onPreview={(item) => {
                if (item.id === pendingHistoryId) {
                  setSelectedHistoryId(item.id);
                  setActiveTab("result");
                  return;
                }
                setFiles([]);
                setSelectedAssets([]);
                setSplitResult(null);
                setSplitPreviewUrl(null);
                setLocalSourcePreviewUrl((current) => {
                  if (current) {
                    URL.revokeObjectURL(current);
                  }
                  return null;
                });
                setSelectedHistoryId(item.id);
                setActiveTab("result");
              }}
              onDeleteHistory={onDeleteHistory}
            />
          </div>
        </div>
      </section>

      {previewOpen ? <ResultPreviewModal title="多视图切图预览" resultUrl={sourcePreviewUrl} resultLabel="待切图原图" onClose={() => setPreviewOpen(false)} /> : null}

      {splitPreviewUrl ? (
        <ResultPreviewModal
          title="切图结果预览"
          sourceUrl={sourcePreviewUrl}
          sourceLabel="原始图"
          resultUrl={splitPreviewUrl}
          resultLabel="单视图结果"
          onClose={() => setSplitPreviewUrl(null)}
        />
      ) : null}
    </div>
  );
}
