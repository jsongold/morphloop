"""highlight: key by ``<ws_id>:<position:020d>`` for creation-order paging (#220)

Revision ID: d3f5a7c9e1b2
Revises: c9e1f3a5b7d0
Create Date: 2026-09-27 18:00:00.000000

Data-only. Each ``<ws_id>:<highlight_id>`` document of the ``highlight``
view moves to ``<ws_id>:<position:020d>`` and gains an
``id:<ws_id>:<highlight_id>`` -> ``{"position": ...}`` lookup document. Same
result as ``morphloop rebuild``, without a manual step on deploy.

Active documents carry ``position`` (since the view shipped in #91).
Tombstones (``removed: true``) do not, so they first get it from their
``highlight.created`` event, then move like the rest: they are kept, not
dropped, because a replayed POST of a since-removed highlight reads the
document back by id (``create_highlight``), and without a lookup that read
would fail.

Downgrade moves every position-keyed document back to
``<ws_id>:<highlight_id>`` (tombstones keep the added ``position``, which the
old code ignores) and drops the lookup documents.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f5a7c9e1b2"
down_revision: str | Sequence[str] | None = "c9e1f3a5b7d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KEY = "(document ->> 'ws_id') || ':' || (document ->> 'highlight_id')"
_NEW_KEY = "(document ->> 'ws_id') || ':' || lpad(document ->> 'position', 20, '0')"
_IS_OLD = f"view = 'highlight' AND key NOT LIKE 'id:%' AND key = {_OLD_KEY}"
_HAS_POSITION = "document -> 'position' IS NOT NULL"  # else orphaned: dropped below
_IS_NEW = f"view = 'highlight' AND key NOT LIKE 'id:%' AND key = {_NEW_KEY}"


def upgrade() -> None:
    op.execute(
        "UPDATE view_documents_v2 SET document = document"
        " || jsonb_build_object('position', e.position) FROM events_v2 e"
        f" WHERE {_IS_OLD} AND document -> 'position' IS NULL"
        " AND e.type = 'highlight.created' AND e.ws_id = document ->> 'ws_id'"
        " AND e.payload ->> 'highlight_id' = document ->> 'highlight_id'"
    )
    op.execute(
        "INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, 'id:' || key, jsonb_build_object('position', document -> 'position')"
        f" FROM view_documents_v2 WHERE {_IS_OLD} AND {_HAS_POSITION}"
        " ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, {_NEW_KEY}, document FROM view_documents_v2"
        f" WHERE {_IS_OLD} AND {_HAS_POSITION}"
        " ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(f"DELETE FROM view_documents_v2 WHERE {_IS_OLD} AND {_HAS_POSITION}")


def downgrade() -> None:
    op.execute(
        "INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, {_OLD_KEY}, document FROM view_documents_v2 WHERE {_IS_NEW}"
        " ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(f"DELETE FROM view_documents_v2 WHERE {_IS_NEW}")
    op.execute("DELETE FROM view_documents_v2 WHERE view = 'highlight' AND key LIKE 'id:%'")
