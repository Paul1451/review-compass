"""Proves the autouse env fixture in conftest.py: it must beat variables set earlier."""

import os
from collections.abc import Iterator

import pytest
from conftest import _ISOLATED_ENV


@pytest.fixture(scope="session", autouse=True)
def _polluted_shell() -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in _ISOLATED_ENV}
    for name in _ISOLATED_ENV:
        os.environ[name] = "from-the-developer-shell"
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_environment_is_isolated_from_the_shell() -> None:
    assert [n for n in _ISOLATED_ENV if n in os.environ] == []
