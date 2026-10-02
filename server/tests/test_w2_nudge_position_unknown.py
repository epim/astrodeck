# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-20 / #144: a nudge refuses rather than guess from an unknown position.

A nudge computes its destination by reading the mount's CURRENT position and
adding an offset. #144 was filed because a reset AM5 answers that read with
its home position -- pointing at the pole -- without having re-synced, so the
"small correction" the operator asked for became a goto to an unrecoverable
guess. ``POST /api/mount/move`` (the rate pad) never reads position at all,
so it stays the honest control; the 409 below says to use it.

This drives ``api/app.py``'s REAL route via ``create_app()`` (the pattern
``test_mount_nudge.py::test_the_shipped_route_carries_the_two_fields_too``
already uses for the same reason: a route mirrored into a test file can drift
from the shipped one and still pass).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.mount_offset import POSITION_UNKNOWN_CODE


class _Tel:
    """A mount that may or may not be able to vouch for its own position.

    ``position_known`` absent entirely (the default-arg path below) covers
    every driver and test double written before #144: the route reads it
    with ``getattr(tel, "position_known", True)``, so an object that never
    heard of the attribute must nudge exactly as it always did.
    """

    connected = True
    backend = ""

    def __init__(self, ra: float = 6.0, dec: float = 40.0, **kw):
        self.ra, self.dec = ra, dec
        for k, v in kw.items():
            setattr(self, k, v)

    async def get_position(self) -> tuple[float, float]:
        return self.ra, self.dec


@pytest.fixture(autouse=True)
def _clean_lanes():
    yield
    for name, task in list(app_module.hub._busy.items()):
        if task is not None and not task.done():
            task.cancel()
        app_module.hub._busy.pop(name, None)


def _client(tel, monkeypatch) -> TestClient:
    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setattr(app_module.hub, "_check_solar",
                        lambda ra, dec, **kw: None, raising=False)
    return TestClient(app)


def test_a_nudge_with_an_unknown_position_is_refused_not_clamped(monkeypatch):
    """NAMED MUTANT (WP20-M1): replacing the route's
    ``if not getattr(tel, "position_known", True):`` guard with ``if False:``
    (the check compiles out, never refuses) turns this red:

        AssertionError: {"started":"goto","from":{"ra_hours":6.0,
        "dec_deg":89.9},"to":{"ra_hours":6.0,"dec_deg":90.0},"arcmin":10.0,
        "clamped":true,"achieved_arcmin":6.0}
        assert 200 == 409

    Run from a byte backup inside the WP-20 worktree, restored byte-identical
    (sha256 compared) afterwards; `test_an_unknown_position_refuses_every_axis_
    not_only_dec` goes red the same way under the same mutant.
    """
    tel = _Tel(ra=6.0, dec=89.9, position_known=False)
    with _client(tel, monkeypatch) as c:
        r = c.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 10.0})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == POSITION_UNKNOWN_CODE
    # The 409 has to say what to do instead, not just that it refused.
    assert "rate-move" in detail["detail"]


def test_an_unknown_position_refuses_every_axis_not_only_dec(monkeypatch):
    """The refusal is about trusting the READ, not about which direction the
    operator asked for -- an RA nudge from an unknown position is exactly as
    much a guess as a Dec one."""
    tel = _Tel(ra=6.0, dec=40.0, position_known=False)
    with _client(tel, monkeypatch) as c:
        r = c.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 10.0})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == POSITION_UNKNOWN_CODE


def test_a_known_position_nudges_exactly_as_before(monkeypatch):
    tel = _Tel(ra=6.0, dec=40.0, position_known=True)
    with _client(tel, monkeypatch) as c:
        r = c.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 10.0})
    assert r.status_code == 200, r.text


def test_a_driver_that_predates_the_flag_is_unaffected(monkeypatch):
    """No ``position_known`` attribute at all -- the shape of every mount
    backend and test double written before #144. ``getattr``'s default must
    keep these nudging, not start refusing a mount that never claimed to be
    untrustworthy."""
    tel = _Tel(ra=6.0, dec=40.0)
    assert not hasattr(tel, "position_known")
    with _client(tel, monkeypatch) as c:
        r = c.post("/api/mount/nudge", json={"axis": "ra", "arcmin": 10.0})
    assert r.status_code == 200, r.text
