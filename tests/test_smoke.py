"""Smoke test: the package imports and exposes a version."""

import intake


def test_package_exposes_version() -> None:
    assert isinstance(intake.__version__, str)
    assert intake.__version__
