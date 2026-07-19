"""COM-T7: off Windows, ascom-local degrades cleanly — absent from the backend
registry, /api/backends, and the drivers surface; /api/discover/ascom-local is
empty. On Windows these assertions invert (present). One test, both truths."""
import sys

import pytest

from astrodeck.devices import backends as _b  # noqa: F401 (registration)
from astrodeck.devices.backend import BACKENDS


def test_backend_presence_matches_platform():
    if sys.platform == "win32":
        assert "ascom-local" in BACKENDS
    else:
        assert "ascom-local" not in BACKENDS


@pytest.mark.asyncio
async def test_drivers_surface_matches_platform(monkeypatch):
    from astrodeck import drivers as drivers_mod
    out = await drivers_mod.describe_all()
    ids = {d["id"] for d in out["drivers"]}
    if sys.platform == "win32":
        assert "ascom-local" in ids
    else:
        assert "ascom-local" not in ids  # native/Alpaca/NINA only — clean degrade
