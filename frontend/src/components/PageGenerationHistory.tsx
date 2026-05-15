import { useLayoutEffect, useMemo, useRef, useState } from "react";

import { FloatingToast } from "./FloatingToast";
import { useViewport } from "../hooks/useViewport";
import { appendSecondSuffixToName, buildDownloadFilename, buildDownloadUrl } from "../utils/download";
import { formatHistoryTimestamp } from "../utils/history";
import type { ModuleHistoryEntry } from "../utils/history";

interface PageGenerationHistoryProps {
  title: string;
  items: ModuleHistoryEntry[];
  activeId?: string | null;
  onPreview?: (item: ModuleHistoryEntry) => void;
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

export function PageGenerationHistory({ title: _title, items, activeId, onPreview, onDeleteHistory }: PageGenerationHistoryProps) {
  const { isMobile } = useViewport();
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [pendingDeleteItem, setPendingDeleteItem] = useState<ModuleHistoryEntry | null>(null);
  const [deletingHistoryId, setDeletingHistoryId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const sidebarTitle = "历史记录";
  const itemsKey = useMemo(() => items.map((item) => item.id).join("|"), [items]);

  useLayoutEffect(() => {
    if (activeId || collapsed || isMobile) return;
    bodyRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [activeId, collapsed, isMobile, itemsKey]);

  function resolveDisplayTitle(item: ModuleHistoryEntry): string {
    return appendSecondSuffixToName(item.title, item.createdAt);
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

  return (
    <aside className={collapsed && !isMobile ? "page-history-sidebar collapsed" : "page-history-sidebar"}>
      <div className="page-history-sidebar-header">
        {!collapsed || isMobile ? (
          <div className="stack-list compact-stack">
            <h4>{sidebarTitle}</h4>
          </div>
        ) : (
          <span className="page-history-sidebar-mini-title">历史</span>
        )}

        {!isMobile ? (
          <button
            className={collapsed ? "page-history-toggle-button collapsed" : "page-history-toggle-button"}
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? "展开历史记录侧栏" : "收起历史记录侧栏"}
            title={collapsed ? "展开历史记录" : "收起历史记录"}
          >
            <span className="page-history-toggle-icon" aria-hidden="true">
              {collapsed ? ">" : "<"}
            </span>
            {!collapsed ? <span>收起</span> : null}
          </button>
        ) : null}
      </div>

      <div ref={bodyRef} className={items.length === 0 ? "page-history-sidebar-body page-history-sidebar-body-empty" : "page-history-sidebar-body"}>
        {items.length > 0 ? (
          <div className="page-history-sidebar-list">
            {items.map((item) => (
              <article
                className={item.id === activeId ? "page-history-card history-entry-card active" : "page-history-card history-entry-card"}
                key={item.id}
              >
                <button
                  className="page-history-card-button"
                  type="button"
                  onClick={() => onPreview?.(item)}
                  title={collapsed ? resolveDisplayTitle(item) : undefined}
                >
                  {item.imageUrl ? (
                    <div className="page-history-thumb history-preview-frame">
                      <img className="generated-image image-fit-contain" src={item.imageUrl} alt={resolveDisplayTitle(item)} />
                    </div>
                  ) : (
                    <span className="status-pill idle">{item.status}</span>
                  )}

                  {!collapsed ? (
                    <>
                      <div className="history-inline-head history-entry-head">
                        <h4>{resolveDisplayTitle(item)}</h4>
                      </div>
                      <div className="history-meta-row">
                        <span className="history-time-pill">{formatHistoryTimestamp(item.createdAt)}</span>
                        <p className="muted">{item.model} / {item.provider}</p>
                      </div>
                    </>
                  ) : null}
                </button>

                {(!collapsed || isMobile) && onDeleteHistory && item.source === "persisted" && item.persistedId ? (
                  <div className="page-history-card-actions">
                    {item.imageUrl ? (
                      <a
                        className="history-icon-button"
                        href={buildDownloadUrl(item.imageUrl, buildDownloadFilename(resolveDisplayTitle(item), item.imageUrl)) ?? item.imageUrl}
                        download={buildDownloadFilename(resolveDisplayTitle(item), item.imageUrl)}
                        title="下载图片"
                        aria-label="下载图片"
                      >
                        <span aria-hidden="true">↓</span>
                      </a>
                    ) : null}
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
                  </div>
                ) : (!collapsed || isMobile) && item.imageUrl ? (
                  <div className="page-history-card-actions">
                    <a
                      className="history-icon-button"
                      href={buildDownloadUrl(item.imageUrl, buildDownloadFilename(resolveDisplayTitle(item), item.imageUrl)) ?? item.imageUrl}
                      download={buildDownloadFilename(resolveDisplayTitle(item), item.imageUrl)}
                      title="下载图片"
                      aria-label="下载图片"
                    >
                      <span aria-hidden="true">↓</span>
                    </a>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <div className={collapsed ? "panel-subcard compact page-history-empty-card collapsed" : "panel-subcard compact page-history-empty-card"}>
            <p className="muted">{collapsed ? "暂无" : "当前页面还没有生成记录。"}</p>
          </div>
        )}
      </div>

      {pendingDeleteItem ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setPendingDeleteItem(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="删除历史记录确认" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">模块历史</p>
                <h3>删除历史记录</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setPendingDeleteItem(null)} aria-label="关闭删除历史记录弹窗">
                x
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="muted">确定删除这条历史记录吗？该操作只删除历史记录，不会删除资产管理中的资产图片。</p>
              <div className="panel-subcard compact">
                <strong>{resolveDisplayTitle(pendingDeleteItem)}</strong>
                <p className="muted">{formatHistoryTimestamp(pendingDeleteItem.createdAt)}</p>
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
    </aside>
  );
}
