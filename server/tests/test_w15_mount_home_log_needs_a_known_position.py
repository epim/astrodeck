# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""POST /api/mount/home writes "mount homed" only when the position was known
(#725, #133's second finding; wave 15 integration of WP-103).

After an AM5 reset the mount reports its home position, pointing at the pole,
wherever the tube really is, and believes it is already home, so ``:hP#`` moves
nothing. WP-103 made the driver say so (``position_known`` False, and a warning
that "the tube may not have moved"), but the route's own success line followed
that warning unconditionally: a night log that said the mount was homed beside a
line saying it may not have moved. The route now reads ``position_known`` BEFORE
it sends the home, and logs the line only for a known position. With an unknown
one it logs nothing; the driver's warning is the whole truth.

A real ``create_app()`` and TestClient over a fake telescope, as
test_mount_routes_retire_solved_pointing.py does for the same routes.

Named mutants, each run from a byte backup of ``api/app.py`` and restored
byte-identically (sha256 compared, the mutant text grepped absent). The failing
assertion is quoted:

* "logged regardless" -- ``if known:`` made ``if True:``:
  ``test_a_mount_whose_position_is_unknown_logs_no_homed_line``,
  ``AssertionError: a mount whose position was unknown was reported as homed:
  [('info', 'mount homed', 'mount')]``.
* "known read after the home" -- ``known = getattr(t, "position_known", True)``
  moved below ``await t.find_home()``:
  ``test_a_driver_that_clears_the_flag_as_it_homes_still_logs_nothing``,
  ``AssertionError: a mount whose position was unknown when the home was sent
  was reported as homed: [('info', 'mount homed', 'mount')]``.
* "never logs" -- the ``bus.log("info", "mount homed", "mount")`` line deleted:
  ``test_a_mount_whose_position_is_known_is_logged_as_homed``,
  ``AssertionError: a known-position home wrote no 'mount homed' line: []``.
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

    def __init__(self, **flags):
        self.did: list[str] = []
        for k, v in flags.items():
            setattr(self, k, v)

    async def find_home(self) -> None:
        self.did.append("home")


class _ClearsAsItHomes(_Tel):
    """A driver that flips ``position_known`` to True once its home returns."""

    position_known = False

    async def find_home(self) -> None:
        await super().find_home()
        self.position_known = True


def _home(client, monkeypatch, tel) -> None:
    """POST the route, then wait (against a wall-clock deadline, #669) for the
    spawned task to finish, so the line that follows the device call has either
    been written or never will be."""
    hub = app_module.hub
    monkeypatch.setattr(hub, "require", lambda role: tel)
    r = client.post("/api/mount/home")
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        task = hub._busy.get("goto")
        if tel.did and (task is None or task.done()):
            return
        time.sleep(0.01)
    raise AssertionError(f"the home never finished: {tel.did}")


def _homed(bus_lines) -> list[tuple[str, str, str]]:
    return [ln for ln in bus_lines if ln[1] == "mount homed"]


def test_a_mount_whose_position_is_known_is_logged_as_homed(
        client, monkeypatch, bus_lines):
    """CONTROL. A driver that says its position is known, and one that says
    nothing at all (every backend but the AM5 has no such flag), keep the line
    the night log has always had."""
    for tel in (_Tel(position_known=True), _Tel()):
        bus_lines.clear()
        _home(client, monkeypatch, tel)
        assert len(_homed(bus_lines)) == 1, (
            f"a known-position home wrote no 'mount homed' line: {bus_lines}")


def test_a_mount_whose_position_is_unknown_logs_no_homed_line(
        client, monkeypatch, bus_lines):
    tel = _Tel(position_known=False)

    _home(client, monkeypatch, tel)

    assert tel.did == ["home"], "premise: the home was sent all the same"
    assert _homed(bus_lines) == [], (
        f"a mount whose position was unknown was reported as homed: "
        f"{_homed(bus_lines)}")


def test_a_driver_that_clears_the_flag_as_it_homes_still_logs_nothing(
        client, monkeypatch, bus_lines):
    """The answer is the one that held when the home was SENT. A driver that
    marks its position known when the home returns has not shown that the tube
    moved, so reading the flag afterwards would turn "unknown" into a claim."""
    tel = _ClearsAsItHomes()

    _home(client, monkeypatch, tel)

    assert tel.position_known is True, "premise: the driver cleared the flag"
    assert _homed(bus_lines) == [], (
        f"a mount whose position was unknown when the home was sent was "
        f"reported as homed: {_homed(bus_lines)}")
