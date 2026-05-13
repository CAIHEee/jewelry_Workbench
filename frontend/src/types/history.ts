export interface PersistedHistoryItem {
  id: string;
  kind: string;
  title: string;
  model: string;
  provider: string;
  status: string;
  prompt: string;
  image_url: string | null;
  storage_url: string | null;
  preview_url: string | null;
  metadata?: Record<string, unknown> | null;
  owner_username: string | null;
  can_delete: boolean;
  created_at: string;
}

export interface PersistedHistoryResponse {
  items: PersistedHistoryItem[];
}
