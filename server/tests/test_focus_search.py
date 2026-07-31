"""Where to move next when you cannot see stars.

Pure decision logic, exercised against a simulated focuser whose blob size
follows the real physics (r = k*|x - focus|). If this logic is wrong, the
symptom at the scope is a focuser wandering all night, which is exactly what
happened before it existed.
"""
import pytest

from astrodeck.focus.search import (
    MAX_EXTRAPOLATION_SPAN, Probe, decide,
)
from astrodeck.imaging.defocus import HANDOVER_R80_PX

LO, HI = 0, 60000


def _r(x: float, focus: float = 38000.0, k: float = 0.02) -> float:
    """Blob radius at focuser position x — the physics the search assumes."""
    return abs(x - focus) * k


def _run(start: int, focus: float, k: float = 0.02, lo=LO, hi=HI, limit=14):
    """Drive the decision loop against the simulated focuser."""
    hist = [Probe(start, _r(start, focus, k))]
    for _ in range(limit):
        d = decide(hist, lo, hi)
        if d.done or d.give_up:
            return d, hist
        if d.move_to is None:
            return d, hist
        hist.append(Probe(d.move_to, _r(d.move_to, focus, k)))
    return decide(hist, lo, hi), hist


# ------------------------------------------------------------ convergence

@pytest.mark.parametrize("start", [0, 5000, 30000, 59000])
@pytest.mark.parametrize("focus", [12000.0, 38000.0, 55000.0])
def test_it_converges_from_anywhere_to_anywhere(start, focus):
    d, hist = _run(start, focus)
    assert d.done, (f"did not converge from {start} to focus {focus}: "
                    f"{[(p.position, round(p.r80)) for p in hist]}")
    assert hist[-1].r80 <= HANDOVER_R80_PX


def test_it_converges_in_a_handful_of_probes():
    """Every probe is an exposure and a move. A search that needs twenty is a
    search nobody will wait for."""
    d, hist = _run(0, 38000.0)
    assert d.done
    assert len(hist) <= 8, [(p.position, round(p.r80)) for p in hist]


def test_it_stops_as_soon_as_the_blob_is_small_enough():
    """Handing over is the goal; polishing here wastes the night doing worse
    what the V-curve does better."""
    d = decide([Probe(38000, HANDOVER_R80_PX - 0.1)], LO, HI)
    assert d.done and not d.give_up


def test_already_in_focus_needs_no_moves():
    d, hist = _run(38000, 38000.0)
    assert d.done and len(hist) == 1


# ------------------------------------------------------------- robustness

def test_it_never_commands_outside_the_searchable_range():
    """The focuser's REAL travel, not whatever max_position claims. On this rig
    max_position reads 600000 and the usable range is a small fraction."""
    for start in (0, 60000):
        _, hist = _run(start, 38000.0)
        assert all(LO <= p.position <= HI for p in hist), \
            [p.position for p in hist]


def test_a_wild_extrapolation_is_bounded_rather_than_obeyed():
    """Two nearly-equal measurements imply a near-zero slope and a focus
    position at infinity. Obeying that flings the focuser to the end of the
    travel on one noisy frame."""
    d = decide([Probe(10000, 100.0), Probe(11000, 99.99)], LO, HI)
    assert d.move_to is not None
    assert abs(d.move_to - 11000) <= MAX_EXTRAPOLATION_SPAN * (HI - LO)
    assert LO <= d.move_to <= HI


def test_it_gives_up_with_the_best_position_rather_than_looping_forever():
    """And it MOVES there: leaving the focuser wherever the last probe landed
    strands the rig somewhere arbitrary."""
    hist = [Probe(1000 * i, 200.0) for i in range(1, 14)]
    hist[4] = Probe(5000, 90.0)                       # the best one seen
    d = decide(hist, LO, HI, max_probes=12)
    assert d.give_up is not None
    assert d.move_to == 5000
    assert "90" in d.give_up


def test_an_empty_history_asks_for_a_measurement_first():
    d = decide([], LO, HI)
    assert d.move_to is None and not d.done and not d.give_up


def test_a_repeated_position_does_not_deadlock():
    d = decide([Probe(5000, 300.0), Probe(5000, 300.0)], LO, HI)
    assert d.move_to is not None and d.move_to != 5000


def test_moving_the_wrong_way_is_corrected_not_continued():
    """Blob grew from 100 to 140 while moving up, so focus is DOWN."""
    d = decide([Probe(10000, 100.0), Probe(12000, 140.0)], LO, HI)
    assert d.move_to is not None
    assert d.move_to < 12000, f"must turn around, went to {d.move_to}"


def test_it_works_when_focus_lies_outside_the_searchable_range():
    """Real case tonight: the focuser could only reach 0..360 while focus was
    far outside. It must give up and say so, not converge on a lie."""
    d, hist = _run(0, 200000.0, lo=0, hi=360, limit=14)
    assert d.give_up is not None, "must not claim success it cannot have"
    assert all(0 <= p.position <= 360 for p in hist)


def test_it_does_not_burn_probes_pinned_at_a_range_edge():
    """Observed on the rig: five probes in a row at 40000, because "nudge on"
    proposed a position the clamp turned back into the same number."""
    hist = [Probe(40000, 590.0), Probe(40000, 591.0)]
    d = decide(hist, 0, 40000)
    assert d.move_to is None or d.move_to < 40000, \
        f"must move off the edge, proposed {d.move_to}"


def test_pinned_with_nowhere_to_go_gives_up_rather_than_spinning():
    hist = [Probe(500, 300.0), Probe(500, 300.0)]
    d = decide(hist, 500, 500)
    assert d.give_up is not None
    assert d.move_to == 500


def test_it_moves_off_the_LOW_edge_too():
    hist = [Probe(0, 500.0), Probe(0, 500.0)]
    d = decide(hist, 0, 40000)
    assert d.move_to is not None and d.move_to > 0
