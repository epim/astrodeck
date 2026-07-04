"""Shared pytest fixtures/path setup for the server test suite.

Puts the sibling ``relay/`` repo dir on ``sys.path`` so the W3 END-TO-END test
(tests/test_remote_e2e.py) can import the REAL relay-lane package alongside the
home ``astrodeck`` package and wire them through an in-memory tunnel. The relay
package is pure Python (+ websockets, already in the venv); if the dir is absent
the e2e test ``importorskip``s.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# server/tests/conftest.py -> parents[1] is server/, parents[2] the git root.
_SERVER_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]

# The ``server/`` dir on sys.path so ``tools.mock_nina`` (server/tools/, a package
# OUTSIDE the installed ``astrodeck`` package -- setuptools find only includes
# ``astrodeck*``) imports at COLLECTION time. Without this the suite errors under
# CI's `pip install -e . && pytest` (the `pytest` console script does NOT add the
# CWD to sys.path, unlike `python -m pytest`), turning the whole server gate red.
# conftest.py is imported before the test modules in this dir, so the shim lands
# before test_nina.py's `from tools.mock_nina import ...` is collected.
_server_str = str(_SERVER_DIR)
if _server_str not in sys.path:
    sys.path.insert(0, _server_str)

_RELAY_DIR = _REPO_ROOT / "relay"
if _RELAY_DIR.is_dir():
    p = str(_RELAY_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)

# Repo root on sys.path so the standalone ``supervisor`` package (which lives at
# the repo root, outside the installed ``astrodeck`` package) imports in tests.
_root_str = str(_REPO_ROOT)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)


@pytest.fixture(autouse=True)
def _reset_hub_singleton_locks():
    """Test-isolation seam: ``astrodeck.hub.hub`` is a process-wide singleton, but
    many tests each spin up their own ``TestClient(app)`` (own event loop) or their
    own ``asyncio.run``-driven async test (pytest-asyncio, also its own loop)
    against that SAME singleton. A plain ``asyncio.Lock`` permanently binds to
    whichever event loop first acquires it (see ``asyncio.mixins._LoopBoundMixin``)
    and never rebinds, so a lock touched by one test's loop raises
    ``RuntimeError: ... is bound to a different event loop`` when a later test's
    loop touches it again -- a cross-test-order flake, not a real bug (a real
    process only ever has one event loop for its whole lifetime). Give every test
    a fresh set of locks so the singleton never leaks loop affinity across tests."""
    import astrodeck.hub as hub_mod
    h = hub_mod.hub
    h._connect_lock = asyncio.Lock()
    h._capture_lock = asyncio.Lock()
    h._motion_lock = asyncio.Lock()
    yield
