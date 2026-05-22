export interface WorkflowStat {
  label: string;
  value: string;
  helper: string;
}

export interface WorkflowStage {
  title: string;
  description: string;
  tags: string[];
}

export interface HistoryItem {
  id: string;
  title: string;
  version: string;
  module: string;
  updatedAt: string;
  preview: string;
}

export interface TaskSummary {
  id: string;
  title: string;
  helper: string;
  statusLabel: string;
  statusClass: "online" | "warning" | "idle";
}

export interface AssetItem {
  id: string;
  name: string;
  category: string;
  moduleKind?: string | null;
  source: string;
  updatedAt: string;
  sortAt?: string;
  preview: string;
  tags: string[];
  storageUrl?: string;
  previewUrl?: string | null;
  fileUrl?: string | null;
  persistedAssetId?: string;
  persistedHistoryId?: string;
  deletable?: boolean;
  scope?: "private" | "community" | "history" | "session";
  ownerUsername?: string | null;
  canPublish?: boolean;
  canUnpublish?: boolean;
}

export const mockWorkflowStats: WorkflowStat[] = [
  { label: "本周任务数", value: "28", helper: "覆盖文生图、多图融合、图像精修等工作" },
  { label: "可复用资产", value: "36", helper: "可直接作为后续步骤的输入素材" },
  { label: "平均流程长度", value: "4 步", helper: "常见路径为生成、融合、精修、沉淀" },
  { label: "待接入能力", value: "6", helper: "后续可扩展更强搜索、批量处理和标注" },
];

export const mockStages: WorkflowStage[] = [
  {
    title: "文生图",
    description: "通过提示词快速生成首版珠宝方案，为后续融合与精修打底。",
    tags: ["提示词", "模型选择", "快速出图"],
  },
  {
    title: "多图融合",
    description: "组合多张参考图中的结构、材质和细节，形成统一方案。",
    tags: ["资产复用", "主参考图", "融合模式"],
  },
  {
    title: "生成多视图",
    description: "把已有方案扩展成统一的多视图结果，便于后续建模和确认。",
    tags: ["视图预设", "结构一致", "标准输出"],
  },
  {
    title: "历史沉淀",
    description: "保存关键版本、来源与结果链接，方便回看与复用。",
    tags: ["版本", "资产沉淀", "追踪"],
  },
];

export const mockHistoryItems: HistoryItem[] = [
  {
    id: "hist-1",
    title: "主石融合草案",
    version: "v3",
    module: "多图融合",
    updatedAt: "2026-04-16 10:20",
    preview: "linear-gradient(135deg, #5c4a72 0%, #f0c27b 100%)",
  },
  {
    id: "hist-2",
    title: "吊坠概念初稿",
    version: "v1",
    module: "文生图",
    updatedAt: "2026-04-16 09:35",
    preview: "linear-gradient(135deg, #3a6073 0%, #d7d2cc 100%)",
  },
  {
    id: "hist-3",
    title: "正侧背多视图",
    version: "v2",
    module: "生成多视图",
    updatedAt: "2026-04-15 18:42",
    preview: "linear-gradient(135deg, #606c88 0%, #3f4c6b 100%)",
  },
  {
    id: "hist-4",
    title: "戒臂纹理精修",
    version: "v5",
    module: "线稿转写实图",
    updatedAt: "2026-04-15 16:08",
    preview: "linear-gradient(135deg, #b79891 0%, #94716b 100%)",
  },
  {
    id: "hist-5",
    title: "祖母绿镶口方案",
    version: "v2",
    module: "文生图",
    updatedAt: "2026-04-14 20:12",
    preview: "linear-gradient(135deg, #134e5e 0%, #71b280 100%)",
  },
  {
    id: "hist-6",
    title: "星形吊坠结构图",
    version: "v4",
    module: "生成多视图",
    updatedAt: "2026-04-14 11:03",
    preview: "linear-gradient(135deg, #0f2027 0%, #2c5364 100%)",
  },
];

export const mockTasks: TaskSummary[] = [
  {
    id: "task-1",
    title: "资产管理接真实存储",
    helper: "当前以前端静态资产为主，后续可继续接 OSS 与元数据检索。",
    statusLabel: "下一步",
    statusClass: "warning",
  },
  {
    id: "task-2",
    title: "图片来源统一接线",
    helper: "各页面都支持从资产管理选图或手动上传。",
    statusLabel: "界面完成",
    statusClass: "online",
  },
  {
    id: "task-3",
    title: "历史搜索与筛选",
    helper: "后续可以把历史、资产、任务状态做统一检索。",
    statusLabel: "规划中",
    statusClass: "idle",
  },
];

export const mockAssetItems: AssetItem[] = [
  {
    id: "asset-1",
    name: "祖母绿戒指正视图",
    category: "戒指",
    source: "历史版本",
    updatedAt: "2026-04-16 11:20",
    preview: "linear-gradient(135deg, #204f46 0%, #9bd6b3 100%)",
    tags: ["祖母绿", "戒指", "主图"],
  },
  {
    id: "asset-2",
    name: "玫瑰金吊坠草图",
    category: "吊坠",
    source: "手动上传",
    updatedAt: "2026-04-16 10:42",
    preview: "linear-gradient(135deg, #6f3e46 0%, #ddb1a4 100%)",
    tags: ["吊坠", "草图", "玫瑰金"],
  },
  {
    id: "asset-3",
    name: "蓝宝石耳饰参考图",
    category: "耳饰",
    source: "参考采集",
    updatedAt: "2026-04-15 19:05",
    preview: "linear-gradient(135deg, #243b83 0%, #86a8ff 100%)",
    tags: ["蓝宝石", "耳饰", "参考图"],
  },
  {
    id: "asset-4",
    name: "钻石戒托结构稿",
    category: "戒托",
    source: "多视图输出",
    updatedAt: "2026-04-15 16:16",
    preview: "linear-gradient(135deg, #4d535d 0%, #c8d0dd 100%)",
    tags: ["结构", "戒托", "金属"],
  },
  {
    id: "asset-5",
    name: "复古胸针纹样稿",
    category: "胸针",
    source: "手动上传",
    updatedAt: "2026-04-14 14:25",
    preview: "linear-gradient(135deg, #57442b 0%, #d7b274 100%)",
    tags: ["复古", "纹样", "胸针"],
  },
  {
    id: "asset-6",
    name: "珍珠项链展示图",
    category: "项链",
    source: "历史版本",
    updatedAt: "2026-04-14 10:08",
    preview: "linear-gradient(135deg, #5f6574 0%, #f3e7dc 100%)",
    tags: ["珍珠", "项链", "展示图"],
  },
];
