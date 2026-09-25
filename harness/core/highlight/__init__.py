"""The highlight resource: a learner's free-form highlights (ADR-0018, #34/#60).

See :mod:`harness.core.highlight.anchor` (the one shape rule JSON Schema
cannot express), :mod:`harness.core.highlight.view` (``HighlightView``) and
:mod:`harness.core.highlight.service` (create/remove/list over the v0.2 event
log). ``harness/api/v2/routes/highlight.py`` wires this to HTTP.
"""

from __future__ import annotations
