"""The engine answers ahead of its own measurement, and names each move.

A sweep point costs `move -> expose -> measure`, and on a 26 MP frame the
measure half is most of the wall clock. Overlapping the two means knowing where
the engine will send the focuser next -- and until now the host guessed, with a
descending `pos - step` model that stops describing the engine at the
turn-round and has to stand aside before the validation move
(`test_the_sweep_exposes_ahead.py`). `FocusSweep.peek_next` replaces the guess
with the engine's own answer for a hypothetical measurement, computed on a
clone so the real machine is untouched, and every step now carries a `kind`
saying what it expects back -- so the validation move is RECOGNISED instead of
counted to.

The state machine itself is unit-tested in Rust (`native/crates/astro-focus/
src/sweep.rs`). What is pinned here is the binding: the dict shape, that a peek
matches what the engine then asks for, that it keeps working through the
turn-round, and that it names the validation move.
"""
import math

import pytest

from astrodeck import providers

if providers.NATIVE_AVAILABLE:
    import astrodeck_native

    _HAS_PEEK = hasattr(astrodeck_native.FocusSweep, "peek_next")
else:  # the module below is skipped entirely; keep the names defined
    astrodeck_native = None
    _HAS_PEEK = False

pytestmark = [
    pytest.mark.skipif(
        not providers.NATIVE_AVAILABLE,
        reason="astrodeck_native wheel not installed"),
    pytest.mark.skipif(
        providers.NATIVE_AVAILABLE and not _HAS_PEEK,
        reason="installed astrodeck_native wheel predates peek_next"),
]

STEP = 350
SIDES = 4
LIMIT = 500          # a broken machine must fail the test, not hang the suite


def _config():
    """The config dict ``focus/native.py`` builds, with its own gate constants,
    so this file cannot pass against a shape the host does not send."""
    from astrodeck.focus.native import CURVE_FITTING, R_SQUARED_THRESHOLD
    return {
        "step_size": STEP,
        "offset_steps": SIDES,
        "max_position": 40000,
        "curve_fitting": CURVE_FITTING,
        "r_squared_threshold": R_SQUARED_THRESHOLD,
    }


def _hyperbola(focus):
    """The V this rig measures: a 2.5 px waist on a 47-steps-per-pixel flank,
    so `focus +/- 4*350` reaches about 30 px like a real nine-point sweep.
    Returns (hfr, stdev, star_count)."""
    def measure(pos):
        d = (pos - focus) / 47.0
        return math.sqrt(2.5 ** 2 + d * d), 0.1, 30
    return measure


def _same_step(a, b):
    """The comparable content of two step dicts. Not raw equality: a `done`
    step carries a whole fit, and one NaN R^2 inside it would make two
    identical outcomes compare unequal."""
    key = [a["action"], a["kind"], a.get("position"), a.get("reason")]
    key_b = [b["action"], b["kind"], b.get("position"), b.get("reason")]
    if a["action"] == "done" and b["action"] == "done":
        key.append(a["outcome"]["best_position"])
        key_b.append(b["outcome"]["best_position"])
    return key == key_b, (key, key_b)


def test_a_peek_is_what_the_engine_then_asks_for():
    """THE PROPERTY THE HOST BETS AN EXPOSURE ON. At every point of a clean
    sweep -- initial pass, extensions, validation move, and the terminal
    `done` -- what `peek_next` says is what `next()` returns once the same
    measurement is really added. And peeking (twice, once with nonsense) does
    not move the machine that is peeked."""
    focus = 12_000
    measure = _hyperbola(focus)
    sweep = astrodeck_native.FocusSweep(_config(), focus)

    step = sweep.next()
    checked = 0
    for _ in range(LIMIT):
        if step["action"] != "move_to":
            break
        pos = step["position"]
        hfr, sd, n = measure(pos)

        peeked = sweep.peek_next(pos, hfr, sd, n)
        # A nonsense peek in between: if peeking mutated, this is what would
        # poison the run, and the agreement below would collapse.
        sweep.peek_next(pos, 99.0, 9.0, 0)
        again = sweep.peek_next(pos, hfr, sd, n)
        ok, seen = _same_step(peeked, again)
        assert ok, f"two peeks at {pos} disagreed: {seen}"

        sweep.add_measurement(pos, hfr, sd, n)
        step = sweep.next()
        ok, seen = _same_step(peeked, step)
        assert ok, f"peek after {pos} disagreed with the engine: {seen}"
        checked += 1

    assert step["action"] == "done", step
    assert checked >= 2 * SIDES + 1, f"only {checked} points, expected the full sweep"


def test_every_step_says_what_it_expects_back():
    """`kind` on the dict `next()` returns: each swept move is a `point`, the
    last move is the `validation`, and the terminal step expects nothing."""
    focus = 12_000
    measure = _hyperbola(focus)
    sweep = astrodeck_native.FocusSweep(_config(), focus)

    kinds = []
    step = sweep.next()
    for _ in range(LIMIT):
        if step["action"] != "move_to":
            break
        kinds.append(step["kind"])
        pos = step["position"]
        sweep.add_measurement(pos, *measure(pos))
        step = sweep.next()

    assert step["action"] == "done", step
    assert step["kind"] == "none", "a terminal step expects no measurement back"
    assert kinds[-1] == "validation", kinds
    assert set(kinds[:-1]) == {"point"}, kinds


def test_the_guess_survives_the_turn_round():
    """WHAT THE RULE OF THUMB COULD NOT DO. With the focus two steps ABOVE the
    start the engine walks down through the initial pass and the left
    extensions, then turns and extends UP from the highest point it has
    measured. `pos - step` is wrong exactly there -- one wasted exposure, and
    under the one-strike rule no speculation for the rest of the run. `peek`
    names the upward move instead, and still calls it a curve point."""
    start = 10_000
    focus = start + 2 * STEP
    measure = _hyperbola(focus)
    sweep = astrodeck_native.FocusSweep(_config(), start)

    highest = None
    turn = None
    step = sweep.next()
    for _ in range(LIMIT):
        if step["action"] != "move_to":
            break
        pos = step["position"]
        highest = pos if highest is None else max(highest, pos)
        hfr, sd, n = measure(pos)
        peeked = sweep.peek_next(pos, hfr, sd, n)
        if (turn is None and peeked["action"] == "move_to"
                and peeked["position"] > pos):
            turn = (highest, peeked["position"], peeked["kind"])
        sweep.add_measurement(pos, hfr, sd, n)
        step = sweep.next()

    assert step["action"] == "done", step
    assert turn is not None, "the sweep never turned round"
    highest_at_turn, target, kind = turn
    assert target == highest_at_turn + STEP, turn
    assert kind == "point", turn


def test_the_validation_move_is_recognised_not_counted():
    """The move after the swept points goes UP to the fitted focus, which is
    why the descending model had to stop guessing before it. The peeked step
    says `validation` on its own, so the host can decide about that move (skip
    it, approach it from the other side) instead of counting to it."""
    focus = 12_000
    measure = _hyperbola(focus)
    sweep = astrodeck_native.FocusSweep(_config(), focus)

    validation = None
    kinds_before = []
    step = sweep.next()
    for _ in range(LIMIT):
        if step["action"] != "move_to":
            break
        pos = step["position"]
        hfr, sd, n = measure(pos)
        peeked = sweep.peek_next(pos, hfr, sd, n)
        if peeked["kind"] == "validation" and validation is None:
            assert peeked["action"] == "move_to", peeked
            validation = peeked["position"]
        elif validation is None:
            kinds_before.append(peeked["kind"])
        sweep.add_measurement(pos, hfr, sd, n)
        step = sweep.next()

    assert step["action"] == "done", step
    assert validation is not None, "no validation move was ever peeked"
    assert abs(validation - focus) <= STEP, (
        f"the validation move went to {validation}, more than a step from {focus}")
    assert set(kinds_before) == {"point"}, kinds_before
