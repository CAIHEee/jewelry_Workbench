import { useEffect, useMemo, useState } from "react";

import { fetchModelCatalog } from "../services/api";
import type { ModelDefinition } from "../types/fusion";

const DEFAULT_MODEL_ID = "gpt-image-2-all-apiyi";

export function useModelCatalog(filterFn?: (model: ModelDefinition) => boolean) {
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const response = await fetchModelCatalog();
        if (!active) {
          return;
        }
        setModels(response.models);
      } catch (loadError) {
        if (!active) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "加载模型目录失败");
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      active = false;
    };
  }, []);

  const filteredModels = useMemo(
    () => (filterFn ? models.filter(filterFn) : models),
    [filterFn, models],
  );

  const defaultModelId = useMemo(() => {
    const preferred = filteredModels.find((model) => model.id === DEFAULT_MODEL_ID);
    return preferred?.id ?? filteredModels[0]?.id ?? DEFAULT_MODEL_ID;
  }, [filteredModels]);

  return {
    models: filteredModels,
    loading,
    error,
    defaultModelId,
  };
}
