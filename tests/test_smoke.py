"""Smoke test verifying the top-level packages import cleanly."""


def test_import_packages() -> None:
    import domains
    import harness

    assert harness is not None
    assert domains is not None
