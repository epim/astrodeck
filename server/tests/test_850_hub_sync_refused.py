# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#850 (hub half): a sync the mount refused is not a solve, and not a centre.

On 2026-10-07 three centring syncs of 2.2 to 2.8 degrees at Dec +34 changed
nothing on the AM5. ``Hub.solve_and_sync`` logged "solved & synced" for each,
``goto_and_center``'s next correction slew was zero length, the next solve
showed the field unmoved, and the run imaged the wrong field for hours (#852).

The driver now raises ``SyncRefused`` when the mount refuses (``e11`` at its
home position) or answers as if it took the sync and does not move, and
``SyncUnverified`` when nobody could tell (the link failed around ``:CM#``, or
the position read-back never answered). These cases grade what the hub does
with them:

* ``solve_and_sync`` attaches the solve (``e.solved``), logs ONE warning in
  words chosen by the type, with no coordinates and no "solved & synced",
  skips the post-sync bookkeeping and re-raises the same exception. A plain
  ``DeviceError`` goes past untouched, with no line of its own.
* ``goto_and_center`` stops at either with ``sync_refused`` or
  ``sync_unverified`` and the matching fixed ``solve_reason``, never re-slews,
  and never takes the "plate solve failed" arm (ruling 5: neither must reach
  the no-light hold). One whose solve is already within tolerance is centred.
  It calls ``solve_and_sync`` with ``refusal_level="info"`` (round 4), so a
  refused centring logs one hub warning, the stop line, and none when the
  field is already within tolerance.
* ``meridian_flip`` carries the re-centre's keys through.

Every surfaced line is also graded against the UI's ``humanizeLog`` rules
(ui/src/lib/humanize.ts, mirrored by ``_humanizer_rewrites``, the one shared
``_humanizer_mirror``), which would replace our words, and against its
140-character cut.

Every coordinate here is fictional and deliberately NON-ROUND (5.4321 h,
+12.8765 degrees): a round pair such as (5.0, 12.5) prints as "(5.0, 12.5)",
which no fixed-precision needle matches, and a leak of ``{e.solved}`` passed
every check in round 1 (review mutants H10a and H10b).

Each test names the mutant of hub.py it was shown red under; the mutants were
applied to a byte copy of hub.py and the file was restored from that copy.
"""
from __future__ import annotations

import pytest

from _humanizer_mirror import humanizer_rewrites as _humanizer_rewrites
from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.devices.base import DeviceError, SyncRefused, SyncUnverified
from astrodeck.hub import (SOLVE_REASON_SYNC_REFUSED,
                           SOLVE_REASON_SYNC_UNVERIFIED)
from astrodeck.solve.base import SolveResult

#: The target every case centres on. Fictional, non-round.
RA, DEC = 5.4321, 10.3765
#: Where the fake solver says the field is: 2.5 degrees north of the target,
#: the size of the 2026-10-07 syncs, so 150.0 arcmin (three digits).
#: Fictional, non-round.
FAR_RA, FAR_DEC = 5.4321, 12.8765
#: A solve inside the default 0.02 degree tolerance (0.6 arcmin). Fictional.
NEAR_RA, NEAR_DEC = 5.4321, 10.3865

#: Driver-shaped words in the safe order (#850 round 5): Trust position
#: before any sync away from the pole, and no slew while the position is
#: unknown.
_REASON = ("the mount answered with a refusal code and did not move; "
           "if the tube is at home, use Trust position first")
#: A stand-in for the driver's e11 advice (#850 round 2, F1.4; round 5's
#: safe order): at most 90 characters, no digits, the action inside it.
#: Exactly 90 here, the worst case.
_E11_REASON = ("if the tube is at home, use Trust position first; then a "
               "sync away from the pole will work")
#: A stand-in for the driver's unverified read-back words (F1.1).
_UNVERIFIED_REASON = ("the mount did not answer the position read after the "
                      "sync, so whether it took is unknown")


def _coordinate_spellings(ra: float, dec: float) -> list[str]:
    """The ways a log line could spell a position, for the privacy checks:
    the default ``repr``/``str``/f-string of each value and of the pair, the
    fixed-precision forms with and without a sign, and the RA in degrees."""
    out = [repr((ra, dec)), str((ra, dec)), f"{(ra, dec)}", f"{ra}, {dec}",
           str([ra, dec])]
    for v in (ra, dec, ra * 15.0):
        out += [repr(v), f"{v}", str(v)]
        for p in range(1, 5):
            out += [f"{v:.{p}f}", f"{v:+.{p}f}"]
    return sorted(set(out))


def _assert_surfaced_ok(text: str, *solves: tuple[float, float]) -> None:
    """Rule 1 (no coordinates) and rule 2 (no humanizer rewrite) for one
    surfaced text."""
    assert not _humanizer_rewrites(text), f"the UI would rewrite: {text!r}"
    for ra, dec in solves:
        for spelling in _coordinate_spellings(ra, dec):
            assert spelling not in text, (spelling, text)


class _FixedSolver:
    """A solver that always finds the field at one place."""
    name = "Fixed"

    def __init__(self, ra: float, dec: float):
        self.ra, self.dec = ra, dec

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        return SolveResult(True, ra_hours=self.ra, dec_deg=self.dec,
                           pixel_scale_arcsec=1.55, message="fixed")


def _use_solver(monkeypatch, ra: float, dec: float) -> None:
    import astrodeck.providers as providers_module
    solver = _FixedSolver(ra, dec)
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)


def _failing_sync(tel, monkeypatch, *, kind=SyncRefused, code: str = "e11",
                  reason: str = _REASON, residual: float | None = 2.5):
    """Make the sim mount fail every sync the way the #850 driver does
    (``SyncRefused`` or ``SyncUnverified``). Returns the list of exceptions
    raised, so a test can check identity."""
    raised: list[DeviceError] = []

    async def sync(ra_hours, dec_deg):
        e = kind(f"sync not taken: {reason}", code=code, reason=reason,
                 residual_deg=residual)
        raised.append(e)
        raise e

    monkeypatch.setattr(tel, "sync", sync)
    return raised


def _counting_slews(tel, monkeypatch) -> list[tuple[float, float]]:
    slews: list[tuple[float, float]] = []
    real = tel.slew

    async def slew(ra_hours, dec_deg):
        slews.append((ra_hours, dec_deg))
        await real(ra_hours, dec_deg)

    monkeypatch.setattr(tel, "slew", slew)
    return slews


def _stub_bookkeeping(sim_hub, monkeypatch) -> list:
    """Record the post-sync bookkeeping a refused or unverified sync must
    skip: the pointing record and the field identity."""
    noted: list = []
    monkeypatch.setattr(sim_hub, "_note_pointing",
                        lambda *a, **k: noted.append(("pointing", a)))

    async def note_field_solve(*a, **k):
        noted.append(("field", a))

    monkeypatch.setattr(sim_hub, "note_field_solve", note_field_solve)
    sim_hub._solved_pointing = None
    return noted


# ------------------------------------------------------------- the helper itself


def test_the_humanizer_mirror_trips_on_a_line_the_ui_rewrites():
    """The mirror is only worth something if it can say True, on a line the
    UI really rewrites (test_997_humanizer_mirror grades it rule by rule
    against the function), and False on the two fixed reasons. It used to
    assert True for "the plate solve worked but the mount refused" and
    "re-centring after guiding was lost", which the UI stopped rewriting
    (#792, #960; #997)."""
    assert _humanizer_rewrites("plate solve failed")
    assert not _humanizer_rewrites("the plate solve worked but the mount refused")
    assert not _humanizer_rewrites(SOLVE_REASON_SYNC_REFUSED)
    assert not _humanizer_rewrites(SOLVE_REASON_SYNC_UNVERIFIED)


def test_the_coordinate_spellings_catch_the_default_repr_of_the_solve():
    """Round 1's needles missed ``(5.0, 12.5)``. These must catch the default
    spellings of the non-round fictional solve."""
    spellings = _coordinate_spellings(FAR_RA, FAR_DEC)
    for leak in (f"{(FAR_RA, FAR_DEC)}", f" at {(FAR_RA, FAR_DEC)}",
                 f"{FAR_RA}", f"{FAR_DEC:+.2f}", f"{FAR_RA * 15:.1f}"):
        assert any(s in leak for s in spellings), leak


# ---------------------------------------------------------------- solve_and_sync


async def test_solve_and_sync_reraises_the_same_refusal_with_the_solve_attached(
        sim_hub, monkeypatch, bus_lines):
    """The exception the driver raised comes back out, the very object, with
    the solve's J2000 position on it; no "solved & synced" line; one warning
    with the reply and the driver's words and no coordinates; and none of the
    post-sync bookkeeping that claims the mount now agrees with the sky.

    NAMED MUTANT "solve_and_sync swallows the refusal" (the ``raise`` at the
    end of the new arm replaced with ``pass``): RED, ``Failed: DID NOT RAISE
    <class 'SyncRefused'>``.

    NAMED MUTANT "solve_and_sync does not attach the solve" (the
    ``e.solved = ...`` line deleted): RED, ``assert None == (5.4321,
    12.8765)``.

    NAMED MUTANT H10a (the review's: `` {e.solved}`` appended to the refused
    warning): RED in 9 cases. Here the exact-words assertion goes first; in
    ``test_a_reply_that_is_not_a_short_code_is_never_quoted``, which does not
    pin the words, it is ``_assert_surfaced_ok`` that catches it, with
    ``('(5.4321, 12.8765)', ...)``.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    raised = _failing_sync(tel, monkeypatch)
    noted = _stub_bookkeeping(sim_hub, monkeypatch)

    with pytest.raises(SyncRefused) as info:
        await sim_hub.solve_and_sync(exposure_s=0.05)

    assert len(raised) == 1
    assert info.value is raised[0], "a different exception came back out"
    assert info.value.solved == (FAR_RA, FAR_DEC)
    assert info.value.code == "e11"

    assert not any("solved & synced" in m for _, m, _ in bus_lines), bus_lines
    warnings = [(m, s) for lvl, m, s in bus_lines if lvl == "warning"]
    assert len(warnings) == 1, warnings
    msg, source = warnings[0]
    assert source == "solve"
    assert msg == (f"solved, but the mount refused the sync ('e11'): "
                   f"{_REASON}"), msg
    assert "did not confirm" not in msg
    _assert_surfaced_ok(msg, (FAR_RA, FAR_DEC))

    assert noted == [], f"post-sync bookkeeping ran for a refused sync: {noted}"
    assert sim_hub._solved_pointing is None


async def test_solve_and_sync_reraises_the_same_unverified_sync_with_the_solve_attached(
        sim_hub, monkeypatch, bus_lines):
    """``SyncUnverified`` takes the same arm in its own words: the very
    object back out with the solve on it, ONE warning saying the mount did
    not confirm the sync (never that it refused), no "solved & synced", no
    bookkeeping.

    NAMED MUTANT "solve_and_sync catches only SyncRefused" (the arm's
    ``(SyncRefused, SyncUnverified)`` cut back to ``SyncRefused``): RED,
    ``assert None == (5.4321, 12.8765)`` on ``solved``.

    NAMED MUTANT "an unverified sync is worded as a refusal" (the
    ``isinstance(e, SyncRefused)`` test in the warning made ``True``): RED,
    the warning is "solved, but the mount refused the sync (an empty reply):
    ...", not the unverified words.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    raised = _failing_sync(tel, monkeypatch, kind=SyncUnverified, code="",
                           reason=_UNVERIFIED_REASON, residual=None)
    noted = _stub_bookkeeping(sim_hub, monkeypatch)

    with pytest.raises(SyncUnverified) as info:
        await sim_hub.solve_and_sync(exposure_s=0.05)

    assert info.value is raised[0], "a different exception came back out"
    assert info.value.solved == (FAR_RA, FAR_DEC)

    assert not any("solved & synced" in m for _, m, _ in bus_lines), bus_lines
    warnings = [(m, s) for lvl, m, s in bus_lines if lvl == "warning"]
    assert len(warnings) == 1, warnings
    msg, source = warnings[0]
    assert source == "solve"
    assert msg == (f"solved, but the mount did not confirm the sync: "
                   f"{_UNVERIFIED_REASON}"), msg
    assert "refused" not in msg
    # It fits whole as well: 48 + 88.
    assert len(msg) <= 140, len(msg)
    _assert_surfaced_ok(msg, (FAR_RA, FAR_DEC))
    assert noted == [], noted
    assert sim_hub._solved_pointing is None


async def test_solve_and_sync_lets_a_plain_device_error_through_untouched(
        sim_hub, monkeypatch, bus_lines):
    """A plain ``DeviceError`` out of the sync (neither a refusal nor an
    unverified sync) is not this arm's: the very same object propagates, no
    ``solved`` is attached to it, and nothing is logged under "solve" for it
    (in particular nothing saying the mount refused or did not confirm).

    NAMED MUTANT H13b (the review's: the arm changed to ``except DeviceError
    as e:``, reading the words with ``getattr(e, 'code', None)`` and
    ``getattr(e, 'reason', e)``): RED, the "solve" warning list is
    ``["solved, but the mount did not confirm the sync: sync failed: the
    link dropped"]``, not empty.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    plain = DeviceError("sync failed: the link dropped")

    async def sync(ra_hours, dec_deg):
        raise plain

    monkeypatch.setattr(tel, "sync", sync)

    with pytest.raises(DeviceError) as info:
        await sim_hub.solve_and_sync(exposure_s=0.05)

    assert info.value is plain
    assert not hasattr(plain, "solved")
    solve_warnings = [m for lvl, m, s in bus_lines
                      if lvl == "warning" and s == "solve"]
    assert solve_warnings == [], solve_warnings
    assert not any("refused the sync" in m or "did not confirm" in m
                   for _, m, _ in bus_lines), bus_lines


async def test_solve_and_sync_still_logs_solved_and_synced_when_the_mount_takes_it(
        sim_hub, monkeypatch, bus_lines):
    """The healthy path is untouched: a sync that returns logs its line and
    returns the solve.

    NAMED MUTANT "the success line deleted" (the "solved & synced" log after
    the try reworded away): RED, the any() below is False.
    """
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    out = await sim_hub.solve_and_sync(exposure_s=0.05)
    assert out["ra_hours"] == FAR_RA and out["dec_deg"] == FAR_DEC
    assert any("solved & synced" in m for _, m, _ in bus_lines), bus_lines
    assert not any(lvl == "warning" and ("refused" in m
                                         or "did not confirm" in m)
                   for lvl, m, _ in bus_lines), bus_lines


@pytest.mark.parametrize("level", ["warning", "info"])
@pytest.mark.parametrize("kind,code,reason,words", [
    (SyncRefused, "e11", _E11_REASON,
     f"solved, but the mount refused the sync ('e11'): {_E11_REASON}"),
    (SyncUnverified, "", _UNVERIFIED_REASON,
     f"solved, but the mount did not confirm the sync: {_UNVERIFIED_REASON}"),
])
async def test_the_one_refusal_line_is_logged_at_the_callers_level(
        sim_hub, monkeypatch, bus_lines, level, kind, code, reason, words):
    """``refusal_level`` (#850 round 3, G2.1) sets the level of the ONE line
    ``solve_and_sync`` logs for a refused or unverified sync, and nothing
    else: the same words, the same source, the same object re-raised with
    the solve attached, and no line at the other level. The resume ladder
    passes ``"info"``: it decides on the refusal itself and its own line
    follows, so a warning telling the operator to act would be wrong when
    the ladder then recovers by itself.

    NAMED MUTANT "the refusal line ignores refusal_level" (both
    ``bus.log(refusal_level, ...)`` calls in the arm put back to
    ``bus.log("warning", ...)``): RED on the two ``info`` cases, ``assert
    [('warning', ...)] == [('info', ...)]``.

    NAMED MUTANT "refusal_level only reaches the refused line" (the
    unverified ``bus.log`` alone put back to ``"warning"``): RED on
    ``SyncUnverified-info``.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    raised = _failing_sync(tel, monkeypatch, kind=kind, code=code,
                           reason=reason, residual=None)
    noted = _stub_bookkeeping(sim_hub, monkeypatch)

    with pytest.raises(kind) as info:
        await sim_hub.solve_and_sync(exposure_s=0.05, refusal_level=level)

    assert info.value is raised[0]
    assert info.value.solved == (FAR_RA, FAR_DEC)
    sync_lines = [(lvl, m) for lvl, m, s in bus_lines
                  if s == "solve" and ("refused the sync" in m
                                       or "did not confirm" in m)]
    assert sync_lines == [(level, words)], sync_lines
    # Nothing ELSE moved to a warning or away from one: the only warning is
    # the refusal line itself, when the caller asked for a warning.
    warnings = [m for lvl, m, _ in bus_lines if lvl == "warning"]
    assert warnings == ([words] if level == "warning" else []), warnings
    assert not any("solved & synced" in m for _, m, _ in bus_lines)
    assert noted == [], noted
    _assert_surfaced_ok(words, (FAR_RA, FAR_DEC))


@pytest.mark.parametrize("bad", ["error", "debug", "Warning", "", None])
async def test_a_refusal_level_that_is_not_warning_or_info_is_refused_up_front(
        sim_hub, monkeypatch, bad):
    """Anything but ``"warning"`` or ``"info"`` is a ``ValueError``, raised
    before the camera exposes: a bad argument must not cost an exposure, and
    must not wait for a refusal to be noticed.

    NAMED MUTANT "refusal_level not checked" (the ``if refusal_level not in
    ...: raise ValueError`` at the top of ``solve_and_sync`` deleted): RED on
    every case, ``Failed: DID NOT RAISE <class 'ValueError'>`` (the sim
    mount takes the sync, so nothing ever reads the level).

    NAMED MUTANT "refusal_level checked only after the solve" (the check
    moved to just before ``await tel.sync(...)``): RED on every case,
    ``assert ['expose'] == []``.
    """
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    cam = sim_hub.devices["camera"]
    calls: list[str] = []
    real_expose = cam.expose

    async def expose(*a, **k):
        calls.append("expose")
        return await real_expose(*a, **k)

    monkeypatch.setattr(cam, "expose", expose)

    with pytest.raises(ValueError) as info:
        await sim_hub.solve_and_sync(exposure_s=0.05, refusal_level=bad)

    assert "refusal_level" in str(info.value)
    assert calls == [], calls


async def test_the_e11_refusal_line_fits_whole_with_the_action_in_it(
        sim_hub, monkeypatch, bus_lines):
    """The UI cuts a line over 140 characters to 137 plus an ellipsis. With
    the driver's e11 advice at its 90-character limit, the refusal warning
    must still fit whole, so "Trust position" and the sync away from the
    pole that follows it reach the operator.

    NAMED MUTANT "round-1 wording" (the refused warning restored to "the
    field solved, but the mount refused the sync (reply 'e11'): <reason>"):
    RED, ``assert 154 <= 140``.
    """
    assert len(_E11_REASON) == 90
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    _failing_sync(tel, monkeypatch, reason=_E11_REASON)

    with pytest.raises(SyncRefused):
        await sim_hub.solve_and_sync(exposure_s=0.05)

    (msg,) = [m for lvl, m, s in bus_lines if lvl == "warning"]
    assert len(msg) <= 140, (len(msg), msg)
    shown = msg if len(msg) <= 140 else msg[:137]
    for phrase in ("refused the sync", "Trust position",
                   "a sync away from the pole", "'e11'"):
        assert phrase in shown, (phrase, msg)
    _assert_surfaced_ok(msg, (FAR_RA, FAR_DEC))


@pytest.mark.parametrize("code,words", [
    ("07:23:41", "an unrecognised reply"),
    ("072341", "an unrecognised reply"),
    ("e123", "an unrecognised reply"),
    ("+12*34'56", "an unrecognised reply"),
    ("unrecognised", "an unrecognised reply"),
    ("", "an empty reply"),
    ("N/A", "'N/A'"),
])
async def test_a_reply_that_is_not_a_short_code_is_never_quoted(
        sim_hub, monkeypatch, bus_lines, code, words):
    """The driver sanitises the reply (F1.5), and the hub applies the same
    rule (``devices.base.quotable_sync_reply``, the one copy) to its own
    lines: a desynchronised link can return a ``:GR#``-shaped string as the
    sync's "reply", and at home that is the pole's RA (#140, #166). Only 1 to
    8 letters, digits or ``/`` with no run of three digits are quoted, so
    ``072341`` (a coordinate with its separators lost) is not.

    NAMED MUTANT "the hub quotes any reply" (``_sync_reply_words`` returning
    ``repr(code)`` unconditionally): RED on the first three cases, ``'07:23:41'``
    is in the warning, and on the empty one (``''`` instead of the words).

    NAMED MUTANT "the hub's own round-2 copy of the rule" (G2.4 undone:
    ``_sync_reply_words`` quoting any 1 to 8 ASCII letters, digits or ``/``
    without asking ``quotable_sync_reply``): RED on ``072341`` and ``e123``,
    ``assert "(an unrecognised reply)" in "solved, but the mount refused the
    sync ('072341'): ..."``.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    _failing_sync(tel, monkeypatch, code=code)
    _counting_slews(tel, monkeypatch)

    await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    # Two lines carry the reply: ``solve_and_sync``'s refusal line, at info
    # under ``goto_and_center`` (#850 round 4), and the centring loop's stop
    # warning. Both are graded, whatever their level.
    refusal = [m for _, m, _ in bus_lines
               if m.startswith("solved, but the mount refused the sync")]
    stop = [m for lvl, m, _ in bus_lines
            if lvl == "warning" and m.startswith("centering stopped")]
    assert len(refusal) == 1 and len(stop) == 1, bus_lines
    for msg in refusal + stop:
        assert f"({words})" in msg, (words, msg)
        if code not in ("", "unrecognised") and words != f"{code!r}":
            assert code not in msg, msg
        _assert_surfaced_ok(msg, (FAR_RA, FAR_DEC))


# ---------------------------------------------------------------- goto_and_center


async def test_a_refused_sync_far_off_target_stops_the_loop_with_the_contract_keys(
        sim_hub, monkeypatch, bus_lines):
    """Real solve_and_sync, real goto loop, a mount that refuses every sync
    with the field 2.5 degrees off: ONE slew, then stop, with exactly the
    contract's keys (plus the rotation key every return carries), no
    ``solve_failed``, and no "plate solve failed" line. The stop line puts
    the cause first and fits whole with a three-digit arcmin figure.

    NAMED MUTANT "no SyncRefused arm in goto_and_center" (the new arm
    deleted, so the catch-all takes it): RED, the result is
    ``{'centered': False, 'error_arcmin': None, 'attempts': 1,
    'solve_failed': True, 'rotation': None}``, not the contract dict.

    NAMED MUTANT "the refusal falls through to the next attempt" (the
    within-tolerance test reduced to ``refused_err is not None``): RED,
    ``assert 2 == 1`` on the slew count.

    NAMED MUTANT H10b (the review's: `` at {e.solved}`` put into the
    stopping warning): RED, ``('(5.4321, 12.8765)', ...)`` from
    ``_assert_surfaced_ok``.

    NAMED MUTANT "round-1 stop line" (figure first: "centering: the field is
    150.0' off target, and the mount refused the sync (reply 'e11'), so
    another slew would land in the same place; stopping"): RED, no line
    starts with "centering stopped", so ``assert len(stop) == 1`` fails.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    _failing_sync(tel, monkeypatch)
    slews = _counting_slews(tel, monkeypatch)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert len(slews) == 1, f"the loop re-slewed after a refused sync: {slews}"
    assert set(result) == {"centered", "error_arcmin", "attempts",
                           "sync_refused", "sync_reply", "sync_reason",
                           "solve_reason", "rotation",
                           "centring_solve_transient"}, result
    assert result["centring_solve_transient"] is False
    assert result["centered"] is False
    assert result["sync_refused"] is True
    assert result["sync_reply"] == "e11"
    assert result["sync_reason"] == _REASON
    assert result["solve_reason"] == SOLVE_REASON_SYNC_REFUSED
    assert result["attempts"] == 1
    assert result["rotation"] is None
    assert result["error_arcmin"] == pytest.approx(150.0, abs=0.1)

    assert not any("plate solve failed" in m for _, m, _ in bus_lines), bus_lines
    stop = [m for lvl, m, _ in bus_lines
            if lvl == "warning" and m.startswith("centering stopped")]
    assert len(stop) == 1, bus_lines
    line = stop[0]
    assert line.startswith("centering stopped: the mount refused the sync "
                           "('e11'); "), line
    assert "the field is 150.0' off target" in line, line
    assert "same place" in line, line
    assert len(line) <= 140, (len(line), line)
    for w in [m for lvl, m, _ in bus_lines if lvl == "warning"]:
        _assert_surfaced_ok(w, (FAR_RA, FAR_DEC))
    # The pointing is not verified, and it says why in the fixed words.
    assert sim_hub._pointing_verified is False
    assert sim_hub._pointing_reason.startswith(SOLVE_REASON_SYNC_REFUSED)


async def test_an_unverified_sync_far_off_target_stops_the_loop_in_its_own_words(
        sim_hub, monkeypatch, bus_lines):
    """The same stop for ``SyncUnverified``, with its own key and fixed
    reason: ``sync_unverified`` and ``SOLVE_REASON_SYNC_UNVERIFIED``, and
    NEITHER ``sync_refused`` NOR ``solve_failed``. The stop line says the
    mount did not confirm the sync (never that it refused), cause first, and
    fits whole with a three-digit arcmin figure.

    NAMED MUTANT "goto_and_center catches only SyncRefused" (the arm's tuple
    cut back to ``SyncRefused``): RED, the result is the solve-failed dict
    and a "plate solve failed" line is logged.

    NAMED MUTANT "an unverified sync reports sync_refused" (``sync_key``
    fixed to ``"sync_refused"``): RED on the key set.

    NAMED MUTANT "an unverified sync carries the refused reason"
    (``sync_const`` fixed to ``SOLVE_REASON_SYNC_REFUSED``): RED on
    ``solve_reason``.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    _failing_sync(tel, monkeypatch, kind=SyncUnverified, code="N/A",
                  reason=_UNVERIFIED_REASON, residual=None)
    slews = _counting_slews(tel, monkeypatch)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert len(slews) == 1, slews
    assert set(result) == {"centered", "error_arcmin", "attempts",
                           "sync_unverified", "sync_reply", "sync_reason",
                           "solve_reason", "rotation",
                           "centring_solve_transient"}, result
    assert result["centring_solve_transient"] is False
    assert result["centered"] is False
    assert result["sync_unverified"] is True
    assert result["sync_reply"] == "N/A"
    assert result["sync_reason"] == _UNVERIFIED_REASON
    assert result["solve_reason"] == SOLVE_REASON_SYNC_UNVERIFIED
    assert result["attempts"] == 1
    assert result["error_arcmin"] == pytest.approx(150.0, abs=0.1)

    assert not any("plate solve failed" in m for _, m, _ in bus_lines), bus_lines
    assert not any("refused" in m for _, m, _ in bus_lines), bus_lines
    stop = [m for lvl, m, _ in bus_lines
            if lvl == "warning" and m.startswith("centering stopped")]
    assert len(stop) == 1, bus_lines
    line = stop[0]
    assert line.startswith("centering stopped: the mount did not confirm the "
                           "sync"), line
    assert "the field is 150.0' off target" in line, line
    assert len(line) <= 140, (len(line), line)
    for w in [m for lvl, m, _ in bus_lines if lvl == "warning"]:
        _assert_surfaced_ok(w, (FAR_RA, FAR_DEC))
    assert sim_hub._pointing_verified is False
    assert sim_hub._pointing_reason.startswith(SOLVE_REASON_SYNC_UNVERIFIED)


@pytest.mark.parametrize("kind,key,const", [
    (SyncRefused, "sync_refused", SOLVE_REASON_SYNC_REFUSED),
    (SyncUnverified, "sync_unverified", SOLVE_REASON_SYNC_UNVERIFIED),
])
async def test_a_failed_sync_with_no_solve_attached_still_stops(
        sim_hub, monkeypatch, bus_lines, kind, key, const):
    """``e.solved`` None (a caller that raised it without the hub's help):
    ``error_arcmin`` None, still the stop with its key, never the
    solve-failed arm, and the stop line still fits whole.

    NAMED MUTANT "a refusal without a solve reports a zero error" (the
    return's ``else None`` for ``error_arcmin`` made ``else 0.0``): RED,
    ``assert 0.0 is None`` in both cases.
    """
    tel = sim_hub.devices["telescope"]
    slews = _counting_slews(tel, monkeypatch)

    async def fail(exposure_s, refusal_level: str = "warning"):
        raise kind("sync not taken", code="N/A", reason=_REASON,
                   residual_deg=None)

    monkeypatch.setattr(sim_hub, "solve_and_sync", fail)

    result = await sim_hub.goto_and_center(RA, DEC)

    assert result[key] is True
    assert result["error_arcmin"] is None
    assert result["sync_reply"] == "N/A"
    assert result["solve_reason"] == const
    assert "solve_failed" not in result
    assert len(slews) == 1
    assert not any("plate solve failed" in m for _, m, _ in bus_lines)
    (line,) = [m for lvl, m, _ in bus_lines
               if lvl == "warning" and m.startswith("centering stopped")]
    assert "offset was not measured" in line, line
    assert len(line) <= 140, (len(line), line)
    _assert_surfaced_ok(line)


@pytest.mark.parametrize("kind,key,const", [
    (SyncRefused, "sync_refused", SOLVE_REASON_SYNC_REFUSED),
    (SyncUnverified, "sync_unverified", SOLVE_REASON_SYNC_UNVERIFIED),
])
async def test_a_sync_not_taken_after_a_rotate_transient_is_no_transient_centring_solve(
        sim_hub, monkeypatch, kind, key, const):
    """#850 round 3, G2.2. The rotate phase of the same call could not solve
    (``SolveFrameTransient``: another process held the solve frame), so the
    rotation keys carry the union ``solve_transient: True``. The centring
    solve then RAN and the mount refused, or did not confirm, its sync. The
    result must say ``centring_solve_transient: False`` outright: without it
    the engine's ``_group_hop_checks`` falls back to the union and defers a
    require_centred panel as "the centring solve could not run" (kind
    SOLVE_TRANSIENT, which nothing counts) for what was the mount's refusal.

    The engine's own reader is called on the real result as the proof: the
    deferral is kind CENTRING with the fixed reason.

    NAMED MUTANT "no centring_solve_transient on a sync not taken" (the
    ``| {"centring_solve_transient": False}`` merge deleted from the
    return): RED in both cases, ``KeyError: 'centring_solve_transient'``, and
    in the two key-set tests of the far-off-target stops.

    NAMED MUTANT "centring_solve_transient copied from the union" (the merge
    made ``{"centring_solve_transient": bool(_rot_keys.get(
    "solve_transient"))}``): RED in both cases, ``assert True is False``.
    """
    from _group_harness import GROUP_ID, GROUP_NAME, panel
    from astrodeck.hub import SolveFrameTransient
    from astrodeck.sequence.engine import SequenceEngine
    from astrodeck.sequence.group_rules import CENTRING, PanelDeferred
    from astrodeck.sequence.models import TargetGroup

    async def not_set(rot, rotation_deg):
        return None

    async def ready():
        return None

    async def rotate_to_pa(rotation_deg, exposure_s=None):
        raise SolveFrameTransient("the solve frame was held by another "
                                  "program on every try")

    async def fail(exposure_s, refusal_level: str = "warning"):
        e = kind("sync not taken", code="e11", reason=_REASON,
                 residual_deg=2.5)
        e.solved = (FAR_RA, FAR_DEC)
        raise e

    monkeypatch.setattr(sim_hub, "_rotation_already_set", not_set)
    monkeypatch.setattr(sim_hub, "ensure_rotator_ready", ready)
    monkeypatch.setattr(sim_hub, "rotate_to_pa", rotate_to_pa)
    monkeypatch.setattr(sim_hub, "solve_and_sync", fail)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.0)

    # The precondition: the rotate phase's transient IS in the result.
    assert result.get("rotation_skipped") is True, result
    assert result.get("rotation_solve_transient") is True, result
    assert result.get("solve_transient") is True, result
    # The fix.
    assert result[key] is True, result
    assert result["solve_reason"] == const
    assert result["centring_solve_transient"] is False, result
    assert "solve_failed" not in result

    group = TargetGroup(id=GROUP_ID, name=GROUP_NAME, require_centred=True)
    with pytest.raises(PanelDeferred) as caught:
        SequenceEngine._group_hop_checks(panel(0, 0), group, result, const)
    assert caught.value.kind == CENTRING, caught.value.kind


@pytest.mark.parametrize("kind,words", [
    (SyncRefused, "the mount refused the sync ('e11')"),
    (SyncUnverified, "the mount did not confirm the sync"),
])
async def test_a_failed_sync_already_within_tolerance_is_centred(
        sim_hub, monkeypatch, bus_lines, kind, words):
    """The goto landed on the field and the mount refused (or did not
    confirm) a correction it did not need: the normal centred verdict, the
    pointing verified, and one line in the type's words.

    NAMED MUTANT "every refusal stops the loop" (the ``refused_err <=
    tolerance_deg`` branch removed, so the stop is always taken): RED,
    ``assert False is True`` on ``centered``, in both cases.

    NAMED MUTANT "the within-tolerance note deleted" (the note's words cut):
    RED, ``assert 0 == 1`` on the note count.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, NEAR_RA, NEAR_DEC)
    _failing_sync(tel, monkeypatch, kind=kind, residual=0.01)
    slews = _counting_slews(tel, monkeypatch)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result["centered"] is True, result
    assert result["attempts"] == 1
    assert result["error_arcmin"] == pytest.approx(0.6, abs=0.05)
    assert not ({"sync_refused", "sync_unverified", "solve_failed"}
                & set(result)), result
    assert len(slews) == 1
    assert sim_hub._pointing_verified is True
    assert sim_hub._solved_pointing is not None
    assert sim_hub._solved_pointing[:2] == (NEAR_RA, NEAR_DEC)
    noted = [m for _, m, _ in bus_lines
             if words in m and "within tolerance" in m]
    assert len(noted) == 1, bus_lines
    for _, m, _ in bus_lines:
        if "sync" in m:
            _assert_surfaced_ok(m, (NEAR_RA, NEAR_DEC))


@pytest.mark.parametrize("kind,code,reason,refusal_words,stop_start", [
    (SyncRefused, "e11", _REASON,
     f"solved, but the mount refused the sync ('e11'): {_REASON}",
     "centering stopped: the mount refused the sync ('e11'); "),
    (SyncUnverified, "", _UNVERIFIED_REASON,
     f"solved, but the mount did not confirm the sync: {_UNVERIFIED_REASON}",
     "centering stopped: the mount did not confirm the sync"),
], ids=["refused", "unverified"])
async def test_a_refused_centring_outside_tolerance_logs_one_warning(
        sim_hub, monkeypatch, bus_lines, kind, code, reason, refusal_words,
        stop_start):
    """#850 round 4, H4. Real ``solve_and_sync`` under the real goto loop,
    the field 2.5 degrees off and the mount refusing (or not confirming) the
    sync: the hub logs exactly ONE warning for the event, the centring loop's
    stop line. ``solve_and_sync``'s own refusal line is still logged, once,
    in the same words, at info: ``goto_and_center`` passes
    ``refusal_level="info"`` because it logs its own line for a sync that was
    not taken. Before this round the operator saw two hub warnings (three
    with the engine's) for one refused centring.

    NAMED MUTANT "goto_and_center logs the refusal at the default level"
    (the round-3 call ``self.solve_and_sync(solve_exposure_s)`` put back):
    RED in both cases, ``assert 2 == 1`` on the warning count, the extra
    warning being the refusal line.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)
    _failing_sync(tel, monkeypatch, kind=kind, code=code, reason=reason,
                  residual=2.5 if kind is SyncRefused else None)
    slews = _counting_slews(tel, monkeypatch)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    # The precondition: this is the stop, not a centre or a solve failure.
    assert result["centered"] is False, result
    assert "solve_failed" not in result, result
    assert len(slews) == 1, slews

    warnings = [m for lvl, m, _ in bus_lines if lvl == "warning"]
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith(stop_start), warnings
    refusal = [(lvl, m) for lvl, m, s in bus_lines
               if s == "solve" and m == refusal_words]
    assert refusal == [("info", refusal_words)], bus_lines


@pytest.mark.parametrize("kind,code,reason", [
    (SyncRefused, "e11", _REASON),
    (SyncUnverified, "", _UNVERIFIED_REASON),
], ids=["refused", "unverified"])
async def test_a_refused_centring_within_tolerance_logs_no_warning(
        sim_hub, monkeypatch, bus_lines, kind, code, reason):
    """#850 round 4, H4. The goto already landed within tolerance and the
    mount refused (or did not confirm) a correction it did not need: the
    result is centred and the hub logs NO warning at all. The refusal line
    and the loop's "within tolerance" note are both info.

    NAMED MUTANT "goto_and_center logs the refusal at the default level"
    (the round-3 call put back): RED in both cases, ``assert ["solved, but
    the mount refused the sync ('e11'): ..."] == []``.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, NEAR_RA, NEAR_DEC)
    _failing_sync(tel, monkeypatch, kind=kind, code=code, reason=reason,
                  residual=0.01 if kind is SyncRefused else None)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result["centered"] is True, result
    warnings = [m for lvl, m, _ in bus_lines if lvl == "warning"]
    assert warnings == [], warnings
    # The refusal was still said, once, at info: the level moved, not the
    # line.
    said = [lvl for lvl, m, s in bus_lines
            if s == "solve" and m.startswith("solved, but the mount ")]
    assert said == ["info"], bus_lines
    assert any("within tolerance" in m for lvl, m, _ in bus_lines
               if lvl == "info"), bus_lines


async def test_any_other_sync_error_still_takes_the_solve_failed_arm(
        sim_hub, monkeypatch, bus_lines):
    """A plain ``DeviceError`` out of the sync keeps today's behaviour
    exactly: the solve-failed arm, no ``sync_refused`` or ``sync_unverified``,
    and no line anywhere saying the mount refused or did not confirm.

    NAMED MUTANT "every DeviceError is a refusal" (goto_and_center's arm
    catching ``DeviceError`` instead of the two sync types): RED, the arm
    reads ``e.solved`` off a plain ``DeviceError`` and the error escapes
    ``goto_and_center`` instead of degrading to the solve-failed result.

    NAMED MUTANT H13b (solve_and_sync's arm catching ``DeviceError`` with
    getattr): RED, a "did not confirm the sync" line is logged for a plain
    link error.
    """
    tel = sim_hub.devices["telescope"]
    _use_solver(monkeypatch, FAR_RA, FAR_DEC)

    async def sync(ra_hours, dec_deg):
        raise DeviceError("sync failed: the link dropped")

    monkeypatch.setattr(tel, "sync", sync)

    result = await sim_hub.goto_and_center(RA, DEC, solve_exposure_s=0.05)

    assert result.get("solve_failed") is True, result
    assert "sync_refused" not in result, result
    assert "sync_unverified" not in result, result
    assert "solve_reason" not in result, result
    assert any("plate solve failed" in m for _, m, _ in bus_lines), bus_lines
    assert not any("refused the sync" in m or "did not confirm" in m
                   for _, m, _ in bus_lines), bus_lines


def test_the_fixed_reasons_survive_the_humanizer_and_carry_no_figures():
    """Rule 2 and rule 4 for the two constants the engine and the resume
    ladder show as a reason."""
    for const in (SOLVE_REASON_SYNC_REFUSED, SOLVE_REASON_SYNC_UNVERIFIED):
        assert not _humanizer_rewrites(const), const
        assert not any(ch.isdigit() for ch in const), const
        assert len(const) <= 137, const


# ---------------------------------------------------------------- meridian_flip


@pytest.mark.parametrize("kind,key,const", [
    (SyncRefused, "sync_refused", SOLVE_REASON_SYNC_REFUSED),
    (SyncUnverified, "sync_unverified", SOLVE_REASON_SYNC_UNVERIFIED),
])
async def test_meridian_flip_carries_the_sync_keys_through(
        sim_hub, monkeypatch, kind, key, const):
    """The flip's re-centre refused or could not confirm its sync: the flip
    step must see every key the re-centre returned, beside its own three.

    Real ``goto_and_center`` under the flip, with ``solve_and_sync`` raising
    the way the hub does (solve attached).

    NAMED MUTANT "the flip returns only its own keys" (``dict(result or {},
    ...)`` replaced by ``dict(flipped=..., pier_side_before=...,
    pier_side_after=...)``): RED, ``KeyError: 'sync_refused'`` and
    ``KeyError: 'sync_unverified'``.
    """
    async def fail(exposure_s, refusal_level: str = "warning"):
        e = kind("sync not taken", code="e11", reason=_REASON,
                 residual_deg=2.5)
        e.solved = (FAR_RA, FAR_DEC)
        raise e

    monkeypatch.setattr(sim_hub, "solve_and_sync", fail)

    result = await sim_hub.meridian_flip(RA, DEC)

    assert result[key] is True
    assert result["sync_reply"] == "e11"
    assert result["sync_reason"] == _REASON
    assert result["solve_reason"] == const
    assert result["error_arcmin"] == pytest.approx(150.0, abs=0.1)
    assert result["centered"] is False
    assert {"flipped", "pier_side_before", "pier_side_after"} <= set(result)
