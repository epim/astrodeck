# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-81 (#649, WP-41's own residual 1): ``GuideStatsSnapshot`` now also
publishes ``tracking`` -- the engine's live search origin, the star it is
CURRENTLY using to compute a correction -- through ``astrodeck_native``'s
``stats()``, and the host's different-star guard (``guide/native.py``'s
``_note_lock``) reads it instead of trusting its own frame-wide rescan.

The gap this closes (WP-41's closing comment, residual 1): ``lock`` is the
engine's FIXED offset reference for the whole guiding session (dither
aside), by design -- so a stale loss's full re-acquire never moves it, even
when the star it reacquires is a different one than whatever was originally
locked. If the TRUE lock star ALSO happens to have returned near its old
spot while the engine's full re-acquire actually picked a different, nearby
star, ``_note_lock``'s own frame-wide rescan finds the TRUE star (nearest
the unchanged ``lock``) and reads a healthy, near-zero re-lock -- while the
engine is actually driving every correction against the OTHER star. This
file's main test reproduces exactly that shape (a true lock star and a
decoy both present in the frame the host rescans) and shows that, once the
engine's own ``tracking`` reading is available, the re-lock is measured
against the star the engine actually accepted, not the host's own
independently nearest-matched guess.

Every behaviour below was shown RED under a named mutation (quoted
verbatim in each docstring), run from a byte-for-byte backup and restored
byte-identical afterwards.
"""
from __future__ import annotations

import asyncio
import math
import time

import pytest

import astrodeck.guide.native as nativemod
from astrodeck.guide.native import NativeGuider

# No module-level ``pytestmark = pytest.mark.asyncio``: ``asyncio_mode =
# "auto"`` (pyproject.toml) already collects the async tests below without it.


# --------------------------------------------------------------------- doubles
# Deliberately NOT imported from test_w5_guide_lock_published.py or
# test_guider_relock_nearest_star.py: a test file owns its doubles. The one
# difference from the WP-41 file's ``_LockEngine`` is the point of this file
# -- ``stats()`` can ALSO carry a ``"tracking"`` key, the WP-81 shape.


class _Frame:
    """``data`` is the frame INDEX; the fake ``guide_star_find`` looks the star
    field up by it."""

    def __init__(self, index: int) -> None:
        self.data = index
        self.timestamp = time.time()


class _Cam:
    """Hands out numbered frames and sets the guider's stop flag on the LAST
    scripted one, so the loop processes exactly the script and returns on its
    own."""

    name = "fake guide camera"

    def __init__(self, total: int, stop: asyncio.Event) -> None:
        self.total = total
        self.stop = stop
        self.n = 0

    async def expose(self, exposure_s, gain, offset, binning=1):
        if self.n >= self.total - 1:
            self.stop.set()
        index = min(self.n, self.total - 1)
        self.n += 1
        await asyncio.sleep(0)
        return _Frame(index)


class _Tel:
    name = "fake mount"

    async def pulse_guide(self, direction, ms):
        return None

    async def pier_side(self):
        raise NotImplementedError


class _TrackEngine:
    """A ``GuideEngine`` stand-in that plays back one Action per frame and
    publishes ``"lock"`` (the fixed offset reference) per frame, plus
    ``"tracking"`` (the star actually accepted into the guiding loop this
    frame) when ``trackings`` is given. ``trackings=None`` omits the key
    entirely, simulating a wheel that predates WP-81 -- the same "absent
    degrades to the old behaviour" contract WP-41's own doubles use for
    ``"lock"``."""

    def __init__(self, actions, locks, trackings=None) -> None:
        self.actions = list(actions)
        self.locks = list(locks)
        self.trackings = None if trackings is None else list(trackings)
        self.frames = 0

    def process(self, data, ts, exposure_s):
        self.frames += 1
        if self.actions:
            return self.actions.pop(0)
        return {"action": "idle"}

    def stats(self):
        i = min(self.frames - 1, len(self.locks) - 1) if self.locks else -1
        lock = self.locks[i] if i >= 0 else None
        out = {"guiding": True, "settling": False, "recent": [], "lock": lock}
        if self.trackings is not None:
            j = (min(self.frames - 1, len(self.trackings) - 1)
                 if self.trackings else -1)
            out["tracking"] = self.trackings[j] if j >= 0 else None
        return out

    def dump_calibration(self):
        return None


class _FieldNative:
    """Stands in for the wheel: ``guide_star_find`` answers from a per-frame
    star field, BRIGHTEST FIRST (the order the list is given in), as the real
    one does."""

    def __init__(self, fields) -> None:
        self.fields = list(fields)

    def guide_star_find(self, data):
        index = int(data)
        field = self.fields[index] if index < len(self.fields) else self.fields[-1]
        return [{"x": float(x), "y": float(y), "snr": 40.0 - 0.1 * i}
                for i, (x, y) in enumerate(field)], {}


def _harness(monkeypatch, actions, fields, locks, trackings=None, **cfg):
    monkeypatch.setattr(nativemod, "_native", _FieldNative(fields))
    engine = _TrackEngine(actions, locks, trackings)
    g = NativeGuider(None, _Tel(), config={"image_scale_arcsec": 5.5,
                                           "image_scale_known": True,
                                           "exposure_s": 0.01, **cfg},
                     profile_id=None)
    g.cam = _Cam(len(actions), g._stop)
    g._engine = engine
    g._active = True
    g._stop.clear()
    return g, engine


_IDLE = {"action": "idle"}
_PULSE = {"action": "pulse_pair", "ra": {"dir": "west", "ms": 100}, "dec": None}
_LOST = {"action": "lock_lost", "reason": "star_lost"}

# The TRUE lock star, and a decoy NEXT TO it (10 px away -- inside the
# default 15 px search radius a host-side rescan centred on the fixed lock
# would still accept as "near", but well past ``_RELOCK_SAME_STAR_PX``
# (1.5 px), so picking the wrong one of the two is independently observable
# in the recorded re-lock, not just quietly folded into "same star").
_TRUE_LOCK = (500.0, 300.0)
_DECOY_NEXT_TO_TRUE = (510.0, 300.0)


# ---------------------------------------------------- the fixed scenario (#649)


async def test_relock_follows_the_engines_own_tracked_star_not_the_nearest_frame_match(
        monkeypatch):
    """#649/WP-81 (WP-41 residual 1): the true lock star returns near its own
    old spot at the SAME time a different, nearby star is also in frame.
    Pre-fix, the host's own rescan -- centred on the unchanged, published
    ``lock`` -- finds the TRUE star (it is nearest) and reads a healthy,
    near-zero re-lock, even though the engine's full re-acquire actually
    accepted the OTHER star and is driving every correction against it
    (``tracking`` reports the decoy). With ``tracking`` read, the re-lock is
    judged against the star the engine actually has.

    MUTANT (the override line commented out, leaving ``near`` as the
    frame-rescan pick) -- RED, observed verbatim:

        AssertionError: the re-lock must follow the engine's own tracked
        star, not the host's independent nearest-frame-match: (500.0, 300.0)
        assert (500.0, 300.0) == (510.0, 300.0)
    """
    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _LOST, _PULSE],
        fields=[[_TRUE_LOCK], [], [_TRUE_LOCK, _DECOY_NEXT_TO_TRUE]],
        locks=[_TRUE_LOCK, _TRUE_LOCK, _TRUE_LOCK],
        trackings=[_TRUE_LOCK, _TRUE_LOCK, _DECOY_NEXT_TO_TRUE],
    )

    await g._guide_loop()

    assert g._lock_xy == _DECOY_NEXT_TO_TRUE, (
        "the re-lock must follow the engine's own tracked star, not the "
        f"host's independent nearest-frame-match: {g._lock_xy}")
    assert g._relocks == 1, f"expected exactly one counted re-lock, got {g._relocks}"
    # 10 px * 5.5 arcsec/px = 55 arcsec -- the REAL displacement between the
    # two distinct stars, not the false zero the old rescan would have read.
    assert abs(g._relock_arcsec_total - 55.0) < 1e-6, (
        f"expected the real 55 arcsec displacement, got {g._relock_arcsec_total}")


async def test_control_an_unpublished_tracking_keeps_the_pre_fix_frame_rescan(
        monkeypatch):
    """CONTROL for the test above, and the proof the old defect was real: the
    IDENTICAL scenario (true star and decoy both back in frame), but with no
    ``"tracking"`` key at all (an older wheel, or any test double that never
    sets it) -- ``_note_lock`` must degrade exactly to the pre-#649 frame
    rescan, which finds the TRUE star (nearest the unchanged ``lock``) and
    reads a healthy, same-star, zero-displacement re-lock. Proves the new
    code path is additive, not a replacement that could silently change
    old-wheel behaviour -- and, by contrast with the test above, that this
    IS the false "healthy" reading WP-41's residual 1 described."""
    g, _e = _harness(
        monkeypatch,
        actions=[_IDLE, _LOST, _PULSE],
        fields=[[_TRUE_LOCK], [], [_TRUE_LOCK, _DECOY_NEXT_TO_TRUE]],
        locks=[_TRUE_LOCK, _TRUE_LOCK, _TRUE_LOCK],
        trackings=None,   # no "tracking" key published at all
    )

    await g._guide_loop()

    assert g._lock_xy == _TRUE_LOCK, (
        "control premise broken: without 'tracking' the rescan must still "
        f"pick the nearest-to-lock match: {g._lock_xy}")
    assert g._relocks == 1, f"expected exactly one counted re-lock, got {g._relocks}"
    assert g._relock_arcsec_total == 0.0, (
        "control premise broken: the pre-#649 rescan reads this as the same "
        f"star with zero displacement, got {g._relock_arcsec_total}")


# ------------------------------------------------------- the real wheel, e2e


native = pytest.importorskip(
    "astrodeck_native",
    reason="WP-81 needs a native wheel rebuilt with the tracking field; see "
           "the work package's report for how to build one into a private venv")


def _ident_cal():
    return {"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
            "y_angle": math.pi / 2, "y_angle_error": 0.0,
            "declination": 0.0, "pier_side": "west",
            "ra_parity": "even", "dec_parity": "even",
            "rotator_angle": 0.0, "binning": 1, "is_valid": True}


def _gaussian_frame(w, h, cx, cy, amp=4000.0, sg=1.6, bg=100):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sg * sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)


def test_the_real_wheel_publishes_tracking_end_to_end():
    """WP-81, the Rust + binding half, exercised through the actual PyO3
    module (not a fake): a rebuilt wheel's ``stats()`` carries ``tracking``,
    the engine's live search origin, and it MOVES with a stale loss's full
    re-acquire while ``lock`` (verified unchanged by
    ``test_w5_guide_lock_published.py``'s own e2e test) does not.

    Needs a REBUILT native wheel -- an unmodified pre-WP-81 one has no
    "tracking" key at all (``_note_lock`` then degrades to its documented
    pre-#649 behaviour; see the fake-engine tests above). Skipped outright
    when no wheel is installed at all (``pytest.importorskip``), and SKIPPED
    (not failed) when one is installed but predates this field -- same
    "deliberately never replaced by a worktree's own build" reasoning as
    ``test_w5_guide_lock_published.py``'s sibling test.

    MUTANT (``astrodeck-native/src/lib.rs``'s ``stats()`` binding, the
    ``match s.tracking { ... }`` block deleted so no "tracking" key is set at
    all) -- this looks identical to an unrebuilt wheel from here (both omit
    the key), so this test cannot re-prove it by itself; it was shown RED
    under that exact mutant with a wheel freshly rebuilt from it, verbatim:

        AssertionError: the engine accepted a star but the binding published
        no tracking position
    """
    e = native.GuideEngine({"image_scale_arcsec": 2.0})
    e.load_calibration(_ident_cal())
    e.begin_guiding()
    if "tracking" not in e.stats():
        pytest.skip("installed astrodeck_native wheel predates WP-81's "
                    "'tracking' field -- needs the wheel rebuilt and deployed")
    assert e.stats().get("tracking") is None, "no star found yet this session"

    # t=0: the lock-establishing frame. tracking mirrors the just-established
    # lock (both are the search origin on this frame, per engine.rs).
    a = e.process(_gaussian_frame(200, 200, 100.0, 100.0), 0.0, 1.0)
    assert a["action"] == "idle"
    lock = e.stats().get("lock")
    tracking = e.stats().get("tracking")
    assert lock is not None and tracking is not None
    assert abs(tracking[0] - lock[0]) < 0.6 and abs(tracking[1] - lock[1]) < 0.6

    # The star goes missing (a blank field) long enough to cross the 20s
    # staleness threshold -> LockLost, and tracking drops to None exactly as
    # a fresh session's does -- lock is untouched.
    blank = _gaussian_frame(200, 200, 100.0, 100.0, amp=0.0)
    last = None
    for i in range(1, 12):
        last = e.process(blank, i * 2.0, 1.0)
    assert last["action"] == "lock_lost", last
    assert e.stats().get("tracking") is None, "tracking must drop on a stale loss"
    assert e.stats().get("lock") == lock, "a stale loss must not move the lock"

    # A full re-acquire finds a star 30px away -- beyond the narrow local
    # search_region, reachable only by the full-frame auto_find fallback
    # (same shape as astro-guide's own
    # stale_star_reacquired_by_full_frame_autofind_against_old_lock).
    e.process(_gaussian_frame(200, 200, 130.0, 100.0), 24.0, 1.0)
    new_tracking = e.stats().get("tracking")
    assert new_tracking is not None, (
        "the engine accepted a star but the binding published no tracking "
        "position")
    assert abs(new_tracking[0] - 130.0) < 0.6 and abs(new_tracking[1] - 100.0) < 0.6, (
        new_tracking)
    assert e.stats().get("lock") == lock, (
        "lock must remain the fixed offset reference through the reacquire")
