"""CONTINUE: carrying a flow's dormant session onto tonight's compile (#189 S1,
spec 5.9 and D6).

ONE LEDGER PER FLOW. Pressing Run on night two hands the flow's own dormant
session to ``engine.start(session=)`` with the fresh compile as its plan, the
same call a resume makes, so ``Session.owed()`` stays the only definition of
finished and there is no second ledger to disagree with it. That works because
``to_sequence_plan(flow_id=)`` names a step the same way on every compile and
the ledger counts frames by step id alone.

What this module answers is whether carrying the ledger over is safe to do
without asking first, and it answers from the session and the new plan alone:
no store, no engine, no clock, no devices. The one outside source ADOPT asks
is the shipped catalogue (``tonight.resolve_target``, injectable): what KIND
of object a target name is, and where a moving body was when its frames were
taken. ``adopt_evidence`` asks all of it in one call, so the caller can ask
off the event loop and outside the store's write lock (#249).
``run_flow`` in ``api/app.py`` owns the write lock, the refusals and the
start.

* ``plan_replace_report`` - which steps carry over, which are new, and which
  steps that hold frames the new plan no longer has. It is also what
  ``PATCH /api/sessions/{id}`` reports for a plan edit, which is why it lives
  here and both call it: two copies of "what counts as dropped" would drift.
* ``saved_before_s1`` / ``adopt_matches`` / ``apply_adoption`` - a session
  saved before S1 has uuid4 step ids that no compile will ever produce again,
  so none of its frames count toward anything tonight. ADOPT re-keys the
  frames whose step matches exactly one step of the new plan, on a target
  within ``ADOPT_MAX_SEPARATION_ARCMIN`` of the old one (or on the same
  moving body, when the old target was within that bound of the body at the
  instants its frames were taken), and leaves the rest where they are. It is
  offered for such a session only: a session compiled since S1 that shares
  no step id with tonight's compile was re-framed, and that is the
  dropped-steps question.
* ``recount`` - a ledger is counted by its FROZEN plan's ``count_mode``, so
  continuing under a different mode recounts every banked frame at once. The
  operator is shown both totals before that happens.
"""
from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..sequence.models import SequencePlan
from ..sequence.session import Session, SessionFrame
from . import tonight
from .tonight import NameResolution


@dataclass(frozen=True)
class ReplaceReport:
    """What replacing a session's plan does to its steps.

    ``kept`` and ``new`` are step ids, sorted. ``dropped`` lists only the old
    steps that HOLD FRAMES and that the new plan lacks: a dropped step with no
    frames loses nothing, so nobody needs to be told about it.
    ``dropped_frames`` is how many ledger entries sit on those steps. They are
    not deleted - the frames list is never edited by a plan replace - they
    simply stop counting toward anything.
    """
    kept: list[str]
    new: list[str]
    dropped: list[str]
    dropped_frames: int

    def merge(self) -> dict[str, list[str]]:
        """The ``merge`` object ``PATCH /api/sessions/{id}`` has always
        answered, key for key."""
        return {"kept": self.kept, "new": self.new, "dropped": self.dropped}


def plan_replace_report(session: Session,
                        new_plan: SequencePlan) -> ReplaceReport:
    """Compare ``session``'s plan with ``new_plan`` by step id.

    A REPORT, NOT A MERGE. Neither caller merges anything: both replace
    ``session.plan`` wholesale afterwards. The refusal on dropped steps is
    CONTINUE's alone (spec 5.9); a PATCH keeps reporting and never refusing.
    """
    old_ids = {st.id for t in session.plan.targets for st in t.steps}
    new_ids = {st.id for t in new_plan.targets for st in t.steps}
    with_frames = {f.step_id for f in session.frames}
    dropped = sorted((old_ids - new_ids) & with_frames)
    gone = set(dropped)
    return ReplaceReport(
        kept=sorted(old_ids & new_ids),
        new=sorted(new_ids - old_ids),
        dropped=dropped,
        dropped_frames=sum(1 for f in session.frames if f.step_id in gone))


# ------------------------------------------------------------------ ADOPT

def _minted(step_id: str) -> bool:
    """True for an id ``flows/identity`` minted: every one of those is a
    uuid5, and ``SequencePlan`` mints uuid4s."""
    try:
        return uuid.UUID(hex=str(step_id)).version == 5
    except ValueError:
        return False


def saved_before_s1(session: Session) -> bool:
    """True when no step of ``session``'s plan carries an id
    ``to_sequence_plan(flow_id=)`` minted: a flow session saved before S1,
    whose uuid4 ids no compile produces again. The one kind of session ADOPT
    is for (spec 5.9).

    ASKED OF THE IDS, NOT OF "NOTHING IS SHARED". A session compiled since S1
    can share no step id with tonight's compile too: a single TARGET is keyed
    on the geometry it is at (spec 3.3), so moving it, or setting its angle,
    re-keys every step. That is a re-frame, whose counts restart (D5), and
    spec 5.9 puts it in the dropped-steps row. Read as pre-S1 it drew the
    adopt question, whose sentence says the session was "saved before flows
    kept their ids" - false - and ADOPT matches on target NAME and recipe, so
    a yes credited the old field's frames to the moved one: the flaw D5
    exists to remove.
    """
    return not any(_minted(st.id)
                   for t in session.plan.targets for st in t.steps)


def _step_key(target, step, body: str | None = None) -> tuple:
    """What ADOPT matches a pre-S1 step on (spec 5.9): target name, frame
    type, filter, exposure, gain, binning.

    ``count`` is deliberately absent, for the reason the step id leaves it out:
    raising a quota must keep the frames already banked. A None filter and ""
    are one key, because the engine treats both as "do not move the wheel"
    and ``flows/identity.py`` spells both "" in the step id.

    ``body`` is the canonical name of the moving body the target's name
    resolves to (``_body``), or None. A body is matched on that name, so
    "jupiter" typed before S1 finds tonight's "Jupiter" (#229); anything else
    keeps the name as typed, which the ruling left unchanged. The body is
    written as a ``("body", name)`` pair, which no ``str`` equals, so a body
    key and a name key are disjoint by construction, whatever a target is
    called."""
    who = ("body", body) if body else target.name
    return (who, step.frame_type or "Light", step.filter or "",
            float(step.exposure_s), int(step.gain), int(step.binning))


def _body(hit: NameResolution | None) -> str | None:
    """The canonical body name a resolved name is, or None. A name the
    catalogue does not know, or knows as a fixed row, is None: only a row
    whose position is a function of time is a body."""
    return hit.identity if hit is not None and hit.moves else None


def _usable(ts) -> float | None:
    """``ts`` as an instant, or None when it is not one: a frame the engine
    never stamped carries 0.0, and nothing before 1970 or not finite is a
    time a frame was taken at."""
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    return t if math.isfinite(t) and t > 0.0 else None


def _capture_times(frames: list[SessionFrame], created_ts) -> list[float]:
    """The instants ADOPT checks a body step's old pointing at (H3
    orchestrator ruling 7): the first and the last frame of each night the
    step was shot on, from the frames' ``ts``; a frame with no night
    recorded is an instant of its own, since nothing says which night it
    belongs to (the engine records one for every frame it banks). A step
    that holds no frames is checked at the session's ``created_ts``. Empty
    when there is no usable instant, which leaves the step unmatched.

    WHY A NIGHT'S TWO ENDS SUFFICE. The points within the bound of the old
    pointing are a spherical cap, and a cap contains the whole of the
    shortest arc between any two of its points. Over one night a planet or a
    comet moves at most a few degrees, near a great circle. The Moon moves
    about 7 degrees, and its topocentric path bends from a great circle by
    under an arcminute over any stretch whose two ends lie within 10 arcmin
    of one point (such a stretch lasts about an hour at most). So a body
    inside the cap at the night's first and last frame was inside it between
    them. A satellite does not move that way, but a sidereal pointing holds
    one for a minute or two at most, so a night of it longer than that fails
    at one of its ends.

    A frame with no usable time is left out. ``created_ts`` stands in only
    for a step with NO frames: it says when the session was made, not when
    anything was shot."""
    if not frames:
        t = _usable(created_ts)
        return [] if t is None else [t]
    nights: dict[str, list[float]] = {}
    out: set[float] = set()
    for f in frames:
        t = _usable(f.ts)
        if t is None:
            continue
        if f.night:
            nights.setdefault(f.night, []).append(t)
        else:
            out.add(t)
    for times in nights.values():
        out.update((min(times), max(times)))
    return sorted(out)


def _frames_by_step(session: Session) -> dict[str, list[SessionFrame]]:
    out: dict[str, list[SessionFrame]] = {}
    for f in session.frames:
        out.setdefault(f.step_id, []).append(f)
    return out


@dataclass(frozen=True)
class AdoptEvidence:
    """Every catalogue answer ADOPT needs for one session and one new plan
    (#249), asked before the match so that the match asks none.

    ``names`` holds ``resolve(name)`` for every distinct target name of both
    plans (None where the catalogue does not know it). ``positions`` holds
    ``resolve(name, when=t)`` for each old target that resolves to a moving
    body, at each instant ``_capture_times`` samples for each of its steps.
    An answer the match looks for and does not find here is not asked for:
    a name missing is a name the catalogue does not know, and an instant
    missing is an instant the body could not be placed at."""
    names: Mapping[str, NameResolution | None]
    positions: Mapping[tuple[str, float], NameResolution | None]


def adopt_evidence(session: Session, new_plan: SequencePlan, *,
                   resolve: Callable | None = None) -> AdoptEvidence:
    """Ask the catalogue every question ``adopt_matches`` has about
    ``session`` and ``new_plan``.

    SAFE ON A WORKER THREAD, AND OUTSIDE THE WRITE LOCK. It reads the two and
    writes neither, and every catalogue call is here: a full search per name
    and per instant, some 10 to 35 ms each and 700 ms for the first body of
    the process (#249), which the route must not spend on the event loop
    holding the store's lock. It may be built from the route's first read of
    the session: frames banked after it are instants it has no answer for,
    and the match lists their step as unplaced rather than mapping it.

    ``resolve`` is ``tonight.resolve_target`` unless a test hands in another
    (``(name, when=None) -> NameResolution | None``), looked up at call time
    so a test that replaces the module's resolver replaces it here too. Each
    distinct name, and each distinct (name, instant), is asked once."""
    ask = resolve or tonight.resolve_target
    names: dict[str, NameResolution | None] = {}
    for t in (*session.plan.targets, *new_plan.targets):
        if t.name not in names:
            names[t.name] = ask(t.name)
    by_step = _frames_by_step(session)
    positions: dict[tuple[str, float], NameResolution | None] = {}
    for t in session.plan.targets:
        if _body(names[t.name]) is None:
            continue
        for st in t.steps:
            for when in _capture_times(by_step.get(st.id, []),
                                       session.created_ts):
                if (t.name, when) not in positions:
                    positions[(t.name, when)] = ask(t.name, when=when)
    return AdoptEvidence(names=names, positions=positions)


def _describe(target, step, frames: int, reason: str,
              separation_arcmin: float | None = None) -> dict:
    """One step left as it is. ``separation_arcmin`` is how far its one
    match is on the sky, and null when there is no one match to measure."""
    return {"step_id": step.id, "target": target.name,
            "frame_type": step.frame_type or "Light", "filter": step.filter,
            "exposure_s": step.exposure_s, "gain": step.gain,
            "binning": step.binning, "frames": frames, "reason": reason,
            "separation_arcmin": separation_arcmin}


#: The two fixed reasons a step that holds frames is left as it is. The third,
#: a match on another field, names its distance (``_moved``).
NO_MATCH = "no step in this flow matches it"
AMBIGUOUS = "more than one step matches it"

#: How far apart on the sky an old target and a new one may be for ADOPT to
#: carry frames between them (#189 A4). The match key is a NAME and a recipe,
#: and a name is a label, not a place: #190's wizard wrote the typed name
#: "M16" onto M31's coordinates, so a session filed as "M16" holds frames of
#: Andromeda, and once the flow is corrected to M16 the key still matches,
#: 103 degrees away. Carrying those frames onto M16 is the flaw D5 removes for
#: a re-frame; ``saved_before_s1`` keeps a re-framed S1 session out of ADOPT,
#: and this keeps a moved pre-S1 one from walking back in.
#:
#: 10 arcmin is a first figure, like ``REFRAME_CARRY_FRACTION``: an order of
#: magnitude above the run's centring tolerance (1.2 arcmin), so one field
#: typed twice, or re-entered from a catalogue, is the same field; and the
#: size of the carry threshold spec 3.3 computes for its 3x2 example grid
#: (10.0 arcmin), so ADOPT carries no further than a re-frame would.
#:
#: INCLUSIVE: a match exactly this far apart is the same field (spec 5.9).
#:
#: FOR A MOVING BODY, AGAINST THE BODY AT CAPTURE TIME (#229, #234). A body
#: leaves the bound behind in days: the shipped ephemeris, sampled every 10
#: days from September 2026 for two years, gives median daily motions of 39
#: arcmin for Mars, 7.4 for Jupiter and 1.4 for Neptune (about a week to 10
#: arcmin, longer near a stationary point), and the Moon moves some 13 deg a
#: day. So measured against TONIGHT's target the bound refused a pre-S1
#: session of one as soon as the body had moved on, and H2 matched a body
#: on its canonical name (``_step_key``) with no measurement at all. That
#: trusted the name at the old end, the #190 fault: a session filed as
#: "Jupiter" at M31's coordinates was adopted onto Jupiter. H3 orchestrator
#: ruling 7 (spec, Still waiting on the owner, item 16) puts the bound back
#: for a body, measured between the OLD target and the body where it was
#: when the frames were taken (``_pointing``). A session frame records no
#: solve position, so the old plan's target coordinates are the evidence of
#: where the frames were pointed.
ADOPT_MAX_SEPARATION_ARCMIN = 10.0


def _separation_arcmin(old, new) -> float:
    """Great-circle distance between two targets, in arcmin. RA is in HOURS,
    turned into an angle here, so a step in RA shrinks by cos(dec) and wraps
    at 0 h / 24 h as the sky does.

    THE ATAN2 FORM (Vincenty's, on a sphere), not the law of cosines
    ``catalog.coords.angular_sep_deg`` uses. The law of cosines takes an
    ``acos`` of a number within 5e-6 of 1 at this scale, and the answers it
    can give there are spaced 1.3e-10 arcmin apart with none of them exactly
    10.0: the bound's own value was unreachable, so "inclusive" was a claim
    no pair of targets could put to the test. The atan2 form keeps full
    precision at every angle, a target 10/60 deg north of another is exactly
    10.0 arcmin from it, and the boundary test holds the ``<=``."""
    dra = math.radians((new.ra_hours - old.ra_hours) * 15.0)
    d1, d2 = math.radians(old.dec_deg), math.radians(new.dec_deg)
    across = math.hypot(
        math.cos(d2) * math.sin(dra),
        math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(dra))
    along = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.atan2(across, along)) * 60.0


def _moved(separation_arcmin: float) -> str:
    return (f"the step in this flow with its name and recipe points "
            f"{separation_arcmin:.1f} arcmin from where these frames were "
            f"taken, more than the {ADOPT_MAX_SEPARATION_ARCMIN:g} arcmin "
            f"ADOPT carries frames across")


def _off_body(body: str, worst_arcmin: float) -> str:
    return (f"these frames were taken up to {worst_arcmin:.1f} arcmin from "
            f"where {body} was at the time, more than the "
            f"{ADOPT_MAX_SEPARATION_ARCMIN:g} arcmin ADOPT carries frames "
            f"across, so they are not of {body}")


def _no_time(body: str) -> str:
    return (f"no capture time is recorded for these frames, so nothing shows "
            f"they were taken of {body}")


def _unplaced(body: str) -> str:
    return (f"the catalogue could not place {body} at a time these frames "
            f"were taken, so nothing shows they are of it")


def _pointing(target, frames: list[SessionFrame], created_ts, body: str,
              evidence: AdoptEvidence) -> tuple[float | None, str | None]:
    """``(worst separation, reason)`` for an old body step: the reason is
    None when the old target was within ``ADOPT_MAX_SEPARATION_ARCMIN`` of
    ``body``, inclusive, at every instant ``_capture_times`` samples, and
    says why not otherwise (H3 orchestrator ruling 7, #234).

    Read from ``evidence`` alone, never from the catalogue: an instant it
    holds no answer for, or an answer that names another object, proves
    nothing and refuses."""
    times = _capture_times(frames, created_ts)
    if not times:
        return None, _no_time(body)
    worst = 0.0
    for when in times:
        at = evidence.positions.get((target.name, when))
        if at is None or at.identity != body:
            return None, _unplaced(body)
        worst = max(worst, _separation_arcmin(target, at))
    if worst <= ADOPT_MAX_SEPARATION_ARCMIN:
        return worst, None
    return worst, _off_body(body, worst)


@dataclass(frozen=True)
class AdoptMatches:
    """The outcome of matching a pre-S1 session against a fresh compile.

    ``mapping`` is old step id -> (new target id, new step id), for every old
    step that matched exactly one new step and was itself the only old step
    with that key. ``unmatched`` and ``ambiguous`` describe the old steps that
    HOLD FRAMES and did not match; a frameless step has nothing to carry.
    ``frames_matched`` is how many ledger entries the mapping re-keys.
    """
    mapping: dict[str, tuple[str, str]]
    unmatched: list[dict] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    frames_matched: int = 0

    def rest(self) -> list[dict]:
        """Every step left as it is, the ambiguous ones first: those are the
        ones a person can resolve by editing the flow."""
        return [*self.ambiguous, *self.unmatched]


def adopt_matches(session: Session, new_plan: SequencePlan, *,
                  resolve: Callable | None = None,
                  evidence: AdoptEvidence | None = None) -> AdoptMatches:
    """Match ``session``'s steps to ``new_plan``'s on ``_step_key``.

    UNIQUE ON BOTH SIDES. A key that two old steps share, or that two new
    steps share, maps nothing: picking one would credit one step's frames to
    the other, which is the #77 fault the step ids exist to prevent. Pre-S1
    flows carry no mosaics, so in practice a key is one TARGET node's one
    recipe, and a collision means the flow itself repeats a recipe.

    ON THE SAME FIELD. A unique match maps only when the two targets are
    within ``ADOPT_MAX_SEPARATION_ARCMIN`` of each other, inclusive; further
    apart, the old step is listed with its separation (when it holds frames)
    and nothing is re-keyed. The name in the key is a label, and the
    separation is what says the label still points where the frames were
    taken.

    ...OR ON THE SAME BODY, WHERE IT WAS (#229, #234). A name the catalogue
    finds as a moving row (a planet, the Moon, a comet, a satellite) is keyed
    on the row's canonical body name, so "jupiter" typed before S1 finds
    tonight's "Jupiter"; tonight's position is not compared, because the body
    has moved on since. The match maps only when the OLD target was within
    the bound of the body at the instants its frames were taken
    (``_pointing``, H3 orchestrator ruling 7); otherwise the step is listed
    with a reason that names the body and, when it was placed, the worst
    separation. A deep-sky or star name keeps the typed-name key and the
    bound against tonight's target.

    ``evidence`` is ``adopt_evidence``'s answer, and when it is given this
    makes NO catalogue call and ``resolve`` is not used: the route asks the
    catalogue off the event loop and outside the write lock, then matches
    inside it (#249). Without it, the evidence is asked here, through
    ``resolve`` (``tonight.resolve_target`` unless a test hands in another,
    looked up at call time). ``api/app.py`` never takes that path: it
    always passes evidence, an empty one when its first read of the session
    did not ask the ADOPT question, and lists every step the evidence holds
    no answer for as one to ADOPT again (``_unasked``).
    """
    if evidence is None:
        evidence = adopt_evidence(session, new_plan, resolve=resolve)

    def key(t, st) -> tuple:
        return _step_key(t, st, _body(evidence.names.get(t.name)))

    old_by_key: dict[tuple, list] = {}
    for t in session.plan.targets:
        for st in t.steps:
            old_by_key.setdefault(key(t, st), []).append((t, st))
    new_by_key: dict[tuple, list] = {}
    for t in new_plan.targets:
        for st in t.steps:
            new_by_key.setdefault(key(t, st), []).append((t, st))
    by_step = _frames_by_step(session)
    mapping: dict[str, tuple[str, str]] = {}
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    matched = 0
    for k, olds in old_by_key.items():
        news = new_by_key.get(k, [])
        # The first field of a body's key is the ("body", name) pair: it is
        # measured against the body where it was when its frames were
        # taken, never against tonight's target.
        body = k[0][1] if isinstance(k[0], tuple) else None
        for t, st in olds:
            n = len(by_step.get(st.id, []))
            if len(olds) == 1 and len(news) == 1:
                nt, nst = news[0]
                if body is None:
                    apart = _separation_arcmin(t, nt)
                    why = (None if apart <= ADOPT_MAX_SEPARATION_ARCMIN
                           else _moved(apart))
                else:
                    apart, why = _pointing(t, by_step.get(st.id, []),
                                           session.created_ts, body,
                                           evidence)
                if why is None:
                    mapping[st.id] = (nt.id, nst.id)
                    matched += n
                elif n:
                    unmatched.append(_describe(
                        t, st, n, why,
                        None if apart is None else round(apart, 3)))
            elif n:
                if news:
                    ambiguous.append(_describe(t, st, n, AMBIGUOUS))
                else:
                    unmatched.append(_describe(t, st, n, NO_MATCH))
    return AdoptMatches(mapping=mapping, unmatched=unmatched,
                        ambiguous=ambiguous, frames_matched=matched)


def apply_adoption(session: Session, matches: AdoptMatches) -> int:
    """Re-key ``session`` IN PLACE through ``matches.mapping``; return how many
    frames moved.

    Each matched frame takes the new target id and step id. The old plan's
    matched steps take the new step id too, so ``plan_replace_report`` run
    afterwards reads them as kept rather than dropped - the plan itself is
    about to be replaced, and this is what makes the report describe the
    adoption honestly. Nothing is written: the caller persists the session,
    through ``engine.start``, only once every refusal has passed.
    """
    moved = 0
    for f in session.frames:
        hit = matches.mapping.get(f.step_id)
        if hit is not None:
            f.target_id, f.step_id = hit
            moved += 1
    for t in session.plan.targets:
        for st in t.steps:
            hit = matches.mapping.get(st.id)
            if hit is not None:
                st.id = hit[1]
    return moved


def adopt_detail(frames: int, matched: int) -> str:
    rest = frames - matched
    return (f"this flow's session holds {frames} sub{_s(frames)} under step "
            f"ids no compile produces any more (it was saved before flows kept "
            f"their ids). ADOPT re-keys the {matched} that match exactly one "
            f"step of this flow and leaves {rest} as {_they(rest)} {_are(rest)}"
            f"; START OVER begins a new session and leaves this one on disk")


# ------------------------------------------------------------------ recount

def _counted(session: Session, mode: str) -> int:
    """Frames ``mode`` counts, over the WHOLE ledger and uncapped: the number
    the session itself shows, not the part of it tonight's quota can use."""
    if mode == "accepted":
        return sum(1 for f in session.frames if f.effective())
    return len(session.frames)


def recount(session: Session, new_plan: SequencePlan) -> tuple[int, int]:
    """``(before, after)``: the ledger counted by the session's frozen plan's
    ``count_mode``, and by ``new_plan``'s."""
    return (_counted(session, session.plan.count_mode),
            _counted(session, new_plan.count_mode))


_MODE_WORDS = {"attempts": "every sub taken", "accepted": "accepted subs"}


def recount_detail(old_mode: str, new_mode: str, before: int,
                   after: int) -> str:
    return (f"this session counted {_MODE_WORDS.get(old_mode, old_mode)} "
            f"({before}); counting {_MODE_WORDS.get(new_mode, new_mode)} "
            f"makes it {after}")


def dropped_detail(frames: int) -> str:
    if frames == 1:
        return ("1 sub belongs to a step this flow no longer has; it stays "
                "on disk")
    return (f"{frames} subs belong to steps this flow no longer has; they "
            f"stay on disk")


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _they(n: int) -> str:
    return "it" if n == 1 else "they"


def _are(n: int) -> str:
    return "is" if n == 1 else "are"
