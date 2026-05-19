import { ReferenceTransformModulePage } from "../components/ReferenceTransformModulePage";
import type { AssetItem } from "../types/mockData";
import type { WorkspaceRun } from "../types/workspace";
import type { ModuleHistoryEntry } from "../utils/history";

const progressPhases = [
  { at: 18, label: "分析裸石参考图..." },
  { at: 40, label: "提交裸石设计请求..." },
  { at: 74, label: "生成切工与材质方案中..." },
  { at: 95, label: "整理裸石结果..." },
];
const preferredGemstoneDesignModelId = "gemini-3.1-flash-image-preview";

interface GemstoneDesignPageProps {
  assetItems: AssetItem[];
  onRecordRun: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  onRefreshAssets?: () => Promise<void> | void;
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

export function GemstoneDesignPage(props: GemstoneDesignPageProps) {
  return (
    <ReferenceTransformModulePage
      {...props}
      pageTitle="裸石设计"
      historyTitle="裸石设计历史"
      previewTitle="裸石设计预览"
      resultLabel="裸石结果"
      sourcePickerTitle="选择裸石设计来源"
      uploadLabel="上传裸石图"
      promptLabel="裸石提示词"
      submitLabel="生成"
      loadingLabel="生成中..."
      emptyModelError="当前没有可用的裸石设计模型。"
      emptyAssetError="请先选择一张裸石参考图。"
      submitErrorLabel="裸石设计失败"
      feature="gemstone_design"
      module="gemstone-design"
      historyKind="gemstone_design"
      endpointPath="/ai/gemstone-design"
      allowMultipleSources
      defaultPrompt=""
      progressPhases={progressPhases}
      successLabel="裸石方案已完成"
      errorProgressLabel="裸石设计失败"
      preferredModelId={preferredGemstoneDesignModelId}
    />
  );
}
