"""What a native autofocus sweep costs today, so a change to it can be graded.

Two halves, and the first is not optional. `_focus_clock` measures a nine-point
sweep in seconds that never elapse -- if the virtual clock or the cost
accounting is wrong, every number the second half prints is a confident lie
about a run that never happened. So the clock is tested against arithmetic
anybody can do in their head (two 5 s sleeps concurrently finish at 5, in
series at 10, and neither costs a fifth of a real second), and the cost
accounting is tested by moving one constant and checking the bill moves with
it.

The second half runs the four scenarios and asserts STRUCTURAL facts about the
status quo: not "the sweep takes 690 s" -- which is a number in a cost model
and would have to be edited every time the model improves -- but "every
measured frame pays a full-frame Rust pass", "the turn-round wastes exactly one
exposure", "the run ends on the wrong side of the backlash". Those are the
things the three lanes of the autofocus-efficiency plan are meant to change,
and each of these assertions is written to FAIL when its lane lands. That is
the point of them: a status-quo test that survives the change it was written
for measured nothing.

Run with `-s` to see the report table.

Sync test functions on purpose, every one of them. The suite runs
pytest-asyncio in auto mode, so an `async def` here would be run on ITS loop,
and the whole harness is about running on a loop of our own.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time

import pytest

from astrodeck import providers

import _focus_clock as clock          # noqa: E402 - sibling helper

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE,
    reason="astrodeck_native wheel not installed")


# ---------------------------------------------------------------- the clock

def test_concurrent_sleeps_finish_together_on_the_virtual_clock():
    """Two 5 s sleeps started together end at virtual time 5, not 10. If this
    were wrong the harness would report a sweep with NO pipelining at all --
    the exact property it exists to measure."""
    async def body():
        loop = asyncio.get_running_loop()
        await asyncio.gather(asyncio.sleep(5), asyncio.sleep(5))
        return loop.time()

    finished, elapsed = clock.run_on_virtual_clock(body)
    assert finished == pytest.approx(5.0)
    assert elapsed == pytest.approx(5.0)


def test_sequential_sleeps_add_up_on_the_virtual_clock():
    async def body():
        await asyncio.sleep(5)
        await asyncio.sleep(5)
        return asyncio.get_running_loop().time()

    finished, elapsed = clock.run_on_virtual_clock(body)
    assert finished == pytest.approx(10.0)
    assert elapsed == pytest.approx(10.0)


def test_the_virtual_clock_costs_no_wall_clock():
    """Ten virtual seconds in under a fifth of a real one. Without this the
    suite could not afford to run four nine-point sweeps, and the harness would
    have to fake the sweep instead of running the real loop."""
    async def body():
        await asyncio.sleep(5)
        await asyncio.sleep(5)

    started = time.monotonic()
    clock.run_on_virtual_clock(body)
    assert time.monotonic() - started < 0.2


def test_a_cancelled_timer_does_not_advance_the_clock():
    """A cancelled `asyncio.sleep` at the head of the queue is not a reason to
    jump: `wait_for` leaves one behind on every call that completes in time,
    and jumping to it would charge the run for a timeout that never fired."""
    async def body():
        await asyncio.wait_for(asyncio.sleep(1), timeout=600)
        return asyncio.get_running_loop().time()

    finished, _elapsed = clock.run_on_virtual_clock(body)
    assert finished == pytest.approx(1.0)


def test_asyncio_to_thread_is_restored_after_a_scenario():
    """The harness replaces a stdlib attribute for the duration of a run. If it
    ever failed to put it back, every test that ran afterwards would silently
    stop offloading -- and would keep passing."""
    before = asyncio.to_thread
    clock.run_scenario(clock.sparse_83())
    assert asyncio.to_thread is before


def test_the_measurement_stubs_are_restored_after_a_scenario():
    from astrodeck.focus import native as host
    before = (host.focus_size, host._native.detect_and_measure)
    clock.run_scenario(clock.sparse_83())
    assert (host.focus_size, host._native.detect_and_measure) == before


# ------------------------------------------------------------- the accounting

def test_cpu_time_is_billed_on_the_virtual_clock():
    """Doubling what a Rust pass costs must move `cpu_s` and `wall_s`, not just
    a counter. A harness whose "cost model" the run does not actually pay would
    report the same wall time for every change to it."""
    base = clock.run_scenario(clock.sparse_83())
    sc = clock.sparse_83()
    sc.cost = dataclasses.replace(sc.cost, rust_base_s=sc.cost.rust_base_s * 2)
    doubled = clock.run_scenario(sc)
    assert doubled.cpu_s > base.cpu_s + 200
    assert doubled.wall_s > base.wall_s + 200


def test_the_exposure_hides_under_the_measurement():
    """THE PIPELINE, on the clock. Eleven exposures of 6 s + 2 s download is 88 s
    of camera work; the run is minutes long, and all but the probe's and the
    validation's exposures happen while the CPU is busy. So the camera's idle
    time must be far less than the wall clock minus its busy time would be if
    nothing overlapped."""
    r = clock.run_scenario(clock.sparse_83())
    frame_s = 6.0 + 2.0
    serial = r.exposures * frame_s + r.cpu_s
    # Neither the probe nor the validation frame is ever speculated (by rule),
    # so at most `exposures - 2` can hide. Demand most of them.
    hideable = r.exposures - 2 - r.wasted_frames
    assert serial - r.wall_s > 0.7 * hideable * frame_s, (
        f"wall {r.wall_s:.1f} against {serial:.1f} fully serial -- only "
        f"{serial - r.wall_s:.1f} s of {hideable} exposures overlapped")


def test_a_smaller_frame_costs_less_to_measure():
    """The pixel-fraction accounting, exercised directly. Nothing in the loop
    crops anything today (`rust_mean_frac` is 1.00 everywhere), so this is the
    only place the harness can prove it would notice one."""
    sc = clock.sparse_83()
    bill = clock.Bill()
    detect, size_fn, rust, size, measured = clock.measurement_stubs(sc, bill)

    full = clock.PositionedArray(sc.cost.sensor_shape, dtype="uint16")
    full.physical_pos = float(sc.focus)
    full.exposure_index = 0
    detect(full)
    size_fn(full)
    whole = bill.take()

    window = full[::2, ::2]                      # a quarter of the pixels
    assert window.physical_pos == float(sc.focus)
    assert window.exposure_index == 0
    detect(window)
    size_fn(window)
    cropped = bill.take()

    assert cropped < whole / 2
    assert [p.fraction for p in rust] == pytest.approx([1.0, 0.25])
    assert [p.fraction for p in size] == pytest.approx([1.0, 0.25])
    assert measured == {0}


def test_a_crop_of_a_sparse_field_falls_below_the_per_point_minimum():
    """The fallback case the plan has to handle: `focus_size` caps its voters
    near 25, so a quarter-frame window of a 34-star field yields one or two --
    under MIN_STARS_PER_POINT, which sends that point back to the whole frame.
    The harness must be able to produce it."""
    from astrodeck.focus.autofocus import MIN_STARS_PER_POINT

    sc = clock.sparse_83()
    _detect, size_fn, _r, _s, _m = clock.measurement_stubs(sc, clock.Bill())
    arr = clock.PositionedArray(sc.cost.sensor_shape, dtype="uint16")
    arr.physical_pos = float(sc.focus)
    arr.exposure_index = 0
    _hfr, voters = size_fn(arr[::4, ::4])        # 1/16 of the frame
    assert voters < MIN_STARS_PER_POINT, voters


def test_the_focuser_models_backlash_in_one_direction_only():
    """IN takes the slack up and lands on the commanded position; OUT leaves
    the tube a backlash short; a reversal shorter than the backlash turns the
    motor and does not move the tube at all."""
    cost = clock.Cost()
    foc = clock.ClockedFocuser(11200, cost)

    async def body():
        await foc.move_to(12600)                 # out
        assert foc.physical_position == 12600 - cost.backlash_steps
        await foc.move_to(12250)                 # in
        assert foc.physical_position == 12250
        await foc.move_to(12260)                 # out, shorter than the slack
        assert foc.physical_position == 12250
        return foc.reversals

    reversals, _elapsed = clock.run_on_virtual_clock(body)
    assert reversals == 2
    assert [m.direction for m in foc.moves] == ["out", "in", "out"]
    assert foc.total_steps == 1400 + 350 + 10
    # And what the focuser REPORTS is the commanded count -- that is all a real
    # EAF can report, which is the whole reason `physical_position` sits beside
    # it rather than replacing it.
    assert foc.commanded == 12260
    assert foc.physical_position != foc.commanded


def test_a_move_costs_a_settle_plus_the_distance():
    cost = clock.Cost()
    foc = clock.ClockedFocuser(11200, cost)

    async def body():
        await foc.move_to(11900)                 # 700 steps at 700 steps/s
        return asyncio.get_running_loop().time()

    finished, _e = clock.run_on_virtual_clock(body)
    assert finished == pytest.approx(cost.move_fixed_s + 1.0)


# ----------------------------------------------------------- the status quo

@pytest.fixture(scope="module")
def reports() -> dict[str, clock.Report]:
    out = {make().name: clock.run_scenario(make()) for make in clock.SCENARIOS}
    print("\n" + clock.format_table(list(out.values())))
    return out


def test_every_scenario_still_finds_focus(reports):
    """The floor under every other assertion here. A harness whose sweeps fail
    is measuring the cost of failing."""
    for make in clock.SCENARIOS:
        sc = make()
        r = reports[sc.name]
        assert r.success, f"{sc.name}: {r.message}"
        assert abs(r.final_commanded - sc.focus) <= sc.step, (
            f"{sc.name} settled at {r.final_commanded}, focus is {sc.focus}")


def test_every_measured_frame_pays_a_full_frame_rust_pass(reports):
    """LANE 1'S TARGET. Today the loop runs `detect_and_measure` over all 26
    million pixels of every frame it measures, for a star count and a MAD that
    `focus_size` can supply itself. When lane 1 lands, the probe keeps its pass
    and the points lose theirs -- so this test is meant to fail."""
    for name, r in reports.items():
        assert r.rust_passes == r.exposures - r.wasted_frames, (
            f"{name}: {r.rust_passes} passes over "
            f"{r.exposures - r.wasted_frames} measured frames")
        assert r.rust_mean_frac == pytest.approx(1.0), name
        assert r.size_mean_frac == pytest.approx(1.0), name
        # The probe pays a Rust pass and no focus_size; every point pays both.
        assert r.size_passes == r.rust_passes - 1, name


def test_the_turn_round_wastes_exactly_one_exposure(reports):
    """LANE 2'S TARGET. The predictor descends by one step and stops at its
    first miss, so a sweep whose focus sits above its start pays for one frame
    it never measures and then overlaps nothing for the rest of the run. A
    centred sweep never turns round and wastes nothing."""
    assert reports["rich_turnround"].wasted_frames == 1
    assert reports["rich_at_focus"].wasted_frames == 0
    assert reports["sparse_83"].wasted_frames == 0
    assert reports["rich_83"].wasted_frames == 0


def test_the_turn_round_costs_travel_as_well_as_a_frame(reports):
    """The wasted guess is a MOVE as well as an exposure: the sweep descends
    one more step, then has to climb back across the whole window."""
    turn = reports["rich_turnround"]
    centred = reports["rich_at_focus"]
    assert turn.total_steps > centred.total_steps * 1.4
    assert turn.reversals > centred.reversals


def test_the_run_settles_on_the_wrong_side_of_the_backlash(reports):
    """LANE 3'S TARGET, and the one place the loop did something the plan did
    not predict -- so this records what ACTUALLY happens.

    Every swept point is approached moving IN, and the vertex is reached moving
    OUT, so the tube ends up ``backlash`` steps short of the position the run
    reports. Two of the three centred scenarios then go one step further: the
    validation frame, exposed 40 steps low, reads 4.78 px against the 3.50
    already measured one step away, and `confirmed_best` refuses the fit over
    it. The loop moves ONE step back in -- a reversal far shorter than the
    slack, so it turns the motor and not the tube. The error is unchanged and
    the recorded approach flips to "in".

    `rich_turnround` is the exception: the engine extends left to quota before
    turning right, so its last swept point sits ABOVE the vertex and the
    validation move is already an IN move. The penalty belongs to sweeps that
    end below their vertex.
    """
    backlash = clock.Cost().backlash_steps
    for name in ("rich_at_focus", "sparse_83", "rich_83"):
        r = reports[name]
        assert -backlash <= r.physical_error_steps <= -backlash + 1, (
            f"{name}: physical error {r.physical_error_steps}")

    # The one that reaches its vertex from above pays nothing.
    turn = reports["rich_turnround"]
    assert turn.approach_of_final == "in"
    assert turn.physical_error_steps >= 0

    # And the approach recorded for the others: "out" where the fit stood,
    # "in" where confirmed_best overrode it by a single step.
    assert reports["rich_at_focus"].approach_of_final == "out"
    assert reports["sparse_83"].approach_of_final == "in"
    assert reports["rich_83"].approach_of_final == "in"


def test_the_first_point_of_every_sweep_is_measured_low(reports):
    """The same backlash, at the other end of the run, and a cost nobody has
    counted: the initial move to the top of the window is the only OUT move
    before the validation, so it eats the whole slack and the outermost point
    of the curve is measured 40 steps in from where the run thinks it is. That
    is what pulls the fitted vertex 1 to 5 steps off a focus this model puts
    exactly on a swept point."""
    for make in clock.SCENARIOS:
        sc = make()
        r = reports[sc.name]
        first_pos, first_hfr = r.points[0]
        assert first_hfr == pytest.approx(
            sc.hfr(first_pos - clock.Cost().backlash_steps), rel=1e-6), sc.name
        assert first_hfr < sc.hfr(first_pos)


def test_the_camera_is_idle_for_most_of_the_run(reports):
    """THE HEADLINE. The shutter is open for 6 s of a 50-to-70 s point; the
    rest is 26 million pixels being measured twice. Every lane of the plan is
    an attempt to move this number."""
    for name, r in reports.items():
        busy = r.exposures * (6.0 + 2.0)
        assert r.camera_idle_s > 0.7 * r.wall_s, (
            f"{name}: idle {r.camera_idle_s:.1f} of {r.wall_s:.1f}")
        assert r.cpu_s > 5 * busy, (
            f"{name}: {r.cpu_s:.1f} s of measurement against {busy:.1f} s of "
            f"camera")


def test_the_2026_09_07_sweep_replays_within_fifteen_percent(reports):
    """THE CALIBRATION. `sparse_83` is the logged sweep of 2026-09-07 21:39:38,
    which took 9 min 11 s to its validation frame with its probe measured at
    +35 s. Nothing in the cost model was fitted to it -- the constants were
    measured on the rig against real frames -- so this is what says the model
    describes the rig and not just itself."""
    r = reports["sparse_83"]
    assert abs(r.wall_s - 550.0) <= 0.15 * 550.0, r.wall_s
    assert abs(r.probe_s - 35.0) <= 5.0, r.probe_s
    # Nine swept points plus the validation, in the order the log records them.
    assert len(r.point_times) == 10
    assert [p for p, _h in r.points] == [
        11508, 11425, 11342, 11259, 11176, 11093, 11010, 10927, 10844, 11177]


def test_a_rich_field_costs_more_than_a_sparse_one_to_measure(reports):
    """Same nine points, same geometry, same exposures -- and three minutes
    more, entirely in the detector's per-star half. It is worth stating because
    the intuition runs the other way: a sparse field is the one that FAILS, so
    it is easy to assume it is also the one that is slow."""
    assert reports["rich_83"].wall_s > reports["sparse_83"].wall_s + 120
    assert reports["rich_83"].exposures == reports["sparse_83"].exposures
    assert reports["rich_83"].moves == reports["sparse_83"].moves


def test_the_report_table_renders(reports):
    table = clock.format_table(list(reports.values()))
    for name in reports:
        assert name in table
    for row, _render in clock._ROWS:
        assert row in table
    assert table.isascii(), "the report must stay plain ASCII"
