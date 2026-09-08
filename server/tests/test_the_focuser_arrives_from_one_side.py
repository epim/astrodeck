"""Every point of a sweep is reached moving IN, including the last one.

THE MEASUREMENT THIS EXISTS FOR. The EAF on this rig has backlash measured in
the tens of steps. A sweep walks DOWNWARD through its points, so every point of
the curve is reached moving in and the slack sits on one side — but the first
move (to the top of the window), the validation frame and the final settle all
go UP, so the drawtube stops that many steps short of the position the run
believes it is at. On the virtual-clock replay of 2026-09-08 that left the run's
final physical position 39 steps below its commanded one, and made the
validation frame read 4.78 px where a swept neighbour one step away had measured
3.50: 36 percent worse, which trips `confirmed_best`'s gate, which then moves
ONE step back in — a reversal shorter than the slack, so it turns the motor and
not the tube. The backlash caused the override and the override did not cure the
backlash.

The fix is the astro-focus `Backlash` Overshoot model with only OUT backlash
set, applied by the HOST: an outward target is passed by
`config.focus.approach_overshoot_steps` and then returned to, so the last leg of
every move is inward. It is applied by the host and not asked of the engine
because `FocusConfig::reverse_sweep()` flips the initial pass to ASCEND when the
engine's Overshoot model has backlash set — turning it on there would reverse
the sweep as a side effect.

What is pinned here is that it applies to EVERY move the run makes — the swept
points, the validation frame, the final settle and the restore-to-start on the
way out of a failure — because a curve measured one way and a vertex reached
the other are two different focuses.
"""
import asyncio

import pytest

from astrodeck import providers
from astrodeck.devices.sim import build_sim_rig

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")

OVERSHOOT = 200
STEP = 350
SIDES = 4


async def _connected_sim():
    parts = build_sim_rig()
    await parts["camera"].connect()
    await parts["focuser"].connect()
    return parts["_rig"], parts["camera"], parts["focuser"]


class _Recording:
    """The real engine, with every position it ASKS for written down.

    The host's move list has to be compared against something, and the only
    honest something is what the state machine actually requested — a list of
    expected positions written by hand would pass against a loop that had
    stopped asking.
    """

    def __init__(self, inner, asks: list[int]):
        self._inner = inner
        self._asks = asks

    def next(self):
        step = self._inner.next()
        if step.get("action") == "move_to":
            self._asks.append(int(step["position"]))
        return step

    def add_measurement(self, *a, **kw):
        return self._inner.add_measurement(*a, **kw)

    def peek_next(self, *a, **kw):
        return self._inner.peek_next(*a, **kw)


async def _sweep(monkeypatch, overshoot: int, *, centred: bool = True,
                 max_position: int | None = None, fail_at_metric: int = 0):
    """Run one sim sweep. Returns (asks, moves, result, start, focuser)."""
    import astrodeck.focus.native as N
    rig, cam, foc = await _connected_sim()
    start = await foc.get_position()
    if centred:
        rig.best_focus = start
    if max_position is not None:
        foc.max_position = max_position

    asks: list[int] = []
    real_cls = N._native.FocusSweep
    monkeypatch.setattr(
        N._native, "FocusSweep",
        lambda config, start_pos: _Recording(real_cls(config, start_pos), asks))

    moves: list[int] = []
    real_move = foc.move_to

    async def move(pos, *a, **kw):
        moves.append(int(pos))
        return await real_move(pos, *a, **kw)

    foc.move_to = move

    if fail_at_metric:
        calls = {"n": 0}
        real_metric = N.native_sweep_metric

        def metric(frame):
            calls["n"] += 1
            if calls["n"] == fail_at_metric:
                raise RuntimeError("the camera link dropped mid-sweep")
            return real_metric(frame)

        monkeypatch.setattr(N, "native_sweep_metric", metric)

    coro = N.run_native_autofocus(
        cam, foc, exposure_s=0.05, gain=200, step=STEP, steps_each_side=SIDES,
        binning=1, approach_overshoot_steps=overshoot)
    if fail_at_metric:
        with pytest.raises(Exception):
            await asyncio.wait_for(coro, timeout=20)
        result = None
    else:
        result = await asyncio.wait_for(coro, timeout=60)
    return asks, moves, result, start, foc


def _expected_legs(asks: list[int], start: int, overshoot: int,
                   ceiling: int, final: int | None) -> list[int]:
    """The move list the approach rule implies, from what the engine asked.

    One leg for an inward target, two for an outward one (over the top, then
    back down onto it), and the overshoot clamped away entirely where there is
    no travel left to use.
    """
    out: list[int] = []
    current = start
    for target in list(asks) + ([] if final is None else [final]):
        if overshoot > 0 and target > current:
            over = min(target + overshoot, ceiling)
            if over > target:
                out.append(over)
        out.append(target)
        current = target
    return out


async def test_every_outward_move_overshoots_and_returns(monkeypatch):
    """THE RULE, over a whole successful sweep: what the focuser was commanded
    is exactly what the engine asked for, with an extra leg over the top of
    every OUTWARD target and nothing added to the inward ones."""
    asks, moves, res, start, foc = await _sweep(monkeypatch, OVERSHOOT)
    assert res is not None and res.success, res
    expected = _expected_legs(asks, start, OVERSHOOT, foc.max_position,
                              res.best_position)
    assert moves == expected, f"asks={asks}\nmoves={moves}\nexpected={expected}"
    # The sweep is centred on the sim's focus, so it walks DOWN from the top of
    # the window: the first move and the settle at the end are the outward ones.
    overshoots = [m for m in moves if m in {a + OVERSHOOT for a in asks}]
    assert len(overshoots) >= 2, moves


async def test_the_last_leg_of_every_move_is_inward(monkeypatch):
    """The property the rule exists for, stated without reference to the rule.

    Whatever the engine asks for, the tube arrives from above — so the slack is
    taken up the same way at every point of the curve, at the validation frame
    and at the final position.
    """
    asks, moves, res, start, _foc = await _sweep(monkeypatch, OVERSHOOT)
    assert res is not None and res.success, res
    targets = set(asks) | {res.best_position}
    current = start
    for i, m in enumerate(moves):
        if m > current and m in targets:
            # An outward arrival at a position the run MEASURES or settles on.
            assert False, (
                f"move {i} to {m} arrived outward from {current}\n{moves}")
        current = m


async def test_an_inward_target_is_reached_directly(monkeypatch):
    """No second move where there is no slack to take up: the descending half
    of the sweep costs exactly what it did before."""
    asks, moves, res, start, _foc = await _sweep(monkeypatch, OVERSHOOT)
    assert res is not None and res.success, res
    descending = 0
    current = start
    for target in asks:
        if target < current:
            descending += 1
            assert target in moves
        current = target
    assert descending >= 6, f"only {descending} inward points in {asks}"
    # And nothing is inserted before an inward target: the move that reaches it
    # comes straight after the move that reached the point before it.
    current = start
    for prev, target in zip([start] + asks, asks):
        if target < prev:
            i = moves.index(target)
            assert i > 0 and moves[i - 1] == prev, (
                f"a leg was inserted before the inward target "
                f"{target}: {moves}")
        current = target
    assert current == asks[-1]


async def test_the_overshoot_clamps_at_the_focusers_ceiling(monkeypatch):
    """There is no travel above the ceiling to overshoot into, so the leg is
    clamped and — when the ceiling IS the target — dropped entirely. Refusing
    the move instead would turn a mechanical nicety into a failed sweep."""
    _rig, _cam, foc = await _connected_sim()
    start = await foc.get_position()
    ceiling = start + STEP * SIDES + 50        # 50 steps above the top point
    asks, moves, res, start2, _foc = await _sweep(
        monkeypatch, OVERSHOOT, max_position=ceiling)
    assert res is not None and res.success, res
    assert start2 == start
    assert max(moves) == ceiling, moves
    top = start + STEP * SIDES
    assert moves[:2] == [ceiling, top], moves[:4]


async def test_zero_disables_it(monkeypatch):
    """A focuser with no measurable backlash pays two moves per outward step for
    nothing, so 0 is a real setting and not a disabled-feature stub."""
    asks, moves, res, start, _foc = await _sweep(monkeypatch, 0)
    assert res is not None and res.success, res
    assert moves == asks + [res.best_position], f"asks={asks}\nmoves={moves}"


async def test_the_teardown_restore_obeys_it_too(monkeypatch):
    """THE MOVE NOBODY WATCHES. A sweep torn down half way — a camera link that
    dropped, a halt from the UI — restores the start position, and by then the
    focuser is usually BELOW it. That restore is where the night's remaining
    frames are shot from, so it is the one arrival that must not be left short.
    """
    # The sixth point of a centred nine-point sweep sits one step below the
    # start, so the restore is an outward move.
    _asks, moves, _res, start, _foc = await _sweep(
        monkeypatch, OVERSHOOT, fail_at_metric=6)
    assert moves[-2:] == [start + OVERSHOOT, start], moves[-4:]


# ------------------------------------------------------------------- the knob

def test_the_default_is_the_measured_backlash_with_room_to_spare():
    from astrodeck.config import FocusConfig
    assert FocusConfig().approach_overshoot_steps == 200
    with pytest.raises(Exception):
        FocusConfig(approach_overshoot_steps=-1)
    with pytest.raises(Exception):
        FocusConfig(approach_overshoot_steps=5001)


def test_the_run_reads_the_knob_from_the_config(monkeypatch):
    """The keyword override exists for the tests; the RIG gets it from
    `config.focus`, and a setting nothing reads is a setting that does not
    exist. 77 rather than a round number so a default cannot pass for it."""
    from astrodeck.config import FocusConfig, config_store

    cfg = config_store.cfg()
    before = cfg.focus
    try:
        cfg.focus = FocusConfig(approach_overshoot_steps=77)

        async def body():
            _asks, moves, res, start, _foc = await _sweep(monkeypatch, None)
            assert res is not None and res.success, res
            assert (start + STEP * SIDES + 77) in moves, moves

        asyncio.run(body())
    finally:
        cfg.focus = before


def test_the_echo_carries_it_so_the_rig_can_be_asked(tmp_path, monkeypatch):
    """Where the key surfaces: in every `/api/config` payload, which is how an
    operator finds out what their focuser is being driven with.

    There is deliberately no write route — see `config.FocusConfig`. A route no
    screen calls is a feature nobody can reach, and this suite has a detector
    that says so (`test_routes_have_callers`); the value ships correct for the
    rig's EAF, so the knob is a correction for a different focuser, made in
    `astrodeck.json` with the server stopped.
    """
    from fastapi.testclient import TestClient

    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module

    with TestClient(app_module.create_app()) as c:
        assert c.get("/api/config").json()["focus"] == {
            "approach_overshoot_steps": 200}
    # And a file written with a different value is what the run then reads.
    (tmp_path / "astrodeck.json").write_text(
        '{"version": 1, "focus": {"approach_overshoot_steps": 0}}',
        encoding="utf-8")
    monkeypatch.setattr(config_store, "_cfg", None)
    assert config_store.cfg().focus.approach_overshoot_steps == 0
