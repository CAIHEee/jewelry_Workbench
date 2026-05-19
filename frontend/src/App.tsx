import { useEffect, useMemo, useState } from "react";

import { AppHeader } from "./components/AppHeader";
import { Sidebar } from "./components/Sidebar";
import { useViewport } from "./hooks/useViewport";
import { AdminPage } from "./pages/AdminPage";
import { AgentPage } from "./pages/AgentPage";
import { AssetManagementPage } from "./pages/AssetManagementPage";
import { FusionStudio } from "./pages/FusionStudio";
import { GemstoneDesignPage } from "./pages/GemstoneDesignPage";
import { GrayscaleReliefPage } from "./pages/GrayscaleReliefPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ImageEditPage } from "./pages/ImageEditPage";
import { LoginPage } from "./pages/LoginPage";
import { MultiViewPage } from "./pages/MultiViewPage";
import { MultiViewSplitPage } from "./pages/MultiViewSplitPage";
import { ProductRefinePage } from "./pages/ProductRefinePage";
import { TextToImagePage } from "./pages/TextToImagePage";
import { UpscaleEnhancePage } from "./pages/UpscaleEnhancePage";
import {
  buildAssetContentUrl,
  createAdminUser,
  deleteAdminUser,
  deletePersistedAsset,
  deletePersistedHistory,
  fetchAdminSystemStatus,
  fetchAdminUsers,
  fetchCurrentUser,
  fetchPersistedAssets,
  fetchPersistedHistory,
  login,
  logout,
  publishPersistedAsset,
  resetAdminUserPassword,
  registerUser,
  unpublishPersistedAsset,
  updateAdminUser,
  updateAdminUserPermissions,
  uploadInputAsset,
} from "./services/api";
import type { AdminSystemStatus, AdminUser } from "./types/admin";
import type { CurrentUser } from "./types/auth";
import type { PersistedAssetItem } from "./types/assets";
import type { PersistedHistoryItem } from "./types/history";
import type { AssetItem } from "./types/mockData";
import type { WorkspaceRun } from "./types/workspace";
import { appendSecondSuffixToName, buildUploadedAssetName } from "./utils/download";
import {
  dedupePersistedHistoryItems,
  formatHistoryTimestamp,
  getHistoryKindLabel,
  isPersistedHistoryDuplicateOfRun,
  mergeModuleHistory,
  normalizeHistoryKind,
} from "./utils/history";

export type AppView =
  | "text-to-image"
  | "ai-agent"
  | "multi-view"
  | "multi-view-split"
  | "image-edit"
  | "product-refine"
  | "gemstone-design"
  | "upscale"
  | "fusion"
  | "history"
  | "grayscale-relief"
  | "asset-management"
  | "admin";

const WORKSPACE_RUNS_STORAGE_KEY_PREFIX = "flux_workspace_runs_v1";
const MAX_STORED_WORKSPACE_RUNS = 60;

const viewModuleMap: Record<AppView, string | null> = {
  "ai-agent": "ai_agent",
  "text-to-image": "text_to_image",
  "multi-view": "multi_view",
  "multi-view-split": "multi_view_split",
  "image-edit": "image_edit",
  "product-refine": "product_refine",
  "gemstone-design": "gemstone_design",
  upscale: "upscale",
  fusion: "multi_image_fusion",
  history: "history",
  "grayscale-relief": "grayscale_relief",
  "asset-management": "asset_management",
  admin: null,
};

function mapKindToAssetCategory(kind: string) {
  const normalizedKind = normalizeHistoryKind(kind);
  return normalizedKind ? getHistoryKindLabel(normalizedKind) : kind;
}

function buildPreviewBackground(url: string | null | undefined) {
  if (!url) return "linear-gradient(135deg, #2b3341 0%, #68758a 100%)";
  return `center / cover no-repeat url("${url}")`;
}

function isMultiViewSplitKind(kind: string | null | undefined) {
  return normalizeHistoryKind(kind ?? "") === "multi_view_split";
}

function parseDisplayTimestamp(value: string): number {
  const timestamp = Date.parse(value);
  if (Number.isFinite(timestamp)) return timestamp;
  const normalized = value.trim().replace(/\//g, "-");
  const fallback = Date.parse(normalized);
  return Number.isFinite(fallback) ? fallback : 0;
}

function isBlobUrl(value: string | null | undefined): value is string {
  return typeof value === "string" && value.startsWith("blob:");
}

function isExpiringSignedUrl(value: string | null | undefined): boolean {
  return typeof value === "string" && /[?&](OSSAccessKeyId|Signature|Expires)=/i.test(value);
}

function normalizeComparableUrl(value: string | null | undefined): string {
  if (!value) return "";
  if (value.startsWith("oss://") || value.startsWith("local://")) return value;
  try {
    const url = new URL(value, "http://placeholder.local");
    if (url.pathname.endsWith("/api/v1/assets/content")) {
      const storageUrl = url.searchParams.get("storage_url");
      if (storageUrl) return `${url.origin}${url.pathname}?storage_url=${storageUrl}`;
    }
    return `${url.origin}${url.pathname}`;
  } catch {
    return value.split("?")[0]?.split("#")[0] ?? value;
  }
}

function extractStoredFilename(storageUrl: string | null | undefined): string | null {
  if (!storageUrl) return null;
  const normalized = storageUrl.startsWith("oss://") || storageUrl.startsWith("local://") ? storageUrl.split("://")[1] ?? storageUrl : storageUrl;
  const pathname = normalized.split("?")[0]?.split("#")[0] ?? normalized;
  const parts = pathname.split("/").filter(Boolean);
  const filename = parts.length ? parts[parts.length - 1].trim() : "";
  return filename || null;
}

function containsChinese(value: string): boolean {
  return /[\u4e00-\u9fff]/.test(value);
}

function looksGeneratedFilename(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return /^[0-9a-f-]{16,}\.[a-z0-9]+$/i.test(normalized) || normalized.startsWith("generated_");
}

function scoreAssetName(value: string): number {
  if (!value.trim()) return 0;
  if (containsChinese(value)) return 3;
  if (looksGeneratedFilename(value)) return 1;
  return 2;
}

function choosePreferredAssetName(primary: string | null | undefined, fallback: string | null | undefined): string {
  const primaryValue = primary?.trim() ?? "";
  const fallbackValue = fallback?.trim() ?? "";
  if (!primaryValue) return fallbackValue;
  if (!fallbackValue) return primaryValue;
  return scoreAssetName(primaryValue) >= scoreAssetName(fallbackValue) ? primaryValue : fallbackValue;
}

function choosePreferredAssetItem(left: AssetItem, right: AssetItem): AssetItem {
  const leftScore = scoreAssetName(left.name);
  const rightScore = scoreAssetName(right.name);
  if (rightScore > leftScore) return right;
  if (leftScore > rightScore) return left;

  if (containsChinese(left.name) && containsChinese(right.name)) {
    const leftLength = left.name.trim().length;
    const rightLength = right.name.trim().length;
    if (leftLength !== rightLength) {
      return rightLength < leftLength ? right : left;
    }
  }

  if (!left.persistedAssetId && right.persistedAssetId) return right;
  if (left.persistedAssetId && !right.persistedAssetId) return left;

  if (left.source === "当前会话" && right.source !== "当前会话") return left;
  if (left.source !== "当前会话" && right.source === "当前会话") return right;

  return right;
}

function dedupeAssetItems(items: AssetItem[]) {
  const deduped = new Map<string, AssetItem>();
  for (const item of items) {
    const comparableUrl = normalizeComparableUrl(item.previewUrl ?? item.storageUrl ?? item.fileUrl ?? null);
    const dedupeKey = comparableUrl || `${item.name}|${item.category}`;
    const existing = deduped.get(dedupeKey);
    if (!existing) {
      deduped.set(dedupeKey, item);
      continue;
    }
    const preferred = choosePreferredAssetItem(existing, item);
    const merged: AssetItem = {
      ...existing,
      ...item,
      ...preferred,
      name: preferred.name,
      category: preferred.category || existing.category || item.category,
      source: existing.persistedAssetId || item.persistedAssetId ? (preferred.scope === "community" ? "社区资产" : "我的资产") : preferred.source,
      previewUrl: preferred.previewUrl ?? existing.previewUrl ?? item.previewUrl,
      storageUrl: preferred.storageUrl ?? existing.storageUrl ?? item.storageUrl,
      fileUrl: preferred.fileUrl ?? existing.fileUrl ?? item.fileUrl,
      persistedAssetId: existing.persistedAssetId ?? item.persistedAssetId,
      persistedHistoryId: existing.persistedHistoryId ?? item.persistedHistoryId,
      deletable: (existing.deletable ?? false) || (item.deletable ?? false),
      scope: existing.persistedAssetId || item.persistedAssetId ? (existing.scope === "community" || item.scope === "community" ? "community" : "private") : preferred.scope,
      ownerUsername: existing.ownerUsername ?? item.ownerUsername ?? preferred.ownerUsername,
      canPublish: (existing.canPublish ?? false) || (item.canPublish ?? false),
      canUnpublish: (existing.canUnpublish ?? false) || (item.canUnpublish ?? false),
      sortAt: existing.sortAt ?? item.sortAt ?? preferred.sortAt,
      updatedAt: existing.updatedAt ?? item.updatedAt ?? preferred.updatedAt,
      tags: Array.from(new Set([...(existing.tags ?? []), ...(item.tags ?? [])])),
      preview: preferred.preview ?? existing.preview ?? item.preview,
    };
    deduped.set(dedupeKey, merged);
  }
  return Array.from(deduped.values());
}

function workspaceRunsStorageKey(userId: string) {
  return `${WORKSPACE_RUNS_STORAGE_KEY_PREFIX}:${userId}`;
}

function loadStoredWorkspaceRuns(userId: string | null | undefined): WorkspaceRun[] {
  if (typeof window === "undefined") return [];
  if (!userId) return [];
  try {
    const raw = window.localStorage.getItem(workspaceRunsStorageKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is WorkspaceRun => Boolean(item) && typeof item === "object" && typeof item.id === "string")
      .filter((item) => !isExpiringSignedUrl(item.imageUrl))
      .map((item) => ({
        ...item,
        imageUrl: isBlobUrl(item.imageUrl) || isExpiringSignedUrl(item.imageUrl) ? null : item.imageUrl,
        sourceImageUrl: isBlobUrl(item.sourceImageUrl) || isExpiringSignedUrl(item.sourceImageUrl) ? null : item.sourceImageUrl,
        sourceImages: Array.isArray(item.sourceImages)
          ? item.sourceImages.filter((entry): entry is string => typeof entry === "string" && !isBlobUrl(entry) && !isExpiringSignedUrl(entry))
          : item.sourceImages,
      }));
  } catch {
    return [];
  }
}

function persistWorkspaceRuns(userId: string | null | undefined, runs: WorkspaceRun[]) {
  if (typeof window === "undefined") return;
  if (!userId) return;
  try {
    window.localStorage.setItem(workspaceRunsStorageKey(userId), JSON.stringify(runs.slice(0, MAX_STORED_WORKSPACE_RUNS)));
  } catch {
    // Ignore storage write failures.
  }
}

function hasViewPermission(user: CurrentUser, view: AppView) {
  if (view === "admin") return user.role === "root";
  const moduleKey = viewModuleMap[view];
  if (!moduleKey) return true;
  if (user.role === "root") return true;
  return user.permissions.some((item) => item.module_key === moduleKey && item.is_enabled);
}

function firstAvailableView(user: CurrentUser): AppView {
  const ordered: AppView[] = [
    "asset-management",
    "ai-agent",
    "text-to-image",
    "fusion",
    "image-edit",
    "product-refine",
    "gemstone-design",
    "upscale",
    "multi-view",
    "grayscale-relief",
    "multi-view-split",
    "history",
    "admin",
  ];
  return ordered.find((view) => hasViewPermission(user, view)) ?? "asset-management";
}

export default function App() {
  const { isMobile, isTablet } = useViewport();
  const [activeView, setActiveView] = useState<AppView>("text-to-image");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [workspaceRuns, setWorkspaceRuns] = useState<WorkspaceRun[]>([]);
  const [persistedItems, setPersistedItems] = useState<PersistedHistoryItem[]>([]);
  const [persistedAssets, setPersistedAssets] = useState<PersistedAssetItem[]>([]);
  const [persistedError, setPersistedError] = useState<string | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminSystemStatus, setAdminSystemStatus] = useState<AdminSystemStatus | null>(null);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [adminLoading, setAdminLoading] = useState(false);

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    if (!currentUser) {
      setWorkspaceRuns([]);
      return;
    }
    setWorkspaceRuns(loadStoredWorkspaceRuns(currentUser.id));
  }, [currentUser?.id]);

  useEffect(() => {
    persistWorkspaceRuns(currentUser?.id, workspaceRuns);
  }, [currentUser?.id, workspaceRuns]);

  useEffect(() => {
    if (!currentUser) return;
    if (!hasViewPermission(currentUser, activeView)) {
      setActiveView(firstAvailableView(currentUser));
    }
  }, [activeView, currentUser]);

  useEffect(() => {
    if (!currentUser) return;
    if (["multi-view", "multi-view-split", "image-edit", "product-refine", "gemstone-design", "upscale", "fusion", "grayscale-relief", "ai-agent", "asset-management"].includes(activeView)) {
      void refreshPersistedAssets();
    }
  }, [activeView, currentUser]);

  useEffect(() => {
    if (isMobile) {
      setSidebarCollapsed(false);
      return;
    }
    setSidebarCollapsed(isTablet);
  }, [isMobile, isTablet]);

  async function bootstrap() {
    setBootstrapping(true);
    try {
      const user = await fetchCurrentUser();
      setCurrentUser(user);
      setActiveView(firstAvailableView(user));
      await Promise.all([refreshPersistedHistory(user), refreshPersistedAssets(), user.role === "root" ? refreshAdminData(user) : Promise.resolve()]);
      setAuthError(null);
    } catch {
      setCurrentUser(null);
    } finally {
      setBootstrapping(false);
    }
  }

  async function handleLogin(username: string, password: string) {
    try {
      const response = await login(username, password);
      setCurrentUser(response.user);
      setActiveView(firstAvailableView(response.user));
      await Promise.all([refreshPersistedHistory(response.user), refreshPersistedAssets(), response.user.role === "root" ? refreshAdminData(response.user) : Promise.resolve()]);
      setAuthError(null);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "登录失败");
    }
  }

  async function handleRegister(payload: { username: string; password: string; displayName?: string }) {
    try {
      const response = await registerUser({
        username: payload.username,
        password: payload.password,
        display_name: payload.displayName,
      });
      setCurrentUser(response.user);
      setActiveView(firstAvailableView(response.user));
      await Promise.all([refreshPersistedHistory(response.user), refreshPersistedAssets()]);
      setAuthError(null);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "注册失败");
    }
  }

  async function handleLogout() {
    await logout();
    setCurrentUser(null);
    setWorkspaceRuns([]);
    setPersistedItems([]);
    setPersistedAssets([]);
    setAdminUsers([]);
    setAdminSystemStatus(null);
  }

  async function refreshPersistedHistory(user = currentUser) {
    if (!user) return;
    try {
      const response = await fetchPersistedHistory(user.role === "root");
      const dedupedItems = dedupePersistedHistoryItems(response.items);
      setPersistedItems(dedupedItems);
      setWorkspaceRuns((current) => current.filter((run) => !dedupedItems.some((item) => isPersistedHistoryDuplicateOfRun(item, run))));
      setPersistedError(null);
    } catch (error) {
      setPersistedError(error instanceof Error ? error.message : "加载持久化历史失败");
    }
  }

  async function refreshPersistedAssets() {
    try {
      const response = await fetchPersistedAssets("library");
      setPersistedAssets(response.items);
      setAssetError(null);
    } catch (error) {
      setAssetError(error instanceof Error ? error.message : "加载资产失败");
    }
  }

  async function refreshAdminUsers(user = currentUser) {
    if (user?.role !== "root") return;
    try {
      setAdminLoading(true);
      setAdminUsers(await fetchAdminUsers());
      setAdminError(null);
    } catch (error) {
      setAdminError(error instanceof Error ? error.message : "加载用户失败");
    } finally {
      setAdminLoading(false);
    }
  }

  async function refreshAdminData(user = currentUser) {
    if (user?.role !== "root") return;
    try {
      setAdminLoading(true);
      const [users, status] = await Promise.all([fetchAdminUsers(), fetchAdminSystemStatus()]);
      setAdminUsers(users);
      setAdminSystemStatus(status);
      setAdminError(null);
    } catch (error) {
      setAdminError(error instanceof Error ? error.message : "加载系统管理数据失败");
    } finally {
      setAdminLoading(false);
    }
  }

  async function handleDeleteAsset(assetId: string) {
    await deletePersistedAsset(assetId);
    await refreshPersistedAssets();
  }

  async function handlePublishAsset(assetId: string) {
    await publishPersistedAsset(assetId);
    await refreshPersistedAssets();
  }

  async function handleUnpublishAsset(assetId: string) {
    await unpublishPersistedAsset(assetId);
    await refreshPersistedAssets();
  }

  async function handleUploadCommunityAsset(file: File, moduleKind: string) {
    await uploadInputAsset(file, moduleKind, "manual_upload", "community");
    await refreshPersistedAssets();
  }

  async function handleDeleteHistory(historyId: string) {
    const target = persistedItems.find((item) => item.id === historyId);
    await deletePersistedHistory(historyId);
    setPersistedItems((current) => current.filter((item) => item.id !== historyId));
    if (target) {
      setWorkspaceRuns((current) => current.filter((run) => !isPersistedHistoryDuplicateOfRun(target, run)));
    }
    await refreshPersistedHistory();
    await refreshPersistedAssets();
  }

  const assetItems = useMemo<AssetItem[]>(() => {
    const uploadedAssets = persistedAssets.map((item) => {
      const moduleLabel = item.module_kind ? mapKindToAssetCategory(item.module_kind) : "已上传资产";
      const previewUrl = item.preview_url ?? item.storage_url;
      const preferredName = choosePreferredAssetName(item.name, extractStoredFilename(item.storage_url));
      const resolvedName =
        item.source_kind === "generated_output"
          ? appendSecondSuffixToName(preferredName, item.created_at)
          : item.source_kind.includes("upload")
            ? buildUploadedAssetName(item.owner_username, preferredName, item.created_at)
            : preferredName;
      return {
        id: `asset-${item.id}`,
        name: resolvedName,
        category: moduleLabel,
        source: item.visibility === "community" ? "社区资产" : "我的资产",
        updatedAt: formatHistoryTimestamp(item.created_at),
        sortAt: item.created_at,
        preview: buildPreviewBackground(previewUrl),
        tags: [moduleLabel, item.source_kind, item.visibility, item.owner_username ?? "unknown"],
        storageUrl: item.storage_url,
        previewUrl,
        fileUrl: buildAssetContentUrl(item.storage_url, item.name),
        persistedAssetId: item.id,
        deletable: item.can_delete,
        scope: item.visibility,
        ownerUsername: item.owner_username,
        canPublish: item.can_publish,
        canUnpublish: item.can_unpublish,
      } satisfies AssetItem;
    });

    const sessionAssets = workspaceRuns
      .filter((item) => !isMultiViewSplitKind(item.kind))
      .filter((item) => item.imageUrl && !isBlobUrl(item.imageUrl))
      .map((item) => ({
        id: `session-${item.id}`,
        name: appendSecondSuffixToName(item.title, item.createdAt),
        category: mapKindToAssetCategory(item.kind),
        source: "当前会话",
        updatedAt: formatHistoryTimestamp(item.createdAt),
        sortAt: item.createdAt,
        preview: buildPreviewBackground(item.imageUrl),
        tags: [mapKindToAssetCategory(item.kind), item.model, item.provider],
        previewUrl: item.imageUrl,
        fileUrl: item.imageUrl,
        scope: "session",
      } satisfies AssetItem));

    const merged = dedupeAssetItems([...uploadedAssets, ...sessionAssets]).sort(
      (left, right) => parseDisplayTimestamp(right.sortAt ?? right.updatedAt) - parseDisplayTimestamp(left.sortAt ?? left.updatedAt),
    );
    return merged;
  }, [persistedAssets, workspaceRuns]);

  const currentViewTitle = useMemo(() => {
    const titleMap: Record<AppView, string> = {
      "ai-agent": "AI Agent",
      "text-to-image": "文生图",
      "multi-view": "生成多视图",
      "multi-view-split": "多视图切图",
      "image-edit": "线稿转写实图",
      "product-refine": "产品精修",
      "gemstone-design": "裸石设计",
      upscale: "高清放大",
      fusion: "多图融合",
      history: "历史记录",
      "grayscale-relief": "转灰度图",
      "asset-management": "资产管理",
      admin: "系统管理",
    };
    return titleMap[activeView];
  }, [activeView]);

  const textToImageRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "text_to_image"), [persistedItems, workspaceRuns]);
  const fusionRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "fusion"), [persistedItems, workspaceRuns]);
  const imageEditRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "sketch_to_realistic"), [persistedItems, workspaceRuns]);
  const productRefineRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "product_refine"), [persistedItems, workspaceRuns]);
  const gemstoneDesignRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "gemstone_design"), [persistedItems, workspaceRuns]);
  const upscaleRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "upscale"), [persistedItems, workspaceRuns]);
  const grayscaleRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "grayscale_relief"), [persistedItems, workspaceRuns]);
  const multiViewRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "multi_view"), [persistedItems, workspaceRuns]);
  const multiViewSplitRuns = useMemo(() => mergeModuleHistory(persistedItems, workspaceRuns, "multi_view_split"), [persistedItems, workspaceRuns]);

  function recordWorkspaceRun(run: Omit<WorkspaceRun, "id" | "createdAt">) {
    setWorkspaceRuns((current) =>
      [
        { ...run, id: `${run.kind}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, createdAt: new Date().toISOString() },
        ...current,
      ].slice(0, MAX_STORED_WORKSPACE_RUNS),
    );
    void refreshPersistedHistory();
    void refreshPersistedAssets();
    window.setTimeout(() => {
      void refreshPersistedHistory();
      void refreshPersistedAssets();
    }, 800);
  }

  if (bootstrapping) {
    return <main className="workspace login-shell"><section className="panel compact-panel login-panel"><p>加载中...</p></section></main>;
  }

  if (!currentUser) {
    return <LoginPage error={authError} onLogin={handleLogin} onRegister={handleRegister} onClearError={() => setAuthError(null)} />;
  }

  return (
    <div className={[sidebarCollapsed && !isMobile ? "app-shell sidebar-collapsed" : "app-shell", isMobile ? "app-shell-mobile" : "", mobileNavOpen ? "mobile-nav-open" : ""].filter(Boolean).join(" ")}>
      <Sidebar
        activeView={activeView}
        onChange={setActiveView}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((value) => !value)}
        currentUser={currentUser}
        mobile={isMobile}
        mobileOpen={mobileNavOpen}
        onCloseMobile={() => setMobileNavOpen(false)}
      />

      <main className="workspace">
        <AppHeader
          title={currentViewTitle}
          currentUser={currentUser}
          onLogout={handleLogout}
          showNavigationButton={isMobile}
          onOpenNavigation={() => setMobileNavOpen(true)}
        />

        <section className={activeView === "text-to-image" ? "view-panel active" : "view-panel hidden"}>
          <TextToImagePage onRecordRun={recordWorkspaceRun} pageRuns={textToImageRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "multi-view" ? "view-panel active" : "view-panel hidden"}>
          <MultiViewPage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshHistory={refreshPersistedHistory} onRefreshAssets={refreshPersistedAssets} pageRuns={multiViewRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "multi-view-split" ? "view-panel active" : "view-panel hidden"}>
          <MultiViewSplitPage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshAssets={refreshPersistedAssets} pageRuns={multiViewSplitRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "image-edit" ? "view-panel active" : "view-panel hidden"}>
          <ImageEditPage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshHistory={refreshPersistedHistory} onRefreshAssets={refreshPersistedAssets} pageRuns={imageEditRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "product-refine" ? "view-panel active" : "view-panel hidden"}>
          <ProductRefinePage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshAssets={refreshPersistedAssets} pageRuns={productRefineRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "gemstone-design" ? "view-panel active" : "view-panel hidden"}>
          <GemstoneDesignPage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshAssets={refreshPersistedAssets} pageRuns={gemstoneDesignRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "upscale" ? "view-panel active" : "view-panel hidden"}>
          <UpscaleEnhancePage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshAssets={refreshPersistedAssets} pageRuns={upscaleRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "grayscale-relief" ? "view-panel active" : "view-panel hidden"}>
          <GrayscaleReliefPage assetItems={assetItems} onRecordRun={recordWorkspaceRun} onRefreshAssets={refreshPersistedAssets} pageRuns={grayscaleRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "ai-agent" ? "view-panel active" : "view-panel hidden"}>
          <AgentPage assetItems={assetItems} onRefreshAssets={refreshPersistedAssets} />
        </section>
        <section className={activeView === "fusion" ? "view-panel active" : "view-panel hidden"}>
          <FusionStudio onRecordRun={recordWorkspaceRun} assetItems={assetItems} onRefreshAssets={refreshPersistedAssets} pageRuns={fusionRuns} onDeleteHistory={handleDeleteHistory} />
        </section>
        <section className={activeView === "history" ? "view-panel active" : "view-panel hidden"}>
          <HistoryPage
            workspaceRuns={workspaceRuns}
            persistedItems={persistedItems}
            persistedError={persistedError}
            onRefresh={refreshPersistedHistory}
            onDeleteHistory={handleDeleteHistory}
          />
        </section>
        <section className={activeView === "asset-management" ? "view-panel active" : "view-panel hidden"}>
          <AssetManagementPage
            assetItems={assetItems}
            assetError={assetError}
            currentUser={currentUser}
            onDeleteAsset={handleDeleteAsset}
            onDeleteHistory={handleDeleteHistory}
            onPublishAsset={handlePublishAsset}
            onUnpublishAsset={handleUnpublishAsset}
            onUploadCommunityAsset={currentUser.role === "root" ? handleUploadCommunityAsset : undefined}
            onRefresh={refreshPersistedAssets}
          />
        </section>
        <section className={activeView === "admin" ? "view-panel active" : "view-panel hidden"}>
          <AdminPage
            users={adminUsers}
            systemStatus={adminSystemStatus}
            loading={adminLoading}
            error={adminError}
            onRefresh={refreshAdminData}
            onCreateUser={async (payload) => {
              await createAdminUser(payload);
              await refreshAdminData();
            }}
            onToggleDisabled={async (user) => {
              await updateAdminUser(user.id, { is_disabled: !user.is_disabled });
              await refreshAdminData();
            }}
            onDeleteUser={async (user) => {
              await deleteAdminUser(user.id);
              await refreshAdminData();
            }}
            onResetPassword={async (user, password) => {
              await resetAdminUserPassword(user.id, password);
            }}
            onSavePermissions={async (user, permissions) => {
              await updateAdminUserPermissions(user.id, permissions);
              await refreshAdminData();
            }}
          />
        </section>
      </main>
    </div>
  );
}
