"""Search Ports: keyword, semantic, rerank and embedding backends (issue #180).

One query across the searchable corpus (textbook blocks, drill questions,
memos) resolves through up to three legs, each behind its own Port so the app
picks concrete adapters (ADR-0009) and no implementation lives in core:

- :class:`KeywordSearchBackend` -- deterministic Postgres ``tsvector('simple')``
  + ``pg_trgm``. Never calls an LLM; reproducible and explainable.
- :class:`SemanticSearchBackend` -- ``pgvector`` similarity over a query
  already embedded by an :class:`EmbeddingProvider`.
- :class:`Reranker` -- optional last stage over already-fused hits (e.g.
  Cohere rerank, or an LLM-as-reranker through the existing ``LLMProvider``
  Port).
- :class:`EmbeddingProvider` -- provider-neutral text embedding call (e.g.
  via ``litellm.embedding()``), mirroring :class:`~harness.core.ports.llm.LLMProvider`.

Fusing the keyword and semantic legs into one ``hybrid`` ranking (RRF) and
picking which concrete backends serve a given mode is wiring, not a Port
(issue #180 S5); this module only declares the shapes.

Drill expected answers are never searchable. Only a drill item's
question/prompt text is an eligible source for the search corpus (migration
``d7b1e3f5a9c2_v040_scale.py``'s ``search_documents``/``search_embeddings``);
a drill item's ``expected`` answer and its reference solution are withheld
entirely -- never embedded, never indexed, never returned as a result. This
is enforced by each adapter's field allowlist when it writes a row, not by
convention: no type in this module accepts or returns an expected-answer or
reference-solution value. :class:`SearchHit`, the only result type, carries
only ``kind``/``id``/``parent_id``/``score``/``source`` (see
``contracts/schemas/search/README.md``).

Every keyword/semantic query is scoped to one authenticated learner
(``user_id``, from auth -- never from the request body). A backend returns a
row only when it is shared corpus (pack content: textbook, drill questions) or
owned by that learner (e.g. memos); the filter applies before ranking and
``limit``, so another learner's rows never affect results or scores.

Value types are frozen dataclasses; see ``harness.core.ports`` for why.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

type SearchMode = Literal["keyword", "semantic", "hybrid"]
type SearchSource = Literal["keyword", "semantic", "hybrid"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchHit:
    """One ranked result.

    Matches ``contracts/schemas/search/response.json#/properties/results/items``.
    ``kind`` is a resource label (ADR-0018), not a closed enum; ``id`` is that
    resource's own id, opaque here. ``parent_id`` is the owning resource's id
    when re-fetching needs it (e.g. the document of a block, the workspace of a
    memo entry); ``None`` for a top-level resource.
    """

    kind: str
    id: str
    score: float
    source: SearchSource
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.kind or not self.id:
            raise ValueError("a search hit needs a non-empty kind and id")
        if self.parent_id == "":
            raise ValueError("parent_id must be non-empty when set")

    def to_dict(self) -> dict[str, str | float]:
        """Return the wire form, valid against ``search/response.json``'s result item."""
        wire: dict[str, str | float] = {
            "kind": self.kind,
            "id": self.id,
            "score": self.score,
            "source": self.source,
        }
        if self.parent_id is not None:
            wire["parent_id"] = self.parent_id
        return wire


class SearchBackendError(Exception):
    """Base class for keyword/semantic search and reranker backend failures."""


@dataclass(frozen=True, slots=True, kw_only=True)
class KeywordSearchRequest:
    """A deterministic keyword query for one learner. ``resources`` empty means every
    resource. Only shared rows and rows owned by ``user_id`` are eligible."""

    text: str
    user_id: str
    limit: int
    resources: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("a search request needs non-empty text")
        if not self.user_id:
            raise ValueError("a search request needs the authenticated user_id")
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


class KeywordSearchBackend(Protocol):
    """Deterministic keyword search (``tsvector('simple')`` + ``pg_trgm``). Never calls
    an LLM; every returned hit's ``source`` is ``"keyword"``."""

    def search(self, request: KeywordSearchRequest) -> Sequence[SearchHit]:
        """Return hits ranked best-first, at most ``request.limit`` of them, never a row
        owned by a learner other than ``request.user_id`` (filtered before ranking).

        Raises :class:`SearchBackendError` on a backend failure.
        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticSearchRequest:
    """A similarity query for one learner over an already-embedded query vector (see
    :class:`EmbeddingProvider`). Only shared rows and rows owned by ``user_id`` are
    eligible."""

    embedding: Sequence[float]
    user_id: str
    limit: int
    resources: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not self.embedding:
            raise ValueError("a semantic search request needs a non-empty embedding")
        if not self.user_id:
            raise ValueError("a search request needs the authenticated user_id")
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


class SemanticSearchBackend(Protocol):
    """Vector similarity search (``pgvector``). Every returned hit's ``source`` is
    ``"semantic"``."""

    def search(self, request: SemanticSearchRequest) -> Sequence[SearchHit]:
        """Return hits ranked best-first (highest similarity), at most ``request.limit``,
        never a row owned by a learner other than ``request.user_id`` (filtered before
        ranking).

        Raises :class:`SearchBackendError` on a backend failure.
        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RerankRequest:
    """Re-score ``candidates`` against ``query``; the optional last stage."""

    query: str
    candidates: Sequence[SearchHit]
    limit: int

    def __post_init__(self) -> None:
        if not self.query:
            raise ValueError("a rerank request needs a non-empty query")
        if self.limit < 1:
            raise ValueError("limit must be >= 1")


class Reranker(Protocol):
    """Optional last stage over already-fused hits. Never given a reference solution or a
    drill item's expected answer: ``candidates`` carry only :class:`SearchHit` values."""

    def rerank(self, request: RerankRequest) -> Sequence[SearchHit]:
        """Return ``request.candidates`` re-ordered/re-scored, at most ``request.limit`` of them.

        Raises :class:`SearchBackendError` on a backend failure.
        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddingProvenance:
    """Embedding settings the pack declares, mirroring
    :class:`~harness.core.ports.llm.LLMProvenance` (ADR-0002): the app/pack supplies
    provider, model and dims; core adds no defaults."""

    provider: str
    model: str
    dims: int

    def __post_init__(self) -> None:
        if self.dims < 1:
            raise ValueError("dims must be >= 1")

    def to_dict(self) -> dict[str, str | int]:
        """Return the wire form."""
        return {"provider": self.provider, "model": self.model, "dims": self.dims}


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddingRequest:
    """Embed ``texts`` with ``embedding``'s provider/model/dims."""

    texts: Sequence[str]
    embedding: EmbeddingProvenance

    def __post_init__(self) -> None:
        if not self.texts:
            raise ValueError("an embedding request needs at least one text")


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddingResponse:
    """One vector per :attr:`EmbeddingRequest.texts` entry, same order, plus the
    provenance actually used."""

    vectors: Sequence[Sequence[float]]
    provenance: EmbeddingProvenance


class EmbeddingError(Exception):
    """Base class for embedding adapter failures (transport, auth, rate limit, etc.)."""


class EmbeddingOutputError(EmbeddingError):
    """The provider returned no usable vector (wrong dims, empty response)."""


class EmbeddingProvider(Protocol):
    """Provider-neutral text embedding call (e.g. via ``litellm.embedding()``), mirroring
    :class:`~harness.core.ports.llm.LLMProvider`. Synchronous, like the other Ports."""

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed ``request.texts``.

        Raises :class:`EmbeddingOutputError` when a returned vector's length does not
        match ``request.embedding.dims``, :class:`EmbeddingError` for other failures.
        """
        ...
