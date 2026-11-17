"""add provider base_url

Revision ID: 8c1f4d2e9a77
Revises: 5ee4d1818341
Create Date: 2026-11-17

Non-DeepSeek users point the console at an OpenAI-compatible gateway, so the
credential needs to carry the endpoint as well as the key.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "8c1f4d2e9a77"
down_revision: str | None = "5ee4d1818341"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_credentials") as batch:
        batch.add_column(sa.Column("base_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("provider_credentials") as batch:
        batch.drop_column("base_url")
