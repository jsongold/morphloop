"""search_documents: owner_user_id + parent_id (#180 S3a)

Revision ID: c9e1f3a5b7d0
Revises: b8d2f4a6c0e3
Create Date: 2026-09-27 15:00:00.000000

The search Ports scope every query to one learner and return ``parent_id``
on each hit, but ``search_documents`` stored neither. Both are nullable:
``owner_user_id`` NULL means shared corpus (pack content); ``parent_id`` NULL
means a top-level resource. The partial index serves the per-learner filter
(``owner_user_id IS NULL OR owner_user_id = :user``) for owned rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e1f3a5b7d0"
down_revision: str | Sequence[str] | None = "b8d2f4a6c0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("search_documents", sa.Column("owner_user_id", sa.Text, nullable=True))
    op.add_column("search_documents", sa.Column("parent_id", sa.Text, nullable=True))
    op.create_index(
        "search_documents_owner_idx",
        "search_documents",
        ["owner_user_id"],
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("search_documents_owner_idx", table_name="search_documents")
    op.drop_column("search_documents", "parent_id")
    op.drop_column("search_documents", "owner_user_id")
