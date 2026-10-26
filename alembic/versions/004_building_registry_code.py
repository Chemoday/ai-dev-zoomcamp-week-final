"""Property: the association's registry code on the building.

Revision ID: 004
Revises: 003
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "property_buildings", sa.Column("registry_code", sa.String(length=20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("property_buildings", "registry_code")
