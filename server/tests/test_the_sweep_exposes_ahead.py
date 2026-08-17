"""The next frame is exposing while this one is measured.

THE IDLE HALF OF EVERY SWEEP. A point costs `move -> expose -> measure`, and the
measure half is `detect_and_measure` plus `focus_size`: 3-6 s of CPU on a 26 MP
frame against a 2 s exposure. The shutter is shut for all of it. `sweep.rs` says
the door is open — "Pipelined move-while-analyzing timing is a host concern; the
machine preserves the measurement positions and their order exactly" — and
`focus/pipeline.py` walks through it.

The engine will not say where to go next until the current measurement is added,
so this means PREDICTING the next position. What makes it safe is that the
prediction is CHECKED: a speculatively exposed frame is used only for the
position the engine actually asks for. What the prediction buys is time; what a
miss costs is one exposure, and two rules bound that to one per run.

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

def test_the_sweep_descends_and_so_does_the_guess():
    """`setup_initial_sweep` walks `start + offset*step` DOWN to `start`, and
    `decide_extension` then continues down from `points.first() - step`. Every
    sweep this rig has logged does exactly that — 12185, 11835, 11485, 11135,
    10785, 10435 … on 2026-08-17 — so the guess is `pos - step`."""
    p = SweepPredictor(step=350, steps_each_side=4)
    p.emitted(12535)
    assert p.predict() == 12185
    assert p.emitted(12185) is True
    assert p.predict() == 11835


def test_the_first_position_is_never_guessed():
    """There is nothing to guess from before the engine has spoken once, and a
    guess made from `start` alone would be wrong for the initial pass (which
    begins at the TOP of the window, not the middle)."""
    p = SweepPredictor(step=350, steps_each_side=4)
    assert p.predict() is None


def test_one_miss_stops_it_for_the_rest_of_the_run():
    """THE COST BOUND. A wasted exposure through a narrowband filter is 30 s,
    which would turn a speedup into a regression if it could happen repeatedly.
    It cannot: the first wrong guess ends speculation permanently."""
    p = SweepPredictor(step=350, steps_each_side=4)
    p.emitted(12535)
    assert p.predict() == 12185
    p.emitted(12535)                     # the engine re-asked a dropped point
    assert p.misses == 1
    assert p.predict() is None
    p.emitted(11835)                     # and it never comes back
    assert p.predict() is None


def test_it_stops_before_the_validation_move():
    """THE OTHER COST BOUND, and the one that matters on a HEALTHY run. After
    the swept points the engine fits and moves to the validation position —
    which is UP, at the fitted focus, never `pos - step`. Guessing there would
    make the one-exposure penalty happen on every single successful sweep, so
    the model steps aside once the nominal 2*sides+1 points are accounted for.
    """
    p = SweepPredictor(step=350, steps_each_side=4)
    pos = 12535
    guesses = []
    for _ in range(9):                   # the nominal nine-point sweep
        p.emitted(pos)
        nxt = p.predict()
        guesses.append(nxt)
        if nxt is None:
            break
        pos = nxt
    assert guesses[-1] is None, guesses
    assert p.misses == 0, "a healthy sweep must not waste a single frame"
    assert len([g for g in guesses if g is not None]) == 8


def test_it_will_not_guess_past_the_floor():
    """At or below 0 the engine clamps and breaks out of its extension loop
    (`hit_focuser_zero`), so the descending model stops describing it."""
    p = SweepPredictor(step=350, steps_each_side=4)
    p.emitted(200)
    assert p.predict() is None


def _rust(name: str) -> str:
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    return (root / "native" / "crates" / "astro-focus" / "src" /
            name).read_text(encoding="utf-8")


def test_the_descending_model_still_matches_the_engine_it_models():
    """ANTI-DRIFT. The guess is `pos - step` because that is what `sweep.rs`
    does, and `sweep.rs` is in another language and another build. If it ever
    stops descending, every guess inverts — the run stays CORRECT (one strike
    turns speculation off) but silently loses the whole speedup, which is the
    kind of regression nothing notices. Fail here instead."""
    src = _rust("sweep.rs")
    # The initial pass: d shrinks as i grows, and is ADDED to start — so the
    # emitted positions walk downward from start + offset*step to start.
    assert "let d = (offset - i) * step;" in src
    assert "self.start + d" in src
    # The extension then continues below the lowest measured point.
    assert (".first().unwrap().position.round_ties_even() as i32 - step" in src)


def test_nothing_has_switched_the_sweep_into_reverse():
    """The other half of the same guard, on our side of the wire.
    `FocusConfig::reverse_sweep()` flips the initial pass to ASCEND when the
    OVERSHOOT model has `backlash_in > 0` and `backlash_out == 0`. The host
    config sets neither key, so the sweep descends; adding one silently would
    invert every prediction."""
    from pathlib import Path
    host = (Path(__file__).resolve().parents[1] / "astrodeck" / "focus" /
            "native.py").read_text(encoding="utf-8")
    config = host.split("config = {", 1)[1].split("}", 1)[0]
    assert "backlash_in" not in config, config
    assert "backlash_out" not in config, config
    assert "backlash_model" not in config, config
    assert "self.backlash_in > 0" in _rust("config.rs")


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
                                       step=350, steps_each_side=4, binning=1)
    assert res.success, res.message

    # ONE POINT AHEAD. Count how often the sweep had already committed to the
    # next position by the time a measurement finished. Serially that can never
    # happen — move k, measure k, move k+1 — so `issued > done` at a measure-end
    # is the overlap itself, with no clock involved.
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
async def test_a_sweep_that_turns_round_pays_exactly_one_frame(monkeypatch):
    """THE COST, MEASURED, in the case that actually incurs it.

    The sim's focus sits 800 steps ABOVE the start, so after extending downward
    the engine turns and extends upward — and `pos - step` is wrong exactly
    once. One strike then ends speculation for the run, so the bill is one
    frame whatever else the sweep does. This is the number that has to stay
    small: a wasted narrowband exposure is 30 s.
    """
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    assert rig.best_focus != await foc.get_position(), "the sim stopped being off-centre"
    counts = _count_frames(monkeypatch, cam, N)

    res = await N.run_native_autofocus(cam, foc, exposure_s=0.05, gain=200,
                                       step=350, steps_each_side=4, binning=1)
    assert res.success, res.message
    assert counts["expose"] - counts["measure"] <= 2, (
        f"{counts} — probe plus at most ONE discarded guess")


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
                                   step=350, steps_each_side=4, binning=1),
            timeout=20)

    assert finished[-1] == start, (
        f"the last completed move was to {finished[-1]}, not the start "
        f"position — a speculative move finished AFTER the restore\n{finished}")
    assert await foc.get_position() == start
    await asyncio.sleep(0.6)
    assert await foc.get_position() == start, (
        "a speculative move outlived the sweep that started it")
