# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""P3 gate (P3-T2): end-to-end proof that the non-default guide algorithms
(Lowpass, Lowpass2, ZFilter) are wired all the way from the AlgoKind
selection strings through to a converging sim guide loop -- not just
unit-tested in isolation (see
native/crates/astro-guide/tests/lowpass_zfilter_golden.rs for the algorithm
math itself). Mirrors test_native_guider_e2e.py's
test_native_guider_converges_on_sim, but pins non-default algorithm pairs
via NativeGuider's config dict -> GuideEngine's ra_algorithm/dec_algorithm
keys (astrodeck/guide/native.py _build_engine_config -> astrodeck-native's
parse_algo_kind -> engine.rs's make_algo). Threshold-only assertions
(dossier §0/§17 convergence precedent): guide RMS is
wall-clock/noise-sensitive, so this proves "guides and holds", not a golden
numeric trajectory.

Pairs: (RA=ZFilter, Dec=Lowpass2) is the brief's original P3-gate pair;
(RA=Lowpass, Dec=ResistSwitch) was added in the P3-T2 fix round (review
coverage item 4) so the "lowpass" string is also exercised end to end.
"""
import asyncio

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")

#: The sim's drift is WALL-CLOCK-rate, not frame-rate (devices/sim.py drives
#: it off elapsed real seconds, so ASTRODECK_FAST_TEST collapsing the
#: per-frame dwell to ~0 buys more FRAMES per real second, not more drift).
#: At this test's 0.15 px/s drift and 2.0 "/px scale, an UNCORRECTED guider
#: crosses the 2.0" bound at 1.0 px / 0.15 px/s ~= 6.7 real seconds --
#: measured directly against the named mutant below (guiding disabled):
#: rms_total was 1.43 at t+1s after start_guiding() and climbed past 2.0
#: between t+6s and t+7s. A FIRST check made immediately after
#: start_guiding() returns proves nothing either way (``recent`` is still
#: empty, rms_total==0.0, whether or not corrections ever fire) -- so
#: `_wait_for_rms_below` must let at least this much real time pass before
#: its first check, same as the old fixed sleep did, or a broken corrector
#: would pass by sheer lack of elapsed time rather than by holding.
_CONVERGE_MIN_OBSERVE_S = 8.0
#: How long `_wait_for_rms_below` will keep polling past the minimum above
#: before giving up (#675 part 1). The old fixed `asyncio.sleep(6.0)` (this
#: test used to check only once, right at 6s -- inside the margin above)
#: assumed the sim guide loop keeps pace with wall clock, which it does not
#: under `-n auto` CPU saturation: a baseline full-suite run observed
#: `assert 79.51 < 2.0` here. 30s total (22s of EXTRA patience past the
#: 8s minimum) is generous against load, and still bounded, so a
#: guiding-disabled mutant fails this poll instead of hanging the test (see
#: the mutant note below).
_CONVERGE_DEADLINE_S = 30.0
#: How often to re-check rms_total once past the minimum. Cheap (`stats()`
#: just reads the engine's cached numbers, no I/O), so a short interval
#: costs nothing and buys the loop more chances to have caught up by the
#: next check.
_CONVERGE_POLL_S = 0.25


async def _wait_for_rms_below(guider, bound: float, *,
                              min_observe_s: float = _CONVERGE_MIN_OBSERVE_S,
                              deadline_s: float = _CONVERGE_DEADLINE_S,
                              poll_s: float = _CONVERGE_POLL_S):
    """Poll ``guider.stats()`` for ``rms_total`` under ``bound``, instead of a
    fixed sleep-then-check (#675 part 1). Returns the LAST stats seen,
    converged or not, so the caller's own assertion reports the real number
    either way.

    First lets ``min_observe_s`` of REAL time pass unconditionally (see the
    module-level comment above for why: the drift this test injects is
    wall-clock-rate, so an instant check cannot tell a working corrector from
    a disabled one). From there it keeps polling, instead of checking once,
    until either ``rms_total`` drops under ``bound`` or ``deadline_s`` of
    total wall clock has passed -- buying the loop more real time to catch up
    under CPU load without raising ``bound``, the SAME convergence threshold
    the test asserts.

    Bounded by ``deadline_s`` so a guider that can genuinely never converge
    (the named mutant: corrections disabled) fails this poll instead of
    hanging the test -- ``loop.time()`` is read fresh each iteration, so a
    slow process still gets its full wall-clock allowance, it just spends it
    polling instead of sleeping blind."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_s
    await asyncio.sleep(min_observe_s)
    st = guider.stats()
    while True:
        st = guider.stats()
        if st.rms_total < bound or loop.time() >= deadline:
            return st
        await asyncio.sleep(poll_s)


async def _assert_converges(ra_algorithm: str, dec_algorithm: str) -> None:
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    # Same modest drift as the default-algorithm e2e test, so each pair is a
    # like-for-like proof that the non-default algorithms also hold.
    rig["_rig"].guide_drift_px_s = 0.15
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2,
                                       "ra_algorithm": ra_algorithm,
                                       "dec_algorithm": dec_algorithm},
                     profile_id=f"test-{ra_algorithm}-{dec_algorithm}")
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)  # calibrate + settle
    assert await g.is_active()
    # Named mutant (#675 part 1): `_dispatch`'s pulse branch
    # (astrodeck/guide/native.py, `if kind in ("pulse", "pulse_pair"):`) had
    # its `await self._pulse(action)` replaced with `await asyncio.sleep(0)`
    # -- corrections are computed but never sent to the mount, while the
    # loop's own cooperative-yield shape is left alone (replacing it with a
    # bare `pass` instead starves the event loop outright under
    # ASTRODECK_FAST_TEST: nothing else in the per-frame cycle actually
    # suspends, so the whole process hangs rather than failing -- caught by
    # running the mutant before trusting it). RED, observed (two runs, one
    # per algorithm pair, each within the deadline, 63.82s total for both):
    #     AssertionError: rms_total=9.06
    #     assert 9.06 < 2.0
    #     AssertionError: rms_total=8.75
    #     assert 8.75 < 2.0
    # -- raised by the final assertion below once `_wait_for_rms_below` gives
    # up at its deadline (it does not hang: the poll is bounded by
    # _CONVERGE_DEADLINE_S regardless of whether rms_total ever drops).
    st = await _wait_for_rms_below(g, 2.0)
    assert st.guiding
    assert st.rms_total < 2.0, f"rms_total={st.rms_total}"
    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_native_guider_converges_with_zfilter_ra_and_lowpass2_dec():
    await _assert_converges("z_filter", "lowpass2")


@pytest.mark.asyncio
async def test_native_guider_converges_with_lowpass_ra():
    await _assert_converges("lowpass", "resist_switch")
