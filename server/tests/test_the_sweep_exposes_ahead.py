"""The next frame is exposing while this one is measured.

THE IDLE HALF OF EVERY SWEEP. A point costs `move -> expose -> measure`, and on
a 26 MP frame the measure half is most of the wall clock. The shutter is shut
for all of it. `sweep.rs` says the door is open — "Pipelined move-while-
analyzing timing is a host concern; the machine preserves the measurement
positions and their order exactly" — and `focus/pipeline.py` walks through it.

The engine will not say where to go next until the current measurement is added,
so this means PREDICTING the next position. It used to be predicted by a rule of
thumb (`pos - step`, because both of the engine's phases descend) which was
wrong about two moves of every run: the turn-round, and the validation move.
Now the engine answers for itself — `peek_next` runs the hypothetical on a clone
and labels the step it would emit — so what is tested here is that the host
forms an honest hypothetical, believes only a `point`, and still bounds what a
wrong answer costs.

What makes any of it safe is that the prediction is CHECKED: a speculatively
exposed frame is used only for the position the engine actually asks for. What
the prediction buys is time; what a miss costs is one exposure, and one rule
bounds that to one per run.

Both halves are tested here: the rules, on their own, and the overlap actually
happening, end to end on the sim.
"""
import asyncio
import time

import pytest

from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus.pipeline import Prefetch, SweepPredictor


# ------------------------------------------------------------- the prediction

class FakeSweep:
    """A state machine that records what it was asked and answers a script.

    Only ``peek_next`` exists, because that is the whole of the predictor's
    contract with the engine — it never calls ``next`` or ``add_measurement``,
    and a fake that offered them could hide a predictor that did.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[tuple] = []

    def peek_next(self, position, hfr, stdev, star_count):
        self.asked.append((position, hfr, stdev, star_count))
        if not self.answers:
            return {"action": "done", "kind": "none"}
        return self.answers.pop(0)


def _point(position):
    return {"action": "move_to", "position": position, "kind": "point"}


def test_the_hypothetical_is_extrapolated_from_the_last_two_points():
    """WHAT THE ENGINE IS ASKED. `peek_next` wants the measurement the pending
    frame is about to produce, and the honest estimate of it is the flank the
    last two points describe: 10.0 at 12535 and 8.0 at 12185 is 2.0 px per step
    of 350, so 11835 is expected to read 6.0. σ and the star count come from the
    last point measured — they are properties of the field, not of the flank."""
    sweep = FakeSweep(_point(11485))
    p = SweepPredictor()
    p.emitted(11835)
    got = p.predict(sweep, 11835,
                    [(12535, 10.0, 0.11), (12185, 8.0, 0.12)], [400, 380])
    assert got == 11485
    (position, hfr, stdev, stars), = sweep.asked
    assert position == 11835
    assert hfr == pytest.approx(6.0)
    assert stdev == pytest.approx(0.12)
    assert stars == 380


def test_one_measured_point_extrapolates_flat():
    """No flank yet, so the best available answer is "the same as last time".
    Right whenever the engine's next move does not turn on the size, which is
    most of the initial pass — and the alternative is not guessing at all."""
    sweep = FakeSweep(_point(11835))
    p = SweepPredictor()
    p.emitted(12185)
    assert p.predict(sweep, 12185, [(12535, 9.25, 0.2)], [400]) == 11835
    assert sweep.asked[0][1] == pytest.approx(9.25)


def test_nothing_measured_yet_is_not_a_guess():
    """The probe's own frame is not a sweep point, so at the first sweep point
    there is nothing to extrapolate from. The loop seeds that one move itself
    (see `seed` below) rather than inventing a measurement for it."""
    sweep = FakeSweep(_point(11835))
    p = SweepPredictor()
    p.emitted(12535)
    assert p.predict(sweep, 12535, [], []) is None
    assert sweep.asked == [], "it asked the engine about a field it has not measured"


def test_the_guess_now_survives_the_turn_round():
    """WHAT THE RULE OF THUMB COULD NOT DO. When the engine has extended down to
    its quota it turns and extends UP from the highest point it measured, and
    `pos - step` was wrong exactly there — one wasted exposure, and under the
    one-strike rule no overlap for the rest of the run. The engine names the
    upward move, and still calls it a curve point."""
    sweep = FakeSweep(_point(12885))
    p = SweepPredictor()
    p.emitted(10785)
    got = p.predict(sweep, 10785,
                    [(11135, 12.0, 0.1), (10785, 15.0, 0.1)], [200, 180])
    assert got == 12885, "an upward answer was refused"
    assert p.emitted(12885) is True


@pytest.mark.parametrize("answer", [
    {"action": "move_to", "position": 11201, "kind": "validation"},
    {"action": "move_to", "position": 12535, "kind": "restore"},
    {"action": "move_to", "position": 12535, "kind": "baseline"},
    {"action": "done", "kind": "none"},
    {"action": "failed", "reason": "not_enough_spread", "kind": "none"},
])
def test_only_a_curve_point_is_worth_a_frame(answer):
    """THE OTHER HALF OF `peek_next`: every step says what it expects back, so
    the moves that must not be speculated are RECOGNISED rather than counted to.

    The validation move is the one that matters — it goes UP to the fitted
    vertex, it is the last move of every healthy run, and guessing it wrong
    would spend a frame on every single successful sweep. A `restore` or a
    `baseline` is the same argument with a rarer move; a terminal step has no
    position at all.
    """
    p = SweepPredictor()
    p.emitted(11550)
    got = p.predict(FakeSweep(answer), 11550,
                    [(11900, 5.0, 0.1), (11550, 3.0, 0.1)], [500, 520])
    assert got is None
    assert p.predicted is None


def test_a_wheel_without_peek_next_simply_does_not_guess():
    """An older `astrodeck_native` has no `peek_next`. Sitting still costs the
    overlap and nothing else; falling back to the retired descending rule would
    cost a frame at every turn-round, which is what it was retired for."""
    class OldSweep:
        pass

    p = SweepPredictor()
    p.emitted(12185)
    assert p.predict(OldSweep(), 12185, [(12535, 9.0, 0.1)], [400]) is None


def test_an_engine_that_refuses_the_hypothetical_is_not_the_runs_failure():
    """A speculative question that raises must not tear down a sweep that is
    measuring perfectly well — the real measurement goes in through
    `add_measurement`, which this cannot touch."""
    class Angry:
        def peek_next(self, *a):
            raise ValueError("bad star count")

    p = SweepPredictor()
    p.emitted(12185)
    assert p.predict(Angry(), 12185, [(12535, 9.0, 0.1)], [400]) is None


def test_one_miss_stops_it_for_the_rest_of_the_run():
    """THE COST BOUND, and it is unchanged. A wasted exposure through a
    narrowband filter is 30 s, which would turn a speedup into a regression if
    it could happen repeatedly. It cannot: the first wrong guess ends
    speculation permanently.

    A miss is still possible even with the engine answering. The hypothetical is
    a LINEAR extrapolation across a curve that turns, so at the pivot the
    expected value can land on the wrong side of the vertex and the engine's
    answer to it is not its answer to the real measurement.
    """
    p = SweepPredictor()
    p.emitted(12535)
    assert p.predict(FakeSweep(_point(12185)), 12535,
                     [(12885, 11.0, 0.1)], [400]) == 12185
    p.emitted(12535)                     # the engine re-asked a dropped point
    assert p.misses == 1
    assert p.predict(FakeSweep(_point(11835)), 12535,
                     [(12885, 11.0, 0.1)], [400]) is None
    p.emitted(11835)                     # and it never comes back
    assert p.predict(FakeSweep(_point(11485)), 11835,
                     [(12885, 11.0, 0.1)], [400]) is None


def test_the_probe_s_own_move_is_seeded_not_guessed():
    """The first sweep move is started under the PROBE's measurement, from the
    engine's own first step — no prediction is involved. Without seeding it, the
    loop's accounting would read the run's one certain move as a miss and turn
    speculation off before the first point was even measured."""
    p = SweepPredictor()
    p.seed(12535)
    assert p.emitted(12535) is True
    assert p.hits == 1 and p.misses == 0 and not p.stopped


async def test_a_frame_is_only_ever_used_for_its_own_position():
    """THE CORRECTNESS PROPERTY, stated on its own. Everything else here is
    about speed; this is the one that says a wrong guess cannot corrupt a
    measurement."""
    async def expose():
        return "frame at 11835"

    pre = Prefetch(11835, asyncio.create_task(expose()))
    assert await pre.take(11485) is None

    pre = Prefetch(11835, asyncio.create_task(expose()))
    assert await pre.take(11835) == "frame at 11835"


async def test_a_speculative_failure_is_not_the_runs_failure():
    """The in-line path that follows will hit the same device error properly and
    report it. A guess that raised out of `settle` would instead tear down a run
    that could have continued."""
    async def boom():
        raise RuntimeError("focuser said no")

    pre = Prefetch(11835, asyncio.create_task(boom()))
    assert await pre.settle() is None


# ------------------------------------------------------ the overlap, for real

pytestmark_native = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


async def _connected_sim():
    parts = build_sim_rig()
    await parts["camera"].connect()
    await parts["focuser"].connect()
    return parts["_rig"], parts["camera"], parts["focuser"]


@pytestmark_native
async def test_the_config_the_host_sends_names_the_key_the_engine_reads():
    """ONE INERT KEY, pinned. The host described the focuser's travel ceiling as
    `max_position`; `build_focus_config` (native/crates/astrodeck-native/src/
    lib.rs) reads `max_step`, so the value never reached the engine at all. The
    dict is built inside the loop, so the only way to see it is to watch the
    engine being constructed."""
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()
    seen: dict = {}

    def spy(config, start_position):
        seen.update(config)
        raise RuntimeError("stop here — the config is all this test wanted")

    real = N._native.FocusSweep
    try:
        N._native.FocusSweep = spy
        with pytest.raises(Exception):
            await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                         step=350, steps_each_side=4, binning=1)
    finally:
        N._native.FocusSweep = real

    assert seen.get("max_step") == foc.max_position, seen
    assert "max_position" not in seen, "the key the engine has never read"
    # And the backlash keys stay OUT of it. `FocusConfig::reverse_sweep()` flips
    # the initial pass to ASCEND when the Overshoot model has backlash_in set,
    # and the one-sided approach is the HOST's now (see `_approach`), so asking
    # the engine for it too would reverse the sweep as a side effect.
    for key in ("backlash_in", "backlash_out", "backlash_model"):
        assert key not in seen, key


@pytestmark_native
async def test_the_next_point_starts_while_this_one_is_being_measured(monkeypatch):
    """END TO END: the property the whole module exists for.

    Recorded as an ORDER of events rather than as elapsed time — a wall-clock
    threshold would be a flake on a loaded machine, and the claim is not "it is
    faster by N seconds" but "the camera is working during the measurement".
    """
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()

    log: list[tuple[str, int]] = []
    real_move = foc.move_to
    real_metric = N.native_sweep_metric

    async def move(pos, *a, **kw):
        log.append(("move", int(pos)))
        return await real_move(pos, *a, **kw)

    def metric(frame):
        log.append(("measure-start", int(frame.focuser_position or -1)))
        time.sleep(0.05)      # in a worker thread: the event loop is free
        out = real_metric(frame)
        log.append(("measure-end", int(frame.focuser_position or -1)))
        return out

    foc.move_to = move
    monkeypatch.setattr(N, "native_sweep_metric", metric)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1,
                                       approach_overshoot_steps=0)
    assert res.success, res.message

    # ONE POINT AHEAD. Count how often the sweep had already committed to the
    # next position by the time a measurement finished. Serially that can never
    # happen — move k, measure k, move k+1 — so `issued > done` at a measure-end
    # is the overlap itself, with no clock involved. (The overshoot is disabled
    # above so one target is one move and this count stays readable.)
    issued = done = ahead = 0
    for kind, _v in log:
        if kind == "move":
            issued += 1
        elif kind == "measure-end":
            done += 1
            if issued > done:
                ahead += 1
    assert ahead >= 5, (
        f"the sweep was running ahead of its own measurements only {ahead} "
        f"times — the camera is still waiting for the CPU\n{log}")


@pytestmark_native
async def test_the_first_sweep_move_starts_under_the_probes_measurement(monkeypatch):
    """THE PROBE'S OWN 27 SECONDS. The probe frame pays the run's only
    full-frame Rust pass, and until 2026-09-08 the focuser and the camera sat
    still through it: the engine was not even constructed until the count came
    back. It does not need to be — the first sweep position is geometry, not a
    response to a measurement — so the move and its exposure now run underneath.

    Order of events again, not a clock. The move must appear BETWEEN the start
    and the end of the probe's measurement.
    """
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()
    start = await foc.get_position()

    log: list[str] = []
    real_move, real_detect = foc.move_to, N._native.detect_and_measure

    async def move(pos, *a, **kw):
        log.append(f"move:{int(pos)}")
        return await real_move(pos, *a, **kw)

    def detect(arr, params=None):
        log.append("probe-measure-start")
        time.sleep(0.1)       # in a worker thread: the event loop is free
        out = real_detect(arr, params)
        log.append("probe-measure-end")
        return out

    foc.move_to = move
    monkeypatch.setattr(N._native, "detect_and_measure", detect)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1,
                                       approach_overshoot_steps=0)
    assert res.success, res.message
    assert log[0] == "probe-measure-start", (
        f"something moved the focuser before the probe was exposed\n{log}")
    end = log.index("probe-measure-end")
    during = [e for e in log[1:end] if e.startswith("move:")]
    assert during, f"nothing moved while the probe was measured\n{log[:6]}"
    assert during[0] == f"move:{start + 4 * 350}", (
        f"the move under the probe was not the engine's first step\n{log[:6]}")


def _count_frames(monkeypatch, cam, N):
    """(exposed, measured). Every exposure that is never measured is one the
    sweep paid for and threw away — the whole cost of guessing, counted."""
    counts = {"expose": 0, "measure": 0}
    real_expose, real_metric = cam.expose, N.native_sweep_metric

    async def expose(*a, **kw):
        counts["expose"] += 1
        return await real_expose(*a, **kw)

    def metric(frame):
        counts["measure"] += 1
        return real_metric(frame)

    cam.expose = expose
    monkeypatch.setattr(N, "native_sweep_metric", metric)
    return counts


@pytestmark_native
async def test_a_sweep_that_runs_its_nominal_shape_wastes_nothing(monkeypatch):
    """Focus AT the start position, which is what a sweep sized from the
    measured span and started from last night's focus actually looks like. The
    engine then walks its initial pass down and extends down, every guess lands,
    and the only exposure that is never measured is the pre-flight probe (which
    goes through `detect_and_measure`, not this seam)."""
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    rig.best_focus = await foc.get_position()
    counts = _count_frames(monkeypatch, cam, N)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1)
    assert res.success, res.message
    assert counts["expose"] - counts["measure"] == 1, counts


@pytestmark_native
async def test_a_sweep_that_turns_round_wastes_nothing_either(monkeypatch):
    """THE FRAME THE OLD RULE PAID FOR, no longer paid.

    The sim's focus sits 800 steps ABOVE the start, so after extending downward
    the engine turns and extends upward. `pos - step` was wrong exactly once
    there, and the one-strike rule then ended speculation for the rest of the
    run — a frame thrown away AND the overlap lost. The engine's own answer
    turns with it, so the probe is again the only unmeasured exposure.
    """
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    assert rig.best_focus != await foc.get_position(), "the sim stopped being off-centre"
    counts = _count_frames(monkeypatch, cam, N)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1)
    assert res.success, res.message
    assert counts["expose"] - counts["measure"] == 1, (
        f"{counts} — the probe should be the only frame nothing measures")


@pytestmark_native
async def test_the_validation_frame_is_never_a_guess(monkeypatch):
    """The last move of a healthy run goes UP to the fitted vertex, and it is
    the one frame that MUST be taken after the fit rather than before it. The
    engine labels that step `validation` and `focus.pipeline` refuses to
    speculate on it, so exactly one exposure ever happens there."""
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    rig.best_focus = await foc.get_position()

    at: list[int] = []
    real_expose = cam.expose

    async def expose(*a, **kw):
        where = await foc.get_position()
        frame = await real_expose(*a, **kw)
        at.append(int(where))
        return frame

    cam.expose = expose
    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1)
    assert res.success, res.message

    validation = res.points[-1][0]
    swept = [p for p, _h in res.points[:-1]]
    assert validation not in swept, (
        f"the fitted vertex landed on a swept point ({validation}); this test "
        f"needs it not to")
    assert at.count(validation) == 1, (
        f"{at.count(validation)} exposures at the validation position "
        f"{validation} — one of them was speculative\n{at}")


@pytestmark_native
async def test_a_refusal_settles_the_speculative_move_before_restoring(monkeypatch):
    """THE OTHER END OF THE PROBE'S OVERLAP. The first sweep move is now in
    flight while the probe is being measured — so the path that reads the probe
    and REFUSES the run ("only N stars at the current focus") has a move it did
    not start to wait for. Restoring the start position without settling it is
    two moves on one focuser, and on the sim two move loops chase each other's
    target for ever.

    The speculative move is deliberately made SLOW so it is certainly still in
    flight when the refusal fires.
    """
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()
    start = await foc.get_position()

    moves = {"n": 0}
    finished: list[int] = []
    real_move, real_detect = foc.move_to, N._native.detect_and_measure

    async def move(pos, *a, **kw):
        moves["n"] += 1
        if moves["n"] == 1:
            await asyncio.sleep(0.4)
        out = await real_move(pos, *a, **kw)
        finished.append(int(pos))
        return out

    def detect(arr, params=None):
        # A field too thin to sweep: the refusal path, on the probe's own count.
        _sources, stats = real_detect(arr, params)
        return _sources, dict(stats, star_count=1)

    foc.move_to = move
    monkeypatch.setattr(N._native, "detect_and_measure", detect)

    res = await asyncio.wait_for(
        N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                               steps_each_side=4, binning=1),
        timeout=20)

    assert not res.success
    assert "1 stars" in res.message, res.message
    assert finished, "the speculative move never happened"
    assert finished[-1] == start, (
        f"the last completed move was to {finished[-1]}, not the start "
        f"position — the restore raced a speculative move\n{finished}")
    assert await foc.get_position() == start
    await asyncio.sleep(0.6)
    assert await foc.get_position() == start, (
        "a speculative move outlived the run that started it")


@pytestmark_native
async def test_a_halt_does_not_leave_an_exposure_driving_the_focuser(monkeypatch):
    """THE TEARDOWN RULE. A speculative task owns the focuser while it runs, so
    the restore-to-start on the way out must not race it — two moves on one
    focuser is how a torn-down sweep ends up somewhere nobody chose, shooting
    the rest of the night defocused.

    The speculative move is deliberately made SLOW so it is certainly still in
    flight when the failure fires. A first draft left it at sim speed, the
    prefetch happened to finish before the restore, and removing the settle
    entirely left the test green — proving nothing.
    """
    import astrodeck.focus.native as N
    _rig, cam, foc = await _connected_sim()
    start = await foc.get_position()

    calls = {"metric": 0, "moves": 0}
    finished: list[int] = []
    real_metric, real_move = N.native_sweep_metric, foc.move_to

    async def move(pos, *a, **kw):
        calls["moves"] += 1
        # Slow only the speculative move that will be in flight at the failure,
        # so the rest of the sweep still runs at sim speed.
        if calls["moves"] == 4:
            await asyncio.sleep(0.4)
        out = await real_move(pos, *a, **kw)
        finished.append(int(pos))
        return out

    def metric(frame):
        calls["metric"] += 1
        if calls["metric"] == 3:
            raise RuntimeError("the camera link dropped mid-sweep")
        return real_metric(frame)

    foc.move_to = move
    monkeypatch.setattr(N, "native_sweep_metric", metric)

    # BOUNDED, because the regression's real symptom is a HANG. The sim
    # focuser's move loop is `while pos != target: step toward it`, so two
    # concurrent moves chase each other's target forever — which is what
    # deleting the settle actually does, on the sim and very likely on an EAF
    # too. A hung suite is a worse signal than a failed one.
    with pytest.raises(Exception):
        await asyncio.wait_for(
            N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                   step=350, steps_each_side=4, binning=1,
                                   approach_overshoot_steps=0),
            timeout=20)

    assert finished[-1] == start, (
        f"the last completed move was to {finished[-1]}, not the start "
        f"position — a speculative move finished AFTER the restore\n{finished}")
    assert await foc.get_position() == start
    await asyncio.sleep(0.6)
    assert await foc.get_position() == start, (
        "a speculative move outlived the sweep that started it")
