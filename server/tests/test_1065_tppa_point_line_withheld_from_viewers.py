# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1065: the native TPPA measurement lines carry values computed from the site.

`polar.native._log_measurement` logs "native TPPA point N/3: solved RA ... HA
...". The hour angle is the local sidereal time minus the RA, so RA, HA and
the line's own timestamp give the site longitude. The line was published
without ``site_derived``, so `GET /api/logs` (view.status, which a viewer
holds) served it from the unflagged ring. Two sibling lines had the same
shape: the "arc runs low" warning (altitudes of a projected pointing at a
known time, a latitude oracle) and the "starting N min from the meridian"
warning (a hour angle magnitude).

WHAT IS GRADED. The real logging functions run on the process bus against a
hub stub whose site is an invented one. `GET /api/logs` is then read through
the real app as a viewer (no view.site_derived) and as an operator, and the
WS seam (`_redact_ws_event`) is asked about the same published event. A viewer
is served no such line; an operator is served it; an unflagged line published
beside it reaches the viewer, so the route is not simply serving nothing.

MUTANT "no flag" (``site_derived=True`` removed from the ``bus.log`` in
`_log_measurement`): RED, observed -
    AssertionError: the TPPA point line was not flagged site_derived:
    {'level': 'info', 'message': 'native TPPA point 1/3: solved RA 4.2500h ...
MUTANT "low-arc unflagged" (the same removal in `_refuse_low_arc`) is graded
by the second case.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from _simhub import sim_hub  # noqa: F401

import astrodeck.api.app as app_module
from astrodeck import events
from astrodeck.api.redact import _redact_ws_event
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.events import is_site_derived
from astrodeck.polar import native

#: An invented site. Never a real one.
SITE = {"latitude": 12.5, "longitude": -33.25, "is_default": False}

CONTROL = "1065 control line, not site derived"


class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal) -> None:
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _as(role: str) -> None:
    set_active_provider(_FixedPrincipal(principal_for_role(role)))


@pytest.fixture(autouse=True)
def _restore_provider():
    yield
    reset_active_provider()


@pytest.fixture()
def app(sim_hub, monkeypatch):
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    return app_module.create_app()


class _Tel:
    async def get_position(self):
        return 3.0, 45.0


def _hub():
    return SimpleNamespace(site=dict(SITE))


def _messages(c: TestClient, role: str) -> list[str]:
    _as(role)
    r = c.get("/api/logs")
    assert r.status_code == 200, r.text
    return [(row.get("data") or {}).get("message", "") for row in r.json()]


async def _point_line() -> None:
    result = SimpleNamespace(ra_hours=4.25, dec_deg=40.0, rotation_deg=10.0)
    frame = SimpleNamespace(timestamp=1_800_000_000.0)
    await native._log_measurement(_hub(), _Tel(), 0, result, frame)


def _only(msgs: list[str], needle: str) -> list[str]:
    return [m for m in msgs if needle in m]


async def test_a_tppa_point_line_is_flagged_and_withheld_from_a_viewer(
        sim_hub, app):
    sub = events.bus.subscribe()
    try:
        await _point_line()
        events.bus.log("info", CONTROL, "polar")
        published = []
        while not sub.empty():
            published.append(sub.get_nowait())
    finally:
        events.bus.unsubscribe(sub)
    point = [e for e in published if e.type == "log"
             and "TPPA point" in e.data.get("message", "")]
    assert len(point) == 1, [e.data for e in published]
    assert is_site_derived(point[0].to_json()), (
        f"the TPPA point line was not flagged site_derived: {point[0].data}")
    # The WS lane drops it for a viewer and serves it to an operator.
    assert _redact_ws_event(point[0].to_json(),
                            principal_for_role("viewer")) is None
    assert _redact_ws_event(point[0].to_json(),
                            principal_for_role("operator")) == \
        point[0].to_json()

    c = TestClient(app)
    viewer = _messages(c, "viewer")
    assert _only(viewer, "TPPA point") == [], (
        f"a viewer's /api/logs carries {_only(viewer, 'TPPA point')}")
    assert CONTROL in viewer, "control: the viewer lost an unflagged line"
    operator = _messages(c, "operator")
    assert len(_only(operator, "TPPA point")) == 1, operator
    assert CONTROL in operator


async def test_the_arc_runs_low_warning_is_withheld_from_a_viewer(
        sim_hub, app, monkeypatch):
    """The altitudes in the warning are a projected pointing's altitude at a
    known time, so they are the site. ``altaz`` is pinned to a value in the
    warning band so the case does not depend on the invented site's sky."""
    import astrodeck.catalog.coords as coords
    monkeypatch.setattr(coords, "altaz", lambda *a, **k: (15.0, 100.0))
    native._refuse_low_arc(_hub(), SimpleNamespace(ra_hours=4.25, dec_deg=40.0),
                           0.5)
    c = TestClient(app)
    assert _only(_messages(c, "viewer"), "the arc runs low") == []
    assert len(_only(_messages(c, "operator"), "the arc runs low")) == 1
