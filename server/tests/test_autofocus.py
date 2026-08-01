"""Autofocus converges on the simulator's true focus position."""
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus import run_autofocus


async def test_autofocus_converges():
    parts = build_sim_rig()
    state = parts["_rig"]
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()

    result = await run_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=2)
    assert result.success, result.message
    assert abs(result.best_position - state.best_focus) <= 400
    assert result.best_hfr is not None and result.best_hfr < 3.5
    assert await foc.get_position() == result.best_position


async def test_autofocus_is_deterministic():
    """The sim render is seeded from rig+exposure state, so an identical sweep
    lands on exactly the same position every run — this is what makes the
    convergence test reliable rather than flaky (the historical >400u tail came
    from per-frame seeing noise on an unseeded render)."""
    results = []
    for _ in range(3):
        parts = build_sim_rig()
        cam, foc = parts["camera"], parts["focuser"]
        await cam.connect()
        await foc.connect()
        r = await run_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                step=350, steps_each_side=4, binning=2)
        results.append(r.best_position)
    assert len(set(results)) == 1, f"non-deterministic sweep: {results}"


async def test_autofocus_minimum_not_bracketed_fails():
    """When true focus lies entirely outside the swept window, the fitter must
    NOT clip the vertex to a sweep edge and report success (review 4d). It must
    fail with a 'minimum not bracketed' message and return the focuser to its
    start position rather than parking it out of focus at a boundary."""
    parts = build_sim_rig()
    state = parts["_rig"]
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()

    start = await foc.get_position()
    # Push true focus WELL past the top of the sweep (start 19200, ±4*350 =
    # 17800..20600): the curve still opens upward (a>0) but its vertex lands
    # beyond the swept range, so the OLD code would np.clip it to 20600 and report
    # success at an out-of-focus edge. The bracket guard must reject it instead.
    #
    # +3000, not +1500. At +1500 the vertex sat only 100 steps outside the top
    # point, and once the sweep switched to the size metric (which has a real
    # minimum instead of median_hfr's flat 15px-box ceiling) the fit could see
    # the turn and bracket it legitimately — so the test was passing on the old
    # metric's blindness rather than on the guard. Put focus somewhere the sweep
    # genuinely never reaches.
    state.best_focus = start + 3000
    result = await run_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                 step=350, steps_each_side=4, binning=2)
    assert not result.success
    assert "bracket" in result.message.lower(), result.message
    assert result.best_position not in (17800, 20600)  # not parked at a sweep edge
    # focuser returned to where it started (not parked out of focus at a boundary).
    assert await foc.get_position() == start
