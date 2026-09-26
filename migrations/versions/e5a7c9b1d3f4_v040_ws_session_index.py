"""v0.4: backfill the per-user ws/session indexes and re-key ws.threads (#175)

Revision ID: e5a7c9b1d3f4
Revises: d7b1e3f5a9c2
Create Date: 2026-09-26 18:00:00.000000

#175 adds two views and re-keys one; a database with existing events needs
them filled before ``GET /ws``, ``GET /sessions`` and ``GET /ws/{id}/threads``
read them:

- ``ws.by_user``: ``<user_id>/*/<newest-first position>`` and
  ``<user_id>/<session_id>/<newest-first position>`` -> ``{ws_id}``, from
  ``ws.created`` events.
- ``session.by_user``: ``<user_id>/<position>`` -> ``{session_id}``, from
  ``session.created`` events.
- ``ws.threads``: key ``<ws_id>/<thread_id>`` becomes ``<ws_id>/<position>``
  (the document already carries ``position``).

Positions are zero-padded to 19 digits; newest-first is
``9999999999999999999 - position`` (numeric: it exceeds bigint).

Plain SQL on purpose, not ``morphloop rebuild``: a migration must stay a
frozen snapshot, and replaying through the live ``View`` classes would change
what this revision does whenever those classes change. Only stored event
columns are copied; nothing is re-derived (no LLM, ADR-0013). Rebuilding with
``morphloop rebuild`` afterwards yields the same documents.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a7c9b1d3f4"
down_revision: str | Sequence[str] | None = "d7b1e3f5a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEWEST_FIRST = "lpad((9999999999999999999::numeric - position)::text, 19, '0')"


def upgrade() -> None:
    """Fill ws.by_user / session.by_user; re-key ws.threads by position."""
    for scope in ("'*'", "session_id"):
        op.execute(
            f"""
            INSERT INTO view_documents_v2 (view, key, document)
            SELECT 'ws.by_user', user_id || '/' || {scope} || '/' || {_NEWEST_FIRST},
                   jsonb_build_object('ws_id', ws_id)
            FROM events_v2
            WHERE type = 'ws.created' AND ws_id IS NOT NULL AND {scope} IS NOT NULL
            ON CONFLICT (view, key) DO NOTHING
            """
        )
    op.execute(
        """
        INSERT INTO view_documents_v2 (view, key, document)
        SELECT 'session.by_user', user_id || '/' || lpad(position::text, 19, '0'),
               jsonb_build_object('session_id', session_id)
        FROM events_v2
        WHERE type = 'session.created' AND session_id IS NOT NULL
        ON CONFLICT (view, key) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE view_documents_v2
        SET key = split_part(key, '/', 1) || '/' || lpad(document->>'position', 19, '0')
        WHERE view = 'ws.threads'
        """
    )


def downgrade() -> None:
    """Drop the index documents; re-key ws.threads back to thread_id."""
    op.execute("DELETE FROM view_documents_v2 WHERE view IN ('ws.by_user', 'session.by_user')")
    op.execute(
        """
        UPDATE view_documents_v2
        SET key = split_part(key, '/', 1) || '/' || (document->>'thread_id')
        WHERE view = 'ws.threads'
        """
    )
