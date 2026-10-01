"""A focuser read taken only to fill in a log number must never end the run
(#577, an #507 residual; backlog ruling WP-01 (b), owner-approved 2026-09-30).

`_carry_on_after_sparse_failures` reads the focuser's position, after a sweep
through luminance and its filter restore, purely to name a number in its
warning line. The read used to go through `_bounded`, whose whole point is to
turn a timeout into a `SafetyAbort` for a caller that means the abort to end
the run — and this caller does not. Under `af_failure_action = "warn"` (the
operator's own choice to carry on after a failed sweep), a hung focuser ended
the night anyway, through the SafetyAbort wind-down, on the strength of a read
the code's own comment and docstring both say never ends anything.

Probed on a private copy of server/ (scratchpad, never the shared tree, per
CLAUDE.md #254): a focuser whose `get_position` never answers,
`FOCUSER_MOVE_TIMEOUT_S` patched to 0.05 s, raised exactly
`SafetyAbort: focuser get_position timed out after 0s` from
`_carry_on_after_sparse_failures("initial autofocus", result, resweep=False,
restored="Ha")`. Reproduced below against the same call, bounded now by
`FOCUSER_QUERY_TIMEOUT_S` and patched to 0.2 s so the test costs real
milliseconds, not a query budget's worth of them.
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import astrodeck.sequence.engine as engine_mod
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.sequence import SequenceEngine

LABEL = "initial autofocus"


def _failed_sweep(best_position: int = 11044):
    """A failed-sweep result, shaped enough for this call: only
    ``best_position`` is read here, so a SimpleNamespace stands in for the
    real ``AutofocusResult`` (focus/native.py's), which is graded on its own
    terms elsewhere (test_h4_af_sparse_retry.py)."""
    return SimpleNamespace(best_position=best_position)


async def test_a_hung_focuser_read_never_ends_the_run(sim_hub, monkeypatch,
                                                       bus_lines):
    """The read is taken only after a sweep through luminance (``restored``
    set): a hung ``get_position`` must be caught, name no number, and never
    raise — the run carries on with the warning ``af_failure_action = "warn"``
    asked for.

    Mutant "the original `except SafetyAbort: raise`" (the read goes back
    through ``_bounded(foc.get_position(), FOCUSER_MOVE_TIMEOUT_S, "focuser
    get_position")`` and an ``except SafetyAbort: raise`` clause is restored
    ahead of the catch-all): RED -
        SafetyAbort: focuser get_position timed out after 0s
    """
    monkeypatch.setattr(engine_mod, "FOCUSER_QUERY_TIMEOUT_S", 0.2)
    # Also short, so the named mutant below (which reverts the read to
    # `_bounded(..., FOCUSER_MOVE_TIMEOUT_S, ...)`) times out in 0.2 s rather
    # than the real 180 s move budget — the mutant must still go RED fast.
    monkeypatch.setattr(engine_mod, "FOCUSER_MOVE_TIMEOUT_S", 0.2)

    async def hangs():
        await asyncio.sleep(9999)

    monkeypatch.setattr(sim_hub.devices["focuser"], "get_position", hangs)

    engine = SequenceEngine(sim_hub)
    assert engine._last_good_focus is None, "premise: no sweep has focused yet"

    await engine._carry_on_after_sparse_failures(
        LABEL, _failed_sweep(), resweep=False, restored="Ha")

    assert engine._sparse_resweep_owed is True, (
        "a failed sparse-field carry-on must still owe a re-sweep")
    assert len(bus_lines) == 1, bus_lines
    level, message, _source = bus_lines[0]
    assert level == "warning", bus_lines[0]
    assert "the focuser position" in message, (
        f"a read that timed out must name no number: {message!r}")
    assert not re.search(r"position \d", message), (
        f"the timed-out read named a number anyway: {message!r}")


async def test_control_a_read_that_answers_names_the_number(sim_hub,
                                                             monkeypatch,
                                                             bus_lines):
    """CONTROL. The same call with a focuser that answers at once: the
    position it reports is the number in the warning line, not "where the
    sweeps started" — proof the fix above is "catch the timeout", not "stop
    reading the focuser"."""
    async def answers():
        return 11064.0

    monkeypatch.setattr(sim_hub.devices["focuser"], "get_position", answers)

    engine = SequenceEngine(sim_hub)
    await engine._carry_on_after_sparse_failures(
        LABEL, _failed_sweep(), resweep=False, restored="Ha")

    assert len(bus_lines) == 1, bus_lines
    _level, message, _source = bus_lines[0]
    assert "focuser position 11064" in message, message
