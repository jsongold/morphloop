"""highlight: key by ``<ws_id>:<position:020d>`` for creation-order paging (#220)

Revision ID: d3f5a7c9e1b2
Revises: c9e1f3a5b7d0
Create Date: 2026-09-27 18:00:00.000000

Data-only. Each active ``<ws_id>:<highlight_id>`` document of the
``highlight`` view moves to ``<ws_id>:<position:020d>`` (both parts read from
the document; every stored active document carries ``position`` since the
view shipped in #91), and gains an ``id:<ws_id>:<highlight_id>`` ->
``{"position": ...}`` lookup document. Same result as ``morphloop rebuild``,
without a manual step on deploy.

Old tombstones (``removed: true``) are dropped, not re-keyed: they carry no
``position``, and nothing depends on them -- a DELETE of an id with no lookup
document is the same 404 as one that finds a tombstone, list reads filter
tombstones out, and idempotent replay checks the event log, not the view. A
later ``morphloop rebuild`` recreates them at their position keys.

Downgrade moves every position-keyed document (active or tombstone) back to
``<ws_id>:<highlight_id>`` and drops the lookup documents.
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
_IS_NEW = f"view = 'highlight' AND key NOT LIKE 'id:%' AND key = {_NEW_KEY}"
_ACTIVE = "NOT coalesce((document ->> 'removed')::boolean, false)"


def upgrade() -> None:
    op.execute(
        "INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, 'id:' || key, jsonb_build_object('position', document -> 'position')"
        f" FROM view_documents_v2 WHERE {_IS_OLD} AND {_ACTIVE}"
        " ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, {_NEW_KEY}, document FROM view_documents_v2"
        f" WHERE {_IS_OLD} AND {_ACTIVE}"
        " ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(f"DELETE FROM view_documents_v2 WHERE {_IS_OLD}")


def downgrade() -> None:
    op.execute(
        "INSERT INTO view_documents_v2 (view, key, document)"
        f" SELECT view, {_OLD_KEY}, document FROM view_documents_v2 WHERE {_IS_NEW}"
        " ON CONFLICT (view, key) DO NOTHING"
    )
    op.execute(f"DELETE FROM view_documents_v2 WHERE {_IS_NEW}")
    op.execute("DELETE FROM view_documents_v2 WHERE view = 'highlight' AND key LIKE 'id:%'")
