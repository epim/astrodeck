"""#204: a re-lock is measured against the LOCK, not against the brightest star.

On the M45 2x2 mosaic (night of 2026-09-23/24) the native guider stopped
guiding at least five times on its own different-star guard, each time after a
star loss, reading re-locks of 1733 to 4513 arcsec. At 5.5 arcsec/px 4513
arcsec is about 820 px, half the guide frame: a jump between two bright stars,
not a drift the engine's search box could produce. The host found the re-lock
position with a brightest-first ``guide_star_find`` over the whole frame and
took ``stars[0]``, then measured it against the previous brightest pick. In the
Pleiades the brightest stars are several near-equal, often saturated, ones, and
a cloud or a saturation cut that reorders them reads as a re-lock of hundreds
of px.

So the re-lock position is now the star NEAREST the previous lock within the
engine's own search radius (``config["search_region"]``, default 15 px). No
star inside it is a LOST frame: nothing recorded or judged, the baseline and
the armed watch both stay. And so that "lost" cannot make the guard inert,
``RELOCK_UNCONFIRMED_FRAMES`` consecutive lost frames while the engine keeps
guiding stop the guider through the honest-death path, with a reason that says
no star was found near the lock, never "moved N arcsec".

Every test here drives the real ``_guide_loop`` over scripted engine Actions
and a fake ``guide_star_find`` that answers from a per-frame star field
(brightest first), the doubles ``test_native_guider_relocks.py`` uses, widened
to more than one star per frame.

Each behaviour below was shown RED under a named mutation of
``guide/native.py``, run from a byte-for-byte backup and restored
byte-identical afterwards; the observed failure is quoted in each docstring.
"""
from __future__ import annotations

import asyncio
import math
import time

import pytest

import astrodeck.guide.native as nativemod
from astrodeck.events import bus
from astrodeck.guide.native import RELOCK_UNCONFIRMED_FRAMES, NativeGuider

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------- doubles


class _Logs:
    """Every ``bus.log`` line with its level, in order (a queue subscriber
    could drop the one line a test asserts on: the loop publishes a ``guide``
    event per frame)."""

    def __init__(self, monkeypatch) -> None:
        self.lines: list[tuple[str, str]] = []
        real = bus.log

        def _spy(level, message, source="hub"):
            self.lines.append((str(level), str(message)))
            return real(level, message, source)

        monkeypatch.setattr(bus, "log", _spy)

    def at(self, level: str, needle: str) -> list[str]:
        return [m for lv, m in self.lines if lv == level and needle in m]


class _Frame:
    """``data`` is the frame INDEX; the fake ``guide_star_find`` looks the star
    field up by it."""

    def __init__(self, index: int) -> None:
        self.data = index
        self.timestamp = time.time()


class _Cam:
    """Hands out numbered frames and sets the guider's stop flag on the LAST
    scripted one, so the loop processes exactly the script and returns on its
    own. A loop that stops EARLIER (the honest-death path) never asks for the
    rest, which ``engine.frames`` then shows."""

    name = "fake guide camera"

    def __init__(self, total: int, stop: asyncio.Event, on_frame=None) -> None:
        self.total = total
        self.stop = stop
        self.n = 0
        self.on_frame = on_frame

    async def expose(self, exposure_s, gain, offset, binning=1):
        if self.n >= self.total - 1:
            self.stop.set()
        index = min(self.n, self.total - 1)
        self.n += 1
        if self.on_frame is not None:
            self.on_frame(index)
        await asyncio.sleep(0)
        return _Frame(index)


class _Tel:
    name = "fake mount"

    def __init__(self) -> None:
        self.pulses: list[tuple[str, int]] = []

    async def pulse_guide(self, direction, ms):
        self.pulses.append((direction, int(ms)))

    async def guide_rates(self):
        return (0.004178, 0.004178)

    async def pier_side(self):
        raise NotImplementedError


class _FieldEngine:
    """A ``GuideEngine`` stand-in playing back one Action per frame.

    ``stats()["guiding"]`` is True throughout -- what the real engine reports
    from the moment it is asked to guide, including on the re-acquire frames
    after a star loss -- except on the frames listed in ``settling_at``, where
    a dither's settle window is open and the real engine reports
    ``guiding=False, settling=True`` (``engine.rs`` ``stats``)."""

    def __init__(self, actions, settling_at=()) -> None:
        self.actions = list(actions)
        self.frames = 0
        self.settling_at = set(settling_at)
        self.dithers: list[tuple[float, float]] = []

    def process(self, data, ts, exposure_s):
        self.frames += 1
        if self.actions:
            return self.actions.pop(0)
        return {"action": "idle"}

    def _settling(self) -> bool:
        return (self.frames - 1) in self.settling_at

    def stats(self):
        s = self._settling()
        return {"guiding": not s, "settling": s, "recent": []}

    def dither(self, dx, dy):
        self.dithers.append((dx, dy))

    def dump_calibration(self):
        return None


class _FieldNative:
    """Stands in for the wheel: ``guide_star_find`` answers from a per-frame
    star field, BRIGHTEST FIRST, as the real one does. Near-equal SNRs, because
    near-equal bright stars are what #204 is about."""

    def __init__(self, fields) -> None:
        self.fields = list(fields)

    def guide_star_find(self, data):
        index = int(data)
        field = self.fields[index] if index < len(self.fields) else \
            self.fields[-1]
        return [{"x": float(x), "y": float(y), "snr": 40.0 - 0.1 * i}
                for i, (x, y) in enumerate(field)], {}


def _harness(monkeypatch, actions, fields, *, scale=5.5, known=True,
             settling_at=(), on_frame=None, **cfg):
    monkeypatch.setattr(nativemod, "_native", _FieldNative(fields))
    engine = _FieldEngine(actions, settling_at)
    g = NativeGuider(None, _Tel(), config={"image_scale_arcsec": scale,
                                           "image_scale_known": known,
                                           "exposure_s": 0.01, **cfg},
                     profile_id=None)
    g.cam = _Cam(len(actions), g._stop,
                 on_frame=(lambda i: on_frame(g, i)) if on_frame else None)
    g._engine = engine
    g._active = True
    g._stop.clear()
    return g, engine


_LOST = {"action": "lock_lost", "reason": "star_lost"}
_IDLE = {"action": "idle"}
_PULSE = {"action": "pulse_pair", "ra": {"dir": "west", "ms": 100}, "dec": None}

# Two near-equal bright stars 820 px apart: the M45 geometry. At 5.5 arcsec/px
# that is 4510 arcsec, the size of the 00:55:58 stop (4513).
_A = (400.0, 300.0)
_B = (1220.0, 300.0)
_A_BACK = (400.4, 300.3)          # the lock star, back after the loss: 0.5 px


# ----------------------------------------------------- (a) + (d): the swap


async def test_a_brightness_swap_is_not_a_relock_to_a_different_star(monkeypatch):
    """(a) The #204 mechanism. Lock on A; the star is lost; it comes back
    UNMOVED, but now B sorts brighter. The re-lock is A at about 0 px and the
    guard does not trip.

    MUTANT "take stars[0]" (``near = stars[0] if stars else None`` in place of
    ``_nearest_within``) -- RED, observed verbatim:

        AssertionError: the guider stopped healthy guiding over a brightness
        swap: 'a single re-lock moved the lock 4510 arcsec, at or past the 120
        arcsec limit — that is a different star, not a flickering one'
    """
    logs = _Logs(monkeypatch)
    g, engine = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _LOST, _IDLE, _PULSE],
        fields=[[_A, _B], [_A, _B], [], [_B, _A_BACK], [_B, _A_BACK]])

    await g._guide_loop()

    assert not g._lost and g._active, (
        f"the guider stopped healthy guiding over a brightness swap: "
        f"{g._relock_stop_reason!r}")
    s = g.stats()
    assert s.relocks == 1, f"the re-lock was not counted: {s.relocks}"
    assert s.relock_events[0]["arcsec"] == pytest.approx(0.5 * 5.5, abs=0.05)
    assert not logs.at("error", "different star")
    assert logs.at("info", "re-acquired the same star")


async def test_each_relock_logs_both_distances(monkeypatch):
    """(d) For one release (0.3.35) each re-lock logs the real distance AND the
    one the old brightest-first rule would have judged, so the first nights on
    this fix show whether the old guard was firing on artefacts. Both numbers
    must be in the SAME line, or the log cannot pair them.

    MUTANT "drop the brightest-first distance from the log" (the same-star
    line built without its ``old`` clause) -- RED, observed verbatim:

        AssertionError: the re-lock line does not carry the old rule's
        distance: ['native guider: re-acquired the same star (2.7 arcsec from
        the last lock; re-lock 1 this session)']
    """
    logs = _Logs(monkeypatch)
    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _LOST, _IDLE, _PULSE],
        fields=[[_A, _B], [_A, _B], [], [_B, _A_BACK], [_B, _A_BACK]])

    await g._guide_loop()

    lines = logs.at("info", "re-acquired the same star")
    assert len(lines) == 1, lines
    # The real distance: A to where A came back. The old one: the brightest
    # pick before the loss (A) to the brightest pick after it (B), 820 px.
    real = math.hypot(_A_BACK[0] - _A[0], _A_BACK[1] - _A[1]) * 5.5
    old = math.hypot(_B[0] - _A[0], _B[1] - _A[1]) * 5.5
    assert f"{real:.1f} arcsec from the last lock" in lines[0], lines
    assert f"brightest-first pick reads {old:.1f} arcsec" in lines[0], (
        f"the re-lock line does not carry the old rule's distance: {lines}")


# ------------------------------------ (b): a far star only, the engine guiding


async def test_only_a_far_star_while_the_engine_guides_stops_the_guider(monkeypatch):
    """(b) The lock star does not come back; the engine re-acquires B, 820 px
    away, and keeps reporting guiding. Every frame is LOST (nothing within the
    radius), nothing is recorded, and after RELOCK_UNCONFIRMED_FRAMES of them
    the guider stops with a reason that says no star was found near the lock.
    Frames scripted past that point are never asked for.

    MUTANT "lost is ignored" (the lost branch returns before counting) -- RED,
    observed verbatim:

        AssertionError: the engine guided on a star 820 px from the lock for 7
        frames and the guider never stopped
    """
    logs = _Logs(monkeypatch)
    n = RELOCK_UNCONFIRMED_FRAMES
    actions = [_IDLE, _PULSE, _LOST] + [_PULSE] * (n + 4)
    fields = [[_A], [_A], []] + [[_B]] * (n + 4)
    g, engine = _harness(monkeypatch, actions=actions, fields=fields)

    await g._guide_loop()

    assert g._lost and not g._active and g._stop.is_set(), (
        f"the engine guided on a star 820 px from the lock for "
        f"{engine.frames - 3} frames and the guider never stopped")
    assert engine.frames == 3 + n, (
        f"stopped after {engine.frames} frames, not on the {n}th lost one")
    reason = g._relock_stop_reason
    assert "no star was found within 15 px of the lock" in reason, reason
    assert "different star" in reason, reason
    assert "moved" not in reason and "arcsec" not in reason, (
        f"the reason claims a displacement nothing measured: {reason!r}")
    s = g.stats()
    assert s.relocks == 0 and s.relock_events == [], \
        "a lost frame recorded a displacement"
    assert not s.guiding
    assert logs.at("error", "no star was found within 15 px of the lock")
    assert len(logs.at("warning", "no star within 15 px of the lock")) == n


async def test_a_lost_frame_keeps_the_baseline_and_the_watch(monkeypatch):
    """CONTROL for (b): fewer than RELOCK_UNCONFIRMED_FRAMES lost frames, then
    the lock star comes back 2 px from where it was. The re-lock is measured
    against the ORIGINAL lock (2 px, 11 arcsec) and counted: the lost frames
    neither moved the baseline nor disarmed the watch, and nothing stopped.

    MUTANT "a lost frame disarms the watch" (``self._relock_pending = False``
    in the lost branch) -- RED, observed verbatim:

        AssertionError: the re-lock after the lost frames was not counted: 0

    MUTANT "a lost frame moves the baseline" (``self._lock_xy = stars[0]`` in
    the lost branch) -- RED, observed verbatim:

        assert 0.0 == 11.0 ± 0.05
    """
    assert RELOCK_UNCONFIRMED_FRAMES >= 3, "the script below needs 2 lost frames"
    logs = _Logs(monkeypatch)
    back = (_A[0] + 2.0, _A[1])
    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _LOST, _PULSE, _PULSE, _PULSE, _PULSE],
        fields=[[_A], [_A], [], [_B], [_B], [_B, back], [_B, back]])

    await g._guide_loop()

    assert not g._lost and g._active, g._relock_stop_reason
    s = g.stats()
    assert s.relocks == 1, (
        f"the re-lock after the lost frames was not counted: {s.relocks}")
    assert s.relock_events[0]["arcsec"] == pytest.approx(11.0, abs=0.05)
    assert logs.at("warning", "re-locked on a star 11.0 arcsec from the last lock")


async def test_a_star_lost_frame_neither_counts_nor_clears_the_streak(monkeypatch):
    """A star-lost frame between two lost-near-the-lock frames is the engine
    finding nothing at all, not evidence about the lock. It must not RESET the
    streak: an engine alternating between "lost" and a lock on a far star
    spends no reacquire budget (the pulses reset it) and, with a resetting
    streak, would never be stopped at all.

    MUTANT "a star-lost frame resets the streak" (``self._relock_unconfirmed =
    0`` on a ``lock_lost`` action in ``_note_lock``) -- RED, observed verbatim:

        AssertionError: an engine alternating between star_lost and a far star
        guided on unstopped
    """
    n = RELOCK_UNCONFIRMED_FRAMES
    actions = [_IDLE, _PULSE, _LOST]
    fields: list = [[_A], [_A], []]
    for _ in range(n + 2):
        actions += [_PULSE, _LOST]
        fields += [[_B], []]
    g, _e = _harness(monkeypatch, actions=actions, fields=fields)

    await g._guide_loop()

    assert g._lost, ("an engine alternating between star_lost and a far star "
                     "guided on unstopped")
    assert "no star was found within" in g._relock_stop_reason


async def test_the_lost_stop_needs_no_image_scale(monkeypatch):
    """The radius is in guide-camera PIXELS, the engine's own unit, so unlike
    the arcsec limits (inert without a known scale, ``_relock_limit_exceeded``)
    this guard works on a rig with no guide focal length configured.

    MUTANT "gate the lost stop on a known image scale" (the stop branch skipped
    unless ``self._image_scale_known``) -- RED, observed verbatim:

        AssertionError: a rig without a guide focal length guided on a
        different star unstopped
    """
    n = RELOCK_UNCONFIRMED_FRAMES
    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _LOST] + [_PULSE] * (n + 2),
        fields=[[_A], [_A], []] + [[_B]] * (n + 2),
        scale=1.0, known=False)

    await g._guide_loop()

    assert g._lost, ("a rig without a guide focal length guided on a "
                     "different star unstopped")
    assert "within 15 px of the lock" in g._relock_stop_reason


# ------------------------------------------- (c): a real re-lock still counts


async def test_a_genuine_relock_inside_the_radius_is_counted_and_narrated(monkeypatch):
    """(c) The GN-03 walk the guard exists for, on a field with a brighter
    star far away: the lock star is lost and the engine re-locks 10 px off.
    That displacement is what is measured (55 arcsec at 5.5 arcsec/px),
    counted, and narrated as a walk, whatever the brightness order says.

    MUTANT "take stars[0]" -- RED, observed verbatim:

        AssertionError: the far brighter star was taken for the lock: 'a
        single re-lock moved the lock 4510 arcsec, at or past the 120 arcsec
        limit — that is a different star, not a flickering one'

    (4510, not 810 px x 5.5: the mutant measures B against the previous lock,
    A, which is exactly the old rule's reading.)
    """
    logs = _Logs(monkeypatch)
    moved = (_A[0] + 10.0, _A[1])
    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _LOST, _IDLE, _PULSE],
        fields=[[_A, _B], [_A, _B], [], [_B, moved], [_B, moved]])

    await g._guide_loop()

    assert not g._lost, (
        f"the far brighter star was taken for the lock: "
        f"{g._relock_stop_reason!r}")
    s = g.stats()
    assert s.relocks == 1
    assert s.relock_events[0]["arcsec"] == pytest.approx(55.0, abs=0.05)
    warned = logs.at("warning", "re-locked on a star 55.0 arcsec from the last lock")
    assert warned and "re-lock 1" in warned[0], logs.lines


async def test_the_radius_is_the_engines_configured_search_region(monkeypatch):
    """The radius is the engine's own: ``config["search_region"]`` is forwarded
    to the engine (``_build_engine_config``), so the host must read the same
    key. With it at 25 a re-lock 20 px off is a re-lock; at the default 15 the
    same frames are lost.

    MUTANT "hard-code the 15 px default" (``_relock_radius_px`` ignores the
    config) -- RED, observed verbatim:

        AssertionError: search_region=25 was ignored: the 20 px re-lock read as
        lost
    """
    moved = (_A[0] + 20.0, _A[1])
    actions = [_IDLE, _PULSE, _LOST, _PULSE, _PULSE, _PULSE, _PULSE]
    fields = [[_A], [_A], [], [moved], [moved], [moved], [moved]]

    wide, _e = _harness(monkeypatch, actions=list(actions), fields=fields,
                        search_region=25)
    await wide._guide_loop()
    assert wide.stats().relocks == 1, \
        "search_region=25 was ignored: the 20 px re-lock read as lost"
    assert not wide._lost

    # CONTROL: the default radius, same frames. 20 px is outside 15.
    narrow, _e = _harness(monkeypatch, actions=list(actions), fields=fields)
    await narrow._guide_loop()
    assert narrow.stats().relocks == 0
    assert narrow._lost and "within 15 px" in narrow._relock_stop_reason


# ---------------------------------------------- dithers move the engine's lock


async def test_a_dither_moves_the_lock_the_relock_is_measured_from(monkeypatch):
    """#219. The engine shifts its lock by every dither (``engine.rs``
    ``dither``) and publishes neither the lock nor the shift, so a host lock
    measured before a dither is stale by the dither. Measured against it, the
    lock star coming back after a loss would be outside the radius, read as
    lost, and a healthy long session would be stopped: 3 px dithers in random
    directions leave the lock an RMS 15 px from its start by the 25th. So the
    dither widens the radius by its size and the first settled frame
    re-measures the lock.

    Here one 20 px dither moves the lock (and, once settled, the star) 20 px;
    the star is lost and comes back 0.5 px from its NEW place.

    MUTANT "a dither does not move the host's lock" (the
    ``self._lock_moved_px += ...`` line in ``dither`` removed) -- RED, observed
    verbatim:

        AssertionError: a dither made the returning lock star read as a
        different star: 'no star was found within 15 px of the lock on 3
        consecutive frames while the engine kept reporting guiding (the nearest
        of 1 star(s) was 20 px away) — it is guiding on a different star'

    MUTANT "the radius does not widen by the dither" (``_relock_radius_px``
    returns ``r`` alone) -- RED, observed verbatim:

        AssertionError: a dither made the returning lock star read as a
        different star: 'no star was found within 15 px of the lock on 3
        consecutive frames while the engine kept reporting guiding (the nearest
        of 1 star(s) was 20 px away) — it is guiding on a different star'
    """
    # A fixed dither angle, so the 20 px lands where the script puts the star.
    monkeypatch.setattr(nativemod.random, "uniform", lambda a, b: 0.0)
    moved = (_A[0] + 20.0, _A[1])
    back = (moved[0] + 0.5, moved[1])
    dither_task: list[asyncio.Task] = []

    def _dither_at_frame_2(g, index):
        if index == 2 and not dither_task:
            dither_task.append(asyncio.ensure_future(g.dither(20.0)))

    g, engine = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _PULSE, _PULSE, _PULSE, _LOST,
                 _IDLE, _PULSE, _PULSE, _PULSE],
        fields=[[_A], [_A], [_A], [moved], [moved], [],
                [back], [back], [back], [back]],
        settling_at={2, 3},
        on_frame=_dither_at_frame_2)

    await g._guide_loop()
    await asyncio.wait_for(dither_task[0], timeout=5.0)   # it settled

    assert engine.dithers == [(20.0, 0.0)]
    assert not g._lost, (
        f"a dither made the returning lock star read as a different star: "
        f"{g._relock_stop_reason!r}")
    s = g.stats()
    assert s.relocks == 1
    assert s.relock_events[0]["arcsec"] == pytest.approx(0.5 * 5.5, abs=0.05)


async def test_a_settled_dither_gives_back_the_engines_radius(monkeypatch):
    """#219, the other half: the widening lasts only until the first settled
    frame re-measures the lock. After that the radius is the engine's 15 px
    again and the steady state pays for no star-find. Left widened, the radius
    grows by every dither of the night (15 + 3N px for N 3 px dithers) and the
    different-star guard goes inert for every star inside it. On a rig without
    a known image scale, as here, the arcsec limits are inert too, so nothing
    else would stop it.

    A 20 px dither settles and is re-measured; three steady frames follow; the
    star is lost and only a star 25 px from the re-measured lock comes back:
    outside 15 px, inside the 35 px a stale widening would still allow.

    MUTANT "the re-measure keeps the widening" (``self._lock_moved_px = 0.0``
    removed from the settled-frame branch of ``_note_lock``) -- RED, observed
    verbatim:

        AssertionError: a star 25 px from the lock was taken for the lock star
        after a settled dither: relocks=1, stop=''
    """
    monkeypatch.setattr(nativemod.random, "uniform", lambda a, b: 0.0)
    moved = (_A[0] + 20.0, _A[1])
    far = (moved[0] + 25.0, moved[1])
    n = RELOCK_UNCONFIRMED_FRAMES
    dither_task: list[asyncio.Task] = []
    finds: list[int] = []

    def _dither_at_frame_2(g, index):
        if index == 2 and not dither_task:
            dither_task.append(asyncio.ensure_future(g.dither(20.0)))

    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _PULSE, _PULSE, _PULSE, _PULSE,
                 _PULSE, _PULSE, _PULSE, _LOST] + [_PULSE] * (n + 2),
        fields=[[_A], [_A], [_A], [moved], [moved],
                [moved], [moved], [moved], []] + [[far]] * (n + 2),
        settling_at={2, 3}, on_frame=_dither_at_frame_2,
        scale=1.0, known=False)
    real = nativemod._native.guide_star_find

    def _counting(data):
        finds.append(int(data))
        return real(data)

    monkeypatch.setattr(nativemod._native, "guide_star_find", _counting)

    await g._guide_loop()
    await asyncio.wait_for(dither_task[0], timeout=5.0)

    assert g._lost and "within 15 px of the lock" in g._relock_stop_reason, (
        f"a star 25 px from the lock was taken for the lock star after a "
        f"settled dither: relocks={g.stats().relocks}, "
        f"stop={g._relock_stop_reason!r}")
    assert g.stats().relocks == 0
    # CONTROL on the cost: the first lock (0), the settled re-measure (4), and
    # nothing on the steady frames 5-7 until the loss re-arms the watch.
    assert [i for i in finds if i < 8] == [0, 4], (
        f"steady guiding after a settled dither ran star-finds on {finds}")


async def test_a_relock_ends_the_streak_of_lost_frames(monkeypatch):
    """The lost frames that stop the guider are CONSECUTIVE ones, inside one
    loss. A genuine re-lock ends the streak: two separate losses, each shorter
    than RELOCK_UNCONFIRMED_FRAMES and each ending with the lock star back,
    are two flickers, not a different star, however many lost frames they
    add up to.

    MUTANT "a re-lock keeps the streak" (``self._relock_unconfirmed = 0``
    removed from the re-lock path of ``_note_lock``) -- RED, observed
    verbatim:

        AssertionError: two separate short losses were added up into a
        different star: 'no star was found within 15 px of the lock on 3
        consecutive frames while the engine kept reporting guiding (the nearest
        of 1 star(s) was 820 px away) — it is guiding on a different star'
    """
    n = RELOCK_UNCONFIRMED_FRAMES
    assert n >= 2, "the first loss below needs n - 1 >= 1 lost frames"
    actions = ([_IDLE, _PULSE, _LOST] + [_PULSE] * (n - 1) + [_PULSE]
               + [_LOST, _PULSE, _PULSE, _PULSE])
    fields = ([[_A], [_A], []] + [[_B]] * (n - 1) + [[_B, _A_BACK]]
              + [[], [_B], [_B, _A_BACK], [_B, _A_BACK]])
    g, _e = _harness(monkeypatch, actions=actions, fields=fields)

    await g._guide_loop()

    assert not g._lost, (
        f"two separate short losses were added up into a different star: "
        f"{g._relock_stop_reason!r}")
    s = g.stats()
    assert s.relocks == 2, f"both re-locks should count: {s.relocks}"


async def test_control_steady_guiding_pays_for_no_star_find(monkeypatch):
    """CONTROL: the steady state is untouched. After the first lock, with no
    loss and no dither, the loop never calls ``guide_star_find`` again.

    MUTANT "no steady-state early return" (the ``return`` after the
    steady-state test in ``_note_lock`` replaced by ``pass``) -- RED, observed
    verbatim:

        AssertionError: steady guiding ran star-finds on frames [0, 1, 2, 3, 4,
        5, 6, 7, 8]
    """
    finds: list[int] = []
    g, _e = _harness(monkeypatch, actions=[_IDLE] + [_PULSE] * 8,
                     fields=[[_A, _B]] * 9)
    real = nativemod._native.guide_star_find

    def _counting(data):
        finds.append(int(data))
        return real(data)

    monkeypatch.setattr(nativemod._native, "guide_star_find", _counting)

    await g._guide_loop()

    assert finds == [0], f"steady guiding ran star-finds on frames {finds}"
    assert g.stats().relocks == 0 and not g._lost
