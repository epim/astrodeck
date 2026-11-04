# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.9 (#1127, members #748 and #1067): a dawn-park or sun-watch event is
timed by the site, so it reaches the night file and a cleared principal and
nobody else, and the alert sink gets a separate wording that carries no moment.

THE LEAK. A dawn-park or sun-watch line emitted on the first tick after the Sun
crosses the dawn-park threshold has its timestamp set by a site computation:
within about a minute of the crossing at the site, which is the #166 class
(``api/redact.py`` writes the argument). They were unflagged because a flagged
line is never forwarded to an alert sink, and the page is the point of these
lines.

THE RULING (D-H, "split"). Each such event writes
  * one night-file line, flagged ``site_derived``, carrying the moment: a
    principal without ``view.site_derived`` never reads it through the log
    routes (ring, night read, both exports) or either WS lane;
  * one ``site_alert`` bus event with an alert-safe wording, which the alert
    dispatcher forwards and nothing else serves.

WHAT IS GRADED. The real ``DawnPark.tick`` and ``SunWatch.tick`` run against the
process bus; the routes are the real app's, the alert path is a real
``AlertDispatcher`` over a mock webhook. The flip-hold half (#1067) grades the
status frame a viewer is served across a hold.

MUTANTS (each applied to a byte backup of the production file, run, restored by
byte copy). The observed failure is quoted in the test it grades.
"""
from __future__ import annotations

import json

import httpx
import pytest

from astrodeck import events
from astrodeck import dawn_park as dawn_park_mod
from astrodeck.alerting import AlertDispatcher
from astrodeck.api.redact import _redact_site_for, _redact_ws_event
from astrodeck.auth import principal_for_role, reset_active_provider
from astrodeck.config import AlertSink, AppConfig
from astrodeck.dawn_park import DawnPark
from astrodeck.events import NightLogWriter

from test_site_derived_log_lines import NIGHT, READS, T, _as, served  # noqa: F401
from test_sun_watch import FakeEngine, JUNE_TS, cfg, pinned_sun  # noqa: F401
from test_w14_sun_watch_blind_fallback import _driven, _tick_n
from test_w15_sun_watch_follow_ups import DAYLIGHT, _site_rig, sky  # noqa: F401

VIEWER = principal_for_role("viewer")
OPERATOR = principal_for_role("operator")

#: What a dawn park's reopen says on the night-file line, and what it must not
#: say to an alert sink: the Sun's altitude is a site computation.
SUN_ALT = -2.0
SUN_ALT_TEXT = f"{SUN_ALT:+.1f}"


@pytest.fixture(autouse=True)
def _restore():
    yield
    reset_active_provider()
    events.end_flip_hold()


# ---------------------------------------------------------------- scenarios

async def _dawn_parks(sky):
    sky["alt"] = SUN_ALT
    hub, tel = _site_rig()
    await DawnPark(hub, FakeEngine(), clock=lambda: JUNE_TS).tick()
    assert tel.park_calls == 1, "precondition: the idle mount was parked"
    return "dawn park: mount parked"


async def _dawn_park_fails(sky):
    sky["alt"] = SUN_ALT
    hub, tel = _site_rig()
    tel.park_error = RuntimeError("mount says no")
    await DawnPark(hub, FakeEngine(), clock=lambda: JUNE_TS).tick()
    assert tel.park_calls == 1, "precondition: the park was attempted"
    return "DAWN PARK FAILED"


async def _dawn_reopens_the_link(sky):
    sky["alt"] = SUN_ALT
    hub, tel = _site_rig()
    tel.connected = False
    tel.reconnects = True
    await DawnPark(hub, FakeEngine(), clock=lambda: JUNE_TS).tick()
    assert tel.connect_calls == 1, "precondition: the link was reopened"
    return "dawn park reopened the mount's link"


async def _sun_watch_blind_in_daylight(sky, pinned_sun):
    hub, tel = _site_rig(ra_hours=0.0, dec_deg=90.0, tracking=False,
                         parked=True)
    tel.connected = False
    w, now = _driven(hub)
    sky["alt"] = -4.0                       # below the -3 dawn-park threshold
    await _tick_n(w, now, 12)
    sky["alt"] = SUN_ALT                    # above it: the first daylight tick
    await w.tick()
    return DAYLIGHT


async def _sun_watch_position_unknown_in_daylight(sky):
    hub, tel = _site_rig(ra_hours=0.0, dec_deg=90.0, tracking=True)
    tel.position_known = False
    w, now = _driven(hub)
    sky["alt"] = SUN_ALT                    # above the -3 threshold
    await w.tick()
    assert tel.park_calls == 0, "precondition: nothing was parked"
    return "sun watch blind, position unknown"


SCENARIOS = ("dawn_parks", "dawn_park_fails", "dawn_reopens",
             "sun_watch_daylight", "sun_watch_position_unknown")


async def _play(name, sky, pinned_sun) -> str:
    """Run one scenario against the process bus; returns the words of the
    line it provoked."""
    if name == "dawn_parks":
        return await _dawn_parks(sky)
    if name == "dawn_park_fails":
        return await _dawn_park_fails(sky)
    if name == "dawn_reopens":
        return await _dawn_reopens_the_link(sky)
    if name == "sun_watch_position_unknown":
        return await _sun_watch_position_unknown_in_daylight(sky)
    return await _sun_watch_blind_in_daylight(sky, pinned_sun)


def _night_text() -> str:
    events.bus.night_log.flush()                # the file is written off-thread
    return NightLogWriter.path_for(NIGHT).read_text(encoding="utf-8")


# ------------------------------------------------- the log routes, per role

@pytest.mark.parametrize("read", list(READS))
@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_a_viewer_never_reads_a_dawn_park_or_sun_watch_event(
        served, cfg, pinned_sun, sky, scenario, read):
    """The ring, the night read and both exports leave the event out for a
    viewer, and an operator reads it (so the filter is not "drop everything"
    and the scenario really provoked the line).

    MUTANT "the marker dropped in dawn_park" (the ``bus.log_with_alert`` call
    of the mount-parked line replaced by an unflagged ``bus.log``): RED for
    ``[dawn_parks-*]``, observed -
        AssertionError: ring: a viewer can read a dawn-park or sun-watch
        event: [{"type":"log","data":{"level":"warning","message":"dawn
        park: mount parked. The night ended without a sequence wind-down, ...
    """
    needle = await _play(scenario, sky, pinned_sun)
    _as("operator")
    held = served.client.get(READS[read])
    assert held.status_code == 200, held.text
    assert needle in held.text, (
        f"{read}: the operator cannot read the event the scenario provoked "
        f"({needle!r}): {held.text}")
    _as("viewer")
    r = served.client.get(READS[read])
    # 404 is the night reads' answer when every line of the night is withheld.
    assert r.status_code in (200, 404), r.text
    assert needle not in r.text, (
        f"{read}: a viewer can read a dawn-park or sun-watch event: {r.text}")


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_the_night_file_holds_the_moment_flagged(
        served, cfg, pinned_sun, sky, scenario):
    """The event is in the durable night file, flagged, at its moment (the
    wall clock is pinned at ``T``), and the alert-safe event is in neither the
    file nor the ring: only the line carries the moment.

    MUTANT "the event not written flagged" is the viewer test above; this one
    fails if the flagged line is not written at all.
    """
    needle = await _play(scenario, sky, pinned_sun)
    rows = [json.loads(line) for line in _night_text().splitlines()]
    mine = [r for r in rows if needle in r["data"]["message"]]
    assert mine, f"the night file lost the line {needle!r}: {rows}"
    assert all(r["data"].get("site_derived") is True and r["ts"] == T
               for r in mine), mine
    assert not [r for r in rows if r["type"] != "log"], rows
    assert not [r for r in events.bus.log_history
                if r["type"] != "log"], "the alert-safe event reached the ring"


# --------------------------------------------------------- the alert sink

def _alert_config() -> AppConfig:
    return AppConfig(alerts=[AlertSink(
        id="w", kind="webhook", url="https://example/hook",
        events=["warning", "error"], min_level="warning")])


def _drain(sub) -> list:
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_the_alert_sink_gets_exactly_one_alert_and_it_is_the_safe_one(
        served, cfg, pinned_sun, sky, scenario):
    """Every bus event the scenario published goes through the dispatcher's
    own mapping. Exactly one alert comes out, it comes from the ``site_alert``
    event and not from the flagged log line, and a webhook sink receives it.
    A mapping that forwarded the flagged line as well would hand the sink two
    alerts (and, when the wordings differ, the line that carries the moment).

    MUTANT "the flagged line forwarded" (the ``and not
    data.get(SITE_DERIVED_KEY)`` guard removed from the log arm of
    `AlertDispatcher._alert_for`): RED, observed -
        AssertionError: the sink was handed 2 alerts for one event: [...]
    """
    sub = events.bus.subscribe()
    try:
        needle = await _play(scenario, sky, pinned_sun)
        evs = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, text="ok")

    cfg_alerts = _alert_config()
    disp = AlertDispatcher(events.bus, lambda: cfg_alerts)
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        mapped = [(ev, disp._alert_for(ev)) for ev in evs]
        alerts = [(ev, a) for ev, a in mapped
                  if a is not None and needle in a.message]
        assert len(alerts) == 1, (
            f"the sink was handed {len(alerts)} alerts for one event: "
            f"{[a.message for _ev, a in alerts]}")
        ev, alert = alerts[0]
        assert ev.type == events.SITE_ALERT_TYPE, (
            f"the alert came from a {ev.type!r} event, not the alert-safe "
            f"one")
        assert alert.level in ("warning", "error")
        await disp._dispatch(alert)
    finally:
        await disp._client.aclose()
    assert len(seen) == 1 and needle.encode() in seen[0].content, seen


async def test_the_alert_safe_wording_carries_no_sun_altitude(
        served, cfg, pinned_sun, sky):
    """The dawn-park reopen line names the Sun's altitude, which is a site
    computation. The night-file line keeps it; the wording the sink is handed
    does not."""
    sub = events.bus.subscribe()
    try:
        await _dawn_reopens_the_link(sky)
        evs = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    (line,) = [e for e in evs if e.type == "log"
               and "reopened the mount's link" in e.data["message"]]
    (page,) = [e for e in evs if e.type == events.SITE_ALERT_TYPE
               and "reopened the mount's link" in e.data["message"]]
    assert SUN_ALT_TEXT in line.data["message"], line.data
    assert SUN_ALT_TEXT not in page.data["message"], page.data
    assert SUN_ALT_TEXT not in json.dumps(page.data)
    assert "continuing with the park" in page.data["message"], (
        "the alert-safe wording still says what happened")
    assert page.data["level"] == line.data["level"] == "warning"
    assert page.data[events.SITE_DERIVED_KEY] is True


async def test_an_info_level_event_pages_nobody(served, cfg, pinned_sun, sky):
    """The sinks route warning and error. An info-level site-timed line (the
    dawn-park intent line) maps to no alert, flagged or alert-safe."""
    sub = events.bus.subscribe()
    try:
        await _dawn_parks(sky)
        evs = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    cfg_alerts = _alert_config()
    disp = AlertDispatcher(events.bus, lambda: cfg_alerts)
    infos = [e for e in evs if e.data.get("level") == "info"]
    assert infos, "precondition: the tick said an info line"
    assert [disp._alert_for(e) for e in infos] == [None] * len(infos)


# -------------------------------------------------- the alert-safe event

@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_the_alert_safe_event_is_served_to_no_websocket(served, role):
    """It exists for the dispatcher. The flagged log line is what a holder
    reads, so no WS lane (LAN or relay, both use this seam) carries the event
    to any principal: a viewer is withheld the moment, and a holder is not
    handed a second copy of a line it already has."""
    sub = events.bus.subscribe()
    try:
        events.bus.log_with_alert("error", "moment line", "safety",
                                  alert_message="alert wording")
        evs = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    (page,) = [e for e in evs if e.type == events.SITE_ALERT_TYPE]
    assert _redact_ws_event(page.to_json(), principal_for_role(role)) is None
    (line,) = [e for e in evs if e.type == "log"]
    served_line = _redact_ws_event(line.to_json(), principal_for_role(role))
    assert (served_line is None) is (role == "viewer"), served_line


def test_log_with_alert_splits_the_two_wordings(served):
    """The helper writes the moment-bearing words to the log and the other
    words to the alert event, and defaults the alert wording to the line's own
    when the line carries nothing computed from the site."""
    sub = events.bus.subscribe()
    try:
        events.bus.log_with_alert("error", "Sun at +1.2 and parked", "safety",
                                  alert_message="parked")
        events.bus.log_with_alert("warning", "plain words", "safety")
        evs = _drain(sub)
    finally:
        events.bus.unsubscribe(sub)
    assert [(e.type, e.data["message"]) for e in evs] == [
        ("log", "Sun at +1.2 and parked"), (events.SITE_ALERT_TYPE, "parked"),
        ("log", "plain words"), (events.SITE_ALERT_TYPE, "plain words")]
    assert all(e.data[events.SITE_DERIVED_KEY] is True for e in evs)
    assert [e.data["source"] for e in evs] == ["safety"] * 4


# ---------------------------------------------------- #1067: the flip hold

def _frame(ra: float, dec: float, tracking: bool) -> dict:
    return {"mount": {"connected": True, "ra_hours": ra, "dec_deg": dec,
                      "tracking": tracking, "slewing": False}}


def test_a_viewer_is_served_the_pre_hold_pointing_while_a_flip_hold_is_open():
    """``mount.ra_hours``, ``dec_deg`` and ``tracking`` stay live for a viewer
    during the hold, and on a mount whose reported coordinates move during the
    flip slew they date the flip. They are the last values served before the
    hold; an operator is served the live ones; after the hold the viewer is
    live again.

    MUTANT "live pointing served" (`_hold_mount_pointing` assigns
    ``mount[k] = mount[k]`` in place of ``flip_hold_value(k)`` while the hold
    is open): RED, observed -
        AssertionError: a viewer read the flip's slew off mount.ra_hours: 9.5
    and the "never saw" case below fails with ``assert 9.5 is None``.
    """
    before = _redact_site_for(_frame(3.25, 41.5, True), VIEWER)
    assert before["mount"]["ra_hours"] == 3.25, "precondition: live outside"
    events.begin_flip_hold()
    live = _frame(9.5, -12.0, False)
    viewer = _redact_site_for(dict(live, mount=dict(live["mount"])), VIEWER)
    operator = _redact_site_for(dict(live, mount=dict(live["mount"])),
                                OPERATOR)
    assert viewer["mount"]["ra_hours"] == 3.25, (
        f"a viewer read the flip's slew off mount.ra_hours: "
        f"{viewer['mount']['ra_hours']}")
    assert viewer["mount"]["dec_deg"] == 41.5
    assert viewer["mount"]["tracking"] is True
    assert (operator["mount"]["ra_hours"], operator["mount"]["dec_deg"],
            operator["mount"]["tracking"]) == (9.5, -12.0, False)
    events.end_flip_hold()
    after = _redact_site_for(_frame(9.5, -12.0, False), VIEWER)
    assert after["mount"]["ra_hours"] == 9.5


def test_a_viewer_who_never_saw_the_pre_hold_pointing_is_served_none():
    """With nothing noted before the hold there is no pre-hold value to serve,
    and the live one is the flip. Null, the shape of a mount that reports
    nothing, never the live value."""
    events._FLIP_HOLD_LAST.clear()
    events.begin_flip_hold()
    viewer = _redact_site_for(_frame(9.5, -12.0, False), VIEWER)
    assert viewer["mount"]["ra_hours"] is None
    assert viewer["mount"]["dec_deg"] is None
    assert viewer["mount"]["tracking"] is None
