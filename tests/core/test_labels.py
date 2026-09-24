"""Label syntax, reserved sys: labels, topic labels and the pack vocabulary."""

from __future__ import annotations

import pytest

from harness.core.labels import LabelError, check_labels, label_problems

VOCAB = {"dns", "origin:pack", "level:intro"}
TOPICS = {"dns-basics", "dns.resolution"}


def test_valid_labels_pass() -> None:
    labels = ["dns", "origin:pack", "sys:holdout", "topic:dns-basics", "topic:dns.resolution"]
    assert label_problems(labels, vocabulary=VOCAB, topic_ids=TOPICS) == {}
    check_labels(labels, vocabulary=VOCAB, topic_ids=TOPICS)


def test_every_offending_label_is_reported_with_a_reason() -> None:
    bad = ["DNS", "a:b:c", "", "sys:secret", "topic:missing", "tls", "origin:generated"]
    with pytest.raises(LabelError) as info:
        check_labels(["dns", *bad], vocabulary=VOCAB, topic_ids=TOPICS)
    problems = info.value.offending
    assert set(problems) == set(bad)
    assert "lowercase" in problems["DNS"] and "lowercase" in problems["a:b:c"]
    assert "sys:holdout" in problems["sys:secret"]
    assert "topic tree" in problems["topic:missing"]
    assert "vocabulary" in problems["tls"]
    assert "'topic:missing'" in str(info.value)
