"""What a native autofocus sweep costs today, so a change to it can be graded.

Two halves, and the first is not optional. `_focus_clock` measures a nine-point
sweep in seconds that never elapse -- if the virtual clock or the cost
accounting is wrong, every number the second half prints is a confident lie
about a run that never happened. So the clock is tested against arithmetic
anybody can do in their head (two 5 s sleeps concurrently finish at 5, in
series at 10, and neither costs a fifth of a real second), and the cost
accounting is tested by moving one constant and checking the bill moves with
it.

The second half runs the four scenarios and asserts STRUCTURAL facts: not "the
sweep takes 153 s" -- which is a number in a cost model and would have to be
edited every time the model improves -- but "one Rust pass per run", "nothing
is wasted at the turn-round", "the tube ends where the run says it does".

Those three sentences used to read the other way round. They were written as
status-quo assertions, each one designed to FAIL when its lane of the
autofocus-efficiency plan landed, because a status-quo test that survives the
change it was written for measured nothing. All three lanes have now landed
(2026-09-08), so each of those assertions has been turned over to state the
contract that replaced it -- and the number it used to hold is kept in its
docstring, because "4.5x faster" is only meaningful next to what it was.

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
    """Move a constant and the bill must move with it, on both clocks. A harness
    whose "cost model" the run does not actually pay would report the same wall
    time for every change to it.

    BOTH constants now, because the run pays them in very different amounts and
    that difference IS lane 1. Doubling the Rust pass moves the bill by ONE pass
    -- the probe's 25.7 s on this 34-star field -- where before lane 1 it moved
    it by eleven (the assertion here used to be `+200`). Doubling the size
    metric's base moves it by all ten points. A test left doubling only the Rust
    constant would now be nearly silent, and would pass just as well against a
    harness that had stopped billing the per-point measurement at all.
    """
    base = clock.run_scenario(clock.sparse_83())

    sc = clock.sparse_83()
    sc.cost = dataclasses.replace(sc.cost, rust_base_s=sc.cost.rust_base_s * 2)
    rust = clock.run_scenario(sc)
    assert rust.cpu_s > base.cpu_s + 20
    assert rust.wall_s > base.wall_s + 20
    assert rust.cpu_s < base.cpu_s + 60, (
        "one Rust pass per run, not eleven -- the probe's, and no other")

    sc = clock.sparse_83()
    sc.cost = dataclasses.replace(sc.cost, size_base_s=sc.cost.size_base_s * 2)
    size = clock.run_scenario(sc)
    assert size.cpu_s > base.cpu_s + 50
    assert size.wall_s > base.wall_s + 50


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
    """The pixel-fraction accounting, exercised directly rather than through a
    run: the sweep crops its size passes now (`size_mean_frac` 0.16 on a rich
    field), and this is where the harness proves it can price a crop at all."""
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


def test_the_run_pays_one_rust_pass_and_measures_the_centre(reports):
    """LANE 1, LANDED. The loop used to run `detect_and_measure` over all 26
    million pixels of EVERY frame it measured -- eleven full-frame passes, all
    at `rust_mean_frac` 1.00 -- for a star count and a MAD that `focus_size` can
    supply itself. Now the probe pays that pass once (whole, because its count
    is what sizes the window) and each point pays one `focus_size` over the
    central 0.4 of each axis: 16 percent of the pixels.

    `sparse_83` keeps the whole frame BY DESIGN. 34 stars has nothing to give
    away (`focus.window.measure_window` returns 1.0 below 40), so its size
    passes stay at 1.00 and its whole saving is the Rust pass.
    """
    for name, r in reports.items():
        assert r.rust_passes == 1, (
            f"{name}: {r.rust_passes} Rust passes -- the probe's, and no other")
        assert r.rust_mean_frac == pytest.approx(1.0), name
        # One size pass per measured frame except the probe, which pays the
        # Rust pass instead.
        assert r.size_passes == r.exposures - r.wasted_frames - 1, name
    for name in ("rich_at_focus", "rich_turnround", "rich_83"):
        assert reports[name].size_mean_frac == pytest.approx(0.16, abs=0.005), (
            f"{name}: {reports[name].size_mean_frac:.3f} of the pixels")
    assert reports["sparse_83"].size_mean_frac == pytest.approx(1.0), (
        "a 34-star field has no window to give")


def test_the_turn_round_wastes_nothing_now(reports):
    """LANE 2, LANDED. The predictor used to descend by one step and stop at its
    first miss, so a sweep whose focus sits above its start paid for one frame
    it never measured AND overlapped nothing for the rest of the run:
    `rich_turnround` took 12 exposures to everyone else's 11. The engine's own
    `peek_next` turns with the sweep, so the guess survives the pivot and the
    frame count is the same as a centred run's."""
    for name, r in reports.items():
        assert r.wasted_frames == 0, f"{name} threw away {r.wasted_frames}"
    assert (reports["rich_turnround"].exposures
            == reports["rich_at_focus"].exposures == 11)


def test_the_turn_round_still_costs_the_travel(reports):
    """What lane 2 did NOT fix, kept so nobody assumes it did. The wasted
    exposure is gone; the mileage is not. A sweep whose focus sits above its
    start still descends to its quota before turning, and then has to climb back
    across the whole window -- 8900 steps against 6400, with two more reversals.
    Nothing in the host can change that: it is the engine's search order."""
    turn = reports["rich_turnround"]
    centred = reports["rich_at_focus"]
    assert turn.total_steps > centred.total_steps + 2000
    assert turn.reversals > centred.reversals


def test_the_run_settles_exactly_where_it_says_it_does(reports):
    """LANE 3, LANDED. Every swept point used to be approached moving IN and the
    vertex reached moving OUT, so the tube ended 39 of its 40 steps of slack
    short of the position the run reported. Two of the centred scenarios then
    went one step further: the validation frame, exposed 40 steps low, read
    4.78 px against the 3.50 already measured one step away, and
    `confirmed_best` refused the fit over it -- then moved ONE step back in, a
    reversal far shorter than the slack, which turns the motor and not the tube.

    Now every outward move overshoots by `config.focus.approach_overshoot_steps`
    and returns, so the last leg of every move is inward, on a focuser this
    harness gives 40 steps of backlash. The tube is where the run says it is, on
    all four scenarios, and the fitted vertex lands ON the model's focus rather
    than 1 to 5 steps off it.
    """
    for make in clock.SCENARIOS:
        sc = make()
        r = reports[sc.name]
        assert r.physical_error_steps == 0, (
            f"{sc.name}: commanded {r.final_commanded}, tube at "
            f"{r.final_physical}")
        assert r.approach_of_final == "in", sc.name
        assert r.final_commanded == sc.focus, (
            f"{sc.name} settled at {r.final_commanded}, focus is {sc.focus}")


def test_the_first_point_of_every_sweep_reads_its_own_position(reports):
    """The same backlash at the other end of the run. The initial move to the
    top of the window was the only OUT move before the validation, so it ate the
    whole slack and the outermost point of the curve was measured 40 steps in
    from where the run thought it was: 12600 read 29.04 px against the model's
    29.89, 11508 read 24.58 against 27.89. That is what pulled the fitted vertex
    off the focus this model puts exactly on a swept point. The first move now
    arrives inward like every other, so the point reads its own position."""
    for make in clock.SCENARIOS:
        sc = make()
        r = reports[sc.name]
        first_pos, first_hfr = r.points[0]
        assert first_hfr == pytest.approx(sc.hfr(first_pos), rel=1e-6), (
            f"{sc.name}: {first_hfr:.2f} at {first_pos}, model says "
            f"{sc.hfr(first_pos):.2f}")


def test_the_camera_now_does_most_of_the_work(reports):
    """THE HEADLINE, INVERTED. The shutter used to be open for 6 s of a 50-to-70
    s point and the camera idle for four fifths of the run; the rest was 26
    million pixels being measured twice.

    Measured now: on the three rich scenarios the camera is busy for 57 to 61
    percent of the wall clock (88 s of shutter and download in a 144-153 s run),
    and total measurement is about the same as total camera time rather than
    five times it.

    `sparse_83` is the honest exception at 35 percent busy (88 s of 251 s): its
    field is too thin to crop, so ten whole-frame `focus_size` passes still
    dominate it. That is the remaining cost, and it is named here rather than
    averaged away.
    """
    for name, r in reports.items():
        busy = r.exposures * (6.0 + 2.0)
        assert r.cpu_s < 3.0 * busy, (
            f"{name}: {r.cpu_s:.1f} s of measurement against {busy:.1f} s of "
            f"camera")
    for name in ("rich_at_focus", "rich_turnround", "rich_83"):
        r = reports[name]
        busy = r.exposures * (6.0 + 2.0)
        assert busy > 0.5 * r.wall_s, (
            f"{name}: busy {busy:.1f} of {r.wall_s:.1f}")
        assert r.cpu_s < 1.2 * busy, name
    sparse = reports["sparse_83"]
    fraction = sparse.exposures * 8.0 / sparse.wall_s
    assert 0.30 <= fraction <= 0.45, (
        f"the whole-frame field is {fraction:.0%} busy, not the ~35 percent "
        f"this test recorded")


def test_the_probe_still_replays_the_2026_09_07_sweep(reports):
    """THE CALIBRATION, on the part of the run the three lanes did not touch.

    `sparse_83` is the logged sweep of 2026-09-07 21:39:38, whose probe was
    measured at +35 s: one full-frame Rust pass on the starting frame, which is
    exactly what the probe still does. So this remains a live check that the
    cost model describes the rig.

    The WHOLE-SWEEP target is retired. That sweep took 9 min 11 s to its
    validation frame and the OLD loop replayed it at 503.2 s -- 8.5 percent
    under, with no constant fitted to it. The point of the three lanes is that
    the same sweep no longer costs that, so the anchor moves to the probe and to
    the per-pass constants (next test).
    """
    r = reports["sparse_83"]
    assert abs(r.probe_s - 35.0) <= 5.0, r.probe_s
    # Nine swept points plus the validation, in the order the log records them.
    # The validation lands on 11176 rather than the 11177 the backlashed run
    # fitted -- see the settles-where-it-says-it-does test above.
    assert len(r.point_times) == 10
    assert [p for p, _h in r.points] == [
        11508, 11425, 11342, 11259, 11176, 11093, 11010, 10927, 10844, 11176]


def test_the_cost_constants_are_the_ones_measured_on_the_rig():
    """The other half of the calibration: each constant priced back into the
    measurement it came from (rig benchmark, 2026-09-08, real 26 MP frames).

    Stated as BANDS with the residual named, because the model is two terms and
    the rig is not: it reproduces the rich end of `detect_and_measure` to within
    a second and the sparse end about 7 percent low, and it reads whole-frame
    `focus_size` about 1.2 s high. Those residuals were acceptable when the
    constants were chosen (the allowed bands were 3..12 s for the size pass and
    0.5..1.5 s for its windowed form) and they are pinned here so a later edit
    to the model has to argue with the rig rather than with a test.
    """
    cost = clock.Cost()
    assert (cost.rust_base_s, cost.rust_per_star_s) == (25.0, 0.021)
    assert (cost.size_fixed_s, cost.size_base_s, cost.size_per_px_s) == (
        0.5, 6.0, 0.9)

    def rust(stars, frac=1.0):
        return cost.rust_base_s * frac + cost.rust_per_star_s * stars

    def size(hfr, frac=1.0):
        return cost.size_fixed_s + (cost.size_base_s
                                    + cost.size_per_px_s * hfr) * frac

    # Whole-frame detector: 67.1 s at 1999 stars and 59.9 s at 1462 on the rig.
    assert rust(1999) == pytest.approx(67.1, abs=1.0)
    assert rust(1462) == pytest.approx(59.9, rel=0.10)   # 55.7 s: 7 percent low
    # The 34-star probe of the replay, logged at about 27 s.
    assert rust(34) == pytest.approx(27.0, abs=2.0)
    # Whole-frame size pass: 8.8 s at 4.0 px and 9.5 s at 4.6 on the rig; the
    # model reads 10.1 and 10.6, inside the 3..12 s band it was fitted across.
    assert 8.0 <= size(3.0) <= 12.0
    assert 8.0 <= size(4.6) <= 12.0
    assert size(4.6) > size(4.0) > size(3.0)
    # And on the 0.4-per-axis window the sweep actually measures: 1.0-2.2 s
    # measured, 1.9-2.1 s modelled.
    assert 0.5 <= size(3.0, 0.16) <= 2.5
    assert 0.5 <= size(4.6, 0.16) <= 2.5


def test_the_sparse_field_is_now_the_expensive_one(reports):
    """THE INVERSION, and the surprising half of the result. Same nine points,
    same geometry, same exposures -- and the RICH field is now 100 s FASTER than
    the sparse one (148 s against 251), where before lane 1 it was 175 s slower.

    Why: the measurement window is sized from the probe's star count, and 34
    stars has nothing to give away, so `sparse_83` measures every point over the
    whole frame while `rich_83` measures 16 percent of it. The detector's
    per-star cost -- what used to make a rich field the expensive one -- is now
    paid once, at the probe.
    """
    rich, sparse = reports["rich_83"], reports["sparse_83"]
    assert rich.wall_s < sparse.wall_s - 80
    assert rich.exposures == sparse.exposures
    assert rich.moves == sparse.moves
    assert rich.size_mean_frac < sparse.size_mean_frac


#: What each scenario cost on the tree that landed all three lanes, from
#: `python tests/_focus_clock.py` on 2026-09-08. A MEASUREMENT, not a target:
#: when a change moves one of these legitimately, re-run the harness, update
#: this dict AND the table in `_focus_clock.py`'s docstring, and say in the
#: commit which way it moved. The margin below is what stops a silent
#: regression -- a re-added full-frame pass or a lost overlap costs tens of
#: seconds, which is many times the tolerance.
_WALL_S = {
    "rich_at_focus": 153.4,
    "rich_turnround": 144.5,
    "sparse_83": 251.1,
    "rich_83": 148.4,
}


def test_each_scenario_still_costs_what_it_did_when_it_was_measured(reports):
    """The budget, both ways. Over is a regression; UNDER by more than the
    margin means the cost model got cheaper rather than the sweep getting
    faster, which is the failure mode a harness cannot otherwise notice about
    itself."""
    for name, expected in _WALL_S.items():
        wall = reports[name].wall_s
        assert abs(wall - expected) <= 0.10 * expected, (
            f"{name}: {wall:.1f} s against the {expected:.1f} s measured on "
            f"2026-09-08 -- re-run tests/_focus_clock.py and update the table")


def test_the_report_table_renders(reports):
    table = clock.format_table(list(reports.values()))
    for name in reports:
        assert name in table
    for row, _render in clock._ROWS:
        assert row in table
    assert table.isascii(), "the report must stay plain ASCII"
