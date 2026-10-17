# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1035, #1036, #1037: the PULL lanes (and the streams other tasks publish)
must not give a viewer the meridian flip's moment, or a twilight time.

#1020 flagged what the flip's own task publishes, which closes the WS push of
those events. The same moment, and the dusk and dawn windows' twilight times,
were still served by three other kinds of lane to a principal without
``view.site_derived``:

* #1035: ``GET /api/sequence/state`` and the monitor snapshot serve
  ``engine.state``, whose ``detail`` stays ``"meridian flip"`` for the flip.
* #1036: the waiting ``schedule`` block (``eta_s``, ``start_ts``,
  ``stop_ts``) on all three sequence seams.
* #1037: ``meridian.pier_side`` (changes at the flip), the status frame's
  ``mount.slewing`` and ``guider`` (the status poller's own task), and the
  guide stream (the guide loop's own task).

WHAT IS GRADED. The real engine takes a stubbed flip and the lanes are read
from INSIDE it (the re-centre stub), as a viewer and as an operator; the
schedule is published by the real `_set_state`; the status frame is the sim
hub's real `poll_status`. The seams are the real ones: the two routes through
the app, and `_redact_ws_event` for the WS lanes.

MUTANTS (each applied to a byte backup of the production file, run, restored
by byte copy). The observed failure is quoted in the test it grades.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _simhub import sim_hub  # noqa: F401

import astrodeck.api.app as app_module
from astrodeck import events
from astrodeck.api.redact import _redact_site_for, _redact_ws_event
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.devices.base import PierSide
from astrodeck.sequence import SequenceEngine, SequencePlan, schedule
from astrodeck.sequence.models import ExposureStep, Target

VIEWER = principal_for_role("viewer")
OPERATOR = principal_for_role("operator")

#: A dusk window's twilight times, as `_run_scheduled` publishes them.
SCHEDULE = {"state": "waiting", "reason": "waiting for the observing window",
            "eta_s": 1234.0, "start_ts": 1.7e9, "stop_ts": 1.7e9 + 30000.0}
TIMED = ("eta_s", "start_ts", "stop_ts")


class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal) -> None:
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _as(role: str) -> None:
    set_active_provider(_FixedPrincipal(principal_for_role(role)))


@pytest.fixture(autouse=True)
def _restore():
    yield
    reset_active_provider()
    events.end_flip_hold()


@pytest.fixture()
def app(sim_hub, monkeypatch):
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    monkeypatch.setattr(app_module, "hub", sim_hub)
    return app_module.create_app()


def _flip_engine(sim_hub, monkeypatch, on_recentre):
    """An engine whose flip is due now, on the real `_maybe_meridian_flip`;
    ``on_recentre`` is awaited from INSIDE the flip, at the re-centre."""
    tel = sim_hub.devices["telescope"]
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) - 5.0 / 3600.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False,
                          meridian_flip_lead_min=10.0)
    e._cfg = None
    e._flip_armed = True
    e._flip_no_op.add(t.id)
    side = {"v": "west"}

    async def noop_gate(context="", target=None):
        return None

    async def pier():
        return {"east": PierSide.EAST, "west": PierSide.WEST}[side["v"]]

    async def no_countdown():
        return None

    async def goto(ra_h, dec_d, **kw):
        side["v"] = "east"
        await on_recentre()
        return {"centered": True, "error_arcmin": 0.2}

    async def hold(target, lead_s=0.0):
        return None

    async def autofocus(label, **kw):
        return True

    e._safety_gate = noop_gate
    monkeypatch.setattr(tel, "pier_side", pier)
    monkeypatch.setattr(tel, "time_to_meridian_flip", no_countdown)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    e._wait_for_flip_point = hold
    e._autofocus = autofocus
    return e, t


def _served(e, principal) -> dict:
    """What GET /api/sequence/state and the snapshot's ``sequence`` serve."""
    return app_module._redact_sequence_for(app_module._sequence_envelope(e),
                                           principal)


async def test_the_flip_span_holds_the_pull_lanes_for_a_viewer(
        sim_hub, app, monkeypatch):
    """#1035 + #1037, through the real engine, read from inside the flip.

    A viewer's sequence state never reads ``"meridian flip"`` (its detail is
    HELD at what the run showed as the span opened, not removed: removal would
    date the start), its status frame holds the last pre-span ``slewing`` and
    ``guider``, and the guide stream is silent; an operator is served the
    flip as it is. After the flip the hold is closed and the marker is gone
    from the state.

    MUTANT "the span's marker not set" (the ``self.state["_flip_site_derived"]
    = ...`` block removed from `SequenceEngine._maybe_meridian_flip`): RED,
    observed -
        AssertionError: a viewer read the flip off GET /api/sequence/state:
        'meridian flip'
    MUTANT "the hold not opened" (``begin_flip_hold()`` removed): RED -
        AssertionError: a viewer was served the guide stream during the flip
    """
    seen: dict = {}
    tel = sim_hub.devices["telescope"]

    async def mid_flip():
        slewing = {"v": True}

        async def is_slewing():
            return slewing["v"]

        monkeypatch.setattr(tel, "is_slewing", is_slewing)
        status = await sim_hub.poll_status()
        for who, p in (("viewer", VIEWER), ("operator", OPERATOR)):
            seen[who] = {
                "state": _served(e, p),
                "status": _redact_site_for(dict(status), p),
                "guide": _redact_ws_event(
                    {"type": "guide", "data": {"guiding": False,
                                               "phase": "stopped"}}, p),
            }

    e, t = _flip_engine(sim_hub, monkeypatch, mid_flip)
    # What a viewer was shown as the span opened (the run was imaging).
    e._set_state(state="running", detail="imaging 3/10")
    status = await sim_hub.poll_status()   # the pre-span frame, slewing False
    assert status["mount"]["slewing"] is False
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)

    v, o = seen["viewer"], seen["operator"]
    assert v["state"]["detail"] != "meridian flip", (
        f"a viewer read the flip off GET /api/sequence/state: "
        f"{v['state']['detail']!r}")
    assert v["state"]["detail"] == "imaging 3/10", (
        f"the viewer's detail must be HELD, not removed: {v['state']}")
    assert o["state"]["detail"] == "meridian flip", o["state"]["detail"]
    for who in ("viewer", "operator"):
        assert "_flip_site_derived" not in seen[who]["state"], who
    assert v["status"]["mount"]["slewing"] is False, (
        "a viewer read the flip's slew off the status frame")
    assert o["status"]["mount"]["slewing"] is True
    assert v["status"]["meridian"]["pier_side"] == "unknown"
    assert o["status"]["meridian"]["pier_side"] != "unknown"
    assert v["guide"] is None, "a viewer was served the guide stream during the flip"
    assert o["guide"] is not None
    # After the span: no hold, no marker, the guide stream is back.
    assert events.flip_hold_open() is False
    assert "_flip_site_derived" not in e.state
    after = _redact_ws_event({"type": "guide", "data": {"guiding": True}},
                             VIEWER)
    assert after is not None


async def test_the_flip_marker_is_cleared_when_the_flip_raises(
        sim_hub, monkeypatch):
    """The span is closed however the flip ends: a failed re-centre must not
    leave a viewer's detail frozen and the guide stream muted for the night."""
    async def boom():
        raise RuntimeError("solve failed")

    e, t = _flip_engine(sim_hub, monkeypatch, boom)
    with pytest.raises(Exception):
        await e._maybe_meridian_flip(t, next_exposure_s=60.0)
    assert events.flip_hold_open() is False
    assert "_flip_site_derived" not in e.state


async def test_a_flip_not_yet_due_opens_no_span(sim_hub, monkeypatch):
    """Control: a gate that returns early (the flip point is far off) opens
    no hold, so the guide stream and the detail are served as they were."""
    e, t = _flip_engine(sim_hub, monkeypatch, lambda: None)
    t.ra_hours = (schedule.lst_hours(sim_hub.site["longitude"]) + 3.0) % 24.0
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)
    assert events.flip_hold_open() is False
    assert "_flip_site_derived" not in e.state


# ------------------------------------------------------------------ #1036

def _publish_wait(e) -> dict:
    sub = events.bus.subscribe()
    try:
        e._set_state(state="waiting", detail="waiting for the window",
                     schedule=dict(SCHEDULE))
        got = []
        while not sub.empty():
            got.append(sub.get_nowait())
    finally:
        events.bus.unsubscribe(sub)
    (ev,) = [g for g in got if g.type == "sequence"]
    return ev.to_json()


def test_the_schedule_block_is_untimed_on_every_seam_for_a_viewer(
        sim_hub, app, monkeypatch):
    """#1036: a dusk-window wait published by the real `_set_state`. On the
    GET route, the monitor snapshot and the WS event a viewer is served the
    state and the reason and none of ``eta_s``, ``start_ts``, ``stop_ts``; an
    operator is served all three on each.

    MUTANT "schedule not withheld" (``out = _withhold_schedule_timing(out)``
    removed from `_redact_sequence_for`): RED, observed -
        AssertionError: GET /api/sequence/state served 'eta_s' to a viewer
    """
    e = SequenceEngine(sim_hub)
    ev = _publish_wait(e)
    monkeypatch.setattr(app_module, "engine", e)
    client = TestClient(app)
    for role in ("viewer", "operator"):
        _as(role)
        want = role == "operator"
        lanes = {
            "GET /api/sequence/state":
                client.get("/api/sequence/state").json()["schedule"],
            "GET /api/monitor/snapshot":
                client.get("/api/monitor/snapshot").json()["sequence"]["schedule"],
            "WS sequence event":
                _redact_ws_event(ev, principal_for_role(role))["data"]["schedule"],
        }
        for lane, sched in lanes.items():
            assert sched["state"] == "waiting" and sched["reason"], (lane, sched)
            for k in TIMED:
                assert (k in sched) is want, (
                    f"{lane} served {k!r} to a {role}" if not want else
                    f"{lane} withheld {k!r} from an operator")
    # The event itself is not mutated for the next subscriber.
    assert all(k in ev["data"]["schedule"] for k in TIMED)


# ------------------------------------------------------------------ #1037

def test_pier_side_is_withheld_from_a_viewer_on_status_and_the_snapshot(
        sim_hub, app, monkeypatch):
    """#1037 ruling: the pier side changes at the flip, so a viewer reads none
    from GET /api/status, the monitor snapshot's status, or the WS status
    event; and the last solve's ``sky_angle.pier_side`` with it. An operator
    is served the real side.

    MUTANT "pier side kept" (the ``pier_side`` block removed from
    `_strip_meridian_derived`): RED, observed -
        AssertionError: GET /api/status served the pier side to a viewer
    """
    sim_hub.last_sky_angle = {"pa_deg": 10.0, "pier_side": "west",
                              "solved_at": 1.0, "source": "solve"}
    client = TestClient(app)
    _as("viewer")
    st = client.get("/api/status").json()
    snap = client.get("/api/monitor/snapshot").json()["status"]
    for lane, s in (("GET /api/status", st), ("monitor snapshot", snap)):
        assert s["meridian"]["pier_side"] == "unknown", (
            f"{lane} served the pier side to a viewer: {s['meridian']}")
        assert s["sky_angle"]["pier_side"] is None, lane
    ws = _redact_ws_event({"type": "status", "data": dict(
        st, meridian={"pier_side": "west", "pier_side_source": "mount",
                      "pier_side_age_s": 0.0, "status": "flip_disabled"})},
        VIEWER)
    assert ws["data"]["meridian"]["pier_side"] == "unknown"
    _as("operator")
    op = client.get("/api/status").json()
    assert op["meridian"]["pier_side"] in ("east", "west")
    assert op["sky_angle"]["pier_side"] == "west"


async def test_the_guider_node_is_held_for_a_viewer_during_the_flip(
        sim_hub, monkeypatch):
    """#1037: the guider going to "stopped" is the flip signal. A viewer's
    ``status.guider`` read from inside the re-centre is the pre-span node;
    an operator is served the live one.

    MUTANT "the guider node not held" (an early ``return`` at the top of
    `_strip_guider_derived`): RED, observed -
        AssertionError: a viewer read the guider stop off the status frame
    """
    class _Stats:
        def __init__(self, guiding):
            self.guiding = guiding
            self.state = "guiding" if guiding else "stopped"

    class _Guider:
        name = "native"
        connected = True
        live = True

        def stats(self):
            return _Stats(self.live)

    g = _Guider()
    monkeypatch.setattr(sim_hub, "guider", g, raising=False)
    seen: dict = {}

    async def mid_flip():
        g.live = False          # the flip stopped the guider
        status = await sim_hub.poll_status()
        for who, p in (("viewer", VIEWER), ("operator", OPERATOR)):
            seen[who] = _redact_site_for(dict(status), p)["guider"]

    e, t = _flip_engine(sim_hub, monkeypatch, mid_flip)
    pre = await sim_hub.poll_status()       # imaging: the guider is guiding
    assert pre["guider"]["guiding"] is True
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)

    assert seen["viewer"]["guiding"] is True, (
        f"a viewer read the guider stop off the status frame: {seen['viewer']}")
    assert seen["viewer"]["name"] == "native"
    assert seen["operator"]["guiding"] is False
