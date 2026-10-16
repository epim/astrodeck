# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1020: the meridian flip's EVENTS are site-derived, not only its log lines.

`Hub.meridian_flip` is called when a flip is due, which at lead 0 is the
target's computed crossing, so the moment it publishes anything is a function
of the site's longitude (#166, #302). Its two log lines were flagged
``site_derived`` (tests/test_w2_hub_flip_line_flagged.py); the ``mount``
event it published first, ``action="meridian_flip"``, was not, and neither
was the engine's own ``sequence`` publish (``detail="meridian flip"``) made a
moment before the hub call. Both ride the WS lanes to every principal with
``view.status``, and ``_redact_ws_event`` only drops an event that carries the
flag. A viewer therefore read the crossing off the clock from the one
channel the flag had not been applied to: the same shape as #166, one lane
over.

WHAT IS GRADED. The real publishers (`Hub.meridian_flip` on the sim hub and
`SequenceEngine._maybe_meridian_flip`) put the real events on the real bus,
and each is read as a viewer and as an operator through the seam both WS
lanes use (`_redact_ws_event`), through the LAN ``/ws`` lane and through the
relay-tunneled lane. A viewer or a syncer (view.status, not
view.site_derived) is served none of them; an operator and an admin are
served all of them, flag and all. Controls ride in each case: an unflagged
event published beside the flip reaches the viewer, and the flag on the
engine's publish does not stay in the engine's state to ride every later one.

MUTANTS. Each was applied to a byte-backed-up copy of the production file in
this worktree and the named test run, then the file was restored by byte copy.
The observed failure is quoted in the test it grades.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from _simhub import sim_hub  # noqa: F401

import astrodeck.api.app as app_module
from astrodeck import events
from astrodeck.api.redact import _redact_ws_event
from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS,
                            principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.devices.base import PierSide
from astrodeck.events import is_site_derived
from astrodeck.remote.protocol import FrameType
from astrodeck.sequence import SequenceEngine, SequencePlan, schedule
from astrodeck.sequence.models import ExposureStep, Target
from test_remote_relay import (FakeChannel, _make_relay_client,
                               _wait_for_frame, _ws_data_payloads)

NON_HOLDERS = ("viewer", "syncer")
HOLDERS = ("operator", "admin")

#: The last line of a burst, so a WS reader knows it has seen all of it.
MARKER = "1020 end of burst"


# ---------------------------------------------------------------- harness

class _FixedPrincipal:
    """Every request resolves to one identity (the #19 scanner's pattern).
    ``name`` is not "none", so the relay lane's remote hard-deny treats it as
    a real provider."""

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
    """The real app over the process bus, on the sim hub's isolated config
    store and capture root."""
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    return app_module.create_app()


def _stub_the_flip(hub, monkeypatch) -> None:
    """The flip's two device-facing calls, as the existing flip tests stub
    them: the pier side reads west then east, and the re-centre succeeds.
    Everything else in `Hub.meridian_flip` is the real code."""
    sides = iter(["west", "east"])

    async def side_now():
        return next(sides)

    async def goto(ra_h, dec_d, **kw):
        return {"centered": True, "error_arcmin": 0.2}

    monkeypatch.setattr(hub, "pier_side_now", side_now)
    monkeypatch.setattr(hub, "goto_and_center", goto)


def _drain(sub) -> list:
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


def _mount_flips(evs) -> list:
    return [e for e in evs if e.type == "mount"
            and e.data.get("action") == "meridian_flip"]


def test_the_roles_are_the_ones_this_file_grades():
    """Premise. If a viewer or syncer came to hold view.site_derived, every
    "withheld from a non-holder" case below would be asking a principal
    entitled to the event, and if an operator lost it every holder case
    would be asking one that is not."""
    for role in NON_HOLDERS:
        p = principal_for_role(role)
        assert p.has(CAP_VIEW_STATUS), role
        assert not p.has(CAP_VIEW_SITE_DERIVED), role
    for role in HOLDERS:
        assert principal_for_role(role).has(CAP_VIEW_SITE_DERIVED), role


# --------------------------------------- (1) the hub's event, at the seam

async def test_the_hubs_flip_event_is_flagged_and_dropped_for_a_non_holder(
        sim_hub, monkeypatch):
    """`Hub.meridian_flip`'s ``mount`` event carries ``site_derived`` and the
    seam both WS lanes use drops it for a viewer and a syncer, and serves it
    verbatim to an operator and an admin. The other mount event the same
    flip's neighbours publish (no flag) reaches the viewer, so the seam is
    dropping the flip event and not every ``mount`` event.

    MUTANT "the hub event unflagged" (``site_derived=True`` dropped from the
    ``bus.publish("mount", action="meridian_flip")`` in `Hub.meridian_flip`):
    RED, observed -
        AssertionError: the flip's mount event was not flagged site_derived:
        {'action': 'meridian_flip'}
    """
    _stub_the_flip(sim_hub, monkeypatch)
    sub = events.bus.subscribe()
    try:
        await sim_hub.meridian_flip(1.0, 20.0)
        events.bus.publish("mount", action="centered", error_arcmin=0.2)
        got = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    flips = _mount_flips(got)
    assert len(flips) == 1, [(e.type, e.data) for e in got if e.type == "mount"]
    assert is_site_derived(flips[0].to_json()), (
        f"the flip's mount event was not flagged site_derived: "
        f"{flips[0].data}")
    plain = [e for e in got if e.type == "mount"
             and e.data.get("action") == "centered"]
    assert len(plain) == 1
    for role in NON_HOLDERS:
        p = principal_for_role(role)
        assert _redact_ws_event(flips[0].to_json(), p) is None, role
        assert _redact_ws_event(plain[0].to_json(), p) == plain[0].to_json(), (
            f"control: {role} lost an unflagged mount event")
    for role in HOLDERS:
        assert _redact_ws_event(flips[0].to_json(),
                                principal_for_role(role)) == \
            flips[0].to_json(), role


# ------------------------------------------- (2) the two WS lanes, end to end

def _ws_collect(ws, marker: str = MARKER) -> list[dict]:
    """Every ``mount`` and ``sequence`` frame up to the marker line.
    Unrelated frames may interleave and are skipped."""
    got = []
    while True:
        ev = ws.receive_json()
        if ev.get("type") in ("mount", "sequence"):
            got.append(ev)
        if (ev.get("type") == "log"
                and (ev.get("data") or {}).get("message") == marker):
            return got


async def test_the_lan_ws_withholds_the_flip_event_from_a_viewer(
        sim_hub, app, monkeypatch):
    """The on-LAN ``/ws`` lane, a viewer and an operator connected together
    across one flip: the operator is sent the ``mount`` ``meridian_flip``
    event, flag and all, and the viewer is sent every other frame of the burst
    and not that one.

    MUTANT "the hub event unflagged": RED, observed -
        AssertionError: a viewer was sent the flip event: [{'type': 'mount',
        'data': {'action': 'meridian_flip'}, 'ts': ...}]
    """
    _stub_the_flip(sim_hub, monkeypatch)
    c = TestClient(app)
    _as("viewer")
    with c.websocket_connect("/ws") as ws_v:
        assert ws_v.receive_json()["type"] == "hello"
        _as("operator")
        with c.websocket_connect("/ws") as ws_o:
            assert ws_o.receive_json()["type"] == "hello"
            await sim_hub.meridian_flip(1.0, 20.0)
            events.bus.publish("mount", action="centered", error_arcmin=0.2)
            events.bus.log("info", MARKER, "sequence")
            viewer = _ws_collect(ws_v)
            operator = _ws_collect(ws_o)
    v_flip = [e for e in viewer if e["data"].get("action") == "meridian_flip"]
    o_flip = [e for e in operator if e["data"].get("action") == "meridian_flip"]
    assert v_flip == [], f"a viewer was sent the flip event: {v_flip}"
    assert len(o_flip) == 1 and o_flip[0]["data"]["site_derived"] is True, (
        operator)
    # Control: the unflagged event of the same burst reaches both.
    for who, frames in (("viewer", viewer), ("operator", operator)):
        assert [e for e in frames
                if e["data"].get("action") == "centered"], (who, frames)


async def _relay_mount_frames(app, hub, monkeypatch, role: str,
                              marker: str) -> list[dict]:
    """One relay-tunneled ``/ws`` (test_remote_relay's harness), opened as
    ``role``, across one flip: the ``mount`` payloads it sent."""
    _as(role)
    _stub_the_flip(hub, monkeypatch)
    channel = FakeChannel()
    client = _make_relay_client(app, channel)
    channel.push_frame(FrameType.WS_OPEN, 7,
                       {"path": "/ws", "query": "", "ws_id": "wsA"})
    task = asyncio.create_task(client._serve_once(client._config()))
    try:
        await _wait_for_frame(channel,
                              lambda f: f.type == FrameType.WS_DATA)  # hello
        await hub.meridian_flip(1.0, 20.0)
        events.bus.publish("mount", action="centered", error_arcmin=0.2)
        events.bus.log("info", marker, "sequence")
        await _wait_for_frame(channel, lambda f: (
            f.type == FrameType.WS_DATA and marker.encode() in f.payload))
    finally:
        channel.finish()
        await asyncio.wait_for(task, timeout=5.0)
    return [p["data"] for p in await _ws_data_payloads(channel)
            if p.get("type") == "mount"]


async def test_the_relay_lane_withholds_the_flip_event_from_a_viewer(
        sim_hub, app, monkeypatch):
    """The relay-tunneled ``/ws`` lane sends what the same seam returns, so a
    viewer behind the relay is not sent the flip event either, and an operator
    is.

    MUTANT "the hub event unflagged": RED, observed -
        AssertionError: a viewer behind the relay was sent the flip event:
        [{'action': 'meridian_flip'}, {'action': 'centered',
        'error_arcmin': 0.2}]
    """
    viewer = await _relay_mount_frames(app, sim_hub, monkeypatch, "viewer",
                                       "1020 relay viewer")
    operator = await _relay_mount_frames(app, sim_hub, monkeypatch, "operator",
                                         "1020 relay operator")
    assert [d for d in viewer if d.get("action") == "meridian_flip"] == [], (
        f"a viewer behind the relay was sent the flip event: {viewer}")
    assert [d for d in viewer if d.get("action") == "centered"], viewer
    assert [d for d in operator if d.get("action") == "meridian_flip"] == [
        {"action": "meridian_flip", "site_derived": True}], operator


# ------------------------------------- (3) the engine's publish at the flip

def _flip_engine(sim_hub, monkeypatch, *, ttf_s: float):
    """An engine armed for a target ``ttf_s`` seconds of hour angle before
    its transit (negative: past it already), on the real
    `_maybe_meridian_flip` (the shape of test_w2_site_derived_flip_lines.py's
    helper, reproduced here so this file owns everything it mutates)."""
    tel = sim_hub.devices["telescope"]
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + ttf_s / 3600.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False,
                          meridian_flip_lead_min=10.0)
    e._cfg = None
    e._flip_armed = True

    async def _noop_gate(context="", target=None):
        return None

    e._safety_gate = _noop_gate
    side = {"v": "west"}

    async def pier():
        return {"east": PierSide.EAST,
                "west": PierSide.WEST}.get(side["v"], PierSide.UNKNOWN)

    async def no_countdown():
        return None

    async def goto(ra_h, dec_d, **kw):
        side["v"] = "east" if side["v"] == "west" else "west"
        return {"centered": True, "error_arcmin": 0.2}

    async def hold(target, lead_s=0.0):
        return None

    async def autofocus(label, **kw):
        return True

    monkeypatch.setattr(tel, "pier_side", pier)
    monkeypatch.setattr(tel, "time_to_meridian_flip", no_countdown)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    e._wait_for_flip_point = hold
    e._autofocus = autofocus
    return e, t


async def test_the_engines_flip_publish_is_flagged_and_dropped_for_a_viewer(
        sim_hub, monkeypatch):
    """The engine publishes ``sequence`` ``detail="meridian flip"`` as it
    takes the flip, a moment before `Hub.meridian_flip` publishes its own
    event: the same instant, on the same lane. Left unflagged it told a viewer
    the crossing the hub's flagged event withheld, so flagging the hub's alone
    protected nothing on a real run.

    Flagged on that publish ONLY: the next publish of the run is unflagged and
    reaches the viewer, and the flag is not in the engine's state (which
    would put it on every later publish of the night).

    MUTANT "the engine publish unflagged" (``site_derived=True`` dropped from
    the ``self._set_state(detail="meridian flip", ...)`` in
    `_maybe_meridian_flip`): RED, observed -
        AssertionError: the engine's flip publish was not flagged
        site_derived: {'state': 'idle', 'detail': 'meridian flip', 'sky':
        {...}, ...}
    MUTANT "the flag is kept" (`_set_state` reading ``site_derived`` with
    ``kw.get`` instead of ``kw.pop``, so it merges into ``self.state``, the
    `_first_running` mistake): RED, observed -
        AssertionError: the flag rode the publish after the flip
    """
    e, t = _flip_engine(sim_hub, monkeypatch, ttf_s=-5.0)
    e._flip_no_op.add(t.id)   # this attempt is the one retry, past the band
    sub = events.bus.subscribe()
    try:
        await e._maybe_meridian_flip(t, next_exposure_s=60.0)
        e._set_state(detail="imaging")
        got = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    seq = [ev for ev in got if ev.type == "sequence"]
    flip = [ev for ev in seq if ev.data.get("detail") == "meridian flip"]
    assert len(flip) == 1, [ev.data.get("detail") for ev in seq]
    assert is_site_derived(flip[0].to_json()), (
        f"the engine's flip publish was not flagged site_derived: "
        f"{flip[0].data}")
    after = [ev for ev in seq if ev.data.get("detail") == "imaging"]
    assert len(after) == 1
    assert not is_site_derived(after[0].to_json()), (
        "the flag rode the publish after the flip")
    assert "site_derived" not in e.state, (
        f"the flag stayed in the engine's state: {e.state['site_derived']}")
    for role in NON_HOLDERS:
        p = principal_for_role(role)
        assert _redact_ws_event(flip[0].to_json(), p) is None, role
        assert _redact_ws_event(after[0].to_json(), p) is not None, (
            f"control: {role} lost the unflagged publish after the flip")
    for role in HOLDERS:
        assert _redact_ws_event(flip[0].to_json(),
                                principal_for_role(role)) == \
            flip[0].to_json(), role


# ------------------------------- (4) the span: neighbours, tasks, alerts

class _FakeGuider:
    """A connected guider that publishes as the real one does: a ``guide``
    event and a line when it stops, and a loop task from ``start_guiding``
    that publishes ``guide`` frames for as long as it lives."""

    connected = True

    def __init__(self) -> None:
        self.loop_gate = asyncio.Event()
        self.task = None

    async def is_active(self) -> bool:
        return True

    async def stop_guiding(self) -> None:
        events.bus.publish("guide", state="stopped")
        events.bus.log("info", "guider stopped", "guide")

    async def start_guiding(self) -> None:
        async def loop():
            await self.loop_gate.wait()
            events.bus.publish("guide", state="guiding", frame=1)

        self.task = asyncio.create_task(loop())


async def test_the_span_flags_the_flips_neighbours_and_ends_with_the_flip(
        sim_hub, monkeypatch):
    """The guider's stop and the re-centre's own events are published by the
    same call, in the same millisecond, as the flip's ``mount`` event, and
    are flagged and dropped for a viewer. The guide loop the flip STARTS
    publishes after the call returns and is NOT flagged: a task started
    inside the span copies its context, and the span's end must reach it.

    MUTANT "the span never closes" (``span.open = False`` replaced by
    ``pass`` in `events.site_timed_span`): RED, observed -
        AssertionError: the guide loop the flip started was flagged
        site_derived after the flip ended
    MUTANT "the span flags nothing" (the ``data[SITE_DERIVED_KEY] = True`` in
    `EventBus.publish` removed): RED on the neighbour assertions.
    """
    guider = _FakeGuider()
    monkeypatch.setattr(sim_hub, "guider", guider)
    sides = iter(["west", "east"])

    async def side_now():
        return next(sides)

    async def goto(ra_h, dec_d, **kw):
        events.bus.publish("centering", attempt=1, error_arcmin=3.0)
        events.bus.publish("solve_activity", active=True)
        events.bus.log("info", "centering solve ok", "sequence")
        return {"centered": True, "error_arcmin": 0.2}

    monkeypatch.setattr(sim_hub, "pier_side_now", side_now)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    sub = events.bus.subscribe()
    try:
        await sim_hub.meridian_flip(1.0, 20.0)
        guider.loop_gate.set()
        await asyncio.wait_for(guider.task, timeout=5.0)
        got = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    inside = [e for e in got if e.type in ("guide", "centering",
                                           "solve_activity")
              and e.data.get("state") != "guiding"]
    assert {e.type for e in inside} == {"guide", "centering",
                                        "solve_activity"}, inside
    for e in inside:
        assert is_site_derived(e.to_json()), (
            f"{e.type} published inside the flip was not flagged: {e.data}")
        for role in NON_HOLDERS:
            assert _redact_ws_event(e.to_json(),
                                    principal_for_role(role)) is None, (
                e.type, role)
        for role in HOLDERS:
            assert _redact_ws_event(e.to_json(),
                                    principal_for_role(role)) is not None
    lines = [e for e in got if e.type == "log"
             and e.data["message"] in ("guider stopped", "centering solve ok")]
    assert len(lines) == 2 and all(is_site_derived(e.to_json())
                                   for e in lines), lines
    loop = [e for e in got if e.type == "guide"
            and e.data.get("state") == "guiding"]
    assert len(loop) == 1
    assert not is_site_derived(loop[0].to_json()), (
        "the guide loop the flip started was flagged site_derived after the "
        "flip ended")
    assert _redact_ws_event(loop[0].to_json(),
                            principal_for_role("viewer")) is not None


async def test_a_task_started_in_the_span_is_unflagged_after_it_exits():
    """The span's end reaches a task that copied its context, at the events
    layer alone: a task started inside, publishing after the block exits, is
    unflagged; one publishing before it exits is flagged.

    MUTANT "the span never closes" (``span.open = False`` replaced by
    ``pass``): RED, observed -
        AssertionError: a task started in the span was flagged after it ended
    """
    release = asyncio.Event()
    inside_done = asyncio.Event()

    async def worker():
        events.bus.publish("probe_1020", when="inside")
        inside_done.set()
        await release.wait()
        events.bus.publish("probe_1020", when="after")

    sub = events.bus.subscribe()
    try:
        with events.site_timed_span():
            task = asyncio.create_task(worker())
            await inside_done.wait()
        release.set()
        await task
        got = [e for e in _drain(sub) if e.type == "probe_1020"]
    finally:
        events.bus.unsubscribe(sub)
    by = {e.data["when"]: e for e in got}
    assert is_site_derived(by["inside"].to_json())
    assert not is_site_derived(by["after"].to_json()), (
        "a task started in the span was flagged after it ended")


async def test_a_failure_inside_the_flip_still_alerts(sim_hub, monkeypatch):
    """The span must not hide a real failure. A warning or error line
    published inside the flip, and a ``safety`` event, are not flagged by the
    span, so the alert dispatcher (which skips anything flagged) maps each to
    an alert, and a viewer is served them.

    MUTANT "the span flags warnings" (``_span_exempt`` returning False):
    RED, observed -
        AssertionError: a warning inside the flip was flagged site_derived
    """
    from astrodeck.alerting import AlertDispatcher

    sides = iter(["west", "east"])

    async def side_now():
        return next(sides)

    async def goto(ra_h, dec_d, **kw):
        events.bus.log("warning", "solve failed during the re-centre",
                       "sequence")
        events.bus.log("error", "centring gave up", "sequence")
        events.bus.publish("safety", is_safe=False, reason="clouds")
        return {"centered": False, "error_arcmin": 9.0}

    monkeypatch.setattr(sim_hub, "pier_side_now", side_now)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    sub = events.bus.subscribe()
    try:
        await sim_hub.meridian_flip(1.0, 20.0)
        got = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    bad = [e for e in got if (e.type == "log"
                              and e.data["level"] in ("warning", "error")
                              and e.data["source"] == "sequence")
           or e.type == "safety"]
    assert len(bad) == 3, [(e.type, e.data) for e in got]
    dispatcher = AlertDispatcher.__new__(AlertDispatcher)
    dispatcher._last_safe = None
    for e in bad:
        assert not is_site_derived(e.to_json()), (
            f"a {e.data.get('level', e.type)} inside the flip was flagged "
            f"site_derived: {e.data}")
        assert _redact_ws_event(e.to_json(),
                                principal_for_role("viewer")) is not None
        assert dispatcher._alert_for(e) is not None, (e.type, e.data)
