# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A centring whose sync the MOUNT did not take stops the target; it never
holds for light and never "continues" (#850, the engine half).

On 2026-10-07 three centring syncs of 2.2 to 2.8 degrees "changed nothing":
the hub logged "solved & synced", the next goto was zero length, the next
solve showed the field unmoved, and the run imaged the wrong field for hours
(#852). The driver now reads every sync back and raises ``SyncRefused`` when
the mount did not take it, or ``SyncUnverified`` when nobody could confirm it
(the link failed around the sync, or the read-back never answered). The hub's
``goto_and_center`` turns either, for a solve NOT within tolerance of the
target, into a result carrying ``sync_refused: True`` or ``sync_unverified:
True``, with ``sync_reply``, ``sync_reason``, ``error_arcmin`` and
``solve_reason`` (``SOLVE_REASON_SYNC_REFUSED`` or
``SOLVE_REASON_SYNC_UNVERIFIED``). This file grades what the engine does with
that result at every place it centres, for both kinds:

- target setup (`_setup_target`): a single target stops (``StopTarget``,
  after the idle bookkeeping); a mosaic panel that requires centring is
  deferred with the matching fixed sentence; the no-light hold
  (`_hold_for_light`) is never entered, and a hold RETRY that comes back
  not taken ends the hold; the tracking-refusal recovery's copied result is
  read the same way;
- the three mid-run re-centres (after the unguided sweep, after the guide
  star went missing, after a walking field) stop the target before the
  guider restarts;
- the meridian flip stops the target after its bookkeeping and before the
  post-flip sweep;
- the tracking-refusal recovery, for the callers that do not read its
  result (`_enforce_tracking`, the flip), stops the target in the stop's
  own words, not in "the mount is not tracking";
- at the flip and in that recovery, both of which re-centre whatever the
  target says, a target that OPTED OUT of centring (``center`` off, or a
  calibration target: the gate the three mid-run re-centres take) is NOT
  stopped: it gets the warning, saying imaging goes on (FIXES3 G3.1)
  BEFORE the figure and the reply, so the cut at 137 never takes it
  (FIXES4 H3): "<name>: <where>: the mount refused the sync; centring is
  off, so imaging goes on (152.3' off, reply 'e11')".

THE STOP IS FIXED WORDS, ``f"{where}: {const}"`` (FIXES F3.2): no target
name, no figure, no reply, so a panel's TARGET_STOP deferral, whose
``last_error`` is ``str(StopTarget)``, reads the same on every pass (D-03).
The figure and the reply go in ONE warning logged just before, figure
first: "<name>: <where>: the mount refused the sync, 152.3' off target
(reply 'e11')" (FIXES3 G3.2). Both texts are graded here against the UI's
humanizer (``_humanizer_rewrites``) and the 137-character cut.

Every "plain non-centred" control keeps today's behaviour.

The hub is a double at the interface FIXES.md fixes (F2's return), so these
cases do not wait on the hub's own work. Coordinates are fictional. Every
mutant named below was applied to a byte copy of ``sequence/engine.py`` and
the file restored from that copy afterwards, with the restore confirmed by
sha256; the failure each produced is quoted.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from _simhub import a_real_site
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.sim import SimTelescope
from astrodeck.hub import (SOLVE_REASON_SYNC_REFUSED,
                           SOLVE_REASON_SYNC_UNVERIFIED, Hub)
from astrodeck.sequence.engine import SequenceEngine, StopTarget
from astrodeck.sequence.group_rules import CENTRING, PanelDeferred
from astrodeck.sequence.models import TargetGroup

from test_centring_settings_reach_goto import (_after_a_lost_star,
                                               _after_a_walking_field,
                                               _after_the_sweep, _guided,
                                               _setup, _target)
import test_recovery_centring_is_measured as rcm
import test_the_flip_stops_paying_for_itself as flp


def _refused(error_arcmin: float | None = 150.0, reply: str = "e11") -> dict:
    """The hub's answer for a refused sync whose solve is off target (F2's
    exact keys; the rotation keys are irrelevant to the engine here)."""
    return {"centered": False, "error_arcmin": error_arcmin, "attempts": 1,
            "sync_refused": True, "sync_reply": reply,
            "sync_reason": "the mount takes no syncs where it stands",
            "solve_reason": SOLVE_REASON_SYNC_REFUSED}


def _unverified(error_arcmin: float | None = 150.0, reply: str = "") -> dict:
    """The hub's answer for a sync nobody could confirm (F2.2's shape: no
    ``sync_refused`` key and no ``solve_failed`` key)."""
    return {"centered": False, "error_arcmin": error_arcmin, "attempts": 1,
            "sync_unverified": True, "sync_reply": reply,
            "sync_reason": ("the link failed during the sync, so whether the "
                            "mount took it is unknown"),
            "solve_reason": SOLVE_REASON_SYNC_UNVERIFIED}


#: (answer builder, the fixed sentence the stop carries, the cause phrase).
_KINDS = {
    "refused": (_refused, SOLVE_REASON_SYNC_REFUSED,
                "the mount refused the sync"),
    "unverified": (_unverified, SOLVE_REASON_SYNC_UNVERIFIED,
                   "the mount did not confirm the sync"),
}
_BY_KIND = pytest.mark.parametrize("kind", list(_KINDS))


def _humanizer_rewrites(text: str) -> bool:
    """Whether ui/src/lib/humanize.ts's ``humanizeLog`` replaces ``text``
    with its own words, by its four rules in order (lower-cased substring
    tests; FIXES "Surfaced-text rules" 2). A line it rewrites never reaches
    the operator in our words."""
    m = text.lower()
    if "camera" in m and any(w in m for w in
                             ("not responding", "timeout", "disconnect")):
        return True
    if "nina" in m and any(w in m for w in ("5", "http", "error")):
        return True
    if "plate" in m and "solve" in m:
        return True
    return "guid" in m and "lost" in m


def _continuing(lines) -> list[str]:
    return [m for _lvl, m, _src in lines if "continuing" in m]


#: The outcome an opted-out target's warning carries straight after the
#: cause, before the figure and the reply (FIXES3 G3.1, FIXES4 H3).
_GOES_ON = "; centring is off, so imaging goes on"


def _stop_warnings(lines) -> list[str]:
    """The engine's sync-not-taken warning(s): the line logged just before
    a stop, or instead of one for a target that opted out of centring. They
    carry the figure and the reply the stop itself must not."""
    return [m for lvl, m, src in lines
            if lvl == "warning" and src == "sequence"
            and ("the mount refused the sync" in m
                 or "the mount did not confirm the sync" in m)]


def _scripted(hub, answers: list) -> None:
    """Replace the recording double's ``goto_and_center`` with one that
    answers ``answers`` in order (copies), still recording each call."""
    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        return dict(answers[min(len(hub.gotos) - 1, len(answers) - 1)])
    hub.goto_and_center = goto


def _no_hold(e) -> list:
    """Stand in for the no-light hold and record every call to it: a sync
    not taken must never reach it. Returns the result unchanged, as a hold
    that gave up would."""
    calls: list = []

    async def hold(target, rotation, result):
        calls.append(dict(result))
        return result
    e._hold_for_light = hold
    return calls


# ===================================================== target setup, single

@_BY_KIND
@pytest.mark.parametrize("err", [150.0, None], ids=["measured", "no figure"])
async def test_a_sync_not_taken_at_acquisition_stops_the_target(
        kind, err, bus_lines):
    """A single target whose centring sync the mount refused, or did not
    confirm, stops in the fixed words for that kind. ONE warning carries the
    name, the reply and (when known) the figure. It never enters the no-light
    hold and never says "continuing", and the mount's idle bookkeeping is
    done first, so the park-hold watches this target.

    MUTANT "skip the check in _setup_target" (the first ``sync_reason =
    self._sync_not_taken(result)`` made ``sync_reason = None``): RED, all
    four -
        measured:  Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
        no figure: AssertionError: a sync not taken reached the no-light
        hold: [{'centered': False, 'error_arcmin': None, ...}]
    MUTANT "unverified reads as nothing" (the ``sync_unverified`` branch of
    `_sync_not_taken` removed): RED, both unverified cases -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    MUTANT "no warning before the stop" (the ``bus.log`` in
    `_stop_if_sync_not_taken` removed): RED, all four -
        assert [] == ["Fictional A...eply 'e11')"]
    MUTANT "raise before the bookkeeping" (``sync_stop = result`` replaced
    by the stop itself, inside the centred branch): RED, all four -
        AssertionError: the stop left the idle clock watching None
    """
    build, const, cause = _KINDS[kind]
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [build(err, reply="e11")])
    holds = _no_hold(e)
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert holds == [], f"a sync not taken reached the no-light hold: {holds}"
    assert _continuing(bus_lines) == [], _continuing(bus_lines)
    assert str(ei.value) == f"centring at acquisition: {const}", str(ei.value)
    figure = ", 150.0' off target" if err is not None else ""
    assert _stop_warnings(bus_lines) == [
        f"Fictional A: centring at acquisition: {cause}{figure} "
        f"(reply 'e11')"]
    assert e._tracked_target is t, (
        f"the stop left the idle clock watching {e._tracked_target}")
    assert len(hub.gotos) == 1, f"premise: one centring: {hub.gotos}"


async def test_the_stop_and_its_warning_say_exactly_this(bus_lines):
    """The two texts, whole: the stop (what the scheduler's skip line, the
    session report and a panel's ``last_error`` carry) and the one warning
    with the figure, which comes BEFORE the reply and no longer opens "the
    field solved but" (FIXES3 G3.2).

    MUTANT "the figure appended to the stop" (``raise
    StopTarget(f"{where}: {const}")`` given `` ({error_arcmin}')`` after
    the constant): RED -
        assert "centring at ...cted (12.34')" == 'centring at ... be corrected'
    MUTANT "reply before figure" (the warning's return made
    ``f"{target.name}: {where}: {what}{said_reply}{off}"``): RED -
        assert ["Fictional A...' off target"] == ["Fictional A...reply 'N/A')"]
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [_refused(12.34, reply="N/A")])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == (
        "centring at acquisition: the mount refused the sync, so its "
        "pointing could not be corrected")
    assert _stop_warnings(bus_lines) == [
        "Fictional A: centring at acquisition: the mount refused the sync, "
        "12.3' off target (reply 'N/A')"]


@pytest.mark.parametrize("kind, err, reply, expected", [
    ("refused", None, "e11",
     "Fictional A: centring at acquisition: the mount refused the sync "
     "(reply 'e11')"),
    ("refused", 7.25, "",
     "Fictional A: centring at acquisition: the mount refused the sync, "
     "7.2' off target"),
    ("unverified", 152.3, "",
     "Fictional A: centring at acquisition: the mount did not confirm the "
     "sync, 152.3' off target"),
    ("unverified", None, "",
     "Fictional A: centring at acquisition: the mount did not confirm the "
     "sync"),
    ("refused", 152.3, "07:23:41",
     "Fictional A: centring at acquisition: the mount refused the sync, "
     "152.3' off target (an unrecognised reply)"),
], ids=["no figure", "no reply", "unverified", "unverified bare",
        "unsafe reply"])
def test_each_clause_is_left_out_when_there_is_nothing_to_say(
        kind, err, reply, expected, bus_lines):
    """No figure clause when the figure is unknown, no reply clause when
    the reply is empty, "(an unrecognised reply)" for one that is not a
    short code, and "did not confirm" for an unverified sync (FIXES3 G3.2).

    MUTANT "an empty reply is unrecognised" (``elif reply:`` made
    ``elif reply is not None:`` in `_sync_not_taken_line`, so "" falls into
    the unrecognised arm): RED, "no reply", "unverified" and "unverified bare" -
        assert 'Fictional A:...gnised reply)' == "Fictional A:...2' off target"
    MUTANT "a missing figure is zero" (``off`` built from
    ``float(err or 0)`` whatever ``err`` is): RED, "no figure" and
    "unverified bare" -
        assert "Fictional A:...0' off target" == 'Fictional A:...firm the sync'
    """
    build, _const, _cause = _KINDS[kind]
    t = _target(name="Fictional A")
    line = SequenceEngine._sync_not_taken_line(
        build(err, reply=reply), t, "centring at acquisition")
    assert line == expected


@_BY_KIND
async def test_two_figures_give_one_stop_text(kind, bus_lines):
    """A panel's TARGET_STOP deferral uses ``str(StopTarget)`` as its
    ``last_error``, and D-03 (`group_rules._held_pass_reason_code`) needs the
    identical text across passes. Two passes refused at different distances
    give the same stop; only the warnings differ.

    MUTANT "the figure appended to the stop": RED, both -
        AssertionError: the stop text moved with the figure:
        "centring at acquisition: ... corrected (152.3')" !=
        "centring at acquisition: ... corrected (7.25')"
    """
    build, _const, _cause = _KINDS[kind]
    stops = []
    for err in (152.3, 7.25):
        t = _target(name="Fictional A")
        e, hub = _setup(t)
        _scripted(hub, [build(err)])
        with pytest.raises(StopTarget) as ei:
            await e._setup_target(0, t)
        stops.append(str(ei.value))
    assert stops[0] == stops[1], (
        f"the stop text moved with the figure: {stops[0]!r} != {stops[1]!r}")
    warned = _stop_warnings(bus_lines)
    assert len(warned) == 2 and warned[0] != warned[1], warned


@pytest.mark.parametrize("reply", ["07:23:41", "+89*59:59", "e11 garbage",
                                   "072341"])
async def test_an_unrecognised_reply_is_never_quoted(reply, bus_lines):
    """The warning quotes a reply only when it is a short code
    (`devices.base.quotable_sync_reply`, the ONE copy of FIXES F1.5's rule,
    FIXES3 G3.3): a desynchronised ``:GR#``-shaped string is a coordinate
    (#140, #166), and so is a bare run of three or more digits whose
    separators were lost ("072341"). Anything else is "an unrecognised
    reply".

    MUTANT "quote any reply" (the warning's reply clause made
    ``f" (reply {reply!r})"`` whenever there is one): RED, all four -
        AssertionError: the reply reached a log line: "Fictional A:
        centring at acquisition: the mount refused the sync, 150.0' off
        target (reply '07:23:41')"
    MUTANT "the engine's own copy of the old rule" (``quotable_sync_reply``
    replaced by a local ``re.fullmatch(r"[A-Za-z0-9/]{1,8}", reply)``, the
    round-2 engine regex with no digit-run test): RED, the "072341" case
    only -
        AssertionError: the reply reached a log line: "Fictional A:
        centring at acquisition: the mount refused the sync, 150.0' off
        target (reply '072341')"
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [_refused(150.0, reply=reply)])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    texts = [str(ei.value)] + [m for _l, m, _s in bus_lines]
    for text in texts:
        assert reply not in text, f"the reply reached a log line: {text!r}"
    assert any("(an unrecognised reply)" in w
               for w in _stop_warnings(bus_lines)), bus_lines


@pytest.mark.parametrize("answer, said", [
    ({"centered": False, "error_arcmin": 4.0}, "converged to 4.0'"),
])
async def test_control_a_plain_miss_still_continues(answer, said, bus_lines):
    """CONTROL. A centring that missed without a sync not taken keeps
    today's behaviour: "continuing", no stop, no hold.

    MUTANT "every miss reads as not taken in setup" (``sync_reason =
    SOLVE_REASON_SYNC_REFUSED if not result["centered"] else None`` where
    setup first reads it): RED, the miss went unreported (and no stop
    either: the stop helper asks the result itself) -
        AssertionError: [('info', 'target 1/1: Fictional A', 'sequence')]
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [answer])
    holds = _no_hold(e)
    await e._setup_target(0, t)
    assert holds == []
    assert any(said in m for m in _continuing(bus_lines)), bus_lines


async def test_control_a_solve_failure_still_holds_for_light(bus_lines):
    """CONTROL. A centring with no figure and no sync not taken is a failed
    solve, and a single target still holds for light (#596 shape b).

    MUTANT "every miss reads as not taken in setup": RED -
        AssertionError: a failed solve no longer holds for light: []
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": None}])
    holds = _no_hold(e)
    await e._setup_target(0, t)
    assert len(holds) == 1, f"a failed solve no longer holds for light: {holds}"


@_BY_KIND
async def test_a_hold_retry_whose_sync_was_not_taken_ends_the_hold_and_stops(
        kind, bus_lines):
    """The REAL no-light hold: the first centring found nothing to solve, the
    first retry got light and a solve, and the mount refused its sync, or
    did not confirm it, with no figure to report. The hold ends on that
    retry (it is not "still no light") and setup stops the target.

    MUTANT "the hold ignores a sync not taken" (``and
    self._sync_not_taken(hop_centring) is None`` removed from the hold's
    loop): RED, both -
        AssertionError: the hold kept retrying a solve that worked: 7
        centrings
    MUTANT "setup ignores a hold result not taken" (the
    ``sync_reason = self._sync_not_taken(result)`` after the hold made
    ``sync_reason = None``): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    build, const, _cause = _KINDS[kind]
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": None}, build(None)])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert len(hub.gotos) == 2, (
        f"the hold kept retrying a solve that worked: {len(hub.gotos)} "
        f"centrings")
    assert str(ei.value) == f"centring at acquisition: {const}"
    assert not any("still no light" in m for _l, m, _s in bus_lines), bus_lines


# ================================================== target setup, a mosaic

def _member(e, t, *, require_centred: bool) -> TargetGroup:
    g = TargetGroup(id="g1", name="Fictional mosaic",
                    require_centred=require_centred)
    e._group_of = lambda target: g if target is t else None
    e._hop_angle_within = lambda *a, **kw: False
    return g


@_BY_KIND
async def test_a_panel_that_requires_centring_is_deferred_with_the_fixed_words(
        kind, bus_lines):
    """A mosaic panel that requires centring is deferred, kind CENTRING,
    with the hub's fixed sentence FOR THAT KIND as its last error, so two
    passes that failed the same way read as one rig-side reason (D-03). Not
    a StopTarget.

    MUTANT "the figure as the panel's last error" (``hop_miss = detail``
    for a sync not taken, the "converged to X'" text): RED, both -
        E   assert "converged to 150.0'" == 'the mount re... be corrected'
    MUTANT "the panel always gets the refused words" (``hop_miss =
    sync_reason`` made ``hop_miss = SOLVE_REASON_SYNC_REFUSED``): RED, the
    unverified case -
        E   AssertionError: assert 'the mount re... be corrected' == 'the mount di... be corrected'
    """
    build, const, _cause = _KINDS[kind]
    t = _target(name="Fictional A 1-1", mosaic_group="g1")
    e, hub = _setup(t)
    _member(e, t, require_centred=True)
    _scripted(hub, [build(150.0)])
    with pytest.raises(PanelDeferred) as ei:
        await e._setup_target(0, t)
    assert ei.value.kind == CENTRING, ei.value.kind
    assert ei.value.last_error == const
    assert _continuing(bus_lines) == [], _continuing(bus_lines)


@_BY_KIND
async def test_a_panel_that_does_not_require_centring_stops(kind, bus_lines):
    """A panel set to "shoot anyway" (``require_centred`` off) is still not
    shot on a field the mount did not correct: it stops, and its visit
    defers it (`_visit_panel`'s TARGET_STOP, ``last_error`` the fixed stop).

    MUTANT "only a required panel is caught" (the stop arm of the setup
    check removed, leaving the deferral): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    build, const, _cause = _KINDS[kind]
    t = _target(name="Fictional A 1-2", mosaic_group="g1")
    e, hub = _setup(t)
    _member(e, t, require_centred=False)
    _scripted(hub, [build(150.0)])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == f"centring at acquisition: {const}"
    assert _continuing(bus_lines) == [], _continuing(bus_lines)


# ============================================== the three mid-run re-centres

_RECENTRES = pytest.mark.parametrize(
    "recentre, where", [
        (_after_the_sweep, "re-centring after the unguided sweep"),
        (_after_a_lost_star, "re-centring after the guide star went missing"),
        (_after_a_walking_field, "re-centring after the guided field walked"),
    ], ids=["after the unguided sweep", "after a lost star",
            "after a walking field"])


@_BY_KIND
@_RECENTRES
async def test_a_recentre_not_taken_stops_the_target_before_guiding(
        recentre, where, kind, bus_lines):
    """Each mid-run re-centre whose sync the mount refused, or did not
    confirm, stops the target in fixed words before the guider is restarted
    on the wrong field; the figure is in the warning.

    MUTANT "the site ignores the result" (that site's
    ``_stop_if_sync_not_taken`` call removed, one site at a time): RED,
    that site's two cases (and the scan premise below) -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    MUTANT "unverified reads as nothing": RED, the three unverified cases,
    with the same failure.
    MUTANT "the old where" ("re-centring after the guide star went
    missing" put back as "re-centring after guiding was lost"): RED, the
    lost-star cases -
        AssertionError: assert 're-centring a... be corrected' == 're-centring a... be corrected'
    """
    build, const, _cause = _KINDS[kind]
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    _scripted(hub, [build(42.0)])
    with pytest.raises(StopTarget) as ei:
        await recentre(e, t)
    assert len(hub.gotos) == 1, f"premise: one re-centre: {hub.gotos}"
    assert str(ei.value) == f"{where}: {const}"
    warned = _stop_warnings(bus_lines)
    assert len(warned) == 1 and "42.0'" in warned[0] and where in warned[0], (
        warned)
    assert "start" not in hub.guider.calls, (
        f"guiding was restarted on a field the mount did not correct: "
        f"{hub.guider.calls}")


@_RECENTRES
async def test_control_a_plain_recentre_miss_still_resumes(recentre, where,
                                                           bus_lines):
    """CONTROL. A re-centre that missed without a sync not taken is
    non-fatal, as it always was: no stop, and guiding restarts where a
    guider was.

    MUTANT "every miss stops" (`_sync_not_taken` answers
    ``SOLVE_REASON_SYNC_REFUSED`` for any result that is not centred): RED,
    all three, the first being -
        astrodeck.sequence.engine.StopTarget: re-centring after the unguided
        sweep: the mount refused the sync, so its pointing could not be
        corrected
    """
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    _scripted(hub, [{"centered": False, "error_arcmin": 4.0}])
    await recentre(e, t)
    assert len(hub.gotos) == 1, f"premise: one re-centre: {hub.gotos}"
    if recentre is not _after_the_sweep:
        assert "start" in hub.guider.calls, hub.guider.calls


# ============================== the surfaced texts: humanizer and the cut

def _wheres() -> list[str]:
    """Every ``where`` the engine passes to `_stop_if_sync_not_taken`, read
    from its source, so a site added later is graded without editing this
    file. Each must be a string literal: a computed ``where`` could carry a
    figure into the fixed-words stop."""
    tree = ast.parse(Path(engine_mod.__file__).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_stop_if_sync_not_taken"):
            last = node.args[-1]
            assert isinstance(last, ast.Constant) and isinstance(
                last.value, str), (
                f"a computed where at line {node.lineno}: {ast.unparse(last)}")
            found.append(last.value)
    return found


def _opted_out_wheres() -> list[str]:
    """The ``where`` of every site that can log the opted-out warning: the
    calls to `_stop_if_sync_not_taken` that pass ``centring_wanted`` (every
    other site uses the default, True, and so always stops), read from the
    engine's source."""
    tree = ast.parse(Path(engine_mod.__file__).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_stop_if_sync_not_taken"
                and any(k.arg == "centring_wanted" for k in node.keywords)):
            found.append(node.args[-1].value)
    return found


def test_premise_only_the_flip_and_the_recovery_can_go_on():
    """PREMISE for the opted-out figure check in
    test_the_whole_figure_fits_before_the_cut: only the flip and the
    tracking recovery pass ``centring_wanted`` (the three mid-run
    re-centres gate the whole re-centre on the same test, and setup has
    its own centring-off branch), so the longest ``where`` an opted-out
    warning can carry is the recovery's. A new site passing it is graded
    by the 137 checks below through this list.

    MUTANT "the lost-star site passes the gate" (``centring_wanted=False``
    added to the "re-centring after the guide star went missing" call):
    RED -
        AssertionError: ['re-centring after the meridian flip', 're-centring
        after the tracking recovery', 're-centring after the guide star went
        missing']
    """
    assert sorted(_opted_out_wheres()) == [
        "re-centring after the meridian flip",
        "re-centring after the tracking recovery",
    ], _opted_out_wheres()


def test_premise_every_stop_site_is_found():
    """PREMISE for the two tests below: the scan finds all six sites
    (acquisition, the flip, the three mid-run re-centres, the tracking
    recovery), so neither can pass on an empty list.

    RED under each "the site ignores the result" mutant (a call removed)
    and under "raise before the bookkeeping" (a seventh call added).
    """
    assert sorted(_wheres()) == sorted([
        "centring at acquisition",
        "re-centring after the meridian flip",
        "re-centring after the unguided sweep",
        "re-centring after the guide star went missing",
        "re-centring after the guided field walked",
        "re-centring after the tracking recovery",
    ]), _wheres()


def _texts_for(where: str, kind: str, err, reply: str, bus_lines):
    """(the stop, the scheduler's skip line for a 12-char name, the warning)
    for one ``where``, from the real helper."""
    build, _const, _cause = _KINDS[kind]
    name = "Fictional 12"
    assert len(name) == 12
    t = _target(name=name)
    e = SequenceEngine.__new__(SequenceEngine)
    before = len(bus_lines)
    with pytest.raises(StopTarget) as ei:
        e._stop_if_sync_not_taken(build(err, reply=reply), t, where)
    stop = str(ei.value)
    warned = [m for _l, m, _s in bus_lines[before:]]
    assert len(warned) == 1, warned
    return stop, f"{name}: skipped — {stop}", warned[0]


def _opted_out_line(where: str, kind: str, err, reply: str,
                    bus_lines) -> str:
    """The one warning an opted-out target gets instead of a stop (FIXES3
    G3.1), for a 12-char name, from the real helper; it raises nothing."""
    build, _const, _cause = _KINDS[kind]
    t = _target(name="Fictional 12")
    e = SequenceEngine.__new__(SequenceEngine)
    before = len(bus_lines)
    e._stop_if_sync_not_taken(build(err, reply=reply), t, where,
                              centring_wanted=False)
    warned = [m for _l, m, _s in bus_lines[before:]]
    assert len(warned) == 1 and "centring is off" in warned[0], warned
    return warned[0]


@_BY_KIND
@pytest.mark.parametrize("err", [152.3, None], ids=["measured", "no figure"])
@pytest.mark.parametrize("reply", ["e11", "", "07:23:41"],
                         ids=["code", "empty", "unrecognised"])
def test_no_surfaced_text_is_rewritten_by_the_humanizer(kind, err, reply,
                                                         bus_lines):
    """The stop, the skip line the scheduler logs it in, and the warning,
    for every ``where``, every kind, with and without a figure and with each
    shape of reply: none trips a humanizer rule (FIXES rule 2), so the
    operator reads our words and not "Guiding was lost - recovering" or
    "Plate-solve failed - check focus/exposure".

    MUTANT "the old where" (the lost-star site's ``where`` put back as
    "re-centring after guiding was lost"): RED, all twelve -
        AssertionError: the humanizer rewrites: 're-centring after guiding
        was lost: the mount refused the sync, so its pointing could not be
        corrected'
    MUTANT "plate in the warning" (the warning's ``{what}`` preceded by
    "the plate solve worked but "): RED, all twelve, for example -
        AssertionError: the humanizer rewrites: 'Fictional 12: re-centring
        after the meridian flip: the plate solve worked but the mount did
        not confirm the sync (an unrecognised reply)'
    The opted-out target's warning (FIXES3 G3.1) is graded too.
    MUTANT "guid and lost in the opted-out clause"
    (``_SYNC_NOT_TAKEN_GOES_ON`` made "; centring is off, so guided
    imaging goes on until the star is lost"): RED, all twelve, for example
    (FIXES4 H3 layout) -
        AssertionError: the humanizer rewrites: 'Fictional 12: re-centring
        after the meridian flip: the mount refused the sync; centring is
        off, so guided imaging goes on until the star is lost (an
        unrecognised reply)'
    """
    for where in _wheres():
        texts = list(_texts_for(where, kind, err, reply, bus_lines))
        texts.append(_opted_out_line(where, kind, err, reply, bus_lines))
        for text in texts:
            assert not _humanizer_rewrites(text), (
                f"the humanizer rewrites: {text!r}")


@_BY_KIND
def test_the_cause_fits_before_the_cut(kind, bus_lines):
    """humanizeLog cuts a line longer than 140 characters to 137 and an
    ellipsis (FIXES rule 3). For every ``where``, "the mount refused the
    sync" / "the mount did not confirm the sync" ends within the first 137
    characters of the scheduler's skip line for a 12-character name, and of
    the warning with a 3-digit figure.

    MUTANT "a preamble before the cause" (the stop made ``f"{where}: not
    imaging a field nobody could centre, because {const}"``): RED, both -
        AssertionError: the cause ends at 140 in 'Fictional 12: skipped —
        re-centring after the tracking recovery: not imaging a field nobody
        could centre, because the mount refused the sync, so its pointing
        could not be corrected'
        AssertionError: the cause ends at 144 in 'Fictional 12: skipped —
        re-centring after the meridian flip: not imaging a field nobody
        could centre, because the mount did not confirm the sync, so its
        pointing could not be corrected'
    """
    _build, _const, cause = _KINDS[kind]
    for where in _wheres():
        _stop, skip, warning = _texts_for(where, kind, 152.3, "e11",
                                          bus_lines)
        for line in (skip, warning):
            end = line.find(cause) + len(cause)
            assert line.find(cause) >= 0 and end <= 137, (
                f"the cause ends at {end} in {line!r}")


@_BY_KIND
def test_the_whole_figure_fits_before_the_cut(kind, bus_lines):
    """The review's minor finding: with a 12-char name and the longer
    ``where``s, humanizeLog's cut at 137 took the figure mid-number
    ("it is still 1..."), a truncated and misleading value. The warning now
    puts the figure before the reply and drops "the field solved but"
    (FIXES3 G3.2), so the WHOLE "152.3' off target" ends within the first
    137 characters for the longest ``where``, in the stop's warning.
    The opted-out warning puts its outcome first (FIXES4 H3), so its
    figure comes later and is checked for the longest ``where`` that
    warning can carry (test_premise_only_the_flip_and_the_recovery_can_go_on):
    the whole number with its unit, "152.3'", ends within 137. For "did not
    confirm" after the tracking recovery it ends at 134 and the word "off"
    after it at 138, so that worst case reads "(152.3' of..." on screen:
    the number is never cut, the word after it can be.

    MUTANT "the round-2 layout" (the return put back as ``f"{target.name}:
    {where}: the field solved but {what}{said_reply}"`` +
    ``f"; it is still {float(err):.1f}' off target"``): RED, both -
        AssertionError: the figure ends at 153 in "Fictional 12:
        re-centring after the guide star went missing: the field solved
        but the mount refused the sync (reply 'e11'); it is still 152.3'
        off target" (161 for "did not confirm")
    MUTANT "the opted-out reply before the figure" (the bracket's parts
    made ``(reply_words, f"{fig} off")``): RED, both -
        AssertionError: the figure ends at 139 in "Fictional 12:
        re-centring after the tracking recovery: the mount refused the
        sync; centring is off, so imaging goes on (reply 'e11', 152.3'
        off)" (147 for "did not confirm")
    """
    _build, _const, _cause = _KINDS[kind]
    longest = max(_wheres(), key=len)
    assert longest == "re-centring after the guide star went missing", (
        f"premise: the longest where is {longest!r}")
    figure = "152.3' off target"
    _stop, _skip, warning = _texts_for(longest, kind, 152.3, "e11", bus_lines)
    at = warning.find(figure)
    assert at >= 0 and at + len(figure) <= 137, (
        f"the figure ends at {at + len(figure)} in {warning!r}")
    longest_opted = max(_opted_out_wheres(), key=len)
    assert longest_opted == "re-centring after the tracking recovery", (
        f"premise: the longest opted-out where is {longest_opted!r}")
    opted = _opted_out_line(longest_opted, kind, 152.3, "e11", bus_lines)
    figure = "152.3'"
    at = opted.find(figure)
    assert at >= 0 and at + len(figure) <= 137, (
        f"the figure ends at {at + len(figure)} in {opted!r}")


@_BY_KIND
def test_imaging_goes_on_fits_before_the_cut(kind, bus_lines):
    """The round-3 finding (FIXES4 H3): the opted-out clause came after the
    figure and the reply, so humanizeLog's cut at 137 always took it, and
    the operator read "the mount refused the sync" and then watched the run
    carry on with nothing saying why. The outcome now comes straight after
    the cause, so with a 12-char name, the longest ``where`` of ALL six
    sites (stricter than the two that can reach it), a figure of 152.3 and
    the reply 'e11', "imaging goes on" ends within the first 137
    characters.

    MUTANT "the round-3 order" (the opted-out return put back as
    ``f"{target.name}: {where}: {what}, {fig} off target (reply ...)"`` +
    ``"; centring is off for this target, so imaging goes on"``): RED,
    both -
        AssertionError: 'imaging goes on' ends at 173 in "Fictional 12:
        re-centring after the guide star went missing: the mount refused
        the sync, 152.3' off target (reply 'e11'); centring is off for this
        target, so imaging goes on" (181 for "did not confirm")
    MUTANT "the short clause after the figure" (the opted-out return made
    ``f"{target.name}: {where}: {what}{detail}"`` + the new
    ``_SYNC_NOT_TAKEN_GOES_ON``): RED, both -
        AssertionError: 'imaging goes on' ends at 150 in "Fictional 12:
        re-centring after the guide star went missing: the mount refused
        the sync (152.3' off, reply 'e11'); centring is off, so imaging
        goes on" (158 for "did not confirm")
    """
    _build, _const, cause = _KINDS[kind]
    longest = max(_wheres(), key=len)
    assert longest == "re-centring after the guide star went missing", (
        f"premise: the longest where is {longest!r}")
    line = _opted_out_line(longest, kind, 152.3, "e11", bus_lines)
    assert len(line) > 140, f"premise: the line must be cut: {line!r}"
    goes_on = "imaging goes on"
    at = line.find(goes_on)
    assert at >= 0 and at + len(goes_on) <= 137, (
        f"{goes_on!r} ends at {at + len(goes_on)} in {line!r}")
    assert line.find(cause) >= 0 and line.find(cause) < at, line


@pytest.mark.parametrize("kind, err, reply, expected", [
    ("refused", 152.3, "e11",
     "Fictional A: re-centring after the meridian flip: the mount refused "
     "the sync; centring is off, so imaging goes on (152.3' off, reply "
     "'e11')"),
    ("refused", 152.3, "07:23:41",
     "Fictional A: re-centring after the meridian flip: the mount refused "
     "the sync; centring is off, so imaging goes on (152.3' off, an "
     "unrecognised reply)"),
    ("refused", 7.25, "",
     "Fictional A: re-centring after the meridian flip: the mount refused "
     "the sync; centring is off, so imaging goes on (7.2' off)"),
    ("refused", None, "e11",
     "Fictional A: re-centring after the meridian flip: the mount refused "
     "the sync; centring is off, so imaging goes on (reply 'e11')"),
    ("refused", None, "07:23:41",
     "Fictional A: re-centring after the meridian flip: the mount refused "
     "the sync; centring is off, so imaging goes on (an unrecognised "
     "reply)"),
    ("unverified", 152.3, "",
     "Fictional A: re-centring after the meridian flip: the mount did not "
     "confirm the sync; centring is off, so imaging goes on (152.3' off)"),
    ("unverified", None, "",
     "Fictional A: re-centring after the meridian flip: the mount did not "
     "confirm the sync; centring is off, so imaging goes on"),
], ids=["figure and code", "figure and unsafe reply", "figure only",
        "code only", "unsafe reply only", "unverified", "unverified bare"])
def test_the_opted_out_line_says_exactly_this(kind, err, reply, expected,
                                              bus_lines):
    """The opted-out warning, whole, for each shape (FIXES4 H3): the
    outcome straight after the cause, then one bracket with the figure and
    the reply, each left out when unknown or empty, "an unrecognised reply"
    for one that is not a short code, "did not confirm" for an unverified
    sync, and no bracket at all when there is nothing to put in it. Read
    from the real `_stop_if_sync_not_taken`, which raises nothing here.

    MUTANT "the round-3 order": RED, all seven, for example -
        assert ['Fictional A...ging goes on'] == ["Fictional A...reply 'e11')"]
    MUTANT "an empty reply is unrecognised" (``elif reply:`` made ``elif
    reply is not None:``): RED, "figure only", "unverified" and
    "unverified bare" -
        assert ['Fictional A...nised reply)'] == ["Fictional A...n (7.2' off)"]
    MUTANT "an empty bracket" (``if parts else ""`` dropped, so the bracket
    is always written): RED, "unverified bare" -
        assert ['Fictional A...g goes on ()'] == ['Fictional A...ging goes on']
    """
    build, _const, _cause = _KINDS[kind]
    t = _target(name="Fictional A")
    e = SequenceEngine.__new__(SequenceEngine)
    before = len(bus_lines)
    e._stop_if_sync_not_taken(build(err, reply=reply), t,
                              "re-centring after the meridian flip",
                              centring_wanted=False)
    assert [m for _l, m, _s in bus_lines[before:]] == [expected]


# =========================================================== the flip


@pytest.fixture
async def flip_hub(tmp_path, monkeypatch):
    """test_the_flip_stops_paying_for_itself.py's own hub, with its
    config-store isolation (its autouse fixture), under a name of its own."""
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    a_real_site(monkeypatch)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _flip_with(flip_hub, monkeypatch, answer: dict):
    """A flip that really swaps sides, on the REAL ``hub.meridian_flip``, whose
    re-centre answers ``answer``: the hub carries it up in its own result."""
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)

    async def goto(ra_h, dec_d, **kw):
        st["gotos"].append((ra_h, dec_d))
        st["side"] = "east" if st["side"] == "west" else "west"
        return dict(answer)

    monkeypatch.setattr(flip_hub, "goto_and_center", goto)
    return e, t, st


@_BY_KIND
async def test_a_flip_recentre_not_taken_stops_after_the_bookkeeping(
        kind, flip_hub, monkeypatch, bus_lines):
    """The flip swapped sides and its re-centre's sync was refused, or not
    confirmed. The flip's bookkeeping stands (the latch is spent, the flip
    was taken), the post-flip sweep does not run on a field nobody could
    centre, and the target stops.

    MUTANT "the flip ignores the result" (the flip's
    ``_stop_if_sync_not_taken`` call removed): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    MUTANT "stop before the bookkeeping" (the call moved to straight after
    the ``hub.meridian_flip`` await, before the side reads and the latch):
    RED, both -
        AssertionError: the flip's latch was left armed: True
    This is the CONTROL for the opted-out case below: the target asked for
    centring (the flip fixture's own target has ``center`` off).
    MUTANT "nobody stops at the flip" (the flip's ``centring_wanted=``
    made ``False``): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    build, const, _cause = _KINDS[kind]
    e, t, st = _flip_with(flip_hub, monkeypatch, build(30.0, reply="N/A"))
    t.center = True
    with pytest.raises(StopTarget) as ei:
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["gotos"], "premise: the flip must have re-slewed"
    assert str(ei.value) == f"re-centring after the meridian flip: {const}"
    assert e._flip_armed is False, (
        f"the flip's latch was left armed: {e._flip_armed}")
    assert st["af"] == [], (
        f"the post-flip sweep ran on a field the mount did not correct: "
        f"{st['af']}")


_OPTED_OUT = pytest.mark.parametrize(
    "opt_out", [{"center": False}, {"center": True, "calibration": True}],
    ids=["centring off", "calibration target"])


@_BY_KIND
@_OPTED_OUT
async def test_a_target_that_opted_out_of_centring_is_not_stopped_at_the_flip(
        kind, opt_out, flip_hub, monkeypatch, bus_lines):
    """The review's major finding (FIXES3 G3.1): ``hub.meridian_flip``
    re-centres whatever ``target.center`` says, and the flip used to stop a
    target with centring off when that re-centre's sync was not taken.
    Such a target asked for no centring, so the field an uncentred slew
    gives is all it wanted. It is NOT stopped: the post-flip sweep runs as
    before, and the one warning says what happened, figure first, and that
    imaging goes on. Same gate as the three mid-run re-centres
    (``center`` and not ``calibration``), so a calibration target counts
    as opted out too.

    MUTANT "the flip stops whatever the target says" (the flip's
    ``centring_wanted=`` made ``True``): RED, all four -
        astrodeck.sequence.engine.StopTarget: re-centring after the meridian
        flip: the mount refused the sync, so its pointing could not be
        corrected
    MUTANT "the gate forgets calibration" (the flip's gate reduced to
    ``getattr(target, "center", False)``): RED, the two calibration
    cases -
        astrodeck.sequence.engine.StopTarget: re-centring after the meridian
        flip: the mount did not confirm the sync, so its pointing could not
        be corrected
    """
    build, _const, cause = _KINDS[kind]
    e, t, st = _flip_with(flip_hub, monkeypatch, build(30.0, reply="N/A"))
    for k, v in opt_out.items():
        setattr(t, k, v)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["gotos"], "premise: the flip must have re-slewed"
    assert e._flip_armed is False
    assert st["af"] == ["post-flip autofocus"], st["af"]
    assert _stop_warnings(bus_lines) == [
        f"T: re-centring after the meridian flip: {cause}{_GOES_ON} "
        f"(30.0' off, reply 'N/A')"], _stop_warnings(bus_lines)


async def test_control_a_flip_whose_recentre_missed_goes_on(
        flip_hub, monkeypatch, bus_lines):
    """CONTROL. A flip whose re-centre missed without a sync not taken goes
    on exactly as before: no stop, and the post-flip sweep runs.

    MUTANT "every miss stops" (`_sync_not_taken`): RED -
        astrodeck.sequence.engine.StopTarget: re-centring after the meridian
        flip: the mount refused the sync, so its pointing could not be
        corrected
    """
    e, t, st = _flip_with(flip_hub, monkeypatch,
                          {"centered": False, "error_arcmin": 4.0})
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["af"] == ["post-flip autofocus"], st["af"]
    assert e._flip_armed is False


# ============================================= the tracking-refusal recovery


@pytest.fixture
async def recovery_hub(tmp_path, monkeypatch):
    """test_recovery_centring_is_measured.py's hub (a configured fixture
    site, night by decree, a fast mount), under a name of its own."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Fixture", latitude=40.0, longitude=-74.0))
    store.set_safety(SafetyConfig(enabled=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    monkeypatch.setattr(engine_mod.schedule, "dark_enough",
                        lambda *a, **kw: True)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@_BY_KIND
async def test_a_recovery_recentre_not_taken_stops_in_its_own_words(
        kind, recovery_hub, monkeypatch, bus_lines):
    """The frame loop's tracking enforcement recovered the mount, and the
    recovery's re-centre sync was refused, or not confirmed. The target
    stops in the stop's words, not in "the mount is not tracking and will
    not resume" (the recovery worked: the mount tracks), and the recovery
    does not report its re-centre as "continuing".

    MUTANT "the recovery ignores the result" (the ``centring is None``
    stop in `_recover_from_tracking_refusal` removed): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    MUTANT "the recovery reports an unverified sync as continuing"
    (`_do_tracking_recovery`'s suppression put back as ``not
    centring.get("sync_refused")``): RED, the unverified case -
        AssertionError: ["Alpha: re-centring after the recovery converged
        to 25.0' — continuing"]
    MUTANT "stop inside the recovery sequence" (the stop raised in
    `_do_tracking_recovery` instead, under its caller's broad catch, which
    turns it into "the recovery failed"): RED, both -
        AssertionError: the stop is in the wrong words: the mount is not
        tracking and will not resume — every light frame from here would be
        a streak
    This is the CONTROL for the opted-out case at the end of this file:
    the target asks for centring.
    MUTANT "nobody stops in the recovery" (its ``centring_wanted=`` made
    ``False``): RED, both -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    build, const, _cause = _KINDS[kind]
    st = rcm._pinned_mount(recovery_hub, monkeypatch, [build(25.0)])
    e, t = rcm._engine(recovery_hub)
    with pytest.raises(StopTarget) as ei:
        await e._enforce_tracking(t.steps[0], t)
    assert "park" in st["events"] and "goto" in st["events"], (
        f"premise: the recovery must run: {st['events']}")
    assert str(ei.value) == (
        f"re-centring after the tracking recovery: {const}"), (
        f"the stop is in the wrong words: {ei.value}")
    assert rcm._misses(bus_lines) == [], rcm._misses(bus_lines)


@_BY_KIND
async def test_a_recentre_not_taken_after_setups_recovery_stops_the_target(
        kind, recovery_hub, monkeypatch, bus_lines):
    """Setup's own goto died on a mount pinned at its limit; the recovery
    re-centred and the mount refused the sync, or did not confirm it. Setup
    reads it off the recovery's copied result and stops, rather than
    "centering plate solve failed — continuing".

    MUTANT "skip the check in _setup_target", and "only a required panel is
    caught": RED, each, both kinds -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    build, const, _cause = _KINDS[kind]
    st = rcm._pinned_mount(recovery_hub, monkeypatch,
                           [rcm.REFUSED, build(None)])
    e, t = rcm._engine(recovery_hub)
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    rcm._assert_recovered(st)
    assert str(ei.value) == f"centring at acquisition: {const}", str(ei.value)
    assert rcm._misses(bus_lines) == [], rcm._misses(bus_lines)


@_BY_KIND
@_OPTED_OUT
async def test_a_target_that_opted_out_is_not_stopped_by_the_recovery(
        kind, opt_out, recovery_hub, monkeypatch, bus_lines):
    """The same finding for the frame loop's tracking enforcement (FIXES3
    G3.1): `_do_tracking_recovery` re-centres whatever ``target.center``
    says, and `_recover_from_tracking_refusal` stopped a target with
    centring off when that re-centre's sync was not taken, while setup
    tolerated the very same refusal for it. Now the recovery succeeds, the
    target is not stopped, the one warning says imaging goes on, and the
    recovery says nothing of "continuing". The control is
    test_a_recovery_recentre_not_taken_stops_in_its_own_words (``center``
    on).

    MUTANT "the recovery stops whatever the target says" (its
    ``centring_wanted=`` made ``True``): RED, all four -
        astrodeck.sequence.engine.StopTarget: re-centring after the tracking
        recovery: the mount refused the sync, so its pointing could not be
        corrected
    MUTANT "the recovery's gate forgets calibration": RED, the two
    calibration cases -
        astrodeck.sequence.engine.StopTarget: re-centring after the tracking
        recovery: the mount did not confirm the sync, so its pointing could
        not be corrected
    """
    build, _const, cause = _KINDS[kind]
    st = rcm._pinned_mount(recovery_hub, monkeypatch, [build(25.0, reply="")])
    e, t = rcm._engine(recovery_hub)
    for k, v in opt_out.items():
        setattr(t, k, v)
    await e._enforce_tracking(t.steps[0], t)
    assert "park" in st["events"] and "goto" in st["events"], (
        f"premise: the recovery must run: {st['events']}")
    assert _stop_warnings(bus_lines) == [
        f"Alpha: re-centring after the tracking recovery: {cause}{_GOES_ON} "
        f"(25.0' off)"], _stop_warnings(bus_lines)
    assert rcm._misses(bus_lines) == [], rcm._misses(bus_lines)
