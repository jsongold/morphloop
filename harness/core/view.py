"""View: a read-only resource built from v2 events (#34, #48).

A subclass declares its stored ``name`` and the event ``handles`` it consumes;
defining it (importing its module) registers it, so there is no central list to
edit. A View does three things only:

- receive: :meth:`View.apply` updates its documents for one event;
- rebuild: :meth:`View.rebuild` clears its documents and re-applies events;
- read: :meth:`View.get` / :meth:`View.list`.

Documents live in the :class:`~harness.core.ports.events_v2.ViewDocumentStore`
under ``(name, key)``. :func:`dispatch` is called with the transaction that
appended the event, so the append and every view update commit or roll back
together (ADR-0008). Views are stateless: every method is a classmethod.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import ClassVar, overload

from harness.core.ports.events_v2 import StoredEventV2, ViewDocumentStore
from harness.core.ports.json_types import JsonObject

_REGISTRY: dict[str, type[View]] = {}


class View:
    """Base class; subclasses set ``name`` and ``handles`` and implement :meth:`apply`."""

    name: ClassVar[str]
    handles: ClassVar[frozenset[str]]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "name", "") or not getattr(cls, "handles", None):
            raise TypeError(f"View {cls.__qualname__} must declare a name and non-empty handles")
        existing = _REGISTRY.get(cls.name)
        if existing is not None and (existing.__module__, existing.__name__) != (
            cls.__module__,
            cls.__name__,
        ):
            raise TypeError(f"view name {cls.name!r} is already registered by {existing!r}")
        _REGISTRY[cls.name] = cls

    @classmethod
    def apply(cls, event: StoredEventV2, tx: ViewDocumentStore) -> None:
        """Update this view's documents for ``event`` (its type is in ``handles``)."""
        raise NotImplementedError

    @classmethod
    def rebuild(cls, events: Iterable[StoredEventV2], tx: ViewDocumentStore) -> None:
        """Drop every document and re-apply the handled ``events`` in the given order."""
        tx.clear_view(cls.name)
        for event in events:
            if event.type in cls.handles:
                cls.apply(event, tx)

    @classmethod
    def get(cls, tx: ViewDocumentStore, key: str) -> JsonObject | None:
        return tx.get_view(cls.name, key)

    @overload
    @classmethod
    def list(
        cls, tx: ViewDocumentStore, *, key_prefix: str = ""
    ) -> Sequence[tuple[str, JsonObject]]: ...

    @overload
    @classmethod
    def list(
        cls, tx: ViewDocumentStore, *, key_prefix: str = "", after: str | None, limit: int
    ) -> tuple[Sequence[tuple[str, JsonObject]], str | None]: ...

    @classmethod
    def list(
        cls,
        tx: ViewDocumentStore,
        *,
        key_prefix: str = "",
        after: str | None = None,
        limit: int | None = None,
    ) -> Sequence[tuple[str, JsonObject]] | tuple[Sequence[tuple[str, JsonObject]], str | None]:
        """Every matching document (existing callers), or, when ``limit`` is
        given, one page plus its next cursor (#173 keyset pagination)."""
        page, next_cursor = tx.list_view(cls.name, key_prefix=key_prefix, after=after, limit=limit)
        return (page, next_cursor) if limit is not None else page


def registered_views() -> Mapping[str, type[View]]:
    """Every registered view by name, in registration order (read-only)."""
    return MappingProxyType(_REGISTRY)


def dispatch(event: StoredEventV2, tx: ViewDocumentStore) -> None:
    """Apply ``event`` to every registered view that handles its type, inside ``tx``.

    Call it with the transaction that appended ``event``, and only when the
    append ``created`` it (a resend was already applied).
    """
    for view in list(_REGISTRY.values()):
        if event.type in view.handles:
            view.apply(event, tx)
