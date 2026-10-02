# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A zero dither distance skips its settle; settle fields reach a run (#560,
backlog WP-58, plan ruling owner-approved 2026-09-30).

Two claims nothing kept, found while reading what a flow's run actually
dithers with (#506 H4-ROUTES-A):

  1. The GUIDER sheet's "Dither pixels" stepper carries the note "0 turns
     dithering off." The engine never read 0 that way: the per-frame cadence
     block called ``guider.dither(0)`` on schedule regardless, which still
     opened the guider's settle window and paid the full wait (1.5 px held
     10 s on the native engine) for a move that never happened. The same
     call sat behind the ``dither`` sequence-instruction action.

  2. SETTLE PX / SETTLE S / TIMEOUT S on the same card were local UI state,
     posted only with a manual DITHER NOW press, and never saved -- a
     scheduled run's dithers always fell through to the guider's own
     built-in rule, whatever those three fields said.

``GuideConfig.dither_settle_pixels``/``_time_s``/``_timeout_s`` (config.py)
persist the three fields; ``SequenceEngine._dither_settle_override`` (engine.py)
reads them into the ``{"pixels", "time", "timeout"}`` shape ``dither()``
already accepts (UX-24), and both call sites now gate on
``self._policy.dither_pixels > 0`` instead of firing on the cadence alone.

A third thing, not named in #560 itself, surfaced while wiring the second:
every dither call is wrapped in ``_bounded(..., GUIDE_OP_TIMEOUT_S, ...)``, a
flat 120 s cap, but ``dither_settle_timeout_s`` lets an operator configure up
to 600 s. Left alone, a timeout past 120 s would never get the chance to
fire -- ``_bounded`` would cancel the dither first and raise a SafetyAbort
that tears down the WHOLE NIGHT, which is worse than the field doing
nothing. ``SequenceEngine._dither_wait_timeout_s`` widens the outer bound to
match a configured timeout (plus phd2.py's own 30 s margin) so it is never
the one that fires first.

Real engine runs on the sim rig with a recording guider double, exactly
test_dither_reset_on_acquisition.py's shape: plan.guide is off, so no guide
start runs, and a connected guider is all the dither gate asks for.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import config_store
from astrodeck.devices.sim import SimTelescope
from astrodeck.guide.base import Guider, GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.instructions import FiredAction
from astrodeck.sequence.policy import resolve_policy


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class _DitherGuider(Guider):
    """Connected, never active, records every dither call -- pixels AND the
    settle dict it was handed -- into the shared log, so a test can tell both
    "did it dither at all" and "what settle did it carry" apart."""

    name = "dither recorder"

    def __init__(self, log: list) -> None:
        self.connected = True
        self.log = log

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None: ...

    async def stop_guiding(self) -> None: ...

    async def is_active(self) -> bool:
        return False

    async def dither(self, pixels: float = 3.0, settle=None) -> None:
        self.log.append(("dither", pixels, settle))

    def stats(self) -> GuideStats:
        return GuideStats()


def _target(name: str, ra: float, *steps: tuple[str, int]) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=20.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=0.02, count=n)
                         for f, n in steps])


def _dithers(log: list) -> list[tuple]:
    return [e for e in log if isinstance(e, tuple) and e[0] == "dither"]


async def _run(hub, targets, *, dither_every: int, dither_pixels: float) -> list:
    """Run the plan to completion; return the shared dither-recorder log."""
    log: list = []
    hub.guider = _DitherGuider(log)
    e = SequenceEngine(hub)
    e.start(SequencePlan(name="w12-dither", guide=False, meridian_flip=False,
                         safety_check=False, autofocus_every=0,
                         dither_every=dither_every, dither_pixels=dither_pixels,
                         targets=targets))
    await asyncio.wait_for(asyncio.shield(e._task), 60)
    assert e.state.get("state") == "complete", (
        f"premise: the run finished: {e.state.get('state')} "
        f"{e.state.get('detail')} {log}")
    return log


# ============================================================ the cadence block


async def test_zero_pixels_skips_the_dither_and_its_settle(sim_hub):
    """Mutant (drop the ``and self._policy.dither_pixels > 0`` clause from the
    cadence ``if`` in ``_run_step``): RED -
        AssertionError: a 0 px cadence dither still called the guider:
        [('dither', 0.0, None), ('dither', 0.0, None), ('dither', 0.0, None)]
    """
    log = await _run(sim_hub, [_target("A", 6.00, ("L", 4))],
                     dither_every=1, dither_pixels=0.0)
    assert _dithers(log) == [], \
        f"a 0 px cadence dither still called the guider: {_dithers(log)}"


async def test_nonzero_pixels_still_dithers_on_cadence(sim_hub):
    """CONTROL for the test above: the gate is 0-specific, not a guard that
    silently swallows every cadence dither."""
    log = await _run(sim_hub, [_target("A", 6.00, ("L", 4))],
                     dither_every=1, dither_pixels=3.0)
    assert len(_dithers(log)) >= 1, \
        f"premise: a nonzero cadence dither still happens: {log}"


async def test_no_configured_settle_still_passes_none(sim_hub):
    """Regression guard on ``_dither_settle_override``'s empty-dict collapse:
    an engine with none of the three settle fields set must keep sending
    ``settle=None`` -- the pre-#560 behaviour every guider backend's default
    path depends on -- not an all-``None`` dict that looks set but is not.

    Mutant (``return settle`` instead of ``return settle or None``): RED -
        AssertionError: an unset settle config sent {} instead of None
    """
    log = await _run(sim_hub, [_target("A", 6.00, ("L", 2))],
                     dither_every=1, dither_pixels=3.0)
    dithers = _dithers(log)
    assert dithers, "premise: at least one dither happened"
    assert dithers[0][2] is None, \
        f"an unset settle config sent {dithers[0][2]!r} instead of None"


async def test_persisted_settle_reaches_the_cadence_dither(sim_hub, monkeypatch):
    """The second half of #560: SETTLE PX/S/TIMEOUT, once saved to
    ``GuideConfig``, must ride every scheduled dither -- not only a manual
    DITHER NOW press.

    Mutant (call ``self.hub.guider.dither(self._policy.dither_pixels)`` with
    no settle argument, the pre-fix call): RED -
        AssertionError: the persisted settle did not reach the cadence
        dither: dithers[0]=('dither', 3.0, None)
    """
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.guide, "dither_settle_pixels", 1.5)
    monkeypatch.setattr(cfg.guide, "dither_settle_time_s", 8.0)
    monkeypatch.setattr(cfg.guide, "dither_settle_timeout_s", 45.0)
    log = await _run(sim_hub, [_target("A", 6.00, ("L", 2))],
                     dither_every=1, dither_pixels=3.0)
    dithers = _dithers(log)
    assert dithers, "premise: at least one dither happened"
    assert dithers[0] == ("dither", 3.0,
                          {"pixels": 1.5, "time": 8.0, "timeout": 45.0}), \
        f"the persisted settle did not reach the cadence dither: dithers[0]={dithers[0]!r}"


async def test_a_partial_settle_omits_the_unset_fields(sim_hub, monkeypatch):
    """Only TIMEOUT set (the field the native backend actually honours,
    UX-24): the dict must carry only that key, exactly like a DITHER NOW press
    with the other two boxes left blank -- so the guider still falls back to
    its own default pixels/time rather than being handed an explicit None it
    would have to special-case."""
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.guide, "dither_settle_timeout_s", 45.0)
    log = await _run(sim_hub, [_target("A", 6.00, ("L", 2))],
                     dither_every=1, dither_pixels=3.0)
    dithers = _dithers(log)
    assert dithers and dithers[0][2] == {"timeout": 45.0}, \
        f"a partial settle config leaked unset fields: {dithers[0][2]!r}"


# ===================================== the outer wait must not preempt a long settle
#
# Found while wiring the settle override above, not named in #560 itself: a
# configured ``dither_settle_timeout_s`` can ask for up to 600 s, but every
# dither call is wrapped in ``_bounded(..., GUIDE_OP_TIMEOUT_S, ...)`` -- a
# flat 120 s cap shared with stop-guiding and other quick guider ops. Without
# widening that outer bound, a timeout configured past 120 s could never get
# the chance to fire: `_bounded` would cancel the dither first and raise a
# SafetyAbort that tears down the WHOLE NIGHT, not just the dither -- making
# the very fields this WP ships actively dangerous to set past 120 s, which
# is worse than the pre-#560 "the fields do nothing" bug.


def test_dither_wait_timeout_defaults_to_the_shared_cap():
    from astrodeck.sequence.engine import GUIDE_OP_TIMEOUT_S, SequenceEngine

    assert SequenceEngine._dither_wait_timeout_s(None) == GUIDE_OP_TIMEOUT_S
    assert SequenceEngine._dither_wait_timeout_s({"pixels": 1.5}) == GUIDE_OP_TIMEOUT_S


def test_dither_wait_timeout_never_shrinks_below_the_shared_cap():
    """A short configured settle timeout must not narrow the outer bound below
    the shared cap every other guider op still relies on."""
    from astrodeck.sequence.engine import GUIDE_OP_TIMEOUT_S, SequenceEngine

    assert SequenceEngine._dither_wait_timeout_s({"timeout": 5.0}) == GUIDE_OP_TIMEOUT_S


def test_dither_wait_timeout_widens_past_the_cap_for_a_long_settle_timeout():
    """The +30 s margin matches ``guide/phd2.py``'s own
    ``timeout=s["timeout"] + 30`` wait, so the outer bound is never the one
    that fires first.

    Mutant (``return GUIDE_OP_TIMEOUT_S`` unconditionally, dropping the
    ``max(...)`` widening): RED -
        AssertionError: assert 120.0 == 330.0
    """
    from astrodeck.sequence.engine import SequenceEngine

    assert SequenceEngine._dither_wait_timeout_s({"timeout": 300.0}) == 330.0


async def test_the_cadence_dither_actually_passes_the_widened_timeout(sim_hub, monkeypatch):
    """The helper above is worthless if the call site still hands `_bounded`
    a hardcoded cap regardless of what it returns -- this calls the real
    cadence path and watches what `_bounded` was actually given.

    Mutant (revert the cadence call site to
    ``_bounded(..., GUIDE_OP_TIMEOUT_S, "dither")``): RED -
        AssertionError: the cadence dither did not widen _bounded's timeout:
        [120.0]
    """
    import astrodeck.sequence.engine as engine_mod

    seen: list[float] = []
    real_bounded = engine_mod._bounded

    async def _spy(awaitable, timeout_s, what, **kw):
        if what == "dither":
            seen.append(timeout_s)
        return await real_bounded(awaitable, timeout_s, what, **kw)

    monkeypatch.setattr(engine_mod, "_bounded", _spy)
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.guide, "dither_settle_timeout_s", 300.0)
    await _run(sim_hub, [_target("A", 6.00, ("L", 2))],
              dither_every=1, dither_pixels=3.0)
    assert seen and seen[0] == 330.0, \
        f"the cadence dither did not widen _bounded's timeout: {seen}"


# ==================================================== the instruction action


def _engine(hub, *, dither_pixels: float) -> SequenceEngine:
    """A bare engine with just enough state for ``_dispatch_actions`` -- no
    full run, matching test_dither_settle_hold.py's direct-call style for the
    private helpers this fix touches."""
    cfg = config_store.cfg()
    e = SequenceEngine(hub)
    e.plan = SequencePlan(guide=False, dither_pixels=dither_pixels)
    e._cfg = cfg
    e._policy = resolve_policy(e.plan, cfg)
    return e


async def test_instruction_dither_skips_on_zero_pixels(sim_hub):
    """The ``dither`` sequence-instruction action shares the exact bug: it
    always dithered at ``self._policy.dither_pixels`` with no regard for 0.

    Mutant (drop the ``and self._policy.dither_pixels > 0`` clause from the
    ``elif fa.action == "dither"`` branch): RED -
        AssertionError: a 0 px instruction dither still called the guider:
        [('dither', 0.0, None)]
    """
    log: list = []
    sim_hub.guider = _DitherGuider(log)
    e = _engine(sim_hub, dither_pixels=0.0)
    fa = FiredAction("i1", "dither", "", "info")
    await e._dispatch_actions([fa], _target("A", 6.00, ("L", 1)), None)
    assert _dithers(log) == [], \
        f"a 0 px instruction dither still called the guider: {_dithers(log)}"


async def test_instruction_dither_carries_the_persisted_settle(sim_hub, monkeypatch):
    """The instruction path reaches the SAME ``_dither_settle_override`` as
    the cadence block -- one settle source, not two that can drift apart.

    Mutant (the instruction branch's ``dither()`` call with no settle
    argument): RED -
        AssertionError: the persisted settle did not reach the instruction
        dither: [('dither', 4.0, None)]
    """
    cfg = config_store.cfg()
    monkeypatch.setattr(cfg.guide, "dither_settle_pixels", 2.0)
    log: list = []
    sim_hub.guider = _DitherGuider(log)
    e = _engine(sim_hub, dither_pixels=4.0)
    fa = FiredAction("i1", "dither", "", "info")
    await e._dispatch_actions([fa], _target("A", 6.00, ("L", 1)), None)
    assert log == [("dither", 4.0, {"pixels": 2.0})], \
        f"the persisted settle did not reach the instruction dither: {log}"


# ================================================================ GuideConfig


def test_settle_fields_default_to_none():
    """The convention this file names explicitly: unset means "the guider's
    own default", never a 0 the guider would obey."""
    from astrodeck.config import GuideConfig

    g = GuideConfig()
    assert g.dither_settle_pixels is None
    assert g.dither_settle_time_s is None
    assert g.dither_settle_timeout_s is None


def test_settle_fields_round_trip_through_the_model():
    """The whole-block PUT the GUIDER sheet already does for ``dither_pixels``
    (guider.tsx's header comment) now carries these three the same way."""
    from astrodeck.config import GuideConfig

    g = GuideConfig(dither_settle_pixels=1.5, dither_settle_time_s=8.0,
                    dither_settle_timeout_s=60.0)
    again = GuideConfig.model_validate(g.model_dump())
    assert again.dither_settle_pixels == 1.5
    assert again.dither_settle_time_s == 8.0
    assert again.dither_settle_timeout_s == 60.0
