export type ConfigKeyType = "image" | "agent" | "vision" | "multiview";

export interface ConfigKeyItem {
  key: string;
  label: string;
  value: string | null;
  value_raw?: string | null;
  placeholder: string;
  type: "text" | "password" | "number" | "select";
  required: boolean;
  options?: string[];
  is_secret?: boolean;
}

export interface ConfigGroup {
  group_key: string;
  label: string;
  category: ConfigKeyType;
  description: string;
  is_active: boolean;
  items: ConfigKeyItem[];
}

export interface ConfigGroupRaw {
  group_key: string;
  items: Record<string, string>;
}

export interface ConfigListResponse {
  groups: ConfigGroup[];
}

export interface ConfigGroupUpdate {
  group_key: string;
  items: Record<string, string>;
  is_active?: boolean | null;
}

export interface ConfigGroupCreate {
  group_key: string;
  label: string;
  category: ConfigKeyType;
  base_url: string;
  api_key: string;
  models: string;  // 格式：model_id:label,model_id:label
  timeout: number;
}

export interface ConfigToggleResponse {
  group_key: string;
  is_active: boolean;
  message: string;
}

export const CATEGORY_LABELS: Record<ConfigKeyType, string> = {
  image: "生图密钥",
  agent: "Agent 对话",
  vision: "视觉分析",
  multiview: "多视图反推",
};

export const CATEGORY_SINGLETON: Record<ConfigKeyType, boolean> = {
  image: false,
  agent: true,
  vision: true,
  multiview: true,
};
