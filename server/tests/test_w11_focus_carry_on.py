# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""D-09 (owner-approved 2026-09-30, #590): after two sparse-field autofocus
failures, the run MOVES to the last good sweep position of THIS run, when
there is one, instead of merely naming it while staying where the sweeps
started. With no good sweep this run (the initial autofocus), it still
stays where the sweeps started -- that half of the old behaviour is
unchanged. Either way the carry-on is recorded in the session report as
well as the night log (#507 (b), WP-57; the other residuals of #507 are
WP-01 (b), WP-64 and R-7).

Ruling 4's wording (owner list item 56, H4 orchestrator ruling 4) and 5.6's
own prose are reworded to match, in
docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md.
server/tests/test_mosaic_spec_claims.py's existing pins on that text were
checked to still hold verbatim after the rewording (`_says` only requires
the phrases it names, never the sentence whole), so none of them needed
re-pinning; `test_revision_11_lists_every_section_h4_edited`,
`test_the_owner_list_records_the_h4_orchestrator_rulings` and
`test_5_6_says_the_hop_as_h4_built_it` were re-run to confirm it.

THE CASES run `SequenceEngine._autofocus` directly on the real simulator
focuser (`sim_hub`), the shape test_h4_af_sparse_retry.py's luminance case
uses: `run_autofocus` is scripted to fail sparse on both tries (the native
sweep's own contract -- a failed result's ``best_position`` is where it
started, since a failed sweep never leaves the focuser anywhere else; see
focus/native.py's `_failed`), and the REAL focuser is read back afterwards
to prove whether it moved, not merely what the log claims.

Every mutant below was applied in a private byte-backed copy of this
worktree's server/ (never the shared tree, #254), restored byte-identical
(sha256-checked) after each run, and the mutant text was grepped gone
afterwards.
"""
from __future__ import annotations

import time

import astrodeck.sequence.engine as engine_mod
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import SequencePlan

#: Where the scripted sweeps start and, failing, leave the focuser. Not a
#: real rig position -- a made-up round number for this fixture only.
START_POS = 11044
#: Where this run's last good sweep left the focuser: the RAW number a
#: successful sweep measured. D-09 does not correct it for temperature or a
#: filter offset, the same scope #507's own follow-up comment left it at.
GOOD_POS = 11064


def _sparse_at(pos: int) -> AutofocusResult:
    """A sweep that failed on a sparse field, left at ``pos`` -- exactly
    what the native sweep's own failed result always carries
    (`focus/native.py`'s ``_failed``: ``best_position`` is ``start_pos``,
    never anywhere else)."""
    return AutofocusResult(False, pos, None, [], "r_squared_below_threshold",
                           sparse_field=True, start_stars=5)


def _engine(sim_hub) -> SequenceEngine:
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(apply_filter_offsets=True)
    e._cfg = None                       # af_failure_action "warn" (default)
    return e


def _script_both_sweeps_sparse(monkeypatch) -> None:
    """Both the initial sweep and its retry fail sparse, each left exactly
    where it started -- the real focuser's own position, read live, so a
    move the carry-on makes before the retry (there is none) would still be
    scripted honestly."""
    async def run_autofocus(cam, focuser, **kw):
        return _sparse_at(await focuser.get_position())
    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)


async def test_a_prior_good_sweep_moves_the_focuser_there(sim_hub,
                                                          monkeypatch,
                                                          bus_lines):
    """D-09: both sweeps fail sparse, and this run already has a good sweep
    at GOOD_POS. The run does not merely NAME that position in the log --
    it moves the real focuser to it.

    RED under mutant "D-09 reverted" (`_carry_on_after_sparse_failures`'s
    ``if good is not None:`` move block guarded with ``if False and``,
    restoring the pre-D-09 build), observed:

        AssertionError: the run must carry on AT the last good sweep's
        position, 11064; the real focuser reads 11044
    """
    foc = sim_hub.devices["focuser"]
    foc.rig.focuser_pos = START_POS
    e = _engine(sim_hub)
    e._last_good_focus = (GOOD_POS, time.monotonic())
    _script_both_sweeps_sparse(monkeypatch)

    ok = await e._autofocus("initial autofocus")
    assert ok is False, "premise: both sweeps failed"
    now = await foc.get_position()
    assert now == GOOD_POS, (
        f"the run must carry on AT the last good sweep's position, "
        f"{GOOD_POS}; the real focuser reads {now}")
    carried = [m for _lv, m, _src in bus_lines
              if "failed on a sparse field at both exposures" in m]
    assert len(carried) == 1 and f"focuser position {GOOD_POS}" in carried[0], (
        f"the line must name the position the run actually moved to: "
        f"{carried}")


async def test_control_no_good_sweep_stays_put(sim_hub, monkeypatch,
                                               bus_lines):
    """CONTROL. The same double sparse failure, with NO good sweep this run
    (the initial autofocus case D-09 names explicitly): the run stays where
    the sweeps started, exactly as before H4's build and the mutant above
    must not disturb this half of it.
    """
    foc = sim_hub.devices["focuser"]
    foc.rig.focuser_pos = START_POS
    e = _engine(sim_hub)
    assert e._last_good_focus is None, "premise: no good sweep this run"
    _script_both_sweeps_sparse(monkeypatch)

    ok = await e._autofocus("initial autofocus")
    assert ok is False, "premise: both sweeps failed"
    now = await foc.get_position()
    assert now == START_POS, (
        f"with no good sweep this run the focuser must stay at "
        f"{START_POS}; it reads {now}")
    carried = [m for _lv, m, _src in bus_lines
              if "failed on a sparse field at both exposures" in m]
    assert len(carried) == 1 and (
        f"focuser position {START_POS}" in carried[0]
        and "no sweep has found focus yet this run" in carried[0]), carried


async def test_the_carry_on_reaches_the_session_report(sim_hub, monkeypatch):
    """WP-57 (b): the carry-on reaches the session report, not only the
    night log, through `_record_safety` -- the same bridge every other
    safety-style event in the report already goes through (a roof close,
    the no-progress watchdog; `test_safe_again_after_cooler_gate.py` tests
    that identical bridge the same way, by replacing it with a spy).

    RED under mutant "no report line" (both ``self._record_safety(msg,
    "focus_carry_on")`` calls in `_carry_on_after_sparse_failures`
    deleted), observed:

        AssertionError: the carry-on must reach the session report; nothing
        was recorded: []
    """
    foc = sim_hub.devices["focuser"]
    foc.rig.focuser_pos = START_POS
    e = _engine(sim_hub)
    e._last_good_focus = (GOOD_POS, time.monotonic())
    reported: list[tuple[str, str]] = []
    monkeypatch.setattr(
        e, "_record_safety",
        lambda reason, action: reported.append((reason, action)))
    _script_both_sweeps_sparse(monkeypatch)

    ok = await e._autofocus("initial autofocus")
    assert ok is False, "premise: both sweeps failed"
    assert len(reported) == 1, (
        f"the carry-on must reach the session report; nothing was "
        f"recorded: {reported}")
    reason, action = reported[0]
    assert ("failed on a sparse field at both exposures" in reason
            and f"focuser position {GOOD_POS}" in reason), (
        f"the report's reason must be the same carry-on sentence the "
        f"night log got: {reason!r}")
    assert action == "focus_carry_on", action
