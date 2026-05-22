import { useEffect, useMemo, useState } from "react";

import { ResultPreviewModal } from "../components/ResultPreviewModal";
import type { CurrentUser } from "../types/auth";
import type { AssetItem } from "../types/mockData";
import { buildDownloadFilename, buildDownloadUrl } from "../utils/download";

const ASSET_CATEGORY_OPTIONS = [
  { label: "全部", moduleKind: null },
  { label: "文生图", moduleKind: "text_to_image" },
  { label: "生成多视图", moduleKind: "multi_view" },
  { label: "线稿转写实图", moduleKind: "sketch_to_realistic" },
  { label: "产品精修", moduleKind: "product_refine" },
  { label: "裸石设计", moduleKind: "gemstone_design" },
  { label: "高清放大", moduleKind: "upscale" },
  { label: "多图融合", moduleKind: "fusion" },
  { label: "转灰度图", moduleKind: "grayscale_relief" },
  { label: "已上传资产", moduleKind: "__uploaded__" },
] as const;
type AssetCategoryLabel = (typeof ASSET_CATEGORY_OPTIONS)[number]["label"];

interface AssetManagementPageProps {
  assetItems: AssetItem[];
  assetError?: string | null;
  currentUser: CurrentUser;
  assetPage?: number;
  assetPageSize?: number;
  assetTotal?: number;
  assetModuleKind?: string | null;
  assetKeyword?: string;
  onDeleteAsset?: (assetId: string) => Promise<void> | void;
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
  onPublishAsset?: (assetId: string) => Promise<void> | void;
  onUnpublishAsset?: (assetId: string) => Promise<void> | void;
  onUploadCommunityAsset?: (file: File, moduleKind: string) => Promise<void> | void;
  onRefresh?: () => Promise<void> | void;
  onAssetPageChange?: (page: number) => Promise<void> | void;
  onAssetPageSizeChange?: (pageSize: number) => Promise<void> | void;
  onAssetFilterChange?: (scope: string, moduleKind: string | null, keyword: string) => Promise<void> | void;
}

interface AssetPreviewState {
  title: string;
  resultUrl: string;
}

interface AssetToastState {
  type: "success" | "error";
  message: string;
}

type AssetTab = "all" | "mine" | "community";

export function AssetManagementPage({
  assetItems,
  assetError,
  currentUser,
  assetPage = 1,
  assetPageSize = 24,
  assetTotal = assetItems.length,
  assetModuleKind = null,
  assetKeyword = "",
  onDeleteAsset,
  onDeleteHistory,
  onPublishAsset,
  onUnpublishAsset,
  onUploadCommunityAsset,
  onRefresh,
  onAssetPageChange,
  onAssetPageSizeChange,
  onAssetFilterChange,
}: AssetManagementPageProps) {
  const [keyword, setKeyword] = useState(assetKeyword);
  const initialCategory = ASSET_CATEGORY_OPTIONS.find((item) => item.moduleKind === assetModuleKind)?.label ?? "全部";
  const [category, setCategory] = useState<AssetCategoryLabel>(initialCategory);
  const [activeTab, setActiveTab] = useState<AssetTab>("mine");
  const [deletingAssetId, setDeletingAssetId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [changingPage, setChangingPage] = useState(false);
  const [previewState, setPreviewState] = useState<AssetPreviewState | null>(null);
  const [communityUploadFile, setCommunityUploadFile] = useState<File | null>(null);
  const [toast, setToast] = useState<AssetToastState | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timeoutId = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timeoutId);
  }, [toast]);

  const currentScope = activeTab === "community" ? "community" : activeTab === "mine" ? "mine" : "library";

  const categories = useMemo(
    () => ASSET_CATEGORY_OPTIONS.map((item) => item.label),
    [],
  );
  const totalPages = Math.max(1, Math.ceil(assetTotal / assetPageSize));
  const pageStart = assetTotal === 0 ? 0 : (assetPage - 1) * assetPageSize + 1;
  const pageEnd = Math.min(assetTotal, assetPage * assetPageSize);

  async function handleDelete(item: AssetItem) {
    const deleteId = item.persistedAssetId ?? item.persistedHistoryId ?? null;
    if (!deleteId) return;
    setDeletingAssetId(deleteId);
    try {
      if (item.persistedAssetId && onDeleteAsset) {
        await onDeleteAsset(item.persistedAssetId);
        setToast({ type: "success", message: "资产已删除" });
        return;
      }
      if (item.persistedHistoryId && onDeleteHistory) {
        await onDeleteHistory(item.persistedHistoryId);
        setToast({ type: "success", message: "记录已删除" });
      }
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "删除失败" });
    } finally {
      setDeletingAssetId(null);
    }
  }

  function handlePreview(item: AssetItem) {
    const resultUrl = item.previewUrl ?? item.fileUrl ?? item.storageUrl ?? null;
    if (!resultUrl) return;
    setPreviewState({ title: item.name, resultUrl });
  }

  async function handlePublish(assetId: string) {
    if (!onPublishAsset) return;
    try {
      await onPublishAsset(assetId);
      setToast({ type: "success", message: "已发布到社区" });
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "发布失败" });
    }
  }

  async function handleUnpublish(assetId: string) {
    if (!onUnpublishAsset) return;
    try {
      await onUnpublishAsset(assetId);
      setToast({ type: "success", message: "已撤回到个人资产" });
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "撤回失败" });
    }
  }

  async function handleCommunityUpload() {
    if (!communityUploadFile || !onUploadCommunityAsset) return;
    try {
      await onUploadCommunityAsset(communityUploadFile, "asset_management");
      setToast({ type: "success", message: "社区资产已上传" });
      setCommunityUploadFile(null);
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "上传失败" });
    }
  }

  async function handleRefresh() {
    if (!onRefresh || refreshing) return;
    setRefreshing(true);
    try {
      await onRefresh();
      setToast({ type: "success", message: "资产列表已刷新" });
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "刷新资产失败" });
    } finally {
      setRefreshing(false);
    }
  }

  async function handleCategoryChange(nextCategory: AssetCategoryLabel) {
    setCategory(nextCategory);
    if (!onAssetFilterChange) return;
    const moduleKind = ASSET_CATEGORY_OPTIONS.find((item) => item.label === nextCategory)?.moduleKind ?? null;
    await onAssetFilterChange(currentScope, moduleKind === "__uploaded__" ? null : moduleKind, keyword);
  }

  async function handleKeywordSubmit(nextKeyword: string) {
    setKeyword(nextKeyword);
    if (!onAssetFilterChange) return;
    const moduleKind = ASSET_CATEGORY_OPTIONS.find((item) => item.label === category)?.moduleKind ?? null;
    await onAssetFilterChange(currentScope, moduleKind === "__uploaded__" ? null : moduleKind, nextKeyword);
  }

  async function handleTabChange(nextTab: AssetTab) {
    setActiveTab(nextTab);
    if (!onAssetFilterChange) return;
    const nextScope = nextTab === "community" ? "community" : nextTab === "mine" ? "mine" : "library";
    const moduleKind = ASSET_CATEGORY_OPTIONS.find((item) => item.label === category)?.moduleKind ?? null;
    await onAssetFilterChange(nextScope, moduleKind === "__uploaded__" ? null : moduleKind, keyword);
  }

  async function handlePageChange(nextPage: number) {
    if (!onAssetPageChange || changingPage) return;
    const normalizedPage = Math.min(Math.max(nextPage, 1), totalPages);
    if (normalizedPage === assetPage) return;
    setChangingPage(true);
    try {
      await onAssetPageChange(normalizedPage);
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "切换分页失败" });
    } finally {
      setChangingPage(false);
    }
  }

  async function handlePageSizeChange(nextPageSize: number) {
    if (!onAssetPageSizeChange || changingPage) return;
    setChangingPage(true);
    try {
      await onAssetPageSizeChange(nextPageSize);
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "调整分页失败" });
    } finally {
      setChangingPage(false);
    }
  }

  return (
    <div className="page-stack compact-page">
      {toast ? (
        <div className="admin-toast-layer" aria-live="polite">
          <div className={toast.type === "success" ? "admin-toast success" : "admin-toast error"}>{toast.message}</div>
        </div>
      ) : null}

      <section className="panel compact-panel asset-management-panel">
        <div className="asset-management-head">
          <div className="asset-primary-nav">
            <button
              className={activeTab === "mine" ? "asset-primary-nav-button active" : "asset-primary-nav-button"}
              type="button"
              onClick={() => void handleTabChange("mine")}
            >
              个人资产
            </button>
            <button
              className={activeTab === "community" ? "asset-primary-nav-button active" : "asset-primary-nav-button"}
              type="button"
              onClick={() => void handleTabChange("community")}
            >
              社区资产
            </button>
          </div>
          {onRefresh ? (
            <button
              className="history-icon-button refresh-icon-button asset-refresh-button"
              type="button"
              onClick={() => void handleRefresh()}
              disabled={refreshing}
              title={refreshing ? "刷新中" : "刷新"}
              aria-label={refreshing ? "刷新中" : "刷新资产列表"}
            >
              <span aria-hidden="true">{refreshing ? "…" : "↻"}</span>
            </button>
          ) : null}
        </div>
        <div className="toolbar">
          <div className="asset-subnav">
            {categories.map((item) => (
              <button className={item === category ? "filter-chip active" : "filter-chip"} type="button" key={item} onClick={() => void handleCategoryChange(item)}>
                {item}
              </button>
            ))}
          </div>
          <input className="search-input asset-search-input" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索名称、来源或标签..." />
        </div>

        {currentUser.role === "root" && activeTab === "community" && onUploadCommunityAsset ? (
          <div className="asset-upload-bar">
            <label className="asset-file-picker" htmlFor="community-asset-upload">
              <span className="asset-file-picker-button">选择文件</span>
              <span className="asset-file-picker-name">{communityUploadFile?.name ?? "未选择任何文件"}</span>
            </label>
            <input
              id="community-asset-upload"
              className="asset-file-input"
              type="file"
              accept="image/*"
              onChange={(event) => setCommunityUploadFile(event.target.files?.[0] ?? null)}
            />
            <button
              className="secondary-button compact-button asset-upload-button"
              type="button"
              disabled={!communityUploadFile}
              onClick={() => void handleCommunityUpload()}
            >
              上传社区资产
            </button>
          </div>
        ) : null}

        {assetError ? <p className="error-text">{assetError}</p> : null}

        <div className="asset-grid">
          {assetItems.map((item) => {
            const canPreview = Boolean(item.previewUrl ?? item.fileUrl ?? item.storageUrl);
            const previewImageUrl = item.previewUrl ?? item.fileUrl ?? item.storageUrl ?? null;
            return (
              <article className="asset-card static" key={item.id}>
                <button
                  className={canPreview ? "asset-thumb asset-thumb-button" : "asset-thumb"}
                  type={canPreview ? "button" : undefined}
                  onClick={canPreview ? () => handlePreview(item) : undefined}
                  title={canPreview ? "点击图片灯箱预览" : undefined}
                >
                  {previewImageUrl ? (
                    <img className="asset-thumb-image" src={previewImageUrl} alt={item.name} loading="lazy" />
                  ) : (
                    <div className="asset-thumb-fallback" style={{ background: item.preview }} aria-hidden="true" />
                  )}
                </button>

                <div className="asset-copy">
                  <strong>{item.name}</strong>
                  <span>
                    {item.category} / {item.source}
                  </span>
                  <small>{item.updatedAt}</small>
                  {item.ownerUsername ? <small>归属: {item.ownerUsername}</small> : null}
                </div>

                <div className="tag-row">
                  {item.tags.map((tag) => (
                    <span className="soft-tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>

                <div className="inline-action-row asset-card-action-row">
                  {canPreview ? (
                    <a
                      className="history-icon-button asset-action-button"
                      href={buildDownloadUrl(item.fileUrl ?? item.previewUrl ?? item.storageUrl ?? null, buildDownloadFilename(item.name, item.fileUrl ?? item.previewUrl ?? item.storageUrl ?? null)) ?? item.fileUrl ?? item.previewUrl ?? item.storageUrl ?? undefined}
                      download={buildDownloadFilename(item.name, item.fileUrl ?? item.previewUrl ?? item.storageUrl ?? null)}
                      title="下载图片"
                      aria-label="下载图片"
                    >
                      <span aria-hidden="true">↓</span>
                    </a>
                  ) : null}

                  {item.persistedAssetId && item.scope !== "community" && item.canPublish && onPublishAsset ? (
                    <button
                      className="history-icon-button asset-action-button"
                      type="button"
                      onClick={() => void handlePublish(item.persistedAssetId!)}
                      title="发布到社区"
                      aria-label="发布到社区"
                    >
                      <span aria-hidden="true">↗</span>
                    </button>
                  ) : null}

                  {item.persistedAssetId && item.canUnpublish && onUnpublishAsset ? (
                    <button
                      className="history-icon-button asset-action-button"
                      type="button"
                      onClick={() => void handleUnpublish(item.persistedAssetId!)}
                      title="撤回社区"
                      aria-label="撤回社区"
                    >
                      <span aria-hidden="true">↙</span>
                    </button>
                  ) : null}

                  {item.deletable && (item.persistedAssetId || item.persistedHistoryId) ? (
                    <button
                      className="history-icon-button asset-action-button"
                      type="button"
                      onClick={() => void handleDelete(item)}
                      disabled={deletingAssetId === (item.persistedAssetId ?? item.persistedHistoryId ?? null)}
                      title={deletingAssetId === (item.persistedAssetId ?? item.persistedHistoryId ?? null) ? "删除中" : "删除资产"}
                      aria-label={deletingAssetId === (item.persistedAssetId ?? item.persistedHistoryId ?? null) ? "删除中" : "删除资产"}
                    >
                      <span aria-hidden="true">{deletingAssetId === (item.persistedAssetId ?? item.persistedHistoryId ?? null) ? "…" : "×"}</span>
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>

        {assetItems.length === 0 ? (
          <div className="panel-subcard empty-state">
            <p className="muted">没有匹配的资产，可以调整视图、分类或搜索词。</p>
          </div>
        ) : null}

        <div className="asset-pagination" aria-label="资产分页">
          <div className="asset-pagination-summary">
            {assetTotal > 0 ? `第 ${pageStart}-${pageEnd} 条，共 ${assetTotal} 条` : "暂无资产"}
          </div>
          <div className="asset-pagination-controls">
            <button
              className="history-icon-button asset-page-button"
              type="button"
              onClick={() => void handlePageChange(assetPage - 1)}
              disabled={changingPage || assetPage <= 1}
              aria-label="上一页"
              title="上一页"
            >
              <span aria-hidden="true">‹</span>
            </button>
            <span className="asset-page-indicator">
              {assetPage} / {totalPages}
            </span>
            <button
              className="history-icon-button asset-page-button"
              type="button"
              onClick={() => void handlePageChange(assetPage + 1)}
              disabled={changingPage || assetPage >= totalPages}
              aria-label="下一页"
              title="下一页"
            >
              <span aria-hidden="true">›</span>
            </button>
            <label className="asset-page-size">
              <span>每页</span>
              <select value={assetPageSize} onChange={(event) => void handlePageSizeChange(Number(event.target.value))} disabled={changingPage}>
                {[12, 24, 48, 72].map((size) => (
                  <option value={size} key={size}>
                    {size}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      </section>

      {previewState ? <ResultPreviewModal title={previewState.title} resultUrl={previewState.resultUrl} resultLabel="资产预览" onClose={() => setPreviewState(null)} /> : null}
    </div>
  );
}
