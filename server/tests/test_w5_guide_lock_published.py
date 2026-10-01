"""WP-41 (#204 Rust half): ``GuideStatsSnapshot`` publishes the engine's lock
position through ``astrodeck_native``'s ``stats()``, and the host's
different-star guard (``guide/native.py``'s ``_note_lock``) reads it instead
of guessing.

Before this, two residuals stayed open (issue #204's closing comment):

1. The session's first lock was a brightest-first guess over the host's own
   full-frame star-find (``_find_stars()[0]``) -- wrong whenever the engine's
   own ``select_primary`` skips a saturated brightest star and locks
   something else.
2. The re-lock radius was centred on the host's own last estimate of the
   lock, widened by an UNMEASURED dither magnitude (#219) because the host
   had no way to read the real post-dither value.

Both close by reading ``stats()["lock"]`` (``engine_lock`` in
``_note_lock``) wherever the host used to guess, falling back to the pre-fix
behaviour whenever a wheel (or a test double, like every one in
``test_guider_relock_nearest_star.py`` and ``test_native_guider_session_
resets.py``) publishes no ``"lock"`` key at all.

Every behaviour below was shown RED under a named mutation of
``guide/native.py`` (or, for the end-to-end test, ``astrodeck-native/src/
lib.rs``), run from a byte-for-byte backup and restored byte-identical
afterwards; the observed failure is quoted in each docstring.
"""
from __future__ import annotations

import asyncio
import math
import time

import pytest

import astrodeck.guide.native as nativemod
from astrodeck.guide.native import NativeGuider

# No module-level ``pytestmark = pytest.mark.asyncio``: this file mixes async
# guide-loop tests with one plain sync end-to-end test, and
# ``asyncio_mode = "auto"`` (pyproject.toml) already collects the async ones
# without it.


# --------------------------------------------------------------------- doubles
# Deliberately NOT imported from test_guider_relock_nearest_star.py: a test
# file owns its doubles. The one difference from that file's ``_FieldEngine``
# is the point of this file -- ``stats()`` ALSO carries a ``"lock"`` key, the
# real wheel's new shape (WP-41).


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


class _LockEngine:
    """A ``GuideEngine`` stand-in that plays back one Action per frame AND
    publishes a ``"lock"`` reading per frame (``locks[i]``, clamped to the
    last entry once the script runs out) -- the WP-41 shape. ``guiding`` is
    True throughout, as the real engine reports from the moment it is asked
    to guide, including on the re-acquire frames after a star loss."""

    def __init__(self, actions, locks) -> None:
        self.actions = list(actions)
        self.locks = list(locks)
        self.frames = 0

    def process(self, data, ts, exposure_s):
        self.frames += 1
        if self.actions:
            return self.actions.pop(0)
        return {"action": "idle"}

    def stats(self):
        i = min(self.frames - 1, len(self.locks) - 1) if self.locks else -1
        lock = self.locks[i] if i >= 0 else None
        return {"guiding": True, "settling": False, "recent": [], "lock": lock}

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


def _harness(monkeypatch, actions, fields, locks, **cfg):
    monkeypatch.setattr(nativemod, "_native", _FieldNative(fields))
    engine = _LockEngine(actions, locks)
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

# select_primary's real pick (what the engine actually locked) vs a brighter,
# far-away decoy the host's own brightest-first find would take instead.
_TRUE_LOCK = (500.0, 300.0)
_BRIGHT_DECOY = (100.0, 300.0)


# ------------------------------------------------- residual 2: the first lock


async def test_first_lock_reads_the_published_engine_lock(monkeypatch):
    """#204 residual 2 (closing comment numbering): the host's own
    ``_find_stars`` is brightest-first; if ``select_primary`` locked a
    DIFFERENT, non-brightest star (a saturated brightest star skipped), the
    pre-fix baseline (``stars[0]``) was simply wrong. With a wheel that
    publishes ``lock``, the baseline is the real value instead.

    MUTANT (the pre-fix line restored) ``self._lock_xy = stars[0]`` in place
    of ``self._lock_xy = engine_lock if engine_lock is not None else
    stars[0]`` -- RED, observed verbatim:

        AssertionError: the baseline took the brightest decoy, not the
        engine's real lock: (100.0, 300.0)
    """
    g, _e = _harness(monkeypatch, actions=[_IDLE, _PULSE],
                     fields=[[_BRIGHT_DECOY, _TRUE_LOCK],
                             [_BRIGHT_DECOY, _TRUE_LOCK]],
                     locks=[_TRUE_LOCK])

    await g._guide_loop()

    assert g._lock_xy == _TRUE_LOCK, (
        f"the baseline took the brightest decoy, not the engine's real "
        f"lock: {g._lock_xy}")


async def test_first_lock_is_taken_even_when_the_hosts_own_find_sees_nothing(
        monkeypatch):
    """The host's own ``guide_star_find`` is a SEPARATE detector from the
    engine's (different SNR/saturation parameters can disagree at the
    margins). Pre-fix, an empty host-side find meant no baseline was EVER
    taken, however long the engine had already locked something -- the watch
    would stay unarmed-for-a-baseline forever. With the lock published, the
    host does not need its own find to succeed at all.

    MUTANT (the pre-fix guard restored) ``if not stars: return`` in place of
    ``if engine_lock is None and not stars: return`` -- RED, observed
    verbatim:

        AssertionError: no baseline was taken although the engine already
        holds a lock: None
    """
    g, _e = _harness(monkeypatch, actions=[_IDLE], fields=[[]],
                     locks=[_TRUE_LOCK])

    await g._guide_loop()

    assert g._lock_xy == _TRUE_LOCK, (
        f"no baseline was taken although the engine already holds a lock: "
        f"{g._lock_xy}")


# ------------------------------------------- residual 1: the re-lock radius


async def test_relock_radius_centers_on_the_published_lock(monkeypatch):
    """#204 residual 1: once a dither (or any approximation drift) leaves the
    host's own tracked ``_lock_xy`` away from the engine's TRUE lock, the
    pre-fix radius search -- centred on ``_lock_xy`` -- misses a star that
    really is back at the engine's lock. #219's widening only ever bounded
    this by an ESTIMATE of how far a dither might have moved it; reading the
    real value removes the guesswork entirely.

    Simulates the drift directly (a stale ``_lock_xy`` 20 px from the
    engine's real, unmoved lock -- just past the 15 px default search radius,
    but inside the 120 arcsec/21.8 px jump limit at this test's 5.5
    arcsec/px, so a correct re-lock is counted and narrated rather than
    tripping an unrelated "different star" stop over the narration baseline
    this test deliberately left stale) rather than via an actual dither, to
    isolate just the radius-origin change this test is for.

    MUTANT (the pre-fix origin restored) ``origin = self._lock_xy`` in place
    of ``origin = engine_lock if engine_lock is not None else
    self._lock_xy`` -- RED, observed verbatim:

        AssertionError: a star back at the engine's real lock was not
        recognized: relocks=0, lost=False
    """
    g, _e = _harness(monkeypatch, actions=[_LOST, _PULSE],
                     fields=[[], [(501.0, 300.0)]],
                     locks=[_TRUE_LOCK, _TRUE_LOCK])
    # A stale host-side baseline, 20 px from the engine's real (unmoved)
    # lock -- the drift #219's dither-widening approximation could leave
    # uncorrected.
    g._lock_xy = (481.0, 300.0)

    await g._guide_loop()

    assert g._relocks == 1 and not g._lost, (
        f"a star back at the engine's real lock was not recognized: "
        f"relocks={g._relocks}, lost={g._lost}")


async def test_control_an_unpublished_lock_keeps_the_pre_fix_radius_origin(
        monkeypatch):
    """CONTROL for the test above: a wheel (or test double) that publishes no
    ``"lock"`` key degrades exactly to the pre-fix behaviour -- the SAME stale
    baseline and the SAME nearby star now read as LOST, because the pre-fix
    code had only the host's own (here, deliberately wrong) estimate to
    search around. Proves the new code path is additive, not a replacement
    that could silently change old-wheel behaviour."""
    g, _e = _harness(monkeypatch, actions=[_LOST, _PULSE],
                     fields=[[], [(502.0, 300.0)]],
                     locks=[])   # no entries at all -> stats()["lock"] is None
    g._lock_xy = (350.0, 300.0)

    await g._guide_loop()

    assert g._relocks == 0, (
        "control premise broken: an unpublished lock must not see the "
        "engine's real position")


# ------------------------------------------------------- the real wheel, e2e


native = pytest.importorskip(
    "astrodeck_native",
    reason="WP-41 needs a native wheel rebuilt with the lock field; see the "
           "work package's report for how to build one into a private venv")


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


def test_the_real_wheel_publishes_the_lock_end_to_end():
    """WP-41, the Rust + binding half, exercised through the actual PyO3
    module (not a fake): a rebuilt wheel's ``stats()`` carries the engine's
    real lock, verbatim, through to the Python dict ``_note_lock`` reads.

    Needs a REBUILT native wheel -- an unmodified pre-WP-41 one has no "lock"
    key at all (``_note_lock`` then degrades to its documented pre-fix
    behaviour; see the fake-engine tests above). Skipped outright when no
    wheel is installed at all (``pytest.importorskip``), and SKIPPED (not
    failed) when one is installed but predates this field: a venv's pinned
    wheel (the rig's deploy checks one; see this work package's own "NATIVE
    BUILD RULE") is deliberately never replaced by a worktree's own build, so
    an unrebuilt pin here is an expected, reported gap -- not a regression --
    until the deploy in the plan's 0.3.40 release note rebuilds it.

    MUTANT (``astrodeck-native/src/lib.rs``'s ``stats()`` binding, the
    ``match s.lock { ... }`` block deleted so no "lock" key is set at all) --
    this looks identical to an unrebuilt wheel from here (both omit the key),
    so this test cannot re-prove it by itself; it was shown RED under that
    exact mutant with a wheel freshly rebuilt from it, verbatim:

        AssertionError: the engine had a lock but the binding published none
    """
    e = native.GuideEngine({"image_scale_arcsec": 2.0})
    e.load_calibration(_ident_cal())
    e.begin_guiding()
    if "lock" not in e.stats():
        pytest.skip("installed astrodeck_native wheel predates WP-41's "
                    "'lock' field -- needs the wheel rebuilt and deployed")
    assert e.stats().get("lock") is None, "no star found yet this session"
    a = e.process(_gaussian_frame(64, 64, 32.4, 30.6), 0.0, 2.0)  # lock frame
    assert a["action"] == "idle"
    lock = e.stats().get("lock")
    assert lock is not None, (
        "the engine had a lock but the binding published none")
    assert abs(lock[0] - 32.4) < 0.6 and abs(lock[1] - 30.6) < 0.6, lock
