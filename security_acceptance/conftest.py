"""Opt-in controls for the pre-implementation security acceptance suite.

These tests intentionally describe the *target* security contract.  They stay
out of ordinary test runs until that contract has been implemented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "server"
RELAY_ROOT = ROOT / "relay"

for candidate in (str(SERVER_ROOT), str(RELAY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("astrodeck security acceptance")
    group.addoption(
        "--run-security-acceptance",
        action="store_true",
        default=False,
        help="run the intentionally opt-in platform-security contract tests",
    )
    group.addoption(
        "--run-security-live",
        action="store_true",
        default=False,
        help="also run live Docker, TLS/proxy, and host-platform checks",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "security_live: needs the named host runtime or an explicitly deployed lab stack",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("--run-security-acceptance"):
        skip = pytest.mark.skip(
            reason="target-state suite; pass --run-security-acceptance explicitly"
        )
        for item in items:
            item.add_marker(skip)
        return

    if not config.getoption("--run-security-live"):
        skip_live = pytest.mark.skip(
            reason="live platform checks require --run-security-live"
        )
        for item in items:
            if item.get_closest_marker("security_live") is not None:
                item.add_marker(skip_live)
