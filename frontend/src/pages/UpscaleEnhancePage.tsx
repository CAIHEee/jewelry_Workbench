import { ReferenceTransformModulePage } from "../components/ReferenceTransformModulePage";
import type { AssetItem } from "../types/mockData";
import type { WorkspaceRun } from "../types/workspace";
import type { ModuleHistoryEntry } from "../utils/history";

const progressPhases = [
  { at: 18, label: "分析原图细节..." },
  { at: 40, label: "提交 2K 放大请求..." },
  { at: 74, label: "增强纹理与边缘中..." },
  { at: 95, label: "输出 2K 结果..." },
];

interface UpscaleEnhancePageProps {
  assetItems: AssetItem[];
  onRecordRun: (run: Omit<WorkspaceRun, "id" | "createdAt">) => void;
  pageRuns: ModuleHistoryEntry[];
  onDeleteHistory?: (historyId: string) => Promise<void> | void;
}

export function UpscaleEnhancePage(props: UpscaleEnhancePageProps) {
  return (
    <ReferenceTransformModulePage
      {...props}
      pageTitle="高清放大"
      historyTitle="高清放大历史"
      previewTitle="高清放大预览"
      resultLabel="放大结果"
      sourcePickerTitle="选择高清放大来源"
      uploadLabel="上传图片"
      promptLabel="增强提示词"
      submitLabel="生成高清图"
      loadingLabel="放大中..."
      emptyModelError="当前没有可用的高清放大模型。"
      emptyAssetError="请先选择一张待放大图片。"
      submitErrorLabel="高清放大失败"
      feature="upscale"
      module="upscale"
      historyKind="upscale"
      endpointPath="/ai/upscale"
      imageSize="2K"
      hideModelSelector
      hidePromptEditor
      defaultPrompt="在保持原始设计与构图不变的前提下，对图片进行高清放大与细节增强，提升边缘锐度、材质纹理、宝石细节和整体清晰度，使其更适合高清展示与细节审看。"
      progressPhases={progressPhases}
      successLabel="2K 图片已完成"
      errorProgressLabel="高清放大失败"
    />
  );
}
