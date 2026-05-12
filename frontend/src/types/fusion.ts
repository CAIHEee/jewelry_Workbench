export type FusionMode = "balanced" | "style_first" | "structure_first" | "detail_enhanced";

export type ProviderType = "flux" | "gemini" | "apiyi" | "wuyin" | "aiapis" | "dmxapi";

export interface ModelDefinition {
  id: string;
  label: string;
  provider: ProviderType;
  category: string;
  supports_text_to_image: boolean;
  supports_multi_image_fusion: boolean;
  supports_reference_images: boolean;
  pricing_hint: string;
}

export interface ModelCatalogResponse {
  models: ModelDefinition[];
}

export interface TextToImageRequest {
  prompt: string;
  model: string;
  aspect_ratio: string;
  size: string;
  image_size: string;
}

export interface GenerationResult {
  job_id: string | null;
  status: string;
  provider: ProviderType;
  model: string;
  image_url: string | null;
  source_image_url?: string | null;
  source_image_storage_url?: string | null;
  revised_prompt?: string | null;
  message: string;
  raw_response?: Record<string, unknown> | null;
  results?: GenerationResult[];
}

export interface FusionRequest {
  files?: File[];
  sourceImageUrls?: string[];
  sourceImageNames?: string[];
  model: string;
  prompt: string;
  mode: FusionMode;
  primaryImageIndex: number;
  strength: number;
}

export interface FusionResult {
  job_id: string | null;
  status: string;
  message: string;
  provider: ProviderType;
  feature: string;
  model: string;
  image_url: string | null;
  metadata: {
    model: string;
    image_count: number;
    prompt: string;
    negative_prompt?: string | null;
    mode: FusionMode;
    primary_image_index: number;
    strength: number;
    filenames: string[];
  };
  raw_response?: Record<string, unknown> | null;
}

export interface ReferenceImageTransformRequest {
  file?: File;
  files?: File[];
  sourceImageUrl?: string;
  sourceImageName?: string;
  sourceImageUrls?: string[];
  sourceImageNames?: string[];
  model: string;
  prompt: string;
  feature: string;
  negativePrompt?: string;
  strength?: number;
  imageSize?: string;
  batchSize?: 1 | 2 | 4;
}

export interface MultiViewSplitRequest {
  image_url: string;
  source_image_name?: string;
  model: string;
  split_x_ratio: number;
  split_y_ratio: number;
  gap_x_ratio: number;
  gap_y_ratio: number;
}

export interface MultiViewSplitItem {
  view: string;
  image_url: string | null;
  storage_url: string | null;
  width: number;
  height: number;
}

export interface MultiViewSplitResponse {
  status: string;
  message: string;
  source_image_url: string;
  split_x_ratio: number;
  split_y_ratio: number;
  gap_x_ratio: number;
  gap_y_ratio: number;
  items: MultiViewSplitItem[];
}

export type GenerationJobStatus = "queued" | "running" | "uploading" | "succeeded" | "failed";

export interface GenerationJobAccepted {
  job_id: string;
  status: GenerationJobStatus;
  feature: string;
  message: string;
}

export interface GenerationJobStatusResponse {
  job_id: string;
  feature: string;
  status: GenerationJobStatus;
  stage?: string | null;
  model?: string | null;
  prompt?: string | null;
  message: string;
  error_message?: string | null;
  result?: Record<string, unknown> | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface GenerationJobProgress {
  percent: number;
  label: string;
}
