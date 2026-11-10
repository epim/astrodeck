# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1083: a star re-acquired after a loss must be the lock star, by its flux and
half-flux diameter, or the loss stands.

On 2026-10-17 and 2026-10-19 the native guider lost its star, the engine
re-acquired a DIFFERENT star within 7.1 and 4.0 arcsec of the old lock, the
position test called it "the same star", and the guider drove the mount
arcminutes toward the old lock with pulses at the limit. The error against the
wrong star shrank as the mount moved, so the runaway gate never fired.

The engine now publishes the flux and half-flux diameter of the star it
accepted (``star_flux``, ``star_hfd``). ``NativeGuider`` keeps the lock star's
own (``StarIdentity``), refuses a re-acquired star that does not match it
within ``STAR_FLUX_TOLERANCE`` and ``STAR_HFD_TOLERANCE`` by stopping through
``_stop_for_relock`` (the recovery the sequence engine runs for a lost guider
takes over), withholds the frame's own pulse, and lets the runaway gate read
the same identity.

Only the Rust ENGINE is replaced in the scripted tests (``ShapeEngine``): the
host code under test (``_note_lock``, ``_judge_frame``, ``StarIdentity``, the
guide loop) is real. The last two tests run the real engine on the sim rig,
through the privately built wheel, with a star swap injected into the guide
camera.

Each test names the mutant it was shown RED under: the production file copied
to a byte backup, mutated, run, and restored from the backup (sha256 checked).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

import astrodeck.guide.native as nativemod
import astrodeck.hub as hub_module
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.native import (
    RUNAWAY_PULSES, STAR_FLUX_TOLERANCE, STAR_HFD_TOLERANCE,
    STAR_IDENTITY_FRAMES, STAR_IDENTITY_MIN_FRAMES, NativeGuider, StarIdentity)

from _oct08_guider_harness import (
    IDLE, Logs, Mount, ScriptedEngine, arm_loop, cal_dict, make_guider,
    profile_id, pulse, stop_loop, wait_until)

def _wheel_lacks_star_stats() -> str:
    """Why the real-engine tests cannot run on the installed wheel, or "".
    A wheel built before #1083 has no ``star_flux`` in ``stats()``; the
    guider then falls back to its earlier behaviour (see the old-wheel test),
    and a real-engine run proves nothing about the identity check."""
    import astrodeck_native
    if "star_flux" not in astrodeck_native.GuideEngine({}).stats():
        return ("the installed astrodeck_native wheel predates #1083: "
                "GuideEngine.stats() has no 'star_flux' key; rebuild it")
    return ""


NEEDS_STAR_STATS = pytest.mark.native(lacks=_wheel_lacks_star_stats)

#: The lock star of the scripted tests: where the engine's lock is, and its
#: flux and half-flux diameter (px).
LOCK_XY = (100.0, 100.0)
FLUX, HFD = 1000.0, 3.0
LOST = {"action": "lock_lost", "reason": "star_lost"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return tmp_path


class FakeNative:
    """The wheel's star find, reduced to one star at the lock: the host's
    own re-scan agrees with the engine about where the star is."""

    def guide_star_find(self, data):
        return [{"x": LOCK_XY[0], "y": LOCK_XY[1], "snr": 30.0}], {}


class ShapeEngine(ScriptedEngine):
    """``ScriptedEngine`` whose ``stats()`` also publishes what the real
    engine does: the lock, the tracked star's position and (unless
    ``publish_shape`` is off, a wheel built before #1083) its flux and HFD.

    ``stars`` is one entry per frame: ``(x, y, flux, hfd)`` for the star the
    engine holds after that frame, or None when it holds none. The last entry
    is held once the list runs out."""

    def __init__(self, script, stars, *, publish_shape=True, **kw):
        super().__init__(script, **kw)
        self.stars = list(stars)
        self.cur = None
        self.publish_shape = publish_shape

    def process(self, data, ts, exp):
        action = super().process(data, ts, exp)
        if self.stars:
            self.cur = self.stars.pop(0)
        return action

    def stats(self):
        s = super().stats()
        s["lock"] = list(LOCK_XY)
        s["tracking"] = list(self.cur[:2]) if self.cur else None
        if self.publish_shape:
            s["star_flux"] = self.cur[2] if self.cur else None
            s["star_hfd"] = self.cur[3] if self.cur else None
        return s


def _star(flux=FLUX, hfd=HFD, dx=0.0):
    return (LOCK_XY[0] + dx, LOCK_XY[1], flux, hfd)


def _loss_script(reacquired, *, steady=8, reacquire_action=None, tail=2,
                 hold=()):
    """``steady`` measured idle frames on the lock star, the loss frame, any
    ``hold`` frames on which the engine holds no star yet (``(action,
    errs)``-less idle frames), the re-acquire frame, then ``tail`` idle frames
    that must never run on a refusal. Returns ``(script, stars)``."""
    script = [(IDLE, (0.4, 0.1))] * steady
    stars = [_star()] * steady
    script.append((LOST, None))
    stars.append(None)
    for _ in hold:
        script.append((IDLE, None))
        stars.append(None)
    script.append((reacquire_action or pulse("east", 300), (0.3, 0.1)))
    stars.append(reacquired)
    script += [(IDLE, (0.3, 0.1))] * tail
    stars += [reacquired] * tail
    return script, stars


async def _run(monkeypatch, script, stars, *, publish_shape=True):
    """Run the real guide loop over ``script`` and wait for it to either stop
    itself or play every frame. Returns the guider, mount, engine and logs,
    and ``lost``, ``active`` and ``relocks`` as they stood at the end of the
    script (``stop_loop`` clears ``_active``)."""
    monkeypatch.setattr(nativemod, "_native", FakeNative())
    logs = Logs(monkeypatch)
    mount = Mount()
    g = make_guider(profile=profile_id("w1id"), mount=mount)
    eng = ShapeEngine(script, stars, publish_shape=publish_shape,
                      cal=cal_dict())
    arm_loop(g, eng)
    g._lock_xy = LOCK_XY            # the engine's lock, not arm_loop's origin
    try:
        assert await wait_until(
            lambda: g._loop_task.done() or eng.frames >= len(script) + 1, 5.0)
        end = SimpleNamespace(lost=g._lost, active=g._active,
                              relocks=g._relocks)
    finally:
        await stop_loop(g)
    return SimpleNamespace(g=g, mount=mount, eng=eng, logs=logs, **vars(end))


# --------------------------------------------------------------- StarIdentity


def test_identity_is_the_median_of_the_window_and_judges_after_a_minimum():
    """The baseline is the median of the last ``STAR_IDENTITY_FRAMES`` frames
    (one wild frame does not move it, the window forgets old frames), and
    nothing is judged before ``STAR_IDENTITY_MIN_FRAMES``.

    MUTANT I1 (``mismatch`` ignores the minimum): RED on the first assertion,
    a refusal with fewer frames than the minimum. MUTANT I2 (the median
    replaced by the last value): RED on the wild-frame assertion."""
    ident = StarIdentity()
    for _ in range(STAR_IDENTITY_MIN_FRAMES - 1):
        ident.observe(FLUX, HFD)
        assert ident.mismatch(FLUX * 5, HFD * 5) == ""
    ident.observe(FLUX, HFD)
    assert ident.mismatch(FLUX * 5, HFD * 5) != ""
    ident.observe(FLUX * 9, HFD * 9)            # one wild frame
    assert ident.mismatch(FLUX, HFD) == ""
    for _ in range(STAR_IDENTITY_FRAMES):       # the window forgets it all
        ident.observe(FLUX * 2, HFD * 2)
    assert ident.mismatch(FLUX * 2, HFD * 2) == ""
    assert ident.mismatch(FLUX, HFD) != ""


@pytest.mark.parametrize("bad", [None, 0, -5, float("nan"), float("inf"), "x"])
def test_an_unreadable_value_is_not_taken_and_is_not_judged(bad):
    """A frame with a missing, zero, negative, non-finite or non-numeric flux
    or HFD adds nothing to the baseline, and a star whose shape cannot be read
    is not refused for it.

    MUTANT I3 (``_usable`` accepts anything that converts): RED, the baseline
    moves or the unreadable star is refused."""
    ident = StarIdentity()
    for _ in range(STAR_IDENTITY_MIN_FRAMES):
        ident.observe(FLUX, HFD)
        ident.observe(bad, HFD)
        ident.observe(FLUX, bad)
    assert ident.mismatch(FLUX, HFD) == ""
    assert ident.mismatch(bad, HFD) == ""
    assert ident.mismatch(FLUX, bad) == ""
    assert ident.mismatch(FLUX * 5, HFD) != ""


# ----------------------------------------------------- a re-acquire is judged


async def test_a_reacquire_onto_a_different_star_is_refused(monkeypatch):
    """The 10-17 shape: the star is lost, the engine accepts another star 1.3
    px from the lock (7 arcsec at 5.5 arcsec/px, inside the same-star radius)
    that is 60% as bright and almost twice as wide. The guider stops through
    the lost-guider path, says why in the night log, and does NOT send the
    frame's correction, which is aimed at the star it has just refused.

    MUTANT D1 (the identity check dropped: ``StarIdentity.mismatch``
    returning ``""``): RED, the guider accepted the star (``lost=False``).
    MUTANT D4 (only the re-lock site dropped, ``other = ""`` in
    ``_note_lock``): RED the same way. MUTANT D2 (``if self._lost: break``
    removed from the guide loop): RED, the frame's pulse was sent."""
    script, stars = _loss_script(_star(flux=600.0, hfd=5.5, dx=1.3))
    r = await _run(monkeypatch, script, stars)
    assert r.lost, (
        f"the guider accepted a star with flux 600 and HFD 5.5 against the "
        f"lock star's {FLUX:.0f} and {HFD}: lost={r.lost}")
    assert not r.active
    assert r.relocks == 0, "a refused star is not a re-lock"
    assert r.mount.pulses == [], (
        f"a correction aimed at the refused star was sent: {r.mount.pulses}")
    said = r.logs.at("error", "is not the lock star")
    assert len(said) == 1 and "flux 600 against the lock star's 1000" in said[0]
    assert "HFD 5.5 px against the lock star's 3.0 px" in said[0]
    assert r.eng.frames == 8 + 2, "the loop ran on after refusing the star"


async def test_the_same_star_coming_back_is_accepted(monkeypatch):
    """The control: the star comes back 0.4 px away, 4% fainter and 3% wider.
    It is the lock star, so guiding carries on, the re-lock is counted, and
    the frame's correction is sent.

    MUTANT D3 (tolerances made 0.0): RED here, the lock star refused."""
    script, stars = _loss_script(_star(flux=960.0, hfd=3.1, dx=0.4))
    r = await _run(monkeypatch, script, stars)
    assert not r.lost and r.active
    assert r.relocks == 1
    assert r.logs.at("info", "re-acquired the same star")
    assert r.mount.pulses == [("east", 300)]
    assert not r.logs.at("error", "not the lock star")


@pytest.mark.parametrize("flux_ratio,hfd_ratio,refused", [
    (1 + STAR_FLUX_TOLERANCE - 0.01, 1.0, False),
    (1 + STAR_FLUX_TOLERANCE + 0.01, 1.0, True),
    (1 - STAR_FLUX_TOLERANCE + 0.01, 1.0, False),
    (1 - STAR_FLUX_TOLERANCE - 0.01, 1.0, True),
    (1.0, 1 + STAR_HFD_TOLERANCE - 0.01, False),
    (1.0, 1 + STAR_HFD_TOLERANCE + 0.01, True),
    (1.0, 1 - STAR_HFD_TOLERANCE + 0.01, False),
    (1.0, 1 - STAR_HFD_TOLERANCE - 0.01, True),
])
async def test_each_tolerance_is_pinned_on_both_sides(
        monkeypatch, flux_ratio, hfd_ratio, refused):
    """A star just inside a tolerance (flux, then HFD, brighter and fainter,
    wider and narrower) is the lock star, and one just outside is not. Each
    bound is judged alone: the other quantity matches exactly. The 0.01 margin
    is the resolution; the exact bound is not pinned.

    MUTANT T1 (the flux test dropped from ``StarIdentity.mismatch``): RED on
    the two outside-flux cases. MUTANT T2 (the HFD test dropped): RED on the
    two outside-HFD cases."""
    script, stars = _loss_script(
        _star(flux=FLUX * flux_ratio, hfd=HFD * hfd_ratio, dx=0.5))
    r = await _run(monkeypatch, script, stars)
    assert r.lost is refused, (
        f"flux x{flux_ratio:.2f}, HFD x{hfd_ratio:.2f}: "
        f"refused={r.lost}, expected {refused}")


async def test_nothing_is_judged_before_the_baseline_has_enough_frames(
        monkeypatch):
    """A swap on the very first frames of a session cannot be told: the lock
    star has been seen fewer than ``STAR_IDENTITY_MIN_FRAMES`` times. Said
    out loud so it is a known limit and not a surprise: that star is
    accepted, as before this change. One frame more and it is refused.

    MUTANT I1 (see the unit test): RED on the first half."""
    swapped = _star(flux=400.0, hfd=5.5, dx=0.5)
    script, stars = _loss_script(swapped, steady=STAR_IDENTITY_MIN_FRAMES - 1)
    r = await _run(monkeypatch, script, stars)
    assert not r.lost
    script, stars = _loss_script(swapped, steady=STAR_IDENTITY_MIN_FRAMES)
    r = await _run(monkeypatch, script, stars)
    assert r.lost


async def test_the_check_waits_until_the_engine_holds_a_star(monkeypatch):
    """After a loss the engine reports guiding on frames where its
    re-acquire was rejected and it holds no star (``star_flux`` None). Nothing
    is identified on those frames and the watch stays armed, so the swapped
    star that is accepted two frames later is still refused.

    MUTANT W1 (the wait removed, a missing shape treated as a match): RED,
    the watch was spent on the frame before the engine held the star and the
    swapped star slipped past."""
    script, stars = _loss_script(_star(flux=400.0, hfd=5.5, dx=0.5),
                                 hold=(1, 2))
    r = await _run(monkeypatch, script, stars)
    assert r.lost, ("the swapped star slipped past on the frame before the "
                     "engine held it")
    assert r.eng.frames == 8 + 1 + 2 + 1


async def test_a_wheel_that_does_not_publish_the_shape_is_not_checked(
        monkeypatch):
    """A wheel built before #1083 has no ``star_flux`` key. The guider keeps
    its earlier behaviour on it: a re-acquire inside the same-star radius is
    accepted, whatever star it is. (The shipped wheel is rebuilt with the
    guider, so this is the safe fallback for a stale install, not a mode.)

    MUTANT W2 (the wait taken on a missing shape without asking whether the
    wheel publishes one): RED, the watch is never spent and no re-lock is
    counted."""
    script, stars = _loss_script(_star(flux=400.0, hfd=5.5, dx=0.5))
    r = await _run(monkeypatch, script, stars,
                                     publish_shape=False)
    assert not r.lost and r.relocks == 1


# ----------------------------------------------- the runaway gate reads it too


def _streak_script(streak_star):
    """Eight steady frames on the lock star, then eight corrections at the
    1000 ms limit, east, on ``streak_star``, with a RA error that SHRINKS
    (the wrong-star signature: the mount chases the old lock, so the error
    against the star the engine holds falls)."""
    script = [(IDLE, (0.4, 0.1))] * 8
    stars = [_star()] * 8
    for k in range(8):
        script.append((pulse("east", 1000), (3.0 - 0.3 * k, 0.1)))
        stars.append(streak_star)
    return script, stars


async def test_a_streak_at_the_limit_on_another_star_stops_the_guider(
        monkeypatch):
    """No loss at all: the engine is simply on a star that is not the lock
    star, and its corrections sit at the limit while the error falls, so the
    error-growth test never trips. Six at the limit on a star that does not
    match the lock star stop the guider, whatever the error does, and the
    sixth correction is not sent.

    MUTANT R1 (``_on_another_star`` made to return False): RED, all eight
    pulses sent and the guider never stopped."""
    script, stars = _streak_script(_star(flux=400.0, hfd=5.5, dx=1.0))
    r = await _run(monkeypatch, script, stars)
    assert r.lost, (f"{len(r.mount.pulses)} capped pulses on a star that is "
                     f"not the lock star and the guider never stopped")
    assert len(r.mount.pulses) == RUNAWAY_PULSES
    assert r.logs.at("error", "on a star that is not the lock star")


async def test_the_same_streak_on_the_lock_star_is_left_to_the_error_trend(
        monkeypatch):
    """The control: the same eight limit corrections with a falling error on
    the lock star itself are the engine converging from a large offset, which
    the gate has never stopped. The identity read adds a stop only for a star
    that does not match.

    MUTANT R2 (``_on_another_star`` stopping whatever the identity says):
    RED, the lock star's streak stopped the guider."""
    script, stars = _streak_script(_star(dx=1.0))
    r = await _run(monkeypatch, script, stars)
    assert not r.lost
    assert len(r.mount.pulses) == 8


# ------------------------------------------------ the real engine on the sim


class SwapRig:
    """Drives a real-engine run on the sim by FRAME NUMBER, so the swap lands
    on the frame the test names whatever the machine's speed. After
    ``start_guiding`` returns, frame ``k`` from the guide camera is rendered
    with the plan for ``k``: the star hidden and the clock skewed past the
    engine's lost-star timeout (the loss), then the star back, optionally
    narrower (the swap).

    The guide camera's frame timestamp is what the engine reads as time, so
    skewing it makes the engine see the star missing for longer than its
    20 s timeout without the test waiting for it."""

    LOSS_SKEW_S = 25.0

    def __init__(self, cam, srig, *, lose_at, return_at, swap_sigma=None):
        self.cam, self.srig = cam, srig
        self.lose_at, self.return_at, self.swap_sigma = (
            lose_at, return_at, swap_sigma)
        self.k = 0
        self.skew = 0.0
        self._expose = cam.expose
        cam.expose = self.expose

    async def expose(self, *a, **kw):
        self.k += 1
        if self.k == self.lose_at:
            self.srig.guide_star_hidden = True
            self.skew += self.LOSS_SKEW_S
        elif self.k == self.return_at:
            self.srig.guide_star_hidden = False
            if self.swap_sigma is not None:
                self.cam.STAR_SIGMA_PX = self.swap_sigma
        frame = await self._expose(*a, **kw)
        frame.timestamp += self.skew
        return frame


async def _sim_run(*, swap_sigma):
    rig = build_sim_rig()
    cam, tel, srig = rig["guide_camera"], rig["telescope"], rig["_rig"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=f"test-w1id-{uuid.uuid4().hex[:8]}")
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    plan = SwapRig(cam, srig, lose_at=14, return_at=15, swap_sigma=swap_sigma)
    return g, plan


@NEEDS_STAR_STATS
async def test_an_injected_star_swap_on_the_sim_is_refused_and_handled(
        monkeypatch):
    """The done-when of #1107 item 2: a sim run with the real engine, the
    star lost and a different star (60% of the sigma, so a different HFD at
    the same position) accepted by the engine on the next frame. The guider
    refuses it: the loss stands, the guider is no longer active, and the night
    log names the identity. (``stop_guiding`` clears the latch, so the state is
    read before it.)

    MUTANT D1 (the identity check dropped, see above): RED, the swapped star
    was accepted (``lost=False``)."""
    logs = Logs(monkeypatch)
    g, plan = await _sim_run(swap_sigma=1.5)
    try:
        await wait_until(lambda: g._loop_task.done(), 30.0, 0.02)
        lost, active, relocks = g._lost, g._active, g._relocks
    finally:
        await g.stop_guiding()
        await g.disconnect()
    assert lost, f"the swapped star was accepted: relocks={relocks} lost={lost}"
    assert not active
    assert logs.at("error", "is not the lock star")


@NEEDS_STAR_STATS
async def test_the_same_star_back_on_the_sim_is_accepted(monkeypatch):
    """The control on the same plan: the star is lost and comes back
    unchanged. The guider counts a re-lock and is still guiding ten frames
    after it."""
    logs = Logs(monkeypatch)
    g, plan = await _sim_run(swap_sigma=None)
    try:
        assert await wait_until(lambda: plan.k >= 25, 30.0, 0.02)
        assert not g._lost and g._active
        assert g._relocks == 1
        assert not logs.at("error", "not the lock star")
    finally:
        await g.stop_guiding()
        await g.disconnect()
