"""Sittings derived from session events (#34, #54).

A sitting is never stored (ADR-0018 says so explicitly): it starts at the
first event and ends when no further event of the session arrives within
``idle_minutes`` of the previous one. ``idle_minutes`` is a caller-supplied
argument, never read from :mod:`harness.core.settings` -- an app setting for
it is a separate, later issue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from harness.core.ports.events_v2 import StoredEventV2


@dataclass(frozen=True, slots=True, kw_only=True)
class Sitting:
    started_at: datetime
    ended_at: datetime


def sittings(events: Sequence[StoredEventV2], *, idle_minutes: int) -> list[Sitting]:
    """One sitting per run of ``events`` with no gap larger than ``idle_minutes``
    between consecutive ``created_at`` times. ``events`` need not be sorted."""
    if idle_minutes <= 0:
        raise ValueError(f"idle_minutes must be positive, got {idle_minutes}")
    ordered = sorted(events, key=lambda event: event.created_at)
    if not ordered:
        return []
    gap = timedelta(minutes=idle_minutes)
    out: list[Sitting] = []
    started_at = ordered[0].created_at
    previous = started_at
    for event in ordered[1:]:
        if event.created_at - previous > gap:
            out.append(Sitting(started_at=started_at, ended_at=previous))
            started_at = event.created_at
        previous = event.created_at
    out.append(Sitting(started_at=started_at, ended_at=previous))
    return out
