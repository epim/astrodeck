# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The resume ladder and a mount that refuses, or does not confirm, a sync
(#850, #857).

The ladder's blind solve (step 2) syncs wherever the mount stands, and after
a dawn park that is the home position, tube at the pole, where the AM5
refuses every sync with the tube at home: on the bench on 2026-10-08 every
sync there was refused: all but one answered ``e11``; one answered ``N/A``
and did not move. Holding on that refusal would end every resume that
follows a park, so the ladder reads the driver's read-back separation
(``SyncRefused.residual_deg``): a mount whose reported pointing is within
``RECOVERY_REFUSED_SYNC_MAX_DEG`` of the solved field goes on to the
re-centre, which syncs away from the pole (near the pole that separation
cannot see the RA axis angle, so the gate relies on the axis being at home,
as it is after a park); one further off, or one nobody could read back,
holds in fixed words, because it does not know where it points and cannot
be corrected from here (#857).

A sync the mount did not refuse but nobody could confirm (``SyncUnverified``:
the link failed around ``:CM#`` or the read-back never answered) holds too,
in its own fixed words: where the mount points is unknown, and the ladder
never slews on that.

The re-centre (step 3) holds when ``goto_and_center`` reports the mount
refused its sync or did not confirm it, and does not hand the engine a
target it did not centre.

EVERY SURFACED TEXT here (a hold reason, a warning) must survive the UI's
``humanizeLog`` (ui/src/lib/humanize.ts), which replaces a line that IS one
of four reports with its own sentence (a bare "plate solve failed" becomes
"Plate-solve failed - check focus/exposure", which would send an operator
whose solve WORKED to fix focus; a line that only mentions a plate solve is
left alone since #792 and #960). ``_humanizer_rewrites`` is the shared
``_humanizer_mirror`` of those rules and every test asserts it is False.

Every coordinate in this file is fictional and deliberately non-round. No
read-back coordinate may reach a log line or a hold (#140, #166); the tests
below check that the solve's own coordinates, which the hub sets on the
exception, do not either, in every spelling a formatter is likely to print.

MUTANTS, each run under this file's normal command and each RED (the test
that went red is named):

- "plate words" is RETIRED (#997): REFUSED_SYNC_FAR_WORDS starting "the plate
  solve after the restart worked" was red through the humanizer mirror when it
  rewrote any line holding "plate" and "solve". The UI shows that line as
  written now (#792, #960), so the mutant passes and nothing here pins it.
- "no unverified arm": step 2's ``except SyncUnverified`` removed (it falls
  to the generic failed-solve arm) -> test_an_unverified_blind_sync_holds.
- "unverified arm leaves the light unset": its ``_ladder_light = "lit"``
  removed -> test_an_unverified_blind_sync_holds.
- "far warning keeps the driver's advice": ``: {e.reason}`` appended after
  "at the scope" -> test_a_refused_sync_that_disagrees_badly_holds.
- "step 3 ignores sync_unverified": its check removed ->
  test_an_unverified_recentre_sync_holds_and_hands_the_engine_nothing.
- "unverified recentre says refused": REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS
  built on SOLVE_REASON_SYNC_REFUSED -> the same test.
- "reply quoted raw": ``_reply_words`` quotes any non-empty reply ->
  test_a_coordinate_shaped_reply_is_never_quoted.
- "unverified warning names the solve": `` at {e.solved}`` appended to the
  unverified arm's warning -> test_an_unverified_blind_sync_holds.
- "unverified hold carries the reason": the arm returns
  ``f"{REFUSED_SYNC_UNVERIFIED_WORDS}: {e.reason}"`` ->
  test_two_different_unverified_reasons_give_the_same_hold.

Round 3 (the action comes first, the hub's refusal line at info, the one
quoting rule):

- "far hold explains first": REFUSED_SYNC_FAR_WORDS back to the round-2
  words ("the field solved after the restart, but ... not slewing until
  someone checks the mount at the scope") ->
  test_every_refused_sync_constant_is_fixed_words.
- "unverified hold explains first": REFUSED_SYNC_UNVERIFIED_WORDS back to
  "the field solved after the restart, but the mount did not confirm the
  sync, so nobody can say where it points; not slewing, and if this
  repeats, check the mount's link" -> the same test.
- "far warning figure first": the far arm's warning back to "...is X deg
  from the sky, beyond the 5 deg the ladder slews on; it needs someone at
  the scope" -> test_each_arm_warning_says_what_happens_next_first[far].
- "unverified warning reason first": the unverified arm's warning back to
  "...{reply}: {e.reason}; not slewing, and if this repeats, check the
  mount's link" ->
  test_each_arm_warning_says_what_happens_next_first[unverified].
- "near warning action last": the near arm's warning back to "...agrees
  with the sky to X deg, so the re-centre goes ahead and syncs away from the
  pole" -> test_each_arm_warning_says_what_happens_next_first[near].
- "hub refusal line at warning": step 2 calls ``solve_and_sync`` without
  ``refusal_level="info"`` ->
  test_the_blind_solve_leaves_the_refusal_line_to_the_ladder.
- "local quoting rule": ``_reply_words`` quotes on its own
  ``[A-Za-z0-9/]{1,8}`` fullmatch again, not ``quotable_sync_reply`` ->
  test_a_reply_with_a_run_of_digits_is_never_quoted.

Round 4 (the unverified re-centre's link advice inside the cut):

- "unverified recentre advice last": REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS
  back to "re-centering after restart stopped and the run is not starting: "
  + SOLVE_REASON_SYNC_UNVERIFIED + "; if this repeats, check the mount's
  link" -> test_every_refused_sync_constant_is_fixed_words.

Round 5 (what the near warning measured, one spelling for step 3):

- "near warning overclaims": the near arm's warning back to "...but its own
  position agrees with the sky to X deg" ->
  test_the_near_warning_says_what_was_measured (every residual).
- "recentre hold spelled two ways": REFUSED_SYNC_RECENTRE_WORDS back to
  "re-centering after restart stopped ..." ->
  test_the_two_recentre_holds_spell_it_one_way.
"""
from __future__ import annotations

import math
import re

import pytest

from _humanizer_mirror import humanizer_rewrites as _humanizer_rewrites
from astrodeck.devices.base import SyncRefused, SyncUnverified
from astrodeck.hub import (SOLVE_REASON_SYNC_REFUSED,
                           SOLVE_REASON_SYNC_UNVERIFIED)
from astrodeck.sequence import resume_arm as ra
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, session_store


# Fictional throughout, and non-round so a formatter's spelling is findable.
_TARGET_RA, _TARGET_DEC = 7.2537, 41.5813
_SOLVED = (7.4321, 43.8765)

# Stand-ins for the driver's fixed words (package J1 owns the real ones).
# The e11 ones follow round 5's safe order, chosen by the driver from the
# read-back separation: Trust position first when the tube may be at home,
# and the tube brought home by eye with a pad key when it is far off. At most
# 90 chars, no digits, no slew and no goto.
_E11_REASON = ("if the tube is at home, use Trust position first; a sync "
               "away from the pole then works")
_E11_FAR_REASON = ("the tube is not where the mount thinks: bring it home by "
                   "eye with a pad key, then Trust position")
_UNVERIFIED_REASONS = (
    "the link failed during the sync, so whether the mount took it is "
    "unknown",
    "the link failed before the sync was sent",
    "the mount did not answer the position read after the sync, so whether "
    "it took is unknown",
)

# The UI cuts a line longer than 140 chars to 137 and an ellipsis.
_CUT = 137


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path, monkeypatch):
    """Sessions land under a temp CAPTURE_DIR, never the developer's."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def fp(tmp_path, monkeypatch):
    """A fingerprint recorded BEFORE this process, matching the focuser, so
    the ladder trusts focus and goes straight to the solve (the pattern of
    test_resume_after_restart)."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "_PATH", tmp_path / "fp.json")
    _fp.reset_for_tests()
    _fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0,
               dec_deg=2.0, parked=False, tracking=True)
    _fp.reset_for_tests()
    yield _fp
    _fp.reset_for_tests()


class _Connected:
    connected = True


class _Foc:
    connected = True

    async def get_position(self):
        return 9935

    async def move_to(self, p):  # pragma: no cover - must never be called
        raise AssertionError("the ladder must not drive the focuser here")


def _refusal(residual, code="e11", reason=None):
    if reason is None:
        # The driver's choice: the far words above its 5 degree threshold.
        far = residual is not None and not residual <= 5.0
        reason = _E11_FAR_REASON if far else _E11_REASON
    e = SyncRefused("the mount refused the sync", code=code, reason=reason,
                    residual_deg=residual)
    # What the hub sets before re-raising: the solve, J2000.
    e.solved = _SOLVED
    return e


def _unverified(code="", reason=_UNVERIFIED_REASONS[0]):
    e = SyncUnverified("the mount did not confirm the sync", code=code,
                       reason=reason)
    e.solved = _SOLVED
    return e


class _Hub:
    """Records what the ladder asked the rig to do. Nothing physical."""

    def __init__(self, *, solve_raises=None, centring=None):
        self.calls: list[str] = []
        self._solve_raises = solve_raises
        self._centring = centring
        self.focuser = _Foc()
        self.devices = {"focuser": self.focuser, "camera": _Connected(),
                        "telescope": _Connected()}
        self.site = {}
        self.solve_kwargs: list[dict] = []

    def require(self, role):
        if role == "focuser":
            return self.focuser
        raise RuntimeError(role)

    async def solve_and_sync(self, exposure_s: float = 3.0, **kw):
        self.calls.append("solve")
        self.solve_kwargs.append({"exposure_s": exposure_s, **kw})
        if self._solve_raises is not None:
            raise self._solve_raises
        return {"ok": True}

    async def goto_and_center(self, ra_h, dec, *a, **k):
        self.calls.append("center")
        return self._centring


class _Engine:
    running = False

    async def current_safety(self):
        from astrodeck.devices.base import SafetyReading
        return SafetyReading(is_safe=True, source="stub")

    async def check_slew_limits(self, target, *, cfg=None, plan=None,
                                projected=True):
        return None

    def start(self, plan, *, session=None, tracking=None, operator=True):
        # ``operator``: ResumeArm passes False (#853, ruling R5).
        return None


def _session() -> Session:
    plan = SequencePlan(name="p", targets=[Target(
        name="t", ra_hours=_TARGET_RA, dec_deg=_TARGET_DEC,
        steps=[ExposureStep(filter="L", exposure_s=1, gain=100, count=2)])])
    s = Session(name="p", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    return s


def _arm(hub, monkeypatch):
    arm = ra.ResumeArm(_Engine(), hub)
    # THIS file is about a solver that worked; a host without one would take
    # the "no solver configured" branch and never reach the arm under test.
    monkeypatch.setattr(arm, "_can_solve", lambda: True)
    return arm


def _coordinate_spellings() -> list[str]:
    """Every spelling of the solve's and the target's coordinates a
    formatter is likely to print: fixed precision, ``repr``, ``str``, an
    f-string, and the solve tuple whole."""
    out = [str(_SOLVED), repr(_SOLVED), f"{_SOLVED}",
           str(list(_SOLVED))]
    for v in (*_SOLVED, _TARGET_RA, _TARGET_DEC):
        out += [repr(v), f"{v}", str(v)]
        out += [fmt.format(v) for fmt in ("{:.4f}", "{:.3f}", "{:.2f}",
                                          "{:.1f}")]
    return out


def _no_coordinates(text: str) -> None:
    for s in _coordinate_spellings():
        assert s not in text, (s, text)


def _surfaced_ok(text: str) -> None:
    """The rules every surfaced text obeys here: no coordinate, and the UI
    shows our words rather than its own."""
    _no_coordinates(text)
    assert not _humanizer_rewrites(text), text


def _warnings(bus_lines) -> list[str]:
    return [m for lvl, m, _ in bus_lines if lvl == "warning"]


def _held_line(reason: str) -> str:
    """The warning ``tick`` logs for a hold (resume_arm.py, "auto-resume
    held: ..."), at the ladder's ten-minute retry."""
    return (f"auto-resume held: {reason} \N{EM DASH} retrying in "
            f"{int(ra.RETRY_INTERVAL_S / 60)} min")


# --------------------------------------------------------------- the words

_REFUSED_SYNC_NAMES = {"REFUSED_SYNC_FAR_WORDS", "REFUSED_SYNC_UNKNOWN_WORDS",
                       "REFUSED_SYNC_UNVERIFIED_WORDS",
                       "REFUSED_SYNC_RECENTRE_WORDS",
                       "REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS"}


def _refused_sync_constants() -> dict[str, str]:
    return {n: getattr(ra, n) for n in dir(ra)
            if n.startswith("REFUSED_SYNC_") and n.endswith("_WORDS")}


# What each hold tells the operator happens next, or what to do. Every one
# of these must sit whole inside the first 137 chars of the "auto-resume
# held: ..." line (#850, round 3): the UI cuts there, and an instruction
# after the explanation was never seen.
_HOLD_ACTIONS = {
    "REFUSED_SYNC_FAR_WORDS": ("not slewing", "needs someone at the scope"),
    "REFUSED_SYNC_UNKNOWN_WORDS": ("not slewing",),
    "REFUSED_SYNC_UNVERIFIED_WORDS": ("not slewing",
                                      "if this repeats, check the mount's "
                                      "link"),
    "REFUSED_SYNC_RECENTRE_WORDS": ("the run is not starting",),
    # Round 4: the link advice too. It sat after the hub's reason, past the
    # cut, so only "the run is not starting" was ever seen.
    "REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS": ("the run is not starting",
                                               "if this repeats, check the "
                                               "mount's link"),
}


def test_every_refused_sync_constant_is_fixed_words():
    """Every ``REFUSED_SYNC_*`` hold: no digit (the hold keeps its ``since``
    only while its words stay the same), not rewritten by the UI on its own
    or inside the "auto-resume held: ..." warning, and both the cause (the
    mount refused, or did not confirm, the sync) and the ACTION (what
    happens next, or what to do) before the cut at 137 chars of that
    warning, so a toast says what went wrong and what to do about it."""
    consts = _refused_sync_constants()
    # Not vacuous: the five holds this file is about are all here, and each
    # has its action named.
    assert _REFUSED_SYNC_NAMES <= set(consts), sorted(consts)
    assert set(_HOLD_ACTIONS) == set(consts), sorted(consts)
    for name, words in consts.items():
        assert not re.search(r"\d", words), (name, words)
        assert not _humanizer_rewrites(words), (name, words)
        line = _held_line(words)
        assert not _humanizer_rewrites(line), (name, line)
        head = line[:_CUT]
        assert ("the mount refused the sync" in head
                or "the mount did not confirm the sync" in head), (name, head)
        for action in _HOLD_ACTIONS[name]:
            assert action in head, (name, action, head)
        # The bare "auto-resume held: " form too, with nothing after it.
        bare = ("auto-resume held: " + words)[:_CUT]
        for action in _HOLD_ACTIONS[name]:
            assert action in bare, (name, action, bare)


# --------------------------------------------------------------- step 2

@pytest.mark.parametrize("residual", [0.002, 1.0, 4.25, 5.0])
async def test_a_refused_sync_that_agrees_with_the_sky_goes_on_to_recentre(
        monkeypatch, bus_lines, residual):
    """At the home position the AM5 answers ``e11`` and takes nothing, and
    the driver's read-back says how far its own position is from the sky.
    Within RECOVERY_REFUSED_SYNC_MAX_DEG (inclusive) the ladder goes on to
    the re-centre, which syncs away from the pole. 4.25 deg is the desync
    measured on 2026-10-08."""
    hub = _Hub(solve_raises=_refusal(residual),
               centring={"centered": True, "error_arcmin": 0.3,
                         "attempts": 1})
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) is None
    assert hub.calls == ["solve", "center"], "the re-centre must go ahead"
    assert arm._recentred is not None
    assert arm._ladder_light == "lit", "the frame solved; light reached it"
    warns = _warnings(bus_lines)
    said = [m for m in warns if "would not take the blind solve's sync" in m]
    assert len(said) == 1, warns
    assert f"{residual:.1f} deg" in said[0]
    assert "(reply 'e11')" in said[0]
    for m in warns:
        _surfaced_ok(m)


@pytest.mark.parametrize("residual", [0.002, 1.0, 4.25, 5.0])
async def test_the_near_warning_says_what_was_measured(monkeypatch, bus_lines,
                                                       residual):
    """The go-ahead warning names the measurement, a separation between the
    mount's reported pointing and the solved field, and claims nothing more
    (#850 round 5). Near the pole that separation cannot see the RA axis
    angle, so "its own position agrees with the sky" overstated it: a mount
    whose axis turned away from home with the tube still at the pole reads
    a separation near zero."""
    hub = _Hub(solve_raises=_refusal(residual),
               centring={"centered": True, "error_arcmin": 0.3,
                         "attempts": 1})
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) is None
    said = [m for m in _warnings(bus_lines)
            if "would not take the blind solve's sync" in m]
    assert len(said) == 1, _warnings(bus_lines)
    assert (f"its reported pointing is within {residual:.1f} deg of the "
            f"solved field") in said[0], said[0]
    for overclaim in ("agrees with the sky", "agrees with"):
        assert overclaim not in said[0], (overclaim, said[0])
    _surfaced_ok(said[0])


def test_the_two_recentre_holds_spell_it_one_way():
    """Step 3's holds word one event, so they spell it one way, and that
    way is the one the step's other holds already show the operator:
    "re-centering after restart refused ..." / "... failed" (#850 round 5).
    Round 5 first respelled the two #850 holds "re-centring" / "re-centre",
    which split the step from its five older siblings instead."""
    holds = (ra.REFUSED_SYNC_RECENTRE_WORDS,
             ra.REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS)
    for words in holds:
        assert "re-centering" in words, words
        assert "re-centr" not in words, words
    assert ra.REFUSED_SYNC_RECENTRE_WORDS.startswith(
        "re-centering after restart stopped"), ra.REFUSED_SYNC_RECENTRE_WORDS


@pytest.mark.parametrize("residual", [5.01, 12.0, 179.0])
async def test_a_refused_sync_that_disagrees_badly_holds(
        monkeypatch, bus_lines, residual):
    """Further off than the bound and refusing the correction: a mount that
    does not know where it points and cannot be corrected from here (#857).
    It must not slew, and the figure goes to a warning, not the hold.

    The warning keeps the reply code and NOT the driver's reason: the
    driver's e11 words carry a remedy of their own (Trust position, or the
    tube brought home by eye first), a second instruction beside "needs
    someone at the scope"."""
    hub = _Hub(solve_raises=_refusal(residual))
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.REFUSED_SYNC_FAR_WORDS
    assert hub.calls == ["solve"], "must not slew a mount that is far off"
    assert arm._recentred is None
    assert arm._ladder_light == "lit", "the frame solved; light reached it"
    warns = _warnings(bus_lines)
    figure = [m for m in warns if f"{residual:.1f} deg from the sky" in m]
    assert len(figure) == 1, warns
    assert "(reply 'e11')" in figure[0]
    assert "needs someone at the scope" in figure[0]
    assert _E11_REASON not in figure[0]
    assert _E11_FAR_REASON not in figure[0]
    for words in ("slew away", "Trust position", "pad key", "home by eye"):
        assert words not in figure[0], (words, figure[0])
    for m in warns:
        _surfaced_ok(m)


async def test_a_refused_sync_with_no_read_back_holds(monkeypatch, bus_lines):
    """No read-back could be taken, so nobody can say how far off the mount
    is: that is not evidence it agrees with the sky."""
    hub = _Hub(solve_raises=_refusal(None))
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.REFUSED_SYNC_UNKNOWN_WORDS
    assert hub.calls == ["solve"]
    assert arm._recentred is None
    assert arm._ladder_light == "lit"
    warns = _warnings(bus_lines)
    assert any("could not be read back" in m for m in warns), warns
    for m in warns:
        _surfaced_ok(m)


async def test_a_non_finite_residual_holds(monkeypatch, bus_lines):
    """A NaN compares False against the bound, and must read as far off,
    never as close enough to slew on."""
    hub = _Hub(solve_raises=_refusal(math.nan))
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) == ra.REFUSED_SYNC_FAR_WORDS
    assert hub.calls == ["solve"]


@pytest.mark.parametrize("raises", [
    _refusal(None), _refusal(5.01), _refusal(30.0), _refusal(90.0),
    _unverified(), _unverified(code="N/A", reason=_UNVERIFIED_REASONS[2])],
    ids=["refused-unknown", "refused-5.01", "refused-30", "refused-90",
         "unverified", "unverified-N/A"])
async def test_the_hold_words_carry_no_figures_and_survive_the_humanizer(
        monkeypatch, bus_lines, raises):
    """The hold keeps its ``since`` across retries only while its words stay
    the same, and a viewer reads it, so it is fixed words: no digit at all
    (not the residual, not the reply code ``e11``, not the bound), and words
    the UI shows as they are, never its "Plate-solve failed" (the solve
    worked)."""
    hub = _Hub(solve_raises=raises)
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason is not None
    assert not re.search(r"\d", reason), reason
    assert "e11" not in reason and "N/A" not in reason
    _surfaced_ok(reason)
    _surfaced_ok(_held_line(reason))
    for m in _warnings(bus_lines):
        _surfaced_ok(m)


async def test_two_different_far_residuals_give_the_same_hold(monkeypatch,
                                                              bus_lines):
    """Same words for a different figure, so the hold's ``since`` survives a
    retry whose read-back moved a little."""
    out = []
    for residual in (8.0, 31.5):
        hub = _Hub(solve_raises=_refusal(residual))
        out.append(await _arm(hub, monkeypatch)._recover(_session()))
    assert out[0] == out[1] == ra.REFUSED_SYNC_FAR_WORDS


@pytest.mark.parametrize("code", ["", "N/A"])
@pytest.mark.parametrize("why", _UNVERIFIED_REASONS)
async def test_an_unverified_blind_sync_holds(monkeypatch, bus_lines, code,
                                              why):
    """The solve worked and nobody knows whether the mount took its answer:
    the link failed around the sync, or the read-back never answered. Not a
    failed solve (whose words the UI turns into "check focus/exposure") and
    not a refusal. Where the mount points is unknown, so the ladder holds in
    fixed words and does not slew; the frame solved, so light reached it.

    The driver's reason and the reply go to ONE warning, the reply quoted
    only when there was one, and the solve's coordinates nowhere."""
    hub = _Hub(solve_raises=_unverified(code=code, reason=why))
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.REFUSED_SYNC_UNVERIFIED_WORDS
    assert hub.calls == ["solve"], "must not slew on an unknown position"
    assert arm._recentred is None
    assert arm._ladder_light == "lit", "the frame solved; light reached it"
    warns = _warnings(bus_lines)
    said = [m for m in warns if "did not confirm the blind solve's sync" in m]
    assert len(said) == 1, warns
    assert why in said[0]
    if code:
        assert f"(reply '{code}')" in said[0]
    else:
        assert "reply" not in said[0], said[0]
    for m in warns:
        _surfaced_ok(m)


async def test_two_different_unverified_reasons_give_the_same_hold(
        monkeypatch, bus_lines):
    """A link that fails one way and then another across retries keeps the
    same hold, so its ``since`` survives."""
    out = []
    for i, why in enumerate(_UNVERIFIED_REASONS):
        hub = _Hub(solve_raises=_unverified(code=("", "N/A", "")[i],
                                            reason=why))
        out.append(await _arm(hub, monkeypatch)._recover(_session()))
    assert out == [ra.REFUSED_SYNC_UNVERIFIED_WORDS] * len(out)


@pytest.mark.parametrize("raises", [
    _refusal(1.0, code="07:23:41"), _refusal(None, code="07:23:41"),
    _refusal(20.0, code="+41*34:52"),
    _unverified(code="07:23:41#junk")],
    ids=["refused-near", "refused-unknown", "refused-far", "unverified"])
async def test_a_coordinate_shaped_reply_is_never_quoted(monkeypatch,
                                                         bus_lines, raises):
    """A desynchronised link can hand ``:CM#`` the answer meant for a
    ``:GR#``/``:GD#``, which is a coordinate, and at home a site oracle (#140,
    #166). The driver already refuses to quote it; the ladder repeats the
    rule: a reply is quoted only when it is a short code, and anything else
    is named, never quoted."""
    hub = _Hub(solve_raises=raises,
               centring={"centered": True, "error_arcmin": 0.3,
                         "attempts": 1})
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    texts = _warnings(bus_lines) + ([reason] if reason else [])
    replied = [m for m in texts if "an unrecognised reply" in m]
    assert len(replied) == 1, texts
    for m in texts:
        assert raises.code not in m, m
        for piece in re.split(r"[^0-9]+", raises.code):
            if piece:
                assert f"{piece}:" not in m and f":{piece}" not in m, m
        _surfaced_ok(m)


async def test_a_generic_solve_failure_keeps_the_old_words(monkeypatch,
                                                           bus_lines):
    """Too few stars is still a failed solve, in today's words."""
    hub = _Hub(solve_raises=RuntimeError("Not enough stars"))
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason is not None
    assert reason.startswith("blind plate solve failed after restart")
    assert hub.calls == ["solve"]


# The longest reply words an arm can print: a reply nobody may quote.
_LONG_REPLY = "07:23:41"


@pytest.mark.parametrize("arm_case", [
    ("near", _refusal(4.99, code=_LONG_REPLY), (
        "the re-centre goes ahead",)),
    ("near-e11", _refusal(4.99), ("the re-centre goes ahead",)),
    ("unknown", _refusal(None, code=_LONG_REPLY), ("not slewing",)),
    ("far", _refusal(179.9, code=_LONG_REPLY), (
        "needs someone at the scope",)),
    ("far-e11", _refusal(179.9), ("needs someone at the scope",)),
    ("unverified", _unverified(code=_LONG_REPLY,
                               reason=_UNVERIFIED_REASONS[2]), (
        "not slewing", "if this repeats, check the mount's link")),
], ids=lambda c: c[0])
async def test_each_arm_warning_says_what_happens_next_first(
        monkeypatch, bus_lines, arm_case):
    """Each step 2 arm's ONE warning puts what happens next (or what to do)
    inside the UI's 137-char cut, with the longest reply words an arm can
    print (an unrecognised reply), a three-digit figure and the longest
    driver reason. The figure and the reason may be cut; the action not."""
    _, raises, actions = arm_case
    hub = _Hub(solve_raises=raises,
               centring={"centered": True, "error_arcmin": 0.3,
                         "attempts": 1})
    arm = _arm(hub, monkeypatch)
    await arm._recover(_session())
    said = [m for m in _warnings(bus_lines)
            if "blind solve's sync" in m]
    assert len(said) == 1, _warnings(bus_lines)
    head = said[0][:_CUT]
    for action in actions:
        assert action in head, (action, said[0])
    _surfaced_ok(said[0])


@pytest.mark.parametrize("raises", [
    None, _refusal(1.0), _refusal(30.0), _refusal(None), _unverified()],
    ids=["solved", "refused-near", "refused-far", "refused-unknown",
         "unverified"])
async def test_the_blind_solve_leaves_the_refusal_line_to_the_ladder(
        monkeypatch, bus_lines, raises):
    """Step 2 decides on a refused or unconfirmed sync itself and logs its
    own warning, so it asks the hub to log its refusal line at info
    (``refusal_level="info"``, #850 round 3). At warning level the hub's
    line would hand the operator the driver's advice for a mount at home
    while the ladder goes on to recover by itself."""
    hub = _Hub(solve_raises=raises,
               centring={"centered": True, "error_arcmin": 0.3,
                         "attempts": 1})
    arm = _arm(hub, monkeypatch)
    await arm._recover(_session())
    assert hub.solve_kwargs, "the blind solve never ran"
    assert hub.solve_kwargs[0].get("refusal_level") == "info", \
        hub.solve_kwargs[0]
    assert hub.solve_kwargs[0]["exposure_s"] == ra.RECOVERY_SOLVE_EXPOSURE_S


@pytest.mark.parametrize("code", ["072341", "e123", "1234"])
async def test_a_reply_with_a_run_of_digits_is_never_quoted(monkeypatch,
                                                            bus_lines, code):
    """The ONE quoting rule (``quotable_sync_reply``): a reply with a run of
    three or more digits could be a coordinate with its separators lost, so
    it is named, never quoted, even though it is short and alphanumeric."""
    hub = _Hub(solve_raises=_refusal(30.0, code=code))
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) == ra.REFUSED_SYNC_FAR_WORDS
    said = [m for m in _warnings(bus_lines) if "blind solve's sync" in m]
    assert len(said) == 1, _warnings(bus_lines)
    assert "an unrecognised reply" in said[0], said[0]
    assert code not in said[0], said[0]
    # The short codes the AM5 really sends are still quoted.
    assert ra._reply_words("e11") == " (reply 'e11')"
    assert ra._reply_words("N/A") == " (reply 'N/A')"
    assert ra._reply_words("") == ""
    assert ra._reply_words(None) == ""


def test_the_bound_is_five_degrees():
    """The brief's number: polar and home-position error and the 4.25 deg
    desync of 2026-10-08 sit inside it."""
    assert ra.RECOVERY_REFUSED_SYNC_MAX_DEG == 5.0


# --------------------------------------------------------------- step 3

def _refused_centring():
    return {"centered": False, "error_arcmin": 142.0, "attempts": 1,
            "sync_refused": True, "sync_reply": "N/A",
            "sync_reason": "the mount answered as if it took the sync and "
                           "its reported position did not move",
            "solve_reason": SOLVE_REASON_SYNC_REFUSED}


def _unverified_centring(reply=""):
    return {"centered": False, "error_arcmin": 142.0, "attempts": 1,
            "sync_unverified": True, "sync_reply": reply,
            "sync_reason": _UNVERIFIED_REASONS[0],
            "solve_reason": SOLVE_REASON_SYNC_UNVERIFIED}


async def test_a_refused_recentre_sync_holds_and_hands_the_engine_nothing(
        monkeypatch, bus_lines):
    """Away from the pole a refusal is a mount that will not be corrected,
    and ``goto_and_center`` reports it only when the field is NOT centred.
    Starting the run would image that field (#852), and ``_recentred``
    would tell the engine the mount is tracking a target it is not on."""
    hub = _Hub(centring=_refused_centring())
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.REFUSED_SYNC_RECENTRE_WORDS
    assert hub.calls == ["solve", "center"]
    assert arm._recentred is None
    assert not re.search(r"\d", reason), reason
    assert SOLVE_REASON_SYNC_REFUSED in reason
    _surfaced_ok(reason)
    _surfaced_ok(_held_line(reason))


@pytest.mark.parametrize("reply", ["", "N/A"])
async def test_an_unverified_recentre_sync_holds_and_hands_the_engine_nothing(
        monkeypatch, bus_lines, reply):
    """The re-centre's sync was not refused but could not be confirmed, and
    the field was not within tolerance (``goto_and_center`` says
    ``sync_unverified`` only then). Same hold as a refusal, in its own words:
    the unverified reason, not the refused one, and not "another slew would
    land in the same place", which nobody knows here."""
    hub = _Hub(centring=_unverified_centring(reply))
    arm = _arm(hub, monkeypatch)
    reason = await arm._recover(_session())
    assert reason == ra.REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS
    assert hub.calls == ["solve", "center"]
    assert arm._recentred is None, "the engine must not be told it is on"
    assert SOLVE_REASON_SYNC_UNVERIFIED in reason
    assert SOLVE_REASON_SYNC_REFUSED not in reason
    assert "refused" not in reason and "same place" not in reason
    assert not re.search(r"\d", reason), reason
    _surfaced_ok(reason)
    _surfaced_ok(_held_line(reason))
    for m in _warnings(bus_lines):
        _surfaced_ok(m)


async def test_a_plain_non_centred_recentre_keeps_todays_behaviour(
        monkeypatch, bus_lines):
    """Not centred, but nothing refused: today's ladder hands the target on
    and lets the run's own acquisition centre it. Unchanged."""
    hub = _Hub(centring={"centered": False, "error_arcmin": 3.0,
                         "attempts": 3})
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) is None
    assert arm._recentred is not None


async def test_a_recentre_that_returns_nothing_keeps_todays_behaviour(
        monkeypatch, bus_lines):
    """The suite's stub hubs return None from goto_and_center; that is not a
    refusal."""
    hub = _Hub(centring=None)
    arm = _arm(hub, monkeypatch)
    assert await arm._recover(_session()) is None
    assert arm._recentred is not None
