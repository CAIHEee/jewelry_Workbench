import { useCallback, useEffect, useState } from "react";
import { SectionHeader } from "./SectionHeader";
import { CATEGORY_LABELS, CATEGORY_SINGLETON, type ConfigGroup, type ConfigGroupCreate, type ConfigKeyItem } from "../types/config";
import { createConfigKey, deleteConfigKey, fetchConfigKeys, fetchConfigKeyRaw, toggleConfigKey, updateConfigKey } from "../services/configService";

interface ConfigManagementProps {
  onToast?: (type: "success" | "error", message: string) => void;
}

type EditingState = {
  groupKey: string;
  items: Record<string, string>;
  isDirty: boolean;
} | null;

type AddingState = {
  group_key: string;
  label: string;
  category: "image" | "agent" | "vision" | "multiview";
  base_url: string;
  api_key: string;
  timeout: number;
  models: { id: string; label: string }[];
} | null;

type DeletingState = {
  groupKey: string;
  label: string;
} | null;

export function ConfigManagement({ onToast }: ConfigManagementProps) {
  const [groups, setGroups] = useState<ConfigGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<EditingState>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [adding, setAdding] = useState<AddingState>(null);
  const [deleting, setDeleting] = useState<DeletingState>(null);

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchConfigKeys();
      setGroups(data.groups);
    } catch (err) {
      onToast?.("error", err instanceof Error ? err.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  }, [onToast]);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  async function handleToggle(groupKey: string) {
    try {
      const result = await toggleConfigKey(groupKey);
      onToast?.("success", result.message);
      
      // 直接更新本地状态，避免重新加载
      setGroups((prevGroups) =>
        prevGroups.map((group) =>
          group.group_key === groupKey
            ? { ...group, is_active: result.is_active }
            : group
        )
      );
      
      // 触发配置变化事件，通知其他页面刷新模型列表
      window.dispatchEvent(new CustomEvent("configChanged"));
    } catch (err) {
      onToast?.("error", err instanceof Error ? err.message : "操作失败");
    }
  }

  async function handleEdit(groupKey: string) {
    try {
      const raw = await fetchConfigKeyRaw(groupKey);
      setEditing({ groupKey, items: raw.items, isDirty: false });
    } catch (err) {
      onToast?.("error", err instanceof Error ? err.message : "加载配置失败");
    }
  }

  async function handleSave() {
    if (!editing) return;
    try {
      setSaving(editing.groupKey);
      const group = groups.find((g) => g.group_key === editing.groupKey);
      if (!group) return;

      await updateConfigKey(editing.groupKey, {
        group_key: editing.groupKey,
        items: editing.items,
        is_active: group.is_active,
      });

      onToast?.("success", "配置已保存");
      setEditing(null);
      void loadConfig();
      
      // 触发配置变化事件，通知其他页面刷新模型列表
      window.dispatchEvent(new CustomEvent("configChanged"));
    } catch (err) {
      onToast?.("error", err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(null);
    }
  }

  function handleCancel() {
    setEditing(null);
  }

  function updateItemValue(key: string, value: string) {
    if (!editing) return;
    setEditing({
      ...editing,
      items: { ...editing.items, [key]: value },
      isDirty: true,
    });
  }

  async function handleAddSubmit() {
    if (!adding) return;
    try {
      // 构建模型配置字符串
      const modelsConfig = adding.models
        .filter((m) => m.id.trim())
        .map((m) => `${m.id.trim()}:${m.label.trim() || m.id.trim()}`)
        .join(",");

      await createConfigKey({
        group_key: adding.group_key.trim(),
        label: adding.label.trim(),
        category: adding.category,
        base_url: adding.base_url.trim(),
        api_key: adding.api_key.trim(),
        models: modelsConfig,
        timeout: adding.timeout,
      });

      onToast?.("success", "供应商已添加");
      setAdding(null);
      await loadConfig();  // 使用 await 确保立即刷新
      
      // 触发配置变化事件
      window.dispatchEvent(new CustomEvent("configChanged"));
    } catch (err) {
      onToast?.("error", err instanceof Error ? err.message : "添加失败");
    }
  }

  async function handleDeleteConfirm() {
    if (!deleting) return;
    try {
      await deleteConfigKey(deleting.groupKey);
      onToast?.("success", "供应商已删除");
      setDeleting(null);
      void loadConfig();
      
      // 触发配置变化事件
      window.dispatchEvent(new CustomEvent("configChanged"));
    } catch (err) {
      onToast?.("error", err instanceof Error ? err.message : "删除失败");
    }
  }

  // 按分类分组
  const groupedByCategory = Object.entries(CATEGORY_LABELS).map(([category, label]) => {
    const categoryGroups = groups.filter((g) => g.category === category);
    return { category, label, isSingleton: CATEGORY_SINGLETON[category as keyof typeof CATEGORY_SINGLETON], groups: categoryGroups };
  });

  if (loading) {
    return <p className="muted">加载配置中...</p>;
  }

  return (
    <div className="config-management">
      <div className="config-toolbar">
        <button className="primary-button compact-button" type="button" onClick={() => setAdding({ group_key: "", label: "", category: "image", base_url: "", api_key: "", timeout: 600, models: [{ id: "", label: "" }] })}>
          + 添加供应商
        </button>
      </div>

      {groupedByCategory.map(({ category, label, isSingleton, groups: categoryGroups }) => (
        <section key={category} className="config-category">
          <div className="config-category-header">
            <SectionHeader eyebrow={label} title={label} description={isSingleton ? "只能激活一个" : "可激活多个"} />
          </div>

          <div className="config-group-list">
            {categoryGroups.map((group) => {
              const isEditing = editing?.groupKey === group.group_key;
              const isSaving = saving === group.group_key;
              const isCustom = !BUILTIN_GROUP_KEYS.includes(group.group_key);

              return (
                <div key={group.group_key} className={`config-card ${group.is_active ? "active" : "inactive"} ${isEditing ? "editing" : ""}`}>
                  <div className="config-card-header">
                    <div className="config-card-title">
                      <span className={`config-status-dot ${group.is_active ? "active" : "inactive"}`} />
                      <strong>{group.label}</strong>
                      {group.is_active && isSingleton && <span className="config-badge">当前使用</span>}
                      {isCustom && <span className="config-badge custom">自定义</span>}
                    </div>
                    <div className="config-card-actions">
                      {isEditing ? (
                        <>
                          <button className="secondary-button compact-button" type="button" onClick={handleCancel} disabled={isSaving}>
                            取消
                          </button>
                          <button className="primary-button compact-button" type="button" onClick={handleSave} disabled={isSaving}>
                            {isSaving ? "保存中..." : "保存"}
                          </button>
                        </>
                      ) : (
                        <>
                          <button className="secondary-button compact-button" type="button" onClick={() => void handleEdit(group.group_key)}>
                            编辑
                          </button>
                          <button
                            className={`compact-button ${group.is_active ? "warning-button" : "success-button"}`}
                            type="button"
                            onClick={() => void handleToggle(group.group_key)}
                          >
                            {group.is_active ? "停用" : "启用"}
                          </button>
                          {isCustom && (
                            <button
                              className="compact-button danger-button"
                              type="button"
                              onClick={() => setDeleting({ groupKey: group.group_key, label: group.label })}
                            >
                              删除
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </div>

                  {group.description && <p className="config-description">{group.description}</p>}

                  {isEditing ? (
                    <div className="config-edit-form">
                      {groups
                        .find((g) => g.group_key === group.group_key)
                        ?.items.map((item) => (
                          <ConfigInput key={item.key} item={item} value={editing?.items[item.key] ?? ""} onChange={updateItemValue} />
                        ))}
                    </div>
                  ) : (
                    <div className="config-preview">
                      {group.items.map((item) => (
                        <div key={item.key} className="config-preview-item">
                          <span className="config-preview-label">{item.label}</span>
                          <span className="config-preview-value">{maskValue(item.value)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}

      {editing && (
        <div className="config-edit-hint">
          <p>
            ️ 注意：{CATEGORY_SINGLETON[groups.find((g) => g.group_key === editing.groupKey)?.category as keyof typeof CATEGORY_SINGLETON]
              ? "此类密钥只能激活一个，启用后将自动停用其他同类密钥"
              : "生图类密钥可同时激活多个"}
          </p>
        </div>
      )}

      {/* 添加供应商弹窗 */}
      {adding && (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setAdding(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="添加供应商" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">供应商</p>
                <h3>添加供应商</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setAdding(null)} aria-label="关闭添加供应商弹窗">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <div className="api-type-hint">
                <p><strong>接口类型：OpenAI 兼容接口</strong></p>
                <p><small>支持 OpenAI SDK 格式，兼容 /v1/chat/completions 和 /v1/images/generations 端点</small></p>
              </div>
              
              <label className="admin-form-field">
                <span>供应商标识</span>
                <small>例如：myopenai（只能包含小写字母、数字和下划线）</small>
                <input
                  className="search-input"
                  placeholder="例如：myopenai"
                  value={adding.group_key}
                  onChange={(event) => setAdding({ ...adding, group_key: event.target.value })}
                />
              </label>
              <label className="admin-form-field">
                <span>显示名称</span>
                <small>例如：我的 OpenAI 平台</small>
                <input
                  className="search-input"
                  placeholder="例如：我的 OpenAI 平台"
                  value={adding.label}
                  onChange={(event) => setAdding({ ...adding, label: event.target.value })}
                />
              </label>
              <label className="admin-form-field">
                <span>分类</span>
                <select className="search-input" value={adding.category} onChange={(event) => setAdding({ ...adding, category: event.target.value as any })}>
                  <option value="image">生图密钥</option>
                  <option value="agent">Agent 对话</option>
                  <option value="vision">视觉分析</option>
                  <option value="multiview">多视图反推</option>
                </select>
              </label>
              <label className="admin-form-field">
                <span>Base URL</span>
                <small>例如：https://api.openai.com/v1 或 http://localhost:8080/v1</small>
                <input
                  className="search-input"
                  placeholder="https://api.openai.com/v1"
                  value={adding.base_url}
                  onChange={(event) => setAdding({ ...adding, base_url: event.target.value })}
                />
              </label>
              <label className="admin-form-field">
                <span>API Key</span>
                <input
                  className="search-input"
                  type="password"
                  placeholder="sk-..."
                  value={adding.api_key}
                  onChange={(event) => setAdding({ ...adding, api_key: event.target.value })}
                />
              </label>
              
              <div className="models-config-section">
                <div className="models-header">
                  <span>模型配置</span>
                  <button className="secondary-button compact-button" type="button" onClick={() => setAdding({ ...adding, models: [...adding.models, { id: "", label: "" }] })}>
                    + 添加模型
                  </button>
                </div>
                {adding.models.map((model, index) => (
                  <div key={index} className="model-config-row">
                    <input
                      className="search-input"
                      placeholder="模型 ID (如 gpt-4o)"
                      value={model.id}
                      onChange={(event) => {
                        const newModels = [...adding.models];
                        newModels[index] = { ...model, id: event.target.value };
                        setAdding({ ...adding, models: newModels });
                      }}
                    />
                    <input
                      className="search-input"
                      placeholder="显示名称 (如 GPT-4o)"
                      value={model.label}
                      onChange={(event) => {
                        const newModels = [...adding.models];
                        newModels[index] = { ...model, label: event.target.value };
                        setAdding({ ...adding, models: newModels });
                      }}
                    />
                    {adding.models.length > 1 && (
                      <button
                        className="danger-button compact-button"
                        type="button"
                        onClick={() => {
                          const newModels = adding.models.filter((_, i) => i !== index);
                          setAdding({ ...adding, models: newModels });
                        }}
                      >
                        ×
                      </button>
                    )}
                  </div>
                ))}
              </div>
              
              <label className="admin-form-field">
                <span>超时时间（秒）</span>
                <input
                  className="search-input"
                  type="number"
                  value={adding.timeout}
                  onChange={(event) => setAdding({ ...adding, timeout: parseInt(event.target.value) || 600 })}
                />
              </label>
              <p className="muted">添加后可以在生图模块中使用该供应商的模型。</p>
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setAdding(null)}>
                取消
              </button>
              <button className="primary-button compact-button" type="button" onClick={() => void handleAddSubmit()}>
                确认添加
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {deleting && (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setDeleting(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="删除供应商确认" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">确认删除</p>
                <h3>删除供应商: {deleting.label}</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setDeleting(null)} aria-label="关闭删除确认弹窗">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="muted">确定要删除供应商"{deleting.label}"吗？此操作不可撤销，所有相关配置将被清除。</p>
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setDeleting(null)}>
                取消
              </button>
              <button className="danger-button compact-button" type="button" onClick={() => void handleDeleteConfirm()}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const BUILTIN_GROUP_KEYS = ["apiyi", "closeai", "ttapi", "agent_llm", "agent_vision", "multiview_prompt"];

function ConfigInput({ item, value, onChange }: { item: ConfigKeyItem; value: string; onChange: (key: string, value: string) => void }) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    onChange(item.key, e.target.value);
  };

  return (
    <label className="config-field">
      <span>
        {item.label}
        {item.required && <span className="config-required">*</span>}
      </span>
      {item.type === "select" && item.options ? (
        <select className="search-input" value={value} onChange={handleChange}>
          {item.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <input
          className="search-input"
          type={item.type === "password" ? "password" : item.type === "number" ? "number" : "text"}
          placeholder={item.placeholder}
          value={value}
          onChange={handleChange}
        />
      )}
    </label>
  );
}

function maskValue(value: string | null): string {
  if (!value) return "未配置";
  if (value.length <= 8) return value;
  return value.slice(0, 6) + "••••";
}
