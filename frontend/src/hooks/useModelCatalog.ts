import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchModelCatalog } from "../services/api";
import type { ModelDefinition } from "../types/fusion";

const DEFAULT_MODEL_ID = "gpt-image-2-all-apiyi";

export function useModelCatalog(filterFn?: (model: ModelDefinition) => boolean) {
  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const loadModels = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetchModelCatalog();
      setModels(response.models);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载模型目录失败");
    } finally {
      setLoading(false);
    }
  }, []);

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

    // 监听页面可见性变化，当用户从其他标签页返回时刷新模型列表
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible" && active) {
        void load();
      }
    };

    // 监听密钥配置变化事件
    const handleConfigChange = () => {
      if (active) {
        void load();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("configChanged", handleConfigChange);

    return () => {
      active = false;
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("configChanged", handleConfigChange);
    };
  }, [refreshKey]);

  const refresh = useCallback(() => {
    setRefreshKey((prev) => prev + 1);
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
    refresh,
  };
}
