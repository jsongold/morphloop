"""drill.answers: key by ``<ws_id>/<position>`` for creation-order paging (#178)

Revision ID: b8d2f4a6c0e3
Revises: e2a4c6b8d0f1
Create Date: 2026-09-27 12:00:00.000000

Data-only: re-keys each existing ``<ws_id>/<item_id>/<position:012d>`` answer
document to ``<ws_id>/<position:012d>`` (both parts read from the document),
so a ws's answers page oldest first. Same result as ``morphloop rebuild``,
without a manual step on deploy. Downgrade restores the old keys.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d2f4a6c0e3"
down_revision: str | Sequence[str] | None = "e2a4c6b8d0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "(document ->> 'ws_id') || '/' || (document ->> 'item_id') || '/' || right(key, 12)"
_NEW = "(document ->> 'ws_id') || '/' || right(key, 12)"


def _rekey(source: str, target: str) -> None:
    where = f"view = 'drill.answers' AND key ~ '/[0-9]{{12}}$' AND key = {source}"
    op.execute(
        f"INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, {target}, document FROM view_documents_v2 WHERE {where}"
        f" ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(f"DELETE FROM view_documents_v2 WHERE {where}")


def upgrade() -> None:
    _rekey(_OLD, _NEW)


def downgrade() -> None:
    _rekey(_NEW, _OLD)
