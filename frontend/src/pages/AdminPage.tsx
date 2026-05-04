import { useEffect, useMemo, useState } from "react";

import { SectionHeader } from "../components/SectionHeader";
import { useViewport } from "../hooks/useViewport";
import type { AdminSystemStatus, AdminUser } from "../types/admin";
import type { ModulePermissionItem } from "../types/auth";

interface AdminPageProps {
  users: AdminUser[];
  systemStatus: AdminSystemStatus | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
  onCreateUser: (payload: { username: string; display_name?: string; email?: string; password: string }) => Promise<void>;
  onToggleDisabled: (user: AdminUser) => Promise<void>;
  onDeleteUser: (user: AdminUser) => Promise<void>;
  onResetPassword: (user: AdminUser, password: string) => Promise<void>;
  onSavePermissions: (user: AdminUser, permissions: ModulePermissionItem[]) => Promise<void>;
}

type ResetPasswordState = {
  user: AdminUser;
  password: string;
  confirmPassword: string;
  submitting: boolean;
  error: string | null;
} | null;

type CreateUserState = {
  username: string;
  display_name: string;
  email: string;
  password: string;
  submitting: boolean;
  error: string | null;
} | null;

type AdminToastState = {
  type: "success" | "error";
  message: string;
} | null;

type PendingUserSwitchState = {
  user: AdminUser;
} | null;

type DeleteUserState = {
  user: AdminUser;
  submitting: boolean;
  error: string | null;
} | null;

export function AdminPage({
  users,
  systemStatus,
  loading,
  error,
  onRefresh,
  onCreateUser,
  onToggleDisabled,
  onDeleteUser,
  onResetPassword,
  onSavePermissions,
}: AdminPageProps) {
  const { isMobile } = useViewport();
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [draftPermissions, setDraftPermissions] = useState<Record<string, boolean>>({});
  const [createUserState, setCreateUserState] = useState<CreateUserState>(null);
  const [resetPasswordState, setResetPasswordState] = useState<ResetPasswordState>(null);
  const [permissionsEditing, setPermissionsEditing] = useState(false);
  const [savingPermissions, setSavingPermissions] = useState(false);
  const [toast, setToast] = useState<AdminToastState>(null);
  const [pendingUserSwitch, setPendingUserSwitch] = useState<PendingUserSwitchState>(null);
  const [deleteUserState, setDeleteUserState] = useState<DeleteUserState>(null);

  const selectedUser = useMemo(() => users.find((item) => item.id === selectedUserId) ?? users[0] ?? null, [selectedUserId, users]);
  const hasUnsavedPermissionChanges = useMemo(() => {
    if (!selectedUser || selectedUser.role === "root" || !permissionsEditing) return false;
    return selectedUser.permissions.some((permission) => Boolean(draftPermissions[permission.module_key]) !== permission.is_enabled);
  }, [draftPermissions, permissionsEditing, selectedUser]);

  useEffect(() => {
    if (!selectedUser && users[0]) {
      setSelectedUserId(users[0].id);
      return;
    }
    if (selectedUser) {
      setDraftPermissions(Object.fromEntries(selectedUser.permissions.map((item) => [item.module_key, item.is_enabled])));
      setPermissionsEditing(false);
    }
  }, [selectedUser, users]);

  useEffect(() => {
    if (!toast) return;
    const timeoutId = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timeoutId);
  }, [toast]);

  function applyUserSelection(user: AdminUser) {
    setSelectedUserId(user.id);
    setDraftPermissions(Object.fromEntries(user.permissions.map((item) => [item.module_key, item.is_enabled])));
    setPermissionsEditing(false);
  }

  function validateCreateUserForm() {
    if (!createUserState) return "表单状态无效。";
    const username = createUserState.username.trim();
    const displayName = createUserState.display_name.trim();
    const email = createUserState.email.trim();
    const password = createUserState.password;

    if (!username || !password.trim()) {
      return "请填写用户名和初始密码。";
    }
    if (username.length < 2) {
      return "用户名至少 2 位。";
    }
    if (username.length > 64) {
      return "用户名不能超过 64 位。";
    }
    if (!/^[A-Za-z0-9._-]+$/.test(username)) {
      return "用户名仅支持字母、数字、点、下划线和短横线。";
    }
    if (displayName.length > 128) {
      return "显示名不能超过 128 位。";
    }
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return "邮箱格式不正确。";
    }
    if (password.length < 6) {
      return "初始密码至少 6 位。";
    }
    return null;
  }

  function validateResetPasswordForm() {
    if (!resetPasswordState) return "表单状态无效。";
    if (!resetPasswordState.password.trim()) {
      return "请输入新密码。";
    }
    if (resetPasswordState.password.length < 6) {
      return "新密码至少 6 位。";
    }
    if (!resetPasswordState.confirmPassword.trim()) {
      return "请再次输入确认密码。";
    }
    if (resetPasswordState.password !== resetPasswordState.confirmPassword) {
      return "两次输入的密码不一致。";
    }
    return null;
  }

  function syncPermissions(user: AdminUser) {
    if (selectedUser?.id === user.id) return;
    if (hasUnsavedPermissionChanges) {
      setPendingUserSwitch({ user });
      return;
    }
    applyUserSelection(user);
  }

  async function handleRefresh() {
    try {
      await onRefresh();
      setToast({ type: "success", message: "列表已刷新" });
    } catch (refreshError) {
      setToast({ type: "error", message: refreshError instanceof Error ? refreshError.message : "刷新失败" });
    }
  }

  async function handleSavePermissions() {
    if (!selectedUser || selectedUser.role === "root") return;
    try {
      setSavingPermissions(true);
      await onSavePermissions(
        selectedUser,
        selectedUser.permissions.map((permission) => ({
          ...permission,
          is_enabled: Boolean(draftPermissions[permission.module_key]),
        })),
      );
      setPermissionsEditing(false);
      setToast({ type: "success", message: "权限已保存" });
    } catch (saveError) {
      setToast({ type: "error", message: saveError instanceof Error ? saveError.message : "权限保存失败" });
    } finally {
      setSavingPermissions(false);
    }
  }

  async function handleToggleDisabled(user: AdminUser) {
    try {
      await onToggleDisabled(user);
      setToast({ type: "success", message: user.is_disabled ? "用户已启用" : "用户已停用" });
    } catch (toggleError) {
      setToast({ type: "error", message: toggleError instanceof Error ? toggleError.message : "用户状态更新失败" });
    }
  }

  async function handleCreateUser() {
    if (!createUserState || createUserState.submitting) return;
    const validationError = validateCreateUserForm();
    if (validationError) {
      setCreateUserState((current) => (current ? { ...current, error: validationError } : current));
      return;
    }

    try {
      setCreateUserState((current) => (current ? { ...current, submitting: true, error: null } : current));
      await onCreateUser({
        username: createUserState.username.trim(),
        display_name: createUserState.display_name.trim() || undefined,
        email: createUserState.email.trim() || undefined,
        password: createUserState.password,
      });
      setCreateUserState(null);
    } catch (submitError) {
      setCreateUserState((current) =>
        current
          ? {
              ...current,
              submitting: false,
              error: submitError instanceof Error ? submitError.message : "创建用户失败",
            }
          : current,
      );
    }
  }

  async function handleDeleteUserSubmit() {
    if (!deleteUserState) return;
    try {
      setDeleteUserState((current) => (current ? { ...current, submitting: true, error: null } : current));
      await onDeleteUser(deleteUserState.user);
      if (selectedUserId === deleteUserState.user.id) {
        setSelectedUserId(null);
      }
      setDeleteUserState(null);
      setToast({ type: "success", message: "用户已删除" });
    } catch (submitError) {
      setDeleteUserState((current) =>
        current
          ? {
              ...current,
              submitting: false,
              error: submitError instanceof Error ? submitError.message : "删除用户失败",
            }
          : current,
      );
    }
  }

  async function handleResetPasswordSubmit() {
    if (!resetPasswordState || resetPasswordState.submitting) return;
    const validationError = validateResetPasswordForm();
    if (validationError) {
      setResetPasswordState((current) => (current ? { ...current, error: validationError } : current));
      return;
    }

    try {
      setResetPasswordState((current) => (current ? { ...current, submitting: true, error: null } : current));
      await onResetPassword(resetPasswordState.user, resetPasswordState.password);
      setToast({ type: "success", message: "密码已重置" });
      setResetPasswordState(null);
    } catch (submitError) {
      setResetPasswordState((current) =>
        current
          ? {
              ...current,
              submitting: false,
              error: submitError instanceof Error ? submitError.message : "重置密码失败",
            }
          : current,
      );
    }
  }

  return (
    <div className="page-stack compact-page">
      {toast ? (
        <div className="admin-toast-layer" aria-live="polite">
          <div className={toast.type === "success" ? "admin-toast success" : "admin-toast error"}>{toast.message}</div>
        </div>
      ) : null}

      <section className="panel compact-panel">
        <SectionHeader eyebrow="状态" title="系统状态" />
        <div className="admin-status-grid">
          <article className="admin-status-card">
            <span className={systemStatus?.backend_status === "ok" ? "status-pill online" : "status-pill warning"}>
              {systemStatus?.backend_status === "ok" ? "后端正常" : "后端异常"}
            </span>
            <strong>API 服务</strong>
            <p>{systemStatus ? "来自 `/api/v1/admin/system-status` 的实时结果。" : "加载中..."}</p>
          </article>
          <article className="admin-status-card">
            <span className={systemStatus?.database_status === "ok" ? "status-pill online" : "status-pill warning"}>
              {systemStatus?.database_status === "ok" ? "数据库正常" : "数据库异常"}
            </span>
            <strong>数据库连接</strong>
            <p>{systemStatus ? "启动时与管理页刷新时会验证数据库连通性。" : "加载中..."}</p>
          </article>
          <article className="admin-status-card">
            <span className={systemStatus?.storage_mode === "local_disk" ? "status-pill online" : "status-pill warning"}>
              {systemStatus?.storage_mode === "local_disk" ? "本地存储" : "存储异常"}
            </span>
            <strong>文件存储</strong>
            <p>
              {systemStatus
                ? `${systemStatus.storage_path}${systemStatus.oss_compat_enabled ? " / 兼容旧 OSS 记录" : ""}`
                : "加载中..."}
            </p>
          </article>
          <article className="admin-status-card">
            <span className="status-pill idle">{systemStatus?.environment ?? "未知环境"}</span>
            <strong>运行环境</strong>
            <p>当前环境标识来自后端配置。</p>
          </article>
        </div>
      </section>

      <section className="panel compact-panel">
        <SectionHeader eyebrow="Root" title="系统管理" description="仅 root 可见，集中管理系统状态、用户账号与模块权限。" />
        {isMobile ? <p className="admin-mobile-hint">手机端已做简化适配，复杂权限批量编辑建议在平板或电脑端操作。</p> : null}
        <div className="inline-action-row">
          <button
            className="primary-button compact-button"
            type="button"
            onClick={() =>
              setCreateUserState({
                username: "",
                display_name: "",
                email: "",
                password: "",
                submitting: false,
                error: null,
              })
            }
          >
            新增用户
          </button>
          <button
            className="history-icon-button refresh-icon-button"
            type="button"
            onClick={() => void handleRefresh()}
            disabled={loading}
            title={loading ? "刷新中" : "刷新列表"}
            aria-label={loading ? "刷新中" : "刷新列表"}
          >
            <span aria-hidden="true">{loading ? "…" : "↻"}</span>
          </button>
        </div>
        {error ? <p className="error-text">{error}</p> : null}
      </section>

      <div className="dashboard-grid admin-management-grid">
        <section className="panel compact-panel admin-management-panel">
          <SectionHeader eyebrow="用户" title="账号列表" description="点击用户后可调整模块权限。" />
          <div className="admin-user-panel-body">
            <div className="admin-user-list-head">
              <span>用户</span>
              <span>{users.length} 个账号</span>
            </div>
            <div className="admin-user-list">
              {users.map((user) => (
                <article className={selectedUser?.id === user.id ? "admin-user-list-item active" : "admin-user-list-item"} key={user.id}>
                  <button className="admin-user-main" type="button" onClick={() => syncPermissions(user)}>
                    <div className="admin-user-main-copy">
                      <h4>{user.display_name || user.username}</h4>
                      <p>{user.username}</p>
                      <div className="admin-user-meta-row">
                        <span className="admin-user-meta-pill">{user.role}</span>
                        <span className="admin-user-meta-pill">{user.is_disabled ? "已停用" : "启用中"}</span>
                        {user.email ? <span className="admin-user-meta-pill subtle">{user.email}</span> : null}
                      </div>
                    </div>
                    <span className={user.is_disabled ? "status-pill warning" : "status-pill online"}>{user.is_disabled ? "停用" : "启用"}</span>
                  </button>
                  <div className="admin-user-actions">
                    {user.role !== "root" ? (
                      <button className="secondary-button compact-button" type="button" onClick={() => void handleToggleDisabled(user)}>
                        {user.is_disabled ? "启用" : "停用"}
                      </button>
                    ) : null}
                    {user.role !== "root" ? (
                      <button
                        className="secondary-button compact-button"
                        type="button"
                        onClick={() =>
                          setDeleteUserState({
                            user,
                            submitting: false,
                            error: null,
                          })
                        }
                      >
                        删除用户
                      </button>
                    ) : null}
                    <button
                      className="secondary-button compact-button"
                      type="button"
                      onClick={() =>
                        setResetPasswordState({
                          user,
                          password: "",
                          confirmPassword: "",
                          submitting: false,
                          error: null,
                        })
                      }
                    >
                      重置密码
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="panel compact-panel admin-management-panel">
          <div className="admin-permission-header">
            <SectionHeader eyebrow="权限" title={selectedUser ? `模块权限: ${selectedUser.username}` : "模块权限"} description="root 默认全开，普通用户按模块开关。" />
            {selectedUser ? (
              <div className="inline-action-row admin-permission-toolbar">
                {!permissionsEditing && selectedUser.role !== "root" ? <span className="admin-readonly-hint">只读模式</span> : null}
                {selectedUser.role !== "root" ? (
                  permissionsEditing ? (
                    <>
                      <button
                        className="secondary-button compact-button"
                        type="button"
                        onClick={() => {
                          setDraftPermissions(Object.fromEntries(selectedUser.permissions.map((item) => [item.module_key, item.is_enabled])));
                          setPermissionsEditing(false);
                        }}
                        disabled={savingPermissions}
                      >
                        取消
                      </button>
                      <button className="primary-button compact-button" type="button" disabled={savingPermissions} onClick={() => void handleSavePermissions()}>
                        {savingPermissions ? "保存中..." : "保存权限"}
                      </button>
                    </>
                  ) : (
                    <button className="secondary-button compact-button" type="button" onClick={() => setPermissionsEditing(true)}>
                      修改权限
                    </button>
                  )
                ) : (
                  <span className="admin-readonly-hint">root 权限固定全开</span>
                )}
              </div>
            ) : null}
          </div>
          {selectedUser ? (
            <>
              <div className="admin-permission-scroll">
                <div className="admin-permission-grid">
                  {selectedUser.permissions.map((permission) => (
                    <label className="admin-permission-card" key={permission.module_key}>
                      <input
                        type="checkbox"
                        checked={selectedUser.role === "root" ? true : Boolean(draftPermissions[permission.module_key])}
                        disabled={selectedUser.role === "root" || !permissionsEditing}
                        onChange={(event) => setDraftPermissions((current) => ({ ...current, [permission.module_key]: event.target.checked }))}
                      />
                      <div>
                        <h4>{permission.label}</h4>
                        <p>{permission.module_key}</p>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <p className="muted">暂无用户。</p>
          )}
        </section>
      </div>

      {resetPasswordState ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setResetPasswordState(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="重置密码" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">密码</p>
                <h3>重置密码: {resetPasswordState.user.username}</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setResetPasswordState(null)} aria-label="关闭重置密码弹窗">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <input
                className="search-input"
                type="password"
                placeholder="新密码"
                value={resetPasswordState.password}
                onChange={(event) => setResetPasswordState((current) => (current ? { ...current, password: event.target.value, error: null } : current))}
              />
              <input
                className="search-input"
                type="password"
                placeholder="确认新密码"
                value={resetPasswordState.confirmPassword}
                onChange={(event) => setResetPasswordState((current) => (current ? { ...current, confirmPassword: event.target.value, error: null } : current))}
              />
              {resetPasswordState.error ? <p className="error-text">{resetPasswordState.error}</p> : null}
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setResetPasswordState(null)}>
                取消
              </button>
              <button className="primary-button compact-button" type="button" onClick={() => void handleResetPasswordSubmit()} disabled={resetPasswordState.submitting}>
                {resetPasswordState.submitting ? "提交中..." : "确认重置"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {createUserState ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setCreateUserState(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="创建用户" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">用户</p>
                <h3>新增账号</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setCreateUserState(null)} aria-label="关闭创建用户弹窗">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <label className="admin-form-field">
                <span>用户名</span>
                <small>用于账号登录，建议使用英文、数字或常用短名称。</small>
                <input
                  className="search-input"
                  placeholder="例如：chaihe"
                  value={createUserState.username}
                  onChange={(event) => setCreateUserState((current) => (current ? { ...current, username: event.target.value, error: null } : current))}
                />
              </label>
              <label className="admin-form-field">
                <span>显示名</span>
                <small>用于系统页面展示，可填写中文姓名、团队名或岗位名。</small>
                <input
                  className="search-input"
                  placeholder="例如：柴禾 / 设计部"
                  value={createUserState.display_name}
                  onChange={(event) => setCreateUserState((current) => (current ? { ...current, display_name: event.target.value, error: null } : current))}
                />
              </label>
              <label className="admin-form-field">
                <span>邮箱</span>
                <small>选填，用于记录联系方式，当前不作为登录账号。</small>
                <input
                  className="search-input"
                  placeholder="例如：name@example.com"
                  value={createUserState.email}
                  onChange={(event) => setCreateUserState((current) => (current ? { ...current, email: event.target.value, error: null } : current))}
                />
              </label>
              <label className="admin-form-field">
                <span>初始密码</span>
                <small>创建后用户可直接用该密码登录，建议设置为便于首次通知的临时密码。</small>
                <input
                  className="search-input"
                  type="password"
                  placeholder="请输入初始密码"
                  value={createUserState.password}
                  onChange={(event) => setCreateUserState((current) => (current ? { ...current, password: event.target.value, error: null } : current))}
                />
              </label>
              <p className="muted">新用户默认启用全部模块权限，创建后 root 可在右侧权限面板中调整。</p>
              {createUserState.error ? <p className="error-text">{createUserState.error}</p> : null}
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setCreateUserState(null)}>
                取消
              </button>
              <button className="primary-button compact-button" type="button" onClick={() => void handleCreateUser()} disabled={createUserState.submitting}>
                {createUserState.submitting ? "创建中..." : "确认创建"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {pendingUserSwitch ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setPendingUserSwitch(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="未保存权限变更提示" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">权限</p>
                <h3>放弃未保存修改？</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setPendingUserSwitch(null)} aria-label="关闭未保存修改提示">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="muted">当前用户的权限变更还没有保存。如果继续切换到 {pendingUserSwitch.user.username}，当前修改将被放弃。</p>
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setPendingUserSwitch(null)}>
                继续编辑
              </button>
              <button
                className="primary-button compact-button"
                type="button"
                onClick={() => {
                  applyUserSelection(pendingUserSwitch.user);
                  setPendingUserSwitch(null);
                }}
              >
                放弃并切换
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {deleteUserState ? (
        <div className="admin-modal-backdrop" role="presentation" onClick={() => setDeleteUserState(null)}>
          <div className="admin-modal-card" role="dialog" aria-modal="true" aria-label="删除用户确认" onClick={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <div>
                <p className="eyebrow">用户</p>
                <h3>删除账号: {deleteUserState.user.username}</h3>
              </div>
              <button className="template-close-button" type="button" onClick={() => setDeleteUserState(null)} aria-label="关闭删除用户弹窗">
                ×
              </button>
            </div>
            <div className="admin-modal-body">
              <p className="muted">该操作为假删除：账号会被停用并从列表隐藏，但历史记录、资产和权限数据会保留。</p>
              {deleteUserState.error ? <p className="error-text">{deleteUserState.error}</p> : null}
            </div>
            <div className="admin-modal-actions">
              <button className="secondary-button compact-button" type="button" onClick={() => setDeleteUserState(null)}>
                取消
              </button>
              <button className="primary-button compact-button" type="button" onClick={() => void handleDeleteUserSubmit()} disabled={deleteUserState.submitting}>
                {deleteUserState.submitting ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
