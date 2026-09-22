"""baseline

Revision ID: f3f0928fbd1d
Revises:
Create Date: 2026-09-22 15:16:32.274967

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f3f0928fbd1d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
