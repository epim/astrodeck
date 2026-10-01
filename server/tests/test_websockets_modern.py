"""OPEN-015: the WebSocket integration uses the modern (non-legacy) API.

websockets 14.0 made ``websockets.connect`` the new asyncio implementation; the
legacy implementation it replaced emits deprecation warnings and will be removed.
The server imports ``websockets.connect`` directly (remote relay client, NINA
bridge, polar), so this pins that we are on the modern API and that every
declared floor guarantees it.
"""
from __future__ import annotations

import re
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]


def test_websockets_connect_is_the_modern_asyncio_impl():
    mod = websockets.connect.__module__
    assert "asyncio" in mod, f"websockets.connect is not the asyncio impl: {mod}"
    assert "legacy" not in mod, f"websockets.connect is the legacy impl: {mod}"


def test_declared_floors_guarantee_the_modern_default():
    """RE-PINNED (W2 integration, backlog WP-13/#597, owner-approved
    2026-09-30): the relay's requirements -- both ``relay/requirements.txt``
    (WP-13) and ``relay/pyproject.toml`` (WP-13's own mirror into the file
    CI's relay job and deploy-relay's test job actually install from) -- are
    now pinned to an EXACT version (``==``) rather than a floor (``>=``), so
    a routine rebuild cannot silently drift to whatever is newest on PyPI
    (#597: that drift is what made v9's tunnel drop hourly where v8 held it
    for days). An exact pin guarantees the modern API just as surely as a
    floor does -- 17.1 is no less >= 14 for being spelled ``==17.1`` -- so
    the regex now reads either spelling.

    RED under mutant "the regex forgets ==" (reverted to ``r"websockets>=
    (\\d+)"`` alone, this test's pre-W2-integration shape), observed:

        AssertionError: no websockets floor declared in relay/pyproject.toml
        assert None
    """
    for rel in ("server/pyproject.toml", "relay/pyproject.toml",
                "relay/requirements.txt"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        m = re.search(r"websockets(?:>=|==)(\d+)(?:\.\d+)*", text)
        assert m, f"no websockets floor declared in {rel}"
        assert int(m.group(1)) >= 14, f"{rel} allows a pre-14 (legacy) websockets"
