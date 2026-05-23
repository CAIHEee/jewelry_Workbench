from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.auth import ModulePermissionItem


class AdminUserBase(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = None
    is_disabled: bool = False


class AdminUserCreate(AdminUserBase):
    password: str = Field(min_length=6)


class AdminUserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = None
    is_disabled: bool | None = None


class AdminUser(BaseModel):
    id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    role: str
    is_disabled: bool
    created_at: datetime
    permissions: list[ModulePermissionItem]


class AdminUserListResponse(BaseModel):
    items: list[AdminUser]


class AdminSystemStatus(BaseModel):
    backend_status: str
    database_status: str
    storage_mode: str
    storage_path: str
    oss_compat_enabled: bool
    environment: str


class UserPermissionUpdateItem(BaseModel):
    module_key: str
    is_enabled: bool


class UserPermissionUpdateRequest(BaseModel):
    items: list[UserPermissionUpdateItem]


# ==================== 密钥管理 Schema ====================

class ConfigKeyType:
    """密钥类型枚举"""
    IMAGE_APIYI = "image_apiyi"
    IMAGE_CLOSEAI = "image_closeai"
    IMAGE_TTAPI = "image_ttapi"
    AGENT_LLM = "agent_llm"
    AGENT_VISION = "agent_vision"
    MULTIVIEW_PROMPT = "multiview_prompt"


class ConfigKeyCategory:
    """密钥分类"""
    IMAGE = "image"        # 生图密钥 (可多个)
    AGENT = "agent"        # Agent 对话 (只能一个)
    VISION = "vision"      # 视觉分析 (只能一个)
    MULTIVIEW = "multiview"  # 多视图反推 (只能一个)


CATEGORY_LABELS = {
    ConfigKeyCategory.IMAGE: "生图密钥",
    ConfigKeyCategory.AGENT: "Agent 对话",
    ConfigKeyCategory.VISION: "视觉分析",
    ConfigKeyCategory.MULTIVIEW: "多视图反推",
}

# 类型到分类的映射
KEY_TYPE_TO_CATEGORY = {
    ConfigKeyType.IMAGE_APIYI: ConfigKeyCategory.IMAGE,
    ConfigKeyType.IMAGE_CLOSEAI: ConfigKeyCategory.IMAGE,
    ConfigKeyType.IMAGE_TTAPI: ConfigKeyCategory.IMAGE,
    ConfigKeyType.AGENT_LLM: ConfigKeyCategory.AGENT,
    ConfigKeyType.AGENT_VISION: ConfigKeyCategory.VISION,
    ConfigKeyType.MULTIVIEW_PROMPT: ConfigKeyCategory.MULTIVIEW,
}

# 分类是否只能激活一个
CATEGORY_SINGLETON = {
    ConfigKeyCategory.IMAGE: False,
    ConfigKeyCategory.AGENT: True,
    ConfigKeyCategory.VISION: True,
    ConfigKeyCategory.MULTIVIEW: True,
}


class ConfigKeyItem(BaseModel):
    """单个密钥配置项"""
    key: str          # 环境变量名，如 APIYI_API_KEY
    label: str        # 显示名称，如 "API Key"
    value: str | None = None  # 当前值 (脱敏后)
    value_raw: str | None = None  # 原始值 (仅编辑时用)
    placeholder: str = ""  # 占位符
    type: str = "text"  # 输入类型: text, password, number, select
    required: bool = True  # 是否必填
    options: list[str] | None = None  # 下拉选项 (select 类型用)
    is_secret: bool = True  # 是否需要脱敏


class ConfigGroup(BaseModel):
    """密钥分组"""
    group_key: str           # 分组键，如 apiyi
    label: str               # 分组显示名，如 "APIYI 平台"
    category: str            # 分类: image/agent/vision/multiview
    description: str = ""    # 描述
    is_active: bool = True   # 是否启用
    interface_type: str = "builtin"  # 接口类型：builtin/openai_compat
    items: list[ConfigKeyItem]  # 配置项列表


class ConfigListResponse(BaseModel):
    """密钥列表响应"""
    groups: list[ConfigGroup]


class ConfigGroupUpdate(BaseModel):
    """更新密钥分组"""
    group_key: str
    items: dict[str, str]  # key -> value 映射
    is_active: bool | None = None


class ConfigToggleResponse(BaseModel):
    """切换密钥状态响应"""
    group_key: str
    is_active: bool
    message: str


class ConfigGroupCreate(BaseModel):
    """添加新供应商配置"""
    group_key: str  # 供应商标识，如 my_openai
    label: str      # 显示名称，如 "我的 OpenAI 平台"
    category: str   # 分类：image/agent/vision/multiview
    base_url: str   # Base URL
    api_key: str    # API Key
    models: str     # 模型配置，格式：model_id:label,model_id:label
    timeout: int = 600  # 超时时间（秒）


class ConfigGroupDelete(BaseModel):
    """删除供应商配置"""
    group_key: str  # 供应商标识
