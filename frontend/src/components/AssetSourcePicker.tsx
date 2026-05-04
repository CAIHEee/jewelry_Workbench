import { useEffect, useMemo, useRef, useState } from "react";

import type { AssetItem } from "../types/mockData";

interface AssetSourcePickerProps {
  title: string;
  assetItems: AssetItem[];
  allowMultiple?: boolean;
  helper?: string;
  includeUploadOption?: boolean;
  compactTrigger?: boolean;
  uploadLabel?: string;
  onUploadFilesChange?: (files: File[]) => void;
  onSelectedAssetsChange?: (assets: AssetItem[]) => void;
}

export function AssetSourcePicker({
  title,
  assetItems,
  allowMultiple = false,
  helper,
  includeUploadOption = true,
  compactTrigger = false,
  uploadLabel,
  onUploadFilesChange,
  onSelectedAssetsChange,
}: AssetSourcePickerProps) {
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sourceType, setSourceType] = useState<"asset" | "upload">(includeUploadOption ? "upload" : "asset");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [assetModalOpen, setAssetModalOpen] = useState(false);
  const [compactMenuOpen, setCompactMenuOpen] = useState(false);
  const [assetScope, setAssetScope] = useState<"mine" | "community">("mine");
  const uploadFilesChangeRef = useRef(onUploadFilesChange);
  const selectedAssetsChangeRef = useRef(onSelectedAssetsChange);

  useEffect(() => {
    uploadFilesChangeRef.current = onUploadFilesChange;
  }, [onUploadFilesChange]);

  useEffect(() => {
    selectedAssetsChangeRef.current = onSelectedAssetsChange;
  }, [onSelectedAssetsChange]);

  const selectedAssets = useMemo(() => assetItems.filter((item) => selectedIds.includes(item.id)), [assetItems, selectedIds]);
  const personalAssetItems = useMemo(
    () => assetItems.filter((item) => item.scope !== "community" || item.source === "我的资产" || item.source === "当前会话"),
    [assetItems],
  );
  const communityAssetItems = useMemo(
    () => assetItems.filter((item) => item.scope === "community" || item.source === "社区资产"),
    [assetItems],
  );
  const visibleAssetItems = assetScope === "mine" ? personalAssetItems : communityAssetItems;

  const uploadedPreviews = useMemo(
    () =>
      uploadedFiles.map((file, index) => ({
        key: `${file.name}-${file.size}-${file.lastModified}-${index}`,
        name: file.name,
        url: URL.createObjectURL(file),
      })),
    [uploadedFiles],
  );

  useEffect(() => {
    return () => {
      uploadedPreviews.forEach((item) => URL.revokeObjectURL(item.url));
    };
  }, [uploadedPreviews]);

  useEffect(() => {
    uploadFilesChangeRef.current?.(sourceType === "upload" ? uploadedFiles : []);
  }, [sourceType, uploadedFiles]);

  useEffect(() => {
    selectedAssetsChangeRef.current?.(sourceType === "asset" ? selectedAssets : []);
  }, [selectedAssets, sourceType]);

  useEffect(() => {
    if (!assetModalOpen) {
      return;
    }

    const htmlElement = document.documentElement;
    const previousOverflow = document.body.style.overflow;
    const previousHtmlOverflow = htmlElement.style.overflow;
    const previousTouchAction = document.body.style.touchAction;
    const previousHtmlTouchAction = htmlElement.style.touchAction;

    htmlElement.classList.add("asset-modal-open");
    document.body.classList.add("asset-modal-open");
    htmlElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    htmlElement.style.touchAction = "none";
    document.body.style.touchAction = "none";

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setAssetModalOpen(false);
      }
    }

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      htmlElement.classList.remove("asset-modal-open");
      document.body.classList.remove("asset-modal-open");
      htmlElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousOverflow;
      htmlElement.style.touchAction = previousHtmlTouchAction;
      document.body.style.touchAction = previousTouchAction;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [assetModalOpen]);

  function toggleAsset(assetId: string) {
    if (!allowMultiple) {
      setSelectedIds([assetId]);
      return;
    }

    setSelectedIds((current) => (current.includes(assetId) ? current.filter((id) => id !== assetId) : [...current, assetId]));
  }

  function removeSelectedAsset(assetId: string) {
    setSelectedIds((current) => current.filter((id) => id !== assetId));
  }

  function removeUploadedFile(indexToRemove: number) {
    setUploadedFiles((current) => current.filter((_, index) => index !== indexToRemove));
  }

  function handleUploadChange(event: React.ChangeEvent<HTMLInputElement>) {
    const nextFiles = Array.from(event.target.files ?? []);
    if (nextFiles.length === 0) {
      return;
    }

    const validMimeTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
    const invalidFile = nextFiles.find((file) => file.type && !validMimeTypes.has(file.type));
    if (invalidFile) {
      setUploadError(`文件 ${invalidFile.name} 格式不支持，请上传 PNG、JPG 或 WEBP 图片。`);
      event.target.value = "";
      return;
    }

    const oversizedFile = nextFiles.find((file) => file.size > 20 * 1024 * 1024);
    if (oversizedFile) {
      setUploadError(`文件 ${oversizedFile.name} 超过 20MB，请压缩后再上传。`);
      event.target.value = "";
      return;
    }

    const dedupedFiles = nextFiles.filter(
      (file, index, files) => files.findIndex((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified) === index,
    );
    if (dedupedFiles.length === 0) {
      setUploadError("没有可用的新图片可上传。");
      event.target.value = "";
      return;
    }

    setUploadError(null);

    setUploadedFiles((current) => {
      if (!allowMultiple) {
        return dedupedFiles.slice(0, 1);
      }

      const merged = [...current];
      dedupedFiles.forEach((file) => {
        const duplicated = merged.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
        if (!duplicated) {
          merged.push(file);
        }
      });
      if (merged.length > 6) {
        setUploadError("最多上传 6 张图片，请先删除部分图片后再继续。");
        return merged.slice(0, 6);
      }
      return merged;
    });

    event.target.value = "";
  }

  const sourceSummary =
    sourceType === "upload"
      ? uploadedFiles.length > 0
        ? `已上传 ${uploadedFiles.length} 张`
        : allowMultiple
          ? "可上传多张"
          : "可上传 1 张"
      : selectedAssets.length > 0
        ? `已选择 ${selectedAssets.length} 张`
      : "未选择";

  if (compactTrigger) {
    return (
      <div className="source-picker-compact">
        <button
          className="source-picker-compact-trigger"
          type="button"
          onClick={() => setCompactMenuOpen((value) => !value)}
          aria-label={title}
          title={title}
        >
          +
        </button>
        {uploadedFiles.length || selectedAssets.length ? (
          <div className="source-picker-compact-preview">
            {uploadedPreviews.map((item, index) => (
              <span key={item.key}>
                <img src={item.url} alt={item.name} />
                <button type="button" aria-label={`删除 ${item.name}`} onClick={() => removeUploadedFile(index)}>×</button>
              </span>
            ))}
            {selectedAssets.map((item) => (
              <span key={item.id}>
                {item.previewUrl ? <img src={item.previewUrl} alt={item.name} /> : <i style={{ background: item.preview }} />}
                <button type="button" aria-label={`删除 ${item.name}`} onClick={() => removeSelectedAsset(item.id)}>×</button>
              </span>
            ))}
          </div>
        ) : null}

        {compactMenuOpen ? (
          <div className="source-picker-compact-menu">
            <p>图片</p>
            <label className="source-picker-compact-item">
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple={allowMultiple}
                onChange={(event) => {
                  setSourceType("upload");
                  handleUploadChange(event);
                  setCompactMenuOpen(false);
                }}
              />
              <span className="source-picker-compact-upload-icon" aria-hidden="true" />
              上传图片
            </label>
            <button className="source-picker-compact-item" type="button" onClick={() => {
              setSourceType("asset");
              setAssetModalOpen(true);
              setCompactMenuOpen(false);
            }}>
              <span className="source-picker-compact-assets-icon" aria-hidden="true" />
              资产库
            </button>
          </div>
        ) : null}

        {assetModalOpen ? (
          <div className="asset-modal-backdrop" role="presentation" onClick={() => setAssetModalOpen(false)}>
            <div className="asset-modal-card" role="dialog" aria-modal="true" aria-label="资产图片选择" onClick={(event) => event.stopPropagation()}>
              <div className="asset-modal-header">
                <div className="stack-list compact-stack">
                  <h3>资产图片选择</h3>
                  {helper ? <p className="muted">{helper}</p> : null}
                </div>
                <button className="template-close-button" type="button" onClick={() => setAssetModalOpen(false)} aria-label="关闭资产窗口">
                  ×
                </button>
              </div>

              <div className="asset-modal-toolbar">
                <div className="asset-modal-scope-nav" role="tablist" aria-label="资产范围选择">
                  <button className={assetScope === "mine" ? "asset-modal-scope-button active" : "asset-modal-scope-button"} type="button" onClick={() => setAssetScope("mine")}>
                    个人资产
                    <span>{personalAssetItems.length}</span>
                  </button>
                  <button className={assetScope === "community" ? "asset-modal-scope-button active" : "asset-modal-scope-button"} type="button" onClick={() => setAssetScope("community")}>
                    社区资产
                    <span>{communityAssetItems.length}</span>
                  </button>
                </div>
                <div className="hint-box template-hint-box">{allowMultiple ? "当前支持多选" : "当前支持单选"}</div>
              </div>

              <div className="asset-modal-body">
                {visibleAssetItems.length > 0 ? (
                  <div className="asset-grid asset-library-grid">
                    {visibleAssetItems.map((item) => {
                      const selected = selectedIds.includes(item.id);
                      const assetPreviewUrl = item.previewUrl ?? item.storageUrl ?? null;
                      return (
                        <button key={item.id} type="button" className={selected ? "asset-card selected" : "asset-card"} onClick={() => toggleAsset(item.id)}>
                          <div className="asset-thumb">
                            {assetPreviewUrl ? (
                              <img className="asset-thumb-image" src={assetPreviewUrl} alt={item.name} />
                            ) : (
                              <div className="asset-thumb-fallback" style={{ background: item.preview }} />
                            )}
                          </div>
                          <div className="asset-copy">
                            <strong>{item.name}</strong>
                            <span>{item.category} / {item.source}</span>
                            <small>{item.updatedAt}</small>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className="panel-subcard empty-state asset-modal-empty-state">
                    <p className="muted">{assetScope === "mine" ? "当前没有可选的个人资产。" : "当前没有可选的社区资产。"}</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="source-picker-card">
      <div className="source-picker-header">
        <div className="source-picker-copy">
          <h4>{title}</h4>
          {helper ? <p className="muted">{helper}</p> : null}
        </div>
        <div className="source-picker-summary">
          <span className="status-pill idle">{sourceType === "upload" ? "本地上传" : "资产图片"}</span>
          <small>{sourceSummary}</small>
        </div>
      </div>

      {includeUploadOption ? (
        <>
          <div className="source-segmented">
            <button
              type="button"
              className={sourceType === "upload" ? "source-mode-button active" : "source-mode-button"}
              onClick={() => setSourceType("upload")}
            >
              <strong>本地上传</strong>
              <span>{allowMultiple ? "上传多张图片" : "上传单张图片"}</span>
            </button>
            <button
              type="button"
              className={sourceType === "asset" ? "source-mode-button active" : "source-mode-button"}
              onClick={() => setSourceType("asset")}
            >
              <strong>基于资产</strong>
              <span>从资产库选择</span>
            </button>
          </div>
          <div className="source-asset-helper">
            <p>基于资产可直接选择历史生成的结果图，或已上传到过的图片。</p>
            <p>以上可以避免多次上传相同的图片，导致资产库出现大量相同图片。</p>
          </div>
        </>
      ) : null}

      {sourceType === "upload" ? (
        <div className="source-panel-body">
          <label className="upload-dropzone compact">
            <input type="file" accept="image/png,image/jpeg,image/webp" multiple={allowMultiple} onChange={handleUploadChange} />
            <strong>{uploadLabel ?? (allowMultiple ? "上传多张参考图" : "上传单张参考图")}</strong>
          </label>
          {uploadError ? <p className="error-text">{uploadError}</p> : null}

          {uploadedFiles.length > 0 ? (
            <details className="drawer-panel inner-drawer source-upload-drawer" open>
              <summary className="drawer-summary">
                <div>
                  <h4>已上传图片</h4>
                </div>
                <span className="drawer-hint">展开 / 收起</span>
              </summary>
              <div className="drawer-content">
                <div className="source-upload-scroll-panel">
                  <div className="source-preview-grid source-preview-grid-full">
                    {uploadedPreviews.map((item, index) => (
                      <article className="source-preview-card source-preview-card-full removable-preview-card" key={item.key}>
                        <button
                          type="button"
                          className="preview-remove-button"
                          aria-label={`删除 ${item.name}`}
                          title="删除"
                          onClick={() => removeUploadedFile(index)}
                        >
                          ×
                        </button>
                        <div className="source-preview-image-frame">
                          <img src={item.url} alt={item.name} />
                        </div>
                        <p title={item.name}>{item.name}</p>
                      </article>
                    ))}
                  </div>
                </div>
              </div>
            </details>
          ) : null}
        </div>
      ) : (
        <div className="asset-picker-inline-card">
          <div className="asset-picker-inline-copy">
            <h4>资产来源</h4>
          </div>
          <button className="secondary-button compact-button" type="button" onClick={() => setAssetModalOpen(true)}>
            打开资产列表
          </button>

          {selectedAssets.length > 0 ? (
            <div className="panel-subcard compact">
              <div className="source-preview-grid">
                {selectedAssets.map((item) => (
                  <article className="source-preview-card asset removable-preview-card" key={item.id}>
                    <button
                      type="button"
                      className="preview-remove-button"
                      aria-label={`删除 ${item.name}`}
                      title="删除"
                      onClick={() => removeSelectedAsset(item.id)}
                    >
                      ×
                    </button>
                    <div className="source-preview-art" style={{ background: item.preview }} />
                    <p>{item.name}</p>
                  </article>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}

      {assetModalOpen ? (
        <div className="asset-modal-backdrop" role="presentation" onClick={() => setAssetModalOpen(false)}>
          <div className="asset-modal-card" role="dialog" aria-modal="true" aria-label="资产图片选择" onClick={(event) => event.stopPropagation()}>
            <div className="asset-modal-header">
              <div className="stack-list compact-stack">
                <h3>资产图片选择</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setAssetModalOpen(false)} aria-label="关闭资产窗口">
                ×
              </button>
            </div>

            <div className="asset-modal-toolbar">
              <div className="asset-modal-scope-nav" role="tablist" aria-label="资产范围选择">
                <button
                  className={assetScope === "mine" ? "asset-modal-scope-button active" : "asset-modal-scope-button"}
                  type="button"
                  onClick={() => setAssetScope("mine")}
                >
                  个人资产
                  <span>{personalAssetItems.length}</span>
                </button>
                <button
                  className={assetScope === "community" ? "asset-modal-scope-button active" : "asset-modal-scope-button"}
                  type="button"
                  onClick={() => setAssetScope("community")}
                >
                  社区资产
                  <span>{communityAssetItems.length}</span>
                </button>
              </div>
              <div className="hint-box template-hint-box">{allowMultiple ? "当前支持多选" : "当前支持单选"}</div>
            </div>

            <div className="asset-modal-body">
              {visibleAssetItems.length > 0 ? (
                <div className="asset-grid asset-library-grid">
                  {visibleAssetItems.map((item) => {
                  const selected = selectedIds.includes(item.id);
                  const assetPreviewUrl = item.previewUrl ?? item.storageUrl ?? null;
                  return (
                    <button key={item.id} type="button" className={selected ? "asset-card selected" : "asset-card"} onClick={() => toggleAsset(item.id)}>
                      <div className="asset-thumb">
                        {assetPreviewUrl ? (
                          <img className="asset-thumb-image" src={assetPreviewUrl} alt={item.name} />
                        ) : (
                          <div className="asset-thumb-fallback" style={{ background: item.preview }} />
                        )}
                      </div>
                      <div className="asset-copy">
                        <strong>{item.name}</strong>
                        <span>
                          {item.category} / {item.source}
                        </span>
                        <small>{item.updatedAt}</small>
                      </div>
                    </button>
                  );
                  })}
                </div>
              ) : (
                <div className="panel-subcard empty-state asset-modal-empty-state">
                  <p className="muted">{assetScope === "mine" ? "当前没有可选的个人资产。" : "当前没有可选的社区资产。"}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
