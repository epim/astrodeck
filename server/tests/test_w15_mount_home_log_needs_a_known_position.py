# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""POST /api/mount/home writes "mount homed" only when the position was known
(#725, #133's second finding; wave 15 integration of WP-103).

After an AM5 reset the mount reports its home position, pointing at the pole,
wherever the tube really is, and believes it is already home, so ``:hP#`` moves
nothing. WP-103 made the driver say so (``position_known`` False, and a warning
that "the tube may not have moved"), but the route's own success line followed
that warning unconditionally: a night log that said the mount was homed beside a
line saying it may not have moved. The route then read ``position_known`` BEFORE
it sent the home, and logged the line only for a known position.

#886 goes further: a home is not SENT while the position is unknown. On the
AM5 ``:hP#`` is a goto to the MODEL's home, aimed from the believed position,
so the route answers 409 ``position_unknown`` in the safe order, and the same
gate is asked again under the motion lock right before ``find_home`` (a
position can turn unknown while the home waits for the lock). A home is then
only ever sent from a known position, which is what makes the line honest.

A real ``create_app()`` and TestClient over a fake telescope, as
test_mount_routes_retire_solved_pointing.py does for the same routes.

Named mutants, each run from a byte backup of ``api/app.py`` and restored
byte-identically (sha256 compared, the mutant text grepped absent). The failing
assertion is quoted:

* H1 "home ungated" -- the ``_refuse_if_position_unknown(tel)`` call in the
  home route removed:
  ``test_a_mount_whose_position_is_unknown_is_not_homed``,
  ``AssertionError: (200, '{"started":"goto"}')``.
* M0 "the gate ignores the commanded driver" -- in
  ``devices.base.position_known_for_motion`` the ``tel`` read (``if tel is
  not None and not bool(getattr(tel, "position_known", True)): return
  False``) made ``pass``; the hub here holds no telescope of its own, so only
  the driver the route commands knows: the same test,
  ``AssertionError: (200, '{"started":"goto"}')``.
* H2 "home ungated at the seam" -- ``if _abandon_if_position_unknown(t,
  "home"): return`` in ``_home`` removed:
  ``test_a_position_lost_while_the_home_waits_sends_nothing``,
  ``AssertionError: a home was sent from an unknown position: ['home']``.
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


def test_a_mount_whose_position_is_unknown_is_not_homed(
        client, monkeypatch, bus_lines):
    """#886. The route refuses with 409 ``position_unknown``, its detail in
    the safe order with no goto word, and nothing reaches the driver."""
    from astrodeck.mount_offset import (POSITION_UNKNOWN_CODE,
                                        POSITION_UNKNOWN_MOTION_DETAIL)
    tel = _Tel(position_known=False)
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)

    r = client.post("/api/mount/home")

    assert r.status_code == 409, (r.status_code, r.text)
    body = r.json()["detail"]
    assert body == {"detail": POSITION_UNKNOWN_MOTION_DETAIL,
                    "code": POSITION_UNKNOWN_CODE}, body
    time.sleep(0.2)     # a spawned home, had there been one, has run
    assert tel.did == [], f"a home was sent from an unknown position: {tel.did}"
    assert _homed(bus_lines) == [], _homed(bus_lines)


class _LostWhileQueued(_Tel):
    """A driver whose position turns unknown after the route's gate answered
    and before the spawned home runs (an AM5 link reopen that reads the home
    pole while the home waits for the motion lock)."""

    position_known = True


def test_a_position_lost_while_the_home_waits_sends_nothing(
        client, monkeypatch, bus_lines):
    """#886, the gate at the seam. The route's gate passed, the latch was set
    before the home's turn at the motion lock: nothing is sent, no "homed"
    line, and one warning in the safe order says nothing was moved."""
    hub = app_module.hub
    tel = _LostWhileQueued()
    calls = {"n": 0}

    def require(role):
        calls["n"] += 1
        if calls["n"] >= 2:          # the spawned task's own require
            tel.position_known = False
        return tel
    monkeypatch.setattr(hub, "require", require)

    r = client.post("/api/mount/home")
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        task = hub._busy.get("goto")
        if calls["n"] >= 2 and (task is None or task.done()):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("the home task never ran")

    assert tel.did == [], f"a home was sent from an unknown position: {tel.did}"
    assert _homed(bus_lines) == [], _homed(bus_lines)
    said = [m for _l, m, _s in bus_lines if m.endswith("Nothing was moved (home)")]
    assert len(said) == 1, bus_lines
    head = said[0][:137]
    assert "Trust position" in head and "pad key" in head, head
    for word in ("goto", "go to", "slew"):
        assert word not in said[0].lower(), (word, said[0])
