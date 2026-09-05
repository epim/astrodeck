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
    for rel in ("server/pyproject.toml", "relay/pyproject.toml",
                "relay/requirements.txt"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        m = re.search(r"websockets>=(\d+)(?:\.\d+)*", text)
        assert m, f"no websockets floor declared in {rel}"
        assert int(m.group(1)) >= 14, f"{rel} allows a pre-14 (legacy) websockets"
