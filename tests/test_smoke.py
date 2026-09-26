"""Smoke test verifying the top-level packages import cleanly."""


def test_import_packages() -> None:
    import harness

    assert harness is not None
