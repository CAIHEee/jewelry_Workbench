import type { GenerationJobProgress, GenerationJobStatusResponse } from "../types/fusion";

interface GenerationJobProgressLabels {
  queued?: string;
  running?: string;
  qwenPrompt?: string;
  imageGeneration?: string;
  uploading?: string;
  succeeded?: string;
  failed?: string;
}

export function buildGenerationJobProgress(
  job: GenerationJobStatusResponse,
  labels: GenerationJobProgressLabels = {},
): GenerationJobProgress {
  if (job.status === "queued") {
    return { percent: 18, label: labels.queued || job.message || "任务排队中..." };
  }

  if (job.status === "running") {
    if (job.stage === "qwen_prompt") {
      return { percent: 32, label: labels.qwenPrompt || job.message || "反推模型分析原图中..." };
    }
    if (job.stage === "image_generation") {
      return { percent: 68, label: labels.imageGeneration || job.message || labels.running || "模型生成中..." };
    }
    return { percent: 68, label: labels.running || job.message || "模型生成中..." };
  }

  if (job.status === "uploading") {
    return { percent: 92, label: labels.uploading || job.message || "上传结果并写入历史..." };
  }

  if (job.status === "succeeded") {
    return { percent: 100, label: labels.succeeded || "已完成" };
  }

  return { percent: 100, label: job.error_message || job.message || labels.failed || "任务执行失败" };
}
