"""GN-07 follow-through: a COMMANDED move retires the plate-solved centre.

``Hub.capture`` used to call ``note_pointing_moved()`` on every saved frame,
which cleared the verdict before the header for that same frame was built;
GN-07 removed that call so a solved centre survives an imaging run. That makes
the motion routes the only thing standing between a stale solve and a header
that claims it: a park, a home or an unpark must clear the record, or the next
sub after a slew would carry the previous target's coordinates as "solved".
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)

from test_api_mount import FakeAuthProvider  # rootdir-relative


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _operator():
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    yield
    reset_active_provider()


class _Tel:
    connected = True
    can_find_home = True

    def __init__(self):
        self.did: list[str] = []

    async def park(self) -> None:
        self.did.append("park")

    async def find_home(self) -> None:
        self.did.append("home")

    async def unpark(self) -> None:
        self.did.append("unpark")

    async def is_parked(self) -> bool:
        return False

    async def get_tracking(self) -> bool:
        return True

    async def set_tracking(self, on: bool) -> None:
        pass


@pytest.mark.parametrize("route,verb", [
    ("/api/mount/park", "park"),
    ("/api/mount/home", "home"),
    ("/api/mount/unpark", "unpark"),
])
def test_a_commanded_move_clears_the_solved_pointing(client, monkeypatch,
                                                     route, verb):
    tel = _Tel()
    hub = app_module.hub
    monkeypatch.setattr(hub, "require", lambda role: tel)
    hub._pointing_verified = True
    hub._solved_pointing = (1.0, 2.0, time.time())

    r = client.post(route)
    assert r.status_code == 200, r.text
    for _ in range(50):
        if tel.did:
            break
        time.sleep(0.02)
    assert tel.did == [verb], f"device call never happened: {tel.did}"

    assert hub._solved_pointing is None, route
    assert hub._pointing_verified is False, route
