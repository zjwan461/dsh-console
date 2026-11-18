"""skills and plugins

Revision ID: c9f1a2b3d4e5
Revises: b4d7e2c1a903
Create Date: 2026-11-18

Platform skill repository (additive: image built-ins are untouched) and an
admin-managed plugin catalog, plus per-instance materialisation state.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c9f1a2b3d4e5"
down_revision: str | None = "b4d7e2c1a903"
branch_labels = None
depends_on = None

BIGINT = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("when_to_use", sa.String(500), nullable=False, server_default=""),
        sa.Column("model_invocable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("user_invocable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "status",
            sa.Enum("draft", "published", "disabled", name="skill_status"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="create"),
        sa.Column("current_revision_id", BIGINT, nullable=True),
        sa.Column("created_by", BIGINT, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skills_status", "skills", ["status"])

    op.create_table(
        "skill_revisions",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "skill_id", BIGINT, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("changelog", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", BIGINT, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.UniqueConstraint("skill_id", "rev", name="uk_skill_rev"),
    )
    op.create_index("ix_skill_revisions_skill_id", "skill_revisions", ["skill_id"])

    op.create_table(
        "skill_grants",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "skill_id", BIGINT, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "scope",
            sa.Enum("all", "plan", "user", name="grant_scope"),
            nullable=False,
        ),
        sa.Column("target_id", BIGINT, nullable=False, server_default="0"),
        sa.UniqueConstraint("skill_id", "scope", "target_id", name="uk_skill_grant"),
    )
    op.create_index("ix_skill_grants_skill_id", "skill_grants", ["skill_id"])

    op.create_table(
        "user_skill_prefs",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", BIGINT, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "skill_id", BIGINT, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "skill_id", name="uk_user_skill"),
    )
    op.create_index("ix_user_skill_prefs_user_id", "user_skill_prefs", ["user_id"])
    op.create_index("ix_user_skill_prefs_skill_id", "user_skill_prefs", ["skill_id"])

    op.create_table(
        "plugins",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column("package_name", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("version", sa.String(32), nullable=False, server_default="0.0.0"),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("dsh_versions", sa.String(200), nullable=False, server_default=""),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("enabled", "disabled", name="plugin_status"),
            nullable=False,
            server_default="disabled",
        ),
        sa.Column("current_revision_id", BIGINT, nullable=True),
        sa.Column("created_by", BIGINT, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_plugins_status", "plugins", ["status"])

    op.create_table(
        "plugin_revisions",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "plugin_id", BIGINT, sa.ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("patch_files", sa.JSON(), nullable=True),
        sa.Column("created_by", BIGINT, sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.UniqueConstraint("plugin_id", "version", name="uk_plugin_rev"),
    )
    op.create_index("ix_plugin_revisions_plugin_id", "plugin_revisions", ["plugin_id"])

    op.create_table(
        "plugin_grants",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column(
            "plugin_id", BIGINT, sa.ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "scope",
            sa.Enum("all", "plan", "user", name="plugin_grant_scope"),
            nullable=False,
        ),
        sa.Column("target_id", BIGINT, nullable=False, server_default="0"),
        sa.UniqueConstraint("plugin_id", "scope", "target_id", name="uk_plugin_grant"),
    )
    op.create_index("ix_plugin_grants_plugin_id", "plugin_grants", ["plugin_id"])

    with op.batch_alter_table("instances") as batch:
        batch.add_column(sa.Column("skills_revision", sa.String(64), nullable=True))
        batch.add_column(sa.Column("plugins_revision", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("needs_restart", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("config_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("instances") as batch:
        batch.drop_column("config_synced_at")
        batch.drop_column("needs_restart")
        batch.drop_column("plugins_revision")
        batch.drop_column("skills_revision")

    op.drop_index("ix_plugin_grants_plugin_id", table_name="plugin_grants")
    op.drop_table("plugin_grants")
    op.drop_index("ix_plugin_revisions_plugin_id", table_name="plugin_revisions")
    op.drop_table("plugin_revisions")
    op.drop_index("ix_plugins_status", table_name="plugins")
    op.drop_table("plugins")
    op.drop_index("ix_user_skill_prefs_skill_id", table_name="user_skill_prefs")
    op.drop_index("ix_user_skill_prefs_user_id", table_name="user_skill_prefs")
    op.drop_table("user_skill_prefs")
    op.drop_index("ix_skill_grants_skill_id", table_name="skill_grants")
    op.drop_table("skill_grants")
    op.drop_index("ix_skill_revisions_skill_id", table_name="skill_revisions")
    op.drop_table("skill_revisions")
    op.drop_index("ix_skills_status", table_name="skills")
    op.drop_table("skills")