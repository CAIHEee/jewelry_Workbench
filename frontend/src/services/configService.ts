import type {
  ConfigGroup,
  ConfigGroupCreate,
  ConfigGroupUpdate,
  ConfigListResponse,
  ConfigToggleResponse,
} from "../types/config";

const API_BASE = "/api/v1/admin";

export async function fetchConfigKeys(): Promise<ConfigListResponse> {
  const res = await fetch(`${API_BASE}/config/keys`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`获取密钥配置失败: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchConfigKeyRaw(groupKey: string): Promise<ConfigGroup> {
  const res = await fetch(`${API_BASE}/config/keys/${encodeURIComponent(groupKey)}`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`获取密钥配置失败: ${res.statusText}`);
  }
  return res.json();
}

export async function updateConfigKey(
  groupKey: string,
  payload: ConfigGroupUpdate,
): Promise<ConfigGroup> {
  const res = await fetch(`${API_BASE}/config/keys/${encodeURIComponent(groupKey)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `更新密钥配置失败: ${res.statusText}`);
  }
  return res.json();
}

export async function toggleConfigKey(groupKey: string): Promise<ConfigToggleResponse> {
  const res = await fetch(`${API_BASE}/config/keys/${encodeURIComponent(groupKey)}/toggle`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `切换密钥状态失败: ${res.statusText}`);
  }
  return res.json();
}

export async function createConfigKey(payload: ConfigGroupCreate): Promise<ConfigGroup> {
  const res = await fetch(`${API_BASE}/config/keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `添加密钥配置失败: ${res.statusText}`);
  }
  return res.json();
}

export async function deleteConfigKey(groupKey: string): Promise<void> {
  const res = await fetch(`${API_BASE}/config/keys/${encodeURIComponent(groupKey)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `删除密钥配置失败: ${res.statusText}`);
  }
}
