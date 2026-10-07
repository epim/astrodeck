# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-113: the sun watch's blind fallback, after wave 14 (#696, and the part of
#137 that was left over). The third follow-up, #695 (a lane that starts inside
the ``_is_parked`` await), lives beside the other hands-off cases in
``test_sun_watch.py``.

#696. The blind fallback projects from ``_last_good``, the last position THIS
net read. Other paths park the mount too: the engine's wind-down and
``dawn_park``. When one of them parks and the link then drops before the net's
next tick, the fallback projected from the pre-park pointing, logged one false
"Parking now" error (which pages the owner) and sent a park to a mount that was
already parked. ``SunWatch.note_parked()`` is the hook those two call after a
CONFIRMED park; the blind projection takes a noted park as "no motion possible"
until the next live read. The hook is reached through the hub
(``getattr(hub, "sun_watch", None)``, which ``SunWatch.__init__`` sets, as
``DewController`` sets ``hub.dew_controller``): no global, and ``api.app`` needs
no edit.

#137, the item left after WP-03 and WP-87 (the issue's own suggested fix, item
1, the "as soon as the Sun is above the dawn-park threshold" half). While blind,
ONE error is
logged as soon as the Sun is above the dawn-park threshold, because from then
on an unparked mount may be tracking toward it and this net cannot tell. The
threshold is ``dawn_park.park_threshold_deg``, read where dawn park reads it
(the later of ``safety.twilight_deg`` and civil twilight), and the Sun's
altitude is computed from ``hub.site`` the way dawn park computes it. Debounced
like the other blind lines: once per blind streak and per Sun-up stretch, not
once per tick; it re-arms when the Sun goes back below the threshold and when
the streak ends. Neither the mount's pointing nor the Sun's altitude is ever
in the line (only the configured threshold is), so it can go to the alert
sinks: a line flagged ``site_derived`` never does, and paging is the point.

Every geometry a decision turns on is COMPUTED with ``closest_approach`` and
asserted as a precondition first, and each "it did not park" case has a
control in which the same blind ticks DO park (``test_without_the_note_the_
same_blind_ticks_park_a_parked_mount``), so none of them can pass because the
tube was never in the cone.

MUTANTS RUN, each applied from a byte backup, restored byte-identically (sha256
compared) and grepped gone; the case that failed and its first assertion,
verbatim. (m16, m17 and m18, the #695 mutants, are in test_sun_watch.py.)

  m1  ``SunWatch.note_parked`` is a no-op (its one statement becomes ``pass``).
      Six cases red: all four ``test_a_park_by_another_path_is_not_parked_again_
      while_blind`` kinds (``AssertionError: wind_down: the blind net parked a
      mount that was already parked`` / ``assert 2 == 1``; the dawn_already_
      parked kind ``assert 1 == 0``), ``test_a_read_that_began_before_the_park_
      does_not_end_the_note`` and ``test_a_noted_park_is_not_a_daylight_
      hazard``.
  m2  the engine's wind-down does not call ``note_parked``: the ``[wind_down]``
      and ``[wind_down_aborted]`` kinds, ``assert 2 == 1``.
  m3  dawn park does not call it after its own park: the ``[dawn_park]`` kind,
      ``assert 2 == 1``.
  m4  dawn park does not call it when it finds the mount already parked: the
      ``[dawn_already_parked]`` kind, ``assert 1 == 0``.
  m5  ``_remember`` does not end the note on a live read (``if False:`` for
      ``if noted is not None and noted <= started:``):
      ``test_a_live_read_ends_the_noted_park``: ``AssertionError: the note
      ended at the live read, so the projection is the net's own again and
      parks`` / ``assert 0 == 1``.
  m6  ``_remember`` ends the note on ANY read, one that began before the park
      too (``if noted is not None:``): ``test_a_read_that_began_before_the_
      park_does_not_end_the_note``: ``AssertionError: the read began before the
      park, so the note stands and nothing is projected from the pre-park
      pointing`` / ``assert 1 == 0``.
  m7  ``_blind_projection`` ignores the note (``if False:`` for its first
      check): the four kinds, ``assert 2 == 1`` (``assert 1 == 0`` for
      dawn_already_parked), and the m6 case.
  m8  the daylight check is deleted from ``_blind``:
      ``test_blind_in_daylight_logs_one_error_as_soon_as_the_sun_is_up``:
      ``AssertionError: one error on the first tick with the Sun up`` /
      ``assert [] == ['error']`` (also the debounce and leak cases).
  m9  the threshold is a constant 0.0 instead of ``park_threshold_deg``: the
      same case and message, ``assert [] == ['error']`` (the Sun at -2 is above
      the configured -3 and below 0).
  m10 the threshold is a constant -6.0: ``AssertionError: the Sun is below the
      dawn-park threshold: only the ordinary lines`` / ``assert ['error'] ==
      []`` (the Sun at -4 is below the configured -3).
  m11 the latch is never set (an error every blind tick):
      ``test_the_daylight_error_is_debounced_and_re_armed``: ``AssertionError:
      21`` / ``assert 21 == 1``.
  m12 the latch is not released when the Sun goes back below the threshold: the
      same case, ``AssertionError: a second dawn in the same outage is a second
      error`` / ``assert 1 == 2``.
  m13 ``_drop_blind`` does not release the latch: the same case, ``AssertionError:
      a new streak starts its own count and says it once`` / ``assert 2 == 3``.
  m14 the line carries the Sun's altitude (``_sun_against_dawn_threshold``
      returns it in the threshold's place):
      ``test_the_daylight_error_never_carries_a_position``: ``AssertionError: a
      position or the Sun's altitude is in the line: ['-2']`` / ``assert ['-2']
      == []`` (and ``assert '-3' in "sun watch BLIND IN DAYLIGHT: ..."`` in the
      first daylight case).
  m15 a noted park does not suppress the line: ``test_a_noted_park_is_not_a_
      daylight_hazard``: ``AssertionError: the park is confirmed`` / ``assert
      ['error'] == []`` (and every kind of the main case, whose Sun is up).
  m19 the fallback note stops saying a park was confirmed: the four kinds,
      ``AssertionError: and its line says why nothing is being projected`` /
      ``assert False``.
  m20 the engine's note is skipped when the wind-down was cancelled (``if parked
      and not cancelled:``): the ``[wind_down_aborted]`` kind only,
      ``AssertionError: wind_down_aborted: the blind net parked a mount that was
      already parked`` / ``assert 2 == 1``.
  m21 ``SunWatch.__init__`` does not attach itself to the hub (``hub.sun_watch =
      self`` becomes ``pass``): the four kinds, ``assert 2 == 1``.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck import dawn_park as dawn_park_mod
from astrodeck import sun_watch as sun_watch_mod
from astrodeck.dawn_park import DawnPark
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sun_watch import BLIND_ERROR_AFTER

from test_sun_watch import (FakeEngine, FakeHub, FakeTel, JUNE_TS,  # noqa: F401
                            cfg, pinned_sun)
from test_w14_sun_watch_blind_fallback import (DEC, LINK_DOWN, UNREADABLE,
                                               _driven, _levels, _tick_n,
                                               _west_stopped_rig)

#: A site that is emphatically not the owner's; only its non-default flag
#: matters, because the Sun's altitude is pinned.
SITE_LAT, SITE_LON = 12.5, -77.25

#: The daylight error's own prefix, so a case can tell it from the ordinary
#: blind lines that share its words.
DAYLIGHT = "BLIND IN DAYLIGHT"


# ------------------------------------------------------------------ harness

class SiteHub(FakeHub):
    """``FakeHub`` with an observing site, which the daylight check reads."""

    def __init__(self, tel=None, *, is_default: bool = False) -> None:
        super().__init__(tel)
        self.site = {"name": "Ridge Test Site", "latitude": SITE_LAT,
                     "longitude": SITE_LON, "elevation_m": 0.0,
                     "is_default": is_default, "horizon_min_deg": 10.0}


def _site_rig(**tel_kw):
    hub = SiteHub()
    tel = FakeTel(hub=hub, **tel_kw)
    hub.devices["telescope"] = tel
    return hub, tel


@pytest.fixture
def sky(monkeypatch, cfg):
    """The Sun's altitude, decided by the test. Records the site it was asked
    for. ``twilight_deg`` is -3, so the dawn-park threshold is -3 (the later
    of -3 and civil twilight's -6): a Sun at -4 is below it and a Sun at -2 is
    above it, and both a constant 0 and a constant -6 would be wrong."""
    cfg.safety.twilight_deg = -3.0
    state = {"alt": -30.0, "asked": []}

    def sun_altaz(lat, lon, ts=None):
        state["asked"].append((lat, lon))
        return state["alt"], 0.0

    monkeypatch.setattr(sun_watch_mod, "sun_altaz", sun_altaz)
    monkeypatch.setattr(dawn_park_mod, "sun_altaz", sun_altaz)
    monkeypatch.setattr(dawn_park_mod, "config_store",
                        type("_S", (), {"cfg": staticmethod(lambda: cfg)})())
    return state


def _west_stopped_site_rig(cfg):
    """``test_w14``'s STOPPED mount well west of the Sun (clear at read time
    under both projections, carried into the cone by the sky some time later),
    on a hub that has a site and a motion lock. Returns the hub, the mount, and
    the age in seconds at which the stale pointing first projects into the
    cone."""
    _hub, _tel, ra, threshold_s = _west_stopped_rig(cfg)   # its preconditions
    hub = SiteHub()
    tel = FakeTel(hub=hub, ra_hours=ra, dec_deg=DEC, tracking=False)
    hub.devices["telescope"] = tel
    return hub, tel, ra, threshold_s


def _errors_but_the_blind_ones(lines) -> list[str]:
    """Every error line the net wrote that is not the blind escalation itself
    ("sun watch held off: ..."): the park block's "Parking now", "PARK FAILED"
    and "mount parked" lines, which are what a false park looks like."""
    return [m for lv, m, src in lines
            if lv == "error" and src == "safety"
            and not m.startswith("sun watch held off")]


async def _another_path_parks(kind: str, hub, tel, watch, now) -> int:
    """Park the mount through ``kind``, a path that is not the sun watch, and
    return how many park commands that path itself sent.

    ``wind_down``: the real engine's ``_wind_down_park_and_close``.
    ``wind_down_aborted``: the same, with an operator's Abort landing while it
    waits for the guider, so the wind-down ends in a cancel AFTER its park
    (the run ends, the park does not, #305): the note must be made before the
    cancel is raised.
    ``dawn_park``: the real ``DawnPark.tick`` with the Sun up and nothing
    running, which parks the idle mount itself.
    ``dawn_already_parked``: the same tick, on a mount that already reports
    itself parked (somebody parked it from the UI): dawn park sends nothing.
    ``control``: a bare ``tel.park()`` that tells nobody, as every other park
    path in the tree still does."""
    if kind == "wind_down":
        engine = SequenceEngine(hub)
        await engine._wind_down_park_and_close(True, False)
        return 1
    if kind == "wind_down_aborted":
        engine = SequenceEngine(hub)
        guider_stop = asyncio.get_running_loop().create_future()
        winding_down = asyncio.ensure_future(engine._wind_down_park_and_close(
            True, False, guider_stop=guider_stop))
        await asyncio.sleep(0)              # it is polling for the guider
        winding_down.cancel()               # the Abort
        guider_stop.set_result(None)        # the guider then answers
        outcome, = await asyncio.gather(winding_down, return_exceptions=True)
        assert isinstance(outcome, asyncio.CancelledError), (
            f"precondition: the Abort ended the wind-down: {outcome!r}")
        return 1
    if kind in ("dawn_park", "dawn_already_parked"):
        if kind == "dawn_already_parked":
            tel.parked = True
        dp = DawnPark(hub, FakeEngine(), clock=lambda: JUNE_TS)
        await dp.tick()
        return 0 if kind == "dawn_already_parked" else 1
    assert kind == "control", kind
    await tel.park()
    return 1


def _drop_the_link(tel) -> None:
    """The mount's link dies: ``connected`` goes False, the reopen fails (the
    ``FakeTel`` default) and every read raises, as on a dead serial port. A
    double that kept answering ``is_parked`` True would let the blind
    projection's own parked check hide the defect."""
    tel.connected = False

    async def _dead() -> bool:
        raise OSError("the mount's link is down")

    tel.is_parked = _dead


async def _blind_stretch(hub, tel, kind: str, sky_):
    """Read a stopped tube once, let ``kind`` park it, and drop the link.
    Returns ``(watch, now, read_at)``; the caller ticks from there.

    The Sun is below the dawn-park threshold for the read and above it from the
    park on: dawn park needs it up to act, and with it up a dark mount would
    log the daylight error unless a noted park rightly suppresses it, so every
    case here also covers that suppression."""
    sky_["alt"] = -30.0
    w, now = _driven(hub)
    await w.tick()                             # the last good read
    assert tel.park_calls == 0, "precondition: quiet at read time"
    read_at = now["t"]
    sky_["alt"] = 5.0
    others = await _another_path_parks(kind, hub, tel, w, now)
    assert tel.park_calls == others, (
        f"precondition: {kind} sent {others} park(s), got {tel.park_calls}")
    assert tel.parked is True, f"precondition: {kind} left the mount parked"
    _drop_the_link(tel)
    return w, now, read_at


# ------------------------------------------------------------- #696: the note

@pytest.mark.parametrize("kind", ["wind_down", "wind_down_aborted",
                                  "dawn_park", "dawn_already_parked"])
async def test_a_park_by_another_path_is_not_parked_again_while_blind(
        cfg, pinned_sun, sky, bus_lines, kind):
    """#696. The mount is read once, STOPPED and clear under both projections.
    Another path then parks it (the engine's wind-down, or dawn park, either by
    parking it or by finding it parked), and the link drops before the net's
    next tick. The stale pre-park pointing, aged as a stopped mount, is carried
    into the cone by the sky: 40 blind ticks from the moment it gets there must
    send NO second park and write NO park-block error line (the false "Parking
    now" pages the owner). The blind escalation itself must still speak: a
    noted park changes what the net projects, never whether it says it is
    blind.

    Mutants m1 (``note_parked`` a no-op), m7 (the projection ignores the
    note), m21 (the net not attached to the hub), and m2/m3/m4/m20 (one call
    site removed, or skipped on a cancel) each turn their own kinds red, on
    ``assert 2 == 1`` (``assert 1 == 0`` for m4); see the module docstring.
    """
    hub, tel, ra, threshold_s = _west_stopped_site_rig(cfg)
    w, now, read_at = await _blind_stretch(hub, tel, kind, sky)
    parks_before = tel.park_calls

    now["t"] = read_at + threshold_s
    await _tick_n(w, now, 40)

    assert tel.park_calls == parks_before, (
        f"{kind}: the blind net parked a mount that was already parked")
    assert _errors_but_the_blind_ones(bus_lines) == [], bus_lines
    assert "error" in _levels(bus_lines, LINK_DOWN), (
        "the blind escalation still reaches error: a noted park is not "
        "silence")
    assert any("a park was confirmed after the last position" in m
               for _lv, m, _s in bus_lines), (
        "and its line says why nothing is being projected")


async def test_without_the_note_the_same_blind_ticks_park_a_parked_mount(
        cfg, pinned_sun, sky, bus_lines):
    """The control for the case above, and the premise of #696: with the same
    rig and the same 40 ticks, a park that tells nobody (every other park path
    in the tree) leaves the net projecting from the pre-park pointing, and it
    logs the false "Parking now" error and sends the second park."""
    hub, tel, ra, threshold_s = _west_stopped_site_rig(cfg)
    w, now, read_at = await _blind_stretch(hub, tel, "control", sky)

    now["t"] = read_at + threshold_s
    await _tick_n(w, now, 40)

    assert tel.park_calls == 2, "the premise: a false second park"
    assert any("Parking now" in m for m in _errors_but_the_blind_ones(
        bus_lines)), "and its false error line"


async def test_a_live_read_ends_the_noted_park(cfg, pinned_sun, sky, bus_lines):
    """The note lasts "until the next live read", no longer. An operator who
    unparks and points the stopped tube back west after the park is the case: a
    live read sees it, the note is gone, and when the link then drops the net
    projects from THAT read and parks it as it would have before the note
    existed. A note that outlived the read would leave the tube unprotected
    for as long as the outage lasts (m5: ``assert 0 == 1``).

    The read is made with the mount NOT parked, so the tick is a plain live
    read of a tube that is clear under both projections."""
    hub, tel, ra, threshold_s = _west_stopped_site_rig(cfg)
    w, now = _driven(hub)
    await w.tick()
    w.note_parked()                            # a park, confirmed elsewhere
    now["t"] += 60.0
    await w.tick()                             # the next live read
    assert tel.park_calls == 0, "precondition: nothing to park at read time"
    read_at = now["t"]
    _drop_the_link(tel)

    now["t"] = read_at + threshold_s
    await w.tick()

    assert tel.park_calls == 1, (
        "the note ended at the live read, so the projection is the net's own "
        "again and parks")


async def test_a_read_that_began_before_the_park_does_not_end_the_note(
        cfg, pinned_sun, sky, bus_lines):
    """A position read that was IN FLIGHT when the park was confirmed reports
    the pre-park pointing, so it is not "the next live read" and must not end
    the note. The read is slow in exactly the situation this is for (a dying
    link, up to 30 s), so the overlap is not hypothetical: the wind-down's park
    confirms while the tick is still waiting on ``get_position``.

    The double advances the clock and notes the park from inside the read, then
    answers with the pre-park pointing. m6 (``_remember`` ends the note on any
    read): ``assert 1 == 0`` on ``park_calls``; see the module docstring."""
    hub, tel, ra, threshold_s = _west_stopped_site_rig(cfg)
    w, now = _driven(hub)
    inner = tel.get_position

    async def slow_read_that_overlaps_the_park() -> tuple[float, float]:
        position = await inner()               # the pre-park pointing
        now["t"] += 5.0                        # the read takes five seconds
        w.note_parked()                        # and a park confirms meanwhile
        return position

    tel.get_position = slow_read_that_overlaps_the_park
    await w.tick()
    read_at = now["t"]
    assert w.state()["last_position_at"] == read_at, (
        "precondition: the slow read succeeded (an exception inside it would "
        "be swallowed as an unreadable mount and leave nothing to project)")
    tel.get_position = inner
    _drop_the_link(tel)

    now["t"] = read_at + threshold_s
    await _tick_n(w, now, 3)

    assert tel.park_calls == 0, (
        "the read began before the park, so the note stands and nothing "
        "is projected from the pre-park pointing")


# -------------------------------------------------- #137: blind in daylight

@pytest.mark.parametrize("failure", ["link_down", "unreadable"])
async def test_blind_in_daylight_logs_one_error_as_soon_as_the_sun_is_up(
        cfg, pinned_sun, sky, bus_lines, failure):
    """#137, the item that was left. A dark mount under a Sun below the
    dawn-park threshold (-3 here, from ``safety.twilight_deg``) gets only the
    ordinary count-driven lines. The first blind tick with the Sun above the
    threshold logs ONE error line, the first tick it is up, not at tick 30,
    and nothing else with that prefix however long the outage runs. Both ways
    the mount goes dark reach it (``_blind`` has two callers), and the Sun's
    altitude is computed for the hub's own site.

    Mutants m8 (the check deleted) and m9 (a constant 0.0 threshold) fail on
    ``assert [] == ['error']``, m10 (a constant -6.0 threshold) on ``assert
    ['error'] == []``; see the module docstring."""
    hub, tel = _site_rig(ra_hours=0.0, dec_deg=90.0, tracking=False,
                         parked=True)
    if failure == "link_down":
        tel.connected = False
    else:
        tel.position_error = RuntimeError("mount not answering")
    w, now = _driven(hub)

    sky["alt"] = -4.0                          # below the -3 threshold
    await _tick_n(w, now, 12)
    assert _levels(bus_lines, DAYLIGHT) == [], (
        "the Sun is below the dawn-park threshold: only the ordinary lines")
    assert "warning" in _levels(bus_lines, "sun watch held off"), (
        "precondition: the ordinary blind lines did run")

    sky["alt"] = -2.0                          # above it
    await w.tick()
    assert _levels(bus_lines, DAYLIGHT) == ["error"], (
        "one error on the first tick with the Sun up")
    assert (SITE_LAT, SITE_LON) in sky["asked"], (
        "the Sun was computed for the hub's own site")
    line = next(m for _lv, m, _s in bus_lines if DAYLIGHT in m)
    assert "-3" in line, "the line names the threshold it read"
    assert LINK_DOWN in line or UNREADABLE in line, (
        "and says which way the mount is dark")
    assert tel.park_calls == 0, "the daylight line parks nothing"


async def test_the_daylight_error_is_debounced_and_re_armed(
        cfg, pinned_sun, sky, bus_lines):
    """ONE error per blind streak per Sun-up stretch. Twenty more blind ticks
    with the Sun still up add nothing; the Sun going back below the threshold
    mid-streak (a night that began blind) and rising again says it again; and
    ending the streak (a live read) lets the NEXT outage say it once more.

    m11 (the latch never set): ``assert 21 == 1``. m12 (not released when the
    Sun goes down): ``assert 1 == 2`` after the dusk and dawn. m13 (not released
    when the streak ends): ``assert 2 == 3`` after the recovery."""
    hub, tel = _site_rig(ra_hours=0.0, dec_deg=90.0, tracking=False,
                         parked=True)
    tel.connected = False
    w, now = _driven(hub)

    sky["alt"] = -2.0
    await _tick_n(w, now, 21)
    assert len(_levels(bus_lines, DAYLIGHT)) == 1, (
        len(_levels(bus_lines, DAYLIGHT)))

    sky["alt"] = -20.0                         # the Sun sets, still blind
    await _tick_n(w, now, 3)
    sky["alt"] = -2.0                          # and rises
    await _tick_n(w, now, 3)
    assert len(_levels(bus_lines, DAYLIGHT)) == 2, (
        "a second dawn in the same outage is a second error")

    tel.connected = True                       # the mount comes back
    await w.tick()
    assert w.state()["blind"] is False, "precondition: the streak ended"
    tel.connected = False                      # an unrelated outage
    await _tick_n(w, now, 3)
    assert len(_levels(bus_lines, DAYLIGHT)) == 3, (
        "a new streak starts its own count and says it once")


async def test_the_daylight_error_never_carries_a_position(
        cfg, pinned_sun, sky, bus_lines):
    """The line carries the configured threshold and no number that locates the
    tube or the site: not the mount's RA or Dec, and not the Sun's altitude
    (a site computation). m14 (the line carries the altitude) fails here."""
    hub, tel, ra, threshold_s = _west_stopped_site_rig(cfg)
    w, now = _driven(hub)
    sky["alt"] = -30.0
    await w.tick()                             # a last good read exists
    tel.position_error = RuntimeError("mount not answering")
    sky["alt"] = -1.75                         # a value no other number equals
    await w.tick()

    line = next(m for _lv, m, _s in bus_lines if DAYLIGHT in m)
    forbidden = {f"{sky['alt']:+.0f}"}         # the altitude, to whole degrees
    for value in (ra, DEC, ra * 15.0, sky["alt"], SITE_LAT, SITE_LON):
        forbidden |= {f"{value:.1f}", f"{value:.2f}", f"{value:+.1f}",
                      f"{value:+.2f}", repr(value)}
    leaked = sorted(f for f in forbidden if f in line)
    assert leaked == [], f"a position or the Sun's altitude is in the line: {leaked}"


async def test_a_noted_park_is_not_a_daylight_hazard(
        cfg, pinned_sun, sky, bus_lines):
    """A park confirmed by another path means the tube is at the park point,
    which is not tracking toward the Sun, so a dark mount behind it is not the
    daylight emergency. The ordinary blind lines still run (the net IS blind).
    m15 (the note does not suppress the line): ``assert ['error'] == []``."""
    hub, tel = _site_rig(ra_hours=0.0, dec_deg=90.0, tracking=False,
                         parked=True)
    w, now = _driven(hub)
    sky["alt"] = -2.0
    await w.tick()
    w.note_parked()
    tel.connected = False

    await _tick_n(w, now, 12)

    assert _levels(bus_lines, DAYLIGHT) == [], "the park is confirmed"
    assert "warning" in _levels(bus_lines, "sun watch held off")


async def test_a_rig_with_no_site_says_nothing_about_daylight(
        cfg, pinned_sun, sky, bus_lines):
    """No site, no Sun altitude: the net is site-independent by design and does
    not invent one. An unconfigured site is a placeholder, exactly as dawn park
    treats it, and the ordinary blind lines still run."""
    hub = SiteHub(is_default=True)
    tel = FakeTel(hub=hub, ra_hours=0.0, dec_deg=90.0, tracking=False,
                  parked=True)
    tel.connected = False
    hub.devices["telescope"] = tel
    w, now = _driven(hub)
    sky["alt"] = -2.0

    await _tick_n(w, now, BLIND_ERROR_AFTER)

    assert _levels(bus_lines, DAYLIGHT) == []
    assert sky["asked"] == [], "an unset site is never turned into a Sun"
    assert "error" in _levels(bus_lines, "sun watch held off")
