# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The per-frame overhead EMA must not count a hop twice (#297, backlog
WP-56 (a)).

THE DEFECT. `SequenceEngine._overhead_ema` is meant to hold only a frame's
cadence minus its exposure (download, quality check, thumbnail); a dither,
an autofocus sweep and a flip are kept out of it by setting
``_frame_had_event = True`` beforehand (P2-1), because those are priced
separately as event costs. A hop between targets is priced separately too
(``_record_event_cost("hop", ...)``, U-07) -- but `_setup_target` never set
the flag, so the FIRST frame after every hop fed the hop's whole wall time
into "per-frame overhead" a second time: once in the hop term, and again
smeared across every remaining frame through the inflated EMA. A rotating
mosaic hops before almost every visit, so this cost real sky: the flip
gate's frame window (exposure + margin + the EMA) widened on the inflated
figure and held the mount idle longer than it needed to, and the mosaic
meridian rule's pre-flip room read smaller than it really was.

THE FIX. `_setup_target` sets ``_frame_had_event = True`` at the end of
every setup (a hop, and a wait that released into one), exactly as the
dither/AF/flip blocks already do for themselves, so `_record_frame` -- which
reads the flag before resetting it (`_begin_frame`'s NB) -- excludes the
hop's wall time the same way.

THE NUMBERS MATCH THE ISSUE'S OWN CLOCKED-SIMULATOR PROBE (#297's evidence):
a 150 s hop into an otherwise back-to-back cadence moved the EMA from its
12.0 s seed (``DEFAULT_OVERHEAD_S``) to 25.8 s before the fix -- exactly
``0.9 * 12.0 + 0.1 * 150.0`` (``OVERHEAD_EMA_ALPHA`` = 0.1). This test
reproduces that arithmetic directly: a virtual clock (`time.time` and
`time.monotonic` both read it, as `_group_harness.py`'s does) drives
`_setup_target` and `_record_frame` with no real delay anywhere, so every
sample is exact down to the float.
"""
from __future__ import annotations

import time as _realtime

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import DEFAULT_OVERHEAD_S, OVERHEAD_EMA_ALPHA


class _Clock:
    """Stand-in for the ``time`` module inside the engine: both ``time()``
    and ``monotonic()`` read one virtual clock (as `_group_harness.py`'s
    does), so a wall-clock gap and a monotonic gap always agree -- which is
    what lets a hop's ``time.monotonic()`` cost and a frame's
    ``time.time()`` cadence be compared directly, as the engine itself
    does."""

    def __init__(self, t0: float) -> None:
        self.t = t0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s

    def __getattr__(self, name):
        return getattr(_realtime, name)


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock(1_000_000.0)
    monkeypatch.setattr(engine_mod, "time", c)
    return c


SLEW_S = 150.0      # the hop, virtual -- the issue's own reproduction figure
EXPOSURE_S = 30.0   # one frame's exposure, virtual
SLOW_FRAME_S = 5.0  # a genuine (non-hop) per-frame overhead, virtual


class _Tel:
    connected = True

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock

    async def is_parked(self) -> bool:
        return False

    async def unpark(self) -> None: ...

    async def set_tracking(self, on: bool) -> None: ...

    async def get_tracking(self) -> bool:
        return True

    async def slew(self, ra: float, dec: float) -> None:
        self.clock.advance(SLEW_S)


class _Hub:
    def __init__(self, clock: _Clock) -> None:
        self.devices = {"telescope": _Tel(clock), "focuser": object()}
        self.guider = None
        self.site: dict = {}

    def _check_solar(self, ra, dec, *, force=False) -> None:
        return None

    def require(self, role: str):
        return self.devices[role]


def _target(name: str, ra: float = 6.0, *, count: int = 2) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=20.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=EXPOSURE_S,
                                      count=count)])


def _engine(clock: _Clock, targets) -> SequenceEngine:
    e = SequenceEngine(_Hub(clock))
    e._cfg = None
    e.plan = SequencePlan(name="overhead", guide=False, meridian_flip=False,
                          safety_check=False, autofocus_every=0,
                          dither_every=0, targets=targets)
    return e


def _shoot(e: SequenceEngine, clock: _Clock, ti: int, si: int, target: Target,
          *, gap_s: float) -> None:
    """One capture of ``target``'s ``si``'th frame: begin it, let ``gap_s``
    of virtual time pass (the whole cadence from the LAST frame's end, this
    capture's download/quality/thumbnail included), then record it -- the
    same three calls `_run_step` makes around a real exposure."""
    step = target.steps[0]
    e._begin_frame(ti, si, EXPOSURE_S)
    clock.advance(gap_s)
    key = f"{target.id}:{step.id}"
    e._record_frame(key, si, target, step, {})


async def test_a_hop_between_targets_does_not_feed_the_overhead_ema(clock):
    """A, two back-to-back frames (zero real overhead between them); a hop
    to B of SLEW_S; B's first frame, ALSO back-to-back (zero real overhead
    once the goto lands). The hop must not move the EMA at all -- it is
    priced once, as the hop event cost, not a second time as "per-frame
    overhead".

    RED ON THE TREE BEFORE THE FIX (observed) -- the hop's whole wall time
    read as this frame's overhead, moving the EMA exactly the way #297's own
    probe measured it:
        AssertionError: the hop leaked into the overhead EMA: 25.8, want
        12.0 (unchanged)

    MUTANT "the fix reverted" (the ``self._frame_had_event = True`` this
    change added at the end of `_setup_target` deleted): RED, same failure
    as above -- restores the exact pre-fix arithmetic,
    ``0.9 * 12.0 + 0.1 * 150.0 == 25.8``.
    """
    a, b = _target("A"), _target("B", 6.1)
    e = _engine(clock, [a, b])
    assert e._overhead_ema == DEFAULT_OVERHEAD_S, (
        f"premise: the EMA starts at its seed: {e._overhead_ema}")

    await e._setup_target(0, a)                  # the opening acquisition
    _shoot(e, clock, 0, 0, a, gap_s=EXPOSURE_S)   # A's first frame: no prior
    _shoot(e, clock, 0, 1, a, gap_s=EXPOSURE_S)   # A's second: zero overhead
    assert e._overhead_ema == DEFAULT_OVERHEAD_S, (
        f"premise: two back-to-back frames left the EMA at its seed: "
        f"{e._overhead_ema}")
    ema_before_hop = e._overhead_ema

    await e._setup_target(1, b)                  # THE HOP (SLEW_S virtual)
    _shoot(e, clock, 1, 0, b, gap_s=EXPOSURE_S)   # B's first: zero overhead
    #                                               once the goto has landed

    want = round(ema_before_hop, 6)
    got = round(e._overhead_ema, 6)
    leaked = round((1 - OVERHEAD_EMA_ALPHA) * ema_before_hop
                    + OVERHEAD_EMA_ALPHA * SLEW_S, 6)
    assert got == want, (
        f"the hop leaked into the overhead EMA: {got}, want {want} "
        f"(unchanged); that is {leaked} if the whole {SLEW_S:.0f} s hop "
        f"counted as this frame's overhead")


async def test_control_a_genuine_slow_frame_still_moves_the_ema(clock):
    """CONTROL. A real per-frame slowdown that carries NO event (no hop, no
    dither, no AF, no flip) must still move the EMA -- otherwise a fix that
    simply stopped the EMA from ever updating would pass the test above for
    the wrong reason.

    Mutant "overhead never recorded" (`_record_frame`'s EMA update deleted
    outright): RED -
        AssertionError: a genuine slow frame never moved the EMA: 12.0,
        want 11.3
    """
    a = _target("A", count=3)
    e = _engine(clock, [a])
    await e._setup_target(0, a)
    _shoot(e, clock, 0, 0, a, gap_s=EXPOSURE_S)                  # no prior
    _shoot(e, clock, 0, 1, a, gap_s=EXPOSURE_S)                  # zero overhead
    ema_before = e._overhead_ema
    _shoot(e, clock, 0, 2, a, gap_s=EXPOSURE_S + SLOW_FRAME_S)   # genuine lag

    want = round((1 - OVERHEAD_EMA_ALPHA) * ema_before
                 + OVERHEAD_EMA_ALPHA * SLOW_FRAME_S, 6)
    got = round(e._overhead_ema, 6)
    assert got == want, (
        f"a genuine slow frame never moved the EMA: {got}, want {want}")
    assert got != ema_before, (
        f"a genuine slow frame never moved the EMA: {got}, "
        f"want {want}")
