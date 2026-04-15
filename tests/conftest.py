"""Shared pytest fixtures."""

from __future__ import annotations

import pathlib

import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--appid", action="store", default=None, help="TCaptcha APP_ID for integration tests")


@pytest.fixture()
def fixtures_dir() -> pathlib.Path:
    return FIXTURES_DIR
