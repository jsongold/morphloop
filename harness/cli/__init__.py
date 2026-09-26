"""The harness command line (ADR-0017): ``rebuild``.

Run as ``python -m harness.cli <command>``; ``main`` is the entry point.

Like ``harness/api``, this package is wiring: it may import ``harness.core``,
and ``harness.adapters``, and holds no domain logic of its own.

- :mod:`harness.cli.rebuild` -- replay the event log into the v0.2 views.
- :mod:`harness.cli.wiring` -- which adapter implements which Port here.
"""

from harness.cli.main import app, main

__all__ = ["app", "main"]
