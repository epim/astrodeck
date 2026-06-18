"""Shared pytest fixtures/path setup for the server test suite.

Puts the sibling ``relay/`` repo dir on ``sys.path`` so the W3 END-TO-END test
(tests/test_remote_e2e.py) can import the REAL relay-lane package alongside the
home ``astrodeck`` package and wire them through an in-memory tunnel. The relay
package is pure Python (+ websockets, already in the venv); if the dir is absent
the e2e test ``importorskip``s.
"""
from __future__ import annotations

import sys
from pathlib import Path

# server/tests/conftest.py -> parents[2] is the git root (C:\Users\bear\astro).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELAY_DIR = _REPO_ROOT / "relay"
if _RELAY_DIR.is_dir():
    p = str(_RELAY_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
