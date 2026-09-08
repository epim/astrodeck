"""The approach rule is not the sweep's private habit.

`test_the_focuser_arrives_from_one_side.py` pins it inside an autofocus run.
This pins the RULE ITSELF (`focus.approach`) and the move that needed it most:
the sequence engine's per-filter offset shift.

WHY THAT MOVE. The offsets measured on this rig are 12 to 20 steps between the
broadband filters, on an EAF with about 40 steps of slack. An OUTWARD offset
move is therefore shorter than the backlash: the motor turns, the drawtube does
not, and "applied filter offset +12 for R" goes in the log either way. Nothing
downstream disagrees, because nothing downstream measures — a sweep at least
grades itself afterwards, an offset move never does. So L and G quietly shoot at
R and B's focus for the rest of the night.
"""
from __future__ import annotations

import pytest

from astrodeck.focus.approach import approach, configured_overshoot

OVERSHOOT = 200


class _Focuser:
    """Every position asked of it, in order, and every position read out."""

    def __init__(self, position: int = 10_000, max_position: int | None = 60_000):
        self._pos = int(position)
        self.moves: list[int] = []
        self.reads = 0
        if max_position is not None:
            self.max_position = int(max_position)

    async def get_position(self) -> int:
        self.reads += 1
        return self._pos

    async def move_to(self, position: int) -> None:
        self.moves.append(int(position))
        self._pos = int(position)


# ----------------------------------------------------------------- the rule

async def test_an_outward_target_is_passed_and_returned_to():
    foc = _Focuser(10_000)
    await approach(foc, 10_020, overshoot=OVERSHOOT, current=10_000)
    assert foc.moves == [10_220, 10_020]


async def test_an_inward_target_is_reached_directly():
    """There is no slack to take up in this direction, so the second leg would
    be pure cost."""
    foc = _Focuser(10_000)
    await approach(foc, 9_980, overshoot=OVERSHOOT, current=10_000)
    assert foc.moves == [9_980]


async def test_a_move_to_where_it_already_is_adds_nothing():
    foc = _Focuser(10_000)
    await approach(foc, 10_000, overshoot=OVERSHOOT, current=10_000)
    assert foc.moves == [10_000]


async def test_the_overshoot_is_clamped_to_the_focusers_ceiling():
    foc = _Focuser(10_000, max_position=10_050)
    await approach(foc, 10_020, overshoot=OVERSHOOT, current=10_000)
    assert foc.moves == [10_050, 10_020]


async def test_the_extra_leg_is_dropped_at_the_very_top_of_travel():
    """No travel left to overshoot into. The move still happens — refusing it
    would turn a mechanical nicety into a failed filter change."""
    foc = _Focuser(10_000, max_position=10_020)
    await approach(foc, 10_020, overshoot=OVERSHOOT, current=10_000)
    assert foc.moves == [10_020]


async def test_a_focuser_with_no_ceiling_still_overshoots():
    """`max_position` is not on the base Focuser contract — a driver that does
    not report one must not lose the rule."""
    foc = _Focuser(10_000, max_position=None)
    assert not hasattr(foc, "max_position")
    await approach(foc, 10_020, overshoot=OVERSHOOT, current=10_000)
    assert foc.moves == [10_220, 10_020]


async def test_zero_disables_it():
    foc = _Focuser(10_000)
    await approach(foc, 10_020, overshoot=0, current=10_000)
    assert foc.moves == [10_020]
    assert foc.reads == 0, "a disabled rule has nothing to decide, so it read " \
                           "the position for nothing"


async def test_it_reads_the_position_when_the_caller_does_not_know_it():
    foc = _Focuser(10_000)
    await approach(foc, 10_020, overshoot=OVERSHOOT)
    assert foc.reads == 1
    assert foc.moves == [10_220, 10_020]
    # …and the read decides the direction, rather than being decoration.
    foc = _Focuser(10_000)
    await approach(foc, 9_980, overshoot=OVERSHOOT)
    assert foc.reads == 1 and foc.moves == [9_980]


# ------------------------------------------------------------------ the knob

def test_the_overshoot_comes_from_the_config():
    from astrodeck.config import FocusConfig, config_store

    cfg = config_store.cfg()
    before = cfg.focus
    try:
        assert configured_overshoot() == 200        # the shipped default
        cfg.focus = FocusConfig(approach_overshoot_steps=77)
        assert configured_overshoot() == 77
        cfg.focus = FocusConfig(approach_overshoot_steps=0)
        assert configured_overshoot() == 0
    finally:
        cfg.focus = before


def test_an_unreadable_config_disables_the_rule_rather_than_the_move(monkeypatch):
    """A config that cannot be read is not a reason to refuse to move a
    focuser: the overshoot is an improvement on the move, not a precondition."""
    from astrodeck.config import config_store

    def boom():
        raise RuntimeError("config on fire")

    monkeypatch.setattr(config_store, "cfg", boom)
    assert configured_overshoot() == 0


# --------------------------------------------------- the per-filter offset move

@pytest.fixture
async def rig(tmp_path, monkeypatch):
    """A connected sim rig with a known wheel, and a recording focuser."""
    import astrodeck.config as configmod
    from astrodeck.hub import Hub
    from astrodeck.sequence.engine import SequenceEngine
    from astrodeck.sequence.models import SequencePlan

    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    h = Hub()
    await h.connect_sim()
    fw = h.devices["filterwheel"]
    foc = h.devices["focuser"]
    fw.filter_names = ["L", "R", "G", "B"]
    fw.filter_offsets = [0, 20, 2, 15]
    fw.filter_opaque = [False] * 4
    fw.filter_narrowband = [False] * 4
    moves: list[int] = []
    inner = foc.move_to

    async def move_to(position, *a, **kw):
        moves.append(int(position))
        return await inner(position, *a, **kw)

    foc.move_to = move_to
    eng = SequenceEngine(h)
    eng.plan = SequencePlan(targets=[], apply_filter_offsets=True)
    try:
        yield h, fw, foc, eng, moves
    finally:
        await h.disconnect_all()


def _step(filt: str):
    from astrodeck.sequence.models import ExposureStep
    return ExposureStep(id="s1", filter=filt, exposure_s=1.0, count=1,
                        frame_type="Light")


async def test_an_outward_filter_offset_overshoots_and_returns(rig):
    """L -> R is +20 steps on a focuser with ~40 steps of slack. Without the
    rule this move turns the motor and nothing else."""
    _h, fw, foc, eng, moves = rig
    await fw.set_position(0)
    start = await foc.get_position()
    moves.clear()
    await eng._apply_filter(_step("R"))
    assert moves == [start + 20 + OVERSHOOT, start + 20]
    assert await foc.get_position() == start + 20


async def test_an_inward_filter_offset_moves_straight_there(rig):
    """R -> G is -18. The descending direction costs exactly what it did."""
    _h, fw, foc, eng, moves = rig
    await fw.set_position(1)
    start = await foc.get_position()
    moves.clear()
    await eng._apply_filter(_step("G"))
    assert moves == [start - 18]
    assert await foc.get_position() == start - 18


async def test_the_offset_move_honours_a_zero_overshoot(rig, monkeypatch):
    from astrodeck.config import FocusConfig, config_store

    _h, fw, foc, eng, moves = rig
    cfg = config_store.cfg()
    before = cfg.focus
    try:
        cfg.focus = FocusConfig(approach_overshoot_steps=0)
        await fw.set_position(0)
        start = await foc.get_position()
        moves.clear()
        await eng._apply_filter(_step("R"))
        assert moves == [start + 20]
    finally:
        cfg.focus = before


async def test_the_offset_line_still_says_what_it_always_said(rig, bus_lines):
    """The overshoot is a mechanical detail, not news. What the operator reads
    is the offset that was applied — one info line, exactly as before."""
    _h, fw, _foc, eng, _moves = rig
    await fw.set_position(0)
    await eng._apply_filter(_step("R"))
    info = [m for lv, m, _s in bus_lines if lv == "info" and "filter offset" in m]
    assert info == ["applied filter offset +20 for R"], info
