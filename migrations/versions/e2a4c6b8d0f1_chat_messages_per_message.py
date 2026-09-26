"""chat.messages: one view document per message (#177)

Revision ID: e2a4c6b8d0f1
Revises: d7b1e3f5a9c2
Create Date: 2026-09-26 23:00:00.000000

Data-only: re-keys each existing ``<ws_id>/<thread_id>`` thread document
(``{"messages": [...]}``) into one row per message keyed
``<ws_id>/<thread_id>/<position:020d>``, the position taken from the
``chat.sent`` / ``chat.replied`` event carrying that ``message_id``. Same
result as ``morphloop rebuild``, without a manual step on deploy.
Downgrade collapses the per-message rows back into thread documents.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2a4c6b8d0f1"
down_revision: str | Sequence[str] | None = "d7b1e3f5a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO view_documents_v2 (view, key, document)
        SELECT 'chat.messages', d.key || '/' || lpad(e.position::text, 20, '0'), m.value
        FROM view_documents_v2 d
        CROSS JOIN LATERAL jsonb_array_elements(d.document -> 'messages') AS m(value)
        JOIN events_v2 e
          ON e.type IN ('chat.sent', 'chat.replied')
         AND e.ws_id || '/' || (e.payload ->> 'thread_id') = d.key
         AND e.payload ->> 'message_id' = m.value ->> 'message_id'
        WHERE d.view = 'chat.messages'
          AND jsonb_typeof(d.document -> 'messages') = 'array'
        ON CONFLICT (view, key) DO NOTHING
        """
    )
    op.execute(
        "DELETE FROM view_documents_v2 WHERE view = 'chat.messages' AND document ? 'messages'"
    )


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO view_documents_v2 (view, key, document)
        SELECT 'chat.messages', substring(key from '^(.*)/[0-9]{20}$'),
               jsonb_build_object('messages', jsonb_agg(document ORDER BY key))
        FROM view_documents_v2
        WHERE view = 'chat.messages' AND key ~ '/[0-9]{20}$'
        GROUP BY 2
        ON CONFLICT (view, key) DO UPDATE SET document = EXCLUDED.document
        """
    )
    op.execute("DELETE FROM view_documents_v2 WHERE view = 'chat.messages' AND key ~ '/[0-9]{20}$'")
