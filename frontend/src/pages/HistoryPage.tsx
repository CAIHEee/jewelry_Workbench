import { useMemo, useState } from "react";

import { FloatingToast } from "../components/FloatingToast";
import { ResultPreviewModal } from "../components/ResultPreviewModal";
import type { PersistedHistoryItem } from "../types/history";
import type { WorkspaceRun } from "../types/workspace";
import { appendSecondSuffixToName, buildDownloadFilename, buildDownloadUrl } from "../utils/download";
import { formatHistoryTimestamp, getHistoryKindLabel, mergeModuleHistory, type ModuleHistoryEntry } from "../utils/history";

interface HistoryPageProps {
  workspaceRuns: WorkspaceRun[];
  persistedItems: PersistedHistoryItem[];
  persistedError: string | null;
  onRefresh?: () => Promise<void> | void;
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

interface PreviewState {
  title: string;
  sourceUrl?: string | null;
  resultUrl?: string | null;
  sourceLabel?: string;
  resultLabel?: string;
}

const filterTags = ["全部", "文生图", "多图融合", "线稿转写实图", "产品精修", "裸石设计", "高清放大", "生成多视图", "多视图切图", "转灰度图"];

export function HistoryPage({ workspaceRuns, persistedItems, persistedError, onRefresh, onDeleteHistory }: HistoryPageProps) {
  const [keyword, setKeyword] = useState("");
  const [activeFilter, setActiveFilter] = useState("全部");
  const [previewState, setPreviewState] = useState<PreviewState | null>(null);
  const [pendingDeleteItem, setPendingDeleteItem] = useState<ModuleHistoryEntry | null>(null);
  const [deletingHistoryId, setDeletingHistoryId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedPromptIds, setExpandedPromptIds] = useState<string[]>([]);
  const [collapsedHistoryIds, setCollapsedHistoryIds] = useState<string[]>([]);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const normalizedKeyword = keyword.trim().toLowerCase();

  const operationItems = useMemo(
    () =>
      [
        ...mergeModuleHistory(persistedItems, workspaceRuns, "text_to_image"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "fusion"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "sketch_to_realistic"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "product_refine"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "gemstone_design"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "upscale"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "multi_view"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "multi_view_split"),
        ...mergeModuleHistory(persistedItems, workspaceRuns, "grayscale_relief"),
      ]
        .filter((item) => {
          const moduleLabel = getHistoryKindLabel(item.kind);
          const filterMatched = activeFilter === "全部" || moduleLabel === activeFilter;
          const searchable = `${item.title} ${item.model} ${item.provider} ${item.prompt}`.toLowerCase();
          return filterMatched && (!normalizedKeyword || searchable.includes(normalizedKeyword));
        })
        .sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt)),
    [activeFilter, normalizedKeyword, persistedItems, workspaceRuns],
  );

  function openOperationPreview(item: ModuleHistoryEntry) {
    setPreviewState({
      title: item.title,
      sourceUrl: item.sourceImages[item.primaryImageIndex ?? 0] ?? item.sourceImageUrl ?? item.sourceImages[0] ?? null,
      resultUrl: item.imageUrl,
      sourceLabel: "原始图",
      resultLabel: "结果图",
    });
  }

  async function confirmDeleteHistory() {
    const item = pendingDeleteItem;
    if (!item || item.source !== "persisted" || !item.persistedId || !onDeleteHistory) {
      return;
    }

    setDeletingHistoryId(item.persistedId);
    try {
      await onDeleteHistory(item.persistedId);
      setPendingDeleteItem(null);
      setToast({ type: "success", message: "历史记录已删除" });
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "删除历史记录失败" });
    } finally {
      setDeletingHistoryId(null);
    }
  }

  async function handleRefresh() {
    if (!onRefresh || refreshing) return;
    setRefreshing(true);
    try {
      await onRefresh();
      setToast({ type: "success", message: "历史记录已刷新" });
    } catch (error) {
      setToast({ type: "error", message: error instanceof Error ? error.message : "刷新历史记录失败" });
    } finally {
      setRefreshing(false);
    }
  }

  function resolveHistoryDownloadName(item: ModuleHistoryEntry): string {
    return appendSecondSuffixToName(item.title, item.createdAt);
  }

  function togglePrompt(itemId: string) {
    setExpandedPromptIds((current) => (current.includes(itemId) ? current.filter((id) => id !== itemId) : [...current, itemId]));
  }

  function toggleHistory(itemId: string) {
    setCollapsedHistoryIds((current) => (current.includes(itemId) ? current.filter((id) => id !== itemId) : [...current, itemId]));
  }

  return (
    <div className="page-stack compact-page">
      <section className="panel compact-panel">
        <div className="history-toolbar">
          <div className="toolbar">
            <div className="tag-row">
              {filterTags.map((tag) => (
                <button
                  className={tag === activeFilter ? "filter-chip active" : "filter-chip"}
                  type="button"
                  key={tag}
                  onClick={() => setActiveFilter(tag)}
                >
                  {tag}
                </button>
              ))}
            </div>
            <input
              className="search-input"
              placeholder="按任务名、模型或提示词搜索..."
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
          </div>
        </div>
      </section>

      <section className="panel compact-panel">
        <div className="history-section-head">
          <div>
            <h3>全部操作记录</h3>
            <p className="section-description">当前会话和持久化记录已自动合并，并按时间倒序展示。</p>
          </div>
          <div className="history-head-actions">
            <span className="status-pill online">{operationItems.length} 条</span>
            {onRefresh ? (
              <button className="secondary-button compact-button" type="button" onClick={() => void handleRefresh()} disabled={refreshing}>
                {refreshing ? "刷新中..." : "刷新"}
              </button>
            ) : null}
          </div>
        </div>

        {persistedError ? <p className="error-text">{persistedError}</p> : null}

        {operationItems.length > 0 ? (
          <div className="session-run-list">
            {operationItems.map((item) => {
              const historyCollapsed = collapsedHistoryIds.includes(item.id);
              return (
              <article className="drawer-panel history-drawer unified-history-drawer" key={item.id}>
                <div className="drawer-summary history-record-summary">
                  <div className="session-run-copy history-entry-copy">
                    <div className="history-inline-head">
                      <h4>{item.title}</h4>
                      <span className="history-module-pill">{getHistoryKindLabel(item.kind)}</span>
                    </div>
                    <p>
                      {item.provider} / {item.model} / {formatHistoryTimestamp(item.createdAt)}
                    </p>
                    {"ownerUsername" in item && item.ownerUsername ? <p>归属: {item.ownerUsername}</p> : null}
                  </div>
                  <button
                    className="history-record-toggle"
                    type="button"
                    onClick={() => toggleHistory(item.id)}
                    aria-expanded={!historyCollapsed}
                    aria-label={historyCollapsed ? "展开历史记录" : "收起历史记录"}
                    title={historyCollapsed ? "展开" : "收起"}
                  >
                    <span aria-hidden="true">{historyCollapsed ? "⌄" : "⌃"}</span>
                  </button>
                </div>
                {!historyCollapsed ? <div className="drawer-content">
                  {item.prompt ? (
                    <div className={expandedPromptIds.includes(item.id) ? "history-prompt-bar expanded" : "history-prompt-bar"}>
                      <p className="history-prompt-text">{item.prompt}</p>
                      <button className="history-prompt-toggle" type="button" onClick={() => togglePrompt(item.id)}>
                        {expandedPromptIds.includes(item.id) ? "收起" : "展开"}
                      </button>
                    </div>
                  ) : null}
                  {item.imageUrl ? (
                    <>
                      <button
                        className="generated-result-card history-result-card history-preview-frame compact-history-preview history-preview-button"
                        type="button"
                        onClick={() => openOperationPreview(item)}
                      >
                        <img className="generated-image image-fit-contain" src={item.imageUrl} alt={item.title} />
                      </button>
                      <div className="page-history-card-actions history-page-actions">
                        <a
                          className="history-icon-button"
                          href={buildDownloadUrl(item.imageUrl, buildDownloadFilename(resolveHistoryDownloadName(item), item.imageUrl)) ?? item.imageUrl}
                          download={buildDownloadFilename(resolveHistoryDownloadName(item), item.imageUrl)}
                          title="下载图片"
                          aria-label="下载图片"
                        >
                          <span aria-hidden="true">↓</span>
                        </a>
                        {item.source === "persisted" && item.persistedId ? (
                          <button
                            className="history-icon-button"
                            type="button"
                            onClick={() => setPendingDeleteItem(item)}
                            disabled={deletingHistoryId === item.persistedId}
                            title={deletingHistoryId === item.persistedId ? "删除中" : "删除记录"}
                            aria-label={deletingHistoryId === item.persistedId ? "删除中" : "删除记录"}
                          >
                            <span aria-hidden="true">{deletingHistoryId === item.persistedId ? "…" : "×"}</span>
                          </button>
                        ) : null}
                      </div>
                    </>
                  ) : (
                    <div className="inline-action-row history-page-empty-actions">
                      <span className="status-pill idle">{item.status}</span>
                      {item.source === "persisted" && item.persistedId ? (
                        <button
                          className="history-icon-button"
                          type="button"
                          onClick={() => setPendingDeleteItem(item)}
                          disabled={deletingHistoryId === item.persistedId}
                          title={deletingHistoryId === item.persistedId ? "删除中" : "删除记录"}
                          aria-label={deletingHistoryId === item.persistedId ? "删除中" : "删除记录"}
                        >
                          <span aria-hidden="true">{deletingHistoryId === item.persistedId ? "…" : "×"}</span>
                        </button>
                      ) : null}
                    </div>
                  )}
                </div> : null}
              </article>
              );
            })}
          </div>
        ) : (
          <div className="panel-subcard">
            <p className="muted">当前筛选条件下没有操作记录。</p>
          </div>
        )}
      </section>

      {previewState ? (
        <ResultPreviewModal
          title={previewState.title}
          sourceUrl={previewState.sourceUrl}
          sourceLabel={previewState.sourceLabel}
          resultUrl={previewState.resultUrl}
          resultLabel={previewState.resultLabel}
          onClose={() => setPreviewState(null)}
        />
      ) : null}

      {pendingDeleteItem ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setPendingDeleteItem(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="删除历史记录确认" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">历史记录</p>
                <h3>删除历史记录</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setPendingDeleteItem(null)} aria-label="关闭删除历史记录弹窗">
                x
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="muted">确定删除这条历史记录吗？该操作只删除历史记录，不会删除资产管理中的资产图片。</p>
              <div className="panel-subcard compact">
                <strong>{pendingDeleteItem.title}</strong>
                <p className="muted">
                  {getHistoryKindLabel(pendingDeleteItem.kind)} / {formatHistoryTimestamp(pendingDeleteItem.createdAt)}
                </p>
              </div>
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setPendingDeleteItem(null)} disabled={Boolean(deletingHistoryId)}>
                取消
              </button>
              <button
                className="primary-button compact-button"
                type="button"
                onClick={() => void confirmDeleteHistory()}
                disabled={deletingHistoryId === pendingDeleteItem.persistedId}
              >
                {deletingHistoryId === pendingDeleteItem.persistedId ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <FloatingToast message={toast?.message ?? null} type={toast?.type ?? "error"} />
    </div>
  );
}
