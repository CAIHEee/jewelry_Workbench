"""
密钥配置管理服务 v2
支持动态添加、删除、启用/停用供应商配置
"""

import os
import re
from pathlib import Path
from typing import Any
from copy import deepcopy

from app.core.config import ENV_FILE_PATH, get_settings
from app.schemas.admin import (
    CATEGORY_LABELS,
    CATEGORY_SINGLETON,
    ConfigGroup,
    ConfigKeyCategory,
    ConfigKeyItem,
    ConfigListResponse,
    KEY_TYPE_TO_CATEGORY,
)


# ==================== 内置分组模板 ====================

BUILTIN_GROUP_TEMPLATES: list[dict[str, Any]] = [
    {
        "group_key": "apiyi",
        "label": "APIYI 平台",
        "category": ConfigKeyCategory.IMAGE,
        "description": "APIYI 生图平台，支持 gpt-image-2 和 Gemini 模型",
        "is_builtin": True,
        "items": [
            {"key": "APIYI_API_KEY", "label": "API Key", "type": "password", "required": True, "is_secret": True},
            {"key": "APIYI_BASE_URL", "label": "Base URL", "type": "text", "required": False, "default": "https://api.apiyi.com", "is_secret": False},
            {"key": "APIYI_OPENAI_BASE_URL", "label": "OpenAI Base URL", "type": "text", "required": False, "default": "https://api.apiyi.com/v1", "is_secret": False},
            {"key": "APIYI_GEMINI_BASE_URL", "label": "Gemini Base URL", "type": "text", "required": False, "default": "https://api.apiyi.com/v1beta", "is_secret": False},
            {"key": "APIYI_TIMEOUT_SECONDS", "label": "超时时间 (秒)", "type": "number", "required": False, "default": "700", "is_secret": False},
        ],
    },
    {
        "group_key": "closeai",
        "label": "CloseAI 平台",
        "category": ConfigKeyCategory.IMAGE,
        "description": "CloseAI OpenAI 兼容平台，支持 gpt-image-2",
        "is_builtin": True,
        "items": [
            {"key": "CLOSEAI_API_KEY", "label": "API Key", "type": "password", "required": True, "is_secret": True},
            {"key": "CLOSEAI_BASE_URL", "label": "Base URL", "type": "text", "required": False, "default": "https://api.openai-proxy.org/v1", "is_secret": False},
            {"key": "CLOSEAI_TIMEOUT_SECONDS", "label": "超时时间 (秒)", "type": "number", "required": False, "default": "1200", "is_secret": False},
        ],
    },
    {
        "group_key": "ttapi",
        "label": "TTAPI 平台",
        "category": ConfigKeyCategory.IMAGE,
        "description": "TTAPI 生图平台，支持 Gemini 模型",
        "is_builtin": True,
        "items": [
            {"key": "TTAPI_API_KEY", "label": "API Key", "type": "password", "required": True, "is_secret": True},
            {"key": "TTAPI_OPENAI_BASE_URL", "label": "OpenAI Base URL", "type": "text", "required": False, "default": "https://api.ttapi.org", "is_secret": False},
            {"key": "TTAPI_TIMEOUT_SECONDS", "label": "超时时间 (秒)", "type": "number", "required": False, "default": "120", "is_secret": False},
        ],
    },
    {
        "group_key": "agent_llm",
        "label": "Agent 对话",
        "category": ConfigKeyCategory.AGENT,
        "description": "Agent 多模态对话模型，用于设计规划、工具调用等",
        "is_builtin": True,
        "is_singleton": True,
        "items": [
            {"key": "AGENT_LLM_BASE_URL", "label": "Base URL", "type": "text", "required": True, "default": "https://dashscope.aliyuncs.com/compatible-mode", "is_secret": False},
            {"key": "AGENT_LLM_API_KEY", "label": "API Key", "type": "password", "required": True, "is_secret": True},
            {"key": "AGENT_LLM_MODEL", "label": "模型", "type": "text", "required": True, "default": "qwen3.6-flash", "is_secret": False},
            {"key": "AGENT_LLM_TIMEOUT_SECONDS", "label": "超时时间 (秒)", "type": "number", "required": False, "default": "60", "is_secret": False},
            {"key": "AGENT_LLM_STRICT_TOOLS", "label": "严格工具调用", "type": "select", "required": False, "default": "true", "options": ["true", "false"], "is_secret": False},
        ],
    },
    {
        "group_key": "agent_vision",
        "label": "Agent 视觉分析",
        "category": ConfigKeyCategory.VISION,
        "description": "裸石/玉石视觉分析模型，留空则复用 Agent 对话配置",
        "is_builtin": True,
        "is_singleton": True,
        "activation_key": "AGENT_VISION_LLM_API_KEY",
        "items": [
            {"key": "AGENT_VISION_LLM_BASE_URL", "label": "Base URL (留空=复用 Agent)", "type": "text", "required": False, "default": "", "is_secret": False},
            {"key": "AGENT_VISION_LLM_API_KEY", "label": "API Key (留空=复用 Agent)", "type": "password", "required": False, "default": "", "is_secret": True},
            {"key": "AGENT_VISION_LLM_MODEL", "label": "模型 (留空=复用 Agent)", "type": "text", "required": False, "default": "", "is_secret": False},
        ],
    },
    {
        "group_key": "multiview_prompt",
        "label": "多视图反推",
        "category": ConfigKeyCategory.MULTIVIEW,
        "description": "多视图提示词反推模型，使用 DashScope OpenAI 兼容接口",
        "is_builtin": True,
        "is_singleton": True,
        "activation_key": "DASHSCOPE_API_KEY",
        "items": [
            {"key": "DASHSCOPE_API_KEY", "label": "DashScope API Key (留空=复用 Agent)", "type": "password", "required": False, "default": "", "is_secret": True},
            {"key": "MULTI_VIEW_PROMPT_MODEL", "label": "模型", "type": "text", "required": True, "default": "qwen3-vl-plus", "is_secret": False},
            {"key": "MULTI_VIEW_PROMPT_THINKING_BUDGET", "label": "Thinking Budget", "type": "number", "required": False, "default": "81920", "is_secret": False},
        ],
    },
]


def _mask_value(value: str | None, is_secret: bool = True) -> str | None:
    """脱敏处理"""
    if value is None or not value.strip():
        return None
    value = value.strip()
    
    if value.startswith(("http://", "https://")):
        return value
    if value.replace(".", "").replace("-", "").isdigit():
        return value
    if value.lower() in ("true", "false"):
        return value
    
    if is_secret and len(value) > 8:
        return value[:6] + "•" * min(8, len(value) - 6)
    elif is_secret:
        return value[:4] + "••••" if len(value) > 4 else "••••"
    
    return value


def _parse_env_file() -> dict[str, str]:
    """解析 .env 文件"""
    result = {}
    if not ENV_FILE_PATH.exists():
        return result

    with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
            if match:
                key = match.group(1)
                value = match.group(2).strip().strip('"').strip("'")
                result[key] = value

    return result


def _get_custom_groups() -> list[dict[str, Any]]:
    """获取自定义分组（从 .env 中解析）"""
    env_data = _parse_env_file()
    custom_groups = []
    
    # 查找所有 CUSTOM_GROUP_* 标记
    group_keys = set()
    for key in env_data:
        if key.startswith("CUSTOM_GROUP_") and key.endswith("_ACTIVE"):
            # 提取 group_key，如 CUSTOM_GROUP_myopenai_ACTIVE -> myopenai
            parts = key.split("_")
            if len(parts) >= 4:
                group_key = "_".join(parts[2:-1]).lower()
                group_keys.add(group_key)
    
    for group_key in group_keys:
        prefix = f"CUSTOM_GROUP_{group_key.upper()}_"
        label = env_data.get(f"{prefix}LABEL", group_key)
        category = env_data.get(f"{prefix}CATEGORY", "image")
        base_url = env_data.get(f"{prefix}BASE_URL", "")
        api_key = env_data.get(f"{prefix}API_KEY", "")
        models = env_data.get(f"{prefix}MODELS", "")
        timeout = env_data.get(f"{prefix}TIMEOUT", "600")
        is_active = env_data.get(f"{prefix}ACTIVE", "true").lower() == "true"
        interface_type = env_data.get(f"{prefix}INTERFACE_TYPE", "openai_compat")
        
        custom_groups.append({
            "group_key": group_key,
            "label": label,
            "category": category,
            "is_builtin": False,
            "is_active": is_active,
            "interface_type": interface_type,
            "items": [
                {"key": f"{prefix}BASE_URL", "label": "Base URL", "type": "text", "required": True, "default": base_url, "is_secret": False},
                {"key": f"{prefix}API_KEY", "label": "API Key", "type": "password", "required": True, "default": api_key, "is_secret": True},
                {"key": f"{prefix}MODELS", "label": "模型配置", "type": "text", "required": False, "default": models, "is_secret": False, "placeholder": "gpt-4o:GPT-4o,flux-pro:Flux Pro"},
                {"key": f"{prefix}TIMEOUT", "label": "超时时间 (秒)", "type": "number", "required": False, "default": timeout, "is_secret": False},
            ],
        })
    
    return custom_groups


def _is_builtin_group(group_key: str) -> bool:
    """检查是否为内置分组"""
    return any(t["group_key"] == group_key for t in BUILTIN_GROUP_TEMPLATES)


def _get_all_groups() -> list[dict[str, Any]]:
    """获取所有分组（内置 + 自定义）"""
    return BUILTIN_GROUP_TEMPLATES + _get_custom_groups()


def _write_env_file(env_data: dict[str, str]) -> None:
    """写入 .env 文件，支持删除不存在的 key"""
    existing_lines = []
    if ENV_FILE_PATH.exists():
        with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()
    
    # 收集所有在 env_data 中的 key
    new_lines = []
    written_keys = set()
    
    for line in existing_lines:
        stripped = line.strip()
        match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', stripped)
        if match:
            key = match.group(1)
            if key in env_data:
                # 更新值
                new_lines.append(f"{key}={env_data[key]}\n")
                written_keys.add(key)
            else:
                # key 不在 env_data 中，删除这一行（跳过）
                pass
        else:
            # 注释或空行，保留
            new_lines.append(line)
    
    # 添加新配置 (不存在的 key)
    for key, value in env_data.items():
        if key not in written_keys:
            new_lines.append(f"{key}={value}\n")
    
    with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _create_custom_group(group_key: str, label: str, category: str, base_url: str, api_key: str, model: str, timeout: int) -> dict[str, Any]:
    """创建自定义分组配置"""
    if _is_builtin_group(group_key):
        raise ValueError(f"组名 '{group_key}' 已被内置分组占用。")
    
    # 验证 group_key 格式
    if not re.match(r'^[a-z][a-z0-9_]*$', group_key):
        raise ValueError("组名只能包含小写字母、数字和下划线，且必须以字母开头。")
    
    prefix = f"CUSTOM_GROUP_{group_key.upper()}_"
    return {
        "group_key": group_key,
        "label": label,
        "category": category,
        "is_builtin": False,
        "is_active": True,
        "items": [
            {"key": f"{prefix}LABEL", "label": "显示名称", "type": "text", "required": True, "default": label, "is_secret": False},
            {"key": f"{prefix}CATEGORY", "label": "分类", "type": "select", "required": True, "default": category, "options": ["image", "agent", "vision", "multiview"], "is_secret": False},
            {"key": f"{prefix}BASE_URL", "label": "Base URL", "type": "text", "required": True, "default": base_url, "is_secret": False},
            {"key": f"{prefix}API_KEY", "label": "API Key", "type": "password", "required": True, "default": api_key, "is_secret": True},
            {"key": f"{prefix}MODEL", "label": "默认模型", "type": "text", "required": False, "default": model, "is_secret": False},
            {"key": f"{prefix}TIMEOUT", "label": "超时时间 (秒)", "type": "number", "required": False, "default": str(timeout), "is_secret": False},
            {"key": f"{prefix}ACTIVE", "label": "是否启用", "type": "select", "required": False, "default": "true", "options": ["true", "false"], "is_secret": False},
        ],
    }


def _delete_custom_group(group_key: str) -> None:
    """删除自定义分组配置"""
    if _is_builtin_group(group_key):
        raise ValueError(f"内置分组 '{group_key}' 不允许删除，请使用停用功能。")
    
    # 检查是否存在
    env_data = _parse_env_file()
    prefix = f"CUSTOM_GROUP_{group_key.upper()}_"
    exists = any(key.startswith(prefix) for key in env_data)
    if not exists:
        raise ValueError(f"自定义分组 '{group_key}' 不存在。")
    
    # 从 .env 中删除所有相关配置
    keys_to_delete = [key for key in env_data if key.startswith(prefix)]
    for key in keys_to_delete:
        del env_data[key]
    
    _write_env_file(env_data)
    get_settings.cache_clear()


def _get_current_env() -> dict[str, str]:
    """获取当前环境变量值（优先从文件读取，其次从进程环境）"""
    # 优先从 .env 文件读取最新值
    result = _parse_env_file()
    
    # 如果文件中没有，再从进程环境变量读取
    settings = get_settings()
    for template in BUILTIN_GROUP_TEMPLATES:
        for item in template["items"]:
            key = item["key"]
            if key not in result:
                value = os.environ.get(key)
                if value is None:
                    value = getattr(settings, key.lower(), None)
                    if value is not None:
                        value = str(value)
                if value is not None:
                    result[key] = value
    
    return result


def _is_group_active(group_def: dict[str, Any], env_data: dict[str, str]) -> bool:
    """判断分组是否激活"""
    activation_key = group_def.get("activation_key")
    if activation_key:
        value = env_data.get(activation_key, "").strip()
        return bool(value)
    
    # 生图类：检查显式的 ACTIVE 标记
    active_key = f"{group_def['group_key'].upper()}_ACTIVE"
    if active_key in env_data:
        return env_data[active_key].lower() == "true"
    
    # 兼容旧逻辑：检查是否有 API Key
    for item in group_def["items"]:
        if "api_key" in item["key"].lower():
            value = env_data.get(item["key"], "").strip()
            return bool(value)
    
    return False


def _get_all_group_keys() -> list[str]:
    """获取所有分组的 key（包括内置和自定义）"""
    env_data = _parse_env_file()
    keys = []
    
    # 内置分组
    for template in BUILTIN_GROUP_TEMPLATES:
        keys.append(template["group_key"])
    
    # 检查是否有自定义分组（通过特殊标记识别）
    # 这里简化处理，只返回内置分组
    # 后续可以扩展支持自定义分组
    
    return keys


class ConfigService:
    """密钥配置服务 v2"""
    
    def get_config_list(self, show_raw: bool = False) -> ConfigListResponse:
        """获取所有密钥配置"""
        env_data = _get_current_env()
        groups = []
        
        # 内置分组
        for template in BUILTIN_GROUP_TEMPLATES:
            group_def = deepcopy(template)
            is_active = _is_group_active(group_def, env_data)
            items = []
            
            for item_def in group_def["items"]:
                key = item_def["key"]
                raw_value = env_data.get(key, item_def.get("default", ""))
                is_secret = item_def.get("is_secret", True)
                
                if show_raw:
                    value = raw_value
                    value_raw = raw_value
                else:
                    value = _mask_value(raw_value, is_secret=is_secret)
                    value_raw = None
                
                item = ConfigKeyItem(
                    key=key,
                    label=item_def["label"],
                    value=value,
                    value_raw=value_raw,
                    placeholder=item_def.get("default", ""),
                    type=item_def.get("type", "text"),
                    required=item_def.get("required", False),
                    is_secret=is_secret,
                )
                if "options" in item_def:
                    item.options = item_def["options"]
                
                items.append(item)
            
            group = ConfigGroup(
                group_key=group_def["group_key"],
                label=group_def["label"],
                category=group_def["category"],
                description=group_def.get("description", ""),
                is_active=is_active,
                interface_type="builtin",
                items=items,
            )
            groups.append(group)
        
        # 自定义分组
        custom_groups = _get_custom_groups()
        for group_def in custom_groups:
            is_active = group_def.get("is_active", True)
            interface_type = group_def.get("interface_type", "openai_compat")
            items = []
            
            for item_def in group_def["items"]:
                key = item_def["key"]
                raw_value = env_data.get(key, item_def.get("default", ""))
                is_secret = item_def.get("is_secret", True)
                
                if show_raw:
                    value = raw_value
                    value_raw = raw_value
                else:
                    value = _mask_value(raw_value, is_secret=is_secret)
                    value_raw = None
                
                item = ConfigKeyItem(
                    key=key,
                    label=item_def["label"],
                    value=value,
                    value_raw=value_raw,
                    placeholder=item_def.get("default", ""),
                    type=item_def.get("type", "text"),
                    required=item_def.get("required", False),
                    is_secret=is_secret,
                )
                if "options" in item_def:
                    item.options = item_def["options"]
                
                items.append(item)
            
            group = ConfigGroup(
                group_key=group_def["group_key"],
                label=group_def["label"],
                category=group_def["category"],
                description="自定义供应商 (OpenAI 兼容接口)",
                is_active=is_active,
                interface_type=interface_type,
                items=items,
            )
            groups.append(group)
        
        return ConfigListResponse(groups=groups)
    
    def update_group(self, group_key: str, items: dict[str, str], is_active: bool | None = None) -> ConfigGroup:
        """更新密钥分组"""
        group_def = None
        for t in BUILTIN_GROUP_TEMPLATES:
            if t["group_key"] == group_key:
                group_def = deepcopy(t)
                break
        
        if group_def is None:
            raise ValueError(f"Unknown group key: {group_key}")
        
        env_data = _parse_env_file()
        
        # 更新配置项
        for key, value in items.items():
            valid_keys = [item["key"] for item in group_def["items"]]
            if key in valid_keys:
                if value.strip():
                    env_data[key] = value.strip()
                elif key in env_data:
                    del env_data[key]
        
        # 处理激活/停用
        if is_active is not None:
            category = group_def["category"]
            activation_key = group_def.get("activation_key")
            
            if is_active:
                # 激活
                if activation_key:
                    # 有 activation_key 的分组（Agent/Vision/Multiview）
                    # 如果是 singleton，停用同类其他
                    if group_def.get("is_singleton", CATEGORY_SINGLETON.get(category, False)):
                        for other_template in BUILTIN_GROUP_TEMPLATES:
                            if (other_template["group_key"] != group_key and 
                                other_template["category"] == category):
                                other_activation_key = other_template.get("activation_key")
                                if other_activation_key and other_activation_key in env_data:
                                    del env_data[other_activation_key]
                                for item in other_template["items"]:
                                    if item["key"] in env_data and item["key"] != other_activation_key:
                                        if "api_key" not in item["key"].lower():
                                            del env_data[item["key"]]
                else:
                    # 生图类密钥：使用 ACTIVE 标记
                    active_key = f"{group_key.upper()}_ACTIVE"
                    env_data[active_key] = "true"
            else:
                # 停用
                if activation_key:
                    if activation_key in env_data:
                        del env_data[activation_key]
                    for item in group_def["items"]:
                        if item["key"] in env_data and item["key"] != activation_key:
                            del env_data[item["key"]]
                else:
                    # 生图类密钥：设置 ACTIVE 标记为 false
                    active_key = f"{group_key.upper()}_ACTIVE"
                    env_data[active_key] = "false"
        
        _write_env_file(env_data)
        get_settings.cache_clear()
        
        return self.get_config_list().groups[[g.group_key for g in self.get_config_list().groups].index(group_key)]
    
    def create_group(self, group_key: str, label: str, category: str, base_url: str, api_key: str, models: str, timeout: int) -> ConfigGroup:
        """创建自定义供应商配置（OpenAI 兼容接口）"""
        if _is_builtin_group(group_key):
            raise ValueError(f"组名 '{group_key}' 已被内置分组占用。")
        
        # 验证 group_key 格式
        if not re.match(r'^[a-z][a-z0-9_]*$', group_key):
            raise ValueError("组名只能包含小写字母、数字和下划线，且必须以字母开头。")
        
        prefix = f"CUSTOM_GROUP_{group_key.upper()}_"
        
        # 读取现有配置
        env_data = _parse_env_file()
        
        # 写入新配置
        env_data[f"{prefix}LABEL"] = label
        env_data[f"{prefix}CATEGORY"] = category
        env_data[f"{prefix}BASE_URL"] = base_url
        env_data[f"{prefix}API_KEY"] = api_key
        env_data[f"{prefix}MODELS"] = models  # 格式：model_id:label,model_id:label
        env_data[f"{prefix}TIMEOUT"] = str(timeout)
        env_data[f"{prefix}ACTIVE"] = "true"
        env_data[f"{prefix}INTERFACE_TYPE"] = "openai_compat"  # 明确标注接口类型
        
        _write_env_file(env_data)
        get_settings.cache_clear()
        
        return self.get_config_list().groups[[g.group_key for g in self.get_config_list().groups].index(group_key)]
    
    def delete_group(self, group_key: str) -> None:
        """删除自定义供应商配置"""
        if _is_builtin_group(group_key):
            raise ValueError(f"内置分组 '{group_key}' 不允许删除，请使用停用功能。")
        
        # 检查是否存在
        env_data = _parse_env_file()
        prefix = f"CUSTOM_GROUP_{group_key.upper()}_"
        exists = any(key.startswith(prefix) for key in env_data)
        if not exists:
            raise ValueError(f"自定义分组 '{group_key}' 不存在。")
        
        # 从 .env 中删除所有相关配置
        keys_to_delete = [key for key in env_data if key.startswith(prefix)]
        for key in keys_to_delete:
            del env_data[key]
        
        _write_env_file(env_data)
        get_settings.cache_clear()
    
    def get_group(self, group_key: str) -> ConfigGroup:
        """获取单个密钥分组"""
        config = self.get_config_list()
        for group in config.groups:
            if group.group_key == group_key:
                return group
        raise ValueError(f"Unknown group key: {group_key}")
    
    def get_group_raw(self, group_key: str) -> ConfigGroup:
        """获取单个密钥分组 (明文)"""
        config = self.get_config_list(show_raw=True)
        for group in config.groups:
            if group.group_key == group_key:
                return group
        raise ValueError(f"Unknown group key: {group_key}")
    
    def get_active_providers(self) -> dict[str, list[str]]:
        """获取当前激活的供应商列表，按分类返回"""
        env_data = _get_current_env()
        result = {
            ConfigKeyCategory.IMAGE: [],
            ConfigKeyCategory.AGENT: [],
            ConfigKeyCategory.VISION: [],
            ConfigKeyCategory.MULTIVIEW: [],
        }
        
        for template in BUILTIN_GROUP_TEMPLATES:
            if _is_group_active(template, env_data):
                category = template["category"]
                result[category].append(template["group_key"])
        
        return result
    
    def get_model_catalog_for_frontend(self) -> dict[str, Any]:
        """为前端提供模型目录，只包含激活的供应商"""
        active_providers = self.get_active_providers()
        
        catalog = {
            "image_models": [],
            "agent_model": None,
            "vision_model": None,
            "multiview_model": None,
        }
        
        # 生图模型（可能有多个）
        for provider in active_providers[ConfigKeyCategory.IMAGE]:
            template = next((t for t in BUILTIN_GROUP_TEMPLATES if t["group_key"] == provider), None)
            if template:
                catalog["image_models"].append({
                    "provider": provider,
                    "label": template["label"],
                    "category": template["category"],
                })
        
        # Agent 模型（只能一个）
        if active_providers[ConfigKeyCategory.AGENT]:
            provider = active_providers[ConfigKeyCategory.AGENT][0]
            template = next((t for t in BUILTIN_GROUP_TEMPLATES if t["group_key"] == provider), None)
            if template:
                env_data = _get_current_env()
                catalog["agent_model"] = {
                    "provider": provider,
                    "label": template["label"],
                    "model": env_data.get("AGENT_LLM_MODEL", ""),
                }
        
        # 视觉模型
        if active_providers[ConfigKeyCategory.VISION]:
            provider = active_providers[ConfigKeyCategory.VISION][0]
            catalog["vision_model"] = {"provider": provider}
        
        # 多视图反推
        if active_providers[ConfigKeyCategory.MULTIVIEW]:
            provider = active_providers[ConfigKeyCategory.MULTIVIEW][0]
            env_data = _get_current_env()
            catalog["multiview_model"] = {
                "provider": provider,
                "model": env_data.get("MULTI_VIEW_PROMPT_MODEL", ""),
            }
        
        return catalog
