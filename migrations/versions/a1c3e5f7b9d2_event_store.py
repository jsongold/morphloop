"""event store: learning_events and projection_documents

Revision ID: a1c3e5f7b9d2
Revises: f3f0928fbd1d
Create Date: 2026-09-23 10:00:00.000000

ADR-0008: append-only ``learning_events`` ordered by the DB-assigned
``position``, a store-wide unique ``idempotency_key``, and UPDATE / DELETE /
TRUNCATE rejected by triggers (AC-F1). Projections are JSONB documents
addressed by ``(name, key)`` (docs/ARCHITECTURE.md, Persistence).

The DDL is written out here rather than derived from the adapter's table
definitions, so this revision stays fixed when the adapter code changes.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c3e5f7b9d2"
down_revision: str | Sequence[str] | None = "f3f0928fbd1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE learning_events (
            position               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            recorded_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            event_id               TEXT NOT NULL,
            event_type             TEXT NOT NULL,
            event_version          INTEGER NOT NULL,
            occurred_at            TIMESTAMPTZ NOT NULL,
            idempotency_key        TEXT,
            causation_id           TEXT,
            correlation_id         TEXT,
            learner_id             TEXT NOT NULL,
            session_id             TEXT NOT NULL,
            attempt_id             TEXT,
            activity_definition_id TEXT,
            actor                  TEXT NOT NULL,
            payload                JSONB NOT NULL,
            CONSTRAINT learning_events_event_id_key UNIQUE (event_id),
            CONSTRAINT learning_events_idempotency_key_key UNIQUE (idempotency_key),
            CONSTRAINT learning_events_event_version_check CHECK (event_version >= 1),
            CONSTRAINT learning_events_actor_check
                CHECK (actor IN ('learner', 'tutor', 'system')),
            CONSTRAINT learning_events_attempt_pair_check
                CHECK ((attempt_id IS NULL) = (activity_definition_id IS NULL)),
            CONSTRAINT learning_events_payload_object_check
                CHECK (jsonb_typeof(payload) = 'object')
        )
        """
    )
    op.execute(
        "CREATE INDEX learning_events_session_position_idx "
        "ON learning_events (session_id, position)"
    )
    op.execute(
        """
        CREATE FUNCTION learning_events_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'learning_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER learning_events_no_update_delete
            BEFORE UPDATE OR DELETE ON learning_events
            FOR EACH ROW EXECUTE FUNCTION learning_events_reject_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER learning_events_no_truncate
            BEFORE TRUNCATE ON learning_events
            FOR EACH STATEMENT EXECUTE FUNCTION learning_events_reject_mutation()
        """
    )
    op.execute(
        """
        CREATE TABLE projection_documents (
            name     TEXT NOT NULL,
            key      TEXT NOT NULL,
            document JSONB NOT NULL,
            CONSTRAINT projection_documents_pkey PRIMARY KEY (name, key),
            CONSTRAINT projection_documents_document_object_check
                CHECK (jsonb_typeof(document) = 'object')
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE projection_documents")
    op.execute("DROP TABLE learning_events")
    op.execute("DROP FUNCTION learning_events_reject_mutation()")
