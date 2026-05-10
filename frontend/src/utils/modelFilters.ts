import type { ModelDefinition } from "../types/fusion";

export const MULTI_VIEW_ONLY_MODEL_ID = "multi-view-few-shot-apiyi";

export function isNotMultiViewOnlyModel(model: ModelDefinition): boolean {
  return model.id !== MULTI_VIEW_ONLY_MODEL_ID;
}
