"""Sittings derived from session events (#34, #54): a pure function, no storage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness.core.ports.events_v2 import StoredEventV2
from harness.core.session.sittings import MAX_IDLE_MINUTES, Sitting, sittings


def _event(n: int, minute: int) -> StoredEventV2:
    return StoredEventV2(
        id=f"0190f5a2-7c3e-7d4b-8a1f-{n:012d}",
        type="probe.created",
        actor="learner",
        user_id="usr_alice",
        session_id="ses_01",
        payload={},
        position=n,
        created_at=datetime(2026, 1, 1, 10, minute, tzinfo=UTC),
    )


def test_no_events_gives_no_sittings() -> None:
    assert sittings([], idle_minutes=10) == []


def test_events_within_the_idle_window_are_one_sitting() -> None:
    events = [_event(1, 0), _event(2, 5), _event(3, 9)]
    assert sittings(events, idle_minutes=10) == [
        Sitting(started_at=events[0].created_at, ended_at=events[2].created_at)
    ]


def test_a_gap_larger_than_idle_minutes_splits_into_two_sittings() -> None:
    events = [_event(1, 0), _event(2, 5), _event(3, 30)]
    assert sittings(events, idle_minutes=10) == [
        Sitting(started_at=events[0].created_at, ended_at=events[1].created_at),
        Sitting(started_at=events[2].created_at, ended_at=events[2].created_at),
    ]


def test_input_order_does_not_matter() -> None:
    events = [_event(3, 30), _event(1, 0), _event(2, 5)]
    ordered = sittings(events, idle_minutes=10)
    reordered = sittings(list(reversed(events)), idle_minutes=10)
    assert ordered == reordered


def test_idle_minutes_must_be_positive() -> None:
    with pytest.raises(ValueError, match="idle_minutes"):
        sittings([_event(1, 0)], idle_minutes=0)


def test_idle_minutes_beyond_timedelta_range_raises_instead_of_overflowing() -> None:
    with pytest.raises(ValueError, match="idle_minutes"):
        sittings([_event(1, 0)], idle_minutes=MAX_IDLE_MINUTES + 1)
