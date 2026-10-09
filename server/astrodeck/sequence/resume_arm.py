# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Auto-resume-at-dusk service (sessions spec §5).

One asyncio task started with the app (pattern: the AlertDispatcher lifespan
task), 60s cadence. Arms via ``Session.auto_resume`` (the PATCH route enforces
the singleton). When the engine is idle, exactly one armed dormant session
exists, and tonight's window for any of its targets has opened (reusing
``schedule.resolve_window``), it runs the recovery ladder and then starts the
run. A refusal alerts and
retries every 10 minutes; when the window closes mid-backoff (dawn) it alerts
one give-up and stays quiet until the window reopens (the next night). The
run_start alert on success comes free from the AlertDispatcher's sequence
state machine. ``resume_veto()`` delegates to the injected WeatherService
(sub-project C, weather spec §4); with no service injected it returns None.

HARD REQUIREMENT (Task 4 review carry-in): ResumeArm is a THIRD ``engine.start``
path, and ``engine.start`` is deliberately unguarded — the route-level gates
that reject an unbounded accepted-quota plan do NOT cover this path. So the
service calls ``quota_unbounded(plan)`` itself before every attempt and refuses
(alert + backoff, stay dormant) when it would run forever under persistent
rejects. ``plan_identity_errors(plan)`` is the second gate on the same terms
(#156): repeated target or step ids, or a rule naming a repeated target.

THE GATES RUN HERE, NOT ONLY IN THE RUN. This file used to say every safety
gate ran inside ``engine.start`` / the run itself. That was accurate while the
resume path WAS ``engine.start``; then ``_recover`` was added in front of it and
the sentence stayed. The ladder plate-solves and re-centers — unattended motion,
by a machine that has just rebooted — so gating only inside the run left exactly
those slews unchecked. ``_recover`` now reads the safety monitor before it moves
anything and puts the re-centering slew through the same altitude limits an
in-run slew gets. Sun avoidance was always covered: it lives at the motion
boundary inside ``goto_and_center``, not in the engine.

ONE STARTER PER SESSION (#211, spec 5.9). The ladder takes minutes, and other
starters (Run CONTINUE, /resume) can take the same session while it runs. So
``recovering`` says when the ladder is running, the ladder stops before its
next move once a run has started, and the start that follows it re-reads the
session under the store's write lock instead of trusting the copy read before
the ladder. See ``tick``.

AN OPERATOR CAN STOP THE LADDER (#220, spec 5.9 and 6.15). Abort, and a
disarm, abandon or delete of the session being recovered, call
``stop_recovery``: the ladder stops before its next step, the step it is
awaiting is cancelled, and nothing starts. ``recovery`` says which step it is
on, for ``GET /api/sequence/resume-arm``.

SO DOES EVERY ROUTE THAT TEARS THE RIG DOWN (#238, spec 6.15).
``/api/disconnect``, and a forced profile apply, profile activate or rig
connect, stop the ladder the way Abort does, then wait on ``wait_stopped``
until it has returned before they touch a device: a teardown under a ladder
still awaiting a solve or a slew pulled the devices out from under it. Unforced,
those three refuse while the ladder runs, as they refuse while a run does.

A HOLD SPEAKS IN WORDS, AND THE NUMBERS ARE KEPT APART (#233). The ladder's
start-floor and slew-limit refusals used to be one sentence carrying the
target's altitude, its floor, an ETA and an azimuth, and that sentence was the
hold's reason and the "auto-resume held" warning, both of which a viewer
reads. Those are the site, re-encoded (#140). Now the reason is words, and the
numbers ride ``hold["site_detail"]``, which ``api/redact.py`` withholds from a
principal without ``view.site_derived``.

A CAPPED OPTIC IS NOT A CLOUD (#251). On 2026-09-24/25 the recovery solve
failed with "Not enough stars." every ten minutes for more than two hours
under a clear sky, because the optic was covered, and nobody was told. The
solve now says whether light reached the sensor (``solve.light``). On a
``NoLightError`` the ladder sends one push alert per session per no-light
spell, retries first after ``RETRY_INTERVAL_S``, and holds in words; a solve
that works or a cloud verdict ends the spell, and so does the end of the
night. A cloud verdict keeps the ten-minute retry and sends nothing, as
before. See ``tick``.

ONLY A DARK MASTER EARNS THE HOURLY RETRY (#308, S3 orchestrator ruling 8).
The retries after a spell's first come every ``NO_LIGHT_RETRY_S`` only when
the verdict was judged against the dark master for the solve's settings,
the one reference that IS that frame's no-light level. A bias master or the
frame the check shoots itself (#262) is a floor drawn from a pedestal, and
whether a thick overcast over a dark site can read inside its band has never
been measured, so every other no-light verdict keeps the ten-minute retry
after its one alert, and the alert says so. The kind is asked of the error
(``NoLightError.reference_kind``), never read out of its words.

THE RE-CENTRE GOES WHERE THE RUN WILL SHOOT, AT ITS ANGLE (#159, I-13;
mosaic spec 5.9, 3.4, Revision 2 ruling 9). The ladder used to slew to the
plan's first light target whatever the ledger said: a finished target, a
mosaic's panel 1-1 whatever the panel order, a panel set aside tonight, and
never with an angle. A finished first target below its start floor refused
the whole resume while the target the run would shoot stood high. Now
``recentre_candidates`` lists what the run can still shoot tonight in the
order it takes it, the ladder re-centres on the first of those that clears
its floor and the slew limits, and ``commanded_rotation`` gives it the
planned angle or the locked one. See step 3 of ``_recover``.

AND THROUGH THE RUN'S GATING (#283). With the site set, each candidate is
asked ``schedule.gating_status``, the question the run asks before it shoots
a target: a closed window or a target that never clears its start floor
tonight is dropped, and a ready target is tried before one waiting on the
clock, its altitude or a constraint. Without it, a session whose only owing
target's window had closed, beside a complete target whose window was open,
was re-centred and started every minute, and each run ended at once.

A NIGHT WITH NOTHING TO SHOOT IS SAID ONCE (#284). That refusal
(``NOTHING_TONIGHT``) is a decided outcome, not a wait for the sky, so it is
latched per session and night: one warning, then quiet re-checks, and at the
window's close one info line that the session's remaining work was set aside
for the night, never the urgent "gave up" line meant for a real loss.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
import traceback
from typing import NamedTuple

from ..aio import reap
from ..config import config_store
from ..devices.base import (GotoRefused, SyncRefused, SyncUnverified,
                            quotable_sync_reply, rig_position_known)
from ..events import bus, night_key
from ..hub import SOLVE_REASON_SYNC_REFUSED, SOLVE_REASON_SYNC_UNVERIFIED
from ..solve.light import (BIAS_MASTER, CLOUD, DARK_MASTER, EXPLICIT,
                           NO_LIGHT_WORDS, SELF_SHOT, FailedSolveError,
                           NoLightError)
from . import schedule
from .group_rules import CENTRING, set_aside_expiry
from .models import (Target, TargetGroup, duplicate_name_warning,
                     plan_identity_errors, quota_unbounded, replan_cooling)
from .panel_order import OrderSnapshot, order_panels
from .policy import resolve_policy
from .session import Session, SessionUnreadable, session_store

_log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 60.0
RETRY_INTERVAL_S = 600.0

#: How often auto-resume tries again while its recovery solve keeps finding no
#: light (#251), after the first retry, which comes at RETRY_INTERVAL_S.
#:
#: A covered optic does not uncover itself, so every ten-minute retry after
#: the alert spends a 12 s exposure and a solver run to learn what the alert
#: already said, and logs it: fifteen identical lines on 2026-09-24. Hourly
#: still picks the night up within the hour of someone uncapping it without
#: telling the rig. The FIRST retry stays at ten minutes on purpose: the
#: operator who reads the alert and walks out to uncap should not then wait
#: an hour for the rig to notice. A wrong no-light verdict, which the
#: classifier works hard never to give, costs at most this long under a sky
#: that has cleared.
#:
#: ONLY FOR A VERDICT A DARK MASTER STANDS BEHIND (#308, S3 orchestrator
#: ruling 8; ``_no_light_backoff``). Every other no-light verdict keeps
#: RETRY_INTERVAL_S: its reference is a floor, and a wrong verdict on it would
#: cost this hour under a sky nobody has measured it against.
NO_LIGHT_RETRY_S = 3600.0

#: How the no-light alert names a reference that is not a dark master, so it
#: can say why it keeps looking every ten minutes (#308). Words only.
_NO_LIGHT_BASIS = {
    BIAS_MASTER: "a bias master",
    SELF_SHOT: "a frame it shot itself at the camera's shortest exposure",
    EXPLICIT: "a reference it was handed",
}

#: Consecutive crashes of ONE session before auto-resume stops trying and stows
#: the rig. Three, because two is inside the range of genuinely transient faults
#: this ladder already recovers from - a USB re-enumeration, a driver that
#: dropped its link between frames - and the point is to distinguish those from
#: a fault that will still be there on the next attempt. At ten minutes a retry
#: this gives roughly half an hour of trying before the night is called.
RESUME_GIVE_UP_AFTER = 3

#: Exposure for the post-restart blind solve. Deliberately longer than
#: solve_and_sync's 3 s default -- see the call site for the measurement.
RECOVERY_SOLVE_EXPOSURE_S = 12.0

#: The largest separation, in degrees, between the mount's reported pointing
#: and the solved field for which the ladder still slews after the mount
#: refused the blind solve's sync where it stands (#850).
#:
#: The blind solve syncs wherever the mount stands, and after a dawn park that
#: is the home position, tube at the pole, where the AM5 refuses every sync
#: with the tube at home: on the bench on 2026-10-08 every sync there was
#: refused: all but one answered ``e11``; one answered ``N/A`` and did not
#: move. Nothing moved,
#: sync-to-self included. Holding on that refusal would end every
#: auto-resume that follows a park. But the refusal is only harmless when the
#: mount's own model is already close to the sky, so the driver's read-back
#: separation (``SyncRefused.residual_deg``) is the test: five degrees covers
#: polar-alignment and home-position error, and the 4.25 deg desync of
#: 2026-10-08. A mount further off than that, refusing the correction, does
#: not know where it points and cannot be corrected from here; it needs the
#: operator at the scope (#857). Inside the bound, step 3's re-centre slews
#: away from the pole, where the AM5 takes syncs, and corrects the rest there.
#:
#: WHAT THE BOUND CANNOT SEE. Near the pole an angular separation cannot see
#: the RA axis angle: a tube at Dec +90 points at the pole whatever the hour
#: angle, so a mount whose RA axis has turned away from home while the tube
#: still points at the pole reads a separation near zero, and the re-centre's
#: goto would be aimed from a wrong hour angle. A small separation is what was
#: measured, not proof the mount knows where it points.
#:
#: SO THE GATE ALSO ASKS THE DRIVER (#867). The go-ahead needs the mount's
#: driver to vouch for its coordinates (``Telescope.position_known``), the
#: evidence the separation lacks: the AM5 latches it False whenever a
#: (re)open reads its home pole, which is what a reset looks like, and only a
#: sync away from the pole or the operator's Trust position clears it. The
#: driver keeps the latch through a sync within its ``SYNC_POLE_BLIND_DEG``
#: of the pole, where its own read-back is blind the same way, so an accepted
#: sync at home does not vouch either. The 5.0 is unchanged.
RECOVERY_REFUSED_SYNC_MAX_DEG = 5.0

#: The hold when the blind solve's sync was refused and the mount's own
#: position disagrees with the sky by more than RECOVERY_REFUSED_SYNC_MAX_DEG
#: (#850, #857). FIXED WORDS, NO FIGURES, NO ``{e}`` TEXT: the hold keeps its
#: ``since`` across retries only while its words stay the same, and a viewer
#: reads it. The figure goes to a separate warning.
#:
#: NEVER "plate" BESIDE "solve" in any of the ``REFUSED_SYNC_*`` words (#850).
#: The UI's ``humanizeLog`` rewrites any line holding both into "Plate-solve
#: failed - check focus/exposure", so the "auto-resume held: ..." warning
#: would tell the operator the solve failed when it worked, and send them to
#: focus and exposure instead of to the mount (#857). Nor "guid" beside
#: "lost", "camera" beside "timeout", "not responding" or "disconnect", or
#: "nina" beside "5", "http" or "error": the other three rewrites.
#:
#: THE ACTION COMES FIRST (#850). ``tick`` logs every hold as "auto-resume
#: held: <words> ...", and the UI cuts a line longer than 140 chars to 137
#: and an ellipsis, so what happens next ("not slewing", "needs someone at
#: the scope", "if this repeats, check the mount's link", "the run is not
#: starting") comes before the explanation, inside those 137 chars. An
#: instruction at the end of a long hold is an instruction nobody sees.
REFUSED_SYNC_FAR_WORDS = (
    "the mount refused the sync after the restart; not slewing, it needs "
    "someone at the scope: its own position disagrees badly with the sky, so "
    "it does not know where it points and cannot be corrected from here")

#: The same hold when the driver could not read the mount's position back
#: after the refusal, so nobody can say how far off it is (#850). Fixed
#: words for the same reasons.
REFUSED_SYNC_UNKNOWN_WORDS = (
    "the mount refused the sync after the restart; not slewing: its position "
    "could not be read back, so nobody can say how far off it points")

#: The hold when the blind solve's sync was NOT refused but nobody could
#: confirm it (``SyncUnverified``, #850): the link failed before, during or
#: after ``:CM#``, or the position read-back never answered. The mount may
#: have taken it or not, so where it points is unknown, and slewing on an
#: unknown position is how a tube meets a pier. Fixed words, as above; the
#: driver's reason goes to a separate warning.
REFUSED_SYNC_UNVERIFIED_WORDS = (
    "the mount did not confirm the sync after the restart; not slewing, and "
    "if this repeats, check the mount's link: nobody can say where it points")

#: The hold when step 3's re-centre came back with the mount refusing its
#: sync (#850): ``goto_and_center`` returns ``sync_refused`` only when the
#: solved field is NOT within tolerance of the target, so the run would image
#: a field it could not centre. Fixed words, as above.
#: Spelled "re-centering", as REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS and the
#: step's older holds ("re-centering after restart refused ...") are: one
#: ladder step words it one way to the operator (#850).
REFUSED_SYNC_RECENTRE_WORDS = (
    "re-centering after restart stopped and the run is not starting: "
    + SOLVE_REASON_SYNC_REFUSED
    + ", and another slew would land in the same place")

#: The same hold when the re-centre's sync was not refused but could not be
#: confirmed (``sync_unverified``, #850). Not "another slew would land in the
#: same place": the mount may have taken the sync, nobody knows. What is
#: known is that the field was not within tolerance and the correction was
#: not confirmed, so the run would image a field the ladder could not centre.
#: A sync nobody could confirm is most often the link, hence the advice.
#: The advice comes BEFORE the hub's reason (#850, round 4): at the end it
#: fell past the UI's 137-char cut of "auto-resume held: ...". Short words
#: in front, so the cause ("the mount did not confirm the sync") still fits
#: inside the cut too.
REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS = (
    "the run is not starting; if this repeats, check the mount's link: "
    "re-centering: "
    + SOLVE_REASON_SYNC_UNVERIFIED)

#: Exception types that, out of the blind solve, are a fault in this
#: program's own code, not the sky, the optics, the camera or the mount
#: (#866): a stub or caller missing a keyword (the #850 round-3 run held 54
#: tests as "blind plate solve failed" for a TypeError), a missing
#: attribute, an unbound name, a missing dict key. ``NameError`` covers
#: ``UnboundLocalError``. Every device, solver and sync error is a
#: ``DeviceError`` (a ``RuntimeError``), so none of them is in here.
#: One KeyError has an outside trigger: ASTAP's .ini parse reads
#: ``kv["CRVAL1"]`` unguarded once PLTSOLVD=T (solve/astap.py), so a
#: truncated result file raises KeyError. Held here as a bug on purpose: the
#: unguarded parse IS a bug, and the hold's retry still recovers when the
#: next solve writes a whole file.
_SOFTWARE_FAULTS = (TypeError, AttributeError, NameError, KeyError)

#: How many of the innermost frames the software-fault warning names (#866).
#: ``_recover`` is always the outermost frame of the caught exception's
#: traceback, so a fault raised in a driver called from the hub is three deep
#: (resume_arm > hub > driver) and shows whole; a deeper fault shows its
#: innermost three, which name the raising line.
FAULT_SITE_FRAMES = 3

#: The hold for a software fault in the blind solve (#866). Fixed words: no
#: type name, no frames (they go to the warning beside it), no ``{e}``.
#: Never "plate" beside "solve": the humanizer would send the operator to
#: focus and exposure for a bug. The action ("report it as a bug") ends
#: inside the 137-char cut of the "auto-resume held: ..." line.
SOLVE_SOFTWARE_FAULT_WORDS = (
    "the resume ladder hit a software fault in the blind solve, not a "
    "sky or optics issue; not slewing: report it as a bug")

#: THE SAFE ORDER, AND NO GOTO (the SAFETY RULE; #867): Trust position if
#: the tube really is at home; otherwise bring it home by eye with a pad key,
#: then Trust it. Ends inside the 137-char cut of the "auto-resume held:
#: ..." line; each hold's cause comes after. Fixed words, no figures, so a
#: hold keeps its ``since``. Named outside the ``REFUSED_SYNC_*_WORDS``
#: family on purpose: those carry "the mount refused the sync" inside the
#: cut, and these put the action first.
_POSITION_UNKNOWN_ACTION = (
    "not slewing, position unknown: tube at home, Trust position; else "
    "bring it home by eye with a pad key, then Trust it.")

#: The hold when the mount refused the blind solve's sync within
#: RECOVERY_REFUSED_SYNC_MAX_DEG of the solved field but its driver says its
#: position is unknown (``Telescope.position_known`` False): an AM5 that read
#: its home pole on (re)connect, or any mount whose last run ended with
#: ``PositionUnknownStop`` (the rig-level latch,
#: ``Telescope.mark_position_unknown``), and has taken no sync and no Trust
#: position since. The words name both causes, so they are true for either
#: (integration review, finding 5). Near the pole the separation cannot see the RA axis angle, so a
#: small one is no evidence, and a goto from a wrong hour angle can put the
#: tube into the pier.
POSITION_UNKNOWN_WORDS = (
    _POSITION_UNKNOWN_ACTION + " The mount refused the sync, and "
    "nothing has confirmed its position since a reconnect or a stopped run "
    "put it in doubt")

#: The hold when the blind solve's sync was ACCEPTED but the driver still
#: says its position is unknown: an AM5 synced within its
#: ``SYNC_POLE_BLIND_DEG`` of the pole, where the read-back cannot tell a
#: sync taken from one ignored (zwo_am5.py ``sync``). The intermittent half
#: of #867 (the bench's one ``N/A`` at home that moved nothing).
POSITION_UNKNOWN_SYNC_WORDS = (
    _POSITION_UNKNOWN_ACTION + " The mount accepted the sync, but its "
    "driver still cannot vouch for the position: near the pole a sync "
    "cannot show where the RA axis points")

#: The hold when no solver is configured and the driver says its position is
#: unknown (#867 by another route, ruling R2). The standing choice to resume
#: a no-solver rig on the mount's model assumes the model is real; a driver
#: that latched ``position_known`` False says it is not.
POSITION_UNKNOWN_NO_SOLVER_WORDS = (
    _POSITION_UNKNOWN_ACTION + " No solver is configured, so nothing here "
    "can confirm where it points")

#: The hold when step 2 passed but the driver stopped vouching before step
#: 3's goto (#867): the slew-limit check awaits a mount read that can run to
#: its bound, and a link that reopens in that window reads the home pole and
#: latches the AM5 again, on the link's clock, not the ladder's.
POSITION_UNKNOWN_RECENTRE_WORDS = (
    _POSITION_UNKNOWN_ACTION + " Its driver stopped vouching for the "
    "position during the resume, as a reopened link does")

#: The UI humanizer's keys (ui/src/lib/humanize.ts): a line holding one of
#: these beside its partner word ("nina" with "5", "http" or "error";
#: "camera" with "timeout" or "disconnect"; "plate" with "solve"; "guid" with
#: "lost") is replaced whole by the UI's own sentence. The software-fault
#: warning interpolates exception type and frame names, which this program
#: does not choose, beside a type name ending "Error", line numbers and the
#: word "solve", so a fault raised in nina.py would reach the operator as
#: "NINA reported an error" (#866). ``_unpaired`` breaks each key with a
#: hyphen ("ni-na.py"), which a developer still reads.
_HUMANIZER_KEYS = re.compile(r"nina|camera|plate|guid", re.IGNORECASE)


def _unpaired(text: str) -> str:
    """``text`` with every humanizer key broken by a hyphen after its second
    letter, so no word pair in it can trip the UI's rewrite (#866)."""
    return _HUMANIZER_KEYS.sub(lambda m: m.group(0)[:2] + "-" + m.group(0)[2:],
                               text)


def _reply_words(code: str | None) -> str:
    """`` (reply 'e11')`` for a sync's reply in a warning, or less (#850).

    Quoted only when ``quotable_sync_reply`` passes it (a short code such as
    ``e11`` or ``N/A``, with no run of three digits): the driver already
    sanitises it, and the ONE copy of the rule is applied here again so a
    reply shaped like a ``:GR#`` answer (a coordinate, a site oracle at home:
    #140, #166) can never be quoted from here. An empty reply says nothing;
    anything else is named, never quoted."""
    if not code:
        return ""
    quoted = quotable_sync_reply(code)
    if quoted is not None:
        return f" (reply '{quoted}')"
    return " (an unrecognised reply)"


def _fault_site(exc: BaseException) -> str:
    """Where ``exc`` was raised, innermost last, as ``basename:function:line``
    joined by `` > `` (#866). Basenames only: the absolute path is kept out
    of everything a viewer reads (``test_no_absolute_paths_externally``). No
    message text: an exception's own words can quote any value."""
    frames = traceback.extract_tb(exc.__traceback__)[-FAULT_SITE_FRAMES:]
    return " > ".join(f"{os.path.basename(f.filename)}:{f.name}:{f.lineno}"
                      for f in frames) or "no frames"

#: Binning of the recovery ladder's autofocus frames. Passed to the sweep
#: rather than left to ``run_native_autofocus``'s own default (the same 2), so
#: the number the sweep ran at and the number its record names cannot differ
#: (#402): the run that stands on the sweep logs it.
RECOVERY_AF_BINNING = 2

#: The words ``recovery`` reports for the ladder's current step, in ladder
#: order (#220). "starting" covers the moment between the tick raising
#: ``recovering`` and the ladder's first step.
#:
#: WORDS, NEVER NUMBERS. ``GET /api/sequence/resume-arm`` is CAP_VIEW_STATUS,
#: which a viewer holds, and a step that carried the target's altitude or the
#: mount's position would hand that viewer the site: a known object's
#: altitude at a known time is a latitude (#140).
LADDER_STEPS = ("starting", "safety", "focus", "autofocus", "solve", "limits",
                "recentre")

#: How long ``wait_stopped`` waits for a stopped ladder to return, in seconds,
#: before a teardown route gives up and answers 409 (#238).
#:
#: A stopped ladder normally returns within a turn or two of the loop: the
#: stop cancels the step it is awaiting. The one thing that legitimately
#: takes longer is a driver finishing a halt ON the cancel before the step
#: can return - Alpaca's ``slew`` sends ``abortslew`` and its exposure wait
#: sends ``abortexposure`` from their ``except CancelledError`` arms
#: (devices/alpaca.py), each one request on the Alpaca client, whose
#: per-request timeout is 30 s (``AlpacaConnection.__init__``). So one request
#: that runs to its timeout, plus 5 s of margin for the loop around it. A
#: bound shorter than that refuses a teardown the halt would have allowed a
#: moment later; a longer one only keeps the operator's request open on a
#: link that is not answering. Read at call time, so a test can shrink it.
LADDER_STOP_WAIT_S = 35.0


def window_open(session: Session, site, twilight_deg: float, now: float) -> bool:
    """True when tonight's window for ANY of the session's targets is open
    (calibration targets shoot any time). Reuses schedule.resolve_window —
    the same resolution a run's scheduler freezes at start.

    A DARK SKY IS THE OUTER BOUND, and it has to be checked here rather
    than left to the per-target schedule. The default ``Schedule`` is
    ``start_mode="now"`` / ``stop_mode="none"``, so ``resolve_window``
    returns ``(now, None)`` and the test below reduces to ``now <= now and
    True`` — open, unconditionally, forever. On 2026-08-11 that had
    auto-resume burning a 4 s exposure and a full ASTAP run every ten
    minutes well after sunrise, on a mount the dawn daemon had already
    parked. The refusal it kept logging was correct; the
    retrying was not.

    Calibration is exempt on purpose and stays first: darks and flats are
    SUPPOSED to be shot in daylight with the mount parked, and the dark-plan
    test harness depends on passing this gate at any hour.

    MODULE-LEVEL BECAUSE TWO CALLERS NEED THE SAME ANSWER. The resume tick asks
    it to decide whether to start; the end-of-run wind-down asks it to decide
    whether the cooler may warm. Two copies of "is the night still on" would
    drift, and the way they would drift is silent.
    """
    for t in session.plan.targets:
        if t.calibration:
            return True
    if not schedule.dark_enough(site, twilight_deg, now):
        return False
    for t in session.plan.targets:
        start, stop = schedule.resolve_window(t.schedule, site, twilight_deg, now)
        if start is not None and start <= now and (stop is None or now < stop):
            return True
    return False


def resume_expected_tonight(hub, now: float | None = None) -> Session | None:
    """The session that is going to be resumed TONIGHT, if there is one.

    ``session_store.armed()`` says what is armed; ``window_open`` says whether
    the tick would act on it before the sky closes. Both, and only both, mean
    "this rig is going to image again in a few minutes".

    WHAT ASKS. The end-of-run wind-down, before it warms the camera. On
    2026-09-08 the NGC 7331 run ended and warmed the sensor while the
    NGC 604 session sat armed behind it; the resumed run then sat waiting for
    the TEC to walk back from -7 to -10 before it could take its first frame.

    AN UNKNOWN SITE ANSWERS None, which is the one place this deliberately does
    NOT match the tick. ``dark_enough`` fails OPEN for a default site — "we
    cannot tell where you are" must not stand auto-resume down — but the
    failure modes here are not symmetric: warming a camera that is about to
    shoot again costs a few minutes of cooling, while holding a TEC at -10 all
    day because we could not tell whether it was still night costs power, dew
    and an unattended cooler nobody asked to leave running. So this withholds
    the warm only when it can show the night is still on.
    """
    try:
        armed = session_store.armed()
    except Exception:      # noqa: BLE001 - bookkeeping must not block a warm
        return None
    if armed is None:
        return None
    from ..site_gate import site_is_set
    site = getattr(hub, "site", None) or {}
    if not site_is_set(site):
        return None
    cfg = config_store.cfg()
    twilight = cfg.safety.twilight_deg if cfg else -12.0
    if not window_open(armed, site, twilight,
                       time.time() if now is None else now):
        return None
    return armed


#: The refusal for a session that has nothing to shoot tonight (#159, #283).
#: Words only, like every reason ``_recover`` returns (#233).
#:
#: IT NAMES THE THREE WAYS, NOT WHICH ONE. Since #283 a target can be out of
#: tonight by its gating as well as by a set-aside record, and a window that
#: closed at dawn or a floor never cleared from this site is a fact computed
#: from the site. A sentence that listed each target's own reason would say
#: that fact beside a target and a time; this one says only what the refusal
#: itself already says, that something ruled the night out.
NOTHING_TONIGHT = ("none of this session's remaining frames can be captured "
                   "tonight: the remaining targets are set aside for tonight, "
                   "past their observing windows, or never above their minimum "
                   "start altitude; waiting until the next night before slewing")

#: The gating states the run drops a target for without shooting it
#: (``_run_scheduled``), and so the re-centre leaves out (#283).
_DROPPED_STATES = ("window_closed", "never_rises")


def _owes(target: Target, remaining: dict[str, int]) -> bool:
    """Does ``target`` still owe a frame, by ``Session.remaining``? A target
    with no steps owes nothing."""
    return any(remaining.get(s.id, 0) > 0 for s in target.steps)


def standing_set_asides(session: Session, night: str,
                        now: float | None) -> list[dict]:
    """``night``'s set-aside records that still stand at ``now``: those
    ``Session.set_aside_on`` reads, less every centring set-aside whose 45
    minutes have passed (#534, H4 orchestrator ruling 2, amended by backlog
    WP-07 #564 2026-09-30: time only).

    A CRASH-RESUME IS NOT HELD ALL NIGHT BY A SET-ASIDE THAT HAS EXPIRED. A
    centring set-aside expires once a night, when ``SET_ASIDE_EXPIRY_S`` have
    passed since it was made (``group_rules.set_aside_expiry``), and the run
    marks the record when it sees it expire. A run that died first never
    marks it, and read as it stands the record would keep the panel out of
    every re-centre, and a session whose only work it was out of every start
    (``NOTHING_TONIGHT``), for the rest of the night. The rule needs no
    ephemeris, so it is applied here exactly as the run applies it; the two
    always agree, which a ruling that read a site-derived altitude could not
    have promised. The run, started, reads the same record and expires it
    at its first selection by the same rule.

    Only a whole panel's record of kind ``"centring"``, with the clock time
    it was made, and only while the panel has not expired tonight already
    (``Session.set_aside_expiries_on``): at most one expiry per panel per
    night. A record without a kind or a ``ts``, as every record before H4
    is, never expires. ``now`` None applies nothing (the answer before)."""
    records = session.set_aside_on(night)
    if now is None:
        return records
    counts = getattr(session, "set_aside_expiries_on", None)
    expiries = counts(night) if counts is not None else {}

    def expired(rec: dict) -> bool:
        ts = rec.get("ts")
        if (rec.get("step_id") is not None or rec.get("kind") != CENTRING
                or isinstance(ts, bool) or not isinstance(ts, (int, float))
                or not math.isfinite(ts)):
            return False
        return set_aside_expiry(
            now=now, set_at=float(ts),
            expiries=int(expiries.get(rec.get("target_id"), 0))) == "time"

    return [r for r in records if not expired(r)]


def _set_aside_tonight(target: Target, remaining: dict[str, int],
                       records: list[dict]) -> bool:
    """Is ``target`` set aside for the night ``records`` belong to?

    TWO KINDS OF RECORD (spec 3.4). The group driver sets a panel aside
    whole (``step_id`` None), and the reject guard sets one step aside. A
    target with a whole record has nothing to shoot tonight, and so does one
    whose every owed step has a step record: the run skips each of those
    steps tonight, and nothing of the target is left for it. A target with
    one of its owed steps set aside still owes the others tonight."""
    whole = {r.get("target_id") for r in records if r.get("step_id") is None}
    if target.id in whole:
        return True
    steps = {r.get("step_id") for r in records
             if r.get("target_id") == target.id
             and r.get("step_id") is not None}
    owed = [s.id for s in target.steps if remaining.get(s.id, 0) > 0]
    return bool(owed) and all(sid in steps for sid in owed)


def _group_order(group: TargetGroup, members: list[Target],
                 live: list[Target], done: dict[str, int],
                 last_ts: dict[str, float]) -> list[Target]:
    """``live``, the group's panels the run can still shoot tonight, in the
    order ``panel_order.order_panels`` gives them from the ledger (spec 5.2,
    5.9). ``members`` is every panel of the group, complete ones included,
    because the panel visited last may have completed on that visit and the
    grid order still resumes after its place.

    THE SNAPSHOT IS THE LEDGER'S (5.2): the fraction of each panel's owed
    frames banked, in the plan's count mode (``done`` is ``done_map``), and
    each panel's newest frame as its last visit. ``time_to_floor_s`` is left
    empty, which ``order_panels`` reads as "does not set tonight", so under
    ``setting_first`` the ladder orders by the fraction and then the snake.
    That is the one place its order can differ from the run's, until the
    scheduler's own snapshot builder can be shared.

    THE COLUMN COUNT is one more than the widest column of ANY member, the
    complete ones included, never the live panels' alone (``order_panels``
    says why). ``geometry["cols"]`` is not read: it is provenance (3.4), and
    the snake order does not depend on the count anyway, for any count wider
    than every panel. Rows never interleave (row r's indexes all sit below
    row r+1's), and within a row the order is by column, ascending or
    descending, whatever the width. So a narrower count than the layout's,
    when a whole column was skipped, gives the same order, and a stale
    geometry cannot make a panel "outside the grid".

    A GROUP THAT CANNOT BE ORDERED IS TAKEN IN PLAN ORDER, with a warning. A
    panel with no grid position makes ``order_panels`` raise, and raised
    here it would end every tick ("resume-arm tick failed") a minute apart
    for the rest of the night."""
    fraction: dict[str, float] = {}
    for m in live:
        owed = sum(s.count for s in m.steps)
        banked = sum(done.get(f"{m.id}:{s.id}", 0) for s in m.steps)
        fraction[m.id] = banked / owed if owed else 1.0
    visited = {m.id: last_ts[m.id] for m in live if m.id in last_ts}
    placed = [m for m in members if m.id in last_ts
              and m.panel_row is not None and m.panel_col is not None]
    last = max(placed, key=lambda m: last_ts[m.id], default=None)
    snapshot = OrderSnapshot(
        fraction_banked=fraction, last_visit_ts=visited,
        last_visited=None if last is None else (last.panel_row,
                                                last.panel_col))
    cols = 1 + max((m.panel_col for m in members
                    if isinstance(m.panel_col, int)), default=0)
    try:
        return order_panels(live, cols=cols, policy=group.order,
                            snapshot=snapshot)
    except ValueError as e:
        bus.log("warning", f"auto-resume: the panel order of "
                           f"'{group.name or group.id}' could not be worked "
                           f"out ({e}), so it re-centres on its panels in "
                           f"plan order", "sequence")
        return list(live)


def _past_meridian_first(panels: list[Target], site,
                         now: float) -> list[Target]:
    """``panels``, a flipped group's live panels in the run's order, with the
    ones past the meridian first and each part in its own order (#312, spec
    5.7, 5.9).

    PAST THE MERIDIAN AS THE RUN COUNTS IT: at least ``MERIDIAN_SIDE_MARGIN_S``
    past the crossing (``schedule.hours_to_meridian_flip`` at or below minus
    that band), where the side a goto lands on is not in doubt. A panel just
    past its crossing waits in the run too
    (`group_rules.meridian_eligibility`).

    WHY. The run reads tonight's record back and shoots only the flipped
    group's panels past the meridian; a panel before it waits for its
    crossing. Re-centred on that panel, the ladder's goto would land the
    mount on the side the group left, and the run's first hop would then
    cross the pier again: two slews across it for no frame. Ordered, not
    filtered: with no panel past the meridian yet, the run waits for the
    first crossing, and the ladder still has a panel to re-centre on.

    SITE-DERIVED and used here only, never said: the answer is an order.
    Called only once ``recentre_candidates`` has found the site set (its
    ``gate``, the one guard, as for the run's gating); a longitude or an
    hour angle that cannot be read leaves the order as it is."""
    from ..site_gate import site_get
    from .engine import MERIDIAN_SIDE_MARGIN_S
    band_h = MERIDIAN_SIDE_MARGIN_S / 3600.0
    try:
        lon = float(site_get(site)("longitude"))
    except (TypeError, ValueError):
        return list(panels)

    def past(t: Target) -> bool:
        try:
            return schedule.hours_to_meridian_flip(t.ra_hours, lon,
                                                   now) <= -band_h
        except (TypeError, ValueError):
            return False

    marks = [past(t) for t in panels]
    return ([t for t, p in zip(panels, marks) if p]
            + [t for t, p in zip(panels, marks) if not p])


def _gating_state(target: Target, site, twilight_deg: float,
                  now: float) -> str:
    """``schedule.gating_status``'s state for ``target`` at ``now``, the
    window resolved at ``now`` as the run resolves and freezes it at its
    start, which follows the ladder within moments.

    NO LONGER GUARDED (#611, the #543 class). This used to wrap the call in
    ``except (KeyError, TypeError, ValueError): return "ready"`` for a site
    ``gating_status`` could not read, the same fallback ``ResumeArm._walk``
    made for the same errors before #543 removed it. Both ``gating_status``
    and its ``constraint_gate`` helper (``schedule.py``) now read coordinates
    through ``site_gate.site_lat_lon``, which answers ``None`` instead of
    raising, so there is no longer a site that could reach this except: a
    verifier ran the 16 test files that call ``_gating_state`` or
    ``recentre_candidates`` (the full pool #543's own verifier used for
    ``_walk``, plus every file calling either function directly), 366 tests,
    with the except narrowed to ``except ZeroDivisionError`` on a byte
    backup — all 366 still passed, through ``site_lat_lon``'s ``None``
    instead of the catch. Removed rather than kept defensive, because a
    branch no test can reach is a branch nobody will notice rot."""
    return schedule.gating_status(target, site, twilight_deg, now)["state"]


def recentre_candidates(session: Session, night: str,
                        walk: list[Target] | None = None, *,
                        site=None, twilight_deg: float = -12.0,
                        now: float | None = None) -> list[Target]:
    """The light targets the run can still shoot on the night ``night``
    (an ``events.night_key``), in the order the run takes them: what the
    recovery ladder may re-centre on (#159, spec 5.9).

    * A target is a candidate while it owes frames and is not set aside for
      ``night`` (``Session.set_aside``, spec 3.4). A complete target is
      never shot again, and a set-aside one is not retried until another
      night, save a centring set-aside whose 45 minutes have passed at
      ``now`` (``standing_set_asides``, #534). Calibration never slews.
    * THE RUN'S WALK. ``walk`` is the plan's targets in the order the run
      walks them, ``schedule.schedule_order`` (``ResumeArm._walk``), and
      plan order when it is not given. Each ``plan.groups`` entry stands at
      its first member's place in it, and its live panels come in the order
      ``_group_order`` gives. A ``mosaic_group`` naming no group is a
      Plan-UI mosaic, whose panels are ordinary targets (3.4: it keeps
      today's panel-first behaviour).
    * A target that waits for a group (``after_group``) is left out while the
      group owes frames: the group is live (the target waits) or set aside
      tonight (the target is skipped with it), and either way the run does
      not shoot it tonight until the group is complete (spec 1.6). A GROUP
      whose member waits for another group is left out whole under the same
      rule (#330): a member's gate holds its whole group, in the run as here.

    * THE RUN'S GATING (#283), when ``site`` is set (``site_is_set``) and
      ``now`` is given: each live candidate is asked
      ``schedule.gating_status`` at ``now``, the question ``_run_scheduled``
      asks of every target before it shoots it. A ``window_closed`` or
      ``never_rises`` candidate is left out, as the run drops it unshot, and
      READY candidates come before WAITING ones, whatever they wait on (the
      clock, their altitude, a moon or hour-angle constraint), each part in
      the order above, as the run takes the first ready target in its order.
      Without this, a session whose one owing target's window had closed,
      beside a complete target whose window was open, passed ``window_open``
      (it asks ANY target's window), was re-centred on the closed target and
      started, and the run dropped it and ended: every minute until the
      window or the night closed.
    * A GROUP THAT CHANGED PIER SIDE TONIGHT (#312, S3 orchestrator ruling
      4), when the site is set and ``now`` is given: its record for
      ``night`` (``Session.group_pier_on``) says it is flipped, so the run,
      which reads the same record back, shoots only its panels past the
      meridian, and a panel before the meridian waits for its crossing. Its
      panels past the meridian come first, each part in the panel order
      (`_past_meridian_first`), so the re-centre lands on the side the group
      is on instead of taking it back across the pier before the run starts.
    * AN UNSET SITE GATES NOTHING (#121). A window resolved at the 0,0
      default is a window somewhere else, and refusing on it would strand
      every resume on a rig whose site is not configured; the run's own
      checks still apply once it starts. The ladder's own start-floor check
      makes the same tri-state choice. Nor does it order by the meridian: an
      hour angle at the 0,0 default is somewhere else's too.

    What this still does not model: the run's placement of a group's
    followers behind its panels (#283's comment). (The ladder used to
    command a locked angle to a disconnected rotator too, #295; fixed in
    ``commanded_rotation``, which checks the rig it is handed.)

    Pure: it reads the session, the walk, the site and the clock it is
    handed and nothing else, and it takes the night key as an argument so
    the answer depends on the caller's clock. Called without ``site`` and
    ``now`` it applies no gating, the answer before #283."""
    from ..site_gate import site_is_set
    gate = site is not None and now is not None and site_is_set(site)
    states: dict[str, str] = {}

    def state(t: Target) -> str:
        if not gate:
            return "ready"
        if t.id not in states:
            states[t.id] = _gating_state(t, site, twilight_deg, now)
        return states[t.id]

    plan = session.plan
    remaining = session.remaining()
    records = standing_set_asides(session, night, now)
    groups = {g.id: g for g in plan.groups}
    owing_groups = {t.mosaic_group for t in plan.targets
                    if t.mosaic_group in groups and _owes(t, remaining)}
    done = session.done_map()
    last_ts: dict[str, float] = {}
    for f in session.frames:
        if f.ts > last_ts.get(f.target_id, float("-inf")):
            last_ts[f.target_id] = f.ts

    def live(t: Target) -> bool:
        # The gating is asked last, so only a target that owes frames and
        # is not set aside costs a window resolution and an altitude scan.
        return (not t.calibration and _owes(t, remaining)
                and not _set_aside_tonight(t, remaining, records)
                and state(t) not in _DROPPED_STATES)

    pier_on = getattr(session, "group_pier_on", None)

    def flipped_tonight(group: TargetGroup) -> bool:
        rec = pier_on(group.id, night) if pier_on is not None else None
        return bool(rec and rec.get("flipped"))

    out: list[Target] = []
    placed: set[str] = set()
    for t in (walk if walk is not None else plan.targets):
        group = groups.get(t.mosaic_group) if t.mosaic_group else None
        if group is not None:
            if group.id not in placed:
                placed.add(group.id)
                members = [m for m in plan.targets
                           if m.mosaic_group == group.id]
                # A MEMBER'S GATE HOLDS ITS WHOLE GROUP (#330), as the run
                # reads it (`SequenceEngine._group_gate`): while a group any
                # of its members waits for owes frames, the run holds this
                # group or skips it for the night, and shoots none of its
                # panels, so none is re-centred. A gate on the group itself
                # is no gate (the start paths refuse it). Asked before the
                # panels are placed, as a follower's gate is asked below.
                waits_for = {m.after_group for m in members
                             if m.after_group is not None
                             and m.after_group != group.id}
                if waits_for & owing_groups:
                    continue
                ordered = _group_order(group, members,
                                       [m for m in members if live(m)],
                                       done, last_ts)
                if gate and flipped_tonight(group):
                    ordered = _past_meridian_first(ordered, site, now)
                out.extend(ordered)
            continue
        if live(t) and t.after_group not in owing_groups:
            out.append(t)
    # A GROUP THAT OWES STILL HOLDS ITS ``after_group`` TARGETS when the
    # gating has dropped every live panel: ``_follower_gate`` skips such a
    # target for the night while its group can shoot nothing more tonight
    # and is not complete, so the run does not shoot it either.
    if gate:
        # Stable, so each part keeps the walk's order and a group's panels
        # keep the panel order within it.
        out = ([t for t in out if state(t) == "ready"]
               + [t for t in out if state(t) != "ready"])
    return out


def nothing_to_shoot_tonight(session: Session,
                             candidates: list[Target]) -> bool:
    """True when the run would shoot nothing tonight although the session
    still owes light frames: no candidate (``recentre_candidates``), a light
    target that owes frames, and no calibration owed. Nothing this session
    still owes can be shot tonight: each owing light target is set aside
    for tonight, waits on a group that is, or, with the site set, has a
    window that closed or never clears its start floor tonight (#283).

    WHY THIS REFUSES (#159, #283). A run does not retry what is set aside
    for its night (spec 3.4) and drops a target whose window has closed, so
    started, such a run has nothing to shoot: it ends, the session stays
    dormant and armed, and the next tick, a minute later, would run the
    ladder and start it again, all night. Refused, it holds in words
    (``NOTHING_TONIGHT``, no numbers, #233) on the ten-minute retry, said
    once a night (#284, ``ResumeArm.tick``), and the next night's key reads
    none of tonight's records and resolves its own windows. A session that
    owes no light frame at all is not this case: calibration-only work
    starts, and so does a session that owes nothing, which the run then
    completes."""
    if candidates:
        return False
    remaining = session.remaining()
    light = any(not t.calibration and _owes(t, remaining)
                for t in session.plan.targets)
    calibration = any(t.calibration and _owes(t, remaining)
                      for t in session.plan.targets)
    return light and not calibration


def commanded_rotation(session: Session, target: Target,
                       hub=None) -> float | None:
    """The angle a re-centre on ``target`` commands, or None for none
    (Revision 2, ruling 9: "the rotator is set explicitly at the start of
    every run").

    THE PLANNED ANGLE FIRST. A framed target carries its ``rotation_deg``,
    and a group's member carries its group's PA there. It is commanded
    whatever the rig, as it always was: the hub says when no rotator
    answered, and that is the operator's own request going unmet.

    THE LOCKED ANGLE IS COMMANDED ONLY TO A CONNECTED ROTATOR (#295). An
    unframed target whose first imaging solve locked its angle
    (``Session.locked_angles``) is re-centred at that angle, so a resumed
    night stacks with the nights before it -- but only when ``hub`` names a
    connected rotator, the same check ``SequenceEngine._commanded_rotation``
    makes before it commands a lock. With no rotator there is nothing to
    turn, and commanding one anyway had the hub warn on every auto-resume
    re-centre of an unframed target that a rotation was asked for, naming an
    angle the operator never set, while the engine's own setup of the same
    target said nothing (or gave the real camera-angle check). With neither
    a planned angle nor a usable, commandable lock, None, and the re-centre
    call is exactly today's.

    ``hub=None`` SKIPS THE ROTATOR CHECK, for a caller with no hub to ask
    (a direct test of the lock arithmetic). Every real caller — the re-centre
    in ``_recover`` — passes one.

    0 IS AN ANGLE (north up), so every test here is ``is None``.

    A LOCK THAT IS NOT A FINITE NUMBER COMMANDS NOTHING. ``lock_angle``
    refuses one, but the session is a JSON file and Python's JSON reads NaN
    and Infinity: handed on, a NaN would reach the rotate loop, where every
    comparison it makes is false.

    NO ANGLE ONCE ROTATION IS OFF FOR THE NIGHT (D-05, backlog ruling,
    owner-approved 2026-09-30; #648). When the hub has MEASURED the camera
    not following the rotator (``_rotation_trusted`` False, from the nightly
    self-test) it refuses every ``rotate_to_pa``, so the engine commands no
    angle (`SequenceEngine._rotation_off_tonight`) and this answers None for
    the same reason: asked, the re-centre would provoke a refused rotate
    and a warning in a recovery that is racing the dawn, and the frames are
    shot at a fixed angle either way. Planned angle and lock alike. Only a
    measured failure counts (None, never measured, commands as before), and
    a caller with no hub has no verdict to read."""
    if hub is not None and getattr(hub, "_rotation_trusted", None) is False:
        return None
    if target.rotation_deg is not None:
        return target.rotation_deg
    lock = session.locked_angle(target.id)
    if not isinstance(lock, dict):
        return None
    pa = lock.get("pa_deg")
    if isinstance(pa, bool) or not isinstance(pa, (int, float)):
        return None
    if not math.isfinite(pa):
        return None
    if hub is not None:
        rotator = hub.devices.get("rotator")
        if rotator is None or not getattr(rotator, "connected", False):
            return None
    return float(pa)


def _unsuperseded_stalled(sessions: list[Session]) -> list[Session]:
    """``sessions``, narrowed to dormant, auto-resume-off ones that a NEWER
    session with the same origin has NOT already superseded (#139).

    The 2026-09-23 incident: a session that had just completed (105 frames)
    shared its name AND its ``origin_id`` with a stale dormant session from
    three nights earlier (18 frames). `tick`'s stalled-session note named the
    newer one's DOUBLE by display name alone and told the operator to arm
    it -- which would have re-shot a plan a later run already finished.

    A stalled session is superseded by ANY other session of the same
    ``origin_id`` with a later ``updated_ts``, WHATEVER that other session's
    own status: a completed run supersedes the dormant leftover it replaced
    just as surely as another dormant one would. An empty ``origin_id``
    ("unknown origin") never matches another empty one -- two unrelated
    sessions that both fail to record an origin are not the same flow."""
    stalled = [s for s in sessions
              if s.status == "dormant" and not s.auto_resume]
    return [s for s in stalled if not (s.origin_id and any(
        o.id != s.id and o.origin_id == s.origin_id
        and o.updated_ts > s.updated_ts for o in sessions))]


class _LadderSweep(NamedTuple):
    """The recovery ladder's last successful autofocus (#402), as the
    ladder keeps it: made on the night ``night`` (``events.night_key`` of
    the ladder's clock) at ``at`` on that clock, leaving the drawtube at
    ``position`` with the focuser reading ``temp_c``, its frames binned
    ``binning``. Held on the ResumeArm only, never written (see
    ``_recovery_sweep``).

    NOT KEYED TO A SESSION. A sweep is a fact about the focuser, and the
    session the ladder happened to be recovering has no say in where the
    drawtube is: a record handed to a start of another session is the
    same focus."""

    night: str
    at: float
    position: int | None
    temp_c: float | None
    binning: int


class ResumeArm:
    def __init__(self, engine, hub, *, clock=None, weather=None):
        self.engine = engine
        self.hub = hub
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # never reached it -- and production builds this WITHOUT a clock
        # (api/app.py:147-185). A simulated night would tick this hundreds of
        # times at one frozen instant with every assertion green.
        self._clock = clock or (lambda: time.time())
        # sub-project C (weather spec §4): injected WeatherService (like clock,
        # so tests inject fakes). None = no weather gate (back-compat).
        self._weather = weather
        self._task: asyncio.Task | None = None
        self._retry_at: float = 0.0        # refusal backoff: no attempt before this
        self._gave_up_for: str | None = None   # session id we give-up-alerted on
        #: session id we have already said "dormant but not armed" about, so the
        #: notice appears once per session rather than once per minute.
        self._quiet_note_for: str | None = None
        #: session id we have already stowed the rig for. The disarm below is
        #: the real latch - it is what stops the next tick reaching the
        #: give-up branch at all - and this is the belt to its braces, so a
        #: session whose disarm failed to save cannot park the mount once a
        #: minute for the rest of the night.
        self._stowed_for: str | None = None
        #: WHY NOTHING IS HAPPENING, for anything that wants to say so.
        #:
        #: Every refusal below was formatted into a log line and dropped. The
        #: log ring holds ~40 minutes of a ten-hour night, and the standing-by
        #: branch latches per session and logs EXACTLY ONCE - so scraping the
        #: ring for it works by luck on a weather veto that re-logs every ten
        #: minutes, and not at all on the more common hold. Monitor therefore
        #: said "No run active - plan a session" over a session that was armed
        #: and waiting, which is an instruction to do the one thing that
        #: strands it (a fresh start disarms every other session).
        #:
        #: ``None`` = not holding. Otherwise {reason, since, retry_at,
        #: session_id, session_name, owed}, and ``site_detail`` as well when
        #: the refusal has site-derived numbers behind its words (#233): the
        #: reason is what anyone may read, ``site_detail`` is withheld from a
        #: viewer by ``api/redact.py``'s ``_redact_resume_arm_for``.
        #:
        #: THE BACKING FIELD FOR THE ``hold`` PROPERTY (#261), not read
        #: directly outside it: ``_set_hold`` and ``_clear_hold`` write it,
        #: and it keeps the last refusal even while a new ladder runs, so a
        #: refusal the new attempt repeats still keeps the first one's
        #: ``since``. See ``hold``.
        self._hold: dict | None = None
        #: True while ``_recover`` runs. Written only by ``tick``; read
        #: through ``recovering``.
        self._recovering = False
        #: The target this tick's ladder left the mount tracking, or None
        #: (calibration-only, or no ladder yet). Cleared by ``tick`` before
        #: each ladder, set by ``_recover`` once its slew succeeds, and read
        #: by ``_tracking_for`` to hand the engine's idle clock (#202).
        self._recentred: Target | None = None
        #: THE LADDER WHILE IT RUNS, and what an operator needs to stop it
        #: (#220). ``tick`` sets the first three as the ladder begins, only
        #: ``stop_recovery`` sets the fourth, and ``tick``'s ``finally``
        #: clears all four. ``_ladder`` is the ladder's own task,
        #: so a stop can cancel the step it is awaiting; ``_ladder_session``
        #: is the session it is recovering, as read before it;
        #: ``_ladder_step`` is one of ``LADDER_STEPS``; ``_stop_why`` is the
        #: reason a stop was asked for, or None, and it is what the ladder
        #: reads between steps.
        self._ladder: asyncio.Task | None = None
        self._ladder_session: Session | None = None
        self._ladder_step: str | None = None
        self._stop_why: str | None = None
        #: Resolved when ``tick`` has left the ladder, in the same ``finally``
        #: that lowers ``recovering``; None while no ladder runs. What
        #: ``wait_stopped`` waits on (#238). A future made per ladder, on
        #: the running loop, rather than an Event made here: this object is
        #: built at import (api/app.py), before any loop exists.
        self._ladder_left: asyncio.Future | None = None
        #: The numbers behind the words-only refusal ``_recover`` last
        #: returned, or None (#233). Set by ``_recover`` beside the reason it
        #: returns, cleared by ``tick`` before each ladder, and handed to
        #: ``_set_hold`` as ``site_detail``. Kept off the return value so
        #: ``_recover`` still answers ``str | None``, which the suite's spies
        #: of it return.
        self._refusal_site_detail: str | None = None
        #: What this tick's recovery solve showed about light (#251): "dark"
        #: for a ``NoLightError``, "lit" for a solve that worked or a cloud
        #: verdict, None when the solve did not run or nothing could judge its
        #: frame. Set by ``_recover`` beside the solve, cleared by ``tick``
        #: before each ladder, for the same reason ``_refusal_site_detail``
        #: is: kept off ``_recover``'s ``str | None`` answer.
        self._ladder_light: str | None = None
        #: The session whose no-light spell is open, i.e. the one that has
        #: had its alert (#251). The latch that makes it ONE alert per
        #: session per spell, and what tells the first retry of a spell (ten
        #: minutes) from the later ones (hourly, on a dark master's verdict,
        #: #308). Cleared when a solve shows light, when nothing is armed, and
        #: when the window closes.
        self._no_light_spell: str | None = None
        #: The ``reference_kind`` of this tick's no-light verdict (#308), set
        #: by ``_recover`` beside ``_ladder_light = "dark"`` and cleared by
        #: ``tick`` before each ladder, for ``_ladder_light``'s reason.
        self._ladder_dark_kind: str | None = None
        #: True when this tick's ``_recover`` refused ``NOTHING_TONIGHT``
        #: (#284). Set there, cleared by ``tick`` before each ladder, and
        #: kept off ``_recover``'s ``str | None`` answer for the same reason.
        self._ladder_nothing_tonight = False
        #: ``(session id, night key)`` whose ``NOTHING_TONIGHT`` refusal has
        #: been said (#284): the latch that makes it one warning a night and
        #: quiet re-checks after. Cleared when nothing is armed and on a
        #: start, so a re-arm or a changed night is heard about again.
        self._nothing_tonight_said: tuple[str, str] | None = None
        #: The warning for this tick's software-fault hold (#866), or None.
        #: Set by ``_recover``, cleared by ``tick`` before each ladder, and
        #: said by ``tick`` once a night per session (``_software_fault_said``).
        self._ladder_software_fault: str | None = None
        #: ``(session id, night key)`` whose software-fault hold has been said
        #: (#866), the #284 latch: one warning pair a night, quiet retries
        #: after. Cleared where ``_nothing_tonight_said`` is (nothing armed; a
        #: start), so a re-arm or a new night hears it again.
        self._software_fault_said: tuple[str, str] | None = None
        #: The session whose CURRENT hold is the ``NOTHING_TONIGHT`` refusal,
        #: or None (#284). Kept by ``_set_hold`` and ``_clear_hold``, so it
        #: always answers for the last refusal, which is what the window's
        #: close reads to choose its line.
        self._held_nothing_tonight: str | None = None
        #: THE LADDER'S LAST GOOD SWEEP, IN PROCESS MEMORY AND NOWHERE ELSE
        #: (#402). ``_recover`` sets it when its autofocus succeeds, clears
        #: it when one fails, and drops it at its focus step once the
        #: focuser no longer reads the position the sweep left; ``tick``
        #: hands it to the next start it makes on the night it was made, as
        #: ``engine.start``'s ``focus_sweep`` (``_sweep_for_start``), and
        #: clears it once a start has taken it. Kept across ticks on
        #: purpose: a ladder that sweeps and then refuses at the solve does
        #: not sweep again ten minutes later (the fingerprint vouches for the
        #: position), and the run the later ladder starts stands on this
        #: sweep. Not on disk, because a sweep is tonight's fact about this
        #: focuser: a later night or another process has no business finding
        #: it (learned facts belong in process memory).
        self._recovery_sweep: _LadderSweep | None = None
        #: The sessions already told that auto-resume stays off because a
        #: DUSK window cannot be placed with no site saved (#527): one line
        #: per session, not one per minute (`_dusk_without_a_site`).
        self._dusk_no_site_said: set[str] = set()

    @property
    def recovering(self) -> bool:
        """True from just before the recovery ladder's first await until it
        returns or raises.

        FOR THE START ROUTES (#211). The ladder blind-solves and re-centres
        the mount for minutes, and a manual start of any session in that time
        puts a run on a rig the ladder is still moving. A route that reads
        this and ``engine.running`` and starts, all with no await between,
        cannot land inside a ladder: ``tick`` raises the flag in the same
        synchronous stretch as its own ``engine.running`` check, so the route
        runs either wholly before that check (and the tick then finds the
        engine running) or wholly after the flag went up.

        Read-only, because only this service can say whether it is
        recovering: a writable flag is one a caller could leave raised, and
        every start that consults it would be refused until the restart.
        """
        return self._recovering

    @property
    def hold(self) -> dict | None:
        """The service's own CURRENT refusal, or None (see ``__init__`` for
        the published shape).

        NONE WHILE THE LADDER IS RECOVERING (#261). ``_hold`` keeps the last
        refusal across a ladder's run so a repeat of it keeps the first
        one's ``since`` (``_set_hold``), but a hold set by an EARLIER attempt
        is not the service's current refusal once a new attempt is under
        way: that attempt may well succeed, and the hold said nothing had
        changed. Masking it here, rather than clearing ``_hold`` when the
        ladder starts, is what lets ``_set_hold`` still see the earlier
        refusal if the new attempt repeats it.

        Before this, every reader of the hold - the classic header, the
        #/next banners, an API script - said "holding: <reason> ... starts
        by itself when that clears" for the whole minutes a ladder spent
        re-centring the mount for that same session, beside `recovering:
        true`. It self-corrected once the ladder returned, which made it
        easy to miss: nothing stayed wrong, the two fields just disagreed
        for the minutes that mattered most."""
        if self._recovering:
            return None
        return self._hold

    @property
    def recovery(self) -> dict | None:
        """What the ladder is doing, for ``GET /api/sequence/resume-arm``:
        ``{step, session_id, session_name}`` while it runs, else None.

        THE 409 POINTS HERE (#220). A start refused ``resume_recovering``
        used to tell the operator to wait for something no route showed, so
        there was no way to tell a ladder that was solving from one that had
        wedged. ``step`` is a word from ``LADDER_STEPS`` and nothing else
        about the step is reported, because the route is CAP_VIEW_STATUS
        (see ``LADDER_STEPS``)."""
        s = self._ladder_session
        if not self._recovering or s is None:
            return None
        return {"step": self._ladder_step, "session_id": s.id,
                "session_name": s.name}

    def stop_recovery(self, why: str, *, session_id: str | None = None,
                      disarm: bool = False) -> str | None:
        """Stop the recovery ladder before its next step, and cancel the
        step it is awaiting (#220). Returns the id of the session whose
        ladder was stopped, or None when there was nothing to stop: no ladder
        running, or ``session_id`` given and the ladder recovering another
        session.

        ``why`` finishes the stand-down line ``tick`` logs ("auto-resume
        stood down for '<name>': <why>"). ``disarm`` also disarms the
        session, for Abort (below).

        WHO CALLS THIS. ``POST /api/sequence/abort``, with ``disarm``, for
        any session: an abort stops whatever is moving the rig. ``PATCH
        /api/sessions/{id}`` when it disarms or abandons the session being
        recovered, or arms another one (the singleton disarms this one), and
        ``DELETE /api/sessions/{id}`` of it, each with ``session_id``, so
        withdrawing a DIFFERENT session leaves this ladder alone. And every
        route that tears the rig down (#238): ``POST /api/disconnect``, and
        a forced profile apply, profile activate or ``/api/connect/rig``,
        each with ``disarm`` as Abort, each then awaiting ``wait_stopped``
        before it touches a device. Before
        #220 none of them reached the ladder: an abort had no run to abort,
        so the ladder slewed on and started the session seconds later, and a
        disarm was read only after the ladder, by ``_still_startable``, so
        the mount was re-centred for a session nobody wanted any more.

        TWO MECHANISMS, because either alone leaves a gap. The flag is read
        at the ladder's between-step points (``_must_stop``, the same points
        ``_a_run_took_over`` guards), which stops a step that runs to its end
        whatever its caller does. The cancel cuts short the await the ladder
        is in, so an operator does not wait out a twelve-second solve, an
        autofocus or a slew before the ladder notices.

        A GOTO ALREADY COMMANDED MAY RUN TO ITS END ON THE MOUNT. Cancelling
        cancels the coroutine awaiting ``goto_and_center``; nothing in the
        Telescope contract says that stops the slew, because the contract
        has no abort_slew (``Telescope.stop`` zeroes ``move_axis`` motion,
        which a goto is not). Some drivers do halt on the cancel - the AM5's
        ``slew`` sends :Q# and Alpaca's sends abortslew - but the ladder is
        written for the contract, not for the drivers it has met: with any
        other driver the mount finishes the slew it was sent and tracks
        there. The ladder has returned by then, so ``recovering`` is down
        and a start is no longer refused, and nothing further moves the
        mount on the ladder's behalf.

        NOT A REFUSAL AND NOT A CRASH. ``tick`` stands the attempt down with
        one info line: no backoff (the operator's decision is not a fault to
        wait out) and no crash counted, and no hold is left on the Monitor.

        ABORT DISARMS, as Abort disarms a running session (spec 6.15,
        ``SequenceEngine._finalize_report``). Without it the next tick, 60 s
        later, would find the session still dormant and armed and run the
        ladder again, and the operator's abort would have bought one minute.
        Written here, synchronously, under the store's write
        lock on a fresh read, so the route's 200 means the file is disarmed
        and a stale copy is never saved over it. PATCH and DELETE make their
        own write and do not ask for this.

        Synchronous on purpose, and so is every caller's path to it: the
        flag is up before the route's next await, so the ladder cannot take
        a step between the operator's request and the stop."""
        s = self._ladder_session
        if not self._recovering or s is None:
            return None
        if session_id is not None and s.id != session_id:
            return None
        if self._stop_why is None:
            self._stop_why = why
        ladder = self._ladder
        if ladder is not None and not ladder.done():
            ladder.cancel()
        if disarm:
            self._disarm_stopped(s)
        return s.id

    async def wait_stopped(self) -> bool:
        """Wait until no recovery ladder is running: True once ``tick`` has
        left the ladder (or none was running), False when
        ``LADDER_STOP_WAIT_S`` passes first (#238).

        FOR THE TEARDOWN ROUTES, after ``stop_recovery``. Asking the ladder
        to stop is not the ladder having stopped: the stop cancels the step
        it is awaiting, and a step can take its time to end on the cancel (a
        driver sending its halt), or swallow the cancel and run to its end,
        and the ladder is still using the camera or the mount until it has
        returned. ``/api/disconnect`` pulled the devices out from under it.
        So a route that tears the rig down calls ``stop_recovery`` and then
        this, and touches nothing unless it answers True.

        Resolves when ``tick``'s ``finally`` lowers ``recovering``, not when
        the ladder's task finishes, which is a turn of the loop earlier: the
        flag the start routes read and the answer here agree.

        Never cancels anything and never raises on the bound: a caller that
        gets False has a ladder that was asked to stop and has not, and
        decides for itself what to refuse."""
        left = self._ladder_left
        if left is None or left.done():
            return not self._recovering
        done, _ = await asyncio.wait({left}, timeout=LADDER_STOP_WAIT_S)
        return left in done

    def _disarm_stopped(self, session: Session) -> None:
        """Disarm the session whose ladder Abort stopped, and say so in the
        words the engine uses when Abort disarms a run. Best-effort: a store
        that cannot be written must not fail the abort, but it is said
        loudly, because an unsaved disarm lets the next tick try again."""
        try:
            with session_store.write_locked():
                fresh = session_store.load(session.id)
                if fresh.auto_resume:
                    fresh.auto_resume = False
                    session_store.save(fresh)
        except (KeyError, SessionUnreadable):
            return                          # nothing left to disarm
        except Exception as e:              # noqa: BLE001 - never fail an abort
            bus.log("error", f"could not disarm '{session.name}' after the "
                             f"abort - auto-resume may start it again on its "
                             f"next tick: {e}", "sequence")
            return
        bus.log("info",
                f"'{fresh.name}': stopped by hand while auto-resume was "
                f"re-centring the mount, so auto-resume is disarmed for it. "
                f"Arm it from the session list to pick it up again.",
                "sequence")

    def _disarm_single_night(self, session: Session, first_night: str,
                             tonight: str) -> None:
        """#195: ``session``'s plan asked for one night (``resume_across_
        nights`` False) and its window has reopened on ``tonight``, which is
        not ``first_night``. Refuse the resume and disarm, the same
        write-locked, re-read-before-write pattern as :meth:`_disarm_stopped`,
        so a concurrent PATCH or CONTINUE cannot be overwritten by a stale
        copy. Best-effort for the same reason: a store that cannot be written
        must not crash the tick, but it is said loudly, because an unsaved
        disarm lets tomorrow's tick try again."""
        try:
            with session_store.write_locked():
                fresh = session_store.load(session.id)
                if fresh.auto_resume:
                    fresh.auto_resume = False
                    session_store.save(fresh)
        except (KeyError, SessionUnreadable):
            return                          # nothing left to disarm
        except Exception as e:              # noqa: BLE001 - never fail a tick
            bus.log("error", f"could not disarm '{session.name}' for its "
                             f"single night - auto-resume may start it on a "
                             f"later night anyway: {e}", "sequence")
            return
        bus.log("info",
                f"auto-resume stays off for '{fresh.name}': this flow asked "
                f"for a single night ({first_night}), and tonight "
                f"({tonight}) is a different one. CONTINUE it by hand to "
                f"shoot the rest.", "sequence")

    def _set_hold(self, session, reason: str, retry_at: float = 0.0,
                  site_detail: str | None = None, *,
                  nothing_tonight: bool = False) -> None:
        """Record the current refusal, preserving ``since`` while the reason
        stands so the UI can say how long it has been waiting.

        ``site_detail`` is the site-derived sentence behind a words-only
        ``reason`` (#233), stored under its own key only when there is one,
        so every other hold keeps the shape it always had. It does not take
        part in ``since``: the altitude in it changes on every retry while
        the words stay, and a floor hold now keeps its ``since`` across the
        retries that used to restamp it.

        ``nothing_tonight`` marks the ``NOTHING_TONIGHT`` refusal (#284).
        Every hold passes through here, so ``_held_nothing_tonight`` is true
        exactly while the latest hold is that one. It is not in the
        published dict, which keeps its shape.

        Reads and writes ``_hold``, the backing field, never the ``hold``
        property: every call here lands while ``_recovering`` is False (the
        property would answer the same either way), and reading through the
        property reads the same as reading the field directly."""
        self._held_nothing_tonight = (getattr(session, "id", None)
                                      if nothing_tonight else None)
        prior = self._hold or {}
        same = prior.get("reason") == reason and prior.get("session_id") == getattr(session, "id", "")
        self._hold = {
            "reason": reason,
            "since": prior.get("since") if same else self._clock(),
            "retry_at": retry_at or None,
            "session_id": getattr(session, "id", ""),
            "session_name": getattr(session, "name", ""),
            "owed": session.owed() if hasattr(session, "owed") else 0,
        }
        if site_detail is not None:
            self._hold["site_detail"] = site_detail

    def _clear_hold(self) -> None:
        self._hold = None
        self._held_nothing_tonight = None

    def _no_light_backoff(self, session, kind: str | None = None) -> float:
        """The wait before the next attempt after a no-light verdict on
        ``session``'s recovery solve, judged against a reference of ``kind``
        (``NoLightError.reference_kind``), sending the spell's one alert when
        this verdict opens the spell (#251).

        THE ALERT IS AN ERROR LINE, because that is the level the alert
        pipeline delivers to a sink left at its defaults: ``bus.log`` becomes
        an alert whose type is its level, and a default ``AlertSink``
        subscribes to "error" and not to "warning". A warning here would be
        one more line in the log nobody read on 2026-09-24.

        ONCE PER SESSION PER SPELL, whatever the kind. The spell is the run
        of no-light verdicts with no evidence of light between them; ``tick``
        ends it on a solve that worked or a cloud verdict, and at the end of
        the night. A failure nothing could judge, or a refusal before the
        solve, is no evidence either way and leaves it open.

        TEN MINUTES, THEN HOURLY, ON A DARK MASTER ONLY (#308, S3
        orchestrator ruling 8): see ``NO_LIGHT_RETRY_S``. A verdict judged
        against a bias master, a self-shot or a reference handed in, or one
        whose kind nobody stated (``kind`` None, the default), keeps the
        ten-minute retry on every verdict, and its alert says so and why.
        The first retry of a spell is ten minutes either way; a later
        verdict takes its own kind's wait, so a spell whose reference turns
        into a dark master as the sensor cools into one's band backs off
        from then on, and one whose dark master drops out of the band
        goes back to ten minutes. So the alert of a spell opened on any
        other kind promises ten minutes only until a dark master gives the
        same verdict: a sensor that cools into a dark master's band mid-spell
        does exactly that, and an alert that said "rather than hourly" was
        then broken by the next retry, without a word."""
        hourly = kind == DARK_MASTER
        if self._no_light_spell == getattr(session, "id", None):
            return NO_LIGHT_RETRY_S if hourly else RETRY_INTERVAL_S
        self._no_light_spell = getattr(session, "id", None)
        if hourly:
            bus.log("error",
                    f"auto-resume for '{session.name}': {NO_LIGHT_WORDS}. Its "
                    f"recovery plate solve read the camera at the level it "
                    f"reads in the dark, so nothing will be imaged until the "
                    f"optic is uncovered. It looks again in "
                    f"{int(RETRY_INTERVAL_S / 60)} min, then every "
                    f"{int(NO_LIGHT_RETRY_S / 60)} min while it stays dark.",
                    "sequence")
        else:
            basis = _NO_LIGHT_BASIS.get(kind, "a reference that is not a "
                                              "dark master")
            bus.log("error",
                    f"auto-resume for '{session.name}': {NO_LIGHT_WORDS}. Its "
                    f"recovery plate solve read the camera at its no-light "
                    f"level as {basis} measures it, so nothing will be "
                    f"imaged until the optic is uncovered. With no dark "
                    f"master for the solve's settings a very dark overcast "
                    f"could read the same, so it looks again every "
                    f"{int(RETRY_INTERVAL_S / 60)} min, and backs off to "
                    f"hourly only once a dark master for those settings "
                    f"gives the same verdict.",
                    "sequence")
        return RETRY_INTERVAL_S

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await reap(self._task)
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 — service must never die
                bus.log("warning", f"resume-arm tick failed: {e}", "sequence")
            await asyncio.sleep(CHECK_INTERVAL_S)

    def resume_veto(self) -> str | None:
        """Veto hook (sessions spec §5 / weather spec §4): delegates to the
        injected WeatherService. Non-None = human-readable reason; the caller
        (tick, :120-125) logs it and arms the 10-min retry latch BEFORE any
        device is touched. No service injected -> no veto (back-compat)."""
        if self._weather is None:
            return None
        return self._weather.veto_reason(self._clock())

    def _window_open(self, session: Session, now: float) -> bool:
        """``window_open`` against the LIVE site and twilight setting.

        Kept as a method because the suite monkeypatches it to drive the tick
        without a sky; the predicate itself is module-level so the end-of-run
        wind-down can ask the same question (see ``resume_expected_tonight``).
        """
        cfg = config_store.cfg()
        twilight = cfg.safety.twilight_deg if cfg else -12.0
        return window_open(session, self.hub.site, twilight, now)

    def _dusk_without_a_site(self, session: Session) -> str:
        """The names of ``session``'s DUSK-windowed targets, when no site is
        saved and every target it would shoot opens on the Sun (#527); ""
        otherwise.

        With no site `schedule.resolve_window` answers None for a dusk or
        dawn boundary (H4-SCHED), where it used to answer 0,0's dusk, and
        `window_open`'s ``start is not None`` then keeps auto-resume off:
        the conservative direction, and nothing said so. The tick's other
        lines would say the wrong thing instead ("it is not dark", "starts
        when the window opens"), about a window that cannot open.

        EVERY target, not any: a session that also holds a ``now`` or
        ``time`` target opens on that one's window, which needs no site, and
        its run then says which DUSK window it did not apply (the engine's
        own line). A calibration target opens the window at any hour, so a
        session holding one never reaches the tick's closed branch."""
        from ..site_gate import site_lat_lon
        if site_lat_lon(self.hub.site) is not None:
            return ""
        lights = [t for t in session.plan.targets if not t.calibration]
        if not lights or any(t.schedule.start_mode not in ("dusk", "dawn")
                             for t in lights):
            return ""
        names = [t.name for t in lights]
        return ", ".join(names[:3]) + (f" and {len(names) - 3} more"
                                       if len(names) > 3 else "")

    def _walk(self, session: Session, now: float) -> list[Target]:
        """The session's targets in the order its run will walk them (#159):
        ``schedule.schedule_order``, the call ``_run_scheduled`` makes at run
        start, against the same live site and twilight, so a target whose
        window opened first comes first and equal windows keep plan order.
        The run starts within moments of the ladder, so ``now`` stands in
        for its start.

        NO LONGER GUARDED (#543). This used to wrap the call in
        ``except (KeyError, TypeError, ValueError): return
        list(session.plan.targets)`` for a site ``schedule_order`` could not
        read (no latitude or longitude at all), walking plan order instead,
        which is what the ladder did before #159. H4-SCHED (#527) moved
        ``resolve_window`` onto ``site_gate.site_lat_lon``, which answers
        ``None`` for such a site rather than raising, and
        ``schedule_order``'s own sort then treats every unresolved start as
        last and stable, which is plan order again by a different route. So
        the except could no longer be reached, and a verifier confirmed it
        across the 62 existing tests whose site doubles used to depend on it
        (issue #543): all 62 still pass, through the sort instead of the
        catch. Removed rather than kept defensive, because a branch no test
        can reach is a branch nobody will notice rot (the class #543 names)."""
        cfg = config_store.cfg()
        twilight = cfg.safety.twilight_deg if cfg else -12.0
        return schedule.schedule_order(session.plan.targets, self.hub.site,
                                       twilight, now)

    async def tick(self) -> None:
        now = self._clock()
        # ON THE LADDER'S OWN CLOCK, not only when a ladder runs: a doubt the
        # last telescope object held moves onto a new one (a profile
        # activate), so the UIs' Trust position button and every plain
        # ``position_known`` reader see it within one tick. No device I/O.
        rig_position_known(self.hub)
        if self.engine.running:
            self._clear_hold()              # a live run is not a hold
            return                          # anything running = no interest
        armed = session_store.armed()
        if armed is None:
            self._retry_at = 0.0            # disarmed from the UI: stop instantly
            self._clear_hold()
            self._gave_up_for = None
            self._no_light_spell = None     # nothing to be dark for (#251)
            # A re-arm is somebody asking the rig to try again, who should
            # hear what it finds, as a no-light spell's re-alert does (#284).
            self._nothing_tonight_said = None
            self._software_fault_said = None    # the same for a fault (#866)
            # SAY SO WHEN THERE IS AN INTERRUPTED RUN NOBODY WILL RESTART.
            #
            # This used to be a bare return, and on 2026-08-11 that cost 25
            # minutes of a clear night and most of an hour of diagnosis. A
            # restart left session 810d46ee dormant with 165 frames and
            # 15 still to shoot; ``armed()`` ANDs status=="dormant" with
            # auto_resume, the second was False, and the tick returned without a
            # word. Nothing on any screen or in any log said the night was over.
            #
            # An unarmed dormant session is a legitimate state — it is what
            # "disarmed from the UI" looks like — so this is not a warning. But
            # it must be VISIBLE, once, or the difference between "deliberately
            # not resuming" and "silently broken" cannot be told apart at 2am.
            #
            # NAMED BY ID/ORIGIN; A SUPERSEDED ONE IS DROPPED (#139, helper).
            stalled = _unsuperseded_stalled(session_store.load_all())
            if stalled:
                newest = max(stalled, key=lambda s: s.updated_ts)
                if self._quiet_note_for != newest.id:
                    self._quiet_note_for = newest.id
                    bus.log("info",
                            f"auto-resume is NOT armed: '{newest.name}' "
                            f"({newest.id}, {newest.origin or 'unknown origin'})"
                            f" is dormant with auto-resume off, so nothing "
                            f"will restart it. Arm it from the session list "
                            f"to resume tonight.", "sequence")
            else:
                self._quiet_note_for = None
            return
        if not self._window_open(armed, now):
            # A NO-LIGHT SPELL NEVER OUTLIVES THE NIGHT (#251). The optic
            # still covered at tomorrow's first solve is news again: the
            # person the alert reached tonight may not be the one looking
            # tomorrow, and the window reopening is already "a fresh night"
            # to this tick (``_gave_up_for`` below).
            self._no_light_spell = None
            # A DUSK WINDOW WITH NO SITE NEVER OPENS (#527), so none of the
            # lines below is true of it: it is not waiting for dark, and it
            # does not start "when the window opens". Said once per session,
            # and held on the reason, before any of them.
            dusk = self._dusk_without_a_site(armed)
            if dusk:
                self._set_hold(armed, "its DUSK window cannot be placed with "
                                      "no site saved, so auto-resume stays "
                                      "off until a site is saved")
                if armed.id not in self._dusk_no_site_said:
                    self._dusk_no_site_said.add(armed.id)
                    bus.log("warning",
                            f"auto-resume stays off for '{armed.name}': the "
                            f"DUSK window of {dusk} needs a saved site to "
                            f"find dusk, and no site is saved, so the window "
                            f"never opens. Save the site, or start the run by "
                            f"hand", "sequence")
                return
            # Read BEFORE the hold below replaces it: was the last refusal
            # "nothing to shoot tonight" (#284)?
            set_aside = self._held_nothing_tonight == armed.id
            # THE MOST COMMON HOLD, and the one the log ring cannot answer for:
            # the branch below latches per session and logs exactly ONCE, so
            # forty minutes later there is nothing left to read. Recorded every
            # tick regardless of whether anything is logged.
            owed = armed.owed()
            self._set_hold(armed,
                           "it is not dark enough yet"
                           + (f", and this session has {owed} frame"
                              f"{'' if owed == 1 else 's'} remaining" if owed else ""))
            if self._retry_at and self._gave_up_for != armed.id and set_aside:
                # NOT A GIVE-UP (#284). The session held because nothing it
                # owes could be shot tonight, which is the run's decision
                # (set aside) or the plan's (a window, a floor), made before
                # the night ended, not a start dawn beat. The urgent error
                # below is for a real loss, and a sink delivers it as one; so
                # this says what happened, once, at info level.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                bus.log("info", f"auto-resume: none of '{armed.name}'s remaining "
                                f"frames could be captured tonight, so its "
                                f"remaining work was set aside for the "
                                f"night. It stays armed and tries again when "
                                f"the next night's window opens.", "sequence")
            elif self._retry_at and self._gave_up_for != armed.id:
                # the window closed while we were mid-backoff: dawn beat us.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                bus.log("error", f"auto-resume gave up for tonight: "
                                 f"'{armed.name}' window closed before a "
                                 "successful start", "sequence")
            elif self._gave_up_for != armed.id:
                # THE RUN DID NOT FINISH AND NOTHING ELSE WOULD SAY SO. The
                # give-up line above only fires mid-backoff — a session vetoed
                # all night by cloud, or one that simply never got its chance,
                # went quiet at dawn with frames still owed and no line
                # anywhere admitting it.
                #
                # Worded to be true at ANY not-dark hour rather than claiming
                # "the night is over": this also fires on an afternoon boot,
                # where it is a useful thing to read (the rig knows it has work
                # pending) and where "the night is over" would be a small lie.
                # Latched per session and cleared when the window opens, so it
                # is at most one line per session per side of the night.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                owed = sum(armed.remaining().values())
                if owed:
                    bus.log("warning",
                            f"auto-resume is standing by: it is not dark, and "
                            f"'{armed.name}' has {owed} frame"
                            f"{'' if owed == 1 else 's'} remaining. It stays armed and "
                            f"starts when the window opens.", "sequence")
                else:
                    bus.log("info",
                            f"auto-resume is standing by: '{armed.name}' has "
                            f"every frame it asked for.", "sequence")
            return
        self._gave_up_for = None            # window open (again): fresh night
        # AUTOMATIC RESUME OFF MEANS ONE NIGHT (#195). `SequencePlan.resume_
        # across_nights` is False for exactly the flows whose DUSK WINDOW has
        # Automatic resume Off (an explicit choice: `repeat`'s own default,
        # "Single night", is not one). A crash or a reboot on the SAME night
        # still resumes here - continuity within a night is a separate
        # promise, and the window reopening because this tick is merely a
        # minute later than the last one is not "a later night". But the
        # window reopening because DAWN CAME AND WENT, and now it is open
        # again, means a night this session never agreed to has arrived, and
        # arming it anyway is the bug the owner's ruling closed: an Off flow
        # that quietly finished itself on the next clear night like a
        # campaign would. This is the NET: the engine already disarms such a
        # session where its night ends (`_finalize_report`), and this check
        # catches the one that slips through, a crash before dawn followed by
        # a restart after it.
        #
        # CHECKED HERE, AHEAD OF EVERY OTHER REFUSAL BELOW, because every
        # refusal below is a RETRY ("try again in 10 minutes") and this one
        # is not: it is permanent for this session, so letting a crash-loop
        # counter, an identity error or a quota refusal run first would leave
        # the session armed and able to resume on some LATER successful tick,
        # on a night it was never supposed to see.
        if not armed.plan.resume_across_nights:
            nights = armed.observing_nights()
            tonight = night_key(now)
            if nights and tonight not in nights:
                self._disarm_single_night(armed, nights[0], tonight)
                return
        # A SESSION THAT KEEPS CRASHING IS NOT A SESSION TO KEEP RESTARTING.
        #
        # Continuity is the right default and it is what the rest of this tick
        # is for: a run that dies should come back and finish the night. But
        # restarting into the same fault forever is not continuity, it is a
        # loop - and the whole time it runs, the mount is tracking, the camera
        # is cold and nothing is being recorded.
        #
        # So after RESUME_GIVE_UP_AFTER consecutive crashes we stop, and we do
        # not merely stop: we put the rig away. Leaving it disarmed and live
        # would trade a crash loop for an idle mount tracking into whatever is
        # east of it until dawn-park notices at -6 degrees, which is the failure
        # this whole ladder exists to avoid.
        #
        # The counter lives on the SESSION, not here, because a crash can take
        # the process with it and a counter in memory would reset on exactly the
        # restart it is counting. It counts crashes only - a veto, a recovery
        # hold or a refusal to start leaves it untouched.
        if armed.crash_resumes >= RESUME_GIVE_UP_AFTER:
            await self._give_up_and_stow(armed)
            return
        if now < self._retry_at:
            return
        # The same identity check as the four HTTP start paths (#156), and for
        # the same reason as the quota refusal below: engine.start is
        # unguarded. HERE, ahead of `_recover`, because the ladder blind-solves
        # and re-centres - it moves the mount - and a plan that cannot start
        # must not cost a slew to find that out. Not a crash: the counter is
        # untouched, and the session stays dormant and armed for a fix.
        identity = plan_identity_errors(armed.plan)
        if identity:
            bus.log("warning", f"auto-resume refused: plan '{armed.name}' "
                               f"cannot start: {'; '.join(identity)} — "
                               f"retrying in {int(RETRY_INTERVAL_S / 60)} min",
                    "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, "this plan cannot start: "
                                  + "; ".join(identity), self._retry_at)
            return
        # HARD REQUIREMENT: engine.start is unguarded here, so refuse an
        # accepted-quota plan that could loop forever (spec §3 / Task 4 review).
        if quota_unbounded(armed.plan,
                           resolve_policy(armed.plan, config_store.cfg())):
            bus.log("warning", f"auto-resume refused: accepted-quota plan "
                               f"'{armed.name}' is unbounded (no reject guard "
                               f"or stop boundary) — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, "this plan counts accepted frames with no "
                                  "reject guard and no stop boundary, so it "
                                  "could run forever", self._retry_at)
            return
        veto = self.resume_veto()
        if veto is not None:
            bus.log("warning", f"auto-resume vetoed: {veto} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, veto, self._retry_at)
            return
        # STILL BOOTING IS NOT A REFUSAL.
        #
        # The boot sequence connects devices asynchronously, and this service's
        # first tick can land in the gap before that finishes. Observed on the
        # rig 2026-08-02: this tick one second after the boot sweep, devices
        # connected two seconds after that — a two-second window in which the recovery
        # ladder's plate solve failed with "no camera connected", was read as a
        # transient inability to verify the sky, and armed the ten-minute
        # backoff. The rig then sat idle for ten minutes with clear sky, a
        # working camera and an armed session, for no reason at all.
        #
        # So: no devices yet means come back on the NEXT 60s tick, with no
        # backoff and no alarming log line. It is not a condition the operator
        # needs to know about; it is the boot finishing.
        dusk = getattr(self.hub, "dusk_arm", None)
        if dusk is not None and (reason := dusk.resume_veto()):
            self._set_hold(armed, reason)
            return
        if not self._devices_ready():
            return

        # Make the rig's beliefs true again BEFORE it is allowed to move.
        #
        # ``recovering`` goes up HERE, and where matters: nothing between the
        # ``engine.running`` check at the top of this method and this line
        # awaits (every await above sits on a branch that returns), so no turn
        # of the loop separates the two. That is what lets a start route
        # refuse on the flag without racing it (see ``recovering``). An await
        # added anywhere above breaks that; put it on a returning branch or
        # re-check ``engine.running`` after it. Down in a ``finally``, because
        # a ladder that raised is not still recovering.
        #
        # ``_recentred`` is cleared first, so an earlier tick's slew cannot
        # be handed to this start: only this ladder's slew sets it.
        #
        # THE LADDER RUNS AS ITS OWN TASK so ``stop_recovery`` can cancel the
        # step it is awaiting (#220) without cancelling this tick, which has
        # to go on and stand the attempt down. Creating the task does not
        # await, so the flag still goes up in the same synchronous stretch as
        # the ``engine.running`` check.
        #
        # ``_refusal_site_detail`` likewise, so a detail an earlier ladder
        # left cannot ride this ladder's refusal (#233). ``_ladder_left`` is
        # made here, with the flag, so a teardown route that saw
        # ``recovering`` always finds a future to wait on (#238).
        #
        # THE PREVIOUS HOLD STOPS BEING REPORTED HERE TOO (#261): see the
        # ``hold`` property, below. Nothing is cleared on the backing field —
        # a new refusal with the same words still keeps the first one's
        # ``since`` once the ladder has returned, which clearing it here
        # would have thrown away.
        self._recentred = None
        self._refusal_site_detail = None
        self._ladder_light = None
        self._ladder_dark_kind = None
        self._ladder_nothing_tonight = False
        self._ladder_software_fault = None
        self._stop_why = None
        self._ladder_session = armed
        self._ladder_step = "starting"
        self._ladder_left = asyncio.get_running_loop().create_future()
        self._recovering = True
        self._ladder = asyncio.ensure_future(self._recover(armed))
        try:
            refusal = await self._ladder
        except asyncio.CancelledError:
            # TWO THINGS CANCEL THIS AWAIT, and only one is ours to absorb.
            # ``stop_recovery`` cancels the ladder task alone, and the tick
            # goes on to stand down. The service's own ``stop`` (the app
            # shutting down) cancels THIS task, which cancels the ladder
            # with it, and that must propagate or the service would not
            # stop. ``cancelling()`` counts the requests to cancel this task,
            # so it tells the two apart.
            me = asyncio.current_task()
            if self._stop_why is None or (me is not None and me.cancelling()):
                raise
            refusal = None
        finally:
            self._recovering = False
            self._ladder = None
            self._ladder_session = None
            self._ladder_step = None
            stopped, self._stop_why = self._stop_why, None
            # Last, once the flag is down: a teardown route waiting in
            # ``wait_stopped`` resumes on the next turn of the loop, and by
            # then there is no ladder left for it to find.
            left, self._ladder_left = self._ladder_left, None
            if left is not None and not left.done():
                left.set_result(None)
        if stopped is not None:
            # AN OPERATOR STOPPED IT (#220), whether the ladder returned early
            # at a between-step point, was cancelled mid-step, or finished a
            # step that ran to its end and then found the flag. Checked ahead
            # of the refusal, because a step cut short can still return a
            # reason ("the solve failed"), and that is not why nothing
            # started. Not a refusal (no backoff: the operator's decision is
            # not a fault to wait out), not a crash (nothing counted), and not
            # a hold, so an earlier refusal's reason comes off the Monitor.
            # The session is never re-read or started here: whatever the
            # operator did to it (disarm, abandon, delete, abort) is theirs.
            self._clear_hold()
            bus.log("info", f"auto-resume stood down for '{armed.name}': "
                            f"{stopped}", "sequence")
            return
        if self._ladder_light == "lit":
            # LIGHT REACHED THE SENSOR, so whatever covered it is off (#251):
            # a solve that worked, or one that failed on a sky it could see.
            # The next no-light verdict is a new spell with its own alert.
            self._no_light_spell = None
        if refusal is not None:
            # THE WORDS GO TO THE LOG, THE NUMBERS ONLY TO THE HOLD (#233).
            # This line reaches the log ring, which ``/api/logs`` serves to a
            # viewer, and the night log, and ``refusal`` is words by
            # ``_recover``'s contract. The site-derived sentence behind it
            # (an altitude, a floor, an ETA, an azimuth) goes on the hold as
            # ``site_detail``, which the route withholds from a viewer; put
            # here, it would reach that viewer through the ring instead.
            backoff = RETRY_INTERVAL_S
            if self._ladder_light == "dark":
                backoff = self._no_light_backoff(armed, self._ladder_dark_kind)
            if self._ladder_software_fault is not None:
                # SAID ONCE A NIGHT, THEN QUIET (#866, the #284 pattern). A
                # code fault is most often deterministic, so the same two
                # warnings every ten minutes would be a push per retry to
                # every warning sink. The retry stays, silent: a fault with an
                # outside trigger (ASTAP's unguarded parse of a truncated
                # result file) can clear on the next solve, and the hold
                # stands on the Monitor all the while.
                said = (armed.id, night_key(now))
                if self._software_fault_said != said:
                    self._software_fault_said = said
                    bus.log("warning", self._ladder_software_fault,
                            "sequence")
                    bus.log("warning", f"auto-resume held: {refusal} — "
                                       f"retrying every {int(backoff / 60)} "
                                       f"min, without a word, until the "
                                       f"night ends", "sequence")
            elif not self._ladder_nothing_tonight:
                bus.log("warning", f"auto-resume held: {refusal} — retrying "
                                   f"in {int(backoff / 60)} min", "sequence")
            elif self._nothing_tonight_said != (armed.id, night_key(now)):
                # SAID ONCE A NIGHT, THEN QUIET (#284). Nothing can change
                # within the night on its own: set-aside records are only
                # added and a closed window stays closed, and the run already
                # said so when it set each target aside. A warning per retry
                # was a push every ten minutes to a sink that takes warnings.
                # The re-checks go on, silently, on the same retry, because an
                # operator can still change the answer (a PATCHed plan), and
                # the hold stands on the Monitor all the while. Keyed on the
                # session AND the night, so the next night's first refusal is
                # news again, as a no-light spell's is.
                self._nothing_tonight_said = (armed.id, night_key(now))
                bus.log("warning", f"auto-resume held: {refusal} — checking "
                                   f"again every {int(backoff / 60)} min, "
                                   f"without a word, until the night ends",
                        "sequence")
            self._retry_at = now + backoff
            self._set_hold(armed, refusal, self._retry_at,
                           site_detail=self._refusal_site_detail,
                           nothing_tonight=self._ladder_nothing_tonight)
            return
        # WHAT WAS READ BEFORE THE LADDER IS STALE AFTER IT (#211).
        #
        # ``armed`` was read at the top of this tick, and the ladder between
        # there and here is minutes of awaits. Run CONTINUE or /resume can
        # start the same session in that time. If that run also ENDED inside
        # the ladder (an abort, a safety stop, a short flow), the session is
        # dormant and armed again, holding frames ``armed`` has never seen.
        # Starting ``armed`` handed the engine that stale ledger, and
        # ``engine.start``'s own save wrote it over the file: every frame the
        # other run banked stopped being counted. The FITS stay on disk, but
        # nothing counts them. If the other run was still going, the start
        # was refused "already running": no frames lost, but a healthy run was
        # logged as a refusal and got a ten-minute backoff.
        #
        # So the session is read again, checked, and started in one section
        # with no await, under the store's write lock: the section Run
        # CONTINUE uses (spec 5.9, ``SessionStore.write_locked``). Nothing on
        # the loop can move the session between this read and the start, and
        # the lock holds off a worker thread's save until ``engine.start``'s
        # own save is done. A session that is no longer this tick's to start
        # is neither a refusal nor a crash: one info line, no backoff, no crash
        # counted, and the next tick looks again.
        with session_store.write_locked():
            fresh, why_not = self._still_startable(armed)
            if fresh is None:
                # Not a hold either, so an earlier refusal's reason must not
                # stay on the Monitor: a live run is not a hold (the top of
                # this method says the same), and a session that is no longer
                # dormant, armed or on disk is not one this service holds.
                self._clear_hold()
                bus.log("info", f"auto-resume stood down for '{armed.name}': "
                                f"{why_not}", "sequence")
                return
            # THE GATES AGAIN, ON THE PLAN THAT STARTS. The identity and
            # quota refusals near the top of this method read ``armed.plan``,
            # and ``fresh.plan`` need not be that plan: PATCH
            # /api/sessions/{id} replaces a dormant session's plan with
            # neither gate, the operator's reject guards live in config that
            # can change too, and the ladder is minutes long. ``engine.start``
            # is unguarded (this module's header), so without this the one
            # plan no gate had seen would be the one started. Refused on the
            # same terms as up there: a hold and the backoff, not a crash,
            # and the next tick's own gates then refuse it before the ladder.
            gate = self._plan_refusal(fresh)
            if gate is not None:
                bus.log("warning", f"auto-resume refused: {gate} — retrying "
                                   f"in {int(RETRY_INTERVAL_S / 60)} min",
                        "sequence")
                self._retry_at = now + RETRY_INTERVAL_S
                self._set_hold(fresh, gate, self._retry_at)
                return
            # Logged HERE, once per resume, and not beside the refusal above:
            # the early returns in between (devices still booting, a dusk
            # hold) come back every 60 s tick, and the warning would repeat
            # with them. Below the re-check, and about the plan that is
            # actually started, so a stood-down resume does not warn about a
            # start that never happened.
            name_warning = duplicate_name_warning(fresh.plan)
            if name_warning:
                bus.log("warning", name_warning, "sequence")
            # THE LADDER'S SWEEP GOES WITH THE START (#402), when it made one
            # tonight that found focus: the run's first acquisition then
            # sweeps again only under the hop rule. Passed only when there
            # is one, so a start with none is today's call exactly.
            sweep = self._sweep_for_start()
            handed = {"focus_sweep": sweep} if sweep is not None else {}
            try:
                self.hub.require("camera")
                # A resume is a NEW run and re-reads the standing setpoint,
                # exactly as the two /api resume entries do (replan_cooling).
                # Without it a multi-night session that began with no
                # setpoint warns every single night with no reachable way to
                # act on the advice.
                #
                # ``tracking``: the ladder just left the mount tracking a
                # target, and the engine's idle clock watches only what it
                # acquired itself, so without this nothing watched that
                # mount's idle time, floor or flip point until the run set
                # a target up (#202). See ``_tracking_for``.
                # ``operator=False``: an automatic restart keeps tonight's
                # per-target hold and re-centre budgets (#853, ruling R5),
                # so a runaway cannot buy a fresh budget by being resumed.
                self.engine.start(replan_cooling(
                    fresh.plan, config_store.cfg().cooling.setpoint_c),
                    session=fresh, tracking=self._tracking_for(fresh),
                    operator=False, **handed)
                # Taken: a later start of this process is not stood on it.
                if handed:
                    self._recovery_sweep = None
            except Exception as e:          # noqa: BLE001 — refusal, not a crash
                bus.log("warning", f"auto-resume refused: {e} — retrying in "
                                   f"{int(RETRY_INTERVAL_S / 60)} min",
                        "sequence")
                self._retry_at = now + RETRY_INTERVAL_S
                self._set_hold(fresh, str(e), self._retry_at)
                return
        self._retry_at = 0.0
        self._clear_hold()
        # A start changed the night: a later "nothing to shoot tonight" for
        # this session is new, and is said (#284).
        self._nothing_tonight_said = None
        self._software_fault_said = None        # the same for a fault (#866)
        bus.log("info", f"auto-resume: '{fresh.name}' resumed", "sequence")

    def _still_startable(self, armed: Session) -> tuple[Session | None, str]:
        """The armed session as it is NOW, if this tick may still start it,
        or None and the reason it may not.

        Called inside ``tick``'s locked section, after the ladder: see the
        comment there. Four ways the session stops being this tick's:

        * a run is going. Usually the same session (then it is active as
          well), but not always: the operator can arm this dormant session
          while another run is live, which the PATCH route allows, and then
          only this check sees it.
        * the file is gone or no longer validates. Starting the copy read
          before the ladder would put a deleted ledger back on disk, running,
          or overwrite the corrupt file someone needs to look at.
        * it is not dormant. A run inside the ladder that banked everything
          ends ``complete`` and completion does not disarm, so ``complete``
          and armed is a real state; starting it would reopen a finished
          session.
        * it was disarmed. ``engine.start`` arms what it starts, so starting
          it would also undo the operator's disarm.
        """
        if self.engine.running:
            return None, "a run started while the recovery ladder was working"
        try:
            fresh = session_store.load(armed.id)
        except (KeyError, SessionUnreadable):
            return None, ("the session was deleted or became unreadable while "
                          "the recovery ladder was working")
        if fresh.status != "dormant":
            return None, f"it is {fresh.status} now, not dormant"
        if not fresh.auto_resume:
            return None, "it was disarmed while the recovery ladder was working"
        return fresh, ""

    def _tracking_for(self, fresh: Session) -> Target | None:
        """The target to hand ``engine.start`` as ``tracking``: the one this
        tick's ladder left the mount tracking, or None when it slewed nowhere
        (a calibration-only session).

        FROM THE RE-READ SESSION BY ID, so the engine is handed a target of
        the plan it runs, which is ``fresh``'s and not the copy the ladder
        read (``_still_startable`` says why that copy is stale).

        UNLESS THAT COPY SAYS SOMEWHERE ELSE. PATCH may replace a dormant
        session's plan while the ladder runs, and the re-read target by that
        id can then sit at other coordinates, or be gone. The mount is still
        tracking where the ladder pointed it, and that is what the idle
        clock's floor and flip checks must read; a target elsewhere would
        have them watch a place the mount is not, and None would leave the
        mount unwatched (#202). So the target as re-centred is handed over
        instead: same id, same coordinates, and nothing in the idle watch
        reads the rest."""
        tgt = self._recentred
        if tgt is None:
            return None
        for t in fresh.plan.targets:
            if (t.id == tgt.id and t.ra_hours == tgt.ra_hours
                    and t.dec_deg == tgt.dec_deg):
                return t
        return tgt

    def _plan_refusal(self, session: Session) -> str | None:
        """The identity and quota gates asked of ``session``'s plan: the
        hold reason when this service may not start it, else None.

        Only the re-check after the ladder calls this. The gates near the top
        of ``tick`` keep their own wording, because their log lines are what
        the operator and the suite already read; the rules themselves live in
        ``models`` and ``policy``, so the two sites cannot disagree about a
        plan, only phrase it differently.
        """
        identity = plan_identity_errors(session.plan)
        if identity:
            return "this plan cannot start: " + "; ".join(identity)
        if quota_unbounded(session.plan,
                           resolve_policy(session.plan, config_store.cfg())):
            return ("this plan counts accepted frames with no reject guard "
                    "and no stop boundary, so it could run forever")
        return None

    async def _give_up_and_stow(self, session) -> None:
        """Stop resuming this session, and PUT THE RIG AWAY.

        The stowing is the point. Disarming alone would end the crash loop and
        leave the mount tracking, the camera cold and the cover open until
        dawn-park notices at -6 degrees - which on a fault at 22:00 is eight
        hours of an unattended telescope following a sky nobody is recording.
        Trading a loop for a silent idle is not a fix.

        Idempotent by construction: disarming is what stops the next tick
        reaching here, and it is written to disk before the wind-down so a
        crash DURING the stow cannot re-enter the loop.
        """
        if self._stowed_for == session.id:
            return
        self._stowed_for = session.id
        bus.log("error",
                f"auto-resume GIVING UP on '{session.name}': "
                f"{session.crash_resumes} consecutive crashes. Something is "
                f"wrong that restarting does not fix. Parking and warming the "
                f"rig; resume it by hand once the cause is found.", "sequence")
        session.auto_resume = False
        try:
            session_store.save(session)
        except Exception as e:
            # Say so LOUDLY: an unsaved disarm means the next tick tries again,
            # and the operator needs to know the latch did not hold.
            bus.log("error", f"could not disarm '{session.name}' — auto-resume "
                             f"may retry the crash loop: {e}", "sequence")
        try:
            # The LIVE config, not a run's frozen snapshot: there is no run here
            # to have taken one, and the operator's current roof setting is the
            # one that should decide whether the shutter moves.
            cfg = config_store.cfg()
            await self.engine._wind_down(
                park=True, warm=True,
                close_dome=bool(cfg and cfg.safety.close_dome_when_done))
        except Exception as e:
            bus.log("error", f"could not stow the rig after giving up: {e} — "
                             f"THE MOUNT MAY STILL BE TRACKING", "sequence")

    async def _recover(self, session) -> str | None:
        """Re-establish what the rig cannot simply assume after a restart.

        Returns None when the rig is fit to resume, otherwise a human-readable
        reason the caller logs before arming the backoff. The session is left
        dormant AND armed either way, so the next tick retries.

        THE REASON IS WORDS (#233): it becomes the hold's ``reason`` and the
        "auto-resume held" warning, which a viewer reads. A refusal whose
        explanation is made of site-derived numbers (the start floor, the
        slew-limit gate) puts that sentence in ``_refusal_site_detail``
        instead, and ``tick`` stores it as the hold's ``site_detail``.

        It also returns None, early, when a run starts while it works (#211)
        or an operator asks it to stop (#220): before each step that would
        focus, expose or slew it asks ``_must_stop`` and stops there. That
        is not a refusal - nothing is wrong with the rig, and a backoff would
        only delay the next resume - so it does not say why; ``tick`` finds
        the stop request or the running engine and stands the attempt down
        with the one line that does. A stop also cancels whatever step this
        is awaiting (``stop_recovery``), so the question matters most for a
        step that ran to its end regardless.

        It names each step in ``_ladder_step`` as it reaches it, for
        ``recovery``: words from ``LADDER_STEPS``, never a number.

        COOLING IS NOT HERE, deliberately. ``SequenceEngine._run`` already awaits
        ``_cool_and_wait(plan.cool_to, plan.cool_timeout_s)`` under the
        ``require_cooling``/``cooling_action`` policy, sharing the
        ``COOLER_AT_TARGET_C`` band with the Monitor. A resumed run reuses the
        same plan object, so it inherits that gate. A second wait here would
        double the delay and let the two bands drift apart.
        """
        from ..devices import fingerprint as _fp

        cfg = config_store.cfg()

        # WHAT THE RUN WILL SHOOT TONIGHT, from the ledger alone (#159). Asked
        # first because it touches no device: a session with nothing to shoot
        # tonight is refused before the safety read, the focuser or the
        # blind solve spend anything on it (see ``nothing_to_shoot_tonight``
        # for why it is refused at all). Step 3 re-centres on these. The
        # night key is ``events.night_key`` of the injected clock, the key
        # ``Session.note_set_aside`` records are written under (3.4).
        #
        # THROUGH THE RUN'S GATING (#283): the live site, the operator's
        # twilight and the same clock, so a closed window drops its target
        # here as the run would drop it, and a ready target is tried first.
        # ``recentre_candidates`` applies none of it while the site is unset
        # (#121).
        now = self._clock()
        candidates = recentre_candidates(
            session, night_key(now), self._walk(session, now),
            site=getattr(self.hub, "site", None),
            twilight_deg=cfg.safety.twilight_deg if cfg else -12.0, now=now)
        if nothing_to_shoot_tonight(session, candidates):
            self._ladder_nothing_tonight = True
            return NOTHING_TONIGHT

        # 0. IS IT SAFE TO BE OUT AT ALL — before anything moves.
        #
        #    This module's header once said every safety gate "runs inside
        #    engine.start / the run itself", and that was true when the resume
        #    path WAS engine.start. The ladder below was added in front of it and
        #    the sentence was not revisited: steps 2 and 3 plate-solve and
        #    re-center, which is real unattended motion, ahead of every gate the
        #    claim named. A rig that rebooted during the rain it had already
        #    stopped for would slew back out into it.
        #
        #    Configuration versus conditions, the same split as the solver and
        #    focus steps below: NO safety monitor is a standing choice and must
        #    not delete auto-resume for that rig, so it proceeds. A monitor that
        #    is present and says unsafe — or has gone stale, which is not
        #    evidence of safety — holds, and the ten-minute backoff is exactly
        #    right here because the sky may well clear.
        if cfg.safety.enabled and self.hub.devices.get("safety") is not None:
            self._ladder_step = "safety"
            reading = await self.engine.current_safety()
            if reading is None:
                return ("the safety monitor has not reported yet — not moving "
                        "the mount on an unknown verdict")
            if reading.stale:
                return ("the safety monitor's reading is stale — a reading that "
                        "stopped arriving is not evidence that it is safe")
            if not reading.is_safe:
                return (f"the safety monitor says it is not safe to observe "
                        f"({reading.reason or 'no reason given'})")

        # 1. FOCUS — measure when the focuser lost count; never restore a number.
        #    A focuser that disagrees with the record has forgotten its position
        #    (the EAF does this on power loss), so its readout is a default, not
        #    a measurement. Driving it back to the remembered value would be a
        #    guess about a device that just said it does not know where it is.
        pos = None
        self._ladder_step = "focus"
        try:
            foc = self.hub.require("focuser")
            pos = await foc.get_position()
        except Exception:  # noqa: BLE001 — no focuser is not a refusal
            pos = None
        # A KEPT SWEEP IS THE FOCUS ONLY WHILE THE DRAWTUBE STANDS WHERE IT
        # LEFT IT (#402). Between two ladders a run may have come and gone,
        # its own sweeps moving the focuser, and a sweep from before it
        # handed to the next start would reuse a focus the drawtube has
        # left. A position nobody can read, then or now, vouches for nothing.
        kept = self._recovery_sweep
        if kept is not None and (pos is None or kept.position is None
                                 or int(pos) != kept.position):
            self._recovery_sweep = None
        if pos is not None and not _fp.verdict(focuser_position=pos).focus_trusted:
            # SAME CONFIGURATION-VERSUS-CONDITIONS SPLIT AS THE SOLVER BELOW, and
            # it was missing here until CI found it. A rig with no autofocus
            # provider at all — no native engine, no NINA — cannot autofocus on
            # ANY night; its owner focuses by hand and images anyway. Treating
            # that as a refusal did not make it safer, it deleted auto-resume for
            # that rig entirely and silently: the tick refused, armed the
            # ten-minute backoff, and did it again forever, with the real reason
            # only in a log line nobody was reading.
            #
            # So "no autofocus provider configured" degrades to a warning and the
            # run resumes at the focuser's current position, which is precisely
            # what that rig would have been doing unattended anyway. "There IS a
            # provider and it failed" still refuses — that is a real inability to
            # recover focus, and resuming a night that will produce nothing but
            # bloated stars is worse than waiting.
            if not self._can_autofocus():
                bus.log("warning",
                        "the focuser lost its position across the restart and no "
                        "autofocus provider is configured — resuming at its "
                        "current position, so check focus before trusting "
                        "tonight's frames", "sequence")
            else:
                if self._must_stop():
                    return None
                bus.log("info", "focuser lost its position across the restart — "
                                "running autofocus before resuming", "sequence")
                self._ladder_step = "autofocus"
                try:
                    result = await self._autofocus()
                except Exception as e:  # noqa: BLE001
                    self._recovery_sweep = None
                    return f"autofocus after restart failed: {e}"
                # THE RUN STANDS ON THIS SWEEP IF IT FOUND FOCUS (#402), so
                # the run's first acquisition does not sweep the same focuser
                # again two minutes later. Kept in memory for ``tick``.
                await self._note_recovery_sweep(foc, result)
                # An autofocus is the MEASUREMENT the fingerprint could not
                # make, so record where it left the drawtube. Without this the
                # next step's refusal (cloud, no solve) sends the whole ladder
                # back through autofocus on every ten-minute retry, because
                # nothing else can re-establish trust once a gap has opened.
                #
                # ONLY A SWEEP THAT FOUND FOCUS IS A MEASUREMENT (#457). A
                # failed native sweep returns ``success`` False after putting
                # the drawtube back where it started, which after a restart is
                # the position the focuser forgot, and vouching for it
                # recorded that position as measured: a refusal after it (a
                # failed solve) then left the next tick trusting the focuser
                # and not sweeping again, and a plan with no initial
                # autofocus imaged the night on it. The same test
                # `_note_recovery_sweep` applies, so a result that is not a
                # result (a test's stub, which returns None) vouches for
                # nothing either. The ladder still goes on after a failed
                # sweep, with that function's warning, as it did.
                if getattr(result, "success", None) is True:
                    try:
                        _fp.vouch(focuser_position=await foc.get_position())
                    except Exception:  # noqa: BLE001 — bookkeeping, never a refusal
                        pass

        # 2. POINTING — ALWAYS re-measure. Never gated on the fingerprint.
        #
        #    The AM5 is a harmonic drive with NO BRAKE. A restart that preserved
        #    every byte of software state still cannot rule out that the tube
        #    sagged under gravity while the motors were unpowered, and the
        #    mount's own encoders cannot report a shift that happened while it
        #    was off. So "nothing changed in software" is not evidence about
        #    where the telescope points; only the sky is.
        #
        #    If the solve fails — too few stars, heavy cloud — DO NOT MOVE. The
        #    alternative is slewing an OTA whose true position is unknown, which
        #    is how a tube meets a pier.
        #    ONE EXCEPTION, and it is about configuration rather than conditions:
        #    a rig with no solver at all cannot verify pointing on ANY night. It
        #    slews on the mount's model every time it observes, by the owner's
        #    standing choice. Refusing to resume such a rig would not make it
        #    safer — it would just delete the feature for it, while leaving the
        #    identical blind slew in place everywhere else. So "no solver
        #    configured" degrades to the rig's normal behaviour with a warning,
        #    while "there IS a solver and it could not solve" refuses: that is
        #    cloud or too few stars, a transient inability to verify, and it is
        #    exactly the case where moving is a gamble.
        if self._must_stop():
            return None
        if not self._can_solve():
            # NO SOLVER, AND THE DRIVER SAYS IT DOES NOT KNOW WHERE IT POINTS
            # (#867 by another route, ruling R2). The standing choice to
            # resume a no-solver rig on the mount's model assumes the model
            # is real; a driver that latched ``position_known`` False (an
            # AM5 that read its home pole on a (re)connect) says it is not.
            if not self._mount_position_known():
                return POSITION_UNKNOWN_NO_SOLVER_WORDS
            bus.log("warning", "resuming after a restart WITHOUT verifying where "
                               "the telescope points — no plate solver is "
                               "configured, so the mount's own position is taken "
                               "on trust", "sequence")
        else:
            try:
                # A LONGER EXPOSURE THAN THE DEFAULT, ON PURPOSE.
                #
                # solve_and_sync defaults to 3 s at gain 200 bin 2, which suits
                # centering — there the mount is already near the target and a
                # solve happens several times per slew, so it is tuned for speed.
                # Recovery is the opposite case: it runs once, nothing else is
                # waiting on it, and failing costs a TEN MINUTE backoff.
                #
                # A longer exposure than solve_and_sync's 3 s default, because
                # recovery runs once, nothing waits on it, and failing costs a
                # ten-minute backoff. More stars is the cheapest lever there is.
                #
                # It KEEPS the mount's pointing hint. Dropping it was tried on
                # 2026-08-02 and was a regression: the hint bounds ASTAP's search
                # to a 15-degree radius, which comfortably covers the ~4 degrees
                # of error a sagged or slipped mount showed that night, while
                # dropping it forces a true all-sky search that failed outright
                # on a sparse field. Bounded-and-generous beats blind.
                self._ladder_step = "solve"
                mark = self._solve_mark()
                try:
                    # ``refusal_level="info"`` (#850): a refused or
                    # unconfirmed sync is decided on below, and the arm
                    # logs its own warning saying what happens next. At
                    # warning level the hub's line would put the driver's
                    # e11 words (advice for an operator at the scope) in
                    # front of the operator while the ladder goes on to
                    # recover by itself, or holds in words of its own.
                    await self.hub.solve_and_sync(
                        exposure_s=RECOVERY_SOLVE_EXPOSURE_S,
                        refusal_level="info")
                finally:
                    # Solved or not: a failed solve is the one whose timing
                    # is wanted (#402).
                    self._say_solve_timing(mark, "the blind solve")
            except NoLightError as e:
                # THE CAMERA IS IN THE DARK (#251), which is not the cloud
                # the words below describe. ``tick`` alerts once and backs
                # off, hourly only when a dark master stands behind the
                # verdict, so the kind of reference goes with it (#308). The
                # reason is words only, not the error's text, which carries
                # the solver's own words and whatever numbers they hold: the
                # hold must keep its ``since`` across the retries, and it is
                # read by a viewer.
                self._ladder_light = "dark"
                self._ladder_dark_kind = e.reference_kind
                return (f"{NO_LIGHT_WORDS}, so the blind plate solve after "
                        f"the restart cannot say where the mount points; not "
                        f"slewing")
            except SyncRefused as e:
                # THE SOLVE WORKED AND THE MOUNT WOULD NOT TAKE ITS ANSWER
                # (#850). Not a failed plate solve, so never the words of
                # the arm below. This solve syncs wherever the mount stands,
                # and after a dawn park that is the home position, tube at
                # the pole, where the AM5 refuses syncs with the tube at home
                # (on the bench, 2026-10-08, all but one answered ``e11``;
                # the one ``N/A`` moved nothing). The driver read the
                # position back after the refusal, so the question is
                # whether the mount's reported pointing is already close
                # enough to the solved field to slew on, AND whether the
                # driver vouches for its position (#867): only then does
                # step 3's re-centre go ahead, away from the pole, where the
                # AM5 takes syncs, and correct the rest. Near the pole that
                # separation cannot see the RA axis angle (see
                # RECOVERY_REFUSED_SYNC_MAX_DEG), so the warning says what
                # was measured and nothing more.
                # A mount that disagrees badly (or whose position could not
                # be read) and refuses the correction does not know where it
                # points and cannot be fixed from here (#857): hold.
                #
                # The figure is a separation in degrees, never the read-back
                # itself: at home that is the pole, and its RA follows local
                # sidereal time, both site oracles (#140, #166). It goes to
                # a warning; the hold is fixed words (``REFUSED_SYNC_*``) so
                # it keeps its ``since`` across retries.
                residual = e.residual_deg
                if residual is not None and residual <= RECOVERY_REFUSED_SYNC_MAX_DEG:
                    # The frame solved, so light reached the sensor (#251).
                    self._ladder_light = "lit"
                    # A SMALL SEPARATION IS NOT A KNOWN POSITION (#867). Near
                    # the pole it cannot see the RA axis angle, so a mount
                    # whose model reports home while its axis has turned
                    # reads close. The driver's ``position_known`` is the
                    # evidence the separation lacks: an AM5 latches it False
                    # on a (re)open that reads its home pole, until a sync
                    # away from the pole or Trust position. Without it, no
                    # slew: the hold gives the safe order, never a goto. The
                    # figure is the separation, which the go-ahead line
                    # below already prints, never the read-back.
                    if not self._mount_position_known():
                        bus.log("warning",
                                f"the mount refused the blind solve's sync"
                                f"{_reply_words(e.code)}; not slewing, as "
                                f"nothing has confirmed its position since "
                                f"a reconnect or a stopped run put it in "
                                f"doubt (its reported pointing is "
                                f"within {residual:.1f} deg of the solved "
                                f"field)", "sequence")
                        return POSITION_UNKNOWN_WORDS
                    # What happens next first (#850): the UI cuts a long
                    # line at 137 chars.
                    bus.log("warning",
                            f"the re-centre goes ahead and syncs away from "
                            f"the pole: the mount would not take the blind "
                            f"solve's sync where it stands"
                            f"{_reply_words(e.code)}, but its reported "
                            f"pointing is within {residual:.1f} deg of the "
                            f"solved field",
                            "sequence")
                else:
                    # Light reached the sensor here too: the solve worked.
                    self._ladder_light = "lit"
                    if residual is None:
                        bus.log("warning",
                                f"the mount refused the blind solve's sync"
                                f"{_reply_words(e.code)}; not slewing, as its "
                                f"position could not be read back: "
                                f"{e.reason}",
                                "sequence")
                        return REFUSED_SYNC_UNKNOWN_WORDS
                    # NOT the driver's reason after "at the scope" (#850):
                    # the driver's e11 words carry a remedy of their own
                    # (Trust position, or the tube brought home by eye
                    # first), a second instruction beside a hold that says
                    # the mount cannot be corrected from here. The reply
                    # code names the refusal well enough. The action
                    # before the figure, inside the UI's 137-char cut.
                    bus.log("warning",
                            f"the mount refused the blind solve's sync"
                            f"{_reply_words(e.code)} and needs someone at the "
                            f"scope: its own position is {residual:.1f} deg "
                            f"from the sky, beyond the "
                            f"{RECOVERY_REFUSED_SYNC_MAX_DEG:.0f} deg the "
                            f"ladder slews on", "sequence")
                    return REFUSED_SYNC_FAR_WORDS
            except SyncUnverified as e:
                # THE SOLVE WORKED AND NOBODY KNOWS WHETHER THE MOUNT TOOK
                # ITS ANSWER (#850): the link failed around ``:CM#`` or the
                # read-back never answered. Not a failed plate solve, so not
                # the arm below (whose words the UI turns into "check
                # focus/exposure"), and not a refusal either. Where the mount
                # points is unknown, and this step exists so the ladder never
                # slews on an unknown position: hold, in fixed words, and
                # retry. The driver's reason (fixed words, no link bytes) and
                # the reply go to a warning.
                #
                # Light reached the sensor: the frame solved (#251), as in
                # the refused arm's hold.
                self._ladder_light = "lit"
                bus.log("warning",
                        f"the mount did not confirm the blind solve's sync"
                        f"{_reply_words(e.code)}; not slewing, and if this "
                        f"repeats, check the mount's link: {e.reason}",
                        "sequence")
                return REFUSED_SYNC_UNVERIFIED_WORDS
            except _SOFTWARE_FAULTS as e:
                # A FAULT IN THIS PROGRAM, NOT THE SKY (#866). Held like a
                # failed solve, since where the mount points is still
                # unverified, but not in its words. The warning names the
                # type and where it was raised (basenames only, no message
                # text) and goes to the durable night log through ``tick``,
                # once a night; the traceback goes to stderr, which the
                # detached supervisor may not keep, so the warning is the
                # record. Light is not judged: a fault is no evidence either
                # way.
                _log.error("resume ladder: software fault in the blind solve",
                           exc_info=e)
                # ``_unpaired``: the type and frame names are not ours to
                # choose, and must not trip the UI's rewrite.
                self._ladder_software_fault = _unpaired(
                    f"the resume ladder hit a software fault in the blind "
                    f"solve ({type(e).__name__}); not slewing. Raised at, "
                    f"innermost last: {_fault_site(e)}")
                return SOLVE_SOFTWARE_FAULT_WORDS
            except Exception as e:  # noqa: BLE001
                # A FAILED SOLVE WHOSE FRAME SHOWED LIGHT ends a no-light
                # spell: something that had covered the optic is off, and a
                # cloudy sky is today's ten-minute hold. Asked of the
                # verdict, not the text; a failure nothing could judge (no
                # reference, a camera fault) is no evidence of light and
                # leaves ``_ladder_light`` None.
                if isinstance(e, FailedSolveError) and e.verdict.kind == CLOUD:
                    self._ladder_light = "lit"
                return (f"blind plate solve failed after restart ({e}) — refusing "
                        "to slew a mount whose true position is unknown")
            self._ladder_light = "lit"
            # AN ACCEPTED SYNC IS NOT ALWAYS A KNOWN POSITION (#867). Near the
            # pole the AM5's read-back cannot tell a sync it took from one it
            # ignored, so its driver keeps the position unknown
            # (``SYNC_POLE_BLIND_DEG``) and says why in a "mount" warning of
            # its own; nothing is added here. On every retry the same holds
            # (the latch is still set), so it lasts until Trust position.
            if not self._mount_position_known():
                return POSITION_UNKNOWN_SYNC_WORDS

        # 3. RE-CENTER ON WHAT THE RUN WILL SHOOT FIRST, AT ITS ANGLE (#159,
        #    I-13; spec 5.9, ruling 9). This used to be the plan's first light
        #    target, whatever the ledger said: a finished one, a mosaic's 1-1
        #    whatever the order, a panel set aside tonight. A finished first
        #    target below its floor then refused the whole resume while the
        #    target the run would shoot stood high. The candidates are what
        #    the run can still shoot tonight, in its order (listed above,
        #    before anything moved), and the first that clears its own start
        #    floor and the slew limits is the one re-centred.
        #
        #    IT REFUSES ONLY WHEN NO CANDIDATE CAN BE REACHED, and in the
        #    FIRST candidate's words and numbers: the target the run would
        #    shoot first is the one whose wait the operator wants to read,
        #    and a plan with one light target keeps today's refusal word for
        #    word. The words are site-free either way (#233).
        #
        #    No candidate and no refusal: calibration-only work, or nothing
        #    owed at all. Those never slew; they still got the solve above,
        #    which costs one exposure and confirms the sky is usable.
        tgt: Target | None = None
        first_refusal: tuple[str, str] | None = None
        for candidate in candidates:
            # EACH CANDIDATE GETS TODAY'S TWO CHECKS, in today's order, and a
            # refusal records its words and numbers and tries the next. Only
            # the refusal step 3 returns is filed, so a candidate that gives
            # way leaves no ``_refusal_site_detail`` behind.
            #
            # THE CANDIDATE'S OWN START FLOOR, and it is asked FIRST.
            #
            # The gate below is the MOUNT's floor - config, horizon, wedges,
            # pier. The plan carries a second, usually higher one:
            # ``Schedule.min_altitude_deg``, the altitude at which this target
            # is worth shooting, which the engine enforces when a run starts a
            # target (``_enforce_altitude_floor``). The re-centre happens
            # before any of that, so on 2026-09-06 the ladder asked
            # the AM5 to slew to NGC 604 at nine degrees - a target its own
            # plan would not have started for two more hours. The mount refused
            # (e6, its own horizon limit) and the resume held on a ten-minute
            # retry until the target rose, which is the right outcome reached
            # by the wrong route: the plan already knew the answer.
            #
            # A refusal, not a wait, because RETRY_INTERVAL_S is already the
            # cadence for exactly this - come back in ten minutes and ask the
            # sky again.
            floor = float(getattr(getattr(candidate, "schedule", None),
                                  "min_altitude_deg", 0.0) or 0.0)
            if floor > 0:
                from .engine import _frame_altitude
                alt = _frame_altitude(candidate, self.hub.site, self._clock())
                # ``None`` is "nobody can say" - an unset site, a bad
                # coordinate - and it must not read as "below the floor". The
                # engine's own floor gate makes the same tri-state distinction,
                # and for the same reason: refusing on an unreadable altitude
                # would strand every rig whose site is not configured.
                if alt is not None and alt < floor:
                    # WORDS FOR THE REASON, NUMBERS FOR SITE_DETAIL (#233; H3
                    # orchestrator ruling 1 (spec, Still waiting on the
                    # owner, item 10)). The reason is the hold a viewer reads
                    # at GET /api/sequence/resume-arm and the warning a viewer
                    # reads at /api/logs, and the altitude of a known target
                    # at a known time is the site (#140); the ETA is a second
                    # fix on it, and the floor, printed beside the altitude,
                    # is the bound that dates the crossing. So none of the
                    # three is in the words. The operator still gets the
                    # sentence that says how long the wait is, through
                    # ``site_detail``, which ``api/redact.py`` withholds from
                    # a viewer. What the words cannot withhold is that a
                    # floor refusal happened at all, and when it stopped:
                    # the timing channel spec 6.9 records as a residual.
                    if first_refusal is None:
                        first_refusal = (
                            f"{candidate.name} is below its start floor; not "
                            f"slewing yet",
                            f"{candidate.name} is at {alt:.0f} deg, below its "
                            f"{floor:.0f} deg start floor"
                            + self._floor_eta_note(candidate, floor))
                    continue
            # The altitude floor, horizon, no-go wedges, pier limits and the
            # zenith keep-out — the SAME gate every in-run slew passes. It lived
            # only inside the run, so this slew, the one made unattended by a
            # machine that just rebooted, was the single slew nothing checked.
            # ``cfg`` is passed explicitly: with no run in flight the engine's
            # own config snapshot is None, and the gate would no-op in silence.
            try:
                # ``plan`` as well as ``cfg``: the pier-collision branch reads
                # plan.meridian_flip, and in this fresh post-reboot process the
                # engine's own plan is still None — so without it the pier half
                # of the gate was inert while the altitude half ran. Same object
                # engine.start receives below, so both gates read one setting.
                self._ladder_step = "limits"
                await self.engine.check_slew_limits(candidate, cfg=cfg,
                                                    plan=session.plan)
            except Exception as e:  # noqa: BLE001 — a refusal, a dead link or a bad target
                from .engine import SafetyAbort, SlewRefused
                if isinstance(e, SafetyAbort) and not isinstance(e, SlewRefused):
                    # THE MOUNT DID NOT ANSWER (#327). The gate raises a
                    # plain SafetyAbort for one thing only: a pier-guard read
                    # past its bound (`_pier_guard_read`, #314), the engine's
                    # dead-link abort. Told apart by TYPE, never by text, as
                    # a cloud hold's re-point tells them apart (#240). Read
                    # as a limit, it told the operator the target was
                    # outside the slew limits and sent them to the limits
                    # configuration while the link was what had failed.
                    # Refused at once, with no other candidate tried: a link
                    # that did not answer for one target will not for the
                    # next, and each try would stall the ladder a whole
                    # bound. The caller's ten-minute retry follows, as it
                    # does for a failed solve or goto. The timed-out read's
                    # own sentence goes to ``site_detail``, the operator's.
                    self._refusal_site_detail = str(e)
                    return ("re-centering after restart refused: the mount "
                            "did not answer the slew-limit check, so the "
                            "link to it may be down; not slewing")
                # The gate's numbers (the altitude, the limit and the
                # azimuth it judged: "... altitude 12 deg below safety floor
                # 20 deg (az 238 deg)") and which of its limits refused
                # (floor, horizon mask, wedge, zenith keep-out, pier side)
                # are each a fact about where the rig stands. So the reason
                # names none of them, and the numbers go to ``site_detail``
                # (#233, as the floor refusal above). Any exception, not only
                # SlewRefused: the gate's text is not read here, so none of
                # it is trusted to be site-free.
                #
                # THE NUMBERS ARE ON THE REFUSAL, NOT IN ITS TEXT. Since
                # #233 the gate's message is words and its numeric sentence
                # rides ``SlewRefused.site_detail``; filing ``str(e)`` handed
                # the operator the same words twice and the numbers never.
                # ``str(e)`` stays the fallback for a refusal without one (a
                # pier-side refusal, a bad target), which is what an operator
                # was shown before.
                if first_refusal is None:
                    words = (
                        "re-centering after restart refused: the target "
                        "is outside this rig's configured slew limits "
                        "(altitude floor, horizon, no-go wedges, pier "
                        "side or zenith keep-out); not slewing yet"
                        if isinstance(e, SlewRefused) else
                        "re-centering after restart refused: the slew-limit "
                        "check failed; not slewing")
                    first_refusal = (words, getattr(e, "site_detail", None)
                                     or str(e))
                continue
            tgt = candidate
            break
        if tgt is None and first_refusal is not None:
            reason, self._refusal_site_detail = first_refusal
            return reason
        if tgt is not None:
            # BELOW the limit check, not above it: that check awaits too, and
            # the slew is the step that must never land on a live run, nor
            # follow an operator's stop (#220). A stop that arrives once the
            # goto is under way cancels this await; see ``stop_recovery`` for
            # what that does and does not do to the mount.
            if self._must_stop():
                return None
            # ASKED AGAIN, ON THE HAZARD'S CLOCK (#867). Step 2 asked the
            # driver, but the limit check above awaits a mount read that can
            # run to its bound, and a link that reopens meanwhile reads the
            # home pole and latches the AM5 again. The latch is set by the
            # link, not by this ladder, so it is read here, beside the stop
            # check, with no await between it and the goto.
            if not self._mount_position_known():
                return POSITION_UNKNOWN_RECENTRE_WORDS
            self._ladder_step = "recentre"
            # THE ANGLE, when there is one (ruling 9): the planned angle, or
            # the locked one commanded only to a CONNECTED rotator (#295;
            # ``commanded_rotation``). With neither, the call is today's,
            # with no keyword at all, so a rig with no rotator and a plan
            # with no angle (or a lock it cannot turn to) see nothing new.
            rotation = commanded_rotation(session, tgt, self.hub)
            mark = self._solve_mark()
            centring = None
            try:
                try:
                    if rotation is None:
                        centring = await self.hub.goto_and_center(
                            tgt.ra_hours, tgt.dec_deg)
                    else:
                        centring = await self.hub.goto_and_center(
                            tgt.ra_hours, tgt.dec_deg, rotation_deg=rotation)
                finally:
                    # Each centring solve's exposure against its GoTo's
                    # settle (#402), however the re-centre ended.
                    self._say_solve_timing(mark, "the re-centre")
            except GotoRefused as e:
                # THE MOUNT SAID NO, which is a different thing from the slew
                # failing, and the operator can act on the difference: a
                # refusal names a limit to wait out or clear, a failure names
                # something broken. The driver's own words, not the wire code
                # the log used to carry alone.
                return f"re-centering after restart refused by the mount: {e.reason}"
            except Exception as e:  # noqa: BLE001
                return f"re-centering after restart failed: {e}"
            # THE MOUNT REFUSED THE RE-CENTRE'S SYNC (#850). Away from the
            # pole the AM5 takes syncs, so a refusal here is a mount that will
            # not be corrected, and ``goto_and_center`` reports it only when
            # the solved field is NOT within tolerance of the target (a field
            # that is centred anyway comes back as centred). Starting the run
            # would image a field the ladder could not centre, which is the
            # 2026-10-07 night (#852). So hold in fixed words, and do NOT set
            # ``_recentred``: ``tick`` hands that target to the engine as the
            # one the mount is tracking, and it is not. The hub already logged
            # how far off the field is, in words a viewer may read.
            #
            # A sync nobody could confirm (``sync_unverified``) holds the same
            # way, in its own words: the field was not within tolerance and
            # the correction is unknown, so ``_recentred`` stays unset too.
            if isinstance(centring, dict) and centring.get("sync_refused"):
                return REFUSED_SYNC_RECENTRE_WORDS
            if isinstance(centring, dict) and centring.get("sync_unverified"):
                return REFUSED_SYNC_UNVERIFIED_RECENTRE_WORDS
            # The mount is tracking this target now: ``tick`` hands it to the
            # engine's idle clock with the start (#202).
            self._recentred = tgt
        return None

    def _a_run_took_over(self) -> bool:
        """Has a run started since ``tick`` checked ``engine.running``?

        ``tick`` checks once, before the ladder, and the ladder then awaits
        a safety read, a focuser, an autofocus, a solve and a limit check. Any
        of those awaits lets a manual start in, and from then on the camera,
        focuser and mount belong to that run: an autofocus would move the
        focuser under its exposures, a solve would take the camera, and the
        re-centring slew would drag the mount off the run's target. So the
        ladder asks this right before each of those three steps, with no
        await between the question and the step.
        """
        return bool(self.engine.running)

    def _must_stop(self) -> bool:
        """Should the ladder stop before its next step? When an operator
        asked it to (``stop_recovery``, #220) or a run has taken over
        (``_a_run_took_over``, #211). Asked at the same three points, with no
        await between the question and the step.

        The operator's flag matters most after a step that ran to its end
        despite the cancel ``stop_recovery`` sent it: without this read the
        ladder would carry on from that step to the next as if nothing had
        been asked."""
        return self._stop_why is not None or self._a_run_took_over()

    def _floor_eta_note(self, target, floor: float) -> str:
        """How long the wait above is, as a parenthetical, or empty when
        nobody can say.

        Reuses the scheduler's own gate-crossing search - the one that fills
        ``gating_status``'s ``eta_s`` for a target waiting on altitude - so the
        hold and the Tonight page cannot quote different numbers for the same
        wait. Best-effort throughout: no site, no crossing inside a sidereal
        day, or any arithmetic failure simply means no note.

        Site-derived, so it goes only into the hold's ``site_detail``, never
        into its words (#233)."""
        try:
            lat, lon = schedule._lat_lon(self.hub.site)
            eta = schedule._time_to_gate(target, lat, lon, floor,
                                         self._clock(), None)
        except Exception:  # noqa: BLE001 - a missing ETA is not a failure
            return ""
        if eta is None or eta <= 0:
            return ""
        mins = eta / 60.0
        return (f" (it reaches {floor:.0f} deg in about "
                + (f"{mins:.0f} min)" if mins < 90 else f"{mins / 60.0:.1f} h)"))

    def _devices_ready(self) -> bool:
        """Are the devices a resume needs actually connected yet?

        Checked BEFORE the recovery ladder so a half-finished boot never reads
        as a hazard. Only the camera and telescope are required: those are what
        the ladder and the run itself cannot proceed without.
        """
        for role in ("camera", "telescope"):
            dev = self.hub.devices.get(role)
            if dev is None or not getattr(dev, "connected", False):
                return False
        return True

    def _mount_position_known(self) -> bool:
        """Does the mount's driver vouch for the coordinates it reports
        (``Telescope.position_known``, #144, #867)? Absent means known, the
        contract every consumer keeps. Read through `rig_position_known`, so
        a doubt survives a profile activate that replaced the telescope
        object (the re-review's reconnect hole)."""
        return rig_position_known(self.hub)

    def _can_solve(self) -> bool:
        """Is a trustworthy plate solver available on this rig RIGHT NOW?

        Uses the same resolver the real solve path uses, so the two can never
        disagree about what this rig can do. Any failure to resolve one means
        no — the conservative reading, which degrades to a warning rather than a
        refusal (see the call site).
        """
        try:
            from .. import providers as _providers
            return _providers.pick_solver(self.hub) is not None
        except Exception:  # noqa: BLE001
            return False

    def _can_autofocus(self) -> bool:
        """Can this rig autofocus at all RIGHT NOW?

        The counterpart to :meth:`_can_solve`, and asked the same way: through
        the provider resolver, so this and the real autofocus path can never
        disagree about what the rig can do. ``_autofocus`` runs the NATIVE
        engine, so the question reduces to whether that engine is importable —
        a host without the Rust wheel has no autofocus, and answering "yes"
        there would send the ladder into a refusal it can never clear.

        Any failure to resolve reads as no, matching ``_can_solve``: the
        conservative answer degrades to a warning at the call site, never to a
        refusal.
        """
        try:
            from .. import providers as _providers
            if not getattr(_providers, "NATIVE_AVAILABLE", False):
                return False
            self.hub.require("focuser")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _autofocus(self):
        """The rig's real autofocus path (native), not the legacy numpy one.

        Returns the sweep's result: ``run_native_autofocus`` answers a sweep
        that found no focus with ``success`` False rather than raising, and
        only a sweep that found focus may stand in for the run's own (#402,
        ``_note_recovery_sweep``). The binning is passed, not defaulted, so
        the record names the binning the frames were taken at."""
        from ..focus.native import run_native_autofocus
        return await run_native_autofocus(self.hub.require("camera"),
                                          self.hub.require("focuser"),
                                          binning=RECOVERY_AF_BINNING)

    async def _note_recovery_sweep(self, foc, result) -> None:
        """Keep the sweep ``_autofocus`` just made as ``_recovery_sweep``
        when it found focus, and drop any earlier one when it did not (#402).

        ONLY A SWEEP THAT FOUND FOCUS COUNTS. A failed native sweep puts the
        drawtube back where it started, which after a restart is the
        position the focuser forgot, so standing the run on it would skip
        the one sweep that could still find focus tonight. So a result that
        does not say ``success`` True is no record at all, and neither is a
        result that is not a result (a test's stub, which returns None).
        The position and temperature are read now, as the sweep ended; a
        read that fails leaves its field None and keeps the record."""
        if getattr(result, "success", None) is not True:
            self._recovery_sweep = None
            if result is not None:
                bus.log("warning",
                        f"the autofocus after the restart did not find focus "
                        f"({getattr(result, 'message', '') or 'no reason given'})"
                        f"; the run will not count it as tonight's sweep",
                        "sequence")
            return
        position: int | None = None
        temp_c: float | None = None
        try:
            position = int(await foc.get_position())
        except Exception:  # noqa: BLE001 — a field the record can do without
            position = None
        try:
            t = await foc.get_temperature()
            temp_c = float(t) if t is not None else None
        except Exception:  # noqa: BLE001
            temp_c = None
        at = self._clock()
        self._recovery_sweep = _LadderSweep(
            night=night_key(at), at=at, position=position, temp_c=temp_c,
            binning=RECOVERY_AF_BINNING)

    def _sweep_for_start(self):
        """The ladder's sweep as ``engine.start``'s ``focus_sweep``, or None
        when there is none for tonight (#402).

        TONIGHT'S ONLY, by the ladder's own clock: a sweep kept across a
        night of refused retries is not the focus of the next evening, so a
        record from another night is dropped here rather than handed over.
        The age goes over, not the time, so the engine never compares this
        clock with its own."""
        rec = self._recovery_sweep
        if rec is None:
            return None
        now = self._clock()
        if rec.night != night_key(now):
            self._recovery_sweep = None
            return None
        from .engine import RecoverySweep
        return RecoverySweep(position=rec.position, temp_c=rec.temp_c,
                             binning=rec.binning,
                             age_s=max(0.0, float(now - rec.at)))

    def _solve_mark(self) -> int:
        """The hub's plate-solve count now, so ``_say_solve_timing`` can say
        which solves a ladder step made (#402). 0 for a hub that keeps no
        count (a test's stub)."""
        seq = getattr(self.hub, "solve_seq", 0)
        return seq if isinstance(seq, int) else 0

    def _say_solve_timing(self, mark: int, what: str) -> None:
        """Log, for every plate solve the hub made since ``mark``, when its
        exposure started against the GoTo it followed (#402).

        THE LINE THE 2026-09-27 LADDER DID NOT HAVE. Its centring solve,
        seconds after a blind solve of the same sky had worked, failed with
        no solution, once, and nothing could say whether the shutter opened
        while the mount was still settling from the GoTo. Each solve's line
        now says it, so a recurrence explains itself. ``what`` names the
        step: the blind solve, or the re-centre, whose attempts are counted.
        Never raises: it is a log line."""
        try:
            recs = [r for r in getattr(self.hub, "solve_exposures", ())
                    if isinstance(r, dict) and int(r.get("seq", 0)) > mark]
            for n, rec in enumerate(recs, start=1):
                which = (what if len(recs) == 1
                         else f"{what}, solve {n} of {len(recs)}")
                exposed, settled = rec.get("exposed_at"), rec.get("settled_at")
                if settled is None:
                    when = ("with no GoTo made since the server started, so "
                            "none was settling")
                else:
                    when = (f"{float(exposed) - float(settled):.1f} s after "
                            f"the last GoTo came to rest")
                bus.log("info", f"auto-resume: {which}: the exposure started "
                                f"{when}", "sequence")
        except Exception:  # noqa: BLE001 — a log line, never a refusal
            return
