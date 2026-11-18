"""add provider model

Revision ID: b4d7e2c1a903
Revises: 8c1f4d2e9a77
Create Date: 2026-11-18

A custom endpoint often exposes a model whose id is not part of the DeepSeek
catalogue (qwen, gpt-4o, ...). Store it next to the endpoint so the console can
generate a generic openai-completions provider.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b4d7e2c1a903"
down_revision: str | None = "8c1f4d2e9a77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_credentials") as batch:
        batch.add_column(sa.Column("model", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("provider_credentials") as batch:
        batch.drop_column("model")
