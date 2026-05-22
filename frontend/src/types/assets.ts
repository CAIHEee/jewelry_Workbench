export interface PersistedAssetItem {
  id: string;
  name: string;
  source_kind: string;
  module_kind: string | null;
  visibility: "private" | "community";
  owner_user_id: string | null;
  owner_username: string | null;
  storage_url: string;
  preview_url: string | null;
  mime_type: string | null;
  file_size: number | null;
  metadata?: Record<string, unknown> | null;
  can_delete: boolean;
  can_publish: boolean;
  can_unpublish: boolean;
  created_at: string;
}

export interface PersistedAssetResponse {
  items: PersistedAssetItem[];
  total: number;
  page: number;
  page_size: number;
}
