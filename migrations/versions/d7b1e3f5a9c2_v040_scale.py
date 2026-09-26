"""v0.4 scale: claims, usage counters, view key index, search tables

Revision ID: d7b1e3f5a9c2
Revises: c4e8a2d6f1b3
Create Date: 2026-09-26 10:00:00.000000

#170 and #180 (S2) in one revision: the Alembic chain is linear.

- ``claims``: lease rows (``key`` held by ``holder`` until ``expires_at``).
- ``usage_counters``: fixed-window counters per (subject, name, window_start).
- ``view_documents_v2 (view, key COLLATE "C")``: prefix / keyset reads become
  an index range scan.
- ``search_documents``: the keyword leg (#180), resource-labelled like
  ``generated_documents``. ``search_tsv`` is ``to_tsvector('simple', text)``
  (language-agnostic) with a GIN index; ``text`` has a pg_trgm GIN index for
  CJK substring / fuzzy matching. This replaces #170's ``search_tsv`` column
  on ``view_documents_v2``.
- ``search_embeddings``: the semantic leg, one row per (resource, source_id,
  model) so a model change adds rows instead of rewriting them (ADR-0008).
  ``vector(MORPHLOOP_EMBEDDING_DIMS)`` with an HNSW cosine index.

Both extensions (``vector``, ``pg_trgm``) are on Supabase's allow-list; plain
Postgres needs the pgvector build (e.g. the ``pgvector/pgvector:pg16`` image).
Downgrade leaves the extensions installed: other schemas may use them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from harness.core.settings import Settings

# revision identifiers, used by Alembic.
revision: str = "d7b1e3f5a9c2"
down_revision: str | Sequence[str] | None = "c4e8a2d6f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("clock_timestamp()")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "claims",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("holder", sa.Text, nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_table(
        "usage_counters",
        sa.Column("subject", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("window_start", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("n", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.execute(
        'CREATE INDEX view_documents_v2_view_key_c_idx ON view_documents_v2 (view, key COLLATE "C")'
    )

    op.create_table(
        "search_documents",
        sa.Column("resource", sa.Text, primary_key=True),
        sa.Column("source_id", sa.Text, primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('simple', text)", persisted=True),
        ),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=_NOW),
    )
    op.create_index(
        "search_documents_tsv_idx", "search_documents", ["search_tsv"], postgresql_using="gin"
    )
    op.create_index(
        "search_documents_text_trgm_idx",
        "search_documents",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )

    op.create_table(
        "search_embeddings",
        sa.Column("resource", sa.Text, primary_key=True),
        sa.Column("source_id", sa.Text, primary_key=True),
        sa.Column("model", sa.Text, primary_key=True),
        sa.Column("embedding", Vector(Settings().morphloop_embedding_dims), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=_NOW),
        sa.ForeignKeyConstraint(
            ["resource", "source_id"],
            ["search_documents.resource", "search_documents.source_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "search_embeddings_hnsw_idx",
        "search_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("search_embeddings")
    op.drop_table("search_documents")
    op.execute("DROP INDEX view_documents_v2_view_key_c_idx")
    op.drop_table("usage_counters")
    op.drop_table("claims")
