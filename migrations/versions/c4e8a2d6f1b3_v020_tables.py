"""v0.2.0 tables: events_v2, view_documents_v2, generated_documents

Revision ID: c4e8a2d6f1b3
Revises: a1c3e5f7b9d2
Create Date: 2026-09-25 10:00:00.000000

#34, #46. The v0.1 tables stay until the v0.1 removal.

- ``events_v2``: envelope v2 (``contracts/schemas/events/envelope/v2/``).
  ``id`` is client-generated and unique (the idempotency key); ``position``
  is the only order; UPDATE / DELETE / TRUNCATE are rejected (append-only).
- ``view_documents_v2``: View documents addressed by ``(view, key)``.
- ``generated_documents``: runtime-generated content of every resource (drill
  items, textbook docs, ...), told apart by ``resource``. Finalized content is
  immutable (ADR-0014), so UPDATE is rejected too.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8a2d6f1b3"
down_revision: str | Sequence[str] | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE events_v2 (
            position   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            id         TEXT NOT NULL,
            type       TEXT NOT NULL,
            actor      TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            session_id TEXT,
            ws_id      TEXT,
            payload    JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT events_v2_id_key UNIQUE (id),
            CONSTRAINT events_v2_actor_check CHECK (actor IN ('learner', 'assistant', 'system')),
            CONSTRAINT events_v2_payload_object_check CHECK (jsonb_typeof(payload) = 'object')
        )
        """
    )
    for column in ("user_id", "session_id", "ws_id"):
        op.execute(
            f"CREATE INDEX events_v2_{column}_position_idx ON events_v2 ({column}, position)"
        )
    op.execute(
        """
        CREATE FUNCTION v2_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '%: % is not allowed', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER events_v2_no_update_delete BEFORE UPDATE OR DELETE ON events_v2
            FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER events_v2_no_truncate BEFORE TRUNCATE ON events_v2
            FOR EACH STATEMENT EXECUTE FUNCTION v2_reject_mutation()
        """
    )
    op.execute(
        """
        CREATE TABLE view_documents_v2 (
            view     TEXT NOT NULL,
            key      TEXT NOT NULL,
            document JSONB NOT NULL,
            CONSTRAINT view_documents_v2_pkey PRIMARY KEY (view, key),
            CONSTRAINT view_documents_v2_document_object_check
                CHECK (jsonb_typeof(document) = 'object')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE generated_documents (
            resource   TEXT NOT NULL,
            id         TEXT NOT NULL,
            body       JSONB NOT NULL,
            labels     TEXT[] NOT NULL DEFAULT '{}',
            provenance JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT generated_documents_pkey PRIMARY KEY (resource, id),
            CONSTRAINT generated_documents_provenance_object_check
                CHECK (jsonb_typeof(provenance) = 'object')
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER generated_documents_no_update BEFORE UPDATE ON generated_documents
            FOR EACH ROW EXECUTE FUNCTION v2_reject_mutation()
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE generated_documents")
    op.execute("DROP TABLE view_documents_v2")
    op.execute("DROP TABLE events_v2")
    op.execute("DROP FUNCTION v2_reject_mutation()")
