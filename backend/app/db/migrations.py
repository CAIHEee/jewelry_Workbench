from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, inspect, text

from app.core.config import get_settings
from app.core.permissions import MODULE_KEYS
from app.core.security import hash_password


settings = get_settings()


def run_migrations(engine: Engine) -> None:
    _ensure_schema_migrations_table(engine)
    with engine.begin() as connection:
        applied_versions = {
            row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))
        }

    migrations = [
        ("20260420_auth_assets", _apply_auth_assets_migration),
        ("20260422_user_soft_delete", _apply_user_soft_delete_migration),
        ("20260422_generation_jobs", _apply_generation_jobs_migration),
        ("20260422_generation_jobs_longtext", _apply_generation_jobs_longtext_migration),
        ("20260428_agent_memory", _apply_agent_memory_migration),
    ]

    for version, migration in migrations:
        if version in applied_versions:
            continue
        migration(engine)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.now(timezone.utc)},
            )


def _ensure_schema_migrations_table(engine: Engine) -> None:
    if inspect(engine).has_table("schema_migrations"):
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE schema_migrations (
                    version VARCHAR(64) PRIMARY KEY,
                    applied_at DATETIME NOT NULL
                )
                """
            )
        )


def _has_column(engine: Engine, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspect(engine).get_columns(table_name))


def _add_column_if_missing(engine: Engine, table_name: str, column_name: str, definition: str) -> None:
    if _has_column(engine, table_name, column_name):
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))


def _create_index_if_missing(engine: Engine, table_name: str, index_name: str, columns: str, *, unique: bool = False) -> None:
    existing = {index["name"] for index in inspect(engine).get_indexes(table_name)}
    if index_name in existing:
        return
    unique_sql = "UNIQUE " if unique else ""
    with engine.begin() as connection:
        connection.execute(text(f"CREATE {unique_sql}INDEX {index_name} ON {table_name} ({columns})"))


def _apply_auth_assets_migration(engine: Engine) -> None:
    dialect = engine.dialect.name

    _add_column_if_missing(engine, "users", "role", "VARCHAR(32) NOT NULL DEFAULT 'user'")
    if not inspect(engine).has_table("user_module_permissions"):
        with engine.begin() as connection:
            bool_type = "BOOLEAN" if dialect == "sqlite" else "TINYINT(1)"
            created_type = "DATETIME" if dialect == "sqlite" else "DATETIME(6)"
            connection.execute(
                text(
                    f"""
                    CREATE TABLE user_module_permissions (
                        id VARCHAR(36) PRIMARY KEY NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        module_key VARCHAR(64) NOT NULL,
                        is_enabled {bool_type} NOT NULL DEFAULT 1,
                        created_at {created_type} NOT NULL,
                        updated_at {created_type} NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    )
                    """
                )
            )
        _create_index_if_missing(engine, "user_module_permissions", "ix_user_module_permissions_user_module", "user_id, module_key", unique=True)
        _create_index_if_missing(engine, "user_module_permissions", "ix_user_module_permissions_user_id", "user_id")

    _add_column_if_missing(engine, "asset_records", "owner_user_id", "VARCHAR(36)")
    _add_column_if_missing(engine, "asset_records", "visibility", "VARCHAR(32) NOT NULL DEFAULT 'private'")
    _add_column_if_missing(engine, "asset_records", "published_at", "DATETIME")
    _add_column_if_missing(engine, "asset_records", "published_by_user_id", "VARCHAR(36)")
    _create_index_if_missing(engine, "asset_records", "ix_asset_records_owner_user_id", "owner_user_id")
    _create_index_if_missing(engine, "asset_records", "ix_asset_records_visibility", "visibility")
    _create_index_if_missing(engine, "asset_records", "ix_asset_records_published_by_user_id", "published_by_user_id")
    _create_index_if_missing(engine, "asset_records", "ix_asset_records_owner_visibility", "owner_user_id, visibility")
    _create_index_if_missing(engine, "users", "ix_users_role", "role")

    with engine.begin() as connection:
        existing_root = connection.execute(
            text("SELECT id, password_hash FROM users WHERE id = :user_id"),
            {"user_id": settings.root_user_id},
        ).mappings().first()
        root_password_hash = hash_password(settings.root_default_password)
        now = datetime.now(timezone.utc)

        if existing_root is None:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, username, role, display_name, email, password_hash, is_disabled, created_at, updated_at)
                    VALUES (:id, :username, 'root', :display_name, :email, :password_hash, 0, :created_at, :updated_at)
                    """
                ),
                {
                    "id": settings.root_user_id,
                    "username": settings.root_username,
                    "display_name": settings.root_display_name,
                    "email": settings.root_email,
                    "password_hash": root_password_hash,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        else:
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET username = :username, role = 'root', display_name = :display_name, email = :email
                    WHERE id = :id
                    """
                ),
                {
                    "id": settings.root_user_id,
                    "username": settings.root_username,
                    "display_name": settings.root_display_name,
                    "email": settings.root_email,
                },
            )
            if not existing_root["password_hash"]:
                connection.execute(
                    text("UPDATE users SET password_hash = :password_hash WHERE id = :id"),
                    {"id": settings.root_user_id, "password_hash": root_password_hash},
                )

        connection.execute(
            text("UPDATE generation_records SET user_id = :root_user_id WHERE user_id IS NULL"),
            {"root_user_id": settings.root_user_id},
        )
        connection.execute(
            text(
                """
                UPDATE asset_records
                SET owner_user_id = COALESCE(owner_user_id, user_id, :root_user_id)
                WHERE owner_user_id IS NULL
                """
            ),
            {"root_user_id": settings.root_user_id},
        )
        connection.execute(
            text(
                """
                UPDATE asset_records
                SET visibility = CASE
                    WHEN visibility IS NULL OR visibility = '' THEN 'community'
                    ELSE visibility
                END
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE asset_records
                SET published_by_user_id = COALESCE(published_by_user_id, :root_user_id),
                    published_at = COALESCE(published_at, created_at)
                WHERE visibility = 'community'
                """
            ),
            {"root_user_id": settings.root_user_id},
        )

        permission_rows = {
            (row["user_id"], row["module_key"])
            for row in connection.execute(text("SELECT user_id, module_key FROM user_module_permissions")).mappings()
        }
        users = list(connection.execute(text("SELECT id, role FROM users")).mappings())
        for user in users:
            for module_key in MODULE_KEYS:
                if (user["id"], module_key) in permission_rows:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO user_module_permissions (id, user_id, module_key, is_enabled, created_at, updated_at)
                        VALUES (:id, :user_id, :module_key, :is_enabled, :created_at, :updated_at)
                        """
                    ),
                    {
                        "id": f"{user['id'][:20]}-{module_key[:15]}",
                        "user_id": user["id"],
                        "module_key": module_key,
                        "is_enabled": True if user["role"] == "root" else module_key != "asset_management",
                        "created_at": now,
                        "updated_at": now,
                    },
                )


def _apply_user_soft_delete_migration(engine: Engine) -> None:
    _add_column_if_missing(engine, "users", "deleted_at", "DATETIME")
    _create_index_if_missing(engine, "users", "ix_users_deleted_at", "deleted_at")


def _apply_generation_jobs_migration(engine: Engine) -> None:
    dialect = engine.dialect.name
    dt_type = "DATETIME" if dialect == "sqlite" else "DATETIME(6)"

    if not inspect(engine).has_table("generation_jobs"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE generation_jobs (
                        id VARCHAR(36) PRIMARY KEY NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        queue_name VARCHAR(64) NOT NULL,
                        rq_job_id VARCHAR(64) NOT NULL,
                        feature_key VARCHAR(64) NOT NULL,
                        model VARCHAR(128) NULL,
                        prompt TEXT NULL,
                        status VARCHAR(32) NOT NULL,
                        request_json TEXT NULL,
                        result_json TEXT NULL,
                        error_message TEXT NULL,
                        started_at {dt_type} NULL,
                        completed_at {dt_type} NULL,
                        created_at {dt_type} NOT NULL,
                        updated_at {dt_type} NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    )
                    """
                )
            )

    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_user_id", "user_id")
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_queue_name", "queue_name")
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_rq_job_id", "rq_job_id", unique=True)
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_feature_key", "feature_key")
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_model", "model")
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_status", "status")
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_user_status", "user_id, status")
    _create_index_if_missing(engine, "generation_jobs", "ix_generation_jobs_feature_status", "feature_key, status")


def _apply_generation_jobs_longtext_migration(engine: Engine) -> None:
    if engine.dialect.name != "mysql" or not inspect(engine).has_table("generation_jobs"):
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE generation_jobs MODIFY COLUMN request_json LONGTEXT NULL"))
        connection.execute(text("ALTER TABLE generation_jobs MODIFY COLUMN result_json LONGTEXT NULL"))
        connection.execute(text("ALTER TABLE generation_jobs MODIFY COLUMN error_message LONGTEXT NULL"))


def _apply_agent_memory_migration(engine: Engine) -> None:
    dialect = engine.dialect.name
    dt_type = "DATETIME" if dialect == "sqlite" else "DATETIME(6)"
    text_type = "TEXT" if dialect == "sqlite" else "LONGTEXT"
    bool_type = "INTEGER" if dialect == "sqlite" else "TINYINT(1)"

    if not inspect(engine).has_table("agent_conversations"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE agent_conversations (
                        id VARCHAR(36) PRIMARY KEY NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        mode VARCHAR(32) NOT NULL,
                        title VARCHAR(255) NOT NULL,
                        current_stage VARCHAR(64) NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'active',
                        state_json {text_type} NULL,
                        summary {text_type} NULL,
                        created_at {dt_type} NOT NULL,
                        updated_at {dt_type} NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    )
                    """
                )
            )
    if not inspect(engine).has_table("agent_messages"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE agent_messages (
                        id VARCHAR(36) PRIMARY KEY NOT NULL,
                        conversation_id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        role VARCHAR(32) NOT NULL,
                        content {text_type} NOT NULL,
                        attachments_json {text_type} NULL,
                        event_json {text_type} NULL,
                        created_at {dt_type} NOT NULL,
                        FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id),
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    )
                    """
                )
            )
    if not inspect(engine).has_table("agent_actions"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE agent_actions (
                        id VARCHAR(36) PRIMARY KEY NOT NULL,
                        conversation_id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        kind VARCHAR(32) NOT NULL,
                        module_key VARCHAR(64) NOT NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'draft',
                        title VARCHAR(255) NOT NULL,
                        prompt {text_type} NULL,
                        params_json {text_type} NULL,
                        source_asset_ids_json {text_type} NULL,
                        source_image_urls_json {text_type} NULL,
                        result_job_id VARCHAR(64) NULL,
                        error_message {text_type} NULL,
                        created_at {dt_type} NOT NULL,
                        updated_at {dt_type} NOT NULL,
                        FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id),
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    )
                    """
                )
            )
    if not inspect(engine).has_table("agent_user_memories"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE agent_user_memories (
                        id VARCHAR(36) PRIMARY KEY NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        memory_type VARCHAR(64) NOT NULL DEFAULT 'preference',
                        content {text_type} NOT NULL,
                        is_enabled {bool_type} NOT NULL DEFAULT 1,
                        source_conversation_id VARCHAR(36) NULL,
                        created_at {dt_type} NOT NULL,
                        updated_at {dt_type} NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id),
                        FOREIGN KEY(source_conversation_id) REFERENCES agent_conversations(id)
                    )
                    """
                )
            )

    _create_index_if_missing(engine, "agent_conversations", "ix_agent_conversations_user_id", "user_id")
    _create_index_if_missing(engine, "agent_conversations", "ix_agent_conversations_mode", "mode")
    _create_index_if_missing(engine, "agent_conversations", "ix_agent_conversations_status", "status")
    _create_index_if_missing(engine, "agent_conversations", "ix_agent_conversations_current_stage", "current_stage")
    _create_index_if_missing(engine, "agent_conversations", "ix_agent_conversations_user_updated", "user_id, updated_at")
    _create_index_if_missing(engine, "agent_conversations", "ix_agent_conversations_user_status", "user_id, status")
    _create_index_if_missing(engine, "agent_messages", "ix_agent_messages_conversation_id", "conversation_id")
    _create_index_if_missing(engine, "agent_messages", "ix_agent_messages_user_id", "user_id")
    _create_index_if_missing(engine, "agent_messages", "ix_agent_messages_role", "role")
    _create_index_if_missing(engine, "agent_messages", "ix_agent_messages_conversation_created", "conversation_id, created_at")
    _create_index_if_missing(engine, "agent_messages", "ix_agent_messages_user_created", "user_id, created_at")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_conversation_id", "conversation_id")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_user_id", "user_id")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_kind", "kind")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_module_key", "module_key")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_status", "status")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_result_job_id", "result_job_id")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_conversation_status", "conversation_id, status")
    _create_index_if_missing(engine, "agent_actions", "ix_agent_actions_user_status", "user_id, status")
    _create_index_if_missing(engine, "agent_user_memories", "ix_agent_user_memories_user_id", "user_id")
    _create_index_if_missing(engine, "agent_user_memories", "ix_agent_user_memories_memory_type", "memory_type")
    _create_index_if_missing(engine, "agent_user_memories", "ix_agent_user_memories_is_enabled", "is_enabled")
    _create_index_if_missing(engine, "agent_user_memories", "ix_agent_user_memories_source_conversation_id", "source_conversation_id")
    _create_index_if_missing(engine, "agent_user_memories", "ix_agent_user_memories_user_enabled", "user_id, is_enabled")
    _create_index_if_missing(engine, "agent_user_memories", "ix_agent_user_memories_user_type", "user_id, memory_type")

    if inspect(engine).has_table("user_module_permissions"):
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            permission_rows = {
                (row["user_id"], row["module_key"])
                for row in connection.execute(text("SELECT user_id, module_key FROM user_module_permissions")).mappings()
            }
            users = list(connection.execute(text("SELECT id, role FROM users")).mappings())
            for user in users:
                if (user["id"], "ai_agent") in permission_rows:
                    continue
                connection.execute(
                    text(
                        """
                        INSERT INTO user_module_permissions (id, user_id, module_key, is_enabled, created_at, updated_at)
                        VALUES (:id, :user_id, 'ai_agent', :is_enabled, :created_at, :updated_at)
                        """
                    ),
                    {
                        "id": f"{user['id'][:20]}-ai_agent",
                        "user_id": user["id"],
                        "is_enabled": True,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
