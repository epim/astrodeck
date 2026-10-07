# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A solve failure carries a stable rig-side reason (#618; WP-101 of the
2026-09-30 open-issue backlog; backlog ruling D-03, owner-approved
2026-09-30).

THE DEFECT. D-03 sets a mosaic aside at once "when every failure in two
passes in a row carries the same rig-side reason code". WP-21 built that
rule (`group_rules._held_pass_reason_code`), but the only ``last_error`` the
engine ever attached to a failed solve was the one generic sentence
``GENERIC_SOLVE_FAILURE``, which the rule deliberately reads as no code. So
the immediate half of D-03 never fired for a real centring hold: a solver
that is not installed, or a solve frame another program holds open, cost
six held passes (about an hour) where two (about twenty minutes) were meant.

THE FIX. A cause is a TYPE, then a sentence of fixed words:

* ``providers.SolverUnavailable`` (a ``DeviceError``) is raised at both of
  the refusals that mean "no plate solver can run on this rig".
* ``hub.solve_failure_reason(exc)`` maps ``SolveFrameTransient`` and
  ``SolverUnavailable`` to a fixed sentence (``SOLVE_REASON_FILE_LOCKED``,
  ``SOLVE_REASON_SOLVER_MISSING``) and EVERYTHING ELSE to None: a cloud
  verdict, a no-light verdict, an ASTAP timeout, an unknown error all keep
  the generic text, because the sky (or an unvalidated classifier, #308)
  may be to blame and the system cannot yet say which.
* ``hub.goto_and_center``'s solve-failure return carries ``solve_reason``
  when there is one, and the engine's ``_setup_target`` puts it where
  ``GENERIC_SOLVE_FAILURE`` went.

THE SENTENCES RIDE ``last_error``, the hold's clock and the set-aside alert
(which quotes the code with ``!r``), so they hold words only: no number, no
path, no site datum.

Every mutant was applied to a byte-for-byte backup of the file it mutates
inside this worktree, restored and sha256-checked after each (never left in
the tree). The exact failing assertion is quoted in the test it turns red,
and in this work package's own report.
"""
from __future__ import annotations

import asyncio
import errno
import re
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
import astrodeck.providers as providers_module
from _group_harness import (GROUP_NAME, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.devices.base import DeviceError
from astrodeck.hub import (SOLVE_REASON_FILE_LOCKED,
                           SOLVE_REASON_SOLVER_MISSING, Hub,
                           SolveFrameTransient, solve_failure_reason)
from astrodeck.providers import SolverUnavailable
from astrodeck.sequence.group_rules import (CENTRING, CENTRING_HOLD_RETRY_S,
                                            DEFER_WAIT_S,
                                            GENERIC_SOLVE_FAILURE,
                                            HELD_PASS_ALERT_AT,
                                            HELD_PASS_SET_ASIDE_AT,
                                            SOLVE_TRANSIENT, GroupRun,
                                            PanelDeferred,
                                            _held_pass_reason_code)
from astrodeck.sequence.session import session_store
from astrodeck.solve.light import (BIAS_MASTER, DARK_MASTER, EXPLICIT,
                                   SELF_SHOT, FailedSolveError,
                                   LightVerdict, NoLightError, Reference)

#: Words that mean "two held passes set the mosaic aside at once"
#: (`GroupRun._apply_held_pass_rule`'s same-reason sentence).
IMMEDIATE = "held passes in a row of"
#: ... and the counted path's own sentence (the sixth held pass).
COUNTED = "the mosaic has been held for"


def _verdict(kind: str, reference: Reference | None) -> LightVerdict:
    return LightVerdict(kind=kind, reference=reference, why="a fixed reason")


# ======================================================== (a) the mapping

def test_the_sentences_are_words_only_and_distinct():
    """The reason rides ``last_error``, the hold clock's key and the
    set-aside alert, which quotes it with ``!r``: a digit, a path separator
    or a drive letter in it would restart the clock on every retry or leak
    a path. Fixed words, each different from the other and from the
    generic text (which `_held_pass_reason_code` reads as no code).

    RED under mutant "a number in the sentence" (``SOLVE_REASON_FILE_LOCKED``
    ending ``file open 3 times``), observed:

        AssertionError: plate solve failed: another program held the solve frame's file open 3 times
        assert not <re.Match object; span=(69, 70), match='3'>
    """
    reasons = (SOLVE_REASON_SOLVER_MISSING, SOLVE_REASON_FILE_LOCKED)
    assert len(set(reasons) | {GENERIC_SOLVE_FAILURE}) == 3
    for r in reasons:
        assert isinstance(r, str) and r.startswith("plate solve failed"), r
        assert not re.search(r"[0-9]", r), r
        assert "\\" not in r and "/" not in r, r


@pytest.mark.parametrize("exc, expected", [
    (SolveFrameTransient("the solve frame could not be written"),
     SOLVE_REASON_FILE_LOCKED),
    (SolverUnavailable("plate solving unavailable: ASTAP not found"),
     SOLVE_REASON_SOLVER_MISSING),
])
def test_a_rig_side_cause_maps_to_its_fixed_sentence(exc, expected):
    """Both rig faults that will not clear on their own name their cause.

    RED under mutant "the file-locked cause has no reason" (the
    ``isinstance(exc, SolveFrameTransient)`` test in ``solve_failure_reason``
    made ``False``), observed:

        assert None == "plate solve failed: another program held the solve frame's file open"
         +  where None = solve_failure_reason(SolveFrameTransient('the solve frame could not be written'))
    """
    assert solve_failure_reason(exc) == expected


@pytest.mark.parametrize("reference", [
    None,
    Reference(level=100.0, kind=DARK_MASTER),
    Reference(level=100.0, kind=BIAS_MASTER),
    Reference(level=100.0, kind=SELF_SHOT),
    Reference(level=100.0, kind=EXPLICIT),
], ids=["no-reference", DARK_MASTER, BIAS_MASTER, SELF_SHOT, EXPLICIT])
def test_a_no_light_verdict_is_never_given_a_reason(reference):
    """RULING (the orchestrator's, WP-101): do NOT wire the no-light verdict
    -- #308 says the classifier is unvalidated, so a "no light" reason would
    set a mosaic aside on a guess. Every ``NoLightError`` stays the
    generic text, whatever it was judged against, the dark master's own
    level included (the follow-on once #308 closes).

    RED under mutant "a no-light verdict carries a reason" (``solve_failure_
    reason`` returning ``SOLVE_REASON_SOLVER_MISSING`` for any
    ``NoLightError``), observed:

        AssertionError: assert 'plate solve failed: no plate solver is available on this rig' is None
    """
    exc = NoLightError("plate solve failed: no light",
                       _verdict("no_light", reference))
    assert solve_failure_reason(exc) is None


@pytest.mark.parametrize("exc", [
    FailedSolveError("plate solve failed: cloud",
                     _verdict("cloud", Reference(level=100.0,
                                                 kind=DARK_MASTER))),
    FailedSolveError("plate solve failed: no verdict",
                     _verdict("no_verdict", None)),
    DeviceError("ASTAP timed out after 60 s"),
    asyncio.TimeoutError(),
    RuntimeError("something nobody has seen before"),
    Exception("a bare exception"),
    # THE CAUSE IS A TYPE, NOT TEXT: a DeviceError that merely SAYS the
    # solver is missing (a driver's own message, a future refusal that is
    # not this one) is not read as one.
    DeviceError("plate solving unavailable: ASTAP not found"),
    DeviceError("the solve frame's file is held by another process"),
], ids=["cloud", "no-verdict", "astap-timeout", "asyncio-timeout",
        "unknown", "bare", "text-only-solver", "text-only-lock"])
def test_everything_else_keeps_the_generic_text(exc):
    """The sky may be to blame for a cloud verdict, a timeout or an unknown
    error, and the system cannot tell, so none of them is a rig-side code:
    None, which the engine reads as `GENERIC_SOLVE_FAILURE`.

    RED under mutant "the cause is read from the words" (the
    ``isinstance(exc, SolverUnavailable)`` test replaced by ``"ASTAP" in
    str(exc)``), observed:

        AssertionError: assert 'plate solve failed: no plate solver is available on this rig' is None
         +  where 'plate solve failed: no plate solver is available on this rig' = solve_failure_reason(DeviceError('plate solving unavailable: ASTAP not found'))

    and, under "everything gets a reason" (the same test made ``True``),
    every case of this table goes red with the same message."""
    assert solve_failure_reason(exc) is None


# ======================================================= (a2) the provider

def test_the_resolvers_refusal_raises_the_typed_error(monkeypatch):
    """The resolver's refusal -- a real mount or focuser is connected and
    ASTAP is not installed, so the simulator solver is refused -- raises
    `SolverUnavailable` with the words it always had, so the status row
    (``resolve_all``) reads as it did.

    RED under mutant "the resolver's refusal is a plain DeviceError" (the
    ``raise SolverUnavailable(`` in ``_resolve_solve`` back to ``raise
    DeviceError(``), observed (the plain error escapes ``pytest.raises``):

        astrodeck.devices.base.DeviceError: plate solving unavailable: ASTAP not found and a real mount/focuser is connected - refusing the simulator solver (install ASTAP or set ASTAP_PATH)
    """
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    monkeypatch.setattr(providers_module, "_rig_has_real_motion",
                        lambda hub: True)
    with pytest.raises(SolverUnavailable) as caught:
        providers_module.resolve("solve", object())
    assert isinstance(caught.value, DeviceError)
    assert str(caught.value).startswith("plate solving unavailable: ASTAP "
                                        "not found"), caught.value


def test_a_solver_that_vanished_raises_the_typed_error(monkeypatch):
    """``pick_solver``'s own refusal: resolution finds ASTAP, and it is gone
    by the time it is used. The same type, the same words.

    RED under mutant "pick_solver's refusal is a plain DeviceError" (``raise
    SolverUnavailable("ASTAP disappeared`` back to ``raise DeviceError(``),
    observed:

        astrodeck.devices.base.DeviceError: ASTAP disappeared between resolution and use
    """
    answers = iter(["astap.exe", None])
    monkeypatch.setattr(providers_module, "find_astap",
                        lambda: next(answers))
    with pytest.raises(SolverUnavailable) as caught:
        providers_module.pick_solver(object())
    assert str(caught.value) == "ASTAP disappeared between resolution and use"


def test_the_status_row_still_reads_the_refusal(monkeypatch):
    """``resolve_all`` never raises: a refusal becomes an ``unavailable``
    row carrying the reason, as it did when the refusal was a plain
    DeviceError (a subclass is caught by the same clause)."""
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    monkeypatch.setattr(providers_module, "_rig_has_real_motion",
                        lambda hub: True)
    row = providers_module.resolve_all(object())["solve"]
    assert row["kind"] == "unavailable"
    assert row["reason"].startswith("plate solving unavailable: ASTAP not "
                                    "found"), row


# ===================================================== (b) the hub's result

RA, DEC = 5.0, 10.0


@pytest.fixture(autouse=True)
def _the_sun_is_not_the_subject(monkeypatch):
    """``goto_and_center`` refuses a target within the Sun-exclusion cone
    (30 degrees by default) of the Sun's place TODAY, read from the real
    wall clock, and (RA, DEC) above is inside that cone for about three
    months of every year (mid-April to mid-July): each test below that
    runs the real ``goto_and_center`` on ``sim_hub`` would then raise
    ``target is within N deg of the Sun`` before it ever solved. Replayed
    with the Sun pinned at RA 4.9 h, Dec +21.5 deg, six of them failed that
    way. The cone is not what these tests are about, so it is stubbed the
    way ``_group_harness.night_hub`` stubs it for the night tests.
    """
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)


def _on_target(sim_hub) -> None:
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC


@pytest.fixture
def quick_backoff(monkeypatch):
    """A backoff this suite can afford: 13 ms instead of the real 0.2 s."""
    monkeypatch.setattr(hub_module, "SOLVE_WRITE_BACKOFF_S", 0.013)


def _sharing_violation(path) -> PermissionError:
    """What Windows raises when another process holds ``path``."""
    e = PermissionError(errno.EACCES, "The process cannot access the file "
                        "because it is being used by another process",
                        str(path))
    e.winerror = 32
    return e


async def test_a_missing_solver_reaches_the_centring_result(
        sim_hub, monkeypatch):
    """THE REAL REFUSAL, end to end through the hub: a real mount is
    connected and ASTAP is not installed, so the REAL ``solve_and_sync``
    refuses at ``pick_solver``, and ``goto_and_center`` returns the fixed
    sentence as ``solve_reason`` beside the keys it always carried. It is
    the rig's fault, not a transient one, so no ``solve_transient``.

    RED under mutant "the hub drops the reason" (the ``solve_reason`` merge
    removed from ``goto_and_center``'s solve-failure return), observed:

        KeyError: 'solve_reason'
    """
    _on_target(sim_hub)
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    monkeypatch.setattr(providers_module, "_rig_has_real_motion",
                        lambda hub: True)

    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["centered"] is False and result["solve_failed"] is True
    assert result["error_arcmin"] is None, result
    assert result["solve_reason"] == SOLVE_REASON_SOLVER_MISSING, result
    assert "solve_transient" not in result, result


async def test_a_held_solve_frame_reaches_the_centring_result(
        sim_hub, monkeypatch, quick_backoff):
    """The other rig-side cause: every attempt's solve frame held by another
    process on every bounded retry (#532, #576). It keeps its transient
    keys and now names the cause too.

    RED under mutant "the file-locked cause has no reason" (the
    ``isinstance(exc, SolveFrameTransient)`` test in ``solve_failure_reason``
    made ``False``), observed:

        KeyError: 'solve_reason'
    """
    _on_target(sim_hub)

    def save_fits(frame, path, **kw):
        raise _sharing_violation(path)

    monkeypatch.setattr(hub_module, "save_fits", save_fits)
    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["solve_failed"] is True, result
    assert result.get("centring_solve_transient") is True, result
    assert result["solve_reason"] == SOLVE_REASON_FILE_LOCKED, result


@pytest.mark.parametrize("exc", [
    DeviceError("plate solve failed: not enough stars"),
    RuntimeError("ASTAP exploded"),
    FailedSolveError("plate solve failed: cloud",
                     _verdict("cloud", Reference(level=1.0))),
], ids=["device-error", "unknown", "cloud"])
async def test_any_other_failure_carries_no_reason_key(
        sim_hub, monkeypatch, exc):
    """CONTROL: a solve that failed for a cause the system cannot name adds
    nothing: the key is absent (the additive contract), so the engine falls
    back to the generic text."""
    _on_target(sim_hub)

    async def failing(exposure_s=3.0, **kw):
        raise exc

    monkeypatch.setattr(sim_hub, "solve_and_sync", failing)
    result = await sim_hub.goto_and_center(RA, DEC)

    assert result["solve_failed"] is True, result
    assert "solve_reason" not in result, result


async def test_a_centred_goto_carries_no_reason(sim_hub):
    """CONTROL: the reason belongs to the solve-failure return alone."""
    _on_target(sim_hub)
    result = await sim_hub.goto_and_center(RA, DEC)
    assert result["centered"] is True, result
    assert "solve_reason" not in result, result


# ===================================== (c) the engine, a mosaic night, end to end

def _failed_hop(exc: BaseException):
    """A ``goto`` script for `Night`: every centring comes back as the hub's
    solve-failure result for ``exc``, built the way the hub builds it (the
    one ``solve_failure_reason`` the hub itself calls)."""
    def goto(who, n, result):
        out = {"centered": False, "error_arcmin": None, "attempts": 1,
               "solve_failed": True, "rotation": None}
        reason = solve_failure_reason(exc)
        if reason is not None:
            out["solve_reason"] = reason
        if isinstance(exc, SolveFrameTransient):
            out["centring_solve_transient"] = True
            out["solve_transient"] = True
        return out
    return goto


async def _night(group_hub, monkeypatch, goto) -> Night:
    night = Night(group_hub, monkeypatch, goto=goto)
    try:
        night.done = await night.run(grid_plan(rows=2, cols=2))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _set_aside_all_group(night: Night) -> list[dict]:
    aside = night.stored.set_aside
    assert night.done, night.lines[-5:]
    assert len(aside) == 4, ("every panel of the 2x2 is set aside", aside)
    assert {a["kind"] for a in aside} == {"group"}, aside
    return aside


async def test_a_missing_solver_on_two_passes_sets_the_mosaic_aside_at_pass_two(
        group_hub, monkeypatch):
    """D-03's immediate path, for the first time on a real centring hold:
    every hop of a 2x2 mosaic fails because no plate solver is available,
    on two consecutive passes. Both passes are all-fail centring holds, so
    both carry the identical rig-side reason, and the mosaic is set aside
    at the SECOND pass with the two-passes-in-a-row wording, not the sixth.

    RED under mutant "the engine drops the reason" (``_setup_target``'s
    ``detail`` back to ``GENERIC_SOLVE_FAILURE`` alone), observed:

        AssertionError: ([], [(..., 'info', 'M31: the centring solve could not run on 1-2: plate solve failed - used raw GoTo; retrie...
        assert 0 == 1
         +  where 0 = len([])

    (the deferral still carried the generic text, so the counted path ran
    to the sixth pass and no two-passes-in-a-row line was ever said)
    """
    night = await _night(group_hub, monkeypatch, _failed_hop(
        SolverUnavailable("plate solving unavailable: ASTAP not found")))

    aside = _set_aside_all_group(night)
    stop = night.said(IMMEDIATE)
    assert len(stop) == 1, (stop, night.lines[-6:])
    assert repr(SOLVE_REASON_SOLVER_MISSING) in stop[0], stop[0]
    assert night.said(COUNTED) == [], "the counted path must not also fire"
    assert SOLVE_REASON_SOLVER_MISSING in aside[0]["reason"], aside[0]
    # The ALERT is the third pass's: it never came.
    assert not [m for _t, lvl, m in night.lines
                if lvl == "warning"
                and m.startswith(f"{GROUP_NAME}: held for")], night.lines
    # Two passes, four panels each; the second pass closes one hold after
    # the first (hops take no fake time), and nothing hops after it.
    assert len(night.gotos) == 8, night.gotos
    last_hop = max(night.rel(t) for t, _who in night.gotos)
    assert last_hop == CENTRING_HOLD_RETRY_S, last_hop


async def test_a_held_solve_frame_on_two_passes_sets_the_mosaic_aside_at_pass_two(
        group_hub, monkeypatch):
    """The same rule on the OTHER held kind (#576): every hop's centring
    solve could not run because its frame's file was held open, two passes
    in a row. These are all-transient ``defer_wait`` passes (no strike, no
    centring pass rule), and with the reason attached the second one sets
    the mosaic aside at once, instead of waiting out the sixth.

    RED under mutant "the file-locked cause has no reason" (as above), and
    under "the engine drops the reason", observed:

        AssertionError: ([], [(..., 'info', 'M31: the centring solve could not run on 1-2: plate solve failed - used raw GoTo; retrie...
        assert 0 == 1
         +  where 0 = len([])
    """
    night = await _night(group_hub, monkeypatch, _failed_hop(
        SolveFrameTransient("the solve frame could not be written")))

    aside = _set_aside_all_group(night)
    stop = night.said(IMMEDIATE)
    assert len(stop) == 1, (stop, night.lines[-6:])
    assert repr(SOLVE_REASON_FILE_LOCKED) in stop[0], stop[0]
    assert night.said(COUNTED) == []
    assert SOLVE_REASON_FILE_LOCKED in aside[0]["reason"], aside[0]
    assert len(night.gotos) == 8, night.gotos
    assert max(night.rel(t) for t, _who in night.gotos) == DEFER_WAIT_S


@pytest.mark.parametrize("exc", [
    FailedSolveError("plate solve failed: cloud",
                     _verdict("cloud", Reference(level=1.0,
                                                 kind=DARK_MASTER))),
    NoLightError("plate solve failed: no light",
                 _verdict("no_light", Reference(level=1.0,
                                                kind=DARK_MASTER))),
    DeviceError("ASTAP timed out"),
], ids=["cloud", "no-light", "timeout"])
async def test_a_generic_failure_only_counts_and_is_never_set_aside_at_two(
        group_hub, monkeypatch, exc):
    """CONTROL (D-03's counted path, unchanged): a failure the system cannot
    name as the rig's keeps the generic text on every pass, and the held
    passes count -- the alert at the third, the set-aside at the sixth,
    never the immediate one at the second. The no-light verdict is here
    on purpose (RULING: not wired, #308).

    RED under mutant "everything gets a reason" (``solve_failure_reason``
    returning ``SOLVE_REASON_SOLVER_MISSING`` for any exception), observed:

        AssertionError: ["M31: two held passes in a row of the mosaic gave the identical reason ('plate solve failed: no plate solver is avail...
        assert ['M31: two he...t night does'] == []

    (the immediate sentence fired at pass two for a cloud verdict). The
    same case is red under "the generic guard deleted" (``errors[0] !=
    GENERIC_SOLVE_FAILURE`` dropped from ``_held_pass_reason_code``), where
    the sentence quotes the generic text itself.
    """
    night = await _night(group_hub, monkeypatch, _failed_hop(exc))

    _set_aside_all_group(night)
    assert night.said(IMMEDIATE) == [], night.said(IMMEDIATE)
    stop = night.said(COUNTED)
    assert len(stop) == 1, (stop, night.lines[-6:])
    assert f"{HELD_PASS_SET_ASIDE_AT} passes in a row" in stop[0]
    warned = [m for _t, lvl, m in night.lines
              if lvl == "warning" and m.startswith(f"{GROUP_NAME}: held for")]
    assert len(warned) == 1 and f"held for {HELD_PASS_ALERT_AT} passes" in (
        warned[0]), warned
    assert max(night.rel(t) for t, _who in night.gotos) == (
        (HELD_PASS_SET_ASIDE_AT - 1) * CENTRING_HOLD_RETRY_S)


async def test_one_generic_panel_in_each_pass_keeps_the_counted_path(
        group_hub, monkeypatch):
    """A pass whose panels do not all blame the same thing is no evidence
    of one persistent cause: with the FIRST panel failing for a cause the
    system cannot name (generic) and the other three for the missing
    solver, no pass carries a code, so nothing is set aside before the
    sixth held pass, whatever the three agree on."""
    missing = _failed_hop(SolverUnavailable("no solver"))
    generic = _failed_hop(DeviceError("ASTAP timed out"))

    def goto(who, n, result):
        return (generic if who == f"{GROUP_NAME} 1-1" else missing)(
            who, n, result)

    night = await _night(group_hub, monkeypatch, goto)

    _set_aside_all_group(night)
    assert night.said(IMMEDIATE) == [], night.said(IMMEDIATE)
    assert len(night.said(COUNTED)) == 1


async def test_the_real_hub_refusal_sets_the_mosaic_aside_at_pass_two(
        group_hub, monkeypatch):
    """THE WHOLE CHAIN, nothing scripted between the cause and the verdict:
    the REAL ``Hub.goto_and_center`` and the REAL ``solve_and_sync`` run for
    every hop, the rig has a real mount and no ASTAP, ``pick_solver``
    refuses with `SolverUnavailable`, the hub names it, the engine carries
    it into the deferral, and the group sets the mosaic aside at pass two.

    RED under mutant "the resolver's refusal is a plain DeviceError"
    (``_resolve_solve`` raising ``DeviceError``), observed:

        AssertionError: ([], [(..., 'info', "M31: centring failed on 1-2: plate solve failed - used raw GoTo; retried on the next pas...
        assert 0 == 1
         +  where 0 = len([])
    """
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    monkeypatch.setattr(providers_module, "_rig_has_real_motion",
                        lambda hub: True)
    real_goto = Hub.goto_and_center
    night = Night(group_hub, monkeypatch)

    async def goto_and_center(ra_hours, dec_deg, *args, **kw):
        # The harness's own stub counts the hop and slews a stand-in;
        # this runs the hub's real one, keeping the harness's count.
        night.gotos.append((night.clock.t,
                            str(night.engine.state.get("target", ""))))
        return await real_goto(group_hub, ra_hours, dec_deg, *args, **kw)

    monkeypatch.setattr(group_hub, "goto_and_center", goto_and_center)
    try:
        night.done = await night.run(grid_plan(rows=2, cols=2))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)

    _set_aside_all_group(night)
    stop = night.said(IMMEDIATE)
    assert len(stop) == 1, (stop, night.lines[-6:])
    assert repr(SOLVE_REASON_SOLVER_MISSING) in stop[0], stop[0]
    assert len(night.gotos) == 8, night.gotos


# ============================================ (d) the rule itself, pure

def _deferrals(*reasons: str, kind: str = CENTRING):
    return [(f"p{i}", PanelDeferred("centring failed", kind=kind,
                                    last_error=r))
            for i, r in enumerate(reasons)]


def test_the_new_sentences_are_codes_and_the_generic_text_is_not():
    """`_held_pass_reason_code` reads a fixed rig-side sentence, identical
    on every panel, as the pass's code; the generic text, a pass of mixed
    texts, and a pass with a generic panel among them as none.

    RED under mutant "the generic guard deleted" (``errors[0] !=
    GENERIC_SOLVE_FAILURE`` dropped from ``_held_pass_reason_code``),
    observed:

        AssertionError: assert 'plate solve failed - used raw GoTo' is None
         +  where 'plate solve failed - used raw GoTo' = _held_pass_reason_code([('p0', PanelDeferred('centring failed: ...
    """
    for reason in (SOLVE_REASON_SOLVER_MISSING, SOLVE_REASON_FILE_LOCKED):
        assert _held_pass_reason_code(_deferrals(reason, reason)) == reason
        assert _held_pass_reason_code(
            _deferrals(reason, reason, kind=SOLVE_TRANSIENT)) == reason
    assert _held_pass_reason_code(_deferrals(
        GENERIC_SOLVE_FAILURE, GENERIC_SOLVE_FAILURE)) is None
    assert _held_pass_reason_code(_deferrals(
        SOLVE_REASON_SOLVER_MISSING, GENERIC_SOLVE_FAILURE)) is None
    assert _held_pass_reason_code(_deferrals(
        SOLVE_REASON_SOLVER_MISSING, SOLVE_REASON_FILE_LOCKED)) is None


def test_two_different_rig_reasons_in_turn_do_not_set_aside_early():
    """CONTROL: the solver is missing on one pass and a file is held on the
    next -- two faults taking turns, not one that has not cleared."""
    run = GroupRun({"p0": "1-1", "p1": "1-2"}, max_failed_visits=3)
    for reason in (SOLVE_REASON_SOLVER_MISSING, SOLVE_REASON_FILE_LOCKED):
        for p in run.members:
            run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                              deferred=PanelDeferred(
                                  "centring failed", kind=CENTRING,
                                  last_error=reason))
        end = run.close_pass()
        assert end.boundary == "centring_hold", end
        run.start_pass()
