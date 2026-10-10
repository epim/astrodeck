# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The sun watch with no site set, and the sun watch on the status frame
(#895, #894).

#895. With the rig's position latch set (owner ruling 4B, #888) the net pages
only while ``_sun_against_dawn_threshold`` says the Sun is at or above the
dawn-park threshold. With no site set (``hub.site["is_default"]``) that helper
cannot compute the Sun and returns None, and the tick resolved the doubt to
night: one info hold, which the AlertDispatcher does not route, whatever the
time of day. A missing site is a first-run and post-reset state, which is when
a position doubt is most likely. A net that cannot tell day from night takes
the daylight answer instead: ``POSITION_UNKNOWN_SUN_UNKNOWN`` at warning level
(which the dispatcher routes) at once and then every ``BLIND_LOG_EVERY`` ticks,
carrying no figure.

#894. ``SunWatch.state()`` was published on ``/api/safety/state`` only, and no
screen read it. It now rides every status frame as ``sun_watch`` (times and
booleans, so a viewer reads it), which ``ui/src/types.ts`` mirrors as
``SunWatchState`` on ``RigStatus``.

Every mutant below was applied to a byte backup of its production file, run
under the suite's normal command (xdist on), and the file restored from the
backup with its md5 compared. The observed failure is quoted at the case it
turns red. Coordinates are made up; no mount position is printed.

  A1 "the info hold restored"  ``if verdict is not None and not verdict[0]:``
     in `SunWatch._position_unknown_tick` made ``if verdict is None or not
     verdict[0]:``
  A2 "warned once only"        ``if n % BLIND_LOG_EVERY == 0:`` there made
     ``if n == 0:``
  A3 "the warning is an info line" the warning's level made ``"info"``
  A4 "the cause dropped"       the cause sentence cut in the constant
  B1 "no sun_watch on the frame" the ``out["sun_watch"] = ...`` line in
     `Hub.poll_status` replaced by ``pass``
  A5 "one count for both lines" the ``if judged != self._unknown_judged:``
     block in `SunWatch._position_unknown_tick` made ``if False:`` (the
     fix round, #895 review: a site set mid-latch in daylight waited out the
     no-site warning's cadence for the daylight error)
  A6 "the remedy dropped"      the closing sentence of the constant cut
"""
from __future__ import annotations

import json
import re

import pytest

import astrodeck.sun_watch as sun_watch_mod
from astrodeck import events
from astrodeck.alerting import AlertDispatcher
from astrodeck.mount_offset import POSITION_UNKNOWN_SAFE_ORDER
from astrodeck.sun_watch import (BLIND_LOG_EVERY, POSITION_UNKNOWN_DAYLIGHT,
                                 POSITION_UNKNOWN_NIGHT_HOLD,
                                 POSITION_UNKNOWN_SUN_UNKNOWN)

from test_888_every_move_asks_the_gate import _CUT, _approach_rig, _check_words
from test_sun_watch import JUNE_TS, cfg, pinned_sun  # noqa: F401
from test_w14_sun_watch_blind_fallback import (_driven, _tick_n,
                                               _viewer_client)
from test_w15_sun_watch_follow_ups import SITE_LAT, SITE_LON, sky  # noqa: F401
from test_types_mirror_groups import (TYPES_TS, _interface, _optional,
                                      _ts_admits)


# ============================================================ #895: no site

def _unsited_latched(cfg):
    """A tube inside the cone on a rig with NO site, the position latch set."""
    hub, tel = _approach_rig(cfg)
    hub.site["is_default"] = True
    tel.position_known = False
    return hub, tel


@pytest.fixture
def published(monkeypatch):
    """Every event the bus is asked to publish, as ``(type, data)``: the
    ``log`` data carries the level, source and site-derived flag the
    AlertDispatcher reads."""
    seen: list[tuple[str, dict]] = []

    def publish(type, **data):
        seen.append((type, data))
    monkeypatch.setattr(events.bus, "publish", publish)
    return seen


def _lines(published, message: str) -> list[dict]:
    return [d for t, d in published if t == "log" and d.get("message") == message]


def test_the_no_site_line_keeps_the_wording_rules():
    """The action inside the humanizer's 137-character cut, cover before the
    safe order, no goto word, no humanizer pair, no digit (a figure), and the
    cause said in full after the cut.

    MUTANT A4 "the cause dropped" (``It cannot judge the Sun (no site set,
    or the Sun cannot be computed), `` cut to ``It cannot judge the Sun, `` in
    the constant): RED -
        AssertionError: the line does not say why it cannot judge the Sun
    MUTANT A6 "the remedy dropped" (the sentence ``If no site is set, set one
    and the net can judge the Sun`` cut from the constant): RED -
        AssertionError: the line does not say how to make it able to judge
    """
    line = POSITION_UNKNOWN_SUN_UNKNOWN
    _check_words(line)
    head = line[:_CUT]
    assert POSITION_UNKNOWN_SAFE_ORDER in head, line
    assert head.index("cover") < head.index(POSITION_UNKNOWN_SAFE_ORDER)
    assert not any(ch.isdigit() for ch in line), line
    assert "no site set" in line, "the line does not say why it cannot judge the Sun"
    assert "set one" in line, "the line does not say how to make it able to judge"
    assert "tell day from night" in line
    assert "It will not park" in line
    # Not the daylight line (that one claims the Sun is up) and not the hold.
    assert line != POSITION_UNKNOWN_DAYLIGHT
    assert POSITION_UNKNOWN_NIGHT_HOLD not in line


@pytest.mark.parametrize("alt", [-30.0, -2.0], ids=["sun below", "sun above"])
async def test_a_latched_rig_with_no_site_warns_at_once_and_on_the_blind_cadence(
        cfg, pinned_sun, sky, published, alt):
    """THE CASE OF THE ISSUE. No site, the position latch set, whatever the
    Sun is really doing (the net cannot know, so the case runs it both ways):
    a WARNING on the first tick, again every ``BLIND_LOG_EVERY`` ticks, source
    "safety", unflagged, which the AlertDispatcher maps to a warning alert.
    The Sun is never computed, so no site figure can be in it, and the quiet
    night hold is never said. Nothing parks and tracking is untouched.

    MUTANT A1 "the info hold restored" (see the header): RED, both cases
    here and the two below that need the loud answer (the Sun that will not
    compute, and the site set mid-latch) -
        AssertionError: no warning within the first tick; the quiet hold was
        the only line: ['sun watch held off: position unknown, no park: tube
        at home, Trust position; else bring it home by eye with a pad key,
        then Trust it.']
    MUTANT A2 "warned once only" (header): RED, both -
        AssertionError: expected the first tick and two more cadences: 1
        assert 1 == 3
    MUTANT A3 "the warning is an info line" (header): RED, both -
        AssertionError: [{'level': 'info', 'message': 'sun watch: position
        unknown, cover it; tube at home, Trust position; ...
        assert False
    """
    hub, tel = _unsited_latched(cfg)
    sky["alt"] = alt
    w, now = _driven(hub)

    await _tick_n(w, now, 1)
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 1, (
        "no warning within the first tick; the quiet hold was the only line: "
        f"{[d.get('message') for t, d in published if t == 'log']}")

    await _tick_n(w, now, 2 * BLIND_LOG_EVERY)
    said = _lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)
    assert len(said) == 3, f"expected the first tick and two more cadences: {len(said)}"
    assert all(d["level"] == "warning" and d["source"] == "safety"
               and not d.get(events.SITE_DERIVED_KEY) for d in said), said

    dispatcher = AlertDispatcher(events.bus, lambda: cfg)
    alerts = [dispatcher._alert_for(events.Event("log", d)) for d in said]
    assert [a and (a.level, a.message) for a in alerts] == [
        ("warning", POSITION_UNKNOWN_SUN_UNKNOWN)] * 3, alerts

    assert not [d for t, d in published if t == "log"
                and str(d.get("message", "")).startswith("sun watch held off")], (
        "the quiet night hold was said on a rig that cannot tell night")
    assert _lines(published, POSITION_UNKNOWN_DAYLIGHT) == [], (
        "the daylight error claims the Sun is up; this rig cannot know")
    assert sky["asked"] == [], f"the Sun was computed for a site: {sky['asked']}"
    assert tel.park_calls == 0 and tel.tracking_calls == [], "the latched net moved the mount"
    st = w.state()
    assert st["position_unknown"] is True and st["position_unknown_since"] == JUNE_TS


async def test_a_site_that_is_set_keeps_the_quiet_night_hold(
        cfg, pinned_sun, sky, published, bus_lines):
    """CONTROL. The same rig WITH a site and the Sun below the threshold: the
    one info hold and no warning, so the new loudness belongs to "cannot
    judge the Sun" and not to every latched night.
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await _tick_n(w, now, 2 * BLIND_LOG_EVERY)
    assert _lines(published, POSITION_UNKNOWN_SUN_UNKNOWN) == []
    assert _lines(published, POSITION_UNKNOWN_DAYLIGHT) == []
    held = [(lvl, m) for lvl, m, _s in bus_lines if m.startswith("sun watch held off")]
    assert held == [("info", "sun watch held off: " + POSITION_UNKNOWN_NIGHT_HOLD)], held
    assert (SITE_LAT, SITE_LON) in sky["asked"], "premise: the Sun was computed for the site"


async def test_setting_the_site_hands_the_alert_back_to_the_suns_clock(
        cfg, pinned_sun, sky, published):
    """A site set while the position is still unknown: the next tick reads the
    Sun for it. Below the threshold the warning stops and the night hold is
    said; the Sun coming up after that says the daylight error at once. The
    below-threshold tick has reset the count by then, so this case does not
    prove the hand-over: a site set while the Sun is ALREADY up is the next
    case, where only the change of what the net can judge resets it.
    """
    hub, tel = _unsited_latched(cfg)
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await _tick_n(w, now, 3)
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 1

    hub.site["is_default"] = False                   # the operator sets a site
    await _tick_n(w, now, 3)
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 1, (
        "the warning went on after the site was set and the Sun was below")
    assert _lines(published, POSITION_UNKNOWN_DAYLIGHT) == []

    sky["alt"] = -2.0                                # the Sun comes up
    await _tick_n(w, now, 1)
    assert len(_lines(published, POSITION_UNKNOWN_DAYLIGHT)) == 1, (
        "the Sun above the threshold did not say the daylight error at once")


async def test_a_site_set_while_the_sun_is_up_says_the_daylight_error_on_the_next_tick(
        cfg, pinned_sun, sky, published):
    """THE FIRST-RUN FLOW. No site, the mount powered, the Sun really up (the
    net cannot know): the no-site warning has been counting its cadence. The
    operator sets the site in the wizard; the very next tick reads the Sun
    for it, finds it above the threshold and says the DAYLIGHT ERROR, the page
    for an error-level sink. It must not wait out the rest of the warning's
    ``BLIND_LOG_EVERY`` ticks (the review measured 28 ticks), and the error's
    own cadence starts from that tick.

    The other way round is the same hand-over: the site lost with the Sun up
    goes from the error back to the warning on the next tick.

    MUTANT A5 "one count for both lines" (header): RED -
        AssertionError: the daylight error did not come on the next tick
        after the site was set: 0 lines (the count the warning ran on made it
        wait)
    """
    hub, tel = _unsited_latched(cfg)
    sky["alt"] = -2.0                               # the Sun is up
    w, now = _driven(hub)
    await _tick_n(w, now, 3)
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 1
    assert _lines(published, POSITION_UNKNOWN_DAYLIGHT) == []

    hub.site["is_default"] = False                  # the operator sets a site
    await _tick_n(w, now, 1)
    said = _lines(published, POSITION_UNKNOWN_DAYLIGHT)
    assert len(said) == 1, (
        "the daylight error did not come on the next tick after the site was "
        f"set: {len(said)} lines (the count the warning ran on made it wait)")
    assert said[0]["level"] == "error" and said[0]["source"] == "safety"
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 1, (
        "the no-site warning went on after the site was set")

    # Its own cadence runs from that tick: not again before BLIND_LOG_EVERY
    # ticks, then once.
    await _tick_n(w, now, BLIND_LOG_EVERY - 1)
    assert len(_lines(published, POSITION_UNKNOWN_DAYLIGHT)) == 1
    await _tick_n(w, now, 1)
    assert len(_lines(published, POSITION_UNKNOWN_DAYLIGHT)) == 2

    hub.site["is_default"] = True                   # the site is lost again
    await _tick_n(w, now, 1)
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 2, (
        "the warning did not come back on the next tick after the site was lost")
    assert len(_lines(published, POSITION_UNKNOWN_DAYLIGHT)) == 2
    assert tel.park_calls == 0 and tel.tracking_calls == []


async def test_a_sun_that_cannot_be_computed_is_not_a_quiet_night(
        cfg, pinned_sun, sky, published, monkeypatch):
    """The helper's other 'cannot tell': a site IS set but the Sun will not
    compute. The doubt is resolved to the loud answer, not the quiet one.
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False

    def broken(lat, lon, ts=None):
        raise ValueError("no ephemeris")
    monkeypatch.setattr(sun_watch_mod, "sun_altaz", broken)
    w, now = _driven(hub)
    await _tick_n(w, now, 1)
    assert len(_lines(published, POSITION_UNKNOWN_SUN_UNKNOWN)) == 1
    assert tel.park_calls == 0 and tel.tracking_calls == []


# ================================================== #894: the status frame

def test_the_status_frame_carries_the_sun_watch_state_for_a_viewer(
        tmp_path, monkeypatch, cfg, pinned_sun, sky):
    """GET /api/status, as a VIEWER, carries ``sun_watch`` and it is the net's
    own ``state()``: healthy, then the position latch set, then blind. Times
    and booleans, exactly the six keys: nothing that locates the tube or the
    Sun can ride along without this set (or the type check) changing.

    MUTANT B1 "no sun_watch on the frame" (header): RED here and in the
    types mirror below -
        KeyError: 'sun_watch'
    """
    import asyncio
    app_module, client, reset = _viewer_client(tmp_path, monkeypatch)
    try:
        hub, tel = _approach_rig(cfg)
        sky["alt"] = -30.0
        w, now = _driven(hub)
        monkeypatch.setattr(app_module, "sun_watch", w)
        monkeypatch.setattr(app_module.hub, "sun_watch", w, raising=False)

        asyncio.run(w.tick())
        sw = client.get("/api/status").json()["sun_watch"]
        assert sw == json.loads(json.dumps(w.state())), sw
        assert sw["position_unknown"] is False and sw["blind"] is False

        tel.position_known = False
        now["t"] = JUNE_TS + 60.0
        asyncio.run(w.tick())
        sw = client.get("/api/status").json()["sun_watch"]
        assert sw["position_unknown"] is True, sw
        assert sw["position_unknown_since"] == JUNE_TS + 60.0, sw
        assert sw["blind"] is False and sw["blind_since"] is None, sw

        tel.position_known = True
        tel.position_error = RuntimeError("mount not answering")
        now["t"] = JUNE_TS + 120.0
        asyncio.run(w.tick())
        sw = client.get("/api/status").json()["sun_watch"]
        assert sw["blind"] is True and sw["blind_since"] == JUNE_TS + 120.0, sw
        assert sw["position_unknown"] is False, sw

        assert set(sw) == {"blind", "blind_since", "position_unknown",
                           "position_unknown_since", "last_position_at",
                           "armed"}, sw
        assert all(isinstance(v, (bool, float, type(None))) for v in sw.values()), sw
        # Nothing in it is a site figure or the Sun's height.
        flat = json.dumps(sw)
        for figure in (repr(SITE_LAT), repr(SITE_LON), repr(sky["alt"])):
            assert figure not in flat, f"a site or Sun figure on the frame: {figure}"
    finally:
        reset()


def test_a_hub_with_no_net_attached_publishes_no_sun_watch_key(
        tmp_path, monkeypatch, cfg):
    """ABSENT, not null and not 'armed: false': a bare hub says nothing about
    a net it does not have, and the UI reads a missing key as unknown.
    """
    app_module, client, reset = _viewer_client(tmp_path, monkeypatch)
    try:
        monkeypatch.delattr(app_module.hub, "sun_watch", raising=False)
        body = client.get("/api/status")
        assert body.status_code == 200, body.text
        assert "sun_watch" not in body.json()
    finally:
        reset()


# ---------------------------------------------------- ui/src/types.ts mirror

pytestmark_types = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="ui/ not present (server-only checkout)")


@pytestmark_types
def test_ui_types_mirror_the_state_the_status_frame_carries(
        tmp_path, monkeypatch, cfg, pinned_sun, sky):
    """``SunWatchState`` in ui/src/types.ts declares exactly the keys the
    status frame sends, with types that admit every value it sends (a time as
    a number and as null, the flags as booleans), and ``RigStatus`` declares
    ``sun_watch`` optional and as that record.

    RED under "a TS field the server never sends" (``stale_since: number |
    null;`` added to ``SunWatchState``): AssertionError: SunWatchState
    drifted: the server sends [], which types.ts does not declare, and
    types.ts declares ['stale_since'], which the server never sends
    """
    import asyncio
    app_module, client, reset = _viewer_client(tmp_path, monkeypatch)
    try:
        hub, tel = _approach_rig(cfg)
        sky["alt"] = -30.0
        w, now = _driven(hub)
        monkeypatch.setattr(app_module, "sun_watch", w)
        monkeypatch.setattr(app_module.hub, "sun_watch", w, raising=False)

        records = []
        asyncio.run(w.tick())                                   # healthy
        records.append(client.get("/api/status").json()["sun_watch"])
        tel.position_known = False
        now["t"] += 60.0
        asyncio.run(w.tick())                                   # latched
        records.append(client.get("/api/status").json()["sun_watch"])
        tel.position_known = True
        tel.position_error = RuntimeError("mount not answering")
        now["t"] += 60.0
        asyncio.run(w.tick())                                   # blind
        records.append(client.get("/api/status").json()["sun_watch"])

        sent = set().union(*map(set, records))
        ts = _interface("SunWatchState")
        assert sent == set(ts), (
            f"SunWatchState drifted: the server sends {sorted(sent - set(ts))}, "
            f"which types.ts does not declare, and types.ts declares "
            f"{sorted(set(ts) - sent)}, which the server never sends")
        assert _optional("SunWatchState") == set(), "every key is always sent"
        for rec in records:
            wrong = {k: (v, ts[k]) for k, v in rec.items()
                     if not _ts_admits(v, ts[k])}
            assert not wrong, f"types.ts types these otherwise: {wrong}"
        # Each null is seen null once and each time seen as a number once.
        for key in ("blind_since", "position_unknown_since"):
            vals = [r[key] for r in records]
            assert None in vals and any(isinstance(v, float) for v in vals), (key, vals)

        status = _interface("RigStatus")
        assert re.fullmatch(r"SunWatchState", status["sun_watch"].strip()), status["sun_watch"]
        assert "sun_watch" in _optional("RigStatus"), (
            "the frame omits the key on a hub with no net; types.ts must say optional")
    finally:
        reset()
