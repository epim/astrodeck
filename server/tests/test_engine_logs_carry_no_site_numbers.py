"""The engine's hold, idle-watch and flip-watch lines tell a viewer nothing
about the site (#233, engine half; mosaic slice H3 task T11; H3 orchestrator
ruling 1 (spec, Still waiting on the owner, item 10); spec 5.8, 5.10, 6.9).

WHAT LEAKED. Four lines a viewer reads, through ``/api/logs`` (the ring, or
the night's file, both CAP_VIEW_STATUS) or through the published sequence
state, put a site-derived number beside a named target:

* the target's own floor rule (`_enforce_altitude_floor`): "M31: sank to
  12.4°, below its 30° floor", also the scheduler's "skipped" reason;
* the slew gate (`_altitude_limit_verdict`, raised by `_enforce_mount_floor`):
  "target M42 altitude 22° below safety floor 30° (az 238°)", which a run it
  ends logs ("sequence stopped (unsafe): ...") and publishes as ``detail``;
* the declined flip (`_maybe_meridian_flip`): "at dec +66.1 from this site
  the target's lowest point is 13°", the latitude by subtraction from one
  line, with no clock needed; and the mount's own limit "in 12 min";
* the flip-point wait (`_wait_for_flip_point`): "holding for the meridian
  flip point (4.2 min)", a countdown to a computed crossing.

A named target's altitude or azimuth at a known time is the site (#140, the
#19 class); a countdown to its meridian is the longitude given its RA.

WHAT CHANGED. Each is words at its source. The gate's numbers ride
`SlewRefused.site_detail` (carried for a surface a site-derived capability
gates; no engine surface renders it). Every other number is dropped, since no
such surface carries those lines: the meridian block already gives a holder
the flip countdown, and nothing needs the altitude that sank.

THE SCAN. Each family below is driven through a whole night on the clocked
simulator (test_cloud_hold_watch's `_Watched`, over test_idle_park_hold's
`_Clocked`) under the #19 scanner's two synthetic sites
(`_site_tracking.SITE_A`/``SITE_B``). What a VIEWER is shown is collected:
every ``bus.log`` line, as the ring and the night's file get it, and every
published event as the ``/ws`` lane hands it to a viewer
(`_redact_ws_event` with the viewer principal). A number counts only if it
moves when only the site moves (`_site_tracking.tracking_tokens`), so a
session id, the fake clock's readings and a catalogue number do not.

HOLDING EVERYTHING ELSE STILL, which is the caller's half of that rule:

* One fake clock for both nights, pinned at ``T0`` (both sites dark).
* One timeline: a family's target is placed per site, by RA, so its event
  (a crossing, a flip point) falls at the same fake second at both sites;
  the premise checks the two nights logged and published at the same fake
  times. The per-site RA is never printed on these paths, and a line that
  printed it would read as a leak here, a false alarm rather than a miss.
* One star field: the simulator seeds each frame from the mount's pointing,
  which the per-site RA moves, so frames are rendered at one fixed field.
* No wall-clock racers: the hub's two-second status poller (whose payload
  the WS redaction already strips of the site, and whose cadence is real
  time) and the gallery's thumbnail worker (a thread whose line lands before
  or after the horizon by chance) are off. The frame preview's ``ts`` is left
  out, and not because it is safe: it is the test machine's wall clock, which
  moves between the two nights for no site reason.

A COUNTDOWN CANNOT MOVE ON ITS OWN HERE, and one family says so. For the
flip point the site's longitude and the target's RA enter only through the
hour angle, so holding the timeline still holds the countdown still. The
same RA would put site B's flip point 1.9 h before site A's, further apart
than any exposure window (an hour at most), so no one frame boundary could
open both waits. So the flip-point wait places each site's flip point at its
own offset past the same boundary (``FLIP_AFTER_S``), both inside that
frame's window and neither reached before the horizon: the two nights wait
side by side, and a countdown in the detail would differ.

THE TIMING CHANNEL IS #166's. A line logged at a computed crossing gives the
longitude however it is worded, and every family's marker line is logged at
one. What this file holds is the words.

THE KNOWN POSITIVE, in the same file, because a scan that finds nothing may
be a scan that cannot see: a line carrying the held target's altitude,
logged through the real bus on every look of a hold, is found, and nothing
else is.

THE MUTANTS, each applied to a byte-for-byte backup of engine.py and
restored byte-identical (SHA-256 compared), failures quoted as observed
(``--tb=short``, the synthetic sites' numbers as printed; a line cut short
ends in "..." as the failure message itself cuts it):

Mutant "restore the 'sank to' numbers" (the line and the reason in
`_enforce_altitude_floor` put back to ``f"sank to {alt:.1f}°, below its
{floor:.0f}° ..."``): RED, [hold_own_floor], test_set_aside_copy.py's
floor case and test_altitude_floor.py's sunk-target case -
    AssertionError: a number a viewer is shown moves with the site
    (hold_own_floor): {'a': ['29.5', '29.5'], 'b': ['29.2', '29.2']}
      [a] 29.5: ...Alpha: sank to 29.5°, below its 30° floor — setting it asid...
      [b] 29.2: ...Alpha: sank to 29.2°, below its 30° floor — setting it asid...

Mutant "restore the lower-culmination degrees" (the declined flip's line put
back to "at dec {dec:+.1f} from this site the target's lowest point is
{height:.0f}° above the horizon ..."): RED, [hold_no_flip_needed];
test_meridian_flip_geometry.py stays green under it (26 passed), its
audit-trail cases asking only for the words -
    AssertionError: a number a viewer is shown moves with the site
    (hold_no_flip_needed): {'a': ['40'], 'b': ['11']}
      [a] 40: ...Alpha: no meridian flip needed — at dec +89.0 from this site the target's lowest point is 40° above the horizon, so the tube never s...
      [b] 11: ...Alpha: no meridian flip needed — at dec +89.0 from this site the target's lowest point is 11° above the horizon, so the tube never s...

Mutant "restore the flip-point minutes" (the wait's detail put back to
``f"holding for the meridian flip point ({remaining_s / 60.0:.1f} min)"``):
RED, [flip_point_wait] (abridged: 24 moving numbers at each site, and one
located line of each site's 20) -
    AssertionError: a number a viewer is shown moves with the site
    (flip_point_wait): {'a': ['4.8', '4.8', '4.9', ... '6.6', '6.7'], 'b':
    ['2.3', '2.3', '2.4', ... '4.1', '4.2']}
      [a] 4.8: ...{"data": {"detail": "holding for the meridian flip point (4.8 min)", "live": {"sensor_temp_c": 12.3},...
      [b] 2.3: ...{"data": {"detail": "holding for the meridian flip point (2.3 min)", "live": {"sensor_temp_c": 12.3},...

Mutant "restore the gate's altitude and azimuth" (the floor and ceiling
sentences of `_altitude_limit_verdict` put back to the numeric ones, which
are also its ``site_detail``): RED, [slew_gate_refusal],
`test_the_gate_keeps_its_numbers_on_site_detail`,
test_the_mount_floor_needs_a_site.py's configured-site case and
test_hold_repoint_refusals_keep_holding.py's type case -
    AssertionError: a number a viewer is shown moves with the site
    (slew_gate_refusal): {'a': ['19', '19', '206', '206'], 'b': ['235',
    '235', '24', '24']}
      [a] 19: ...sequence stopped (unsafe): target Alpha altitude 19° below safety floor 30° (az 206°)...
      [a] 206: ...sequence stopped (unsafe): target Alpha altitude 19° below safety floor 30° (az 206°)...
      [b] 235: ...sequence stopped (unsafe): target Alpha altitude 24° below safety floor 30° (az 235°)...
      [b] 24: ...sequence stopped (unsafe): target Alpha altitude 24° below safety floor 30° (az 235°)...

The ceiling half of that sentence is graded by its own family,
[slew_gate_ceiling], and `test_the_gate_keeps_its_ceiling_numbers_on_site_detail`,
both added by T11's verifier: the mutant above goes RED on the floor alone,
and the ceiling sentence restored to its numbers by itself passed every
test in this file and in the hold, idle, keep-out and refusal files
(observed: 138 passed). Each now goes RED under it; see there.

The families with no mutant of their own (the mount floor, the keep-out
and the three idle park-holds) were words before this task. They are here
so a number added to any of those lines later is caught the same way.

SAME-CLASS LINES OUTSIDE THIS SCOPE are recorded on #166 and #233 and not
fixed here: the sequence state's ``live.meridian_eta_s`` (the hours-to-flip
the meridian block's redaction strips, re-published in seconds), the waiting
``schedule`` block, ``_missed_start``'s "start window opened N min ago" and
the dawn stop's timing.
"""
from __future__ import annotations

import contextlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck import events
from astrodeck.api.redact import _redact_ws_event
from astrodeck.auth import principal_for_role
from astrodeck.catalog import altaz
from astrodeck.config import AppConfig, ConfigStore, SafetyConfig, Site
from astrodeck.devices.sim import SimCamera, SimTelescope
from astrodeck.hub import Hub
from astrodeck.sequence import (ExposureStep, SequenceEngine, SequencePlan,
                                Target, schedule)
from astrodeck.sequence.engine import SafetyAbort, _frame_altitude

from _site_tracking import (SITE_A, SITE_B, any_tracking, numeric_tokens,
                            site, tracking_tokens)
from test_cloud_hold_watch import EXP, LEAD_S, _Watched
from test_idle_park_hold import _Clock

#: 2026-09-25T04:30:00Z: well after dusk at both synthetic sites. Nothing on
#: these paths gates on darkness; pinned so that it could not matter.
T0 = 1_790_310_600.0
SITES = (("a", SITE_A), ("b", SITE_B))
#: A scripted sky that never closes.
NEVER = 1.0e9
#: Where every simulated frame is rendered (`_rig`): the simulator's own
#: default pointing.
FIELD = (5.59, -5.39)
VIEWER = principal_for_role("viewer")


# ---------------------------------------------------------------- harness

@dataclass
class _Seen:
    """What one night showed. Each entry carries the fake time it happened
    at, or None before the harness's clock was installed."""

    lines: list = field(default_factory=list)
    published: list = field(default_factory=list)

    @staticmethod
    def _now():
        return getattr(engine_mod.time, "t", None)

    def log(self, level, message, source="hub"):
        self.lines.append((self._now(), level, message, source))

    def publisher(self, real):
        def publish(type, **data):
            if type != "log":
                self.published.append((self._now(), type, json.loads(
                    json.dumps(data, default=str))))
            return real(type, **data)
        return publish

    def texts(self) -> list[str]:
        """Every line, and every published event as a viewer's ``/ws``
        receives it (an event the lane drops for a viewer is not shown)."""
        out = [m for _t, _l, m, _s in self.lines]
        for _t, ty, data in self.published:
            if ty == "preview":
                data = {k: v for k, v in data.items() if k != "ts"}
            ev = _redact_ws_event({"type": ty, "data": data}, VIEWER)
            if ev is not None:
                out.append(json.dumps(ev, sort_keys=True))
        return out

    def marked(self, marker: str) -> list:
        """The fake times of every line, or published sequence detail, that
        says ``marker``."""
        said = [t for t, _l, m, _s in self.lines if marker in m]
        said += [t for t, ty, data in self.published
                 if ty == "sequence" and marker in (data.get("detail") or "")]
        return sorted(said)

    def timeline(self):
        return ([(t, lvl, src) for t, lvl, _m, src in self.lines],
                [(t, ty) for t, ty, _d in self.published])


@dataclass
class _Rig:
    hub: Hub
    store: ConfigStore
    mp: pytest.MonkeyPatch
    seen: _Seen
    lat: float
    lon: float


@contextlib.asynccontextmanager
async def _rig(pair, root):
    """A private store, capture root and simulator hub at one synthetic site,
    with everything this file patches undone on the way out."""
    lat, lon = pair
    with pytest.MonkeyPatch.context() as mp:
        store = ConfigStore(path=root / "astrodeck.json")
        store.set_site(Site(name="synthetic", latitude=lat, longitude=lon,
                            is_default=False))
        store.set_safety(SafetyConfig(enabled=False))
        for mod in (config_mod, hub_module, engine_mod):
            mp.setattr(mod, "config_store", store)
        # Sessions live under the capture root, so each night has its own
        # and neither disarms the other's.
        mp.setattr(hub_module, "CAPTURE_DIR", root / "captures")
        mp.setattr(Hub, "_check_solar",
                   lambda self, ra, dec, *, force=False: None)
        mp.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
        # The two wall-clock racers (see the module docstring).
        mp.setattr(Hub, "_enqueue_thumb", lambda self, path: None)
        mp.setattr(Hub, "ensure_status_poller",
                   lambda self: self.ensure_safety_poller())
        # A THIRD RACER, ONCE HERE: THE HOP'S WALL TIME (#299). The engine
        # prices each completed setup into the ETA's hop term from
        # ``time.monotonic()`` (`_setup_target`, #189 U-07). Until WP-40,
        # test_idle_park_hold's `_Clock` faked only ``time()``, so the
        # sample was the machine's real clock: 0 (dropped, the 150 s seed
        # stays) when the setup finished inside one tick of Windows' 15.6 ms
        # monotonic clock, one tick (0.0156 s, priced as about nothing) when
        # it crossed one. Which happened at which site was chance, and the
        # ETA's ``events_cost_s`` and ``eta_s`` then differed by 150 between
        # the two nights, read as a number that moves with the site. S2's
        # longer hop made the crossing likely enough to see: before a fix
        # the file failed about one run in five (observed: 1 of 8, 2 of 5,
        # 1 of 6 and one full-suite run, each on a different family), after
        # it 10 of 10 passed. Observed, verbatim:
        #     AssertionError: a number a viewer is shown moves with the site
        #     (idle_flip_point): {'a': ['0', '0', '1680', '1722'], 'b':
        #     ['150', '150', '1830', '1872']}
        # `_Clock.monotonic` now reads the fake clock at its source (WP-40,
        # the "still open" item #299's diagnosis named: the other thirteen
        # `_Clocked` files share the same racer), as
        # tests/_group_harness.py's clock already did, so no patch is
        # needed here any more.
        real_render = SimCamera._render

        def render(self, *a, **k):
            # Synchronous, on the loop's thread: nothing reads the rig's
            # pointing between the swap and the restore.
            rig = self.rig
            kept = (rig.ra_hours, rig.dec_deg)
            rig.ra_hours, rig.dec_deg = FIELD
            try:
                return real_render(self, *a, **k)
            finally:
                rig.ra_hours, rig.dec_deg = kept

        mp.setattr(SimCamera, "_render", render)
        seen = _Seen()
        mp.setattr(events.bus, "log", seen.log)
        mp.setattr(events.bus, "publish", seen.publisher(events.bus.publish))
        hub = Hub()
        await hub.connect_sim()
        try:
            yield _Rig(hub, store, mp, seen, lat, lon)
        finally:
            await hub.disconnect_all()


def _lst_h(t, lon):
    return schedule.hour_angle_h(0.0, lon, t) % 24.0


def _ra_at(ha_h, t, lon):
    """The RA whose hour angle is ``ha_h`` at ``t`` (HA = LST - RA)."""
    return (_lst_h(t, lon) - ha_h) % 24.0


def _crossing_ra(alt_deg, dec, t, lat, lon, *, rising):
    """The RA whose altitude at (lat, lon) passes ``alt_deg`` at ``t``."""
    la, d = math.radians(lat), math.radians(dec)
    cos_h = ((math.sin(math.radians(alt_deg)) - math.sin(la) * math.sin(d))
             / (math.cos(la) * math.cos(d)))
    ha_h = math.degrees(math.acos(cos_h)) / 15.0
    return _ra_at(-ha_h if rising else ha_h, t, lon)


def _flip_ra(t_flip, lon):
    """The RA that reaches the plan's flip point (the default lead before
    transit) at ``t_flip``."""
    return (_lst_h(t_flip, lon) + LEAD_S / 3600.0) % 24.0


def _tgt(name, ra, dec, *, exposure_s=EXP, count=40, **sched):
    """Fixed ids, so neither night mints one of its own."""
    slug = name.lower()
    t = Target(id=f"t-{slug}", name=name, ra_hours=ra, dec_deg=dec,
               center=False, autofocus_first=False,
               steps=[ExposureStep(id=f"s-{slug}", filter="L",
                                   exposure_s=exposure_s, gain=100,
                                   count=count)])
    for k, v in sched.items():
        setattr(t.schedule, k, v)
    return t


def _waiter(t0, lon):
    """A second target held two hours by its hour-angle constraint, so the
    night goes on, waiting, after the first is set aside or done."""
    return _tgt("Bravo", _ra_at(-3.0, t0, lon), 40.0, max_hour_angle_h=1.0)


def _plan(*targets, flip=False, darks=0):
    return SequencePlan(name="leak", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=flip,
                        safety_check=True, cloud_hold_darks=darks,
                        targets=list(targets))


async def _night(w, plan, **kw):
    try:
        await w.night(plan, **kw)
    finally:
        await w.close()


# ------------------------------------------------------------ the families
#
# Each drives one night at the rig's site and answers what the premise needs:
# ``marker``, words every night of the family must show, and ``power``, for
# a family a named mutant grades, the site-derived value that mutant prints,
# as it would print it. The premise asks the two sites' powers to differ, or
# the mutant would print the same number twice and the scan could not see it.

async def _hold_mount_floor(rig, tag):
    """A cloud hold whose target sets through the mount's 30 degree floor
    250 s in: the hold sets it aside with tracking stopped."""
    t_cross_s = EXP + 250.0
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=t_cross_s + 150.0,
                 clears_at_s=t_cross_s - 15.0, safety={"min_alt_deg": 30.0},
                 t0=T0)
    a = _tgt("Alpha", _crossing_ra(30.0, 20.0, T0 + t_cross_s, rig.lat,
                                   rig.lon, rising=False), 20.0)
    await _night(w, _plan(a, _waiter(T0, rig.lon)))
    return {"marker": "reaches the mount's altitude floor during the cloud "
                      "hold"}


async def _hold_own_floor(rig, tag):
    """The target's own on_floor rule inside a hold. Alpha sinks through its
    30 degree floor 100 s into a 300 s hold dark, and the look after the dark
    sets it aside: a gap long enough that the altitude it has sunk to by then
    differs between the sites at the tenth of a degree the old line printed
    (the premise's power)."""
    exp = 300.0
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=2 * exp + 100.0,
                 t0=T0)
    a = _tgt("Alpha", _crossing_ra(30.0, 0.0, T0 + exp + 100.0, rig.lat,
                                   rig.lon, rising=False), 0.0,
             exposure_s=exp, min_altitude_deg=30.0, on_floor="advance")
    await _night(w, _plan(a, _waiter(T0, rig.lon), darks=1))
    # Words the line has had before #233 and since, so the premise holds
    # under the mutant that puts the numbers back.
    marker = "Alpha: sank"
    said = rig.seen.marked(marker)
    alt = (_frame_altitude(a, site((rig.lat, rig.lon)), said[0])
           if said else None)
    return {"marker": marker,
            "power": None if alt is None else f"{alt:.1f}"}


async def _hold_keep_out(rig, tag):
    """A cloud hold whose target rises into the mount's 70 degree zenith
    keep-out 230 s in: the hold stops tracking."""
    t_cross_s = EXP + 230.0
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=t_cross_s + 300.0,
                 safety={"max_alt_deg": 70.0}, t0=T0)
    # Dec 28 culminates above 70 degrees at both sites.
    a = _tgt("Alpha", _crossing_ra(70.0, 28.0, T0 + t_cross_s, rig.lat,
                                   rig.lon, rising=True), 28.0)
    await _night(w, _plan(a))
    return {"marker": "is rising into the mount's zenith keep-out"}


async def _hold_no_flip_needed(rig, tag):
    """The hold's flip watch, through a flip point, for a target whose
    declination needs no flip.

    THE DECLINE IS FORCED BY GEOMETRY, as test_meridian_flip_geometry's
    audit-trail cases force it by hand: `schedule.flip_can_be_skipped`
    answers False for every mount a driver can describe today, so the branch
    is reachable only for a mount known not to be a GEM. It is made to
    answer as `flip_unnecessary_over_pole`, which at dec 89 is yes at both
    sites (lowest points 40 and 11 degrees against the 10 degree clearance).

    THE HOLD OPENS BEFORE THE FIRST FRAME (the sky is shut before the setup),
    so the flip latch is still armed when the hold's first look reaches the
    flip gate: the frame loop's gate, had a frame come first, would have
    declined it there. The flip point comes 200 s in, inside the night.
    """
    dec = 89.0
    rig.mp.setattr(schedule, "flip_can_be_skipped",
                   lambda d, lat, side, *a, **k:
                   schedule.flip_unnecessary_over_pole(d, lat))
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=400.0,
                 closes_at_s=-1.0, t0=T0)
    a = _tgt("Alpha", _flip_ra(T0 + 200.0, rig.lon), dec)
    await _night(w, _plan(a, flip=True))
    return {"marker": "no meridian flip needed",
            "power": f"{schedule.lower_culmination_deg(dec, rig.lat):.0f}"}


#: Where each site's flip point falls after the first frame boundary: inside
#: that frame's window (a 600 s exposure's, plus the gate's margin) and after
#: the horizon, 120 s past the boundary. See the module docstring for why
#: the two differ.
FLIP_AFTER_S = {"a": 400.0, "b": 250.0}


async def _flip_point_wait(rig, tag):
    """The frame loop's flip-point wait: the flip point falls inside the next
    frame's window, so the run waits for it, publishing its detail, until
    the horizon."""
    exp = 600.0
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=exp + 120.0,
                 closes_at_s=NEVER, t0=T0)
    a = _tgt("Alpha", _flip_ra(T0 + exp + FLIP_AFTER_S[tag], rig.lon), 20.0,
             exposure_s=exp)
    await _night(w, _plan(a, flip=True))
    return {"marker": "holding for the meridian flip point",
            "power": f"{FLIP_AFTER_S[tag] / 60.0:.1f}"}


async def _idle_floor(rig, tag):
    """The idle park-hold for the floor: Alpha, done after one frame, sets
    through the mount's floor 40 s into the next target's wait."""
    exp = 150.0
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=exp + 100.0,
                 closes_at_s=NEVER, safety={"min_alt_deg": 30.0}, t0=T0)
    a = _tgt("Alpha", _crossing_ra(30.0, 0.0, T0 + exp + 40.0, rig.lat,
                                   rig.lon, rising=False), 0.0,
             exposure_s=exp, count=1)
    await _night(w, _plan(a, _waiter(T0, rig.lon)))
    return {"marker": "has sunk below the mount's altitude floor"}


async def _idle_keep_out(rig, tag):
    """The idle park-hold for the keep-out: Alpha rises into it 40 s into
    the wait."""
    exp = 150.0
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=exp + 100.0,
                 closes_at_s=NEVER, safety={"max_alt_deg": 70.0}, t0=T0)
    a = _tgt("Alpha", _crossing_ra(70.0, 28.0, T0 + exp + 40.0, rig.lat,
                                   rig.lon, rising=True), 28.0,
             exposure_s=exp, count=1)
    await _night(w, _plan(a, _waiter(T0, rig.lon)))
    return {"marker": "has risen into the mount's zenith keep-out"}


async def _idle_flip_point(rig, tag):
    """The idle park-hold for the flip point: Alpha reaches it 75 s after
    its one frame, outside the frame loop's own window, so the wait's watch
    is what meets it (test_idle_park_hold's `_flip_run`)."""
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=EXP + 200.0,
                 closes_at_s=NEVER, t0=T0)
    a = _tgt("Alpha", _flip_ra(T0 + EXP + 75.0, rig.lon), 20.0, count=1)
    await _night(w, _plan(a, _waiter(T0, rig.lon), flip=True))
    return {"marker": "has reached its meridian flip point"}


#: The slew-gate refusal's target: ONE place on the sky for both sites, and
#: one clock reading, the first `_gate_when` finds.
GATE_RA, GATE_DEC = 2.0, -25.0


def _gate_when() -> float:
    """A clock reading at which the gate's target is between 5 and 25
    degrees at both sites, across the gate's slew projection, with a
    different whole-degree altitude and azimuth at each: what the gate's old
    sentence printed. Searched rather than hardcoded, so the premise is
    computed, not assumed (test_resume_arm_hold_is_site_free's ``_when``)."""
    for i in range(2 * 24 * 12):
        t = T0 + i * 300.0
        worst = {}
        for tag, (lat, lon) in SITES:
            worst[tag] = min(
                altaz(GATE_RA, GATE_DEC, lat, lon, t),
                altaz(GATE_RA, GATE_DEC, lat, lon,
                      t + engine_mod.SLEW_PROJECT_S))
        if (all(5.0 <= float(w[0]) <= 25.0 for w in worst.values())
                and f"{worst['a'][0]:.0f}" != f"{worst['b'][0]:.0f}"
                and f"{worst['a'][1]:.0f}" != f"{worst['b'][1]:.0f}"):
            return t
    raise AssertionError("no clock reading in two days puts the gate's "
                         "target under the floor at both synthetic sites")


def _assert_stopped_on(rig, words: str) -> None:
    """Premise for a gate family: the run ended unsafe on the refusal the
    family is about. The marker ("sequence stopped (unsafe)") is met by any
    unsafe end, and a family ended by another limit would grade nothing
    about its own. ``words`` are in the gate's sentence before #233 and
    since, so the premise holds under the mutant that restores the numbers.

    Added by T11's verifier. Control "the ceiling refuses as a floor" (the
    ceiling branch of `_altitude_limit_verdict` returns a floor verdict and
    the floor's words): RED in [slew_gate_ceiling] (observed) -
        AssertionError: premise: the run ended unsafe on the refusal this
        family is about ('zenith keep-out'): ["sequence stopped (unsafe):
        Alpha is below the mount's altitude floor"]
    """
    stops = [m for _t, _l, m, _s in rig.seen.lines
             if "sequence stopped (unsafe)" in m]
    assert stops and all(words in m for m in stops), (
        f"premise: the run ended unsafe on the refusal this family is about "
        f"({words!r}): {stops}")


async def _slew_gate_refusal(rig, tag):
    """The slew gate refuses the first target's setup slew: below the
    mount's 30 degree floor. A SafetyAbort, so the run ends unsafe, logs the
    refusal and publishes it as its detail. The plan and the clock are the
    same at both sites; only the site moves."""
    t0 = _gate_when()
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=600.0,
                 closes_at_s=NEVER, safety={"min_alt_deg": 30.0}, t0=t0)
    await _night(w, _plan(_tgt("Alpha", GATE_RA, GATE_DEC)),
                 to_the_horizon=False)
    _assert_stopped_on(rig, "floor")
    alt, az = min(altaz(GATE_RA, GATE_DEC, rig.lat, rig.lon, t0),
                  altaz(GATE_RA, GATE_DEC, rig.lat, rig.lon,
                        t0 + engine_mod.SLEW_PROJECT_S))
    # The run's end, which the refusal's message follows whatever it says.
    return {"marker": "sequence stopped (unsafe)",
            "power": f"{alt:.0f} {az:.0f}"}


#: The zenith keep-out refusal's target and ceiling. Dec 25 culminates near
#: 74 degrees at site A and 77 at site B, and a 60 degree ceiling is crossed
#: about two hours either side of each site's transit, so the two sites'
#: windows (their transits 1.9 h apart) overlap by about two hours.
CEIL_RA, CEIL_DEC, CEILING = 2.0, 25.0, 60.0


def _ceiling_best(lat, lon, t):
    """The gate's ceiling judgement: the higher of now and the slew
    projection (`_altitude_limit_verdict`), as (alt, az)."""
    return max(altaz(CEIL_RA, CEIL_DEC, lat, lon, t),
               altaz(CEIL_RA, CEIL_DEC, lat, lon,
                     t + engine_mod.SLEW_PROJECT_S))


def _ceiling_when() -> float:
    """A clock reading at which the keep-out target is above ``CEILING``
    at both sites, across the slew projection, with a different whole-degree
    altitude and azimuth at each: what the gate's old ceiling sentence
    printed. Searched, as `_gate_when` is."""
    for i in range(2 * 24 * 12):
        t = T0 + i * 300.0
        best = {tag: _ceiling_best(lat, lon, t)
                for tag, (lat, lon) in SITES}
        if (all(float(b[0]) >= CEILING + 3.0 for b in best.values())
                and f"{best['a'][0]:.0f}" != f"{best['b'][0]:.0f}"
                and f"{best['a'][1]:.0f}" != f"{best['b'][1]:.0f}"):
            return t
    raise AssertionError("no clock reading in two days puts the keep-out "
                         "target above the ceiling at both synthetic sites")


async def _slew_gate_ceiling(rig, tag):
    """The slew gate refuses the first target's setup slew: inside the
    mount's zenith keep-out. The ceiling half of the gate's sentence, which
    `_slew_gate_refusal` (the floor) does not reach. Added by T11's verifier:
    the ceiling sentence restored to its numbers alone passed every test in
    this file and in the hold, idle, keep-out and refusal files (observed:
    138 passed).

    Mutant "restore the gate's ceiling numbers" (the ceiling sentence of
    `_altitude_limit_verdict` put back to its numeric ``site_detail``): RED
    (observed) -
        AssertionError: a number a viewer is shown moves with the site
        (slew_gate_ceiling): {'a': ['120', '120', '63', '63'], 'b': ['351',
        '351', '77', '77']}
          [a] 120: ...sequence stopped (unsafe): target Alpha altitude 63°
          above the zenith keep-out 60° (az 120°) — the mount can reach its
          own tripod ...
          [b] 351: ...sequence stopped (unsafe): target Alpha altitude 77°
          above the zenith keep-out 60° (az 351°) — the mount can reach its
          own tripod ...
    """
    t0 = _ceiling_when()
    w = _Watched(rig.hub, rig.store, rig.mp, horizon_s=600.0,
                 closes_at_s=NEVER, safety={"max_alt_deg": CEILING}, t0=t0)
    await _night(w, _plan(_tgt("Alpha", CEIL_RA, CEIL_DEC)),
                 to_the_horizon=False)
    _assert_stopped_on(rig, "zenith keep-out")
    alt, az = _ceiling_best(rig.lat, rig.lon, t0)
    return {"marker": "sequence stopped (unsafe)",
            "power": f"{alt:.0f} {az:.0f}"}


FAMILIES = {
    "hold_mount_floor": _hold_mount_floor,
    "hold_own_floor": _hold_own_floor,
    "hold_keep_out": _hold_keep_out,
    "hold_no_flip_needed": _hold_no_flip_needed,
    "flip_point_wait": _flip_point_wait,
    "idle_floor": _idle_floor,
    "idle_keep_out": _idle_keep_out,
    "idle_flip_point": _idle_flip_point,
    "slew_gate_refusal": _slew_gate_refusal,
    "slew_gate_ceiling": _slew_gate_ceiling,
}


async def _both(family, root, wrap=None):
    """The family's night at each site: ``{tag: (seen, facts)}``. ``wrap``
    runs on each rig before the night, for the known positive."""
    out = {}
    for tag, pair in SITES:
        async with _rig(pair, root / f"site_{tag}") as rig:
            if wrap is not None:
                wrap(rig)
            facts = await FAMILIES[family](rig, tag)
        out[tag] = (rig.seen, facts)
    return out


def _where(found, runs) -> list[str]:
    """Where each moving number was shown: the first text at its site that
    the other site did not show word for word and that carries it, around
    the number. Synthetic sites only, so printing is safe."""
    out = []
    for tag, other in (("a", "b"), ("b", "a")):
        theirs = Counter(runs[other][0].texts())
        own = []
        for txt in runs[tag][0].texts():
            if theirs[txt]:
                theirs[txt] -= 1
            else:
                own.append(txt)
        for tok in sorted(set(found[tag])):
            pat = re.compile(r"(?<![\w.])" + re.escape(tok) + r"(?![\w])")
            for txt in own:
                m = pat.search(txt)
                if m:
                    around = txt[max(0, m.start() - 90):m.end() + 40]
                    out.append(f"[{tag}] {tok}: ...{around}...")
                    break
    return out


def _stray_detail(tag: str, stray: list, runs) -> str:
    """The failure detail for a stray number the control line did not carry
    (#299 point 1): the numbers themselves, and ``_where`` finds each one
    for ``tag`` alone, exactly as the tracking assertion above already names
    a line for the family scan. Before this the control's own assertion
    printed only the bare numbers, and the file's one real occurrence (the
    issue) had its failure text lost to a run's truncated capture, with
    nowhere for the next one to point."""
    other = "b" if tag == "a" else "a"
    only = {tag: stray, other: []}
    return f"{stray}\n" + "\n".join(_where(only, runs))


# ------------------------------------------------------------------ the scan

@pytest.mark.parametrize("family", list(FAMILIES))
async def test_no_number_a_viewer_is_shown_moves_with_the_site(family,
                                                               tmp_path):
    """Each family's night at site A and at site B: the same lines and the
    same published states at the same fake times, and not one number among
    them that moves with the site."""
    runs = await _both(family, tmp_path)
    (seen_a, facts_a), (seen_b, facts_b) = runs["a"], runs["b"]
    marker = facts_a["marker"]
    at_a, at_b = seen_a.marked(marker), seen_b.marked(marker)
    assert at_a and at_a == at_b, (
        f"premise: both nights reach the family's event ({marker!r}) at the "
        f"same fake times: {at_a} / {at_b}")
    assert seen_a.timeline() == seen_b.timeline(), (
        "premise: the two nights logged and published at the same fake "
        "times, so only what was said can differ")
    if "power" in facts_a:
        assert facts_a["power"] != facts_b["power"], (
            f"premise: the value this family's mutant would print differs "
            f"between the sites: {facts_a['power']!r} / {facts_b['power']!r}")
    found = tracking_tokens(seen_a.texts(), seen_b.texts())
    assert not any_tracking(found), (
        f"a number a viewer is shown moves with the site ({family}): "
        f"{found}\n" + "\n".join(_where(found, runs)))


async def test_control_the_scan_sees_a_number_that_does_move(tmp_path):
    """THE KNOWN POSITIVE. The own-floor hold again, with one line added: on
    every look of the hold, the held target's live altitude, logged through
    the real bus as the old "sank to" line did. The scan finds numbers, and
    every number it finds is one of that line's: the harness sees a
    site-derived number when one is shown, and shows nothing else that
    moves.

    Mutant "the scan compares one site with itself" (``_both`` drives
    ``SITE_A`` twice): RED (observed) -
        AssertionError: the scan found nothing moving, though a line
        carried the held target's altitude under each site
    """
    real_floor = SequenceEngine._enforce_altitude_floor

    def wrap(rig):
        async def enforce(self, target):
            alt = _frame_altitude(target, self.hub.site,
                                  engine_mod.time.time())
            if alt is not None:
                events.bus.log("info", f"control: {target.name} at "
                                       f"{alt:.1f}", "control")
            return await real_floor(self, target)

        rig.mp.setattr(SequenceEngine, "_enforce_altitude_floor", enforce)

    runs = await _both("hold_own_floor", tmp_path, wrap=wrap)
    found = tracking_tokens(runs["a"][0].texts(), runs["b"][0].texts())
    assert any_tracking(found), (
        "the scan found nothing moving, though a line carried the held "
        "target's altitude under each site")
    for tag in ("a", "b"):
        control = numeric_tokens("\n".join(
            m for _t, _l, m, src in runs[tag][0].lines if src == "control"))
        stray = sorted(set(found[tag]) - set(control))
        assert not stray, (
            f"[{tag}] a number moved that the control line did not carry: "
            + _stray_detail(tag, stray, runs))


def test_a_stray_number_report_names_the_line():
    """The control's stray-number failure must say WHERE a number was
    shown, as the family scan's own failure already does (#299 point 1):
    this file's one real occurrence had its failure text lost to a run's
    truncated capture, and a report that gives only the bare numbers leaves
    the next occurrence exactly as blind. No night needed: two one-line
    ``_Seen`` records are enough to grade the report's shape.

    Mutant "report only the numbers" (`_stray_detail` returns ``str(stray)``
    alone, the ``_where`` call dropped): RED (observed) -
        AssertionError: assert '[a] 12.4:' in "['12.4']"
    """
    seen_a = _Seen(lines=[(12.0, "warning",
                          "Alpha: sank to 12.4, below its 30 floor",
                          "sequence")])
    seen_b = _Seen(lines=[(12.0, "warning",
                          "Alpha: sank to 24.0, below its 30 floor",
                          "sequence")])
    runs = {"a": (seen_a, {}), "b": (seen_b, {})}
    detail = _stray_detail("a", ["12.4"], runs)
    assert "[a] 12.4:" in detail and "sank to 12.4" in detail, detail


# ---------------------------------------- the two lines no night reaches

async def test_the_mount_limit_line_carries_no_minutes(tmp_path):
    """The flip gate's other line: a mount that reports its own meridian
    limit close, for a target whose tube would clear the pier. The minutes
    came from the mount, which counts them from where it stands, and no
    simulated mount reports any, so no night above reaches this line. The
    mount's answer is a fixed 0.2 h here, which is no site at all, so the
    rule is graded as it stands: no number in the line.

    Mutant "restore the mount-limit minutes" (the line put back to "...
    reports its own meridian limit in {dev * 60:.0f} min ..."): RED
    (observed) -
        AssertionError: the mount-limit line carries a number: 'Alpha: the
        tube would clear the pier, but the mount reports its own meridian
        limit in 12 min — flipping anyway, because it stops tracking at that
        limit whatever the geometry says'
    """
    async with _rig(SITE_A, tmp_path) as rig:
        e = SequenceEngine(rig.hub)
        e.plan = SequencePlan(name="limit", meridian_flip=True, targets=[])
        e._flip_armed = True
        now = time.time()
        # Three hours before its flip point, and so high (dec 89) that its
        # lowest point clears the horizon here: only the mount's own limit
        # makes this flip.
        a = _tgt("Alpha", _ra_at(-3.0, now, rig.lon), 89.0)
        tel = rig.hub.devices["telescope"]

        async def limit_close():
            return 0.2

        rig.mp.setattr(tel, "time_to_meridian_flip", limit_close)
        assert schedule.flip_unnecessary_over_pole(a.dec_deg, rig.lat), (
            "premise: the tube clears the pier")
        await e._maybe_meridian_flip(a, 0.0)
        said = [m for _t, _l, m, _s in rig.seen.lines
                if "its own meridian limit" in m]
    assert len(said) == 1, f"premise: the line was said once: {said}"
    assert not numeric_tokens(said[0]), (
        f"the mount-limit line carries a number: {said[0]!r}")


async def test_the_gate_keeps_its_numbers_on_site_detail(tmp_path):
    """The slew gate's refusal, asked directly at one instant: the message
    is words, and the altitude, the floor and the azimuth it judged are all
    on ``site_detail``, the one place a surface gated on the site-derived
    capability could take them from (`SlewRefused`).

    Mutant "restore the gate's altitude and azimuth" (the floor verdict's
    sentence made its numeric ``site_detail``): RED (observed) -
        AssertionError: the refusal's message is not words: 'target Alpha
        altitude 19° below safety floor 30° (az 206°)'
    Mutant "site_detail dropped" (`_enforce_mount_floor` passes
    ``site_detail=None``): RED here, and in test_idle_park_hold.py's
    one-predicate case, test_the_mount_floor_needs_a_site.py's
    configured-site case and test_hold_repoint_refusals_keep_holding.py's
    type case (observed) -
        AssertionError: site_detail does not carry 'altitude 19°': ''
    """
    t0 = _gate_when()
    async with _rig(SITE_A, tmp_path) as rig:
        rig.mp.setattr(engine_mod, "time", _Clock(time, t0))
        e = SequenceEngine(rig.hub)
        e.plan = SequencePlan(name="gate", meridian_flip=False, targets=[])
        e._cfg = AppConfig(safety=SafetyConfig(enabled=False,
                                               min_alt_deg=30.0))
        a = _tgt("Alpha", GATE_RA, GATE_DEC)
        with pytest.raises(SafetyAbort) as refused:
            await e._enforce_mount_floor(projected=True, target=a)
    alt, az = min(altaz(GATE_RA, GATE_DEC, *SITE_A, t0),
                  altaz(GATE_RA, GATE_DEC, *SITE_A,
                        t0 + engine_mod.SLEW_PROJECT_S))
    message = str(refused.value)
    assert message.startswith("slew refused:") and not numeric_tokens(
        message), f"the refusal's message is not words: {message!r}"
    detail = refused.value.site_detail or ""
    for piece in (f"altitude {alt:.0f}°", "safety floor 30°",
                  f"(az {az:.0f}°)"):
        assert piece in detail, (
            f"site_detail does not carry {piece!r}: {detail!r}")


async def test_the_gate_keeps_its_ceiling_numbers_on_site_detail(tmp_path):
    """The same for the zenith keep-out: the ceiling refusal's message is
    words, and the altitude, the ceiling and the azimuth are on
    ``site_detail``. Added by T11's verifier with the `slew_gate_ceiling`
    family: nothing graded the ceiling sentence before.

    Mutant "restore the gate's ceiling numbers" (the ceiling verdict's
    sentence made its numeric ``site_detail`` again, in
    `_altitude_limit_verdict`): RED here and in [slew_gate_ceiling]
    (observed) -
        AssertionError: the ceiling refusal's message is not words: 'target
        Alpha altitude 63° above the zenith keep-out 60° (az 120°) — the
        mount can reach its own tripod up there'
    """
    t0 = _ceiling_when()
    async with _rig(SITE_A, tmp_path) as rig:
        rig.mp.setattr(engine_mod, "time", _Clock(time, t0))
        e = SequenceEngine(rig.hub)
        e.plan = SequencePlan(name="gate", meridian_flip=False, targets=[])
        e._cfg = AppConfig(safety=SafetyConfig(enabled=False,
                                               max_alt_deg=CEILING))
        a = _tgt("Alpha", CEIL_RA, CEIL_DEC)
        with pytest.raises(SafetyAbort) as refused:
            await e._enforce_mount_floor(projected=True, target=a)
    alt, az = _ceiling_best(*SITE_A, t0)
    message = str(refused.value)
    assert message.startswith("slew refused:") and not numeric_tokens(
        message), f"the ceiling refusal's message is not words: {message!r}"
    assert "zenith keep-out" in message, message
    detail = refused.value.site_detail or ""
    for piece in (f"altitude {alt:.0f}°", f"zenith keep-out {CEILING:.0f}°",
                  f"(az {az:.0f}°)"):
        assert piece in detail, (
            f"site_detail does not carry {piece!r}: {detail!r}")
