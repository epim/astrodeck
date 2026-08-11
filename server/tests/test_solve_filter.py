"""#222 — a plate solve must not inherit whatever filter the run is on.

The night this comes from: the 2026-08-10 NGC 6946 meridian flip landed on an
Ha step, the centring solve shot 0.3 s through a 3 nm passband, ASTAP returned
no solution, centring fell back to a raw GoTo, and the pointing landed 66' off
with nobody watching.
"""
import inspect

import pytest

from astrodeck.focus.filter_offsets import (default_ref_slot, luminance_slot,
                                            solve_filter_slot)
from astrodeck.hub import Hub

# The wheel this was written against, in slot order.
WHEEL = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]
NARROW = [False, False, False, False, True, True, True, False]
OPAQUE = [False, False, False, False, False, False, False, True]


class TestResolver:
    def test_narrowband_slot_moves_to_luminance(self):
        """The defect, stated as a test: mid-Ha, the solve goes to L."""
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=5) == 0

    @pytest.mark.parametrize("slot", [4, 5, 6])
    def test_every_narrowband_slot_moves(self, slot):
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=slot) == 0

    def test_opaque_slot_moves_too(self):
        """A blackout slot passes NO light — worse than narrowband, not better."""
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=7) == 0

    @pytest.mark.parametrize("slot", [0, 1, 2, 3])
    def test_broadband_slot_is_left_alone(self, slot):
        """Most solves happen mid-L/R/G/B and must not pay for a wheel move."""
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=slot) is None

    def test_configured_filter_always_wins(self):
        """An operator who NAMED a filter made a decision, not a suggestion —
        even when the automatic rule would have chosen something else."""
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=0, configured="Ha") == 5

    def test_configured_filter_is_case_insensitive(self):
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=5, configured="l") == 0

    def test_stale_configured_name_falls_through_to_the_rule(self):
        """A filter that is not on the wheel is a stale setting. It must not
        strand the solve on narrowband — the automatic rule still applies."""
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=5, configured="Sii") == 0

    def test_wheel_with_no_luminance_slot_stays_put(self):
        """Nothing better to move to. Returning a slot anyway would trade a bad
        solve for a bad solve plus two wheel moves."""
        names = ["Ha", "Oiii", "S"]
        assert solve_filter_slot(names, narrowband=[True] * 3,
                                 current_slot=0) is None

    def test_no_wheel_is_not_an_error(self):
        assert solve_filter_slot([], current_slot=0) is None
        assert solve_filter_slot(None, current_slot=0) is None

    def test_unflagged_wheel_is_left_alone(self):
        """Every wheel predates the narrowband flags. An unflagged wheel must
        behave exactly as it did before this function existed."""
        assert solve_filter_slot(WHEEL, current_slot=5) is None

    def test_current_slot_out_of_range_is_not_an_error(self):
        assert solve_filter_slot(WHEEL, narrowband=NARROW,
                                 current_slot=99) is None

    def test_already_on_luminance_returns_none_not_a_no_op_move(self):
        assert solve_filter_slot(WHEEL, narrowband=NARROW, opaque=OPAQUE,
                                 current_slot=0) is None


class TestLuminanceVocabulary:
    @pytest.mark.parametrize("name", ["L", "l", "Lum", "Luminance", "Clear",
                                      "LP", "UV/IR Cut", "UVIR", " l "])
    def test_recognised(self, name):
        assert luminance_slot([name]) == 0

    @pytest.mark.parametrize("name", ["Ha", "Oiii", "R", "Dark", "SII"])
    def test_not_recognised(self, name):
        assert luminance_slot([name]) is None

    def test_default_ref_slot_still_falls_back_to_current(self):
        """default_ref_slot was refactored onto luminance_slot; its contract
        (fall back to the wheel's current position) must not have changed."""
        assert default_ref_slot(["Ha", "Oiii"], current_position=1) == 1
        assert default_ref_slot(["Ha", "L"], current_position=0) == 1


class TestBothSolvePathsUseTheResolver:
    """The centring path and the polar path drifted apart once already: polar
    honoured the configured filter from 2026-08-07 and ``solve_and_sync``
    never did, which is what made the 66' miss possible. Assert they reach
    their answer THROUGH the shared function, so the next fix to one is
    automatically a fix to the other."""

    def test_hub_borrow_calls_the_resolver(self):
        from astrodeck.hub import Hub
        src = inspect.getsource(Hub._borrow_wheel_for_solve)
        assert "solve_filter_slot(" in src

    def test_polar_calls_the_resolver(self):
        from astrodeck.polar import native
        src = inspect.getsource(native._apply_solve_filter)
        assert "solve_filter_slot(" in src

    def test_solve_and_sync_borrows_and_returns(self):
        """Symmetric by construction: the sequence engine derives its focuser
        offset delta from the wheel's REAL position, so a borrow that is not
        returned leaves the focuser one filter's worth of steps out."""
        src = inspect.getsource(Hub.solve_and_sync)
        assert "_borrow_wheel_for_solve()" in src
        assert "_return_wheel_after_solve(" in src
        borrow = src.index("_borrow_wheel_for_solve()")
        give_back = src.index("_return_wheel_after_solve(")
        assert borrow < give_back, "the wheel is returned before it is borrowed"


@pytest.fixture
async def rig_with_wheel(monkeypatch):
    """A sim rig whose wheel carries the 2026-08-09 slot layout and flags."""
    h = Hub()
    await h.connect_sim()
    fw = h.devices["filterwheel"]
    fw.filter_names = list(WHEEL)
    fw.filter_offsets = [0, -18, 0, -18, -18, -15, 2, 0]
    fw.filter_narrowband = list(NARROW)
    fw.filter_opaque = list(OPAQUE)
    moves: list[int] = []

    async def record_move(slot):
        moves.append(int(slot))
        fw.rig.filter_slot = int(slot)
    monkeypatch.setattr(fw, "set_position", record_move)
    h._moves = moves
    try:
        yield h
    finally:
        await h.disconnect_all()


class TestTheWheelActuallyMoves:
    """The structural tests above prove the resolver is CALLED. These prove the
    wheel is driven — a call whose result is discarded would pass those and
    still lose the night."""

    async def test_borrow_moves_off_narrowband_and_reports_the_way_back(
            self, rig_with_wheel):
        h = rig_with_wheel
        fw = h.devices["filterwheel"]
        fw.rig.filter_slot = 5                      # mid-Ha, as at the flip
        back = await h._borrow_wheel_for_solve()
        assert h._moves == [0], "the solve did not move to luminance"
        assert back == 5, "the way back was not reported"

    async def test_return_puts_the_wheel_back(self, rig_with_wheel):
        h = rig_with_wheel
        fw = h.devices["filterwheel"]
        fw.rig.filter_slot = 5
        back = await h._borrow_wheel_for_solve()
        await h._return_wheel_after_solve(back)
        assert h._moves == [0, 5]
        assert await fw.get_position() == 5

    async def test_broadband_solve_touches_nothing(self, rig_with_wheel):
        h = rig_with_wheel
        h.devices["filterwheel"].rig.filter_slot = 1     # R
        assert await h._borrow_wheel_for_solve() is None
        assert h._moves == []

    async def test_return_of_none_touches_nothing(self, rig_with_wheel):
        h = rig_with_wheel
        await h._return_wheel_after_solve(None)
        assert h._moves == []

    async def test_a_wheel_that_throws_does_not_break_the_solve(
            self, rig_with_wheel, monkeypatch):
        """A solve is what RECOVERS pointing. A wheel that will not answer must
        degrade to 'solve through whatever is loaded', never to an exception."""
        h = rig_with_wheel
        fw = h.devices["filterwheel"]
        fw.rig.filter_slot = 5

        async def boom():
            raise RuntimeError("wheel jammed")
        monkeypatch.setattr(fw, "get_position", boom)
        assert await h._borrow_wheel_for_solve() is None

    async def test_a_wheel_that_will_not_return_does_not_break_the_solve(
            self, rig_with_wheel, monkeypatch):
        h = rig_with_wheel

        async def boom(slot):
            raise RuntimeError("wheel jammed")
        monkeypatch.setattr(h.devices["filterwheel"], "set_position", boom)
        await h._return_wheel_after_solve(3)    # must not raise

    async def test_no_wheel_at_all_is_not_an_error(self, rig_with_wheel):
        h = rig_with_wheel
        h.devices.pop("filterwheel")
        assert await h._borrow_wheel_for_solve() is None
        await h._return_wheel_after_solve(2)
