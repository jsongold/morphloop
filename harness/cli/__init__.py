"""The harness command line (ADR-0017): ``generate``, ``import``, ``rebuild``.

Run as ``python -m harness.cli <command>``; ``main`` is the entry point.

Like ``harness/api``, this package is wiring: it may import ``harness.core``,
``harness.adapters`` and ``domains``, and holds no domain logic of its own.
The one thing it owns is the write side of a pack
(:mod:`harness.cli.pack_files`), because ``authoring`` generation is the only
place that adds files to a pack and it runs from here.

- :mod:`harness.cli.generate` -- the ``authoring`` generation path (ADR-0014).
- :mod:`harness.cli.import_pack` -- pack files -> pack projection (ADR-0015).
- :mod:`harness.cli.rebuild` -- replay the event log into the loop projections.
- :mod:`harness.cli.wiring` -- which adapter implements which Port here.
"""

from harness.cli.main import build_parser, main

__all__ = ["build_parser", "main"]
